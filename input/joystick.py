"""HID 手柄输入：按 RCConfig.mappings 绑定轴（设计文档第 5 节）"""
from .channels import Channels, RCInput
from config_store import AxisBinding

class JoystickInput(RCInput):
    """get_axis_fn(i) 返回第 i 轴原始值 [-1,1]（App 层接 pygame.joystick）"""
    def __init__(self, get_axis_fn, mappings: dict, deadband: float = 0.02):
        self._get_axis = get_axis_fn
        self._mappings = mappings
        self._deadband = deadband

    def _channel(self, name: str, center: float) -> float:
        """取绑定轴值：死区归零 + 反向。center: 0=对称轴, 0.5=油门轴"""
        b = self._mappings.get(name) or AxisBinding()
        if b.axis < 0:
            return 0.0  # 未绑定
        v = self._get_axis(b.axis)
        if b.inverted:
            v = -v     # 先在 [-1,1] 域反向
        if abs(v - 0.0) <= self._deadband and center == 0.0:
            return 0.0  # 对称轴死区
        if center == 0.5:
            return max(0.0, min(1.0, (v + 1.0) / 2.0))  # 油门：[-1,1]→[0,1]
        return max(-1.0, min(1.0, v))

    def read(self, dt: float) -> Channels:
        return Channels(
            throttle=self._channel("throttle", 0.5),
            yaw=self._channel("yaw", 0.0),
            roll=self._channel("roll", 0.0),
            pitch=self._channel("pitch", 0.0))
