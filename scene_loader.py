"""场景枚举与校验：scene/<名>/config.json（name/model 必填，scale/spawn 可选）"""
import json
from dataclasses import dataclass, field
from pathlib import Path

@dataclass
class SceneInfo:
    """单个场景的元信息；valid=False 时 error 说明原因"""
    dir: Path = None
    name: str = ""
    model: str = ""
    cover: str = ""   # 封面图（可选，相对场景目录；仅展示用，缺失不判无效）
    scale: float = 1.0
    spawn: tuple = (0.0, 0.0, 2.0)
    valid: bool = True
    error: str = ""

def list_scenes(scene_root: str = "scene") -> list:
    """枚举场景目录；config.json 非法或模型缺失的场景标记 invalid 而非剔除"""
    root = Path(scene_root)
    if not root.is_dir():
        return []
    out = []
    for d in sorted(root.iterdir()):
        if not d.is_dir():
            continue
        info = SceneInfo(dir=d)
        cfg = d / "config.json"
        try:
            data = json.loads(cfg.read_text(encoding="utf-8"))
            info.name = str(data["name"])
            info.model = str(data["model"])
            if not (d / info.model).exists():
                raise FileNotFoundError(f"模型文件不存在: {info.model}")
            cover = data.get("cover")   # 可选装饰字段：类型不对仅影响显示
            info.cover = str(cover) if cover is not None else ""
            info.scale = float(data.get("scale", 1.0))
            spawn = data.get("spawn", [0, 0, 2])
            info.spawn = tuple(float(v) for v in spawn)
        except Exception as e:  # 非法场景：标记而非中断（设计文档第 8 节）
            info.valid = False
            info.error = str(e)
            info.name = info.name or d.name
        out.append(info)
    return out
