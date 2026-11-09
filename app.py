"""App：pygame 主循环 + Screen 状态机 + AppContext（设计文档第 3 节）"""
import os
import time
import pygame
import imgui
from imgui.integrations.pygame import PygameRenderer
from OpenGL.GL import glActiveTexture, GL_TEXTURE0
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
        self._pending_rebuild = False  # 延后到帧边界执行的重建标记（见 run()）
        self.debug_overlay = False    # F3 调试覆盖层开关（FPS + 相机坐标）
        # 自测窗口平均帧率：io.framerate = 1/io.delta_time，但主循环只调
        # process_event 从不调集成层的 process_inputs() → delta_time 恒为
        # 初始 0.01667 → framerate 恒 60 不可用（统计见 run() 的窗口计数）
        self._fps_avg = 0.0        # 窗口平均帧率（0.5s 窗口；io.framerate 因 delta_time 无人维护恒 60，不可用）
        self._fps_frames = 0       # 当前窗口帧计数
        self._fps_window_start = 0.0
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
        # 垂直同步：两层生效路径——设置保存 → rebuild_window → _open_window
        # 重跑 → 交换间隔随新 GL 上下文重设（vsync 热生效，重建失败回落
        # restart_needed 重启生效）；tick 数字档变更由 run() 每帧读 settings
        # 即时生效。soup3D 引擎默认不限速，零改动
        self._set_swap_interval(1 if self.settings.fps_mode == "vsync" else 0)

    @staticmethod
    def _set_swap_interval(interval: int) -> bool:
        """当前 GL 上下文设交换间隔（垂直同步，set_mode 建上下文后调用）。
        实测注记：pygame 2.6.1 的 gl_set_attribute(GL_SWAP_CONTROL) 常量值为 0，
        实际撞 SDL_GL_RED_SIZE（SDL2 已移除该属性）→ 静默无效；pygame 静态链接
        SDL，ctypes 直调系统 libSDL2 的 SDL_GL_SetSwapInterval 也只打到另一实例
        （报 Video subsystem has not been initialized）。改走 GLX 扩展：作用于
        进程当前上下文（pygame 所建），MESA 扩展优先、SGI 回退（覆盖 Mesa/
        Intel/AMD/NVIDIA）；无 libGL/glX 符号（非 X11 平台）时静默跳过，
        vsync 档回落无限制行为。返回是否成功。"""
        try:
            import ctypes
            gl = ctypes.CDLL("libGL.so.1")
            for fname in ("glXSwapIntervalMESA", "glXSwapIntervalSGI"):
                fn = getattr(gl, fname, None)
                if fn is not None and fn(interval) == 0:
                    return True
        except OSError:
            pass
        return False

    def rebuild_window(self):
        """设置变更后的热重建：重开窗口 + soup3D 重初始化 + 字体/界面资源重载。
        GL 上下文重建失败时置 restart_needed=True（UI 提示重启生效）——Task 8 接线。"""
        try:
            # OPENGL 窗口 set_mode 同 flag 不改尺寸（pygame 2.6.1 实测）：
            # 先关窗销毁旧 GL 上下文，再按新尺寸/模式重开（__init__ 首开无需此步）
            pygame.display.set_mode((0, 0))
            self._open_window()
            # 字体重载失败不置 None：旧字体对象结构完好（纹理最多次过期待下次
            # 重建），置 None 反而令主循环 push/pop 失衡 → render 断言崩溃；
            # 失败路径提示重启生效
            new_font = load_cjk_font()
            if new_font:
                self.font = new_font
                # 与 __init__ 对称：新字体加载后必须重烘焙图集，否则下一帧 imgui 断言崩溃
                self._impl.refresh_font_texture()
            if self.screen:
                self.screen.reload()
            self.restart_needed = new_font is None  # 字体重载失败 → 提示重启生效
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
            # 帧率控制：垂直同步/无限制不限速（vsync 由交换间隔阻塞在 GPU 侧限帧），
            # 数字模式时钟限速（每帧读取 settings，设置页改动即时生效）
            dt = self.clock.tick(0 if self.settings.fps_mode in ("vsync", "unlimited")
                                 else int(self.settings.fps_mode)) / 1000.0
            # 帧率统计：0.5s 真实时间窗口计数（perf_counter 浮点精度，不受
            # clock.tick 整数毫秒量化影响——高帧率下 tick 可返回 0ms，
            # 倒数成百万 fps 毛刺污染 EMA）
            self._fps_frames += 1
            now = time.perf_counter()
            if self._fps_window_start == 0.0:
                self._fps_window_start = now
            elapsed = now - self._fps_window_start
            if elapsed >= 0.5:
                self._fps_avg = self._fps_frames / elapsed
                self._fps_frames = 0
                self._fps_window_start = now
            if self._pending_rebuild:
                # 帧边界执行重建：imgui 帧中禁止字体图集操作（add_font 在帧中
                # 失败被吞 → font 置 None → push/pop 失衡 → render 断言崩溃，
                # 用户实飞「保存并应用」触发过；故 UI 处理器只置标记，此处执行）
                self._pending_rebuild = False
                self.rebuild_window()
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    return
                if event.type == pygame.KEYDOWN and event.key == pygame.K_F3:
                    self.debug_overlay = not self.debug_overlay  # 全局热键，任意界面可用
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
            # push 前捕获：render_ui 处理器可能触发重建改动 self.font（帧中禁字体
            # 图集操作），pop 条件若再读 self.font 会与 push 时不一致 → 栈失衡
            pushed = self.font is not None
            if pushed:
                imgui.push_font(self.font)
            self.screen.render_ui()
            self._render_debug_overlay()  # 仍在 push_font 作用域内，中文标签可显示
            if pushed:
                imgui.pop_font()
            imgui.render()                # 再画 UI
            # pyimgui 2.0：render() 只终结帧并生成绘制数据，集成层才真正上屏
            # 兜底：引擎 3D 绘制若遗留活动纹理单元非 0（soup3D rend_cached 已在
            # 引擎侧归位，此处防御其他泄漏路径），pyimgui 的字体绑定会落在错误
            # 单元上导致 UI 整体隐形（飞行界面 UI 消失之谜的根因）
            glActiveTexture(GL_TEXTURE0)
            self._impl.render(imgui.get_draw_data())

            pygame.display.flip()

            if self._frames_left is not None:
                self._frames_left -= 1
                if self._frames_left <= 0:
                    return

    def _render_debug_overlay(self):
        """F3 调试覆盖层：右上角无装饰小窗，显示 FPS 与相机坐标（渲染系，米）"""
        if not self.debug_overlay:
            return
        io = imgui.get_io()
        w, h = io.display_size
        imgui.set_next_window_position(w - 240, 8)  # 固定右上角（宽 240 预留）
        flags = (imgui.WINDOW_NO_TITLE_BAR | imgui.WINDOW_ALWAYS_AUTO_RESIZE |
                 imgui.WINDOW_NO_MOVE)
        imgui.push_style_color(imgui.COLOR_WINDOW_BACKGROUND, 0, 0, 0, 0.5)  # 半透明底
        imgui.begin("##debug_overlay", flags=flags)
        imgui.text(f"FPS: {self._fps_avg:.0f}")  # 0.5s 窗口平均（io.framerate 恒 60 不可用）
        imgui.text(f"相机: {soup3D.camera.X:.2f}, {soup3D.camera.Y:.2f}, {soup3D.camera.Z:.2f}")
        imgui.end()
        imgui.pop_style_color(1)

    def save_all(self):
        self.store.save("settings", self.settings)
        self.store.save("drone", self.drone_params)
        self.store.save("rc", self.rc_config)
