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
    # 最大帧率模式："vsync"（垂直同步）/ "unlimited"（无限制）/ 具体数值字符串
    # （"30"~"240"）。app.py 双机制生效：vsync 走 GL_SWAP_CONTROL（重开窗口），
    # 数字走 clock.tick 限速（即时生效）。additive 字段：旧档缺失键加载时补默认
    # "60"（保持一期硬锁 60 行为），不升 schema
    fps_mode: str = "60"
    # 着色器预设包 id（shaderpacks.registry 的 PackInfo.id，内置为 builtin_performance /
    # builtin_quality，文件夹包为目录名）。additive 字段：旧档缺失键加载时补默认
    # "builtin_performance"，不升 schema（fps_mode 先例）。失效 id 由 App.
    # reload_shader_preset 回落 builtin_performance（spec：着色器预设系统设计 5.7）
    shader_preset: str = "builtin_performance"
    # 当前机预设 id（config/drones/<id>.json，二期多预设管理）。additive 字段：旧档
    # 缺失键加载时补默认 "default"，不升 schema（fps_mode/shader_preset 先例）。
    # id 失效（预设被删）由 App.__init__ 回落列表第一台，select_drone 切换时落盘
    current_drone_id: str = "default"

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

def _default_rate() -> dict:
    """三轴 rate 默认（DroneParams 平铺与 TuneParams 共用，防两处默认漂移）"""
    return {k: RateParams() for k in ("roll", "pitch", "yaw")}

def _default_pid() -> dict:
    """三轴 PID 默认（BF 量级刻度；DroneParams 平铺与 TuneParams 共用）"""
    # PID 量纲：误差 °/s → 电机油门单位。开环增益实测 roll/pitch ≈ 6.8e4、yaw ≈ 6.7e3
    # °/s²/单位输出，帧长 1/30 时临界 P（电机域）：roll/pitch ≈ 9e-4、yaw ≈ 9e-3（test/
    # manual/calibrate.py 无头闭环标定，双 dt 稳定、阶跃无过冲、悬停零饱和）。
    # 存档/展示为 BF 量级刻度（PIDController 内 ÷PID_MIX_SCALING=1e5 换算）：
    # 由电机域标定值 4e-4/4e-4/4e-6（roll/pitch）与 4e-3/1e-3/0（yaw）×1e5 换算而来；
    # yaw P 偏大是桨反扭矩权限弱的诚实物理结果
    return {
        "roll": PIDGains(40.0, 40.0, 0.4),
        "pitch": PIDGains(40.0, 40.0, 0.4),
        "yaw": PIDGains(400.0, 100.0, 0.0),
    }

@dataclass
class TuneParams:
    """调参域快照——与 DroneParams 平铺调参字段同构（二期 MSP 地面站调参用）：
    virtual_fc=True 时飞控仿真消费 virtual.tune，参数页消费平铺字段，两者经
    snapshot_tune/sync_tune_to_ui 互相同步"""
    rate_type: str = "betaflight_actual"  # "betaflight_actual" | "linear"
    rate: dict = field(default_factory=_default_rate)
    pid: dict = field(default_factory=_default_pid)
    tpa_percent: float = 65.0          # 满油门衰减 %（语义同 DroneParams 同名字段）
    tpa_breakpoint: float = 0.30       # 断点油门比例

@dataclass
class VirtualFCParams:
    """虚拟飞控域（二期 MSP 打通后地面站可调项；一期 UI 不触碰）"""
    tune: TuneParams = field(default_factory=TuneParams)
    # 模式开关区间：条目 (box_id, aux_index, lo_us, hi_us)——BETAFLIGHT 调参步骤
    # （如 ARM/MODE 分支设置）写入；MSP 打通任务消费。JSON round-trip 后元组成 list。
    # 出厂默认 ARM=AUX1 高段（1700-2100µs，box 0）：地面站 Modes 页开箱即见 ARM
    # 开关（MSP Task 22；eval_boxes 求值，AUX 未接线时条目不激活）
    mode_ranges: list = field(default_factory=lambda: [(0, 0, 1700, 2100)])
    osd_layout: dict = field(default_factory=dict)    # OSD 元素坐标（元素 id → 坐标）
    osd_enabled: dict = field(default_factory=dict)   # OSD 元素开关（元素 id → bool）
    craft_name: str = ""               # 机名（MSP Craft Name）
    cells: int = 4                     # 电芯节数（电池电压计算基准，默认 4S）

