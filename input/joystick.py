"""HID 手柄输入：按 RCConfig.mappings 绑定轴（设计文档第 5 节）"""
from .channels import Channels, RCInput
from config_store import AxisBinding, AuxBinding

# axis3 三段阈值：|v| ≤ 1/3 → 0（严格不等：<−1/3 才 −1、>1/3 才 +1）
_AXIS3_THRESHOLD = 1.0 / 3.0

class JoystickInput(RCInput):
    """get_axis_fn(i) 返回第 i 轴原始值 [-1,1]（App 层接 pygame.joystick）。
    二期新增 get_button_fn(i) 按钮态与 num_axes/num_buttons 轴数/按钮数（均带
    默认值，旧构造零回归）：按钮源供 AUX button 绑定，两计数供主轴/AUX 轴与
    AUX 按钮越界防御（换设备回中，防 get_axis/get_button 越界抛 IndexError）"""
    def __init__(self, get_axis_fn, mappings: dict, deadband: float = 0.02,
                 get_button_fn=None, num_axes: int | None = None,
                 num_buttons: int | None = None):
        self._get_axis = get_axis_fn
        self._mappings = mappings
        self._deadband = deadband
        self._get_button = get_button_fn
        self._num_axes = num_axes
        self._num_buttons = num_buttons

    def _channel(self, name: str, center: float) -> float:
        """取绑定轴值：死区归零 + 反向。center: 0=对称轴, 0.5=油门轴"""
        b = self._mappings.get(name) or AxisBinding()
        if b.axis < 0:
            return 0.0  # 未绑定
        if self._num_axes is not None and b.axis >= self._num_axes:
            return 0.0  # 轴越界按未绑定回中（换设备防御；num_axes 未知不检查）
        v = self._get_axis(b.axis)
        if b.inverted:
            v = -v     # 先在 [-1,1] 域反向
        if abs(v - 0.0) <= self._deadband and center == 0.0:
            return 0.0  # 对称轴死区
        if center == 0.5:
            return max(0.0, min(1.0, (v + 1.0) / 2.0))  # 油门：[-1,1]→[0,1]
        return max(-1.0, min(1.0, v))

    def _aux_channel(self, b) -> float:
        """AUX 通道求值入口：none → 0.0；button 两档 ±1；axis3 三段（阈值 ±1/3）。
        inverted 对结果整体取反（±1 互换、中位 0 不动）。无按钮源 / index 无效或
        越界 → 0.0（防御，读不到的开关不乱跳）"""
        return -self._aux_raw(b) if b.inverted else self._aux_raw(b)

    def _aux_raw(self, b) -> float:
        """AUX 原始求值（未反向）"""
        if b.kind == "button":
            if self._get_button is None:
                return 0.0  # 无按钮源防御（旧构造）
            if b.index < 0 or (self._num_buttons is not None
                               and b.index >= self._num_buttons):
                return 0.0  # 未绑定 / 按钮越界按未绑定回中（换设备防御；
                            # num_buttons 未知不检查，与 num_axes 同款语义）
            return 1.0 if self._get_button(b.index) else -1.0
        if b.kind == "axis3":
            if b.index < 0:
                return 0.0  # 未绑定
            if self._num_axes is not None and b.index >= self._num_axes:
                return 0.0  # 轴越界按未绑定回中（换设备防御）
            v = self._get_axis(b.index)
            if v < -_AXIS3_THRESHOLD:
                return -1.0
            if v > _AXIS3_THRESHOLD:
                return 1.0
            return 0.0
        return 0.0  # "none" 及未识别 kind

    def read(self, dt: float) -> Channels:
        return Channels(
            throttle=self._channel("throttle", 0.5),
            yaw=self._channel("yaw", 0.0),
            roll=self._channel("roll", 0.0),
            pitch=self._channel("pitch", 0.0),
            aux=[self._aux_channel(self._mappings.get(f"aux{i}") or AuxBinding())
                 for i in range(1, 5)])
