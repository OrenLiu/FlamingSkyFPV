"""着色器预设包系统（设计：docs/specs/2026-11-10-shader-presets-design.md）。

包格式：文件夹 = pack.json（元信息）+ pack.py（代码）。用户包放仓库根 shader_packs/。
"""
from .registry import PackInfo, list_packs

__all__ = ["PackInfo", "list_packs"]
