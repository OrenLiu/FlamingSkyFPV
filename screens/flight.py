"""飞行界面：场景选择 → 花式飞行（相机由 PyBullet 位姿驱动）+ HUD/OSD + Esc 菜单"""
import json
import math
import os
import time
import imgui
import pygame
import soup3D
from .base import Screen
from input.joystick import JoystickInput
from physics.drone import (ARM_THROTTLE_MAX, BOX_ARM,
                           FlightController, eval_boxes)
from physics.world import (FlightWorld, bullet_to_render, compose_camera_quat,
                           SIM_TICK)
from cover_cache import (cover_uv_crop, load_cover_texture, free_cover_texture,
                         COVER_BOX_W, COVER_BOX_H)
from osd import compose_state, build_frame, OSD_COLS, OSD_ROWS
import bootstrap
from osd_font import (parse_mcm, bake_atlas, free_atlas,
                      MAX7456_FORMAT, ATLAS_COLS)
from ui_scale import u


def _glyph_cell(display_w: float, display_h: float) -> tuple:
    """OSD 字符格自适应窗口（Task 39）：30×16 网格铺满界面的格尺寸与
    居中锚点，返回 (cell_w, cell_h, origin_x, origin_y)。

    真机 OSD 是全屏视频叠加（字符格铺满画面、随画面两轴独立拉伸——
    PAL 像素本就非正方形），故两轴各自 floor(display/格数) 取整、不做
    min 等比缩放；余数像素水平/垂直居中平分（半像素锚点 imgui 可接受）。
    格尺寸下限 1（极小窗口不产生 0/负宽高绘制）。每帧读 io.display_size
    即时计算——窗口 resize（rebuild_window→reload）后自动跟随新尺寸，
    无需额外失效逻辑（reload 已释放图集会重烘焙，UV 步进不受影响）。
    """
    cell_w = max(1, int(display_w) // OSD_COLS)
    cell_h = max(1, int(display_h) // OSD_ROWS)
    origin_x = (display_w - cell_w * OSD_COLS) / 2.0
    origin_y = (display_h - cell_h * OSD_ROWS) / 2.0
    return cell_w, cell_h, origin_x, origin_y


class FlightScreen(Screen):
    # 网格卡片布局：卡片宽/封面高复用 cover_cache.COVER_BOX_W/H，只留两个
    CARD_NAME_H = 36     # 卡片标题条高（含文字垂直居中）
    CARD_GAP = 16        # 卡片间距
    # OSD（设计 §9 / Task 28→39）：字体资产与图集 UV 参数。屏上格尺寸不再
    # 固定 12×18（高分辨率窗口下只占左上角一小块），改 _glyph_cell 按窗口
    # 自适应铺满；12×18 仅作图集内字形 UV 步进基准（图集 16×16 格平铺）
    OSD_FONT_PATH = "assets/osd_betaflight.mcm"
    OSD_GLYPH_W = MAX7456_FORMAT[2]
    OSD_GLYPH_H = MAX7456_FORMAT[3]
    # 内部子状态：选场景 → 加载 → 飞行
    def enter(self, app):
        self.app = app
        self.stage = "select"       # select | loading | flying
        self.error = ""
        self._covers = {}           # 场景目录 → (tex_id, w, h) | None（含失败缓存）
        self._scene = None          # SceneInfo
        self._model = None          # soup3D.Model
        self._sun = None
        self._world = None
        self._fc = None
        self._paused = False
        # 固定 tick 累加器（SIM_TICK）：物理/飞控节拍与渲染帧率解耦——设置页
        # 帧率不再触碰物理（根因修复，帧率不变性回归 test/test_fps_invariance.py）
        self._sim_acc = 0.0
        self._esc_open = False
        self._flight_time_s = 0.0   # 飞行计时（MSP 快照/OSD 消费，暂停不累计）
        # 飞行取证记录器（FLIGHT_REC=1 开启）：每 tick 追加位姿/速度/杆量到
        # flight_rec.jsonl（相对 cwd），用于穿墙等缺陷的精确复现回放——用户
        # 复现一次穿越后用该轨迹逐 tick 回放定位失效子步。默认关闭零开销
        self._rec = None
        if os.environ.get("FLIGHT_REC"):
            self._rec = open("flight_rec.jsonl", "a", buffering=1)
            self._rec.write(json.dumps({"event": "session",
                                        "wall": time.time()}) + "\n")
        # OSD 距离状态（Task 47，HOME_DIST/FLIGHT_DIST 数据源）：水平面取
        # bullet (x,y)（z 为高度，与 alt_m 同源同单位惯例——米）；锁定后冻结
        self._home_xy = None        # 起飞点水平位置（None=从未解锁）
        self._last_xy = None        # 上一帧水平位置（里程累计步进基准）
        self._dist_flown_m = 0.0    # 本次解锁累计水平飞行里程
        self._dist_home_m = 0.0     # 当前到起飞点水平距离
        self._home_bearing = None   # 机→home 罗盘角·度（Task 55 HOME_DIR；
                                    # None=从未解锁；0=在 home 点上 atan2(0,0))
        self._dist_armed_prev = False   # 距离跟踪的上一帧解锁态（上升沿检测）
        # 飞行统计（Task 52，OSD TOTAL_FLIGHTS 数据源）：解锁时段起止锚点
        # 与上升/下降沿检测（固件 stats.c statsOnArm/statsOnDisarm 语义）；
        # _arm_flip=反乌龟解锁锁存的消费副本（Task 54 起锁存源唯一化：
        # fc.flip_over_latched——解锁沿读值，加锁沿消费并清零）
        self._arm_start_s = None        # 本次解锁起始 _flight_time_s（None=未解锁）
        self._arm_flip = False          # 本次解锁 fc 反乌龟锁存值（救援不计架次）
        self._stats_armed_prev = False  # 统计跟踪的上一帧解锁态（沿检测）
        self._aux_us = []           # 本帧 AUX µs 缓存（快照消费；非 virtual_fc 恒空）
        self._boxes = frozenset()   # 本帧激活模式 box 缓存（同上）
        # OSD 图集（Task 28）：脏标记在帧边界（update 顶部，imgui 帧外）烘焙——
        # 帧内禁 GL 创建（同封面纹理/_pending_preset_reload 先例）。virtual_fc
        # 仅参数页转换可变（飞行页存活期间稳定，enter 时判定一次即可）；
        # getattr 防御：单测 fake app 桩（SimpleNamespace）常缺 drone_params，
        # select/MSP 路径无 OSD 需求，缺属性即不烘焙
        params = getattr(app, "drone_params", None)
        self._osd_atlas = None      # (tex_id, w, h) | None（烘焙失败也置 None）
        self._osd_atlas_dirty = bool(getattr(params, "virtual_fc", False))

    def exit(self):
        self._teardown_scene()      # 离开飞行必须清理 GL 与物理资源
        self._release_covers()      # 封面纹理同属 GL 资源：FlightScreen 每次进入都新建实例，不释放会泄漏
        self._release_osd_atlas()   # OSD 图集同属 GL 资源，一并释放

    def reload(self):
        """窗口重建后重载：新 GL 上下文 → 封面纹理句柄全部失效，必须释放重载"""
        self._release_covers()
        self._release_osd_atlas()   # OSD 图集句柄随 GL 上下文一并失效
        params = getattr(self.app, "drone_params", None)   # 防御同 enter
        self._osd_atlas_dirty = bool(getattr(params, "virtual_fc", False))
        if self.stage == "flying" and self._scene is not None:
            self._teardown_scene()
            self.stage = "loading"

    def _release_covers(self):
        """释放全部封面纹理（GL 句柄不跨 GL 上下文复用；窗口重建/退出时调用）"""
        for entry in self._covers.values():
            if entry:
                free_cover_texture(entry[0])
        self._covers.clear()

    def _release_osd_atlas(self):
        """释放 OSD 字体图集（free_atlas 容错，重复/非法句柄不抛）"""
        if self._osd_atlas is not None:
            free_atlas(self._osd_atlas[0])
            self._osd_atlas = None

    def _bake_osd_atlas(self):
        """烘焙 OSD 字体图集（帧边界专用：update 顶部调用，imgui 帧外）。

        swizzle(R/G/B=ONE, A=ALPHA) 已并入 osd_font.bake_atlas（Task 39
        顺路批：与 bake 同 try 块，pre-GL3.3 驱动不炸）。parse/bake 失败
        置 None 静默降级——OSD 为增强层，缺失仅无叠加信息，不影响飞行
        （HUD 仍按设计 §9 保持隐藏）。"""
        try:
            chars = parse_mcm(bootstrap.resource_path(self.OSD_FONT_PATH))
        except (OSError, ValueError):
            self._osd_atlas = None
            return
        self._osd_atlas = bake_atlas(chars)

    # ---- 场景选择 ----
    def render_select(self):
        io = imgui.get_io()
        imgui.set_next_window_position(0, 0)
        imgui.set_next_window_size(*io.display_size)
        imgui.begin("##select", flags=imgui.WINDOW_NO_TITLE_BAR | imgui.WINDOW_NO_BACKGROUND)
        imgui.text("花式飞行——选择场景")
        imgui.text("( 竞速飞行将在二期开放 )")
        imgui.separator()
        # 网格卡片：封面缩略图 + 名称按钮，按窗口宽度自动折行
        valid = [s for s in self.app.scenes if s.valid]
        avail = imgui.get_content_region_available_width()
        cols = max(1, int((avail + u(self.CARD_GAP)) // (u(COVER_BOX_W) + u(self.CARD_GAP))))
        for i, s in enumerate(valid):
            self._scene_card(s)
            if (i + 1) % cols and i + 1 < len(valid):
                imgui.same_line()
        # 无效场景保留文字行
        for s in self.app.scenes:
            if not s.valid:
                imgui.text(f"{s.name}（无效：{s.error}）已跳过")
        imgui.separator()
        if imgui.button("返回主菜单", u(200), u(40)):
            self._back()
        imgui.end()

    def _scene_card(self, s):
        # 单卡片：invisible_button 承接整卡点击/悬停，draw list 直绘封面与标题
        card_h = u(COVER_BOX_H) + u(self.CARD_NAME_H)
        clicked = imgui.invisible_button(f"##card_{s.dir.name}", u(COVER_BOX_W), card_h)
        hovered = imgui.is_item_hovered()
        p = imgui.get_item_rect_min()
        e = imgui.get_item_rect_max()
        dl = imgui.get_window_draw_list()
        col_text = imgui.get_color_u32_rgba(0.92, 0.92, 0.95, 1.0)
        col_dim = imgui.get_color_u32_rgba(0.55, 0.55, 0.58, 1.0)
        col_bg = imgui.get_color_u32_rgba(0.10, 0.10, 0.12, 1.0)
        col_border = imgui.get_color_u32_rgba(1.0, 1.0, 1.0, 0.7 if hovered else 0.25)
        dl.add_rect_filled(p.x, p.y, e.x, e.y, col_bg)
        strip_top = p.y + u(COVER_BOX_H)
        cover = self._get_cover(s)
        if cover:
            tid, w, h = cover
            uv = cover_uv_crop(w, h, u(COVER_BOX_W), u(COVER_BOX_H))
            dl.add_image(tid, (p.x, p.y), (p.x + u(COVER_BOX_W), strip_top),
                         uv[:2], uv[2:])
        else:
            dl.add_rect_filled(p.x, p.y, p.x + u(COVER_BOX_W), strip_top,
                               imgui.get_color_u32_rgba(0.16, 0.16, 0.18, 1.0))
            self._draw_centered_text(dl, p.x, p.y, u(COVER_BOX_W), u(COVER_BOX_H),
                                     col_dim, "无封面")
        self._draw_centered_text(dl, p.x, strip_top, u(COVER_BOX_W),
                                 u(self.CARD_NAME_H), col_text, s.name)
        dl.add_rect(p.x, p.y, e.x, e.y, col_border)
        if clicked:
            self._scene = s
            self.stage = "loading"

    @staticmethod
    def _draw_centered_text(dl, x, y, w, h, col, text):
        # 矩形区域内水平垂直居中绘制文本（draw list 直绘，不产生 imgui item）
        size = imgui.calc_text_size(text)
        dl.add_text(x + (w - size.x) / 2, y + (h - size.y) / 2, col, text)

    def _get_cover(self, s):
        # 封面纹理懒加载缓存：失败也缓存 None，避免逐帧重试
        if not s.cover:
            return None
        if s.dir not in self._covers:
            self._covers[s.dir] = load_cover_texture(s.dir / s.cover)
        return self._covers[s.dir]

    # ---- 场景加载/卸载 ----
    def _load_scene(self):
        s = self._scene
        sky = s.sky   # 天空配置：太阳参数同时驱动方向光与画面优先包的天空盒
        obj = s.dir / s.model
        mtl = obj.with_suffix(".mtl")
        self._model = soup3D.open_obj(str(obj), str(mtl) if mtl.exists() else None,
                                      surface=self.app.preset.surface_class)
        # 包场景加载钩子（spec 4.3 扩展）：逐材质扩展注入（如 map_Ns 逐像素
        # 粗糙度贴图），在首帧渲染前完成
        if self.app.preset.post_load is not None:
            self.app.preset.post_load(self._model,
                                      str(mtl) if mtl.exists() else None)
        # 画面优先包的天空盒（渲染器可选方法 set_sky）：全景图或按太阳参数
        # 程序生成；性能优先包无帧渲染器，方向光参数两模式共享
        renderer = getattr(self.app, "_frame_renderer", None)
        if renderer is not None and hasattr(renderer, "set_sky"):
            renderer.set_sky(sky["sun_yaw"], sky["sun_pitch"],
                             sky["sun_color"], sky["panorama"] or None)
        self._model.size(s.scale, s.scale, s.scale)   # 倍数缩放（引擎 API：倍数）
        # spike 结论：不调 show()（显示列表回放在 Mesa 失效），render_scene() 逐帧直渲
        self._sun = soup3D.light.Direct((sky["sun_yaw"], sky["sun_pitch"], 0),
                                        tuple(sky["sun_color"]))
        self._sun.turn_on()
        soup3D.light.ambient(*s.sky["ambient"])
        self._world = FlightWorld(self.app.drone_params)
        self._world.load_scene_mesh(str(obj), s.scale)
        self._world.reset(s.spawn)
        self._fc = FlightController(self.app.drone_params)
        self.stage = "flying"

    def _teardown_scene(self):
        # 灯光必须 destroy()：Direct.__init__ 自登记进引擎全局 light_queue（强引用），
        # 弃引用不会注销——旧灯保持点亮，每次进场景叠加一盏方向光（场景越来越亮）。
        # 置 None 防双重 destroy（加载失败路径可能 teardown 两次，destroy 已删键再删会 KeyError）
        if self._sun is not None:
            self._sun.destroy()
            self._sun = None
        # 未调 show()，模型未进引擎队列，弃引用即可
        # （hide() 对未 show 的模型会 KeyError，勿调用）
        self._model = None
        if self._world is not None:
            self._world.close()
            self._world = None

    def _back(self):
        from .main_menu import MainMenuScreen
        self.app.switch(MainMenuScreen())

    # ---- 事件 ----
    def handle_event(self, event):
        if (event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE
                and self.stage == "flying"):
            self._esc_open = not self._esc_open   # Esc 菜单开关
            self._paused = self._esc_open
            if self._paused and self._world is not None:
                # 暂停即恢复正常推力方向（Task 50 v4）：暂停期无物理步，
                # 此处防御性复位防桨反转残留（恢复后首帧 update 会按反乌龟
                # 激活态重设）；进入飞行页无需处理——_load_scene 新建
                # FlightWorld 即正常推力，退出飞行页世界整体销毁
                self._world.set_prop_reverse(False)

    # ---- 每帧 ----
    def flight_snapshot(self) -> "dict | None":
        """MSP 状态源飞行遥测快照（Task 19）：flying 未暂停且世界/飞控/杆量
        在位时返回，其余态返回 None（App._msp_state 消费）。字段：
        armed=飞控解锁态；boxes/aux_us=本帧模式求值结果（update 缓存）；
        stick_us 顺序 (roll, pitch, yaw, throttle)，全通道 1500 中心 ±500
        （throttle∈[0,1] 映射 1500-2000，MSP 线上格式由 handler 换算）；
        attitude_deg 为 bullet 语义 world.euler_deg（roll 右滚正 / pitch
        低头正 / yaw 机头左转正——杆义 euler_stick_deg 翻转 y/z 两轴，
        Task 49 更正原「pitch抬头/yaw右」误标），非杆义；vario_mps 取
        velocity[2]（正=上升）。Task 49 增三字段：arm_denied=fc.
        arm_denied_once 闩锁（Task 52 起 OSD WARNINGS 改读实时
        arming_blocked，本键保留向后兼容）；rtc_text=params.virtual.
        rtc_unix_s（SET_RTC 246 存储）的 "%Y-%m-%d %H:%M:%S" 全格式化
        （0=未设置空串，OSD RTC_DATETIME——osd.py 零 import 纪律，
        时间换算在本层）。Task 52 走时模型：MSP_SET_RTC 同帧记
        rtc_recv_unix 墙锚（handlers 246），有效时间 = rtc_unix_s +
        (time.time() − rtc_recv_unix)——对应固件真实走时 RTC（上电校时
        后自由运行，OSD 每秒刷新）；无锚点（Task 49 旧档/旧预设缺键）
        维持冻结显示旧行为；
        up_body=世界 +Z（上）单位向量在机体系坐标 (x,y,z)（euler_deg 推导：
        x=-sin(pitch)、y=sin(roll)cos(pitch)、z=cos(roll)cos(pitch)，与
        yaw 无关；OSD UP_DOWN_REFERENCE 天顶/天底参考点消费）。
        Task 52 增 arming_blocked=实时解锁被拒条件（ARM box 激活 ∧ 油门
        ≥ ARM_THROTTLE_MAX ∧ 未解锁，逐帧计算——与 drone.py 置
        arm_denied_once 的条件同式但非闩锁；OSD WARNINGS 消费，对应固件
        renderOsdWarning 按实时 armingDisableFlags 渲染的语义）。
        Task 54 增 flip_latched=反乌龟解锁锁存（fc.flip_over_latched；
        OSD FLIP_ARROW 门控消费——固件 osdElementCrashFlipArrow L874 读
        isFlipOverAfterCrashActive() 锁存标志而非实时开关）。Task 55 增
        home_bearing_deg=机→home 罗盘角 int 度（OSD HOME_DIR 消费；None=
        从未解锁——与 dist_home_m 的 0 缺省区分两种「零距离」语义）。"""
        if (self.stage != "flying" or self._paused
                or self._world is None or self._fc is None):
            return None
        ch = getattr(self, "_ch", None)
        if ch is None:
            return None   # update 尚未求值杆量（进入 flying 的首帧边缘）
        v = self._world.velocity
        att = self._world.euler_deg
        roll_r, pitch_r = math.radians(att[0]), math.radians(att[1])
        # rtc 防御链：单测 fake app（SimpleNamespace）常缺 drone_params
        virt = getattr(getattr(self.app, "drone_params", None),
                       "virtual", None)
        rtc = getattr(virt, "rtc_unix_s", 0.0)
        recv = getattr(virt, "rtc_recv_unix", 0.0)
        if rtc > 0 and recv > 0:
            rtc += time.time() - recv       # 走时：设置时刻 + 流逝墙钟
        # 真实电池模拟实时域（physics/battery.py）：voltage_v 实时负载电压
        # （sim 关闭 = 静态 battery_voltage）；battery = BatterySim.snapshot()
        # （sim 关闭 None——OSD 告警/功率元素与 MSP 130 按 None 回落静态行为）。
        # getattr 防御同 rtc 链（单测 fake fc 桩缺属性不崩）
        batt = getattr(self._fc, "battery", None)
        lv = getattr(self._fc, "live_voltage", None)
        return {
            "armed": self._fc.armed,
            "voltage_v": (lv if lv is not None
                          else getattr(getattr(self.app, "drone_params", None),
                                       "battery_voltage", 14.8)),
            "battery": batt.snapshot() if batt is not None else None,
            "boxes": self._boxes,
            # 反乌龟解锁锁存（Task 54）：OSD FLIP_ARROW 门控消费（与实时
            # boxes 分离——锁存会话中途关开关箭头不消失）。getattr 防御
            # 同 arm_denied（SimpleNamespace 桩缺该属性）
            "flip_latched": bool(getattr(self._fc, "flip_over_latched",
                                         False)),
            "aux_us": list(self._aux_us),
            "stick_us": (1500.0 + ch.roll * 500.0,
                         1500.0 + ch.pitch * 500.0,
                         1500.0 + ch.yaw * 500.0,
                         1500.0 + ch.throttle * 500.0),
            "throttle": ch.throttle,
            "attitude_deg": att,
            "gyro_dps": self._world.gyro_dps,
            "speed_mps": math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2]),
            "alt_m": self._world.pos[2],
            "dist_home_m": int(self._dist_home_m),     # Task 47：int 米（OSD
            "dist_flown_m": int(self._dist_flown_m),   # HOME_DIST/FLIGHT_DIST）
            "home_bearing_deg": (int(round(self._home_bearing)) % 360
                                 if self._home_bearing is not None else None),
            # ↑ Task 55：机→home 罗盘角 int 度（OSD HOME_DIR；None=从未
            # 解锁哨兵——与 dist_home_m 的 0 缺省区分「无 home」与「在 home 上」
            "vario_mps": v[2],
            "flight_time_s": self._flight_time_s,
            "arm_denied": bool(getattr(self._fc, "arm_denied_once", False)),
            "arming_blocked": (not self._fc.armed
                               and BOX_ARM in (self._boxes or frozenset())
                               and ch.throttle >= ARM_THROTTLE_MAX),
            "rtc_text": (time.strftime("%Y-%m-%d %H:%M:%S",
                                       time.localtime(rtc))
                         if rtc > 0 else ""),
            "up_body": (-math.sin(pitch_r),
                        math.sin(roll_r) * math.cos(pitch_r),
                        math.cos(roll_r) * math.cos(pitch_r)),
        }

    def _track_flight_distance(self, pos, armed: bool) -> None:
        """OSD 距离状态逐帧跟踪（Task 47，update 帧尾调用）：解锁上升沿
        （armed False→True）记录起飞点水平位置并清零里程；解锁期间每帧累计
        帧间水平位移模长、更新到起飞点距离；锁定后保持上次飞行值不再累计
        （真机语义：落地后 OSD 距离冻结至下次解锁）。水平面取 bullet (x,y)
        ——z 为高度轴，纯垂直移动不计入；param 模式 armed 恒 False 恒空转。
        Task 55 增 home bearing 跟踪（OSD HOME_DIR 数据源，固件 GPS_
        directionToHome 同构——机→home 正向量罗盘角，与距离同帧冻结）。"""
        if armed and not self._dist_armed_prev:
            self._home_xy = (pos[0], pos[1])
            self._last_xy = self._home_xy
            self._dist_flown_m = 0.0
            self._dist_home_m = 0.0
            self._home_bearing = 0.0   # 起飞点=当前位置（atan2(0,0)=0）
        elif armed:
            cur = (pos[0], pos[1])
            self._dist_flown_m += math.hypot(cur[0] - self._last_xy[0],
                                             cur[1] - self._last_xy[1])
            self._last_xy = cur
            self._dist_home_m = math.hypot(cur[0] - self._home_xy[0],
                                           cur[1] - self._home_xy[1])
            # 机→home 罗盘角（固件 GPS_directionToHome 同构，osd HOME_DIR
            # 消费）：北=世界 +X、东=−Y（heading=-att[2] 同一符号链）→
            # bearing = atan2(东分量, 北分量) = atan2(pos.y−home.y, home.x−pos.x)
            self._home_bearing = math.degrees(math.atan2(
                cur[1] - self._home_xy[1],
                self._home_xy[0] - cur[0])) % 360.0
        self._dist_armed_prev = armed

    def _track_flight_stats(self, armed: bool) -> None:
        """OSD TOTAL_FLIGHTS 计数（Task 52，update 帧尾调用）：固件
        stats.c statsOnDisarm 语义——解锁时长 ≥ stats_min_armed_time_s 才
        stats_total_flights += 1（出厂 STATS_OFF=-1 不计数，pg/stats.c
        PG_RESET_TEMPLATE L33；模拟器无 stats PG 配置域，按阈值 0 的计数
        开启语义实现：每次解锁→加锁沿 +1，时长基准 _flight_time_s——
        暂停不累计的飞行页时钟，与固件 millis() 差值口径同构）。
        反乌龟例外（fix round 1，core.c L469）：加锁沿 !flipOverAfterCrashActive
        才计数——Task 54 起该锁存与 drone.py ARM 锁存同源（fc.
        flip_over_latched：解锁成功瞬间按 ARM 时 box 21 置位/未置位清零
        L528-533、飞行中不跟随开关、加锁沿 L474 清零），解锁沿读值存
        消费副本 _arm_flip（加锁沿时刻 fc 已清零，读回架次始值不可得），
        即带反乌龟开关解锁的救援架次不计（正常解锁后中途开 box 21 不
        影响——锁存飞行中不变，照常计数）。virtual.total_flights 就地
        递增（params 对象上 additive 字段），持久化走既有预设保存机制
        （EEPROM_WRITE/参数页落盘）。防御链：fake app 缺 drone_params
        （SimpleNamespace 桩）时静默跳过。"""
        if armed and not self._stats_armed_prev:
            self._arm_start_s = self._flight_time_s   # 解锁沿：记时段起点
            self._arm_flip = self._fc.flip_over_latched   # 同源锁存消费副本
        elif (not armed and self._stats_armed_prev
                and self._arm_start_s is not None):
            # 加锁沿：固件按整秒下取整比对阈值；阈值 0 下任意时长均计数；
            # 反乌龟锁存例外（core.c L469 同款门控）
            if not self._arm_flip:
                virt = getattr(getattr(self.app, "drone_params", None),
                               "virtual", None)
                if virt is not None:
                    virt.total_flights = getattr(virt, "total_flights", 0) + 1
            self._arm_start_s = None
            self._arm_flip = False                   # 固件 L474 加锁清零
        self._stats_armed_prev = armed

    def update(self, dt: float):
        if self._osd_atlas_dirty:
            # 帧边界烘焙（imgui 帧外：主循环 update 在 new_frame 之前调用）
            self._osd_atlas_dirty = False
            self._bake_osd_atlas()
        if self.stage == "loading":
            try:
                self._load_scene()
            except Exception as e:   # OBJ 加载失败 → 返回选择（设计文档第 8 节）
                self.error = str(e)
                self._teardown_scene()
                self.stage = "select"
            return
        if self.stage != "flying" or self._paused:
            return
        # 运行期修正（冒烟实测）：场景加载 ~2s 会计入下一帧 dt（clock 跨加载计时），
        # 2 秒巨型物理步会使机体穿透地面平面坠入世界下方（bullet 平面碰撞单面）。
        # 物理步长钳位到 33ms（仿真常规做法；不动 PID/物理参数）。
        dt = min(dt, 1.0 / 30.0)
        # 固定 tick 累加器（帧率解耦核心）：帧 dt（钳位后）只决定本帧 tick 数 n，
        # 物理/飞控恒按 SIM_TICK 节拍推进——设置页帧率不再改变闭环动力学
        # （clock.tick 整数毫秒量化抖动一并消除；60fps 设置下每帧恰 1 tick，
        # 飞行手感与原 60fps 路径一致）。超限兜底 8 tick 防死亡螺旋
        self._sim_acc += dt
        n = min(int(self._sim_acc / SIM_TICK), 8)
        self._sim_acc -= n * SIM_TICK
        self._flight_time_s += n * SIM_TICK   # 飞行时长按仿真时间累计
        ch = self.app.rc_input.read(dt)
        if self.app.drone_params.virtual_fc:
            # MSP SET 写参数即时生效（Task 19/21）：地面站写参数 → on_change 置
            # 脏标志 → 此处帧首重建飞控参数。真机写 PID 不 disarm——set_params
            # 重建后回写 armed（armed 为普通逐帧标志，回写无不变量破坏；重建清
            # PID 积分按重建语义保留：参数已变清零合理）。Task 54 起反乌龟解锁
            # 锁存随 armed 一并回写（固件写参数不重估 flipOverAfterCrashActive
            # ——否则反乌龟救援中地面站写参会静默中断救援）。换预设路径
            # （app.select_drone → 新 fc 实例）不在此列，保持回锁定态
            if self.app.msp_params_dirty:
                was_armed = self._fc.armed
                was_flip = self._fc.flip_over_latched
                self._fc.set_params(self.app.drone_params)
                self._fc.restore_after_rebuild(was_armed, was_flip)
                self.app.msp_params_dirty = False
            # 虚拟飞控接线（Task 14）：aux [-1,1] → µs（1500 中位 ±500）、
            # mode_ranges 求值激活 box 集（帧级 ZOH，同帧多 tick 间不重估；
            # 杆义姿态角在下方 tick 循环内按 bullet 现值逐 tick 重读）。反乌龟
            # 接管门槛 Task 54 起不读 boxes——按解锁锁存判定。
            # 无 ARM box 配置时 eval_boxes 恒空 → 永不解锁（零推）
            aux_us = [1500.0 + v * 500.0 for v in ch.aux]
            boxes = eval_boxes(self.app.drone_params.virtual.mode_ranges, aux_us)
            self._aux_us = aux_us    # 快照缓存（flight_snapshot 消费）
            self._boxes = boxes
        # 固定 tick 循环（帧率解耦核心）：fc.update + world.step 恒按 SIM_TICK
        # 节拍推进 n 次，帧 dt 不再进入闭环——设置页帧率只改渲染节拍。
        # 60fps 设置下每帧恰 1 tick，手感与原 60fps 路径一致
        for _ in range(n):
            if self.app.drone_params.virtual_fc:
                # 杆义姿态角按 tick 重读（bullet 现值，自稳外环/反乌龟消费）；
                # aux/boxes 为帧级 ZOH（上方帧级求值，同帧多 tick 间不重估）
                motors = self._fc.update(ch, self._world.gyro_dps, SIM_TICK,
                                         aux_us=aux_us, boxes=boxes,
                                         angles_deg=self._world.euler_stick_deg)
            else:
                # 参数模式：一期三参调用形态，行为零修改
                motors = self._fc.update(ch, self._world.gyro_dps, SIM_TICK)
            # 反乌龟桨反转接线（Task 50 v4；Task 54 锁存同源）：判定读 fc
            # 解锁锁存 flip_over_latched ∧ armed（解锁时刻锁存、飞行中不
            # 跟随开关、加锁清零）。真机靠双向 DShot 反转螺旋桨——倒扣时
            # 推力从压向地面变为抬离地面。每 tick 先于 step 设置（激活帧
            # 电机与推力方向同 tick 生效）；加锁当帧恢复正常推力。参数模式
            # armed 恒 False → 恒 False
            # 反乌龟桨反转标志每 tick 先于 step 设置（激活帧电机与推力方向
            # 同 tick 生效，语义与原实现同帧一致）
            self._world.set_prop_reverse(
                self._fc.flip_over_latched and self._fc.armed)
            self._world.step(self._fc.motor_thrusts(motors), SIM_TICK)
            # 取证记录（FLIGHT_REC=1）：每 tick 位姿/速度/角速度/杆量
            if self._rec is not None:
                q = self._world.quat
                self._rec.write(json.dumps({
                    "t": round(self._flight_time_s, 4),
                    "pos": [round(c, 4) for c in self._world.pos],
                    "quat": [round(q.x, 5), round(q.y, 5),
                             round(q.z, 5), round(q.w, 5)],
                    "vel": [round(c, 3) for c in self._world.velocity],
                    "gyro": [round(c, 1) for c in self._world.gyro_dps],
                    "ch": [round(ch.throttle, 3), round(ch.yaw, 3),
                           round(ch.roll, 3), round(ch.pitch, 3)],
                    "armed": self._fc.armed,
                }) + "\n")
        # OSD 距离状态（Task 47）：world.step 后位置已就绪，armed 为 fc.update
        # 后的本帧值——解锁沿/累计/冻结判定见 _track_flight_distance
        self._track_flight_distance(self._world.pos, self._fc.armed)
        # 飞行统计（Task 52）：同帧沿检测——解锁→加锁沿 total_flights +1
        # （反乌龟解锁锁存例外，core.c L469 同款门控；Task 54 起锁存与
        # drone.py 同源）
        self._track_flight_stats(self._fc.armed)
        # 相机同步：位姿 + 机头对齐 + 摄像头仰角合成（纯函数在 physics.world）
        # 相机按 tick 间插值位姿渲染（render_pose prev→cur，alpha=acc/SIM_TICK）：
        # 固定 tick 后 bullet 状态以 60Hz 更新，高帧率渲染设置下不插值会帧重复顿挫
        rpos, rquat = self._world.render_pose(self._sim_acc / SIM_TICK)
        cam = compose_camera_quat(rquat,
                                  self.app.drone_params.camera_angle_deg)
        px, py, pz = bullet_to_render(rpos)
        soup3D.camera.goto(px, py, pz)
        soup3D.camera.turn_quat(cam)
        soup3D.camera.update()
        self._ch = ch   # HUD 用

    def render_scene(self):
        """逐帧直渲场景模型（spike：显示列表路径失效；open_obj 按材质分面，
        bando 约 15 个 Face → 每帧约 15 次 draw call）。
        用 rend_cached（VENDORED 引擎缓存机制）：静态地图的顶点拆分与
        VAO/VBO 仅首次建立，逐帧只 bind + 绘制（剖析 44ms → 0.3ms）；
        use/unuse 与 is_dirty→update 保持每帧执行（uniform/矩阵仍需刷新）"""
        if self.stage != "flying" or self._model is None:
            return
        for face in self._model.faces:
            surface = face.surface
            if surface.is_dirty():
                surface.update()
            surface.use()
            surface.rend_cached(face.mode, face.vertex)
            surface.unuse()

    # ---- UI ----
    def render_ui(self):
        if self.stage == "select":
            self.render_select()
            if self.error:
                imgui.set_next_window_position(u(60), u(60))
                imgui.begin("加载失败", flags=imgui.WINDOW_NO_COLLAPSE)
                imgui.text(self.error)
                if imgui.button("知道了", u(140), u(32)):
                    self.error = ""
                imgui.end()
            return
        if self.stage == "loading":
            imgui.set_next_window_position(u(60), u(60))
            imgui.begin("加载中", flags=imgui.WINDOW_NO_COLLAPSE)
            imgui.text(f"正在加载 {self._scene.name} …")
            imgui.end()
            return
        if self.app.drone_params.virtual_fc:
            # 虚拟飞控模式（设计 §9）：一期 HUD（含输入徽标）隐藏，改渲 OSD。
            # 隐含约定（Task 39 点名）：此处分派每帧读 drone_params.virtual_fc，
            # 而图集烘焙脏标记是 enter/reload 时的缓存判定——两处同源不同时。
            # 依据是 virtual_fc 仅参数页转换可变（见 enter 的脏标记注释），
            # 飞行页存活期间两值恒一致；若未来允许飞行中切换 virtual_fc，
            # 须同步引入烘焙脏标记失效（仅改此处会 OSD/HUD 双缺或双绘）
            self._render_osd()
        else:
            self._render_hud()
        self._render_esc_menu()

    def _render_osd(self):
        """OSD 渲染（设计 §9 / Task 28/39）：compose_state → build_frame →
        imgui 背景绘制层逐格位图绘制。用背景层（而非窗口）不产生
        imgui item，且保证 Esc 弹窗/任意 UI 绘制在 OSD 之上。图集缺失
        （资产/GL 失败）静默不画。格尺寸/锚点按窗口自适应铺满（真机
        全屏叠加观感，见 _glyph_cell），每帧读 io 即时值——resize 自动跟随。"""
        atlas = self._osd_atlas
        if atlas is None:
            return
        state = compose_state(self.app.drone_params, self.flight_snapshot(),
                              self.app.settings.current_drone_id,
                              time.monotonic())
        frame = build_frame(state)
        if not frame:
            return
        tex, aw, ah = atlas
        cw, ch, ox, oy = _glyph_cell(*imgui.get_io().display_size)
        u_step, v_step = self.OSD_GLYPH_W / aw, self.OSD_GLYPH_H / ah
        # 白色调制：swizzle 后采样恒 (1,1,1,a)，字形透明度全由图集 alpha 决定
        col = imgui.get_color_u32_rgba(1.0, 1.0, 1.0, 1.0)
        # blink 相位关防御跳过（生成端已过滤 DISARMED 相位关，此处按同源
        # now_s 再判一次，双保险不引入第二时钟）
        phase_off = int(state["now_s"] * 2) % 2 == 1
        dl = imgui.get_background_draw_list()
        for (c, r), (glyph, blink) in frame.items():
            if blink and phase_off:
                continue
            u0 = (glyph % ATLAS_COLS) * u_step
            v0 = (glyph // ATLAS_COLS) * v_step
            x, y = ox + c * cw, oy + r * ch
            dl.add_image(tex, (x, y), (x + cw, y + ch),
                         (u0, v0), (u0 + u_step, v0 + v_step), col)

    def _render_hud(self):
        v = self._world.velocity
        speed = math.sqrt(sum(x * x for x in v))
        alt = self._world.pos[2]
        roll, pitch, yaw = self._world.euler_deg
        imgui.set_next_window_position(u(24), u(24))
        imgui.push_style_color(imgui.COLOR_WINDOW_BACKGROUND, 0, 0, 0, 0.35)
        imgui.begin("##hud", flags=imgui.WINDOW_NO_TITLE_BAR |
                    imgui.WINDOW_ALWAYS_AUTO_RESIZE)
        ch = getattr(self, "_ch", None)
        imgui.text(f"高度 {alt:5.1f} m   速度 {speed:4.1f} m/s")
        imgui.text(f"横滚 {roll:5.0f}° 俯仰 {pitch:5.0f}° 偏航 {yaw:5.0f}°")
        if ch:
            imgui.text(f"油门 {ch.throttle * 100:3.0f}%")
        # 输入来源徽标（一期遗留 minor）：键盘显示固定文案，手柄显示设备名；
        # 主四通道有未绑定路数时追加计数提示（全绑定不显示）。Task 38 一致性
        # 修正：配置为手柄但实际 rc_input 已回落键盘（拔出）时如实显示键盘
        # 兜底——徽标跟随实际输入源，消除配置态/实际态不一致窗口期（自动
        # 拾取成功后两者天然同步一致）
        rc = self.app.rc_config
        if rc.mode == "joystick" and not isinstance(self.app.rc_input, JoystickInput):
            src = "键盘（手柄已断开）"
        else:
            src = "键盘" if rc.mode == "keyboard" else (rc.device_name or "手柄")
        # 未绑定计数仅 joystick 模式显示（键盘模式无绑定概念，噪音抑制——
        # Task 9 deferred → M6 顺路批收口）
        n = 0
        if rc.mode == "joystick":
            n = sum(1 for c in ("throttle", "yaw", "roll", "pitch")
                    if rc.mappings[c].axis < 0)
        imgui.text(f"输入: {src}" + (f"（{n} 路未绑定）" if n else ""))
        imgui.text("Esc 暂停菜单")
        imgui.end()
        imgui.pop_style_color(1)

    def _render_esc_menu(self):
        if not self._esc_open:
            return
        imgui.open_popup("暂停") if not imgui.is_popup_open("暂停") else None
        if imgui.begin_popup_modal("暂停", flags=imgui.WINDOW_ALWAYS_AUTO_RESIZE)[0]:
            if imgui.button("继续飞行", u(220), u(44)):
                self._esc_open = False; self._paused = False
                imgui.close_current_popup()
            if imgui.button("重置位置", u(220), u(44)):
                self._world.reset(self._scene.spawn)
                if self._rec is not None:
                    self._rec.write(json.dumps({"event": "reset"}) + "\n")
                self._fc.disarm()   # 位置重置联动加锁（Task 14）：重置后须重新解锁
                br = getattr(self._fc, "battery_reset", None)
                if br is not None:
                    br()            # 重置位置=换新电池（真实电池模拟满电恢复）
                self._esc_open = False; self._paused = False
                imgui.close_current_popup()
            if imgui.button("返回主菜单", u(220), u(44)):
                imgui.close_current_popup()
                self._back()
            imgui.end_popup()
