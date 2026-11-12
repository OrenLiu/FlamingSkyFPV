"""键盘兜底输入。W/S 油门（保持式：按住每秒±100%，松开保持），A/D 偏航、↑↓ 俯仰、←→ 横滚（瞬态满杆）；F1/F2/F4 拨动 AUX1/2/3"""
from .channels import Channels, RCInput

# 键码与 pygame.K_* 数值一致（避免 input 层 import pygame）
K_UP, K_DOWN = 1073741906, 1073741905
K_LEFT, K_RIGHT = 1073741904, 1073741903
K_W, K_S, K_A, K_D = 119, 115, 97, 100
K_F1, K_F2, K_F4 = 1073741882, 1073741883, 1073741885  # AUX1/2/3 拨动键（AUX3 用 F4 避开 app.py 的 F3 调试热键）

class KeyboardInput(RCInput):
    """键盘输入：油门为积分器，横滚/俯仰/偏航为方波，AUX 为拨动开关"""
    THROTTLE_RATE = 1.0  # 每秒 100%
    _AUX_TOGGLES = {K_F1: 0, K_F2: 1, K_F4: 2}  # 拨动键 → aux 下标（键盘无 AUX4 键位，aux[3] 恒中位）

    def __init__(self):
        self._pressed = set()
        self._throttle = 0.0
        self._aux = [0.0] * 4
        self._prev_pressed: set = set()  # 上一帧按键集合，用于上升沿检测

    def set_pressed(self, pressed: set):
        self._pressed = pressed

    def read(self, dt: float) -> Channels:
        # 油门：保持式积分
        if K_W in self._pressed: self._throttle += self.THROTTLE_RATE * dt
        if K_S in self._pressed: self._throttle -= self.THROTTLE_RATE * dt
        self._throttle = max(0.0, min(1.0, self._throttle))
        # 瞬态通道：按下满杆、松开回中
        yaw = (K_D in self._pressed) - (K_A in self._pressed)     # D 右偏航为正
        pitch = (K_UP in self._pressed) - (K_DOWN in self._pressed)  # ↑ 拉杆抬头为正
        roll = (K_RIGHT in self._pressed) - (K_LEFT in self._pressed)  # → 右横滚为正
        # AUX：上升沿 toggle，±1 两档（首拨 +1.0，之后在 ±1 间切换，未按键保持）
        for key, idx in self._AUX_TOGGLES.items():
            if key in self._pressed and key not in self._prev_pressed:
                self._aux[idx] = -1.0 if self._aux[idx] == 1.0 else 1.0
        self._prev_pressed = set(self._pressed)
        return Channels(self._throttle, float(yaw), float(roll), float(pitch), list(self._aux))
