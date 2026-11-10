"""包发现与元信息校验（spec 5.1）：枚举内置包与用户文件夹包。

非法包标记 invalid 而非剔除（镜像 scene_loader 哲学），error 记原因；
文件夹无效包由设置页以文字行展示、不进下拉。校验只读 pack.json；
pack.py 的代码级校验在 loader.load_pack。
"""
import dataclasses
import json
from pathlib import Path

# 内置包根目录（shaderpacks/builtin/<目录名>/pack.json）
_BUILTIN_ROOT = Path(__file__).resolve().parent / "builtin"

# 内置包展示顺序（performance、quality 在前；新增内置包不在表内时按目录名排后）
_BUILTIN_ORDER = {"performance": 0, "quality": 1}


@dataclasses.dataclass
class PackInfo:
    """包元信息。invalid 包保留在列表中，error 记原因。"""
    id: str
    dir: Path
    name: str
    description: str = ""
    author: str = ""
    version: str = ""
    builtin: bool = False
    valid: bool = True
    error: str = ""


def list_packs(packs_root: str = "shader_packs") -> list:
    """枚举全部预设包：内置在前（performance、quality），文件夹包按目录名排序。

    :param packs_root: 用户包根目录（仓库根 shader_packs/，与 scene/ 平行）
    :return: list[PackInfo]，含 valid 与 invalid 条目
    """
    packs = []
    # 1. 内置包：shaderpacks/builtin/<目录名>/pack.json，id = "builtin_" + 目录名
    builtin_dirs = sorted((d for d in _BUILTIN_ROOT.iterdir() if d.is_dir()),
                          key=lambda d: (_BUILTIN_ORDER.get(d.name, 2), d.name))
    for d in builtin_dirs:
        packs.append(_make_pack_info(d, pack_id="builtin_" + d.name, builtin=True))
    # 2. 用户文件夹包：<packs_root>/<目录名>/，id = 目录名；根目录不存在则无此类
    root = Path(packs_root)
    if root.is_dir():
        folder_dirs = sorted((x for x in root.iterdir() if x.is_dir()),
                             key=lambda x: x.name)
        for d in folder_dirs:
            packs.append(_make_pack_info(d, pack_id=d.name, builtin=False))
    return packs


def _make_pack_info(d: Path, pack_id: str, builtin: bool) -> PackInfo:
    """读 pack.json 构造 PackInfo；非法包标记 invalid（error 记原因），不剔除。"""
    info = PackInfo(id=pack_id, dir=d, name=d.name, builtin=builtin)
    meta_path = d / "pack.json"
    if not meta_path.is_file():
        info.valid = False
        info.error = "缺少 pack.json"
        return info
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as e:
        info.valid = False
        info.error = f"pack.json 解析失败：{e}"
        return info
    if not isinstance(meta, dict):
        info.valid = False
        info.error = "pack.json 顶层必须是对象"
        return info
    name = meta.get("name")
    if not isinstance(name, str) or not name:
        info.valid = False
        info.error = "name 必须是非空字符串"
        return info
    # 文件夹包目录名以 builtin_ 开头 → invalid（内置保留前缀，防 id 冲突）。
    # 内置包不受此限制（id 本就含 builtin_ 前缀）
    if not builtin and pack_id.startswith("builtin_"):
        info.valid = False
        info.error = "目录名不可用 builtin_ 前缀（内置包保留前缀）"
        return info
    info.name = name
    info.description = meta.get("description") if isinstance(meta.get("description"), str) else ""
    info.author = meta.get("author") if isinstance(meta.get("author"), str) else ""
    info.version = meta.get("version") if isinstance(meta.get("version"), str) else ""
    return info
