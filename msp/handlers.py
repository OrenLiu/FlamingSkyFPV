# msp/handlers.py
"""MSP 命令表：身份组（虚拟飞控自报身份，纯标准库）。

命令号对齐 Betaflight Configurator（src/js/msp/MSPCodes.js，2026-11 核对）：
1=MSP_API_VERSION、2=MSP_FC_VARIANT、3=MSP_FC_VERSION、4=MSP_BOARD_INFO、
160=MSP_UID（v1 惯例值）。204 为设计文档规定的自报载荷（非真实
MSP_SET_RC_TUNING 语义，见 docs/plans/2026-11-12-phase2.md §Task 16）。
"""
import struct
from typing import Callable

# MSP_UID：Configurator MSPCodes.js 定义为 160（v1 惯例，已在线核对）
MSP_UID = 160

# 板名/构建自报（设计文档规定值；25 尾号预留扩展）
_BOARD_NAME = b"FSKY"

# UID 应答：12 字节固定值（三个 u32，模拟固定硬件 unique id；每次启动一致）
_UID_PAYLOAD = struct.pack("<3I", 0x46534B59, 0x56464331, 0x32363131)
assert len(_UID_PAYLOAD) == 12


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
        # 4 MSP_BOARD_INFO（设计文档规定自报值：板名 + 构建时间戳 + 扩展位）
        4: lambda _payload: b"FSKY sim build 2026-11-12 00:00:00 26",
        # 204（设计文档规定自报值：板名 + 版本 0 + 标志 4 + 保留 8 字节）
        204: lambda _payload: _BOARD_NAME + struct.pack("<H", 0)
                              + bytes([4]) + b"\0" * 8,
        # 160 MSP_UID：12 字节固定 unique id
        MSP_UID: lambda _payload: _UID_PAYLOAD,
    }
