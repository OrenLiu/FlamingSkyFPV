"""配置持久化：dataclass ↔ JSON，损坏自动备份回默认（设计文档第 7 节）"""
import dataclasses
import json
import os
import shutil
from dataclasses import dataclass, field

@dataclass
class Settings:
    """应用设置（设计文档：分辨率、全屏）"""
    resolution: tuple = (1280, 720)   # (宽, 高)
    fullscreen: bool = False

@dataclass
class PIDGains:
    """单轴 PID 增益（仿真标定值）"""
    p: float = 0.0
    i: float = 0.0
    d: float = 0.0

# PID 参数 schema 版本：2 = 现行物理标定量级（roll/pitch 临界 P≈9e-4）；
# 缺失/更早版本的存档为旧量级（P≈45），加载时整体重置（见 ConfigStore.load）
PID_SCHEMA = 2

@dataclass
class DroneParams:
    """无人机全参数——参数页编辑对象，飞控仿真消费对象"""
    weight_g: float = 550.0           # 重量（克）
    motor_kv: int = 1750              # 电机 KV
    thrust_ratio: float = 7.0         # 推重比（默认 kv/250，可覆盖）
    camera_angle_deg: float = 20.0    # 摄像头仰角
    rate_type: str = "betaflight_actual"  # "betaflight_actual" | "linear"
    rc_rate: float = 1.0
    super_rate: float = 0.70
    expo: float = 0.32
    linear_rate_max_deg: float = 360.0  # linear 模式满杆角速度 °/s
    # PID 量纲：误差 °/s → 电机油门单位。开环增益实测 roll/pitch ≈ 6.8e4、yaw ≈ 6.7e3
    # °/s²/单位输出，帧长 1/30 时临界 P：roll/pitch ≈ 9e-4、yaw ≈ 9e-3（test/manual/
    # calibrate.py 无头闭环标定，双 dt 稳定、阶跃无过冲、悬停零饱和）
    pid: dict = field(default_factory=lambda: {
        "roll": PIDGains(0.0004, 0.0004, 0.000004),
        "pitch": PIDGains(0.0004, 0.0004, 0.000004),
        "yaw": PIDGains(0.004, 0.001, 0.0),
    })
    pid_schema: int = PID_SCHEMA       # PID 参数版本：不匹配的存档加载时重置 pid

@dataclass
class AxisBinding:
    """通道-轴绑定"""
    axis: int = -1                    # -1 表示未绑定
    inverted: bool = False

@dataclass
class RCConfig:
    """遥控配置：键盘或手柄 + 四通道绑定"""
    mode: str = "keyboard"            # "keyboard" | "joystick"
    device_name: str = ""
    mappings: dict = field(default_factory=lambda: {
        k: AxisBinding() for k in ("throttle", "yaw", "roll", "pitch")})

def default_drone_params() -> DroneParams:
    """出厂默认（参数页"恢复默认"用）"""
    return DroneParams()

def default_rc_config() -> RCConfig:
    return RCConfig()

# dataclass 字段名 → 嵌套 dataclass 类型表（重建用）
_NESTED = {"pid": PIDGains, "mappings": AxisBinding}

def _rebuild(cls, data: dict):
    """dict → dataclass 实例，递归重建嵌套 dict[str, dataclass] 字段（缺失键用默认值补齐）"""
    kwargs = {}
    for f in dataclasses.fields(cls):
        if f.name not in data:
            continue
        v = data[f.name]
        if f.name in _NESTED and isinstance(v, dict):
            # 以默认实例的同名字典为基底，JSON 数据覆盖——仅保存了部分键（如只绑定了
            # throttle）时，其余通道回落默认 AxisBinding()，避免 round-trip 丢键
            base = {k: dataclasses.asdict(inst)
                    for k, inst in getattr(cls(), f.name).items()}
            base.update(v)
            v = {k: _rebuild(_NESTED[f.name], sub) for k, sub in base.items()}
        kwargs[f.name] = v
    return cls(**kwargs)

class ConfigStore:
    """JSON 文件存取；目录不存在自动创建；损坏文件备份 .bak 后回默认"""
    def __init__(self, config_dir: str = "config"):
        self.config_dir = config_dir

    def _path(self, name: str) -> str:
        return os.path.join(self.config_dir, f"{name}.json")

    def load(self, name: str, cls):
        os.makedirs(self.config_dir, exist_ok=True)
        path = self._path(name)
        if not os.path.exists(path):
            return cls()  # 无文件 → 默认值（首启动自动生成的效果由后续 save 达成）
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            obj = _rebuild(cls, data)
        except (json.JSONDecodeError, TypeError, KeyError):
            shutil.copy2(path, path + ".bak")  # 备份损坏文件
            return cls()
        # PID 版本迁移：旧 schema（无 pid_schema 键或值≠2）量级与现行物理不符——
        # 加载即 bang-bang 且参数页滑条超限。pid 整体重置为新默认，其余字段保留。
        # 注意须查原始 JSON：_rebuild 对缺失键会补 dataclass 默认值 2，查对象会漏掉旧档
        if cls is DroneParams and isinstance(data, dict) \
                and data.get("pid_schema") != PID_SCHEMA:
            obj.pid = default_drone_params().pid
            obj.pid_schema = PID_SCHEMA
        return obj

    def save(self, name: str, obj):
        os.makedirs(self.config_dir, exist_ok=True)
        with open(self._path(name), "w", encoding="utf-8") as f:
            json.dump(dataclasses.asdict(obj), f, ensure_ascii=False, indent=2)
