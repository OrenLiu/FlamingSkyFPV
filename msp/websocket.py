# msp/websocket.py
"""RFC6455 WebSocket 最小子集（纯标准库，浏览器版 Configurator 兼容）。

浏览器版 Betaflight Configurator 的 "tcp" 槽位实为 WebSocket（serial.ts
注释原文），且 WebSocket.ts 为二进制透传（无 JSON 封装）——MSP 字节流
直接走 WS 二进制帧，桌面版走 TCP、浏览器版走本模块。

范围（最小实现）：
- 握手：``websocket_handshake`` 解析 HTTP 升级请求，应答 101 +
  ``Sec-WebSocket-Accept = base64(sha1(key + GUID))``；
- 服务器发帧不掩码（RFC6455 服务器侧规范）：``build_binary_frame`` /
  ``build_pong`` / ``build_close``，长度 126 起扩展为 u16/u64；
- 客户端帧必掩码：``parse_client_frame`` 掩码异或还原载荷，返回
  (opcode, payload, consumed)；帧不完整返回 None 等待续传，协议违规
  （未掩码/超限长度）抛 ValueError 由调用方关闭连接；
- 分片：FIN=0 首片返回其自身 opcode（binary=0x2）、continuation 返回
  0x0，重组由调用方按序拼接（调用方可从缓冲首字节 FIN 位判断结束）。

依赖方向约束：msp/ 仅用标准库，禁 import 渲染/UI/pygame 与第三方库。
"""
import base64
import hashlib
import struct

# RFC6455 §1.3 握手 GUID（协议常量，全部实现必须一致）
_WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

# 子协议 token 合法字符集（RFC2616 token：alnum + 保留符号，不含
# separators/CTL——回显值直接拼入应答头，非法字符即潜在头注入）
_TOKEN_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    "!#$%&'*+-.^_`|~"
)

# 操作码（RFC6455 §5.2）
OP_CONT = 0x0     # continuation（分片续帧）
OP_TEXT = 0x1     # 文本帧（Configurator 不用，服务器侧忽略）
OP_BINARY = 0x2   # 二进制帧（MSP 字节流）
OP_CLOSE = 0x8
OP_PING = 0x9
OP_PONG = 0xA

# 客户端数据帧载荷上限：防异常长度声明（u64 可表达 2^64）驻留连接；
# Configurator 的 MSP 帧（v1 上限 4096）远小于此
_MAX_CLIENT_PAYLOAD = 1 << 20


def websocket_handshake(request: bytes) -> bytes | None:
    """解析客户端握手请求，返回 101 应答字节流；非法/非握手返回 None。

    仅要求 GET 起始、Upgrade: websocket、含 Sec-WebSocket-Key（其余头
    宽松忽略，浏览器实况冗余头多）。应答含 RFC 规定的
    Sec-WebSocket-Accept = base64(sha1(key + GUID))。

    子协议协商：请求带 Sec-WebSocket-Protocol（逗号分隔列表）时回显
    第一个 token——浏览器按规范强制：请求非空而应答缺失则拒绝连接
    （code 1006，Configurator 以 ``new WebSocket(addr, ["binary"])``
    发起，实测踩中）。回显值校验 RFC2616 token 字符集（防头注入），
    非法或不含该头则应答不出现该头。
    """
    try:
        text = request.decode("latin-1")
    except UnicodeDecodeError:
        return None  # latin-1 全字节可映射，纯防御
    head, _, _rest = text.partition("\r\n\r\n")
    lines = head.split("\r\n")
    if not lines or len(lines[0].split()) < 3:
        return None
    if lines[0].split()[0].upper() != "GET":
        return None  # MSP 帧等非 HTTP 字节在此被拒
    headers: dict[str, str] = {}
    for line in lines[1:]:
        name, sep, value = line.partition(":")
        if sep:
            headers[name.strip().lower()] = value.strip()
    if headers.get("upgrade", "").lower() != "websocket":
        return None
    key = headers.get("sec-websocket-key")
    if not key:
        return None
    accept = base64.b64encode(
        hashlib.sha1((key + _WS_GUID).encode("ascii")).digest()
    ).decode("ascii")
    resp = (
        "HTTP/1.1 101 Switching Protocols\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Accept: {accept}\r\n"
    )
    first_protocol = headers.get("sec-websocket-protocol",
                                 "").split(",")[0].strip()
    if first_protocol and set(first_protocol) <= _TOKEN_CHARS:
        resp += f"Sec-WebSocket-Protocol: {first_protocol}\r\n"
    return (resp + "\r\n").encode("ascii")


def build_binary_frame(payload: bytes, fin: bool = True) -> bytes:
    """构造服务器→客户端二进制帧（服务器帧不掩码）。"""
    return _build_frame(OP_BINARY, payload, fin)


def build_pong(payload: bytes = b"") -> bytes:
    """构造 pong 帧：回显 ping 的应用数据（RFC6455 §5.5.3）。"""
    return _build_frame(OP_PONG, payload)


def build_close(code: int = 1000) -> bytes:
    """构造 close 帧：载荷为 u16 大端状态码（1000=正常关闭）。"""
    return _build_frame(OP_CLOSE, struct.pack(">H", code))


def _build_frame(opcode: int, payload: bytes, fin: bool = True) -> bytes:
    """帧头 + 裸载荷：len<126 内联、<65536 用 u16、否则 u64（大端）。"""
    b0 = (0x80 if fin else 0x00) | opcode
    n = len(payload)
    if n < 126:
        head = bytes([b0, n])
    elif n < 65536:
        head = bytes([b0, 126]) + struct.pack(">H", n)
    else:
        head = bytes([b0, 127]) + struct.pack(">Q", n)
    return head + payload


def parse_client_frame(buf: bytes | bytearray) -> tuple[int, bytes, int] | None:
    """解析缓冲首部的客户端帧（必须掩码）。

    返回 ``(opcode, unmasked_payload, consumed)``；帧未收全返回 None
    （调用方保留缓冲等待续传）。协议违规（未掩码/载荷超限）抛
    ValueError——RFC6455 要求服务器此时失败连接，调用方负责关闭。

    FIN 位不由本函数返回：调用方可直接读 ``buf[0] & 0x80`` 判断该帧
    是否为消息末片（帧头首字节恒在缓冲最前）。
    """
    if len(buf) < 2:
        return None
    opcode = buf[0] & 0x0F
    masked = bool(buf[1] & 0x80)
    if not masked:
        raise ValueError("客户端帧未掩码（RFC6455 §5.1 强制）")
    length = buf[1] & 0x7F
    offset = 2
    if length == 126:
        if len(buf) < 4:
            return None
        length = struct.unpack_from(">H", buf, 2)[0]
        offset = 4
    elif length == 127:
        if len(buf) < 10:
            return None
        length = struct.unpack_from(">Q", buf, 2)[0]
        offset = 10
    if length > _MAX_CLIENT_PAYLOAD:
        raise ValueError(f"客户端帧载荷超限：{length}")
    mask_start = offset
    frame_end = mask_start + 4 + length
    if len(buf) < frame_end:
        return None  # 掩码键或载荷未收全
    mask = buf[mask_start:mask_start + 4]
    payload = bytearray(buf[mask_start + 4:frame_end])
    for i in range(len(payload)):
        payload[i] ^= mask[i & 3]
    return opcode, bytes(payload), frame_end
