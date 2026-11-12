# msp/handlers.py
"""MSP 命令表：身份组 + 状态组 + 参数读写组 + 模式组 + OSD 组。

命令号对齐 Betaflight Configurator（src/js/msp/MSPCodes.ts，2026-11 核对）：
1=MSP_API_VERSION、2=MSP_FC_VARIANT、3=MSP_FC_VERSION、4=MSP_BOARD_INFO、
5=MSP_BUILD_INFO、160=MSP_UID（v1 惯例值）。
状态组（Task 20，Setup 页实时遥测）：101=MSP_STATUS、150=MSP_STATUS_EX、
102=MSP_RAW_IMU、105=MSP_RC、108=MSP_ATTITUDE。
参数读写组（Task 21，PID tab 调参读写映射 virtual.tune）：117=MSP_PIDNAMES、
112=MSP_PID、202=MSP_SET_PID、111=MSP_RC_TUNING、204=MSP_SET_RC_TUNING、
94=MSP_PID_ADVANCED、95=MSP_SET_PID_ADVANCED。GET 经 params.active_tune()
（virtual_fc=True → virtual.tune 本体，False → 平铺快照）；SET 恒写
virtual.tune 本体并置 state["changed"]（dispatch → on_change → 脏标志 →
飞行帧首 set_params 即时生效）。

模式组（Task 22，Modes 页开关范围）：116=MSP_BOXNAMES、119=MSP_BOXIDS、
34=MSP_MODE_RANGES、35=MSP_SET_MODE_RANGE、238=MSP_MODE_RANGES_EXTRA。
GET/SET 恒读写 virtual.mode_ranges（平铺域无此字段）；µs↔step 换算
900+step×25；SET_MODE_RANGE 兼容 5/7 字节双形态、slot≥20 整体忽略。

OSD 组（Task 23，OSD tab 布局编辑）：84=MSP_OSD_CONFIG、85=MSP_SET_OSD_CONFIG、
0x3007=MSP2_SET_TEXT（0x3006 GET_TEXT 见上）。GET 应答 190 字节固定布局
（item_count=80，元素表单一来源 import 根目录 osd.py）；SET 位置 11 位 +
可见位 0x0800 分解写 virtual.osd_layout/osd_enabled。布局差异核对记录见
_osd_config_payload docstring（简报 194/81 与固件写序不符，按外部源码实现）。

MSP2_GET_TEXT(0x3006=12294) 为 v2 命令（fix round 3）：连接序列在身份组
之后请求 BUILDKEY(5)/CRAFT_NAME(2)/PILOT_NAME(1)（serial_backend.ts
processBuildConfiguration/processCraftName），无应答会被判超时断开。
请求 payload=[类型 u8]，应答 payload=[类型 u8][长度 u8]+文本（fix round 1
对齐固件/Configurator 的 getText 帧；空文本保持单字节，Configurator
readU8 越界 null||0 容错）；类型 2 CRAFT_NAME 为 state 感知（Task 23：
virtual.craft_name → preset_id → FSKY-SIM 兜底）。

状态组载荷布局经外部源码核对（2026-11-12 GitHub 取 tag 4.5.1 的
src/main/msp/msp.c L1080-1133 与 Configurator src/js/msp/MSPHelper.ts
L841-903）：STATUS_EX 与固件写序逐字段一致；STATUS 应答 11 字节止于
profile（Configurator 解码器只读 11 字节即停、尾字节忽略——真实固件续写
load/gyroCycleTime/禁锁段共 24 字节，本表从简，差异记录于 Task 20 提交）。

import 约定：除标准库外仅 import 根目录 osd.py（纯数据表模块，无渲染/
UI 依赖）——OSD 元素表单一来源，handlers 与飞行页 OSD 渲染共用一份。
"""
import struct
from typing import Callable

import osd

# MSP_UID：Configurator MSPCodes.js 定义为 160（v1 惯例，已在线核对）
MSP_UID = 160

# ---- 状态组常量（Task 20） ----
# activeSensors 位图：ACC=bit0、BARO=bit1、MAG=bit2、GPS=bit3、RANGEFINDER=
# bit4、GYRO=bit5（msp.c L1087）——模拟器仅 ACC|GYRO = 1|32 = 33
_SENSORS = 33

# gyro dps→raw 换算：Configurator 还原 ×4/16.4（MSPHelper.ts L903），发送侧
# 逆换算即 ×16.4/4 = 4.1（如 100dps → 410）
_GYRO_SCALE = 4.1

# 自报 PID 任务周期 µs（cycleTime，模拟量级代表值）
_CYCLE_TIME_US = 500

# MSP2_GET_TEXT（MSPCodes.ts MSP2_GET_TEXT = 0x3006；0x3007 为 SET_TEXT）
MSP2_GET_TEXT = 0x3006

