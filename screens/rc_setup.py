"""遥控通道设置：手动绑定 + 实时预览（向导在 Task 11 加入同一界面）"""
import imgui
import pygame
from .base import Screen
from config_store import AxisBinding

CHANNELS = ["throttle", "yaw", "roll", "pitch"]
CHANNEL_CN = {"throttle": "油门", "yaw": "偏航", "roll": "横滚", "pitch": "俯仰"}

def joystick_devices():
    """枚举当前手柄 [(index, name)]"""
    pygame.joystick.init()
    return [(i, pygame.joystick.Joystick(i).get_name())
            for i in range(pygame.joystick.get_count())]

def _preview_channels(app, dt: float = 0.0) -> tuple:
    """读当前 RCInput 四通道值（预览条与向导验证页共用）；dt 推进键盘油门积分器"""
    ch = app.rc_input.read(dt)
    return (ch.throttle, ch.yaw, ch.roll, ch.pitch)

def _bar(label, value, lo, hi):
    """通道预览条"""
    imgui.text(f"{label}")
    imgui.same_line(90)
    # pyimgui 2.0 运行期调整：progress_bar 第二参为 size 元组而非 (size_x, size_y)，
    # brief 原式 0.0,18 传参报 TypeError，按 brief 注用仅 fraction 形式
    imgui.progress_bar((value - lo) / (hi - lo))

class RCSetupScreen(Screen):
    def enter(self, app):
        self.app = app
        self._dev_index = 0
        self._msg = ""
        self._dt = 0.0   # 最近一帧时长（update 缓存，预览条用）

    def update(self, dt: float):
        self._dt = dt   # 主循环在 render_ui 之前调用，同帧预览条可拿到真实 dt

    def handle_event(self, event):
        if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
            self._back()

    def _back(self):
        from .main_menu import MainMenuScreen
        self.app.switch(MainMenuScreen())

    def render_ui(self):
        imgui.set_next_window_position(30, 30)
        imgui.set_next_window_size(560, 0)
        imgui.begin("遥控通道设置", flags=imgui.WINDOW_NO_COLLAPSE)

        mode = self.app.rc_config.mode
        imgui.text(f"当前模式: {'手柄' if mode == 'joystick' else '键盘（兜底）'}")
        if getattr(self.app, "joystick_lost", False):
            imgui.text("⚠ 手柄已断开，已自动切回键盘")

        imgui.separator()
        devs = joystick_devices()
        if not devs:
            imgui.text("未检测到手柄（插入后自动出现）")
            imgui.button("启动配置向导", 180, 40)  # 无手柄时不可用（Task 11 接线）
        else:
            labels = [f"{i}: {name}" for i, name in devs]
            _, self._dev_index = imgui.combo("设备", self._dev_index, labels,
                                             0 if len(labels) < 15 else len(labels))
            imgui.separator()
            imgui.text("手动绑定（轴 0 ~ N，-1 未绑定）")
            joy = pygame.joystick.Joystick(self._dev_index)
            joy.init()
            axis_count = joy.get_numaxes()
            for c in CHANNELS:
                b = self.app.rc_config.mappings[c]
                _, b.axis = imgui.slider_int(f"{CHANNEL_CN[c]} 轴",
                                             b.axis, -1, axis_count - 1)
                _, b.inverted = imgui.checkbox(f"{CHANNEL_CN[c]} 反向", b.inverted)
            if imgui.button("应用并保存", 180, 40):
                self.app.rc_config.mode = "joystick"
                self.app.rc_config.device_name = devs[self._dev_index][1]
                self.app.store.save("rc", self.app.rc_config)
                self.app.rc_input = self._make_joystick_input()
                self._msg = "已应用手柄绑定"
            imgui.same_line()
            imgui.button("启动配置向导", 180, 40)   # Task 11 接线

        imgui.separator()
        imgui.text("通道实时预览")
        t, y, r, p = _preview_channels(self.app, self._dt)
        _bar("油门", t, 0, 1)
        _bar("偏航", y, -1, 1)
        _bar("横滚", r, -1, 1)
        _bar("俯仰", p, -1, 1)
        if self._msg:
            imgui.text(self._msg)
        if imgui.button("返回主菜单", 180, 40):
            self._back()
        imgui.end()

    def _make_joystick_input(self):
        from input.joystick import JoystickInput
        joy = pygame.joystick.Joystick(self._dev_index)
        joy.init()
        return JoystickInput(joy.get_axis, self.app.rc_config.mappings)
