# msp/protocol.py
"""MSP 帧编解码（协议层地基，纯标准库）。

MSPv1 真实线格式（权威源：Configurator master ``src/js/msp.ts``
``encode_message_v1`` 与解码器 ``BYTE_HANDLERS``，2026-11-12 抓取核实；
固件 msp.c 同构）：

``$M dir len(u8) cmd(u8) payload checksum``

- checksum = XOR 校验和（len、cmd、payload 逐字节异或；**非 CRC8**，
  CRC8-DWD-SID86 仅 MSPv2 使用）
- ``len==0xFF`` 为 jumbo 标记：真实长度在 cmd 之后的 u16LE
  （线序 ``$M dir FF cmd lenLo lenHi payload``，checksum 多异或 0xFF
  与两个长度字节）
- v1 的 cmd 为 u8：命令号 >254 时 Configurator 走 v2
  （``code <= 254 ? encode_message_v1 : encode_message_v2``）

MSPv2（fix round 3 起解析并应答，替代旧的"识别后整帧丢弃"策略）：

``$X dir flag(u8) cmd(u16LE) len(u16LE) payload crc8``

- crc8 = CRC-8-DWD-SID86，覆盖 flag + cmd + len + payload（不含
  ``$X`` 前导与 dir，对齐 ``encode_message_v2`` 的
  ``crc8_dvb_s2_data(bufView, 3, end)``）
- len 为 **u16LE**（Configurator 编解码器各状态机一致；任务早期按
  u24 读系误记，弃用）
- 连接序列中的 ``MSP2_GET_TEXT(0x3006=12294)`` 走本格式（v2 命令
  >254），无应答会被 Configurator 判超时断开——v2 必须应答

> 布局勘误（fix round 2，Task 18 浏览器实测）：本模块曾实现
> ``len/cmd 均 u16 + CRC8`` 的变体，浏览器实发的 6 字节
> ``$M<\\x00\\x01\\x01`` 被读成 len=256 的巨帧、静默停转（500ms×2
> 超时后 Configurator 断开）。Task 16 及更早的自测因 build/parse 自洽
> 而未暴露；对齐真实线格式后双向互认。

依赖方向约束：msp/ 禁 import 渲染/UI/pygame。
"""
import struct
from dataclasses import dataclass

# v1 payload 上限：超限视为坏帧丢弃并计数（真实 jumbo 上限 65535，
# Configurator 实际请求远小于此，4096 为防滥用上限；v2 同限）
MSP_V1_MAX_PAYLOAD = 4096

# jumbo 标记：len 字节 0xFF 表示真实长度在 cmd 后的 u16LE
_JUMBO_MARKER = 0xFF

# 头部长度（字节）：$M + dir + len(u8) + cmd(u8)；jumbo 头再加 u16
_V1_HEADER_LEN = 2 + 1 + 1 + 1
_V1_JUMBO_HEADER_LEN = _V1_HEADER_LEN + 2
_V2_HEADER_LEN = 2 + 1 + 1 + 2 + 2  # $X + dir + flag + cmd(u16) + len(u16)


@dataclass
class MspRequest:
    """一条解析出的 MSP 请求（direction "<" 主机→FC / ">" FC 侧帧）。

    ``v2`` 标记请求来自 MSPv2 帧（应答须按 ``build_v2_frame`` 包帧）；
    默认 False 兼容既有 v1 调用点。
    """
    direction: str
    cmd: int
    payload: bytes
    v2: bool = False


def crc8(data: bytes, crc: int = 0) -> int:
    """CRC-8-DWD-SID86（MSPv2 校验算法）：poly 0xD5、初值 0、MSB-first、
    无终值异或。``crc`` 参数支持分段累积。

    （MSPv1 使用 XOR 校验和，见 build_v1_frame；本函数保留供 v2 构帧
    与测试向量使用。）
    """
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ (0xD5 if crc & 0x80 else 0)) & 0xFF
    return crc


def build_v1_frame(direction: str, cmd: int, payload: bytes) -> bytes:
    """构造 MSPv1 帧（8 位线格式）：

    ``$M dir len(u8) cmd(u8) payload checksum``；``len >= 255`` 走
    jumbo：``$M dir 0xFF cmd len(u16LE) payload checksum``。

    checksum = XOR(len[, jumbo 标记与长度字节], cmd, payload)。
    """
    if not 0 <= cmd <= 0xFF:
        # v1 命令号 u8；Configurator 对 >254 的命令一律走 v2（msp.ts:484）
        raise ValueError(f"v1 命令号须 u8 域（0-255）：{cmd}")
    n = len(payload)
    if n < _JUMBO_MARKER:
        head = (b"$M" + direction.encode("ascii")
                + struct.pack("<BB", n, cmd))
        checksum = n ^ cmd
    else:
        head = (b"$M" + direction.encode("ascii")
                + struct.pack("<BBH", _JUMBO_MARKER, cmd, n))
        checksum = _JUMBO_MARKER ^ cmd ^ (n & 0xFF) ^ (n >> 8)
    for byte in payload:
        checksum ^= byte
    return head + payload + bytes([checksum & 0xFF])


