"""键盘兜底输入。W/S 油门（保持式：按住每秒±100%，松开保持），A/D 偏航、↑↓ 俯仰、←→ 横滚（瞬态满杆）"""
from .channels import Channels, RCInput

# 键码与 pygame.K_* 数值一致（避免 input 层 import pygame）
K_UP, K_DOWN = 1073741906, 1073741905
K_LEFT, K_RIGHT = 1073741904, 1073741903
K_W, K_S, K_A, K_D = 119, 115, 97, 100

class KeyboardInput(RCInput):
    """键盘输入：油门为积分器，横滚/俯仰/偏航为方波"""
    THROTTLE_RATE = 1.0  # 每秒 100%

    def __init__(self):
        self._pressed = set()
        self._throttle = 0.0

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
        return Channels(self._throttle, float(yaw), float(roll), float(pitch))
