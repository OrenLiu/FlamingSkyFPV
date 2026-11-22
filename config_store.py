"""配置持久化：dataclass ↔ JSON，损坏自动备份回默认（设计文档第 7 节）"""
import dataclasses
import json
import logging
import math
import os
import shutil
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

# 显示模式合法值（app 窗口分支与设置页共用同一来源，防字面量漂移）
DISPLAY_MODES = ("fullscreen", "fixed_window", "window")

@dataclass
class Settings:
    """应用设置（显示模式 / 分辨率 / 帧率 / 画质预设 / 当前机）"""
    resolution: tuple = (1280, 720)   # (宽, 高)：全屏与固定大小窗口化的逻辑分辨率
    # 显示模式："fullscreen" 全屏 / "fixed_window" 固定大小窗口化 / "window"
    # 窗口化（可拖拽调窗口大小与最大化，分辨率跟随窗口）。additive 字段：旧档
    # 缺失键时由 ConfigStore.load 按 legacy fullscreen bool 折算（True→fullscreen，
    # False→fixed_window），不升 schema（fps_mode 先例）
    display_mode: str = "fixed_window"
    # 窗口化模式的窗口尺寸 (宽, 高)：拖拽/最大化随窗口实时更新（内存），
    # 退出经 save_all 落盘，下次启动恢复窗口大小。additive 字段：旧档缺失键
    # 补默认，不升 schema（fps_mode 先例）
    window_size: tuple = (1280, 720)
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
    cells: int = 4                     # 电芯节数（电池电压计算基准，默认 4S；
                                       # cells_auto=True 时为派生值——加载/
                                       # 电压编辑时按 detect_cell_count 重算）
    # 节数自动识别开关（Task 46）：True=按 battery_voltage 自动识别（真机
    # 同款）；False=cells 手动保持。additive 字段：旧预设缺键 → dataclass
    # 默认 True（自动），不升 schema（fps_mode 先例）
    cells_auto: bool = True
    # RTC 时钟（Task 49，MSP_SET_RTC(246) 存储）：unix 秒（毫秒折入小数），
    # 0=未设置——OSD RTC_DATETIME 元素此时不渲染（flight_snapshot 格式化
    # 为 "%Y-%m-%d %H:%M:%S" 全格式文本，固件同式）。additive 字段：旧
    # 预设缺键补默认 0.0，不升 schema（cells_auto 先例）
    rtc_unix_s: float = 0.0
    # RTC 走时墙锚点（Task 52）：MSP_SET_RTC(246) 接收时刻的 time.time()
    # 值，0=无锚点（Task 49 旧档——rtc_text 冻结在设置时刻的旧行为）。
    # 有效 RTC 时间 = rtc_unix_s + (time.time() - rtc_recv_unix)，对应固件
    # 真实走时 RTC（OSD 每秒刷新显示）。additive 字段同上不升 schema
    rtc_recv_unix: float = 0.0
    # 累计飞行次数（Task 52，OSD TOTAL_FLIGHTS）：固件 stats.c statsOnDisarm
    # 语义——加锁沿且解锁时长 ≥ stats_min_armed_time_s 才 +1（出厂
    # STATS_OFF=-1 不计数；pg/stats.c PG_RESET_TEMPLATE L33）。模拟器无
    # stats PG 配置域，按阈值 0 的计数开启语义：每次解锁→加锁沿 +1
    # （flight.py _track_flight_stats）。持久化走预设保存既有机制。
    # additive 字段：旧预设缺键补默认 0，不升 schema
    total_flights: int = 0

