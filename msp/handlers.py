# msp/handlers.py
"""MSP 命令表：身份组 + 状态组 + 参数读写组（纯标准库，不 import 项目模块）。

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

MSP2_GET_TEXT(0x3006=12294) 为 v2 命令（fix round 3）：连接序列在身份组
之后请求 BUILDKEY(5)/CRAFT_NAME(2)/PILOT_NAME(1)（serial_backend.ts
processBuildConfiguration/processCraftName），无应答会被判超时断开。
请求 payload=[类型 u8]，应答 payload=[类型 u8]+文本。

状态组载荷布局经外部源码核对（2026-11-12 GitHub 取 tag 4.5.1 的
src/main/msp/msp.c L1080-1133 与 Configurator src/js/msp/MSPHelper.ts
L841-903）：STATUS_EX 与固件写序逐字段一致；STATUS 应答 11 字节止于
profile（Configurator 解码器只读 11 字节即停、尾字节忽略——真实固件续写
load/gyroCycleTime/禁锁段共 24 字节，本表从简，差异记录于 Task 20 提交）。
"""
import struct
from typing import Callable

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

# MSP2TextType（MSPCodes.ts MSP2TextType 枚举，2026-11 核对）：GET 方向
# 合法类型 → 应答文本（空串即 [类型字节] 单字节载荷）
_TEXT_TYPES: dict[int, bytes] = {
    1: b"",            # PILOT_NAME
    2: b"FSKY-SIM",    # CRAFT_NAME（Setup 页机名显示）
    3: b"",            # PID_PROFILE_NAME
    4: b"",            # RATE_PROFILE_NAME
    5: b"",            # BUILDKEY（非 32 字长 → Configurator 跳过云构建选项）
    6: b"",            # RELEASENAME（GET 方向固件支持）
    11: b"",           # BATTERY_PROFILE_NAME
}

# 板名/构建自报（设计文档规定值；25 尾号预留扩展）
_BOARD_NAME = b"FSKY"

# UID 应答：12 字节固定值（三个 u32，模拟固定硬件 unique id；每次启动一致）
_UID_PAYLOAD = struct.pack("<3I", 0x46534B59, 0x56464331, 0x32363131)
assert len(_UID_PAYLOAD) == 12


def _get_text(payload: bytes, state: "dict | None" = None) -> bytes:
    """MSP2_GET_TEXT 应答：[类型 u8] + 文本；未知/空类型 → 空载荷降级。

    state 为 Task 19 二参协议的第二个参数（身份组不消费，带缺省保兼容）。"""
    if not payload:
        return b""
    text_type = payload[0]
    text = _TEXT_TYPES.get(text_type)
    if text is None:
        return b""
    return bytes([text_type]) + text


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
    }
