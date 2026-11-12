"""遥控通道抽象：RCInput 协议与 Channels 数据类（设计文档第 5 节）"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

@dataclass
class Channels:
    """四通道输出。throttle∈[0,1]，yaw/roll/pitch∈[-1,1]（杆义：yaw右+ roll右+ pitch拉杆+）
    aux：4 路 AUX 开关通道 ∈[-1,1]，0=中位（二期模式开关，长度恒 4）"""
    throttle: float = 0.0
    yaw: float = 0.0
    roll: float = 0.0
    pitch: float = 0.0
    aux: list = field(default_factory=lambda: [0.0] * 4)

class RCInput(ABC):
    """遥控输入抽象：飞行代码不感知数据来源"""
    @abstractmethod
    def read(self, dt: float) -> Channels:
        """返回当前四通道值；dt 为距上次 read 的秒数"""
    def set_pressed(self, pressed: set):
        """接收当前按下的按键集合（仅键盘实现需要，默认空操作）"""