def detect_cell_count(voltage_v: float) -> int:
    """按电池总电压自动识别电芯节数：int(V/4.30)+1 钳 [1,8]。

    固件依据（Betaflight 4.5.1 src/main/sensors/battery.c
    batteryUpdatePresence 权威算法）：cells = voltage / vbatmaxcellvoltage
    + 1，VBAT_CELL_VOLTAGE_DEFAULT_MAX = 430cV = 4.30V、上限
    MAX_AUTO_DETECT_CELL_COUNT = 8。仿真域补充下限钳 1（电池恒在场，
    固件 0 节数走「未接电池」分支，仿真无此态）。非有限电压（NaN/
    Infinity——json 默认可解析、外部编辑/历史落盘可入）按下限 1 返回：
    int() 直转会抛 ValueError/OverflowError 且发生在 load 内即启动崩溃，
    绕过损坏备份 .bak 兜底。"""
    if not math.isfinite(voltage_v):
        return 1
    return min(8, max(1, int(voltage_v / 4.30) + 1))

@dataclass
class BatteryParams:
    """真实电池模拟配置（physics/battery.py 模型消费）。additive 域：旧预设
    缺键 → dataclass 默认（sim_enabled=False 静态路径行为零变化），不升
    schema（cells_auto/drag_k 先例）。关闭时全字段仅存储不消费"""
    sim_enabled: bool = False
    chem: str = "lipo"                # "lipo" | "lihv" | "liion"（physics.battery.CHEMS 键）
    cells: int = 4                    # 节数（sim 模式节数唯一源，virtual.cells 镜像）
    capacity_mah: int = 1500          # 容量 mAh
    c_rating: int = 100               # 放电倍率 C（满油门电流 = Ah×C）
    warning_cell_v: float = 3.5       # 单芯警告电压 V（OSD LOW BATTERY 阈）
    min_cell_v: float = 3.3           # 单芯最低电压 V（OSD LAND NOW 阈）
    max_cell_v: float = 4.3           # 单芯最高电压 V（OSD 图标段缩放上下限）


# 参数 schema 版本：6 = 阻力二次化（drag_k 语义 线性 N·s/m → 二次 N·s²/m²，
# 迁移换算 drag_k /= DRAG_REF_SPEED）。5 = 虚拟飞控域（DroneParams 新增
# virtual_fc/virtual 字段，additive 迁移：pid/rate 不重置）。4 = PID 刻度对齐
# Betaflight 量级（P≈40，见 physics.drone.PID_MIX_SCALING）。3 = 三轴 rate +
# 小刻度 PID（仅 pid 过时）；缺失/更早 = 旧量级 PID 或全局单份 rate。加载时
# 按版本迁移（见 ConfigStore.load）
PARAMS_SCHEMA = 6

# v6 迁移参考速度 m/s：线性阻力 T13 极速标定参考点（~30m/s）。旧线性值按
# drag_k/DRAG_REF_SPEED 换算为二次系数，使 30m/s 处阻力大小不变、高速手感连续
DRAG_REF_SPEED = 30.0

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
    # 二次空气阻力 N·s²/m²（阻力 = drag_k×速度²，方向沿速度反向；physics.
    # world.step 消费）。默认 0.0415 = T13 极速标定换算——默认机（550g/推重比 7）
    # 平飞极速 ~30m/s 不变；自由落体终端速度 = sqrt(m·g/drag_k)（如 240g/0.0167
    # → ~12m/s ≈ 43km/h，修复线性模型 21km/h 天花板）。旧线性档迁移 /30
    # （params_schema v6），0 = 无阻力
    drag_k: float = 0.0415
    # 当前电池电压 V：电机转速 ∝ KV×V、推力 ∝ 转速² → 推力按 (V/14.8)² 缩放
    # （physics.drone.REF_VOLTAGE）。默认 14.8（4S 标称）保持既有行为；参考：满电
    # 4S = 16.8（推力 ×1.288）、3S = 11.1（×0.563）。additive 字段不升 schema
    battery_voltage: float = 14.8
    # 真实电池模拟配置（physics/battery.py 模型消费）：additive 域——旧预设缺键
    # → dataclass 默认（sim_enabled=False），不升 schema（cells_auto 先例）
    battery: BatteryParams = field(default_factory=BatteryParams)
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
        """当前生效调参域：virtual_fc=True → virtual.tune 本体（非拷贝——
        MSP SET 路径为唯一合法写方，直接改返回值即改 virtual 域）；False →
        平铺字段深拷贝快照（snapshot_tune，改快照不影响平铺字段）"""
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

