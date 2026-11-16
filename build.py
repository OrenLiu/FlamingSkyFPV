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
    macOS:   dist/FlamingSkyFPV.app

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

    sep = os.pathsep  # add-data 分隔符:POSIX 为 ":" Windows 为 ";"
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--name", APP_NAME,
        "--paths", ".",
        "--console" if args.console else "--windowed",
        "main.py",  # 入口脚本(PyInstaller 必需位置参数)
    ]
    for src, dst in datas:
        cmd += ["--add-data", src + sep + dst]
    print("构建命令:", " ".join(cmd))
    subprocess.check_call(cmd)

    if sys.platform == "darwin":
        out = os.path.join("dist", APP_NAME + ".app")
    else:
        out = os.path.join("dist", APP_NAME)
    print("构建完成:", out)

    if args.zip:
        tag = {"Linux": "linux", "Darwin": "macos", "Windows": "windows"}.get(
            platform.system(), platform.system())
        base = os.path.join("dist", f"{APP_NAME}-{tag}")
        arc = APP_NAME + ".app" if sys.platform == "darwin" else APP_NAME
        shutil.make_archive(base, "zip", root_dir="dist", base_dir=arc)
        print("压缩包:", base + ".zip")

if __name__ == "__main__":
    main()