# ---- 参数读写组常量（Task 21，Configurator PID tab） ----
# 仿真 D 标定域与 BF 展示域差百倍（config_store._default_pid 注释：存档/
# 展示为 BF 量级刻度、仿真内部经 PID_MIX_SCALING 换算，默认 D=0.4 ↔ BF 40）
_PID_D_SCALE = 100.0

# ---- 模式组常量（Task 22，Configurator Modes 页） ----
# 槽位数 = 固件 4.5.1 fc/rc_modes.h MAX_MODE_ACTIVATION_CONDITION_COUNT
MODE_RANGE_SLOTS = 20

# BOXNAMES：box 显示名 0x3b 分隔（MSPHelper.ts:1276-1295 按 ';' 拆解，
# 条目数与 BOXIDS 对齐；显示名参考值，以固件 box 名称表核对）
_BOX_NAMES = b"ARM;ANGLE;HORIZON;FLIP OVER AFTER CRASH"

# BOXIDS：permanentId 表（plan 1 Task 10 已按固件 box_ids.h 核对：ARM=0/
# ANGLE=1/HORIZON=2/FLIP_OVER_AFTER_CRASH=21，与 drone.py BOX_* 常量同源）
_BOX_IDS = bytes([0, 1, 2, 21])

# MSP_PID 条目序（固件 4.5.1 pid.h PID_ITEM_COUNT=5：ROLL/PITCH/YAW/LEVEL/MAG）；
# 仿真仅前三轴有映射，LEVEL/MAG 恒 0（诚实降级）
_PID_AXES = ("roll", "pitch", "yaw")

# MSP_PIDNAMES：条目名 0x3b 分隔（Configurator 按分号拆解，条目数与 PID 一致）
_PID_NAMES = b"ROLL;PITCH;YAW;LEVEL;MAG"

# ---- OSD 组常量（Task 23，Configurator OSD tab） ----
# MSP_OSD_CONFIG(84) 头部 osdFlags（msp.c OSD_FLAGS_* 位定义）：
# bit0 FEATURE_OSD | bit4 OSD_HARDWARE_MAX_7456 | bit5 DEVICE_DETECTED
# = 0x31——模拟器恒报 MAX7456 检出（PAL SD 网格 30×16 路径）
_OSD_FLAGS = 0x31

# video_system=1 PAL（Configurator VIDEO_TYPES[1]="PAL" → 30×16 网格；
# NTSC 13 行受限，PAL 为 BF 默认）
_OSD_VIDEO_SYSTEM = 1

# 统计/计时器/警告段条目计数（固件 4.5.1 osd.h 枚举计数）。警告段结构
# 本身即 count+flags u32（无逐条字节，Configurator 按 flags 位解析）→ 报
# 固件语义计数；计时器段 count+u16×2 逐条全 0；统计段 GET 恒 count=0
# （fix round 1 裁定：固件写 count 后逐条写 31×u8，Configurator
# decodeStatItems 是无边界守卫的 count 驱动循环——只报 count 不发条目会
# 把后续段字节吃成统计项；模拟器无统计屏功能，count=0 最简且解码干净）
_OSD_STAT_COUNT = 31      # osd_stats_e（RTC_DATE_TIME..AVG_THROTTLE）；仅
                          # SET_OSD_CONFIG 侧统计开关形态判定（addr<31）用
_OSD_TIMER_COUNT = 2      # osd_timer_e（TIMER_1/TIMER_2；简报写 3 系笔误）
_OSD_WARNING_COUNT = 19   # osdWarningsFlags_e（4.5.1：ARMING_DISABLE..LOAD）

# MSP2TextType（MSPCodes.ts MSP2TextType 枚举，2026-11 核对）：GET 方向
# 合法类型 → 应答文本（空串即 [类型字节] 单字节载荷）。类型 2 CRAFT_NAME
# 走 _craft_name_text state 感知分支（Task 23）、类型 3/4 恒 FSKY（单
# profile 参考名），不入此表
_TEXT_TYPES: dict[int, bytes] = {
    1: b"",            # PILOT_NAME
    3: b"FSKY",        # PID_PROFILE_NAME（参考值）
    4: b"FSKY",        # RATE_PROFILE_NAME（参考值）
    5: b"",            # BUILDKEY（非 32 字长 → Configurator 跳过云构建选项）
    6: b"",            # RELEASENAME（GET 方向固件支持）
    11: b"",           # BATTERY_PROFILE_NAME
}

# 板名/构建自报（设计文档规定值；25 尾号预留扩展）
_BOARD_NAME = b"FSKY"

# UID 应答：12 字节固定值（三个 u32，模拟固定硬件 unique id；每次启动一致）
_UID_PAYLOAD = struct.pack("<3I", 0x46534B59, 0x56464331, 0x32363131)
assert len(_UID_PAYLOAD) == 12


