# msp/server.py
"""虚拟飞控 MSP 服务器（TCP + PTY + WebSocket 三传输，纯标准库）。

线程模型（设计 §10，状态单线程访问）：
- 每连接一个读线程：TCP/WS ``socket.settimeout(0.05)`` 循环 recv / PTY
  ``select`` 超时轮询 ``os.read(master_fd)`` → ``CrcMspStreamParser.feed``
  → ``(发送器, 请求)`` 入队；读线程只读不写（除 dropped 计数聚合
  int 自增 GIL 下原子，及 WS 例外：握手应答/pong/close 控制帧在线程内
  回写，与主线程应答共用 per-transport 锁防交织）。
- ``drain()`` 由主循环每帧调用（未启动直返）：非阻塞 accept 新连接 →
  取空请求队列 → 分派 ``handlers[cmd]`` → 应答 sendall/写 master；
  WS 连接应答经 ``build_binary_frame`` 包帧（按 transport 类型区分）。
  应答在主线程生成，handlers 状态访问无锁。
- ``stop()`` 幂等：置停止标志 → 关 listen/连接/PTY fd（读线程 recv/read
  立即出错退出）→ join 收线程 → 状态复位（可重新 start）。

WebSocket（浏览器版 Configurator）：读线程内完成 RFC6455 握手后，二进制
帧 payload 剥壳喂 MSP 解析器（等价 TCP 字节流），应答按连接类型包 WS 帧。
"""
import os
import pty
import queue
import select
import socket
import sys
import threading
import tty
from typing import Callable

from msp.protocol import CrcMspStreamParser, MspRequest, build_v1_frame
from msp.websocket import (
    OP_BINARY,
    OP_CLOSE,
    OP_CONT,
    OP_PING,
    OP_TEXT,
    build_binary_frame,
    build_close,
    build_pong,
    parse_client_frame,
    websocket_handshake,
)

_RECV_TIMEOUT = 0.05   # 秒：读线程收包超时（周期检查停止标志）
_CHUNK = 4096          # 单次 recv/read 上限
_TCP_HOST = "127.0.0.1"
_TCP_BACKLOG = 4
_WS_PORT_DEFAULT = 5762      # 浏览器版 WS 默认端口（与 TCP 5761 并存）
_WS_HANDSHAKE_MAX = 8192     # 握手请求字节上限（超限视为恶意/坏连接）


class _Transport:
    """统一 TCP socket / WS socket / PTY master fd 的发送接口（drain 应答回写用）。

    ``ws=True`` 时数据经 ``build_binary_frame`` 包帧（MSP 字节流 → WS
    二进制帧）；发送加锁——WS 的 pong（读线程）与应答（主线程）可能
    并发写同一 socket，防止帧字节交织。
    """

    def __init__(self, sock: socket.socket | None = None,
                 fd: int | None = None, ws: bool = False):
        self.sock = sock
        self.fd = fd
        self.ws = ws
        self._send_lock = threading.Lock()

    def send(self, data: bytes) -> None:
        if self.ws:
            data = build_binary_frame(data)
        with self._send_lock:
            if self.sock is not None:
                self.sock.sendall(data)
            else:
                os.write(self.fd, data)


