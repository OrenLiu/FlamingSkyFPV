"""独立运行自举:数据根定位 + 辅助文件生成(bootstrap)。

目标:程序在「源码运行」与「打包独立运行」两种形态下,首启动即可自给自足——
chdir 到数据根使既有全部 cwd 相对路径(config/、scene/、shader_packs/ 等)
零改动生效,并生成缺失的辅助文件:
- config/、config/drones/、scene/、shader_packs/ 目录骨架
- settings.json / rc.json 默认档(仅缺失时写,用户档永不覆盖)
- 出厂无人机预设(init_drone / virtual_FC,来自 factory_defaults,仅当
  drones/ 一台都没有时播种)
- 种子场景(打包版把构建机 scene/* 装进包内 seed/scenes/,首启动缺失拷入;
  资源根=数据根的源码运行不播种——scene/ 本身就是源)

数据根解析优先级:FLAME_DATA_DIR 环境变量 > 打包版可执行文件目录(不可写
或 macOS 签名包回退用户数据目录) > 源码运行仓库根。
"""
import json
import logging
import os
import shutil
import sys

from config_store import CONFIG_DIR, DRONES_DIR, ConfigStore, RCConfig, Settings

log = logging.getLogger(__name__)
from factory_defaults import factory_drones

_APP_DIRNAME = "FlamingSkyFPV"

# ---- 路径解析 ----

def _user_data_root() -> str:
    """平台标准用户数据目录(macOS: ~/Library/Application Support;
    Windows: %APPDATA%;Linux: $XDG_DATA_HOME 或 ~/.local/share)"""
    if sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support")
    elif os.name == "nt":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    else:
        base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return os.path.join(base, _APP_DIRNAME)

def _ensure_writable_dir(path: str) -> bool:
    """目录存在且可写(探针文件实测,防只读目录误判)"""
    probe = os.path.join(path, ".write_probe")
    try:
        os.makedirs(path, exist_ok=True)
        with open(probe, "w") as f:
            f.write("x")
        os.remove(probe)
        return True
    except OSError:
        return False

def resolve_data_root() -> str:
    """数据根(全部用户数据 config/scene/shader_packs 的父目录)。

    - FLAME_DATA_DIR 环境变量:强制覆盖(测试/便携盘场景;不存在则创建)
    - 打包版(PyInstaller frozen):可执行文件目录(便携式,双击即用)。
      目录不可写 → 用户数据目录;macOS .app 恒走用户目录(包内写入会
      破坏签名且 /Applications 通常不可写)
    - 源码运行:仓库根(bootstrap.py 所在目录,与历史 cwd=仓库根行为一致)
    """
    env = os.environ.get("FLAME_DATA_DIR")
    if env:
        return os.path.abspath(env)
    if getattr(sys, "frozen", False):
        if sys.platform == "darwin":
            return _user_data_root()
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        if _ensure_writable_dir(exe_dir):
            return exe_dir
        return _user_data_root()
    return os.path.dirname(os.path.abspath(__file__))

def resolve_resource_root() -> str:
    """只读资源根(shaderpacks 内置包、assets、seed/scenes 所在):
    打包版 = sys._MEIPASS(PyInstaller 解包目录);源码 = 仓库根"""
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS",
                       os.path.dirname(os.path.abspath(sys.executable)))
    return os.path.dirname(os.path.abspath(__file__))

def resource_path(rel: str) -> str:
    """只读资源(如 assets/osd_betaflight.mcm)的绝对路径;两形态下等价于
    「仓库根/rel」,与 cwd 无关(打包版 assets 在 _MEIPASS 内)"""
    return os.path.join(resolve_resource_root(), rel)

# ---- 自举 ----

def ensure_user_files(data_root=None, resource_root=None) -> str:
    """定位数据根 → chdir → 生成缺失辅助文件。幂等:已有内容一概不动。
    显式传参供测试注入;正常入口无参调用。返回最终数据根。"""
    data_root = os.path.abspath(data_root or resolve_data_root())
    resource_root = os.path.abspath(resource_root or resolve_resource_root())
    log.info("data root: %s (resource root: %s)", data_root, resource_root)
    if not _ensure_writable_dir(data_root):
        raise RuntimeError(f"数据目录不可写且无法创建: {data_root}")
    os.chdir(data_root)

    # 目录骨架(幂等)
    for d in (CONFIG_DIR, DRONES_DIR, "scene", "shader_packs"):
        os.makedirs(d, exist_ok=True)

    # 设置/遥控通道默认档:仅文件缺失时写(ConfigStore.save 原子写);
    # 用户的 settings/rc 永不被自举覆盖
    store = ConfigStore(CONFIG_DIR)
    if not os.path.exists(os.path.join(CONFIG_DIR, "settings.json")):
        log.info("first launch: writing default settings.json")
        store.save("settings", Settings())
    if not os.path.exists(os.path.join(CONFIG_DIR, "rc.json")):
        log.info("first launch: writing default rc.json")
        store.save("rc", RCConfig())

    # 出厂无人机:仅当预设目录一台都没有时播种(部分存在 = 用户已管理,
    # 缺台不补——删机是有意行为)
    if not [n for n in os.listdir(DRONES_DIR) if n.endswith(".json")]:
        log.info("drones dir empty: seeding factory presets: %s",
                 ", ".join(factory_drones().keys()))
        for preset_id, payload in factory_drones().items():
            path = os.path.join(DRONES_DIR, f"{preset_id}.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)

    # 种子场景:仅资源根≠数据根时(打包版 / FLAME_DATA_DIR 源码运行),
    # 把包内 seed/scenes/*(打包版)或仓库 scene/*(源码回退源)拷入
    # scene/ 缺失项。此后 scene/ 归用户管理,自举不再回写。
    if os.path.normcase(resource_root) != os.path.normcase(data_root):
        seed_candidates = [
            os.path.join(resource_root, "seed", "scenes"),
            os.path.join(resource_root, "scene"),
        ]
        for seed_root in seed_candidates:
            if not os.path.isdir(seed_root):
                continue
            for name in sorted(os.listdir(seed_root)):
                src = os.path.join(seed_root, name)
                dst = os.path.join("scene", name)
                if os.path.isdir(src) and not os.path.exists(dst):
                    log.info("copying seed scene: %s", name)
                    shutil.copytree(src, dst)
    return data_root