def build_v2_frame(direction: str, cmd: int, payload: bytes,
                   flag: int = 0) -> bytes:
    """构造 MSPv2 帧：``$X dir flag(u8) cmd(u16LE) len(u16LE) payload
    crc8``。crc8 覆盖 flag+cmd+len+payload（不含前导与 dir）。"""
    if not 0 <= cmd <= 0xFFFF:
        raise ValueError(f"v2 命令号须 u16 域（0-65535）：{cmd}")
    if len(payload) > 0xFFFF:
        raise ValueError(f"v2 载荷超 u16 长度域：{len(payload)}")
    body = struct.pack("<BHH", flag, cmd, len(payload)) + payload
    return (b"$X" + direction.encode("ascii") + body
            + bytes([crc8(body)]))


class CrcMspStreamParser:
    """跨 feed 分段重组的 MSP 流式解析器（bytearray 状态机）。

    类名中的 Crc 为 Task 15 接口历史命名（v1 实为 XOR 校验和）。

    坏帧（校验失败 / len 超限，v1 v2 同规则）丢弃并计入
    ``dropped_frames``，同时 resync 到缓冲中下一个 ``$``。
    """

    def __init__(self) -> None:
        self._buf = bytearray()
        self.dropped_frames = 0

    def feed(self, chunk: bytes) -> list[MspRequest]:
        """送入一段字节流，返回本轮完整解析出的请求列表。"""
        self._buf += chunk
        requests: list[MspRequest] = []
        # _parse_one 返回 True 表示有进展（解析出一帧或丢弃了一段），继续尝试
        while self._parse_one(requests):
            pass
        return requests

    # ---- 内部状态机 -------------------------------------------------

    def _parse_one(self, requests: list[MspRequest]) -> bool:
        """尝试从缓冲解析一帧。返回是否取得进展（可继续循环）。"""
        buf = self._buf
        start = buf.find(b"$")
        if start < 0:
            buf.clear()  # 无 $ 前导的纯垃圾，直接丢弃（不计坏帧）
            return False
        del buf[:start]  # 丢弃 $ 前的垃圾字节
        if len(buf) < 2:
            return False  # 只有孤立的 $，等下一字节判定版本
        if buf[1] == 0x4D:  # 'M'
            return self._parse_v1(requests)
        if buf[1] == 0x58:  # 'X'
            return self._parse_v2(requests)
        del buf[:1]  # 未知前导（$ 后非 M/X），跳过该 $ 继续找
        return True

    def _parse_v1(self, requests: list[MspRequest]) -> bool:
        """8 位 v1 帧解析：len(u8)（0xFF→jumbo u16）cmd(u8) XOR 校验。"""
        buf = self._buf
        if len(buf) < _V1_HEADER_LEN:
            return False  # 头部未收全
        cmd = buf[4]
        if buf[3] == _JUMBO_MARKER:
            if len(buf) < _V1_JUMBO_HEADER_LEN:
                return False  # jumbo 长度未收全
            plen = buf[5] | (buf[6] << 8)
            header_len = _V1_JUMBO_HEADER_LEN
            # checksum 覆盖：0xFF ^ cmd ^ lenLo ^ lenHi ^ payload
            checksum = _JUMBO_MARKER ^ cmd ^ buf[5] ^ buf[6]
        else:
            plen = buf[3]
            header_len = _V1_HEADER_LEN
            checksum = plen ^ cmd
        if plen > MSP_V1_MAX_PAYLOAD:
            self.dropped_frames += 1
            self._resync()
            return True
        total = header_len + plen + 1
        if len(buf) < total:
            return False  # 等 payload + 校验字节收全
        for i in range(header_len, header_len + plen):
            checksum ^= buf[i]
        if checksum & 0xFF != buf[total - 1]:
            self.dropped_frames += 1
            self._resync()
            return True
        requests.append(MspRequest(
            direction=chr(buf[2]),
            cmd=cmd,
            payload=bytes(buf[header_len:header_len + plen]),
        ))
        del buf[:total]
        return True

    def _parse_v2(self, requests: list[MspRequest]) -> bool:
        """MSPv2 帧解析（fix round 3：由丢弃改为解析入队）：
        ``$X dir flag cmd(u16LE) len(u16LE) payload crc8``，
        crc8 覆盖 flag+cmd+len+payload。"""
        buf = self._buf
        if len(buf) < _V2_HEADER_LEN:
            return False  # 头部未收全
        plen = buf[6] | (buf[7] << 8)
        if plen > MSP_V1_MAX_PAYLOAD:
            self.dropped_frames += 1
            self._resync()
            return True
        total = _V2_HEADER_LEN + plen + 1
        if len(buf) < total:
            return False  # 等 payload + crc 收全
        # crc8 覆盖 flag+cmd+len+payload：即 buf[3] 起至 payload 末
        if crc8(bytes(buf[3:_V2_HEADER_LEN + plen])) != buf[total - 1]:
            self.dropped_frames += 1
            self._resync()
            return True
        requests.append(MspRequest(
            direction=chr(buf[2]),
            cmd=buf[4] | (buf[5] << 8),
            payload=bytes(buf[_V2_HEADER_LEN:_V2_HEADER_LEN + plen]),
            v2=True,
        ))
        del buf[:total]
        return True

    def _resync(self) -> None:
        """丢弃坏帧剩余部分：跳过当前 $，保留缓冲中下一个 $ 起的内容"""
        nxt = self._buf.find(b"$", 1)
        if nxt < 0:
            self._buf.clear()
        else:
            del self._buf[:nxt]