# ---- 节数手动/自动切换（Task 46；UI 与 MSP SET 95 共用纯函数） ----

def set_cells_manual(params: DroneParams, v: int) -> None:
    """节数转手动：cells=min(8, max(1, int(v)))（下限 1 防电压计算除零/
    失真、上限 8 与 auto 域一致——越界值 GET 94 回发时 Configurator 按
    s8 解码会变负数）、cells_auto=False"""
    params.virtual.cells = min(8, max(1, int(v)))
    params.virtual.cells_auto = False
    if params.battery.sim_enabled:
        params.battery.cells = params.virtual.cells   # sim 模式节数源=电池配置

def set_cells_auto(params: DroneParams) -> None:
    """节数转自动：cells_auto=True 并按当前 battery_voltage 重算
    （幂等：已是自动态时重算同值）"""
    params.virtual.cells_auto = True
    if params.battery.sim_enabled:
        # sim 模式节数源=电池配置：auto 标志语义=镜像电池节数（不按电压识别）
        params.virtual.cells = params.battery.cells
    else:
        params.virtual.cells = detect_cell_count(params.battery_voltage)

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
    inverted: bool = False            # 开关方向取反（±1 互换、中位 0 不动）

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
    """出厂默认代码值；逐项回退/新建无人机的参考源是 init_drone 出厂预设，
    仅当该预设缺失时回落到此"""
    return DroneParams()

def default_rc_config() -> RCConfig:
    return RCConfig()

# dataclass 字段名 → 嵌套 dataclass 类型表（重建用）
_NESTED = {"pid": PIDGains, "rate": RateParams, "mappings": AxisBinding}
# 字段值整体为单个 dataclass 的字段名 → 类型表（DroneParams.virtual →
# VirtualFCParams，VirtualFCParams.tune → TuneParams；递归重建用）
_NESTED_ONE = {"virtual": VirtualFCParams, "tune": TuneParams,
               "battery": BatteryParams}

# int 键字典字段（Task 39）：osd_layout/osd_enabled 的键为 OSD 元素 id
# （int）。JSON object 键恒序列化为 str（json 模块限制），round-trip 后
# int 键变 str，消费端 dict[int].get 全 miss → 布局回默认（用户实测）。
# _rebuild 为「盘上 JSON → 对象」的唯一边界，在此归一根治，所有消费方
# （msp handlers GET / osd.build_frame / handlers SET 写路径）统一受益
_INT_KEY_FIELDS = frozenset({"osd_layout", "osd_enabled"})

