# msp/server.py
"""虚拟飞控 MSP 服务器（TCP + PTY 双传输，纯标准库）。

线程模型（设计 §10，状态单线程访问）：
- 每连接一个读线程：TCP ``socket.settimeout(0.05)`` 循环 recv / PTY
  ``select`` 超时轮询 ``os.read(master_fd)`` → ``CrcMspStreamParser.feed``
  → ``(发送器, 请求)`` 入队；读线程只读不写（除 dropped 计数聚合，
  int 自增在 GIL 下原子）。
- ``drain()`` 由主循环每帧调用（未启动直返）：非阻塞 accept 新连接 →
  取空请求队列 → 分派 ``handlers[cmd]`` → 应答 sendall/写 master。
  应答在主线程生成，handlers 状态访问无锁。
- ``stop()`` 幂等：置停止标志 → 关 listen/连接/PTY fd（读线程 recv/read
  立即出错退出）→ join 收线程 → 状态复位（可重新 start）。
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

_RECV_TIMEOUT = 0.05   # 秒：读线程收包超时（周期检查停止标志）
_CHUNK = 4096          # 单次 recv/read 上限
_TCP_HOST = "127.0.0.1"
_TCP_BACKLOG = 4


class _Transport:
    """统一 TCP socket 与 PTY master fd 的发送接口（drain 应答回写用）"""

    def __init__(self, sock: socket.socket | None = None,
                 fd: int | None = None):
        self.sock = sock
        self.fd = fd

    def send(self, data: bytes) -> None:
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
        self._conns: list[socket.socket] = []   # 已 accept 的 TCP 连接
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
    def connected(self) -> bool:
        """任一传输已启动（服务器运行中）"""
        return self._running

    @property
    def dropped_frames(self) -> int:
        """全部连接累计丢弃的坏帧数（CRC 失败/超限，见 protocol.py）"""
        return self._dropped

    # ---- 启停 ----

    def start(self, port: int = 5761) -> None:
        """双传输开启：TCP 失败（端口占用）抛 OSError；PTY 失败降级仅 TCP"""
        self.start_tcp(port)
        self.start_pty()

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
        """每帧调用：非阻塞 accept + 取空请求队列分派应答（未启动直返）"""
        if not self._running:
            return
        # 1) 非阻塞 accept 新 TCP 连接（listen socket 已 settimeout(0)）
        if self._listen_sock is not None:
            while True:
                try:
                    sock, _addr = self._listen_sock.accept()
                except (BlockingIOError, socket.timeout):
                    break  # 无新连接
                except OSError:
                    break  # listen 已关（stop 竞态）：本轮跳过
                self._conns.append(sock)
                t = threading.Thread(target=self._tcp_reader, args=(sock,),
                                     daemon=True, name="msp-tcp-reader")
                t.start()
                self._threads.append(t)
        # 2) 取空请求队列 → 分派 handlers → 应答写回该连接
        while True:
            try:
                transport, req = self._queue.get_nowait()
            except queue.Empty:
                break
            self._dispatch(transport, req)

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