def _craft_name_text(state: "dict | None") -> bytes:
    """CRAFT_NAME 应答文本：virtual.craft_name → state["preset_id"] →
    "FSKY-SIM" 兜底（无状态源时保持 Task 19 的自报机名语义）。"""
    params = (state or {}).get("params")
    virtual = getattr(params, "virtual", None)
    name = getattr(virtual, "craft_name", "") or ""
    if not name:
        name = (state or {}).get("preset_id") or ""
    return (name or "FSKY-SIM").encode("utf-8")


def _get_text(payload: bytes, state: "dict | None" = None) -> bytes:
    """MSP2_GET_TEXT 应答：非空文本 [类型 u8][长度 u8][文本]（固件 msp.c
    写长度字节、Configurator getText 先 readU8 当长度再循环收字符——fix
    round 1 对齐）；空文本保持 [类型] 单字节（getText 的 readU8 越界
    null||0=0 容错，Task 19 行为不变）；未知类型 → 空载荷降级。

    state 为 Task 19 二参协议的第二个参数；类型 2 CRAFT_NAME 消费 state
    （Task 23：地面站写入/预设代号可回显），其余类型查静态表。"""
    if not payload:
        return b""
    text_type = payload[0]
    if text_type == 2:
        text = _craft_name_text(state)
    else:
        text = _TEXT_TYPES.get(text_type)
        if text is None:
            return b""
    if not text:
        return bytes([text_type])
    return bytes([text_type, len(text)]) + text


# ---- 状态组（Task 20） ----

def _i16(v: float) -> int:
    """浮点 → i16 载荷值：四舍五入并钳位到 [-32768, 32767]（超界截断防
    struct.pack 抛错；简报参考值 -32868 超出 i16 表示范围，系笔误）"""
    return max(-32768, min(32767, round(v)))


def _u16(v: float) -> int:
    """浮点 µs → u16 载荷值：四舍五入并钳位（µs 实际域 1000-2000，纯防御）"""
    return max(0, min(65535, round(v)))


def _mode_bits(flight: dict) -> int:
    """flight 快照 boxes → mode u32 = Σ(1 << box_id)（ARM=bit0）。

    boxes 为 eval_boxes 求值的 box id 整数集（drone.py BOX_* 常量）；
    非 int 或 ≥32 的 id 跳过（u32 容量外/未核实的位号不冒充）。"""
    mode = 0
    for box in flight.get("boxes") or ():
        if isinstance(box, int) and 0 <= box < 32:
            mode |= 1 << box
    return mode


def _status_payload(state: "dict | None", extended: bool) -> bytes:
    """STATUS(101)/STATUS_EX(150) 应答（固件 msp.c L1080-1133 写序）。

    共享头 11 字节：cycleTime u16、i2cError u16、activeSensors u16、
    mode u32、profile u8。STATUS 到此为止（Configurator 解码器只读 11 字节
    即停）。STATUS_EX 续 13 字节：cpuload u16、numProfiles u8、rateProfile
    u8、flagByteCount u8（=0：mode u32 已容纳全部激活位，无扩展字节）、
    armingDisableCount u8、armingDisableFlags u32、configStateFlag u8、
    cpuTemp u16（API 1.46 尾）。

    禁锁位恒 count=0/flags=0：模拟器无禁锁枚举可报（油门保护拒绝解锁不映
    射禁锁位——位号未核实不冒充）。state/flight 缺键 → mode=0 其余同构。"""
    flight = (state or {}).get("flight") or {}
    head = struct.pack("<HHHIB", _CYCLE_TIME_US, 0, _SENSORS,
                       _mode_bits(flight), 0)
    if not extended:
        return head
    # cpuload=0（模拟器无 CPU 负载概念）、numProfiles=1、rateProfile=0、
    # 尾段（flagByteCount/禁锁/configState/cpuTemp）全 0
    return head + struct.pack("<HBB", 0, 1, 0) + struct.pack("<BBIBH", 0, 0, 0, 0, 0)


def _attitude_payload(payload: bytes, state: "dict | None" = None) -> bytes:
    """MSP_ATTITUDE(108)：3×i16 = (roll×10, pitch×10, yaw)（Configurator
    解码前两轴 ÷10、yaw 原值——decidegree 惯例）。姿态角取 bullet 语义
    world.euler_deg；无飞行快照 → 3×0。"""
    flight = (state or {}).get("flight") or {}
    roll, pitch, yaw = flight.get("attitude_deg") or (0.0, 0.0, 0.0)
    return struct.pack("<hhh", _i16(roll * 10), _i16(pitch * 10), _i16(yaw))


