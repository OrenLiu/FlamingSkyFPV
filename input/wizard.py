"""配置向导纯逻辑：轴捕获 + 遥控模式表（设计文档第 5 节）"""

# 各模式下每根杆对应的操作（左杆上下/左右，右杆上下/左右）。
# 依据标准遥控器模式定义（Mode 1/2/3，业界通用约定，参见
# rc-airplane-world.com/rc-transmitter-modes 等资料）：
#   Mode 2 美国手：左杆=油门+偏航，右杆=俯仰+横滚
#   Mode 1 日本手：右杆=油门+横滚，左杆=俯仰+偏航
#   Mode 3 中国手：左杆=俯仰+横滚，右杆=油门+偏航
MODE_STICKS = {
    "美国手": {"throttle": "左杆上下", "yaw": "左杆左右",       # Mode 2
               "pitch": "右杆上下", "roll": "右杆左右"},
    "日本手": {"throttle": "右杆上下", "roll": "右杆左右",       # Mode 1
               "pitch": "左杆上下", "yaw": "左杆左右"},
    "中国手": {"pitch": "左杆上下", "roll": "左杆左右",         # Mode 3
               "throttle": "右杆上下", "yaw": "右杆左右"},
}

# 各通道的方向指令（用户裁定：向导以带方向的指令引导，方向固定不变）
DIRECTION = {"throttle": "向上", "yaw": "向右", "roll": "向右", "pitch": "向上"}

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
        """采样各轴基线（点「开始识别」当帧杆的静止位置；油门不回中也成立）。
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
