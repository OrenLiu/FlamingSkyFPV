# msp/cli.py
"""CLI 文本模式最小支持（Task 34，纯标准库）。

Configurator 的 CLI tab 是绕过 MSP 的纯文本流（2026.6.2 tag 与 master
2026-11-13 双取互证，``src/composables/useCli.js|ts``）：

- **进入**：tab 打开后 250ms 定时器发送单字节 ``#``(0x23)，无换行；
  判定「已进入」靠读侧滑窗——先见 ``CLI`` 标记、终态窗口恰为
  ``\r\n# ``（``CLI_ENTRY_PROMPT``）→ 横幅必须以 ``\r\n# `` 收尾
- **命令**：命令文本 + ``\n`` 逐行发送；离开 tab 时 cleanup 发
  ``exit\r``（CR 结尾）并整链重连——服务端 exit 后恢复 MSP 模式，
  重连的新会话即正常 MSP 连接序列
- **回显是硬需求**：自动补全构建器（CliAutoComplete.js builderStart）
  在进入成功后发 sentinel ``# <random>`` 并靠**回显**匹配推进，
  命令行必须原样回显、注释行（``#`` 开头）不得报错
- **错误行前缀** ``###ERROR``（useMspCliSession ``ERROR_PREFIX``，
  useCli 将该前缀行渲染为红色 error_message）
- MSP 版 CLI（STX/ETX 帧，msp.ts ``send_cli_command``）按固件版本
  ≥4.5.4 门控（``MIN_FC_VERSION_FOR_MSP_CLI``），虚拟飞控自报 4.5.1
  → 永不触发，不在本任务范围（诚实边界）

进入检测与固件同构（msp.c ``evaluateOtherData``）：只有落在 MSP 帧
之外的「垃圾字节」（无 ``$`` 前导/未知前导/resync 丢弃段）才参与 ``#``
扫描——帧内 0x23 是合法载荷字节，原理上不可区分，绝不冒充进入序列；
代价是「MSP 半帧滞留后紧接 ``#``」的交错（干净会话不出现，固件同样
吞掉）不触发进入，属既定边界。

会话形态对齐固件流：每条命令应答 = 回显行 + 输出行 + 提示符
``\r\n# ``；空行只回提示符；exit 应答 ``Leaving CLI mode, rebooting.``
后该连接恢复 MSP 模式（虚拟飞控无重启语义，重连即新会话）。

版本行取自 handlers 身份常量（与 MSP_BUILD_INFO 单一来源），格式对齐
固件 ``version`` 输出：``Betaflight 4.5.1 (FSKY) Nov 12 2026 / 00:00:00 (26)``。

依赖方向约束：msp/ 禁 import 渲染/UI/pygame；本模块仅标准库 +
msp.handlers（身份常量）。
"""
import logging

from msp.handlers import BOARD_NAME, FC_BUILD_INFO, FC_VERSION
from msp.protocol import CrcMspStreamParser, MspRequest

# 模块级 logger（Task 24 惯例）：进入/退出 CLI → info、逐命令 → debug
log = logging.getLogger(__name__)

# 固件同款横幅（cli.c cliEnter 文本；尾部 \r\n# 为 Configurator 判定条件）
_BANNER = b"\r\nEntering CLI Mode, type exit to return, or help\r\n# "

# 每条命令后的提示符（Configurator 提示符跟踪依赖该尾巴）
_PROMPT = b"\r\n# "

# exit 应答（固件 cliExit 文本；前导 \r\n 先把残留提示符冲刷成独立行，
# 防止与上一条命令的 "# " 提示符合并；不发提示符——会话已结束）
_EXIT_RESPONSE = b"\r\nLeaving CLI mode, rebooting.\r\n"

# 错误行前缀（Configurator ERROR_PREFIX，渲染为红色）
_ERROR_PREFIX = b"###ERROR: "

# CLI 进入序列：单字节 '#'（useCli.js enter_cli 定时器实发形态）
_ENTER_BYTE = 0x23

# 行缓冲上限（固件 CLI_LINE_BUFFER 128 字节量级；纯防御，超出截断）
_LINE_CAP = 256

# 错误行里回显命令的最大长度（超长截断，防错误行比输出还长）
_CMD_DISPLAY_CAP = 64

# 月份缩写（构建日期 YYYY-MM-DD → 固件 version 行的 "Nov 12 2026" 形态）
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _version_line() -> bytes:
    """固件 ``version`` 输出行，自 MSP_BUILD_INFO 常量合成（单一来源）。

    FC_BUILD_INFO 形如 ``FSKY sim build 2026-11-12 00:00:00 26``——
    尾三段为日期/时间/修订尾号；日期转为固件的 ``Nov 12 2026`` 形态。
    """
    parts = FC_BUILD_INFO.decode("ascii").split()
    y, m, d = parts[-3].split("-")
    date = f"{_MONTHS[int(m) - 1]} {int(d)} {y}".encode("ascii")
    return (b"Betaflight " + FC_VERSION + b" (" + BOARD_NAME + b") "
            + date + b" / " + parts[-2].encode("ascii")
            + b" (" + parts[-1].encode("ascii") + b")")