def _raw_imu_payload(payload: bytes, state: "dict | None" = None) -> bytes:
    """MSP_RAW_IMU(102)：9×i16 = acc + gyro + mag。

    acc 恒 (0, 0, 2048)：水平静态 1g（Configurator ÷2048 还原 g）；
    gyro = round(dps × 4.1)（Configurator ×4/16.4 还原 °/s 的逆换算）；
    mag 恒 0（模拟器无磁力计）。无飞行快照同构（acc 静态值照发）。"""
    flight = (state or {}).get("flight") or {}
    gx, gy, gz = flight.get("gyro_dps") or (0.0, 0.0, 0.0)
    return (struct.pack("<hhh", 0, 0, 2048)
            + struct.pack("<hhh", _i16(gx * _GYRO_SCALE),
                          _i16(gy * _GYRO_SCALE), _i16(gz * _GYRO_SCALE))
            + struct.pack("<hhh", 0, 0, 0))


def _rc_payload(payload: bytes, state: "dict | None" = None) -> bytes:
    """MSP_RC(105)：8×u16 µs = [油门, yaw, roll, pitch, AUX1..AUX4]
    （通道序为本虚拟飞控的固定接线，语义标签由后续 RX_MAP 任务对齐）。

    stick_us 为快照 4 元组 (roll, pitch, yaw, throttle)（flight_snapshot
    文档序），缺省中位 1500；油门通道取 throttle 键换算 1000+v×1000
    （MSP 油门标度，非 stick_us[3] 的 1500±500 标度）；AUX 不足 4 个补
    1500。无飞行快照 → 油门 1000、其余 1500。"""
    flight = (state or {}).get("flight") or {}
    sticks = flight.get("stick_us") or ()
    roll_us = sticks[0] if len(sticks) > 0 else 1500.0
    pitch_us = sticks[1] if len(sticks) > 1 else 1500.0
    yaw_us = sticks[2] if len(sticks) > 2 else 1500.0
    throttle = flight.get("throttle") or 0.0
    aux = list(flight.get("aux_us") or ())[:4]
    aux += [1500.0] * (4 - len(aux))
    channels = [1000.0 + throttle * 1000.0, yaw_us, roll_us, pitch_us, *aux]
    return struct.pack("<8H", *(_u16(v) for v in channels))


# ---- 参数读写组（Task 21） ----

def _u8(v: float) -> int:
    """浮点 → u8 载荷值：钳位 [0,255] + 半进位取整（0.5 进 1）。"""
    return int(min(255.0, max(0.0, v)) + 0.5)


def _set_changed(state: "dict | None") -> None:
    """SET 写参数成功后声明变更（dispatch 末尾 pop → on_change 置脏标志）。

    仅在写路径完整成功后调用（len 守卫失败/无参数源不计变更）。"""
    state["changed"] = True


def _active_tune(state: "dict | None"):
    """state["params"] → 当前生效调参域（DroneParams.active_tune：virtual_fc
    =True → virtual.tune 本体；False → 平铺字段深拷贝快照）。无参数源 → None
    （GET 诚实降级空载荷；SET 不可写直接放弃）。"""
    params = (state or {}).get("params")
    return params.active_tune() if params is not None else None


def _pid_payload(payload: bytes, state: "dict | None" = None) -> bytes:
    """MSP_PID(112)：5 条目 × 3 u8（P/I/D）。P/I ×1 无损、D ×100 域换算，
    LEVEL/MAG 条目恒 0（仿真无自稳/磁锁 PID）。"""
    tune = _active_tune(state)
    if tune is None:
        return b""
    out = bytearray()
    for ax in _PID_AXES:
        g = tune.pid[ax]
        out += struct.pack("<3B", _u8(g.p), _u8(g.i), _u8(g.d * _PID_D_SCALE))
    return bytes(out) + b"\x00" * 6      # LEVEL + MAG（恒 0）


def _set_pid(payload: bytes, state: "dict | None" = None) -> bytes:
    """MSP_SET_PID(202)：15 字节同 GET 布局（len<15 整体忽略）。

    roll/pitch/yaw 写回 virtual.tune 本体（设计 §8.3：SET 恒写 virtual 域，
    参数模式无地面站入口）；P/I ×1、D ÷100；LEVEL/MAG 忽略。"""
    if state is None or len(payload) < 15:
        return b""
    params = state.get("params")
    if params is None:
        return b""
    for i, ax in enumerate(_PID_AXES):
        p, i_gain, d = struct.unpack_from("<3B", payload, i * 3)
        g = params.virtual.tune.pid[ax]
        g.p, g.i, g.d = float(p), float(i_gain), d / _PID_D_SCALE
    _set_changed(state)
    return b""