# 参数 schema 版本：5 = 虚拟飞控域（DroneParams 新增 virtual_fc/virtual 字段，
# additive 迁移：pid/rate 不重置）。4 = PID 刻度对齐 Betaflight 量级（P≈40，见
# physics.drone.PID_MIX_SCALING）。3 = 三轴 rate + 小刻度 PID（仅 pid 过时）；
# 缺失/更早 = 旧量级 PID 或全局单份 rate。加载时按版本迁移（见 ConfigStore.load）
PARAMS_SCHEMA = 5

@dataclass
class DroneParams:
    """无人机全参数——参数页编辑对象，飞控仿真消费对象"""
    weight_g: float = 550.0           # 重量（克）
    motor_kv: int = 1750              # 电机 KV
    thrust_ratio: float = 7.0         # 推重比（默认 kv/250，可覆盖）
    camera_angle_deg: float = 20.0    # 摄像头仰角
    rate_type: str = "betaflight_actual"  # "betaflight_actual" | "linear"
    # rate 曲线三轴独立（用户裁定）：横滚/俯仰/偏航各自可调
    rate: dict = field(default_factory=_default_rate)
    # PID 三轴增益，默认见 _default_pid（BF 量级刻度，标定来源注释在彼处）
    pid: dict = field(default_factory=_default_pid)
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
    # 虚拟飞控开关（二期 MSP 打通）：True = 飞控仿真消费 virtual.tune（地面站调参
    # 生效）；False = 消费上方平铺调参字段（一期行为）
    virtual_fc: bool = False
    # 虚拟飞控域（TuneParams 调参快照 + MSP mode_ranges/OSD/机名/电芯数）
    virtual: VirtualFCParams = field(default_factory=VirtualFCParams)
    params_schema: int = PARAMS_SCHEMA  # 参数版本：不匹配的存档加载时按版本迁移

    # ---- 调参域同步三方法（平铺字段 ↔ virtual.tune） ----
    # 逐轴 dataclass 重建实现深拷贝（同 screens/drone_params._clone_params 手法），
    # 两域互不共享嵌套实例

    def snapshot_tune(self) -> TuneParams:
        """平铺调参字段 → TuneParams 深拷贝快照（改快照不影响平铺字段）"""
        return TuneParams(
            rate_type=self.rate_type,
            rate={k: RateParams(**vars(v)) for k, v in self.rate.items()},
            pid={k: PIDGains(**vars(v)) for k, v in self.pid.items()},
            tpa_percent=self.tpa_percent,
            tpa_breakpoint=self.tpa_breakpoint,
        )

    def active_tune(self) -> TuneParams:
        """当前生效调参域：virtual_fc=True → virtual.tune；False → 平铺字段快照"""
        if self.virtual_fc:
            return self.virtual.tune
        return self.snapshot_tune()

    def sync_tune_to_ui(self) -> None:
        """virtual.tune 深拷贝回写平铺字段（地面站调参后同步参数页；回写后脱钩）"""
        t = self.virtual.tune
        self.rate_type = t.rate_type
        self.rate = {k: RateParams(**vars(v)) for k, v in t.rate.items()}
        self.pid = {k: PIDGains(**vars(v)) for k, v in t.pid.items()}
        self.tpa_percent = t.tpa_percent
        self.tpa_breakpoint = t.tpa_breakpoint

@dataclass
class AxisBinding:
    """通道-轴绑定"""
    axis: int = -1                    # -1 表示未绑定
    inverted: bool = False

