# msp/handlers.py
"""MSP 命令表：身份组（虚拟飞控自报身份，纯标准库）。

命令号对齐 Betaflight Configurator（src/js/msp/MSPCodes.ts，2026-11 核对）：
1=MSP_API_VERSION、2=MSP_FC_VARIANT、3=MSP_FC_VERSION、4=MSP_BOARD_INFO、
5=MSP_BUILD_INFO、160=MSP_UID（v1 惯例值）。204=MSP_SET_RC_TUNING 为写
命令，身份组不应答（计划简报起草时误记 4/204 的映射，已修正）。

MSP2_GET_TEXT(0x3006=12294) 为 v2 命令（fix round 3）：连接序列在身份组
之后请求 BUILDKEY(5)/CRAFT_NAME(2)/PILOT_NAME(1)（serial_backend.ts
processBuildConfiguration/processCraftName），无应答会被判超时断开。
请求 payload=[类型 u8]，应答 payload=[类型 u8]+文本。
"""
import struct
from typing import Callable

# MSP_UID：Configurator MSPCodes.js 定义为 160（v1 惯例，已在线核对）
MSP_UID = 160

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


def _get_text(payload: bytes) -> bytes:
    """MSP2_GET_TEXT 应答：[类型 u8] + 文本；未知/空类型 → 空载荷降级。"""
    if not payload:
        return b""
    text_type = payload[0]
    text = _TEXT_TYPES.get(text_type)
    if text is None:
        return b""
    return bytes([text_type]) + text


def make_identity_handlers() -> dict[int, Callable[[bytes], bytes]]:
    """身份组命令表：cmd → payload 载荷生成器。

    API_VERSION 自报 mspProtocolVersion=0（v1，引导 Configurator 走 v1 通道，
    与 protocol.py「应答恒走 v1」一致）+ API 1.46。
    """
    return {
        # 1 MSP_API_VERSION：v1 协议 + API 1.46（4.5.1 对应的 API 版本）
        1: lambda _payload: bytes([0, 1, 46]),
        # 2 MSP_FC_VARIANT：BTFL → Configurator 走 Betaflight 分支
        2: lambda _payload: b"BTFL",
        # 3 MSP_FC_VERSION：固件版本 4.5.1
        3: lambda _payload: b"4.5.1",
        # 4 MSP_BOARD_INFO：板名 + 板修订 0 + 标志 4 + 保留 8 字节
        4: lambda _payload: _BOARD_NAME + struct.pack("<H", 0)
                            + bytes([4]) + b"\0" * 8,
        # 5 MSP_BUILD_INFO：构建来源字符串（日期 + 时间 + 修订尾号）
        5: lambda _payload: b"FSKY sim build 2026-11-12 00:00:00 26",
        # 160 MSP_UID：12 字节固定 unique id
        MSP_UID: lambda _payload: _UID_PAYLOAD,
        # 12294 MSP2_GET_TEXT：连接序列的 BUILDKEY/CRAFT_NAME/PILOT_NAME
        MSP2_GET_TEXT: _get_text,
    }