def _rc_tuning_payload(payload: bytes, state: "dict | None" = None) -> bytes:
    """MSP_RC_TUNING(111)：23 字节（MSPHelper.ts L1147-1180 布局）。

    三轴 rate ×100 双向换算（roll 占头部三参数，pitch/yaw 的 super_rate 在
    头部、rc_rate/expo 在尾段）；throttle_MID=50/throttle_EXPO=0/reserved/
    throttleLimit=0/rate_limit=500/rates_type=0 为只读参考值（仿真无对应，
    SET 忽略——GET/SET 对称往返，诚实降级）。"""
    tune = _active_tune(state)
    if tune is None:
        return b""
    r = tune.rate
    return (struct.pack(
                "<6B", _u8(r["roll"].rc_rate * 100), _u8(r["roll"].expo * 100),
                _u8(r["roll"].super_rate * 100), _u8(r["pitch"].super_rate * 100),
                _u8(r["yaw"].super_rate * 100), 0)
            + struct.pack("<2B", 50, 0) + struct.pack("<H", 0)
            + struct.pack(
                "<4B", _u8(r["yaw"].expo * 100), _u8(r["yaw"].rc_rate * 100),
                _u8(r["pitch"].rc_rate * 100), _u8(r["pitch"].expo * 100))
            + struct.pack("<2B", 0, 0)
            + struct.pack("<3H", 500, 500, 500) + bytes([0]))


def _set_rc_tuning(payload: bytes, state: "dict | None" = None) -> bytes:
    """MSP_SET_RC_TUNING(204)：23 字节同 GET 布局（len<23 整体忽略）。

    三轴 rate 写回 virtual.tune 本体（÷100）；throttle MID/EXPO、
    rate_limit、rates_type、throttleLimit 段忽略（GET 报参考值；
    rate_type 保持预设——linear 无地面站等价档）。"""
    if state is None or len(payload) < 23:
        return b""
    params = state.get("params")
    if params is None:
        return b""
    r = params.virtual.tune.rate
    (roll_rc, roll_expo, roll_rate, pitch_rate, yaw_rate) = \
        struct.unpack_from("<5B", payload, 0)
    yaw_expo, yaw_rc, pitch_rc, pitch_expo = struct.unpack_from("<4B", payload, 10)
    r["roll"].rc_rate = roll_rc / 100.0
    r["roll"].expo = roll_expo / 100.0
    r["roll"].super_rate = roll_rate / 100.0
    r["pitch"].super_rate = pitch_rate / 100.0
    r["yaw"].super_rate = yaw_rate / 100.0
    r["yaw"].expo = yaw_expo / 100.0
    r["yaw"].rc_rate = yaw_rc / 100.0
    r["pitch"].rc_rate = pitch_rc / 100.0
    r["pitch"].expo = pitch_expo / 100.0
    _set_changed(state)
    return b""


def _pid_advanced_payload(payload: bytes, state: "dict | None" = None) -> bytes:
    """MSP_PID_ADVANCED(94)：59 字节（MSPHelper.ts L1959-2028 解码序：
    12×u16 + 35×u8）。非零语义字段仅 autoProfileCellCount(s8)=virtual.cells、
    tpaRate u8=tpa_percent、tpaBreakpoint u16=断点 µs（1000-2000 域）；
    D-Min/FF 等 44 字段恒 0（仿真无对应，SET 全部忽略——诚实降级）。"""
    params = (state or {}).get("params")
    tune = _active_tune(state)
    if params is None or tune is None:
        return b""
    return (b"\x00" * 46
            + struct.pack("<B", _u8(params.virtual.cells))
            + b"\x00" * 9
            + struct.pack("<BH", _u8(tune.tpa_percent),
                          _u16(1000 + tune.tpa_breakpoint * 1000)))


def _set_pid_advanced(payload: bytes, state: "dict | None" = None) -> bytes:
    """MSP_SET_PID_ADVANCED(95)：len≥59 才消费（守卫外整体忽略）。

    仅取 autoProfileCellCount（s8，≥1 → virtual.cells；0=auto 不写）与尾部
    tpaRate/tpaBreakpoint（percent ← u8、breakpoint ← (µs−1000)/1000 钳位
    [0,1]），写 virtual.tune 本体；其余字段忽略。"""
    if state is None or len(payload) < 59:
        return b""
    params = state.get("params")
    if params is None:
        return b""
    cells = struct.unpack_from("<b", payload, 46)[0]
    if cells >= 1:
        params.virtual.cells = cells
    tpa_rate, tpa_bp_us = struct.unpack_from("<BH", payload, 56)
    tune = params.virtual.tune
    tune.tpa_percent = float(tpa_rate)
    tune.tpa_breakpoint = min(1.0, max(0.0, (tpa_bp_us - 1000) / 1000.0))
    _set_changed(state)
    return b""


# ---- 模式组（Task 22） ----

def _step_to_us(step: int) -> int:
    """step（25µs 步进基准 900µs）→ µs（Configurator 编码段逆换算）。"""
    return 900 + step * 25


