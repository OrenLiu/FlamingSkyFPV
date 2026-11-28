#!/usr/bin/env python3
"""FlamingSkyFPV 跨平台打包脚本(linux / mac / windows)。

PyInstaller 无法交叉编译:须在目标系统上运行本脚本(linux、mac、windows
三平台各自执行同一命令即可)。在仓库根目录运行:

    python build.py                # 标准构建:onedir 无控制台 + 随包种子场景
    python build.py --console      # 控制台版(排障:stdout/异常可见)
    python build.py --no-scene     # 不随包携带本机 scene/*(首启动无场景)
    python build.py --zip          # 构建后把 dist 产物压缩为 zip

产物(PyInstaller ≥6 onedir 布局):
    Linux:   dist/FlamingSkyFPV/FlamingSkyFPV       + _internal/
    Windows: dist/FlamingSkyFPV/FlamingSkyFPV.exe   + _internal/
    macOS:   dist/FlamingSkyFPV.app(--console 版为 dist/FlamingSkyFPV/FlamingSkyFPV)

独立运行语义:首启动自动在数据根生成 config/(settings/rc + 出厂无人机
init_drone、virtual_FC)与 scene/(种子场景拷入),见 bootstrap.py。
"""
import argparse
import os
import platform
import shutil
import subprocess
import sys

REPO = os.path.dirname(os.path.abspath(__file__))
APP_NAME = "FlamingSkyFPV"

# 运行时按路径动态执行、PyInstaller 静态分析看不见其 import 的代码所在的包:整包
# 子模块强制进包。只放一方的纯 Python 包——标准库的启动子集(os/weakref 等)在
# base_library.zip 而不在 PYZ,放进来会被 verify_bundle 误判成缺失。
#   shaderpacks:内置预设 shaderpacks/builtin/*/pack.py 以数据文件随包分发,运行时由
#   loader.load_pack 经 importlib 按路径执行——它 import 的 shaderpacks.api 没有任何
#   静态导入方,不声明就不进包(打包版「画面优先」ModuleNotFoundError → 回落性能优先)
COLLECT_SUBMODULES = ("shaderpacks",)

def _ensure_pyinstaller() -> None:
    """PyInstaller 缺失时自动经 pip 安装(构建环境自举)"""
    try:
        import PyInstaller  # noqa: F401
        return
    except ImportError:
        pass
    print("未检测到 PyInstaller,自动安装...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller"])

def _collect_scene_datas() -> list:
    """本机 scene/* 各子目录 → 包内 seed/scenes/<名>(首启动拷入用户 scene/)"""
    datas = []
    scene_root = os.path.join(REPO, "scene")
    if os.path.isdir(scene_root):
        for name in sorted(os.listdir(scene_root)):
            src = os.path.join(scene_root, name)
            if os.path.isdir(src):
                datas.append((src, os.path.join("seed", "scenes", name)))
    return datas

def build_command(datas, console=False):
    """组装 PyInstaller 命令行(纯函数:不写盘、不起进程;datas=[(src, dst)])"""
    sep = os.pathsep  # add-data 分隔符:POSIX 为 ":" Windows 为 ";"
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--name", APP_NAME,
        "--paths", ".",
        "--console" if console else "--windowed",
        "main.py",  # 入口脚本(PyInstaller 必需位置参数)
    ]
    for pkg in COLLECT_SUBMODULES:
        cmd += ["--collect-submodules", pkg]
    for src, dst in datas:
        cmd += ["--add-data", src + sep + dst]
    return cmd


def artifact_paths(console, sys_platform=sys.platform, nt=os.name == "nt"):
    """(产物目录, 内嵌归档所在的可执行文件):onedir 三平台布局。macOS 仅窗口版产
    .app(PyInstaller makespec: is_darwin and not console),控制台版是普通目录"""
    if sys_platform == "darwin" and not console:
        app = os.path.join("dist", APP_NAME + ".app")
        return app, os.path.join(app, "Contents", "MacOS", APP_NAME)
    out = os.path.join("dist", APP_NAME)
    return out, os.path.join(out, APP_NAME + (".exe" if nt else ""))


def verify_bundle(exe_path):
    """构建后自检:COLLECT_SUBMODULES 各包的全部子模块必须进了产物内嵌 PYZ。

    只读解析产物归档,不运行程序。缺模块 → SystemExit(构建失败并点名,别等用户
    在打包版里撞上回落);归档读不出(布局/PyInstaller 版本差异)→ 仅告警不阻断
    ——自检失效不能反过来拖垮构建。
    """
    try:
        from PyInstaller.archive.readers import CArchiveReader
        from PyInstaller.utils.hooks import collect_submodules
        want = set()
        for pkg in COLLECT_SUBMODULES:
            want.update(collect_submodules(pkg))
        arc = CArchiveReader(exe_path)
        pyz_names = [name for name in arc.toc if name.endswith(".pyz")]
        if not pyz_names:
            raise LookupError("产物归档里没有 PYZ 条目")    # 落到下方告警分支
        have = set()
        for name in pyz_names:
            have.update(arc.open_embedded_archive(name).toc)
    except Exception as e:
        print(f"警告: 产物自检未执行({e!r});请手动验证打包版画质预设")
        return
    missing = sorted(want - have)
    if missing:
        raise SystemExit("打包产物缺少模块: " + ", ".join(missing)
                         + " (pack.py 动态导入的模块没进包,见 COLLECT_SUBMODULES)")
    print("产物自检通过: " + ", ".join(sorted(want)))


def main() -> None:
    ap = argparse.ArgumentParser(description="FlamingSkyFPV 打包脚本(PyInstaller onedir)")
    ap.add_argument("--console", action="store_true", help="控制台版(排障用)")
    ap.add_argument("--no-scene", action="store_true", help="不随包携带本机 scene/*")
    ap.add_argument("--zip", action="store_true", help="构建后压缩 dist 产物")
    args = ap.parse_args()

    os.chdir(REPO)
    _ensure_pyinstaller()

    # 只读资源:内置着色器包(__file__ 相对读取,须在包内同路径) / OSD 字模 /
    # 种子场景(构建机 scene/*,bootstrap 首启动拷入数据根)
    datas = [
        (os.path.join(REPO, "shaderpacks", "builtin"), "shaderpacks/builtin"),
        (os.path.join(REPO, "assets"), "assets"),
    ]
    if not args.no_scene:
        datas += _collect_scene_datas()

    cmd = build_command(datas, console=args.console)
    print("构建命令:", " ".join(cmd))
    subprocess.check_call(cmd)

    out, exe = artifact_paths(args.console)
    print("构建完成:", out)
    verify_bundle(exe)

    if args.zip:
        tag = {"Linux": "linux", "Darwin": "macos", "Windows": "windows"}.get(
            platform.system(), platform.system())
        base = os.path.join("dist", f"{APP_NAME}-{tag}")
        arc = APP_NAME + ".app" if sys.platform == "darwin" else APP_NAME
        shutil.make_archive(base, "zip", root_dir="dist", base_dir=arc)
        print("压缩包:", base + ".zip")

if __name__ == "__main__":
    main()