class VfcServer:
    """虚拟飞控 MSP 服务器骨架：双传输 + 身份组命令分派"""

    def __init__(self, handlers: dict[int, Callable[[bytes], bytes]]):
        self._handlers = handlers
        self._listen_sock: socket.socket | None = None
        self._tcp_port: int | None = None
        self._ws_listen_sock: socket.socket | None = None
        self._ws_port: int | None = None
        self._conns: list[socket.socket] = []   # 已 accept 的 TCP/WS 连接
        self._master_fd: int | None = None      # PTY master（读线程持有收）
        self._slave_fd: int | None = None       # 服务器自持 slave（防 EIO 忙转）
        self._threads: list[threading.Thread] = []
        self._queue: queue.Queue = queue.Queue()  # (transport, MspRequest)
        self._stopping = threading.Event()
        self._running = False
        self._dropped = 0   # 全连接累计坏帧数（读线程聚合，GIL 下原子自增）
        self.slave_path: str | None = None

    # ---- 属性 ----

    @property
    def tcp_address(self) -> str:
        """TCP 接入地址；未启动返回空串（UI 提示框显示占位）"""
        if self._tcp_port is None:
            return ""
        return f"tcp://{_TCP_HOST}:{self._tcp_port}"

    @property
    def websocket_address(self) -> str:
        """WebSocket 接入地址（浏览器版 Configurator）；未启动返回空串"""
        if self._ws_port is None:
            return ""
        return f"ws://{_TCP_HOST}:{self._ws_port}"

    @property
    def connected(self) -> bool:
        """任一传输已启动（服务器运行中）"""
        return self._running

    @property
    def dropped_frames(self) -> int:
        """全部连接累计丢弃的坏帧数（CRC 失败/超限，见 protocol.py）"""
        return self._dropped

    # ---- 启停 ----

    def start(self, port: int = 5761) -> None:
        """三传输开启（TCP+PTY+WS）：TCP 失败（端口占用）抛 OSError；
        PTY/WS 失败降级（stderr 提示，仅其余传输可用，避免半启动态异常）"""
        self.start_tcp(port)
        self.start_pty()
        try:
            self.start_websocket()
        except OSError as e:
            print(f"[msp] WebSocket 启动失败，仅 TCP/PTY 接入：{e}",
                  file=sys.stderr)

    def start_tcp(self, port: int = 5761) -> None:
        """TCP 传输：bind 127.0.0.1:port（port=0 内核分配）；占用抛 OSError"""
        if self._listen_sock is not None:
            return  # 幂等：已启动
        ls = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        ls.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            ls.bind((_TCP_HOST, port))
            ls.listen(_TCP_BACKLOG)
        except OSError:
            ls.close()
            raise  # 端口占用等向上传播（UI 捕获提示，不进入半启动态）
        ls.settimeout(0)  # drain() 非阻塞 accept
        self._listen_sock = ls
        self._tcp_port = ls.getsockname()[1]
        self._running = True

    def start_pty(self) -> None:
        """PTY 传输：master 读线程持有收包；失败打印降级（仅 TCP）"""
        if self._master_fd is not None:
            return  # 幂等：已启动
        try:
            master_fd, slave_fd = pty.openpty()
        except OSError as e:
            print(f"[msp] PTY 创建失败，仅 TCP 接入：{e}", file=sys.stderr)
            return
        # slave_fd 由服务器自持：外部地面站断开后 master 读不会陷入 EIO 忙转
        self._master_fd, self._slave_fd = master_fd, slave_fd
        # 二进制透传：PTY 线路默认 canonical+ECHO（行规程扣帧/回显污染），
        # 必须整条线路 raw 化（termios 属性作用于线路，置 slave 侧即可）
        tty.setraw(slave_fd)
        self.slave_path = os.ttyname(slave_fd)
        t = threading.Thread(target=self._pty_reader, args=(master_fd,),
                             daemon=True, name="msp-pty-reader")
        t.start()
        self._threads.append(t)
        self._running = True

    def start_websocket(self, port: int = _WS_PORT_DEFAULT) -> None:
        """WebSocket 传输：bind 127.0.0.1:port（port=0 内核分配）；
        占用抛 OSError（drain 非阻塞 accept，握手在读线程内完成）"""
        if self._ws_listen_sock is not None:
            return  # 幂等：已启动
        ls = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        ls.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            ls.bind((_TCP_HOST, port))
            ls.listen(_TCP_BACKLOG)
        except OSError:
            ls.close()
            raise  # 端口占用等向上传播（不进入半启动态）
        ls.settimeout(0)  # drain() 非阻塞 accept
        self._ws_listen_sock = ls
        self._ws_port = ls.getsockname()[1]
        self._running = True

    def stop(self) -> None:
        """关全部连接与传输（幂等）：先关 fd 令读线程立即退出再 join"""
        self._stopping.set()
        if self._listen_sock is not None:
            try:
                self._listen_sock.close()
            except OSError:
                pass
            self._listen_sock = None
            self._tcp_port = None
        if self._ws_listen_sock is not None:
            try:
                self._ws_listen_sock.close()
            except OSError:
                pass
            self._ws_listen_sock = None
            self._ws_port = None
        for sock in self._conns:
            try:
                sock.close()
            except OSError:
                pass
        self._conns.clear()
        # PTY：先关 master 再关 slave（读线程 select/read master 立即出错退出）
        if self._master_fd is not None:
            for fd in (self._master_fd, self._slave_fd):
                if fd is not None:
                    try:
                        os.close(fd)
                    except OSError:
                        pass
            self._master_fd = None
            self._slave_fd = None
            self.slave_path = None
        for t in self._threads:
            t.join(timeout=1.0)
        self._threads.clear()
        self._running = False
        self._stopping.clear()   # 状态复位，可重新 start

    # ---- 主循环驱动 ----

    def drain(self) -> None:
        """每帧调用：非阻塞 accept（TCP+WS）+ 取空请求队列分派应答"""
        if not self._running:
            return
        # 1) 非阻塞 accept 新连接（listen socket 已 settimeout(0)）
        self._accept_loop(self._listen_sock, self._tcp_reader,
                          "msp-tcp-reader")
        self._accept_loop(self._ws_listen_sock, self._ws_reader,
                          "msp-ws-reader")
        # 2) 取空请求队列 → 分派 handlers → 应答写回该连接
        while True:
            try:
                transport, req = self._queue.get_nowait()
            except queue.Empty:
                break
            self._dispatch(transport, req)

    def _accept_loop(self, listen_sock: socket.socket | None,
                     reader: Callable[[socket.socket], None],
                     name: str) -> None:
        """非阻塞 accept 尽取新连接，为每连接派一个读线程"""
        if listen_sock is None:
            return
        while True:
            try:
                sock, _addr = listen_sock.accept()
            except (BlockingIOError, socket.timeout):
                break  # 无新连接
            except OSError:
                break  # listen 已关（stop 竞态）：本轮跳过
            self._conns.append(sock)
            t = threading.Thread(target=reader, args=(sock,),
                                 daemon=True, name=name)
            t.start()
            self._threads.append(t)

    # ---- 内部：分派与读线程 ----

    def _dispatch(self, transport: _Transport, req: MspRequest) -> None:
        """单条请求分派：handler 缺席/抛错 → 空载荷应答（不断连不崩主循环）"""
        handler = self._handlers.get(req.cmd)
        if handler is None:
            print(f"[msp] 未实现命令 {req.cmd}，空载荷应答", file=sys.stderr)
            payload = b""
        else:
            try:
                payload = handler(req.payload)
            except Exception as e:  # handler 故障不得中断主循环
                print(f"[msp] 命令 {req.cmd} handler 异常：{e}", file=sys.stderr)
                payload = b""
        try:
            transport.send(build_v1_frame(">", req.cmd, payload))
        except OSError as e:
            # 对端已断：清死连接（读线程随后 recv 出错自行退出）
            print(f"[msp] 应答发送失败，清理连接：{e}", file=sys.stderr)
            sock = transport.sock
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass
                if sock in self._conns:
                    self._conns.remove(sock)

    def _tcp_reader(self, sock: socket.socket) -> None:
        """TCP 读线程：超时循环 recv → parser → 入队（只读不写状态）"""
        sock.settimeout(_RECV_TIMEOUT)
        parser = CrcMspStreamParser()
        last_dropped = 0
        while not self._stopping.is_set():
            try:
                chunk = sock.recv(_CHUNK)
            except socket.timeout:
                continue  # 周期检查停止标志
            except OSError:
                break  # 连接关闭/出错
            if not chunk:
                break  # 对端关闭
            transport = _Transport(sock=sock)
            for req in parser.feed(chunk):
                self._queue.put((transport, req))
            if parser.dropped_frames > last_dropped:
                # GIL 下 int 自增原子；读线程唯一的共享写（计数聚合）
                self._dropped += parser.dropped_frames - last_dropped
                last_dropped = parser.dropped_frames
        try:
            sock.close()  # 自清理；注册表残留由 stop/drain 兜底（close 幂等）
        except OSError:
            pass

    def _pty_reader(self, master_fd: int) -> None:
        """PTY 读线程：select 超时轮询 master → parser → 入队"""
        parser = CrcMspStreamParser()
        last_dropped = 0
        while not self._stopping.is_set():
            r, _, _ = select.select([master_fd], [], [], _RECV_TIMEOUT)
            if not r:
                continue  # 周期检查停止标志
            try:
                chunk = os.read(master_fd, _CHUNK)
            except OSError:
                break  # master 已关（stop）
            if not chunk:
                break
            transport = _Transport(fd=master_fd)
            for req in parser.feed(chunk):
                self._queue.put((transport, req))
            if parser.dropped_frames > last_dropped:
                self._dropped += parser.dropped_frames - last_dropped
                last_dropped = parser.dropped_frames

    # ---- WebSocket 读线程（浏览器版 Configurator） ----

    def _ws_reader(self, sock: socket.socket) -> None:
        """WS 读线程：线程内握手升级 → 帧循环（二进制 payload 喂 MSP
        解析器入队；ping→pong；close→回 close 清连接；协议违规断连）"""
        sock.settimeout(_RECV_TIMEOUT)
        leftover = self._ws_handshake(sock)
        if leftover is None:
            self._ws_cleanup(sock)
            return
        transport = _Transport(sock=sock, ws=True)
        parser = CrcMspStreamParser()
        last_dropped = 0
        buf = bytearray(leftover)  # 客户端握手后立发的首帧字节
        closing = False
        while not self._stopping.is_set() and not closing:
            # 先消化缓冲中已收全的帧，再补收新字节
            error: str | None = None
            while buf:
                try:
                    frame = parse_client_frame(buf)
                except ValueError as e:
                    error = f"WS 帧协议违规：{e}"
                    break
                if frame is None:
                    break  # 帧未收全，等下一 chunk
                opcode, payload, consumed = frame
                del buf[:consumed]
                if opcode in (OP_BINARY, OP_CONT):
                    # 二进制透传：MSP 流自身可跨帧分片，无需等 FIN 重组
                    for req in parser.feed(payload):
                        self._queue.put((transport, req))
                    if parser.dropped_frames > last_dropped:
                        self._dropped += (parser.dropped_frames
                                          - last_dropped)
                        last_dropped = parser.dropped_frames
                elif opcode == OP_PING:
                    self._ws_send(transport, build_pong(payload))
                elif opcode == OP_CLOSE:
                    self._ws_send(transport, build_close())  # 礼貌回 close
                    closing = True
                # OP_TEXT/OP_PONG：Configurator 二进制透传不用，忽略
            if error is not None:
                print(f"[msp] {error}，断开 WS 连接", file=sys.stderr)
                break
            if closing:
                break
            try:
                chunk = sock.recv(_CHUNK)
            except socket.timeout:
                continue  # 周期检查停止标志
            except OSError:
                break  # 连接关闭/出错
            if not chunk:
                break  # 对端关闭
            buf += chunk
        self._ws_cleanup(sock)

    def _ws_handshake(self, sock: socket.socket) -> bytes | None:
        """读线程内完成 RFC6455 握手；返回握手请求后的剩余字节（客户端
        可能在同一 TCP 段内紧随握手发出首帧），失败返回 None"""
        data = bytearray()
        while not self._stopping.is_set():
            if b"\r\n\r\n" in data:
                break
            if len(data) > _WS_HANDSHAKE_MAX:
                print("[msp] WS 握手请求超限，拒绝", file=sys.stderr)
                return None
            try:
                chunk = sock.recv(_CHUNK)
            except socket.timeout:
                continue  # 周期检查停止标志
            except OSError:
                return None
            if not chunk:
                return None  # 对端在握手前关闭
            data += chunk
        if self._stopping.is_set():
            return None
        head, sep, rest = bytes(data).partition(b"\r\n\r\n")
        if not sep:
            return None  # 停止时跳出循环无终止符
        resp = websocket_handshake(head)
        if resp is None:
            print("[msp] 非法 WS 握手请求，拒绝连接", file=sys.stderr)
            try:
                sock.sendall(b"HTTP/1.1 400 Bad Request\r\n"
                             b"Connection: close\r\n\r\n")
            except OSError:
                pass
            return None
        try:
            sock.sendall(resp)
        except OSError:
            return None  # 应答都发不出去：对端已断
        return rest

    def _ws_send(self, transport: _Transport, frame: bytes) -> None:
        """读线程发送 WS 控制帧（已构帧，勿再包 binary 帧）"""
        try:
            with transport._send_lock:
                transport.sock.sendall(frame)
        except OSError:
            pass  # 对端已断：帧循环随后 recv 出错退出

    def _ws_cleanup(self, sock: socket.socket) -> None:
        """WS 连接清理：关 socket + 出注册表（close/移除均幂等）"""
        try:
            sock.close()
        except OSError:
            pass
        if sock in self._conns:
            self._conns.remove(sock)
