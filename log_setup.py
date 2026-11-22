"""日志初始化：stderr 终端 + logs/<启动时间>.log 文件双写。

调用时机：main.py 在 bootstrap.ensure_user_files() 之后（数据根已 chdir，
logs/ 落数据根，与 config/、scene/ 同级）。级别由环境变量 FLAME_LOG 控制
（缺省 INFO；取值 DEBUG/INFO/WARNING/ERROR，非法值回落 INFO）。
每次启动新建一个日志文件（yyyy-MM-dd_HH-MM-SS.log），不轮转、不自动清理
历史文件。msp/ 既有 getLogger(__name__) logger 由此一并激活。
注意：日志消息一律用 ASCII 英文——日志会流经多种终端/编码环境（Windows
GBK 控制台、重定向管道等），中文消息在这些环境可能乱码。
"""
import logging
import os
import sys
from datetime import datetime

# 幂等标记：重复调用只调级别，不重复挂 handler（测试/多次启动防御）
_configured = False

_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


def setup_logging() -> str:
    """配置根 logger 双 handler。返回日志文件路径（logs/ 下）。"""
    global _configured
    level_name = os.environ.get("FLAME_LOG", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    root = logging.getLogger()
    root.setLevel(level)
    fmt = logging.Formatter(_FORMAT)
    if not _configured:
        # 终端：stderr（gui 程序 stdout 不可靠，且 pygame 不占用 stderr）
        sh = logging.StreamHandler(sys.stderr)
        sh.setFormatter(fmt)
        root.addHandler(sh)
        # 文件：数据根 logs/<启动时间>.log（cwd 已由 bootstrap chdir 到数据根）
        os.makedirs("logs", exist_ok=True)
        path = os.path.join(
            "logs", datetime.now().strftime("%Y-%m-%d_%H-%M-%S") + ".log")
        fh = logging.FileHandler(path, encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)
        _configured = True
        logging.getLogger(__name__).info("logging initialized, file: %s (level %s)",
                                         os.path.abspath(path), level_name)
        return path
    # 已配置过：仅按最新环境变量调级
    logging.getLogger(__name__).info("log level set: %s", level_name)
    return ""
