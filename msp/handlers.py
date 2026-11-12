# msp/handlers.py
"""MSP 命令表：身份组 + 状态组（虚拟飞控自报身份与实时遥测，纯标准库）。

命令号对齐 Betaflight Configurator（src/js/msp/MSPCodes.ts，2026-11 核对）：
1=MSP_API_VERSION、2=MSP_FC_VARIANT、3=MSP_FC_VERSION、4=MSP_BOARD_INFO、
5=MSP_BUILD_INFO、160=MSP_UID（v1 惯例值）。204=MSP_SET_RC_TUNING 为写
命令，身份组不应答（计划简报起草时误记 4/204 的映射，已修正）。
状态组（Task 20，Setup 页实时遥测）：101=MSP_STATUS、150=MSP_STATUS_EX、
102=MSP_RAW_IMU、105=MSP_RC、108=MSP_ATTITUDE。

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
    }