def _int_key_dict(d: dict) -> dict:
    """dict 键 int 归一：能转 int 的键（str "1"、int 1）统一为 int；
    不可归一的键原样保留（防御不丢用户数据，int 消费端 .get 自然 miss）。
    非 dict 输入原样返回（缺键防御由 _rebuild 默认值兜底）。"""
    if not isinstance(d, dict):
        return d
    out = {}
    for k, v in d.items():
        try:
            out[int(k)] = v
        except (TypeError, ValueError):
            out[k] = v
    return out

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
        elif f.name in _INT_KEY_FIELDS and isinstance(v, dict):
            v = _int_key_dict(v)   # osd_layout/osd_enabled 键 int 归一（见上）
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
            log.info("config missing, using defaults: %s", path)
            return cls()  # 无文件 → 默认值（首启动自动生成的效果由后续 save 达成）
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            obj = _rebuild(cls, data)
            log.info("load config: %s", path)
        except (json.JSONDecodeError, TypeError, KeyError):
            log.warning("config corrupt, backed up to .bak, using defaults: %s", path)
            shutil.copy2(path, path + ".bak")  # 备份损坏文件
            return cls()
        # Settings 显示模式迁移/归一：legacy fullscreen bool → display_mode
        # （缺键时折算：True→fullscreen，False→fixed_window）；display_mode
        # 非法值归一 fixed_window；window_size 防御归一（非二元/非正数→默认）。
        # 缺键判定查原始 JSON——_rebuild 对缺失键补 dataclass 默认值，会掩盖
        # 「旧档缺键」事实（params_schema 迁移同款手法）
        if cls is Settings:
            if "display_mode" not in data:
                obj.display_mode = ("fullscreen" if data.get("fullscreen")
                                    else "fixed_window")
            if obj.display_mode not in DISPLAY_MODES:
                obj.display_mode = "fixed_window"
            try:
                ws = (int(obj.window_size[0]), int(obj.window_size[1]))
                obj.window_size = ws if ws[0] > 0 and ws[1] > 0 else (1280, 720)
            except (TypeError, ValueError, IndexError):
                obj.window_size = (1280, 720)
        # 参数版本迁移（v6/5/4/更早四档）：v6 档仅 drag_k 语义换算（线性→二次，
        # /DRAG_REF_SPEED）；v5/v4 档 additive——pid/rate 自调保留，仅 drag_k
        # 换算+升版本（缺失的 virtual_fc/virtual 键 _rebuild 已补默认）；v3 档仅
        # PID 小刻度过时（rate 语义未变）→ 只重置 pid；缺失/更早版本量级/结构
        # 与现行不符（PID 旧量级加载即 bang-bang、rate 为全局单份标量）→ pid
        # 与 rate 整体重置。其余字段均保留。
        # 注意须查原始 JSON：_rebuild 对缺失键会补 dataclass 默认值，查对象会漏掉旧档
        if cls is DroneParams and isinstance(data, dict) \
                and data.get("params_schema") != PARAMS_SCHEMA:
            ver = data.get("params_schema")
            log.info("params schema migrated: v%s -> v%s (%s)", ver, PARAMS_SCHEMA, path)
            # v6 阻力二次化：凡旧档 drag_k 一律 /DRAG_REF_SPEED——30m/s 参考速度
            # 处阻力大小不变（线性 T13 极速标定参考点），高速手感连续；v4 及更早
            # 档 drag_k 同为线性语义，一并换算
            obj.drag_k = obj.drag_k / DRAG_REF_SPEED
            if ver not in (4, 5):           # v4/v5 档 additive：pid/rate 自调保留不重置
                d = default_drone_params()
                obj.pid = d.pid
                if ver != 3:                # v3 档仅 pid 过时，rate 语义未变
                    obj.rate = d.rate
            obj.params_schema = PARAMS_SCHEMA
        # 节数源判定（真实电池模拟）：sim_enabled → 电池节数为唯一源（虚拟
        # 飞控 cells 镜像电池配置，电压自动识别不适用——节数是显式配置）；
        # 静态模式 auto 态按电压重算（Task 46 既有语义不变）。schema 迁移
        # 在前（仅涉 pid/rate，与本段无交集）
        if cls is DroneParams:
            if obj.battery.sim_enabled:
                obj.virtual.cells = obj.battery.cells
            elif obj.virtual.cells_auto:
                obj.virtual.cells = detect_cell_count(obj.battery_voltage)
        return obj

    def save(self, name: str, obj):
        """原子写：先写 <name>.json.tmp 再 os.replace 覆盖——写中途失败
        （磁盘满/进程被杀）不损坏旧档，tmp 残留时清理后原样抛异常"""
        os.makedirs(self.config_dir, exist_ok=True)
        path = self._path(name)
        tmp = path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(dataclasses.asdict(obj), f, ensure_ascii=False,
                          indent=2)
            os.replace(tmp, path)
            log.info("save config: %s", path)
        except BaseException:
            try:
                os.remove(tmp)
            except OSError:
                pass            # tmp 未创建/已清理：无需处理
            raise

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
    """删除预设文件；id 先 sanitize 兜底（与 save_preset 同一清洗，非法
    id 永不逃逸出预设目录）；不存在静默返回"""
    try:
        os.remove(os.path.join(
            drones_dir, f"{sanitize_preset_id(preset_id)}.json"))
        log.info("delete preset: %s", preset_id)
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
    log.info("copy preset: %s -> %s", src_id, new_id)
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
