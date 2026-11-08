"""配置向导纯逻辑：轴捕获 + 遥控模式表（设计文档第 5 节）"""

# 各模式下每根杆对应的操作（左杆上下/左右，右杆上下/左右）
MODE_STICKS = {
    "美国手": {"throttle": "左杆上下", "yaw": "左杆左右",
               "pitch": "右杆上下", "roll": "右杆左右"},
    "中国手": {"throttle": "左杆上下", "yaw": "左杆左右",
               "pitch": "右杆左右", "roll": "右杆上下"},
    "日本手": {"throttle": "右杆上下", "yaw": "右杆左右",
               "pitch": "左杆上下", "roll": "左杆左右"},
}

class AxisCapture:
    """喂入每帧全部轴值，跟踪各轴 min/max；用于"动一动杆"自动识别轴"""
    def __init__(self, threshold: float = 0.25, exclude: set = None):
        self.threshold = threshold
        self.exclude = exclude or set()   # 已绑定到其他通道的轴
        self._min = {}
        self._max = {}

    def feed(self, axis_values: list):
        for i, v in enumerate(axis_values):
            self._min[i] = min(self._min.get(i, v), v)
            self._max[i] = max(self._max.get(i, v), v)

    def displacement(self, axis: int) -> float:
        return self._max.get(axis, 0.0) - self._min.get(axis, 0.0)

    def result(self, max_axis_count: int):
        """位移最大且超阈值的未排除轴；无则 None"""
        best, best_d = None, self.threshold
        for a in range(max_axis_count):
            if a in self.exclude:
                continue
            d = self.displacement(a)
            if d > best_d:
                best, best_d = a, d
        return best