# help 输出（自动补全构建器 parse-help 按行首词收割——仅列真实命令，
# 说明行用 '#' 注释形态避免被收割成假命令）
_HELP_LINES = (
    b"# Virtual flight controller - supported commands:",
    b"version",
    b"help",
    b"exit",
)


class CliSession:
    """单连接 CLI 会话：按行收集命令字节 → 文本应答。

    行终止符 LF/CR 皆收（命令实发 ``\\n``、tab cleanup 实发 ``exit\\r``），
    CR 后紧跟的 LF 吞并为一次终止；0x7F/0x08 作退格（Configurator 粘贴
    补偿会实发 DEL 序列）；超上限行截断（不崩不断连）。
    """

    def __init__(self, send, on_exit) -> None:
        self._send = send
        self._on_exit = on_exit
        self._line = bytearray()
        self._last_terminator_cr = False

    def feed(self, chunk: bytes) -> None:
        """送入一段 CLI 模式字节流（命令文本片段），即时应答。"""
        for byte in chunk:
            if byte == 0x0A or byte == 0x0D:  # LF / CR：行终止
                if (byte == 0x0A and self._last_terminator_cr
                        and not self._line):
                    continue  # CRLF 的 LF 半个：已随 CR 终止过，吞并
                self._last_terminator_cr = byte == 0x0D
                self._execute()
            elif byte == 0x7F or byte == 0x08:  # DEL / BS：退格修正
                if self._line:
                    del self._line[-1]
            elif len(self._line) < _LINE_CAP:
                self._line.append(byte)

    # ---- 内部：行执行 ----

    def _execute(self) -> None:
        line = bytes(self._line)
        self._line.clear()
        cmd = line.decode("utf-8", "replace").strip()
        if not cmd:
            self._send(_PROMPT)  # 空行只回提示符（固件同款）
            return
        if line.lstrip().startswith(b"#"):
            # 注释行（自动补全 sentinel 实发形态）：仅回显不报错
            self._send(line + b"\r\n" + _PROMPT)
            return
        if cmd == "exit":
            log.debug("CLI command: exit")
            self._send(_EXIT_RESPONSE)
            self._on_exit()
            return
        out = bytearray(line + b"\r\n")  # 回显（sentinel 匹配依据）
        if cmd == "version":
            log.debug("CLI command: version")
            out += _version_line() + b"\r\n"
        elif cmd == "help":
            log.debug("CLI command: help")
            for help_line in _HELP_LINES:
                out += help_line + b"\r\n"
        else:
            # 诚实边界：完整 CLI 配置命令集超出虚拟飞控语义
            display = cmd.encode("utf-8", "replace")[:_CMD_DISPLAY_CAP]
            out += _ERROR_PREFIX + b"'" + display \
                + b"' is not supported by the virtual flight controller"
            out += b"\r\n"
        out += _PROMPT
        self._send(bytes(out))


class MspCliDemux:
    """连接级 MSP/CLI 字节流分配器（每连接一个，server 读线程持有）。

    MSP 模式：字节全部喂 ``CrcMspStreamParser``，帧外丢弃段经
    ``on_garbage`` 回调扫描 ``#`` 进入序列（帧内 0x23 为合法载荷，
    不参与扫描——防误切）；命中后本轮 feed 结束时切模式（同段内 ``#``
    之后的完整帧仍按 MSP 解析，此后字节归 CLI）。

    CLI 模式：字节全部交 ``CliSession``（文本应答经 send 即时回写，
    纯静态内容无共享状态，读线程直发安全）；exit → 会话回调恢复 MSP
    模式。进入/退出都 ``parser.reset()`` 清残帧（dropped 计数保留）。
    """

    def __init__(self, send) -> None:
        self._send = send
        self._parser = CrcMspStreamParser(on_garbage=self._scan_enter)
        self._cli: CliSession | None = None
        self._pending_enter: bytes | None = None

    @property
    def dropped_frames(self) -> int:
        """坏帧计数（透传内部解析器；跨 CLI 往返持续累计）"""
        return self._parser.dropped_frames

    def feed(self, chunk: bytes) -> list[MspRequest]:
        """送入一段字节流：MSP 模式返回解析出的请求，CLI 模式恒空表。"""
        if self._cli is not None:
            self._cli.feed(chunk)
            return []
        requests = self._parser.feed(chunk)
        if self._pending_enter is not None:
            leftover = self._pending_enter
            self._pending_enter = None
            log.info("CLI entry sequence matched, connection switched to text mode")
            self._parser.reset()  # 丢弃滞留半帧，防污染 exit 后的 MSP 会话
            self._send(_BANNER)
            self._cli = CliSession(self._send, self._exit_cli)
            if leftover:
                self._cli.feed(leftover)  # '#' 同段尾随字节按 CLI 输入处理
        return requests

    # ---- 内部 ----

    def _scan_enter(self, data: bytes) -> None:
        """帧外垃圾段扫描进入序列：首个 '#' 命中即挂起进入（首个优先）。"""
        if self._pending_enter is not None:
            return
        idx = data.find(bytes([_ENTER_BYTE]))
        if idx >= 0:
            self._pending_enter = data[idx + 1:]

    def _exit_cli(self) -> None:
        """CliSession exit 回调：恢复 MSP 模式（连接保持，供重连序列）"""
        log.info("CLI exit, connection restored to MSP mode")
        self._cli = None
        self._parser.reset()
