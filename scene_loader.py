"""场景枚举与校验：scene/<名>/config.json（name/model 必填，scale/spawn 可选）"""
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

@dataclass
class SceneInfo:
    """单个场景的元信息；valid=False 时 error 说明原因"""
    dir: Path = None
    name: str = ""
    model: str = ""
    cover: str = ""   # 封面图（可选，相对场景目录；仅展示用，缺失不判无效）
    scale: float = 1.0
    spawn: tuple = (0.0, 0.0, 2.0)
    sky: dict = field(default_factory=dict)   # 天空配置（解析后恒含四键，见 list_scenes）
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
            log.debug("reading scene config: %s", cfg)
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
            # 天空配置：太阳参数同时驱动方向光与画面优先包的天空盒；panorama
            # 为场景目录内等距圆柱全景图（缺省程序生成默认天空）
            sky = data.get("sky", {})
            # 太阳方向优先级：sky.sun_yaw/sun_pitch 显式指定 > 顶层
            # sun_toward（[yaw, pitch, roll] 度，与引擎光源 toward 参数
            # 同语义，roll 对平行光方向无效，取前两分量）> 默认 30/-60
            if "sun_yaw" in sky or "sun_pitch" in sky:
                sun_yaw = float(sky.get("sun_yaw", 30))
                sun_pitch = float(sky.get("sun_pitch", -60))
            else:
                toward = data.get("sun_toward")
                if toward is not None:
                    toward = [float(v) for v in toward]
                    if len(toward) < 2:
                        raise ValueError(
                            f"sun_toward 至少需要 [yaw, pitch]: {toward}")
                    sun_yaw, sun_pitch = toward[0], toward[1]
                else:
                    sun_yaw, sun_pitch = 30, -60
            info.sky = {
                "sun_yaw": sun_yaw,
                "sun_pitch": sun_pitch,
                # 颜色/环境光：sky 段显式优先，顶层 sun_color/ambient 作
                # 回退（用户按引擎光源参数直觉写的顶层字段），ambient
                # 缺省与 flight.py 原硬编码一致
                "sun_color": [float(v) for v in
                              sky.get("sun_color",
                                      data.get("sun_color",
                                               [0.8, 0.8, 0.75]))],
                "ambient": [float(v) for v in
                            data.get("ambient", [0.45, 0.45, 0.45])],
                "panorama": (str(d / sky["panorama"])
                             if sky.get("panorama") else ""),
            }
        except Exception as e:  # 非法场景：标记而非中断（设计文档第 8 节）
            info.valid = False
            info.error = str(e)
            info.name = info.name or d.name
            log.warning("invalid scene '%s': %s", d.name, e)
        out.append(info)
    log.info("scene scan: %d found (%d valid) in %s",
             len(out), sum(1 for i in out if i.valid), root)
    return out
