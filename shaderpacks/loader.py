"""pack.py 动态加载与接口契约校验（spec 5.2）。

信任模型（spec 4.5）：运行下载的 pack.py = 执行任意第三方代码（可访问文件系统
与网络），与 Minecraft 模组同等待遇——只安装来源可信的包；不做沙箱。
不缓存模块对象：同一 id 重复加载先从 sys.modules 弹出旧条目再加载（覆盖语义），
避免旧实例残留。
"""
import dataclasses
import importlib.util
import sys
import types

from .registry import PackInfo


class PackLoadError(Exception):
    """pack.py 加载或契约校验失败（调用方回落/提示，不中断游戏）"""


@dataclasses.dataclass
class LoadedPack:
    """load_pack 的产物：pack.py 契约校验后的加载结果（spec 4.3）。"""
    info: PackInfo
    surface_class: type
    skinned_surface_class: type
    create_frame_renderer: object = None
    raw_module: types.ModuleType = None


def load_pack(info: PackInfo) -> LoadedPack:
    """加载 info.dir/pack.py 并校验契约；失败抛 PackLoadError。

    契约（spec 4.3）：模块级 surface_class 必填且可调用；create_frame_renderer
    可选（存在则必须可调用）；skinned_surface_class 缺省 = surface_class。
    """
    pack_py = info.dir / "pack.py"
    # 模块名含包 id 防冲突；目录名无需是合法标识符（仅作 sys.modules 键字符串）
    module_name = "shaderpacks.pack_code." + info.id
    # 同 id 重复加载：先弹出旧条目（sys.modules 覆盖语义，避免旧实例残留）
    sys.modules.pop(module_name, None)
    try:
        spec = importlib.util.spec_from_file_location(module_name, pack_py)
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
    except Exception as e:
        # 半初始化模块不留残骸
        sys.modules.pop(module_name, None)
        raise PackLoadError(f"pack.py 加载失败（{info.id}）：{e}") from e
    surface_class = getattr(module, "surface_class", None)
    if not callable(surface_class):
        raise PackLoadError(f"pack.py 缺少可调用的 surface_class（{info.id}）")
    cfr = getattr(module, "create_frame_renderer", None)
    if cfr is not None and not callable(cfr):
        raise PackLoadError(f"create_frame_renderer 不是可调用对象（{info.id}）")
    skinned = getattr(module, "skinned_surface_class", surface_class)
    return LoadedPack(info=info,
                      surface_class=surface_class,
                      skinned_surface_class=skinned,
                      create_frame_renderer=cfr,
                      raw_module=module)
