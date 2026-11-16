"""App：pygame 主循环 + Screen 状态机 + AppContext（设计文档第 3 节）"""
import os
import sys
import time
import traceback
import pygame
import imgui
from imgui.integrations.pygame import PygameRenderer
from OpenGL.GL import (glActiveTexture, glClear, glClearColor, glViewport,
                       glGetFloatv, glDeleteTextures,
                       GL_COLOR_BUFFER_BIT, GL_COLOR_CLEAR_VALUE,
                       GL_TEXTURE0)
from soup3D.target import RenderTarget
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
from screens.flight import FlightScreen   # _msp_state 的 isinstance 判定（app → screens 单向合法）
from debug_frame_log import FrameLogger, frame_log_enabled  # 卡顿记录仪（FLAME_STUTTER_LOG 门控）
import ui_scale   # UI 等比缩放（布局像素换算 u()、字号档 font_px、样式表缩放）

RESOLUTIONS = [(1280, 720), (1600, 900), (1920, 1080), (2560, 1440)]  # 设置页候选（全屏/固定窗口的逻辑分辨率；窗口化跟随窗口）
# 配置根/预设目录常量在 config_store 定义（app 与 screens 同源引用，防漂移）

# 手柄自动拾取退避间隔（秒）：构造失败（init 异常/设备名未出现）后的重试
# 最小间隔，防插拔抖动下每帧重建 Joystick 对象的死循环（Task 38）
JOYSTICK_PICKUP_RETRY_S = 2.0

# 中文字体候选（跨平台）：运行时按平台合成（_font_candidates），数据根
# fonts/ 目录的用户自置字体优先，其后平台系统字体——打包独立运行时
# Linux 硬编码路径不存在，Windows/macOS 走各自预装 CJK 字体
_FONT_PATHS_LINUX = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Medium.ttc",
    "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
]
_FONT_PATHS_WINDOWS = [
    r"C:\Windows\Fonts\msyh.ttc",     # 微软雅黑（Win10/11 预装）
    r"C:\Windows\Fonts\simhei.ttf",   # 黑体
    r"C:\Windows\Fonts\simsun.ttc",   # 宋体
]
_FONT_PATHS_MACOS = [
    "/System/Library/Fonts/PingFang.ttc",          # 苹方
    "/System/Library/Fonts/Hiragino Sans GB.ttc",  # 冬青黑体
    "/System/Library/Fonts/STHeiti Light.ttc",     # 黑体-简
]

def _font_candidates() -> list:
    """合成字体候选：数据根 fonts/ 目录（*.ttf/*.ttc，最高优先）→ 平台系统
    字体。全部缺失返回空（UI 回落英文）。"""
    import glob
    cands = sorted(glob.glob(os.path.join("fonts", "*.ttf"))
                   + glob.glob(os.path.join("fonts", "*.ttc")))
    if sys.platform == "win32":
        cands += _FONT_PATHS_WINDOWS
    elif sys.platform == "darwin":
        cands += _FONT_PATHS_MACOS
    else:
        cands += _FONT_PATHS_LINUX
    return cands

