"""配置向导纯逻辑：轴捕获 + 遥控模式表（设计文档第 5 节）+ 演示图换算"""

# 各模式下每根杆对应的操作（左杆上下/左右，右杆上下/左右）。
# 依据标准遥控器模式定义（Mode 1/2/3，业界通用约定，参见
# rc-airplane-world.com/rc-transmitter-modes 等资料）：
#   Mode 2 美国手：左杆=油门+偏航，右杆=俯仰+横滚
#   Mode 1 日本手：右杆=油门+横滚，左杆=俯仰+偏航
#   Mode 3 中国手：左杆=俯仰+横滚，右杆=油门+偏航
# 结构化表 (杆侧, 轴向)：rc_setup 演示图按它定位每步动画的是哪个点。
STICK_AXIS = {
    "美国手": {"throttle": ("左", "上下"), "yaw": ("左", "左右"),
               "pitch": ("右", "上下"), "roll": ("右", "左右")},
    "日本手": {"throttle": ("右", "上下"), "roll": ("右", "左右"),
               "pitch": ("左", "上下"), "yaw": ("左", "左右")},
    "中国手": {"pitch": ("左", "上下"), "roll": ("左", "左右"),
               "throttle": ("右", "上下"), "yaw": ("右", "左右")},
}

# 各模式下每根杆对应的操作文案（由 STICK_AXIS 派生，字符串与历史版本逐字一致）
MODE_STICKS = {mode: {ch: f"{side}杆{ax}" for ch, (side, ax) in table.items()}
               for mode, table in STICK_AXIS.items()}

# 各通道的方向指令（用户裁定：向导以带方向的指令引导，方向固定不变）
DIRECTION = {"throttle": "向上", "yaw": "向右", "roll": "向右", "pitch": "向上"}

# 指令方向 → 演示点屏幕方向单位向量 (dx, dy)（imgui y 向下：向上 = dy −1）
DIRECTION_VEC = {"向上": (0.0, -1.0), "向右": (1.0, 0.0)}

# 捕获结果的极性修正：向导假定「指令方向 = 通道正杆义」——对 throttle/yaw/roll
# 成立（油门上推+、yaw右+、roll右+），唯 pitch 杆义为「拉杆+」（channels.py），
# 指令「向上」是推杆 = 负杆义 → 捕获的方向标志须翻转；否则飞行时推杆读出抬头，
# 俯仰整体反向。油门上推读出高油门不受影响，保持不变。
POLARITY_FLIP = frozenset({"pitch"})

def channel_inverted(channel: str, raw_inverted: bool) -> bool:
    """AxisCapture 判得的方向标志 → 该通道绑定的 inverted（按 POLARITY_FLIP 翻转）"""
    return raw_inverted != (channel in POLARITY_FLIP)

def demo_motion(mode, channel):
    """向导演示动画参数 → (杆侧, 轴向, 屏幕方向单位向量 (dx, dy))。
    杆侧∈{"左","右"}，轴向∈{"上下","左右"}；向量 = 指令方向在屏幕上的指向
    （imgui y 向下，"向上" = dy −1）。模式/通道不在表内返回 None（如键盘模式）。"""
    entry = STICK_AXIS.get(mode, {}).get(channel)
    if entry is None:
        return None
    side, axis_name = entry
    return (side, axis_name, DIRECTION_VEC[DIRECTION[channel]])

def verify_offset(channel, value):
    """验证阶段演示点位移（imgui y 向下，单位=框半高）：按杆义换算——
    油门全量程 [0,1] 线性映射框底→框顶（最低位=+1、最高位=−1，旧映射只走
    上半程致最低位显示成中位）；俯仰拉杆=+（点向下 dy=+v）；yaw/roll 右=+
    （点向右 dx=+v）。未识别通道回中 (0, 0)。"""
    if channel == "throttle":
        return (0.0, 1.0 - 2.0 * value)
    if channel == "pitch":
        return (0.0, value)
    if channel in ("yaw", "roll"):
        return (value, 0.0)
    return (0.0, 0.0)

class AxisCapture:
    """喂入每帧全部轴值，跟踪各轴 min/max；用于"动一动杆"自动识别轴。
    以 begin 采样的基线判定移动方向：偏向 −1 → 该轴 inverted。"""
    def __init__(self, threshold: float = 0.25, exclude: set = None):
        self.threshold = threshold
        self.exclude = exclude or set()   # 已绑定到其他通道的轴
        self._min = {}
        self._max = {}
        self._baseline = None             # begin 采样的各轴基线 {轴: 值}

    def begin(self, axis_values: list):
        """采样各轴基线（点「继续」当帧杆的静止位置；油门不回中也成立）。
        基线同时作 min/max 初始值——位移从基线起算，而非仅 feed 帧的极差"""
        self._baseline = dict(enumerate(axis_values))
        self._min = dict(self._baseline)
        self._max = dict(self._baseline)

    def feed(self, axis_values: list):
        if self._baseline is None:   # 未 begin 过：以当帧为基线（向后兼容）
            self.begin(axis_values)
        for i, v in enumerate(axis_values):
            self._min[i] = min(self._min.get(i, v), v)
            self._max[i] = max(self._max.get(i, v), v)

    def displacement(self, axis: int) -> float:
        return self._max.get(axis, 0.0) - self._min.get(axis, 0.0)

    def result(self, max_axis_count: int):
        """位移最大且超阈值的未排除轴，连同方向判定返回 (axis, inverted)；
        无则 None。方向：偏向 +1 → inverted=False；偏向 −1 → inverted=True"""
        best, best_d = None, self.threshold
        for a in range(max_axis_count):
            if a in self.exclude:
                continue
            d = self.displacement(a)
            if d > best_d:
                best, best_d = a, d
        if best is None:
            return None
        base = self._baseline.get(best, 0.0)   # best 超阈值必有 feed，必有基线
        inverted = (base - self._min[best]) > (self._max[best] - base)
        return (best, inverted)