@dataclass
class AuxBinding:
    """AUX 通道绑定（二期模式开关等）。kind："none" 未绑定 / "button" 按钮两档 /
    "axis3" 轴三段（阈值 ±1/3）"""
    kind: str = "none"
    index: int = -1                   # 按钮号或轴号，-1 表示未绑定

def _default_mappings() -> dict:
    """mappings 默认工厂：主四通道 AxisBinding + aux1..aux4 AuxBinding（均未绑定）"""
    m = {k: AxisBinding() for k in ("throttle", "yaw", "roll", "pitch")}
    m.update({f"aux{i}": AuxBinding() for i in range(1, 5)})
    return m

@dataclass
class RCConfig:
    """遥控配置：键盘或手柄 + 四通道绑定 + 四路 AUX 绑定"""
    mode: str = "keyboard"            # "keyboard" | "joystick"
    device_name: str = ""
    mappings: dict = field(default_factory=_default_mappings)

def default_drone_params() -> DroneParams:
    """出厂默认（参数页"恢复默认"用）"""
    return DroneParams()

def default_rc_config() -> RCConfig:
    return RCConfig()

# dataclass 字段名 → 嵌套 dataclass 类型表（重建用）
_NESTED = {"pid": PIDGains, "rate": RateParams, "mappings": AxisBinding}
# 字段值整体为单个 dataclass 的字段名 → 类型表（DroneParams.virtual →
# VirtualFCParams，VirtualFCParams.tune → TuneParams；递归重建用）
_NESTED_ONE = {"virtual": VirtualFCParams, "tune": TuneParams}

def _rebuild(cls, data: dict):
    """dict → dataclass 实例，递归重建嵌套 dict[str, dataclass] 与单 dataclass
    字段（缺失键用默认值补齐）"""
    kwargs = {}
    for f in dataclasses.fields(cls):
        if f.name not in data:
            continue
        v = data[f.name]
        if f.name in _NESTED_ONE and isinstance(v, dict):
            v = _rebuild(_NESTED_ONE[f.name], v)
        elif f.name in _NESTED and isinstance(v, dict):
            # 以默认实例的同名字典为基底，JSON 数据覆盖——仅保存了部分键（如只绑定了
            # throttle）时，其余通道回落默认绑定，避免 round-trip 丢键
            base = {k: dataclasses.asdict(inst)
                    for k, inst in getattr(cls(), f.name).items()}
            base.update(v)
            # mappings 值类型按键名选：aux 前缀键 → AuxBinding（kind/index），
            # 其余 → AxisBinding（axis/inverted）——同一 dict 两种绑定类型并存，
            # 统一按 AxisBinding 重建会丢 aux 的 kind/index 字段
            if f.name == "mappings":
                v = {k: _rebuild(AuxBinding if k.startswith("aux") else AxisBinding,
                                 sub) for k, sub in base.items()}
            else:
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
        # 参数版本迁移（三档）：v4 档 additive——pid/rate 自调保留，仅升版本
        # （缺失的 virtual_fc/virtual 键 _rebuild 已补默认）；v3 档仅 PID 小刻度
        # 过时（rate 语义未变）→ 只重置 pid；缺失/更早版本量级/结构与现行不符
        # （PID 旧量级加载即 bang-bang、rate 为全局单份标量）→ pid 与 rate 整体
        # 重置。其余字段均保留。
        # 注意须查原始 JSON：_rebuild 对缺失键会补 dataclass 默认值，查对象会漏掉旧档
        if cls is DroneParams and isinstance(data, dict) \
                and data.get("params_schema") != PARAMS_SCHEMA:
            ver = data.get("params_schema")
            if ver != 4:                    # 非 v4 档才重置（v4 档 additive：仅升版本，不重置任何字段）
                d = default_drone_params()
                obj.pid = d.pid
                if ver != 3:                # v3 档仅 pid 过时，rate 语义未变
                    obj.rate = d.rate
            obj.params_schema = PARAMS_SCHEMA
        return obj

    def save(self, name: str, obj):
        os.makedirs(self.config_dir, exist_ok=True)
        with open(self._path(name), "w", encoding="utf-8") as f:
            json.dump(dataclasses.asdict(obj), f, ensure_ascii=False, indent=2)

