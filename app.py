"""App：pygame 主循环 + Screen 状态机 + AppContext（设计文档第 3 节）"""
import os
import pygame
import imgui
from imgui.integrations.pygame import PygameRenderer
import soup3D

from config_store import (ConfigStore, Settings, DroneParams, RCConfig,
                          default_drone_params, default_rc_config)
from scene_loader import list_scenes
from input.keyboard import KeyboardInput
from input.joystick import JoystickInput

RESOLUTIONS = [(1280, 720), (1600, 900), (1920, 1080), (2560, 1440)]  # 设置页候选

# 中文字体候选（spike 验证过的顺序）
FONT_PATHS = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Medium.ttc",
    "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
]

def load_cjk_font():
    """加载中文字体（24px，含常用汉字区），全部失败返回 None（UI 回落英文）"""
    io = imgui.get_io()
    for path in FONT_PATHS:
        if os.path.exists(path):
            try:
                return io.fonts.add_font_from_file_ttf(
                    path, 24,
                    glyph_ranges=io.fonts.get_glyph_ranges_chinese_full())
            except Exception:
                continue
    return None

class App:
    """全局唯一应用对象：窗口、配置、输入、场景列表与当前 Screen"""
    def __init__(self):
        self.store = ConfigStore("config")
        self.settings = self.store.load("settings", Settings)
        self.drone_params = self.store.load("drone", DroneParams)
        self.rc_config = self.store.load("rc", RCConfig)
        self.scenes = list_scenes("scene")
        self.restart_needed = False   # 热重建失败的降级标记
        self.screen = None
        self._frames_left = None

        pygame.init()
        self._open_window()
        imgui.create_context()
        self._impl = PygameRenderer()
        # spike 结论：pygame 集成不初始化 display_size，new_frame 会断言崩溃
        imgui.get_io().display_size = pygame.display.get_surface().get_size()
        self.font = load_cjk_font()
        # spike 结论：自定义字体加载后必须重烘焙字体图集，否则中文花字
        if self.font:
            self._impl.refresh_font_texture()
        self.clock = pygame.time.Clock()
        self.rc_input = self._make_rc_input()
        pygame.joystick.init()  # 手柄热插拔轮询用

    # ---- 窗口 ----
    def _open_window(self):
        flags = pygame.DOUBLEBUF | pygame.OPENGL
        if self.settings.fullscreen:
            flags |= pygame.FULLSCREEN
        w, h = self.settings.resolution
        pygame.display.set_caption("FlamingSkyFPV")
        pygame.display.set_mode((w, h), flags)
        soup3D.init(w, h, 75, (0.55, 0.75, 0.95), 0.1, 2048)

    def rebuild_window(self):
        """设置变更后的热重建：重开窗口 + soup3D 重初始化 + 字体/界面资源重载。
        GL 上下文重建失败时置 restart_needed=True（UI 提示重启生效）——Task 8 接线。"""
        try:
            # OPENGL 窗口 set_mode 同 flag 不改尺寸（pygame 2.6.1 实测）：
            # 先关窗销毁旧 GL 上下文，再按新尺寸/模式重开（__init__ 首开无需此步）
            pygame.display.set_mode((0, 0))
            self._open_window()
            self.font = load_cjk_font()
            # 与 __init__ 对称：新字体加载后必须重烘焙图集，否则下一帧 imgui 断言崩溃
            if self.font:
                self._impl.refresh_font_texture()
            if self.screen:
                self.screen.reload()
            self.restart_needed = False
        except Exception:
            self.restart_needed = True

    # ---- 输入 ----
    def _make_rc_input(self):
        if self.rc_config.mode == "joystick" and self.rc_config.device_name:
            for i in range(pygame.joystick.get_count()):
                j = pygame.joystick.Joystick(i)
                if j.get_name() == self.rc_config.device_name:
                    j.init()
                    return JoystickInput(j.get_axis, self.rc_config.mappings)
        return KeyboardInput()  # 无手柄/未配置 → 键盘兜底（UI 明示）

    def poll_joysticks(self):
        """手柄插拔检测：配置为手柄但设备消失 → 回落键盘（设计文档第 8 节）"""
        if isinstance(self.rc_input, JoystickInput):
            if pygame.joystick.get_count() == 0:
                self.rc_input = KeyboardInput()
                self.joystick_lost = True
        self.joystick_lost = getattr(self, "joystick_lost", False)

    # ---- 状态机 ----
    def switch(self, screen):
        if self.screen:
            self.screen.exit()
        self.screen = screen
        screen.enter(self)

    # ---- 主循环 ----
    def run(self, max_frames=None):
        from screens.main_menu import MainMenuScreen
        self.switch(MainMenuScreen())
        self._frames_left = max_frames
        while True:
            dt = self.clock.tick(60) / 1000.0
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    return
                if event.type == pygame.VIDEORESIZE:
                    # 不透传 pyimgui 集成层：其 VIDEORESIZE 分支会再 set_mode，
                    # 全屏切换的中间态尺寸可越界致 OverflowError；窗口尺寸与
                    # 字体图集已由 rebuild_window 自管，这里只同步 display_size
                    if event.w > 0 and event.h > 0:
                        imgui.get_io().display_size = (event.w, event.h)
                    continue
                self._impl.process_event(event)
                self.screen.handle_event(event)

            # 键盘输入喂给 RC 层（只传按下集合，避免 input 层依赖 pygame）
            pressed = {i for i, on in enumerate(pygame.key.get_pressed()) if on}
            self.rc_input.set_pressed(pressed)
            self.poll_joysticks()

            self.screen.update(dt)

            soup3D.update()               # 先渲 3D（spike 验证的顺序）
            self.screen.render_scene()    # 3D 直渲钩子（显示列表失效，见 base.py）
            imgui.new_frame()
            if self.font:
                imgui.push_font(self.font)
            self.screen.render_ui()
            if self.font:
                imgui.pop_font()
            imgui.render()                # 再画 UI
            # pyimgui 2.0：render() 只终结帧并生成绘制数据，集成层才真正上屏
            self._impl.render(imgui.get_draw_data())

            pygame.display.flip()

            if self._frames_left is not None:
                self._frames_left -= 1
                if self._frames_left <= 0:
                    return

    def save_all(self):
        self.store.save("settings", self.settings)
        self.store.save("drone", self.drone_params)
        self.store.save("rc", self.rc_config)
