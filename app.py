"""App：pygame 主循环 + Screen 状态机 + AppContext（设计文档第 3 节）"""
import os
import time
import traceback
import pygame
import imgui
from imgui.integrations.pygame import PygameRenderer
from OpenGL.GL import glActiveTexture, GL_TEXTURE0
import soup3D

from config_store import (CONFIG_DIR, DRONES_DIR, ConfigStore, Settings,
                          DroneParams, RCConfig, DEFAULT_PRESET_ID,
                          list_preset_ids, load_preset, save_preset,
                          migrate_legacy_drone)
from scene_loader import list_scenes
from shaderpacks import runtime
from shaderpacks.loader import load_pack
from shaderpacks.registry import list_packs
from input.keyboard import KeyboardInput
from input.joystick import JoystickInput
from msp.handlers import make_identity_handlers
from msp.server import VfcServer

RESOLUTIONS = [(1280, 720), (1600, 900), (1920, 1080), (2560, 1440)]  # 设置页候选
# 配置根/预设目录常量在 config_store 定义（app 与 screens 同源引用，防漂移）

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
        self.store = ConfigStore(CONFIG_DIR)
        self.settings = self.store.load("settings", Settings)
        # 二期多预设接线（App 生命周期三步）：一期 drone.json 存量档先迁移为
        # 默认机预设；预设目录空 → 自动建默认机；current_drone_id 失效（旧档
        # "default"/预设被删）→ 回落列表第一台（下次 save_all 落盘）
        migrate_legacy_drone(CONFIG_DIR)
        ids = list_preset_ids(DRONES_DIR)
        if not ids:
            save_preset(DRONES_DIR, DEFAULT_PRESET_ID, DroneParams())
            ids = [DEFAULT_PRESET_ID]
        if self.settings.current_drone_id not in ids:
            self.settings.current_drone_id = ids[0]
        self.drone_params = load_preset(DRONES_DIR, self.settings.current_drone_id)
        self.rc_config = self.store.load("rc", RCConfig)
        self.scenes = list_scenes("scene")
        self.restart_needed = False   # 热重建失败的降级标记
        self._pending_rebuild = False  # 延后到帧边界执行的重建标记（见 run()）
        # 着色器预设（shaderpacks）：包列表与当前生效预设（spec 5.7）
        self.packs = list_packs()            # 预设包列表（reload_shader_preset 每次重扫）
        self.preset = None                   # LoadedPack；reload 后保证非 None（内置损坏除外）
        self._frame_renderer = None          # 预设帧渲染器；None = 默认管线直渲
        self.preset_fallback = False         # 预设回落提示标记（设置页显示）
        self._pending_preset_reload = False  # 帧边界执行的预设重载标记（同 _pending_rebuild）
        self.debug_overlay = False    # F3 调试覆盖层开关（FPS + 相机坐标）
        # 自测窗口平均帧率：0.5s 真实时间窗口计数（perf_counter 浮点精度）。
        # io.framerate（=1/io.delta_time 平滑值）自 run() 按实测帧间隔喂
        # delta_time 起已可用，但窗口计数不受 clock.tick 毫秒量化影响、更稳，
        # 故保留（run() 内注释见 delta_time 喂入机制）
        self._fps_avg = 0.0        # 窗口平均帧率（0.5s 窗口计数）
        self._fps_frames = 0       # 当前窗口帧计数
        self._fps_window_start = 0.0
        self.screen = None
        self._frames_left = None

        pygame.init()
        self._open_window()
        # 着色器预设：GL 上下文就绪后加载一次（帧渲染器的 RT/程序在此创建）；
        # _open_window 自身不调（避免与 rebuild 路径双重调用，spec 5.7）
        self.reload_shader_preset()
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
        # MSP 虚拟飞控服务器（Task 16）：参数页「接入地面站」启动；未启动时
        # drain() 内部直返（run() 每帧调用无开销）
        self.msp_server = VfcServer(make_identity_handlers())

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
            # 新 GL 上下文：预设 RT/程序属旧上下文必须重建（失败由回落链兜底）；
            # 置于 screen.reload() 之前，保证场景以新包表面类重载（spec 5.7）
            self.reload_shader_preset()
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
                    # 二期 AUX：按钮源 + 轴数/按钮数传入（越界防御换设备回中）
                    return JoystickInput(j.get_axis, self.rc_config.mappings,
                                         get_button_fn=j.get_button,
                                         num_axes=j.get_numaxes(),
                                         num_buttons=j.get_numbuttons())
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
                self._pending_preset_reload = False  # rebuild 尾部已 reload，避免二次执行
                self.rebuild_window()
            elif self._pending_preset_reload:
                # 帧边界执行预设重载：GL 资源创建禁止在 imgui 帧中进行（同 _pending_rebuild）
                self._pending_preset_reload = False
                self.reload_shader_preset()
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

            # MSP 请求分派（Task 16）：accept 新连接 + 取空队列应答；
            # 服务器未启动直返，不阻塞主循环
            self.msp_server.drain()

            self.screen.update(dt)

            if self._frame_renderer is not None:
                fr = self._frame_renderer
                try:
                    soup3D.update(render=lambda: runtime.pack_frame(fr, self.screen.render_scene))
                except Exception:
                    # 运行期兜底（spec 5.7/6）：预设帧渲染抛异常 → 当帧起回落默认
                    # 管线（当帧画面残缺可接受），不中断游戏。引擎 update(render=...)
                    # 的 finally 已恢复 GL 状态，异常后状态干净
                    traceback.print_exc()
                    self._dispose_frame_renderer()
                    self.preset_fallback = True
                    soup3D.update()
                    self.screen.render_scene()
            else:
                soup3D.update()               # 先渲 3D（spike 验证的顺序）
                self.screen.render_scene()    # 3D 直渲钩子（显示列表失效，见 base.py）
            # imgui 内部时间每帧 io.Time += io.DeltaTime（光标闪烁、退格/方向键
            # 重复、双击判定全吃这个时间）。集成层 process_inputs() 从不被调
            # （主循环只走 process_event 事件流），delta_time 恒为初始 1/60 →
            # 实际帧率 F 时 UI 时间以 F/60 倍速快进：unlimited 实测 ~900fps 即
            # ~15 倍速，光标高频闪烁出残影、文本编辑按键重复飞快。按实测帧间隔
            # 喂 imgui；clock.tick 整数毫秒在高帧率下可量化为 0ms，下限 1ms
            # 防 UI 时间冻结
            imgui.get_io().delta_time = dt if dt > 0.0 else 0.001
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

    # ---- 着色器预设（shaderpacks）----
    def _dispose_frame_renderer(self):
        """释放当前帧渲染器（dispose 契约可选；释放失败打印不阻断回落链）"""
        if self._frame_renderer is None:
            return
        dispose = getattr(self._frame_renderer, "dispose", None)
        if callable(dispose):
            try:
                dispose()
            except Exception:
                traceback.print_exc()
        self._frame_renderer = None

    def _find_pack_info(self, pack_id: str):
        """在 self.packs 里找 id 匹配且 valid 的包（无则 None）"""
        return next((p for p in self.packs if p.id == pack_id and p.valid), None)

    def reload_shader_preset(self):
        """按 settings.shader_preset 重载预设（spec 5.7 回落链）。

        旧渲染器释放 → 重扫包列表 → 找 valid 包 → load_pack → 实例化帧渲染器。
        代码级失败（import/契约/实例化，含目标 id 不存在时 load_pack(None) 的
        AttributeError）整体回落 builtin_performance；连性能优先都失败（内置包
        损坏）→ 空预设走默认管线。applied id 与存档不一致时置 preset_fallback
        （设置页显示回落提示）。
        """
        self._dispose_frame_renderer()
        self.preset_fallback = False
        try:
            self.packs = list_packs()
            info = self._find_pack_info(self.settings.shader_preset)
            try:
                loaded = load_pack(info)
                renderer = None
                if loaded.create_frame_renderer is not None:
                    renderer = loaded.create_frame_renderer(soup3D.proj_width,
                                                            soup3D.proj_height)
            except Exception:
                # 代码级失败：整体回落性能优先
                traceback.print_exc()
                info = self._find_pack_info("builtin_performance")
                loaded = load_pack(info)
                renderer = None
                if loaded.create_frame_renderer is not None:
                    renderer = loaded.create_frame_renderer(soup3D.proj_width,
                                                            soup3D.proj_height)
            self.preset = loaded
            self._frame_renderer = renderer
            self.preset_fallback = loaded.info.id != self.settings.shader_preset
        except Exception:
            # 兜底：连性能优先包都加载失败（内置包损坏的异常情形）→ 空预设走默认
            # 管线；flight 注入点对 preset=None 会 AttributeError → 场景加载失败进
            # 错误弹窗，游戏本体仍可用（spec 6 错误表的最终降级）
            traceback.print_exc()
            self.preset = None
            self._frame_renderer = None
            self.preset_fallback = True

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

    # ---- 多预设（二期：config/drones/<代号>.json，Task 5 UI 消费） ----
    def select_drone(self, preset_id: str) -> None:
        """切换当前机：加载预设为新 DroneParams 对象（参数页编辑对象随之换） +
        current_drone_id 记忆即时落盘（仅 settings，整机参数由 save_all/save_preset）"""
        self.drone_params = load_preset(DRONES_DIR, preset_id)
        self.settings.current_drone_id = preset_id
        self.store.save("settings", self.settings)

    def save_all(self):
        """退出/手动保存：settings + rc + 当前机预设（一期 store.save("drone")
        路径至此退役；参数页保存按钮的旧路径 Task 5 移除）"""
        self.store.save("settings", self.settings)
        save_preset(DRONES_DIR, self.settings.current_drone_id, self.drone_params)
        self.store.save("rc", self.rc_config)