# ---- 预设存储函数集（二期多预设管理数据层：drones_dir = DRONES_DIR，每机一文件） ----
# 六个函数全部经 ConfigStore(drones_dir) 复用 load/save：损坏备份 .bak 与
# params_schema 三档迁移自动继承，无需各自重写容错逻辑

# 配置根（settings/rc 存档目录）与预设目录（每机一 json）。常量定义在此、
# app.py 与 screens/drone_params.py 两侧引用同一来源，防字面量漂移
# （screens 不 import app，依赖方向仍单向：screens → config_store）
CONFIG_DIR = "config"
DRONES_DIR = os.path.join(CONFIG_DIR, "drones")

# 默认机预设 id：migrate_legacy_drone 的迁移目标 + App 启动空列表时自动建机，
# 两处共用同一常量防字面量漂移
DEFAULT_PRESET_ID = "默认机"

def sanitize_preset_id(name: str) -> str:
    """预设 id 清洗：strip；"/" "\\" 与控制字符（ord<32）替换 "_"；空 → "default" """
    cleaned = "".join("_" if ch in ("/", "\\") or ord(ch) < 32 else ch
                      for ch in name.strip())
    return cleaned or "default"

def list_preset_ids(drones_dir: str) -> list[str]:
    """预设目录枚举：.json 文件名去后缀 sorted 返回；目录不存在 → []
    （.bak 以 .bak 结尾不匹配 .json 后缀，天然不入列表）"""
    try:
        names = os.listdir(drones_dir)
    except OSError:
        return []
    return sorted(name[:-len(".json")] for name in names if name.endswith(".json"))

def load_preset(drones_dir: str, preset_id: str) -> DroneParams:
    """读取预设为 DroneParams（无文件 → 默认；损坏 → 备份 .bak 回默认；
    旧 schema → 迁移；均由 ConfigStore.load 继承）"""
    return ConfigStore(drones_dir).load(preset_id, DroneParams)

def save_preset(drones_dir: str, preset_id: str, params: DroneParams) -> None:
    """落盘预设；id 先 sanitize 兜底（非法字符/空名不产生坏路径）"""
    ConfigStore(drones_dir).save(sanitize_preset_id(preset_id), params)

def delete_preset(drones_dir: str, preset_id: str) -> None:
    """删除预设文件；不存在静默返回"""
    try:
        os.remove(os.path.join(drones_dir, f"{preset_id}.json"))
    except FileNotFoundError:
        pass

def copy_preset(drones_dir: str, src_id: str, new_name: str) -> str:
    """复制预设：new_name sanitize 后为目标 id，已存在则追加 -2/-3 序号至空闲。
    深拷贝语义由 save 的 asdict 序列化天然保证（load→save 后两预设互不影响）"""
    params = load_preset(drones_dir, src_id)
    base = sanitize_preset_id(new_name)
    existing = set(list_preset_ids(drones_dir))
    new_id, n = base, 2
    while new_id in existing:
        new_id = f"{base}-{n}"
        n += 1
    ConfigStore(drones_dir).save(new_id, params)
    return new_id

def migrate_legacy_drone(config_dir: str) -> None:
    """一期存量档迁移：<config_dir>/drone.json 存在且 drones/DEFAULT_PRESET_ID.json
    不存在 → 逐字深拷贝为默认机预设 + 旧档改名 drone.json.migrated 归档；否则
    不操作（幂等：二次调用时旧档已不在，默认机参数不被覆盖）"""
    legacy = os.path.join(config_dir, "drone.json")
    drones_dir = os.path.join(config_dir, "drones")
    target = os.path.join(drones_dir, f"{DEFAULT_PRESET_ID}.json")
    if not os.path.exists(legacy) or os.path.exists(target):
        return
    os.makedirs(drones_dir, exist_ok=True)
    shutil.copy2(legacy, target)
    os.replace(legacy, legacy + ".migrated")
