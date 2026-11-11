"""飞行界面：场景选择 → 花式飞行（相机由 PyBullet 位姿驱动）+ HUD + Esc 菜单"""
import math
import imgui
import pygame
import soup3D
from .base import Screen
from physics.drone import FlightController
from physics.world import FlightWorld, bullet_to_render, compose_camera_quat
from cover_cache import (cover_uv_crop, load_cover_texture, free_cover_texture,
                         COVER_BOX_W, COVER_BOX_H)

class FlightScreen(Screen):
    # 网格卡片布局：卡片宽/封面高复用 cover_cache.COVER_BOX_W/H，只留两个
    CARD_NAME_H = 36     # 卡片标题条高（含文字垂直居中）
    CARD_GAP = 16        # 卡片间距
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
        self._esc_open = False

    def exit(self):
        self._teardown_scene()      # 离开飞行必须清理 GL 与物理资源
        self._release_covers()      # 封面纹理同属 GL 资源：FlightScreen 每次进入都新建实例，不释放会泄漏

    def reload(self):
        """窗口重建后重载：新 GL 上下文 → 封面纹理句柄全部失效，必须释放重载"""
        self._release_covers()
        if self.stage == "flying" and self._scene is not None:
            self._teardown_scene()
            self.stage = "loading"

    def _release_covers(self):
        """释放全部封面纹理（GL 句柄不跨 GL 上下文复用；窗口重建/退出时调用）"""
        for entry in self._covers.values():
            if entry:
                free_cover_texture(entry[0])
        self._covers.clear()

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
        cols = max(1, int((avail + self.CARD_GAP) // (COVER_BOX_W + self.CARD_GAP)))
        for i, s in enumerate(valid):
            self._scene_card(s)
            if (i + 1) % cols and i + 1 < len(valid):
                imgui.same_line()
        # 无效场景保留文字行
        for s in self.app.scenes:
            if not s.valid:
                imgui.text(f"{s.name}（无效：{s.error}）已跳过")
        imgui.separator()
        if imgui.button("返回主菜单", 200, 40):
            self._back()
        imgui.end()

    def _scene_card(self, s):
        # 单卡片：invisible_button 承接整卡点击/悬停，draw list 直绘封面与标题
        card_h = COVER_BOX_H + self.CARD_NAME_H
        clicked = imgui.invisible_button(f"##card_{s.dir.name}", COVER_BOX_W, card_h)
        hovered = imgui.is_item_hovered()
        p = imgui.get_item_rect_min()
        e = imgui.get_item_rect_max()
        dl = imgui.get_window_draw_list()
        col_text = imgui.get_color_u32_rgba(0.92, 0.92, 0.95, 1.0)
        col_dim = imgui.get_color_u32_rgba(0.55, 0.55, 0.58, 1.0)
        col_bg = imgui.get_color_u32_rgba(0.10, 0.10, 0.12, 1.0)
        col_border = imgui.get_color_u32_rgba(1.0, 1.0, 1.0, 0.7 if hovered else 0.25)
        dl.add_rect_filled(p.x, p.y, e.x, e.y, col_bg)
        strip_top = p.y + COVER_BOX_H
        cover = self._get_cover(s)
        if cover:
            tid, w, h = cover
            uv = cover_uv_crop(w, h, COVER_BOX_W, COVER_BOX_H)
            dl.add_image(tid, (p.x, p.y), (p.x + COVER_BOX_W, strip_top),
                         uv[:2], uv[2:])
        else:
            dl.add_rect_filled(p.x, p.y, p.x + COVER_BOX_W, strip_top,
                               imgui.get_color_u32_rgba(0.16, 0.16, 0.18, 1.0))
            self._draw_centered_text(dl, p.x, p.y, COVER_BOX_W, COVER_BOX_H,
                                     col_dim, "无封面")
        self._draw_centered_text(dl, p.x, strip_top, COVER_BOX_W,
                                 self.CARD_NAME_H, col_text, s.name)
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

    # ---- 每帧 ----
    def update(self, dt: float):
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
        ch = self.app.rc_input.read(dt)
        motors = self._fc.update(ch, self._world.gyro_dps, dt)
        self._world.step(self._fc.motor_thrusts(motors), dt)
        # 相机同步：位姿 + 机头对齐 + 摄像头仰角合成（纯函数在 physics.world）
        cam = compose_camera_quat(self._world.quat,
                                  self.app.drone_params.camera_angle_deg)
        px, py, pz = bullet_to_render(self._world.pos)
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
                imgui.set_next_window_position(60, 60)
                imgui.begin("加载失败", flags=imgui.WINDOW_NO_COLLAPSE)
                imgui.text(self.error)
                if imgui.button("知道了", 140, 32):
                    self.error = ""
                imgui.end()
            return
        if self.stage == "loading":
            imgui.set_next_window_position(60, 60)
            imgui.begin("加载中", flags=imgui.WINDOW_NO_COLLAPSE)
            imgui.text(f"正在加载 {self._scene.name} …")
            imgui.end()
            return
        self._render_hud()
        self._render_esc_menu()

    def _render_hud(self):
        v = self._world.velocity
        speed = math.sqrt(sum(x * x for x in v))
        alt = self._world.pos[2]
        roll, pitch, yaw = self._world.euler_deg
        imgui.set_next_window_position(24, 24)
        imgui.push_style_color(imgui.COLOR_WINDOW_BACKGROUND, 0, 0, 0, 0.35)
        imgui.begin("##hud", flags=imgui.WINDOW_NO_TITLE_BAR |
                    imgui.WINDOW_ALWAYS_AUTO_RESIZE)
        ch = getattr(self, "_ch", None)
        imgui.text(f"高度 {alt:5.1f} m   速度 {speed:4.1f} m/s")
        imgui.text(f"横滚 {roll:5.0f}° 俯仰 {pitch:5.0f}° 偏航 {yaw:5.0f}°")
        if ch:
            imgui.text(f"油门 {ch.throttle * 100:3.0f}%")
        imgui.text("Esc 暂停菜单")
        imgui.end()
        imgui.pop_style_color(1)

    def _render_esc_menu(self):
        if not self._esc_open:
            return
        imgui.open_popup("暂停") if not imgui.is_popup_open("暂停") else None
        if imgui.begin_popup_modal("暂停", flags=imgui.WINDOW_ALWAYS_AUTO_RESIZE)[0]:
            if imgui.button("继续飞行", 220, 44):
                self._esc_open = False; self._paused = False
                imgui.close_current_popup()
            if imgui.button("重置位置", 220, 44):
                self._world.reset(self._scene.spawn)
                self._esc_open = False; self._paused = False
                imgui.close_current_popup()
            if imgui.button("返回主菜单", 220, 44):
                imgui.close_current_popup()
                self._back()
            imgui.end_popup()
