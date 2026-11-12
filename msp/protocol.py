# msp/protocol.py
"""MSP 帧编解码（协议层地基，纯标准库）。

MSPv1 完整收发：帧布局 `$M dir lenLo lenHi cmdLo cmdHi payload crc`
（len/cmd 均为 u16 LE，len 在 cmd 之前，与固件 msp.c serialHeaderV1_t 一致）；
CRC8-DWD-SID86 覆盖 dir+len+cmd+payload（`$M` 前导不参与）。

MSPv2 识别丢弃：`$X dir flag cmd(u16LE) len(u24LE) payload crc` 读出长度后
整帧跳过、不计坏帧——应答恒走 v1（API_VERSION 的 mspProtocolVersion=0
引导 Configurator 用 v1）。

依赖方向约束：msp/ 禁 import 渲染/UI/pygame。
"""
import struct
from dataclasses import dataclass

# v1 payload 上限：超限视为坏帧丢弃并计数
MSP_V1_MAX_PAYLOAD = 4096

# 头部长度（字节）
_V1_HEADER_LEN = 2 + 1 + 2 + 2   # $M + dir + len(u16) + cmd(u16)
_V2_HEADER_LEN = 2 + 1 + 1 + 2 + 3  # $X + dir + flag + cmd(u16) + len(u24)


@dataclass
class MspRequest:
    """一条解析出的 MSP 请求（direction "<" 主机→FC / ">" FC 侧帧）"""
    direction: str
    cmd: int
    payload: bytes


def crc8(data: bytes, crc: int = 0) -> int:
    """CRC-8-DWD-SID86：poly 0xD5、初值 0、MSB-first、无终值异或。

    ``crc`` 参数支持分段累积（把上一段返回值传入下一段）。
    """
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ (0xD5 if crc & 0x80 else 0)) & 0xFF
    return crc


def build_v1_frame(direction: str, cmd: int, payload: bytes) -> bytes:
    """构造 MSPv1 帧：$M dir len(u16LE) cmd(u16LE) payload crc8。

    CRC 从 dir 字节起覆盖 dir+len+cmd+payload。
    """
    body = (direction.encode("ascii")
            + struct.pack("<HH", len(payload), cmd) + payload)
    return b"$M" + body + bytes([crc8(body)])


class CrcMspStreamParser:
    """跨 feed 分段重组的 MSP 流式解析器（bytearray 状态机）。

    坏帧（CRC 失败 / v1 len 超限）丢弃并计入 ``dropped_frames``，
    同时 resync 到缓冲中下一个 ``$``；MSPv2 帧识别长度后整帧丢弃、
    不计坏帧。
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
            return self._parse_v2()
        del buf[:1]  # 未知前导（$ 后非 M/X），跳过该 $ 继续找
        return True

    def _parse_v1(self, requests: list[MspRequest]) -> bool:
        buf = self._buf
        if len(buf) < _V1_HEADER_LEN:
            return False  # 头部未收全
        plen = buf[3] | (buf[4] << 8)
        if plen > MSP_V1_MAX_PAYLOAD:
            self.dropped_frames += 1
            self._resync()
            return True
        total = _V1_HEADER_LEN + plen + 1
        if len(buf) < total:
            return False  # 等 payload + crc 收全
        body = bytes(buf[2:2 + 1 + 2 + 2 + plen])  # dir+len+cmd+payload
        if crc8(body) != buf[_V1_HEADER_LEN + plen]:
            self.dropped_frames += 1
            self._resync()
            return True
        requests.append(MspRequest(
            direction=chr(buf[2]),
            cmd=buf[5] | (buf[6] << 8),
            payload=bytes(buf[_V1_HEADER_LEN:_V1_HEADER_LEN + plen]),
        ))
        del buf[:total]
        return True

    def _parse_v2(self) -> bool:
        # MSPv2：识别长度后整帧丢弃，不计坏帧（应答恒走 v1）
        buf = self._buf
        if len(buf) < _V2_HEADER_LEN:
            return False
        plen = buf[6] | (buf[7] << 8) | (buf[8] << 16)
        total = _V2_HEADER_LEN + plen + 1
        if len(buf) < total:
            return False
        del buf[:total]
        return True

    def _resync(self) -> None:
        """丢弃坏帧剩余部分：跳过当前 $，保留缓冲中下一个 $ 起的内容"""
        nxt = self._buf.find(b"$", 1)
        if nxt < 0:
            self._buf.clear()
        else:
            del self._buf[:nxt]
