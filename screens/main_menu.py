"""主界面：标题居中 + 左侧按钮列（设计文档第 6 节）"""
import imgui
import pygame
from .base import Screen

class MainMenuScreen(Screen):
    def enter(self, app):
        self.app = app

    def handle_event(self, event):
        if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
            pygame.event.post(pygame.event.Event(pygame.QUIT))

    def render_ui(self):
        io = imgui.get_io()
        w, h = io.display_size
        imgui.set_next_window_position(0, 0)
        imgui.set_next_window_size(w, h)
        flags = (imgui.WINDOW_NO_TITLE_BAR | imgui.WINDOW_NO_RESIZE |
                 imgui.WINDOW_NO_MOVE | imgui.WINDOW_NO_BRING_TO_FRONT_ON_FOCUS |
                 imgui.WINDOW_NO_BACKGROUND)
        imgui.begin("##main", flags=flags)

        imgui.set_cursor_pos((w / 2 - 120, h * 0.18))
        imgui.text("FlamingSkyFPV")   # 标题居中

        imgui.set_cursor_pos((w * 0.08, h * 0.42))  # 选项居左
        imgui.begin_group()
        # [Task 12] 飞行界面实现后解开此块
        # if imgui.button("开始飞行", 220, 50):
        #     from .flight import FlightScreen   # 延迟导入避免循环依赖
        #     self.app.switch(FlightScreen())
        if imgui.button("设置", 220, 50):
            from .settings import SettingsScreen
            self.app.switch(SettingsScreen())
        if imgui.button("无人机参数", 220, 50):
            from .drone_params import DroneParamsScreen
            self.app.switch(DroneParamsScreen())
        if imgui.button("遥控通道设置", 220, 50):
            from .rc_setup import RCSetupScreen
            self.app.switch(RCSetupScreen())
        if imgui.button("退出", 220, 50):
            pygame.event.post(pygame.event.Event(pygame.QUIT))
        imgui.end_group()

        imgui.end()