def load_cjk_font(size_px: int = 24):
    """加载中文字体（size_px 像素，含常用汉字区），全部失败返回 None（UI 回落英文）。
    size_px 由窗口高度经 ui_scale.font_px 档位量化（720p→24、1080p→36、1440p→48）"""
    io = imgui.get_io()
    # 图集宽度钉死 8192（desired 是上限，实际打包常更小）：宽度自动选择在
    # GL 上下文环境下会被翻倍（36px 档实测 32768 > GPU 纹理上限 → 上传失
    # 败字符消失），显式 desired 后宽度恒 ≤8192（GPU 普适）
    io.fonts.texture_desired_width = 8192
    for path in _font_candidates():
        if os.path.exists(path):
            try:
                return io.fonts.add_font_from_file_ttf(
                    path, size_px,
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
        self._imgui_ctx = imgui.create_context()
        self._impl = PygameRenderer()
        # spike 结论：pygame 集成不初始化 display_size，new_frame 会断言崩溃
        # 窗口尺寸权威值：GL RESIZABLE 窗口的 pygame surface 尺寸不随真实拖拽
        # 更新（恒为 set_mode 初始值），display_size 与字号档一律以本值为准，
        # 由 VIDEORESIZE 事件实时维护（run() 事件分支）
        self._display_size = pygame.display.get_surface().get_size()
        imgui.get_io().display_size = self._display_size
        # UI 等比缩放：字号档按当前窗口高量化（720p→24px…，窗口化 resize 随
        # 档去抖重建）；样式基准快照捕获一次，run() 每帧按缩放重设
        self._pending_font_px = None    # 窗口化 resize 的待重建字号档（帧边界去抖执行）
        self._last_window_resize = 0.0  # 最近 VIDEORESIZE 时刻（monotonic 秒，去抖基准）
        self._capture_base_style()
        self._reload_font(self._current_font_px())
        # spike 结论：自定义字体加载后必须重烘焙字体图集，否则中文花字
        if self.font:
            self._impl.refresh_font_texture()
        self.clock = pygame.time.Clock()
        # 卡顿诊断记录仪（环境变量 FLAME_STUTTER_LOG 门控）：未设置时为 None，
        # run() 各打点全 if 守卫，行为与无记录仪时一致
        self._flog = FrameLogger(self) if frame_log_enabled else None
        self.rc_input = self._make_rc_input()
        # 手柄热插拔状态（poll_joysticks 双向维护）：lost=配置手柄消失已回落
        # 键盘（rc_setup 明示）；recovered=消失后自动拾取成功（对称提示）
        self.joystick_lost = False
        self.joystick_recovered = False
        self._joystick_pickup_after = 0.0  # 自动拾取退避到期时刻（monotonic 秒）
        pygame.joystick.init()  # 手柄热插拔轮询用
        # MSP 虚拟飞控服务器（Task 16/19）：参数页「接入地面站」启动；未启动时
        # drain() 内部直返（run() 每帧调用无开销）。state_provider 每请求供
        # 参数/飞行遥测快照（GET 组读运行时状态），on_change 是 SET 写信号
        # （置脏标志 → 飞行帧首重建飞控参数，即时生效）
        self.msp_params_dirty = False   # MSP SET 写参数脏标志（FlightScreen 消费）
        self.msp_server = VfcServer(make_identity_handlers(),
                                    state_provider=self._msp_state,
                                    on_change=self._msp_on_change)
        # 地面站常驻浮窗状态（Task 43）：提示框上移 App 层跨 Screen 显示
        self._gs_window_spawned = False  # 首帧定位标记（此后位置交 imgui 记忆）
        self._gs_copy_fail = None        # 复制失败的地址行下标（None=无失败提示）
        self._gs_error = None            # 浮窗内断开失败错误（None=无）

    # ---- 窗口 ----
    def _open_window(self):
        flags = pygame.DOUBLEBUF | pygame.OPENGL
        # 全屏缩放模式（不切显示模式保原生刷新率）：全屏开在桌面原生分辨率
        # （请求尺寸==当前模式 → RandR 不做模式切换），引擎仍按设置的逻辑
        # 分辨率渲染，上屏时 GPU 等比缩放居中（宽高比不同留黑边）。
        # _scale_rt=None 表示不用缩放（窗口化，或全屏尺寸恰等逻辑分辨率）
        self._scale_rt = None
        self._scale_rect = None
        w, h = self.settings.resolution
        pygame.display.set_caption("FlamingSkyFPV")
        if self.settings.display_mode == "fullscreen":
            flags |= pygame.FULLSCREEN
            dw, dh = pygame.display.get_desktop_sizes()[0]
            pygame.display.set_mode((dw, dh), flags)
            soup3D.init(w, h, 75, (0.55, 0.75, 0.95), 0.1, 2048)
            if (dw, dh) != (w, h):
                # 缩放离屏目标：引擎按逻辑分辨率渲进 RT，上屏 present 等比放大
                self._scale_rt = RenderTarget(w, h, "rgba8", "renderbuffer")
                scale = min(dw / w, dh / h)   # 等比取较小比例（16:9 进 16:10 高度受限）
                sw, sh = int(w * scale), int(h * scale)
                self._scale_rect = ((dw - sw) // 2, (dh - sh) // 2, sw, sh)  # 居中留黑边
        elif self.settings.display_mode == "window":
            # 窗口化: RESIZABLE, 尺寸=记忆的窗口大小, resize 事件驱动引擎跟随
            flags |= pygame.RESIZABLE
            w, h = self.settings.window_size
            pygame.display.set_mode((w, h), flags)
            soup3D.init(w, h, 75, (0.55, 0.75, 0.95), 0.1, 2048)
        else:
            # 固定大小窗口化: 不可拖拽, 尺寸=设置的逻辑分辨率
            pygame.display.set_mode((w, h), flags)
            soup3D.init(w, h, 75, (0.55, 0.75, 0.95), 0.1, 2048)

        # 垂直同步：两层生效路径——设置保存 → rebuild_window → _open_window
        # 重跑 → 交换间隔随新 GL 上下文重设（vsync 热生效，重建失败回落
        # restart_needed 重启生效）；tick 数字档变更由 run() 每帧读 settings
        # 即时生效。soup3D 引擎默认不限速，零改动
        self._set_swap_interval(1 if self.settings.fps_mode == "vsync" else 0)

    def _scaled_frame_begin(self):
        """全屏缩放模式帧首（run() 每帧渲染分支前调用）：
        1) 视口先设整窗，用黑色清整个窗口——等比上屏后窗口边缘（黑边区）
           必须有底色，否则残留上一帧像素；
        2) 清屏色取自引擎背景色（soup3D.init 传入），临时改黑清完即还原，
           不污染引擎自身的清屏状态；
        3) 视口切到等比居中矩形——后续引擎渲染 / 画质包合成通道 / present
           直绘全部落在此矩形内，实现 RT 内容等比放大上屏。"""
        win_w, win_h = pygame.display.get_surface().get_size()
        clear = glGetFloatv(GL_COLOR_CLEAR_VALUE)   # 存引擎清屏色
        glClearColor(0.0, 0.0, 0.0, 1.0)
        glViewport(0, 0, win_w, win_h)              # 清整窗（含黑边区）
        glClear(GL_COLOR_BUFFER_BIT)
        glClearColor(*clear)                        # 还原清屏色
        glViewport(*self._scale_rect)               # 本帧绘制收窄到上屏矩形

    def _scaled_frame_end(self):
        """全屏缩放模式帧尾：视口恢复整窗。imgui 集成层按整窗 display_size
        布局绘制 UI，若视口仍收窄在等比矩形会把整窗 UI 裁剪/挤压到矩形内。"""
        win_w, win_h = pygame.display.get_surface().get_size()
        glViewport(0, 0, win_w, win_h)

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
            font_ok = self._reload_font(self._current_font_px())
            if self.screen:
                self.screen.reload()
            self.restart_needed = not font_ok  # 字体重载失败 → 提示重启生效
        except Exception:
            self.restart_needed = True

    # ---- UI 等比缩放 ----
    # 样式表缩放白名单(值=元组按分量缩放; 对齐类 0..1 项不缩放)
    _STYLE_SCALED_VARS = (
        "window_padding", "frame_padding", "item_spacing", "item_inner_spacing",
        "cell_padding", "touch_extra_padding", "display_safe_area_padding",
        "display_window_padding", "window_min_size", "indent_spacing",
        "scrollbar_size", "grab_min_size", "columns_min_spacing",
        "log_slider_deadzone",
        "window_rounding", "child_rounding", "frame_rounding", "popup_rounding",
        "scrollbar_rounding", "grab_rounding", "tab_rounding",
        "window_border_size", "child_border_size", "frame_border_size",
        "popup_border_size", "tab_border_size",
        "tab_min_width_for_close_button",
    )
    FONT_RELOAD_DEBOUNCE_S = 0.5   # 拖拽静止 0.5s 后一次重建(CJK 图集烘焙 ~0.5s/次)

    def _current_font_px(self) -> int:
        """按当前窗口像素高算字号档位(开窗/重建/resize 共用)"""
        return ui_scale.font_px(self._display_size[1])

    def _reload_font(self, px: int) -> bool:
        """字号档重建唯一入口(开窗/设置保存/窗口化 resize 三路径共用)：销毁
        重建 imgui 上下文与集成层，再加载当前档字体。

        两个方案实测不可行：
        - 反复 add_font：图集累积语义，5 份 CJK 达 32768 宽(512MB)超 GPU 纹
          理上限，上传失败 → UI 字符整体消失(本 bug：最大化/还原两次即触发，
          反复保存设置同样累积——一并根治)；
        - io.fonts.clear_fonts 后二次烘焙：这版 imgui 的 rect packing 残留
          断言 (src_tmp.DstIndex != -1) 稳定复现(alpha8/rgba32 皆炸)。
        destroy+create 是全新图集，等效初次启动序列(生产实证安全)。旧集成
        层唯一 GL 资源是字体纹理(_font_texture)，重建前显式删除防显存泄漏。
        加载失败回落 font=None(UI 英文；空图集首烘自动垫默认字体，启动同款)。
        调用时机：帧边界(run() new_frame 前)，禁止 imgui 帧中。"""
        old_tex = getattr(self._impl, "_font_texture", None)
        if old_tex:
            glDeleteTextures([old_tex])
        # pyimgui 绑定的 destroy 必须显式传 ctx 对象(无参恒抛 Context invalid)
        imgui.destroy_context(self._imgui_ctx)
        self._imgui_ctx = imgui.create_context()
        self._impl = PygameRenderer()
        # spike 结论同款坑：pygame 集成不初始化 display_size，重建后必须补设，
        # 否则下一帧 new_frame 断言 Invalid DisplaySize 崩溃。必须用 _display_size
        # 权威值——surface 尺寸不随真实拖拽更新，用它会把 io 拉回初始尺寸 → UI
        # 回到原尺寸挤在左上（用户实测：缩放后 UI 先跟随、去抖重建瞬间回退）
        imgui.get_io().display_size = self._display_size
        new_font = load_cjk_font(px)
        if new_font is not None:
            self.font = new_font
            self._font_px = px
            self._impl.refresh_font_texture()   # 新字体必须重烘焙，否则下帧断言
            return True
        self.font = None
        self._font_px = 0
        self._impl.refresh_font_texture()   # 空图集首烘自动垫默认字体(启动同款)
        return False

    def _apply_pending_resized_font(self):
        """窗口化 resize 的字号档重建(run() 帧边界调用, 去抖见常量)。

        拖拽中只记录 pending, 静止后一次重建(GL 纹理上传在帧边界, imgui 帧
        中禁字体图集操作——同 _pending_rebuild 机制); 字体重载失败保留旧字
        体(rebuild_window 同款回落)。预设帧渲染器 RT 按 create 时的 proj 定
        尺寸, resize 后过期, 一并重建(设置保存同一条 reload 路径, 回落链兜底)。"""
        if time.monotonic() - self._last_window_resize < self.FONT_RELOAD_DEBOUNCE_S:
            return
        px = self._pending_font_px
        self._pending_font_px = None
        self._reload_font(px)
        if self._frame_renderer is not None:
            self.reload_shader_preset()

    def _capture_base_style(self):
        """捕获 imgui 默认样式基准(创建上下文后调用一次; 每帧按缩放重设,
        避免在缩放后的值上二次放大)"""
        s = imgui.get_style()
        self._base_style = {n: getattr(s, n) for n in self._STYLE_SCALED_VARS}

    def _apply_ui_style_scale(self):
        """按当前 UI 缩放重设样式表(run() 每帧 new_frame 前调用)"""
        sc = ui_scale.current_scale()
        s = imgui.get_style()
        for name, base in self._base_style.items():
            if isinstance(base, tuple):
                setattr(s, name, tuple(round(b * sc) for b in base))
            else:
                setattr(s, name, base * sc)

    # ---- 输入 ----
    def _make_rc_input(self):
        if self.rc_config.mode == "joystick" and self.rc_config.device_name:
            for i in range(pygame.joystick.get_count()):
                try:
                    j = pygame.joystick.Joystick(i)
                    if j.get_name() == self.rc_config.device_name:
                        j.init()
                        # 二期 AUX：按钮源 + 轴数/按钮数传入（越界防御换设备回中）
                        return JoystickInput(j.get_axis, self.rc_config.mappings,
                                             get_button_fn=j.get_button,
                                             num_axes=j.get_numaxes(),
                                             num_buttons=j.get_numbuttons())
                except Exception:
                    continue  # 构造/init 失败（热插拔窗口期设备半就绪）→ 试下一个
        return KeyboardInput()  # 无手柄/未配置/全部失败 → 键盘兜底（UI 明示）

    def poll_joysticks(self):
        """手柄插拔双向检测（一期「消失回落」语义保持 + Task 38「出现拾取」）：
        - 消失方向（一期）：当前 JoystickInput 且设备列表空 → 回落键盘 +
          joystick_lost 标记（rc_setup 明示）+ 日志
        - 出现方向（Task 38）：配置为手柄但当前 KeyboardInput（启动前未接 /
          启动后接入 / 拔出后重插）→ _make_rc_input 重扫设备名命中即自动重建；
          成功清 lost、置 recovered（对称提示）+ 日志；失败（init 异常/名字
          未出现）保持键盘，按 JOYSTICK_PICKUP_RETRY_S 退避重试防抖动死循环。
          热插拔发现依赖 SDL 事件泵——run() 每帧 event.get() 先于此调用"""
        self.joystick_lost = getattr(self, "joystick_lost", False)
        self.joystick_recovered = getattr(self, "joystick_recovered", False)
        if isinstance(self.rc_input, JoystickInput):
            if pygame.joystick.get_count() == 0:
                self.rc_input = KeyboardInput()
                self.joystick_lost = True
                self.joystick_recovered = False
                print(f"[输入] 手柄已断开，回落键盘: {self.rc_config.device_name}")
            return
        # 出现方向：仅手柄配置参与自动拾取（键盘配置的手柄只作 UI 设备源）
        if self.rc_config.mode != "joystick" or not self.rc_config.device_name:
            return
        now = time.monotonic()
        if now < getattr(self, "_joystick_pickup_after", 0.0):
            return  # 退避窗口内：本轮不试（上次失败/设备未出现）
        picked = self._make_rc_input()
        if isinstance(picked, JoystickInput):
            self.rc_input = picked
            self.joystick_lost = False
            self.joystick_recovered = True
            print(f"[输入] 手柄已自动连接: {self.rc_config.device_name}")
        else:
            self._joystick_pickup_after = now + JOYSTICK_PICKUP_RETRY_S

    # ---- 状态机 ----
    def switch(self, screen):
        if self.screen:
            self.screen.exit()
        self.screen = screen
        screen.enter(self)

    # ---- MSP 状态源（Task 19） ----
    def _msp_state(self) -> dict:
        """MSP handler 状态快照：当前机参数 + 预设代号恒在（基础字段）；
        飞行页在场且在飞时附 "flight" 遥测（flight_snapshot None 则省略键）"""
        state = {"params": self.drone_params,
                 "preset_id": self.settings.current_drone_id}
        if isinstance(self.screen, FlightScreen):
            snap = self.screen.flight_snapshot()
            if snap is not None:
                state["flight"] = snap
        return state

    def _msp_on_change(self) -> None:
        """MSP SET 写信号：置脏标志；FlightScreen.update 帧首消费（重建
        飞控参数即时生效）。飞行页不在场时标志滞留，进飞行页首帧生效"""
        self.msp_params_dirty = True

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
            if self._flog: self._flog.mark("tick")
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
                self._pending_font_px = None   # rebuild 已按当前窗口高重载字体
                self.rebuild_window()
            elif self._pending_preset_reload:
                # 帧边界执行预设重载：GL 资源创建禁止在 imgui 帧中进行（同 _pending_rebuild）
                self._pending_preset_reload = False
                self.reload_shader_preset()
            if self._pending_font_px is not None:
                self._apply_pending_resized_font()
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    return
                if event.type == pygame.KEYDOWN and event.key == pygame.K_F3:
                    self.debug_overlay = not self.debug_overlay  # 全局热键，任意界面可用
                if self._flog and event.type == pygame.KEYDOWN and event.key == pygame.K_F9:
                    self._flog.mark_user()  # 用户体感卡顿标记（记录仪开着才生效）
                if event.type == pygame.VIDEORESIZE:
                    # 不透传 pyimgui 集成层：其 VIDEORESIZE 分支会再 set_mode，
                    # 全屏切换的中间态尺寸可越界致 OverflowError；窗口尺寸与
                    # 字体图集已由 rebuild_window 自管，这里只同步 display_size
                    if event.w > 0 and event.h > 0:
                        imgui.get_io().display_size = (event.w, event.h)
                        self._display_size = (int(event.w), int(event.h))   # 权威值同步
                        if self.settings.display_mode == "window":
                            # 拖拽/最大化: 引擎视口+投影跟随新窗口尺寸,
                            # window_size 实时记忆(退出 save_all 落盘),
                            # 字号档 pending 由帧边界去抖重建(烘焙~0.5s/次)
                            soup3D.resize(event.w, event.h)
                            self.settings.window_size = (int(event.w), int(event.h))
                            self._last_window_resize = time.monotonic()
                            px = ui_scale.font_px(event.h)
                            if px != self._font_px:
                                self._pending_font_px = px
                    continue
                self._impl.process_event(event)
                self.screen.handle_event(event)
            if self._flog: self._flog.mark("ev")

            # 键盘输入喂给 RC 层（只传按下集合，避免 input 层依赖 pygame）
            pressed = {i for i, on in enumerate(pygame.key.get_pressed()) if on}
            self.rc_input.set_pressed(pressed)
            self.poll_joysticks()

            # MSP 请求分派（Task 16）：accept 新连接 + 取空队列应答；
            # 服务器未启动直返，不阻塞主循环
            self.msp_server.drain()
            if self._flog: self._flog.mark("inp")

            self.screen.update(dt)
            if self._flog: self._flog.mark("upd")

            if self._scale_rect is not None:
                self._scaled_frame_begin()  # 缩放模式帧首：整窗清黑边 + 视口收窄上屏矩形

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
                if self._scale_rt is not None:
                    # 缩放模式默认管线：场景渲进离屏 RT（保持逻辑分辨率的 GPU
                    # 成本），场景直渲钩子同进 RT；present 把 RT 内容直通绘制到
                    # 帧首收窄的等比视口矩形内放大上屏——全屏不切显示模式保
                    # 面板原生刷新率（165Hz）
                    soup3D.update(target=self._scale_rt)
                    self._scale_rt.bind()
                    self.screen.render_scene()    # 3D 直渲钩子（显示列表失效，见 base.py）
                    self._scale_rt.unbind()
                    self._scale_rt.present()
                else:
                    soup3D.update()               # 先渲 3D（spike 验证的顺序）
                    self.screen.render_scene()    # 3D 直渲钩子（显示列表失效，见 base.py）
            if self._scale_rect is not None:
                self._scaled_frame_end()  # 缩放模式帧尾：恢复整窗视口（imgui 全窗绘制）
            if self._flog: self._flog.mark("rnd")
            # imgui 内部时间每帧 io.Time += io.DeltaTime（光标闪烁、退格/方向键
            # 重复、双击判定全吃这个时间）。集成层 process_inputs() 从不被调
            # （主循环只走 process_event 事件流），delta_time 恒为初始 1/60 →
            # 实际帧率 F 时 UI 时间以 F/60 倍速快进：unlimited 实测 ~900fps 即
            # ~15 倍速，光标高频闪烁出残影、文本编辑按键重复飞快。按实测帧间隔
            # 喂 imgui；clock.tick 整数毫秒在高帧率下可量化为 0ms，下限 1ms
            # 防 UI 时间冻结
            imgui.get_io().delta_time = dt if dt > 0.0 else 0.001
            self._apply_ui_style_scale()
            imgui.new_frame()
            # push 前捕获：render_ui 处理器可能触发重建改动 self.font（帧中禁字体
            # 图集操作），pop 条件若再读 self.font 会与 push 时不一致 → 栈失衡
            pushed = self.font is not None
            if pushed:
                imgui.push_font(self.font)
            self.screen.render_ui()
            self._render_debug_overlay()  # 仍在 push_font 作用域内，中文标签可显示
            self._render_gs_overlay()     # 地面站常驻浮窗（Task 43，同上作用域）
            if pushed:
                imgui.pop_font()
            imgui.render()                # 再画 UI
            # pyimgui 2.0：render() 只终结帧并生成绘制数据，集成层才真正上屏
            # 兜底：引擎 3D 绘制若遗留活动纹理单元非 0（soup3D rend_cached 已在
            # 引擎侧归位，此处防御其他泄漏路径），pyimgui 的字体绑定会落在错误
            # 单元上导致 UI 整体隐形（飞行界面 UI 消失之谜的根因）
            glActiveTexture(GL_TEXTURE0)
            self._impl.render(imgui.get_draw_data())
            if self._flog: self._flog.mark("cnt")

            pygame.display.flip()
            if self._flog:
                self._flog.mark("flip")
                self._flog.frame(dt * 1000)   # 本帧收口：写一行（dt 毫秒=clock.tick 返回×1000）

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
        imgui.set_next_window_position(w - ui_scale.u(240), ui_scale.u(8))  # 固定右上角
        flags = (imgui.WINDOW_NO_TITLE_BAR | imgui.WINDOW_ALWAYS_AUTO_RESIZE |
                 imgui.WINDOW_NO_MOVE)
        imgui.push_style_color(imgui.COLOR_WINDOW_BACKGROUND, 0, 0, 0, 0.5)  # 半透明底
        imgui.begin("##debug_overlay", flags=flags)
        imgui.text(f"FPS: {self._fps_avg:.0f}")  # 0.5s 窗口平均（io.framerate 恒 60 不可用）
        imgui.text(f"相机: {soup3D.camera.X:.2f}, {soup3D.camera.Y:.2f}, {soup3D.camera.Z:.2f}")
        imgui.end()
        imgui.pop_style_color(1)

    # ---- 地面站常驻浮窗（Task 43：提示框上移 App 层，跨 Screen 显示）----

    @staticmethod
    def gs_address_lines(server) -> list:
        """从 VfcServer 组装浮窗地址行（空值省略）：TCP / WebSocket / PTY
        slave 路径。属性缺失（server 未挂接/契约变更）全防御返回 []。
        状态机契约（test_msp_server.py Task 43 组）：未启动 [] → start()
        非空行 → stop() 归空"""
        lines = []
        for attr in ("tcp_address", "websocket_address"):
            v = getattr(server, attr, "") or ""
            if v:
                lines.append(str(v))
        slave = getattr(server, "slave_path", None)
        if slave:
            lines.append(str(slave))
        return lines

    @staticmethod
    def copy_text_to_clipboard(text: str) -> bool:
        """pygame.scrap 剪贴板写入（utf-8 bytes）。类型逐个回退：先平台
        规范类型 pygame.SCRAP_TEXT（常量在 pygame 顶层，2.6.1 实测不在
        scrap 子模块），再 "text/plain;charset=utf-8"——实测 pygame 2.6.1
        X11 后端裸 'text/plain' 被拒（content could not be placed in
        clipboard），charset 变体走通（put/get utf-8 中文往返验证）；
        Windows/macOS 两类型文档均支持。任何失败（init 无显示子系统/两
        类型 put 全败）返回 False 不抛——调用方降级提示手动复制"""
        try:
            import pygame.scrap as scrap
            scrap.init()   # 幂等；须在 set_mode 后（App 主循环内必然满足）
            data = text.encode("utf-8")
            for mime in (pygame.SCRAP_TEXT, "text/plain;charset=utf-8"):
                try:
                    scrap.put(mime, data)
                    return True
                except Exception:
                    continue   # 该类型被拒 → 试回退类型
            return False
        except Exception:
            return False

    def _render_gs_overlay(self):
        """地面站连接常驻浮窗（用户实测衍生：一期提示框困在参数页，退出即
        消失而 MSP 功能跨页存活——渲染层上移到 App 与功能层对齐）。
        connected 门控（VfcServer 的 running 属性实名 connected）；默认
        标题栏可拖动，首帧固定右上角、此后 imgui 按 title 记忆位置（用户
        拖开避遮挡，重连回用户拖动处）；三地址行各带「复制」按钮 +
        「断开」按钮（stop 失败 OSError 转行内错误提示）"""
        srv = self.msp_server
        if srv is None or not getattr(srv, "connected", False):
            return
        if not self._gs_window_spawned:
            # 仅 App 生命周期内首帧定位：每帧 set 会覆盖用户拖动后的位置
            io = imgui.get_io()
            imgui.set_next_window_position(io.display_size.x - ui_scale.u(440), ui_scale.u(30))
            imgui.set_next_window_size(ui_scale.u(420), 0)   # 宽固定高自适应（内容少不溢出）
            self._gs_window_spawned = True
        # 默认标题栏（无 NO_MOVE/NO_TITLE_BAR）→ 可拖动；NO_COLLAPSE 防折叠丢地址
        imgui.begin("地面站连接", flags=imgui.WINDOW_NO_COLLAPSE)
        imgui.text("地面站已接入，在地面站软件中连接以下任一地址：")
        for i, line in enumerate(self.gs_address_lines(srv)):
            imgui.push_id(f"addr{i}")   # 同名「复制」按钮按行隔离
            imgui.text(line)
            imgui.same_line()
            if imgui.button("复制", ui_scale.u(72), ui_scale.u(22)):
                if self.copy_text_to_clipboard(line):
                    self._gs_copy_fail = None
                else:
                    self._gs_copy_fail = i   # 行级失败标记（新行提示，不挤地址行）
            if self._gs_copy_fail == i:
                imgui.text("复制失败，请手动选择文本")
            imgui.pop_id()
        if self._gs_error:
            imgui.text(self._gs_error)
        if imgui.button("断开", ui_scale.u(160), ui_scale.u(40)):
            try:
                srv.stop()
            except OSError as e:
                self._gs_error = f"断开失败：{e}"
            else:
                self._gs_error = None
        imgui.end()

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
