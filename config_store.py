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

@dataclass
class RateParams:
    """单轴 rate 曲线参数（rate_type 全局共用，曲线参数三轴独立）"""
    rc_rate: float = 1.0
    super_rate: float = 0.70
    expo: float = 0.32
    linear_max_deg: float = 360.0      # linear 模式满杆角速度 °/s

# 参数 schema 版本：4 = PID 刻度对齐 Betaflight 量级（P≈40，见 physics.drone.
# PID_MIX_SCALING）。3 = 三轴 rate + 小刻度 PID（仅 pid 过时）；缺失/更早 = 旧量级
# PID 或全局单份 rate。加载时按版本重置（见 ConfigStore.load）
PARAMS_SCHEMA = 4

@dataclass
class DroneParams:
    """无人机全参数——参数页编辑对象，飞控仿真消费对象"""
    weight_g: float = 550.0           # 重量（克）
    motor_kv: int = 1750              # 电机 KV
    thrust_ratio: float = 7.0         # 推重比（默认 kv/250，可覆盖）
    camera_angle_deg: float = 20.0    # 摄像头仰角
    rate_type: str = "betaflight_actual"  # "betaflight_actual" | "linear"
    # rate 曲线三轴独立（用户裁定）：横滚/俯仰/偏航各自可调
    rate: dict = field(default_factory=lambda: {
        k: RateParams() for k in ("roll", "pitch", "yaw")})
    # PID 量纲：误差 °/s → 电机油门单位。开环增益实测 roll/pitch ≈ 6.8e4、yaw ≈ 6.7e3
    # °/s²/单位输出，帧长 1/30 时临界 P（电机域）：roll/pitch ≈ 9e-4、yaw ≈ 9e-3（test/
    # manual/calibrate.py 无头闭环标定，双 dt 稳定、阶跃无过冲、悬停零饱和）。
    # 存档/展示为 BF 量级刻度（PIDController 内 ÷PID_MIX_SCALING=1e5 换算）：
    # 由电机域标定值 4e-4/4e-4/4e-6（roll/pitch）与 4e-3/1e-3/0（yaw）×1e5 换算而来；
    # yaw P 偏大是桨反扭矩权限弱的诚实物理结果
    pid: dict = field(default_factory=lambda: {
        "roll": PIDGains(40.0, 40.0, 0.4),
        "pitch": PIDGains(40.0, 40.0, 0.4),
        "yaw": PIDGains(400.0, 100.0, 0.0),
    })
    # TPA（Throttle PID Attenuation，油门 PID 衰减）：推力∝油门² → 控制权限随油门
    # 线性上升（满油门 ≈ 2.9× 悬停值），悬停标定的 PID 在高油门越过稳定边界——高油门
    # 段按此参数衰减 PID 增益（Betaflight 同名机制）。默认 65%/0.30 为无头闭环标定
    # 定稿（双机型双 dt 全油门段去趋势峰峰 <5°/s 的最小衰减，见 physics/drone.py 与
    # test/test_world.py 集成回归）。additive 字段：v4 旧档缺失键加载时补此默认值，
    # 不升 schema
    tpa_percent: float = 65.0          # 满油门衰减 %
    tpa_breakpoint: float = 0.30       # 断点油门比例（断点以下不衰减）
    # 线性空气阻力 N·s/m（阻力 = drag_k×速度，physics.world.step 消费）。默认 1.2 =
    # T13 标定值——默认机（550g/推重比 7）俯冲极速 ~30m/s；轻量高推机（如 240g/8.6）
    # 按手感调低（0.4~0.6 → 极速 33~50m/s），0 = 无阻力。additive 字段不升 schema
    drag_k: float = 1.2
    # 当前电池电压 V：电机转速 ∝ KV×V、推力 ∝ 转速² → 推力按 (V/14.8)² 缩放
    # （physics.drone.REF_VOLTAGE）。默认 14.8（4S 标称）保持既有行为；参考：满电
    # 4S = 16.8（推力 ×1.288）、3S = 11.1（×0.563）。additive 字段不升 schema
    battery_voltage: float = 14.8
    params_schema: int = PARAMS_SCHEMA  # 参数版本：不匹配的存档加载时重置 pid+rate

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
_NESTED = {"pid": PIDGains, "rate": RateParams, "mappings": AxisBinding}

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
        # 参数版本迁移：v3 档仅 PID 小刻度过时（rate 语义未变）→ 只重置 pid；
        # 缺失/更早版本量级/结构与现行不符（PID 旧量级加载即 bang-bang、rate 为
        # 全局单份标量）→ pid 与 rate 整体重置。其余字段均保留。
        # 注意须查原始 JSON：_rebuild 对缺失键会补 dataclass 默认值，查对象会漏掉旧档
        if cls is DroneParams and isinstance(data, dict) \
                and data.get("params_schema") != PARAMS_SCHEMA:
            d = default_drone_params()
            obj.pid = d.pid
            if data.get("params_schema") != 3:
                obj.rate = d.rate
            obj.params_schema = PARAMS_SCHEMA
        return obj

    def save(self, name: str, obj):
        os.makedirs(self.config_dir, exist_ok=True)
        with open(self._path(name), "w", encoding="utf-8") as f:
            json.dump(dataclasses.asdict(obj), f, ensure_ascii=False, indent=2)