def _us_to_step(us: float) -> int:
    """µs → step：(µs−900)//25，经 _u8 钳位 [0,255]（实际域 900-2100 →
    0-48，钳位纯防御）。"""
    return _u8((us - 900) // 25)


def _slot_entry(ranges: list, i: int) -> tuple:
    """按槽序号取 mode_ranges 条目；越界（未配置槽）→ (0, 0, 0, 0) 缺省。

    JSON round-trip 后条目为 list，tuple() 归一为可解包元组。"""
    return tuple(ranges[i]) if i < len(ranges) else (0, 0, 0, 0)


def _mode_ranges_payload(payload: bytes, state: "dict | None" = None) -> bytes:
    """MSP_MODE_RANGES(34)：20 槽 ×4B = permanentId u8、auxChannelIndex u8、
    startStep u8、endStep u8（固件 msp.c 写序）。读 virtual.mode_ranges
    （出厂默认 ARM=AUX1 高段，Modes 页开箱可用）；未配置槽恒 (0,0,0,0)。
    无参数源 → 空载荷降级。"""
    params = (state or {}).get("params")
    if params is None:
        return b""
    out = bytearray()
    for i in range(MODE_RANGE_SLOTS):
        box_id, aux, lo_us, hi_us = _slot_entry(params.virtual.mode_ranges, i)
        out += struct.pack("<4B", _u8(box_id), _u8(aux),
                           _us_to_step(lo_us), _us_to_step(hi_us))
    return bytes(out)


def _mode_ranges_extra_payload(payload: bytes, state: "dict | None" = None) -> bytes:
    """MSP_MODE_RANGES_EXTRA(238)：count u8=20 + 每槽 3B（permanentId 与
    MODE_RANGES 槽位对齐、modeLogic=0 恒 OR、linkedTo=0——仿真无 linked
    mode，恒零语义段）。无参数源 → 空载荷降级。"""
    params = (state or {}).get("params")
    if params is None:
        return b""
    out = bytearray([MODE_RANGE_SLOTS])
    for i in range(MODE_RANGE_SLOTS):
        box_id = _slot_entry(params.virtual.mode_ranges, i)[0]
        out += struct.pack("<3B", _u8(box_id), 0, 0)
    return bytes(out)


def _set_mode_range(payload: bytes, state: "dict | None" = None) -> bytes:
    """MSP_SET_MODE_RANGE(35)：len≥5 消费；7 字节形态尾 2 字节（modeLogic/
    linkedTo）忽略（无 linked mode 语义），len 5 旧形态（尾部缺省 0）同容。

    布局 = slot u8 + permanentId u8 + auxChannelIndex u8 + startStep u8 +
    endStep u8 (+ modeLogic u8 + linkedTo u8)。slot ≥ MODE_RANGE_SLOTS
    整体忽略；列表补长（缺省 (0,0,0,0)）后写元组 (permanentId, aux,
    900+start×25, 900+end×25)，置 changed。start=end=0 → 900/900µs 在
    aux 实际域 1000-2000 永不激活（eval_boxes 语义下等价清空）。"""
    if state is None or len(payload) < 5:
        return b""
    params = state.get("params")
    if params is None:
        return b""
    slot, perm_id, aux, start, end = payload[:5]
    if slot >= MODE_RANGE_SLOTS:
        return b""
    ranges = params.virtual.mode_ranges
    while len(ranges) <= slot:
        ranges.append((0, 0, 0, 0))
    ranges[slot] = (perm_id, aux, _step_to_us(start), _step_to_us(end))
    _set_changed(state)
    return b""


# ---- OSD 组（Task 23） ----

def _elem_pos(virtual, item_id: int) -> int:
    """元素 u16 布局值合成：pos 11 位 = 覆盖布局 osd_layout[id]，缺省回落
    出厂默认表（未实现元素恒 0）；可见位 0x0800 = osd_enabled[id] 覆盖，
    缺省「pos 非零」（未显式关过的元素跟随位置位）。变体位（bits 14-15）
    不合成（恒 0，单形态元素）。"""
    stored = virtual.osd_layout.get(item_id, osd.DEFAULT_LAYOUT.get(item_id, 0))
    pos = stored & osd.OSD_POS_MASK
    visible = virtual.osd_enabled.get(item_id, pos != 0)
    return pos | (osd.OSD_VISIBLE if visible else 0)


def _osd_config_payload(payload: bytes, state: "dict | None" = None) -> bytes:
    """MSP_OSD_CONFIG(84)：190 字节固定布局（固件 4.5.1 msp.c 写序）。

    头部 10 字节：osdFlags u8=0x31、video_system u8=1(PAL)、units u8=0、
    rssi_alarm u8=0、cap_alarm u16=0、obsolete u8=0、item_count u8=80、
    alt_alarm u16=0；item_pos u16×80（位置/可见位合成见 _elem_pos）；
    统计段 count u8=0（fix round 1 裁定：固件写 count 后逐条写 31×u8，
    只报 count 不发条目会被 Configurator 无守卫的 count 循环吃掉后续段
    字节；模拟器无统计屏，count=0 循环 0 次解码干净）；计时器段
    count u8=2 + u16×2=0；警告段 obsolete u16=0 + count u8=19 + flags
    u32=0（警告段固件结构本身即 count+flags，无逐条字节，Configurator
    按 flags 位解析）；OSD profile 段 number=1/selected=1（单 profile
    模拟器）；overlay_radio_mode u8=0；camera_frame w/h=24/11；
    link_quality_alarm u16=0（API 1.46 尾）。总长
    10+160+1+5+7+2+1+2+2=190。

    与简报参考布局的差异（按外部源码实现，2026-11-12 GitHub 取固件 4.5.1
    tag 与 Configurator master 互证）：item_count 80 非 81（osd.h osd_items_e
    机械计数 0..79，与 Configurator chooseDisplayFields API 1.46 数组 80 项
    一致）；计时器段 count=2+u16×2 非 3+u16×3（osd.h osd_timer_e=2）。
    无参数源 → 空载荷降级。"""
    params = (state or {}).get("params")
    if params is None:
        return b""
    out = bytearray()
    out += struct.pack("<BBBBHBBH", _OSD_FLAGS, _OSD_VIDEO_SYSTEM, 0, 0, 0,
                       0, osd.OSD_ITEM_COUNT, 0)
    virtual = params.virtual
    for i in range(osd.OSD_ITEM_COUNT):
        out += struct.pack("<H", _elem_pos(virtual, i))
    out += bytes([0])                                        # 统计段 count=0
    out += bytes([_OSD_TIMER_COUNT]) + b"\x00\x00" * _OSD_TIMER_COUNT
    out += struct.pack("<HBI", 0, _OSD_WARNING_COUNT, 0)     # 警告段
    out += bytes([1, 1])                                     # profile：单 profile 选中 1
    out += bytes([0])                                        # overlay_radio_mode
    out += bytes([24, 11])                                   # camera_frame w/h
    out += struct.pack("<H", 0)                              # link_quality_alarm
    return bytes(out)


def _set_osd_config(payload: bytes, state: "dict | None" = None) -> bytes:
    """MSP_SET_OSD_CONFIG(85)：len≥3 消费 addr u8 + value u16（尾部可选
    screen u8 读出忽略；固件 4.5.1 msp.c 同款 3/4 字节双形态）。

    addr < 80 → 元素写：osd_layout[addr] = value & 0x07FF（仅位置位）、
    osd_enabled[addr] = bool(value & 0x0800)（可见位分解），置 changed；
    变体位（bits 14-15）丢弃。addr ≥ 80 整体忽略——含 0xFF 通用设置块
    (video_system/units/alarms/warnings/profile/camera)、0xFE 计时器块、
    screen=0 统计屏开关形态（virtual 域无对应存储，诚实降级不冒充）。"""
    if state is None or len(payload) < 3:
        return b""
    params = state.get("params")
    if params is None:
        return b""
    addr = payload[0]
    value = struct.unpack_from("<H", payload, 1)[0]
    # 尾部可选 screen u8 读出（固件缺省 1）：screen==0 且 addr < 统计计数
    # 为统计屏开关形态（固件分支一）——模拟器无统计屏域，整体忽略；
    # screen==0 但 addr ≥ 31 仍落元素写分支（固件 else-if 语义忠实还原）
    screen = payload[3] if len(payload) >= 4 else 1
    if screen == 0 and addr < _OSD_STAT_COUNT:
        return b""
    if addr >= osd.OSD_ITEM_COUNT:
        return b""
    params.virtual.osd_layout[addr] = value & osd.OSD_POS_MASK
    params.virtual.osd_enabled[addr] = bool(value & osd.OSD_VISIBLE)
    _set_changed(state)
    return b""


def _set_text(payload: bytes, state: "dict | None" = None) -> bytes:
    """MSP2_SET_TEXT(0x3007)：[类型 u8][长度 u8][文本字节]（Configurator
    MSPHelper.setText 先推长度字节、固件 msp.c sbufReadU8 读长度后按长收
    字符——简报布局缺长度字节，按外部源码实现）；len≥2 消费。

    类型 2 → virtual.craft_name = 文本[:32]（固件 MAX_NAME_LENGTH 截断
    语义）、置 changed；文本字节以长度为准（尾部多余字节丢弃），UTF-8
    容错解码。其余类型整体忽略（空载荷 ack）。"""
    if state is None or len(payload) < 2:
        return b""
    params = state.get("params")
    if params is None:
        return b""
    text_type, size = payload[0], payload[1]
    if text_type != 2:
        return b""
    params.virtual.craft_name = \
        payload[2:2 + size].decode("utf-8", "replace")[:32]
    _set_changed(state)
    return b""


def make_identity_handlers() -> dict[int, Callable[..., bytes]]:
    """命令表（身份组 + 状态组）：cmd → payload 载荷生成器。

    API_VERSION 自报 mspProtocolVersion=0（v1，引导 Configurator 走 v1 通道，
    与 protocol.py「应答恒走 v1」一致）+ API 1.46。

    handler 协议（Task 19）：``(payload, state=None) -> bytes``——state 为
    dispatch 注入的运行时状态快照（身份组不消费，状态组消费 flight 键，
    缺键防御见各 payload 函数）；state 缺省保证一期单参调用点保持合法
    （表内全部 handler 均支持单参/两参调用）。
    """
    return {
        # 1 MSP_API_VERSION：v1 协议 + API 1.46（4.5.1 对应的 API 版本）
        1: lambda _payload, _state=None: bytes([0, 1, 46]),
        # 2 MSP_FC_VARIANT：BTFL → Configurator 走 Betaflight 分支
        2: lambda _payload, _state=None: b"BTFL",
        # 3 MSP_FC_VERSION：固件版本 4.5.1
        3: lambda _payload, _state=None: b"4.5.1",
        # 4 MSP_BOARD_INFO：板名 + 板修订 0 + 标志 4 + 保留 8 字节
        4: lambda _payload, _state=None: _BOARD_NAME + struct.pack("<H", 0)
                                         + bytes([4]) + b"\0" * 8,
        # 5 MSP_BUILD_INFO：构建来源字符串（日期 + 时间 + 修订尾号）
        5: lambda _payload, _state=None: b"FSKY sim build 2026-11-12 00:00:00 26",
        # 160 MSP_UID：12 字节固定 unique id
        MSP_UID: lambda _payload, _state=None: _UID_PAYLOAD,
        # 12294 MSP2_GET_TEXT：连接序列的 BUILDKEY/CRAFT_NAME/PILOT_NAME
        MSP2_GET_TEXT: _get_text,
        # ---- 状态组（Task 20，Setup 页实时遥测） ----
        # 101 MSP_STATUS：共享头 11 字节（解码器只读 11 字节即停）
        101: lambda _payload, state=None: _status_payload(state, extended=False),
        # 150 MSP_STATUS_EX：24 字节（API 1.46 全尾）
        150: lambda _payload, state=None: _status_payload(state, extended=True),
        # 102 MSP_RAW_IMU：acc 静态 1g + gyro dps×4.1 + mag 0
        102: _raw_imu_payload,
        # 105 MSP_RC：[油门, yaw, roll, pitch, AUX1-4] µs
        105: _rc_payload,
        # 108 MSP_ATTITUDE：姿态角 decidegree
        108: _attitude_payload,
        # ---- 参数读写组（Task 21，Configurator PID tab） ----
        # 117 MSP_PIDNAMES：条目名 0x3b 分隔
        117: lambda _payload, _state=None: _PID_NAMES,
        # 112 MSP_PID / 202 MSP_SET_PID：5×3 u8（P/I ×1、D ×100）
        112: _pid_payload,
        202: _set_pid,
        # 111 MSP_RC_TUNING / 204 MSP_SET_RC_TUNING：rate 三轴 ×100（23B）
        111: _rc_tuning_payload,
        204: _set_rc_tuning,
        # 94 MSP_PID_ADVANCED / 95 MSP_SET_PID_ADVANCED：59B（TPA/cells）
        94: _pid_advanced_payload,
        95: _set_pid_advanced,
        # ---- 模式组（Task 22，Configurator Modes 页） ----
        # 116 MSP_BOXNAMES / 119 MSP_BOXIDS：显示名与 permanentId 表
        116: lambda _payload, _state=None: _BOX_NAMES,
        119: lambda _payload, _state=None: _BOX_IDS,
        # 34 MSP_MODE_RANGES / 35 MSP_SET_MODE_RANGE：20 槽 ×4B 步进编码
        34: _mode_ranges_payload,
        35: _set_mode_range,
        # 238 MSP_MODE_RANGES_EXTRA：count + 恒零语义段
        238: _mode_ranges_extra_payload,
        # ---- OSD 组（Task 23，Configurator OSD tab） ----
        # 84 MSP_OSD_CONFIG：190 字节固定布局（80 元素位置 + 可见位）
        84: _osd_config_payload,
        # 85 MSP_SET_OSD_CONFIG：元素位置/可见位写（其余块忽略）
        85: _set_osd_config,
        # 0x3007 MSP2_SET_TEXT：craft name 写入（[类型][长度][文本]）
        0x3007: _set_text,
    }
