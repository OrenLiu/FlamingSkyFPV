"""无人机参数界面：二期列表/编辑子状态机（设计文档第 6 节 + 二期预设管理）。
列表态：预设清单 + 整行选中当前机 + 编辑/复制/删除；编辑态暂用一期表单
（Task 5 重构为双模式表单）。参数保存即时生效。
用户裁定（一期沿用）：PID 与 rate 三轴独立 + 全参数数值输入框（量级跨度大，
滑条精度不足）。"""
import imgui
import pygame
from .base import Screen
from config_store import (DroneParams, PIDGains, RateParams, default_drone_params,
                          DEFAULT_PRESET_ID, copy_preset, delete_preset,
                          list_preset_ids, load_preset, save_preset)

# 预设目录：与 app.DRONES_DIR 同值（screens 不 import app，保持依赖单向）
DRONES_DIR = "config/drones"

RATE_TYPES = ["betaflight_actual", "linear"]
AXES = ["roll", "pitch", "yaw"]
AXIS_CN = {"roll": "横滚", "pitch": "俯仰", "yaw": "偏航"}

ROW_H = 36        # 列表行高（行内按钮同高对齐）
ROW_BTN_W = 64    # 行内按钮宽（编辑/复制/删除）

def _input(label, v, step, fmt="%.5g"):
    """数值输入框助手：pyimgui 2.0 返回 (changed, value)，changed=False 时回写幂等"""
    _, nv = imgui.input_float(label, v, step, step * 10.0, fmt)
    return nv

def _clone_params(src: DroneParams) -> DroneParams:
    """工作副本/保存快照深拷贝：嵌套 pid/rate 字典逐轴重建，
    避免参数页编辑直接污染运行中的 app.drone_params"""
    p = default_drone_params()
    p.__dict__.update(src.__dict__)
    p.pid = {k: PIDGains(**vars(v)) for k, v in src.pid.items()}
    p.rate = {k: RateParams(**vars(v)) for k, v in src.rate.items()}
    return p

class DroneParamsScreen(Screen):
    """stage 子状态机（FlightScreen 同款）："list" 预设列表 | "edit" 参数表单"""

    def enter(self, app):
        self.app = app
        self.stage = "list"       # enter 默认列表态；主菜单入口不变
        self._ids = list_preset_ids(DRONES_DIR)   # 列表缓存，操作后重取
        self._edit_id = None      # 编辑态目标预设 id
        self._p = None            # 编辑态工作副本
        self._copy_src = None     # 复制弹窗源 id（None=弹窗未开）
        self._copy_buf = ""       # 复制弹窗新代号输入缓冲
        self._del_target = None   # 删除弹窗目标 id（None=弹窗未开）

    def handle_event(self, event):
        if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
            if self._copy_src is not None or self._del_target is not None:
                # 弹窗开时 Esc 只关弹窗：清标记后不再提交 begin_popup_modal，
                # imgui 帧末回收未提交弹窗（flight.py Esc 关「暂停」同机制）
                self._copy_src = None
                self._del_target = None
            elif self.stage == "edit":
                self.stage = "list"
                self._refresh_ids()
            else:
                self._back()

    def _back(self):
        from .main_menu import MainMenuScreen
        self.app.switch(MainMenuScreen())

    def _refresh_ids(self):
        """复制/删除/编辑返回后重取列表（数据层以目录文件为准）"""
        self._ids = list_preset_ids(DRONES_DIR)

    def render_ui(self):
        if self.stage == "list":
            self._render_list()
        else:
            self._render_edit()

    # ---- 列表态 ----
    def _render_list(self):
        imgui.set_next_window_position(30, 30)
        io = imgui.get_io()
        # 高度必须固定为视口可用高度（上 30 + 下 30 边距）：0=自适应时 imgui
        # 把窗口钳到视口高减安全边距，顶部又偏移 30 → 底部永远压出屏幕外；
        # 固定高度后 imgui 自动为溢出内容出滚动条（一期参数窗口同做法）
        imgui.set_next_window_size(620, io.display_size[1] - 60)
        imgui.begin("无人机参数", flags=imgui.WINDOW_NO_COLLAPSE)
        imgui.text("点击行选中当前无人机；复制可新建预设")
        imgui.separator()
        avail = imgui.get_content_region_available_width()
        for pid in self._ids:
            self._preset_row(pid, avail)
        imgui.separator()
        if imgui.button("返回", 180, 40):
            self._back()
        imgui.end()
        # 模态弹窗在主窗口 end 之后、栈根处开（flight.py _render_esc_menu 同位置）
        self._render_copy_modal()
        self._render_delete_modal()

    def _preset_row(self, pid, avail):
        """单行：左段 invisible_button 承接整行选中点击，draw list 直绘底色/
        代号/标签（直绘不产生 imgui item，不与按钮抢点击；flight.py _scene_card
        同手法）；右段编辑/复制/删除三按钮。push_id 隔离各行同名控件"""
        gap = imgui.get_style().item_spacing.x
        sel_w = max(80.0, avail - (ROW_BTN_W * 3 + gap * 3))
        imgui.push_id(pid)
        is_cur = pid == self.app.settings.current_drone_id
        clicked = imgui.invisible_button("##sel", sel_w, ROW_H)
        hovered = imgui.is_item_hovered()
        p = imgui.get_item_rect_min()
        e = imgui.get_item_rect_max()
        dl = imgui.get_window_draw_list()
        # 当前机绿底高亮，悬停亮边
        if is_cur:
            bg = imgui.get_color_u32_rgba(0.13, 0.30, 0.16, 1.0)
        elif hovered:
            bg = imgui.get_color_u32_rgba(0.16, 0.16, 0.19, 1.0)
        else:
            bg = imgui.get_color_u32_rgba(0.10, 0.10, 0.12, 1.0)
        edge = 0.7 if is_cur else (0.45 if hovered else 0.2)
        dl.add_rect_filled(p.x, p.y, e.x, e.y, bg)
        dl.add_rect(p.x, p.y, e.x, e.y, imgui.get_color_u32_rgba(1.0, 1.0, 1.0, edge))
        # 代号 +「当前无人机」标签垂直居中
        name_size = imgui.calc_text_size(pid)
        ty = p.y + (ROW_H - name_size.y) / 2
        dl.add_text(p.x + 10, ty, imgui.get_color_u32_rgba(0.92, 0.92, 0.95, 1.0), pid)
        if is_cur:
            dl.add_text(p.x + 10 + name_size.x + 12, ty,
                        imgui.get_color_u32_rgba(0.45, 0.90, 0.55, 1.0), "当前无人机")
        if clicked and not is_cur:
            self.app.select_drone(pid)   # 换机 + current_drone_id 落盘（Task 3 接口）
        imgui.same_line()
        if imgui.button("编辑", ROW_BTN_W, ROW_H):
            self._edit_id = pid
            self._p = _clone_params(load_preset(DRONES_DIR, pid))
            self.stage = "edit"
        imgui.same_line()
        if imgui.button("复制", ROW_BTN_W, ROW_H):
            self._copy_src = pid
            self._copy_buf = pid    # 预填源代号：直接确认即得 -2 序号新预设
        imgui.same_line()
        if imgui.button("删除", ROW_BTN_W, ROW_H):
            self._del_target = pid
        imgui.pop_id()

    def _render_copy_modal(self):
        if self._copy_src is None:
            return
        imgui.open_popup("复制无人机") if not imgui.is_popup_open("复制无人机") else None
        if imgui.begin_popup_modal("复制无人机", flags=imgui.WINDOW_ALWAYS_AUTO_RESIZE)[0]:
            imgui.text(f"复制「{self._copy_src}」为新预设")
            _, self._copy_buf = imgui.input_text("新代号", self._copy_buf, 64)
            imgui.text("重名时自动追加 -2/-3 序号")
            name_ok = bool(self._copy_buf.strip())
            if not name_ok:
                imgui.text("代号不能为空")
            if imgui.button("确认", 160, 40) and name_ok:
                copy_preset(DRONES_DIR, self._copy_src, self._copy_buf)
                self._refresh_ids()
                self._copy_src = None
                imgui.close_current_popup()
            imgui.same_line()
            if imgui.button("取消", 160, 40):
                self._copy_src = None
                imgui.close_current_popup()
            imgui.end_popup()

    def _render_delete_modal(self):
        if self._del_target is None:
            return
        imgui.open_popup("删除无人机") if not imgui.is_popup_open("删除无人机") else None
        if imgui.begin_popup_modal("删除无人机", flags=imgui.WINDOW_ALWAYS_AUTO_RESIZE)[0]:
            imgui.text(f"删除无人机：{self._del_target}？")
            if imgui.button("确认删除", 160, 40):
                pid = self._del_target
                delete_preset(DRONES_DIR, pid)
                if pid == self.app.settings.current_drone_id:
                    # 删当前机 → 回落列表第一台；列表删空 → 重建默认机
                    # （App.__init__ 空列表同语义）
                    ids = list_preset_ids(DRONES_DIR)
                    if not ids:
                        save_preset(DRONES_DIR, DEFAULT_PRESET_ID, DroneParams())
                        ids = [DEFAULT_PRESET_ID]
                    self.app.select_drone(ids[0])   # 回落 + 记忆落盘
                self._refresh_ids()
                self._del_target = None
                imgui.close_current_popup()
            imgui.same_line()
            if imgui.button("取消", 160, 40):
                self._del_target = None
                imgui.close_current_popup()
            imgui.end_popup()

    # ---- 编辑态（一期表单暂留为编辑内容，Task 5 重构为双模式） ----
    def _render_edit(self):
        imgui.set_next_window_position(30, 30)
        io = imgui.get_io()
        # 高度固定理由同列表态：0=自适应会把底部按钮压出屏幕外
        imgui.set_next_window_size(620, io.display_size[1] - 60)
        imgui.begin("无人机参数", flags=imgui.WINDOW_NO_COLLAPSE)

        imgui.text(f"正在编辑：{self._edit_id}")
        p = self._p
        imgui.text("Rate（遥控曲线，三轴独立）")
        cur = RATE_TYPES.index(p.rate_type) if p.rate_type in RATE_TYPES else 0
        _, picked = imgui.combo("Rate 类型", cur, RATE_TYPES)
        p.rate_type = RATE_TYPES[picked]
        # push_id 使三轴可复用同一组标签（imgui 以 label 为控件 ID）
        for ax in AXES:
            r = p.rate[ax]
            imgui.push_id(ax)
            imgui.text(AXIS_CN[ax])
            r.rc_rate = _input("RC Rate", r.rc_rate, 0.05)
            r.super_rate = _input("Super Rate", r.super_rate, 0.01)
            r.expo = _input("Expo", r.expo, 0.01)
            if p.rate_type == "linear":
                r.linear_max_deg = _input("线性满杆°/s", r.linear_max_deg, 10.0)
            imgui.pop_id()
        imgui.separator()

        imgui.text("PID（三轴独立）")
        # 增益为 BF 量级刻度（默认 roll/pitch 40/40/0.4、yaw 400/100/0，内部
        # ÷PID_MIX_SCALING 换算到电机域，见 physics.drone）；P/I step=1、D step=0.1
        for ax in AXES:
            g = p.pid[ax]
            imgui.push_id(ax)
            imgui.text(AXIS_CN[ax])
            g.p = _input("P", g.p, 1.0)
            g.i = _input("I", g.i, 1.0)
            g.d = _input("D", g.d, 0.1)
            imgui.pop_id()
        # TPA：油门 PID 衰减（高油门控制权限随推力²上升，补偿悬停标定 PID 越界，
        # 见 physics.drone.tpa_factor）；衰减 % step=1、断点 step=0.05
        p.tpa_percent = _input("TPA 衰减 %", p.tpa_percent, 1.0)
        p.tpa_breakpoint = _input("TPA 断点", p.tpa_breakpoint, 0.05)
        imgui.separator()

        imgui.text("机体")
        p.weight_g = _input("重量 (g)", p.weight_g, 10.0)
        # input_int 返回 (changed, int)，与 input_float 同模式；step=50、快进 500
        kv_changed, p.motor_kv = imgui.input_int("电机 KV", int(p.motor_kv),
                                                 50, 500)
        p.motor_kv = int(p.motor_kv)
        suggested = p.motor_kv / 250.0
        imgui.text(f"KV 建议推重比: {suggested:.1f}")
        p.thrust_ratio = _input("推重比", p.thrust_ratio, 0.1)
        # 电池电压 V：推力按 (V/14.8)² 缩放（参考 4S 标称；满电 4S=16.8、3S=11.1）
        p.battery_voltage = _input("电池电压 (V)", p.battery_voltage, 0.1)
        # 线性空气阻力 N·s/m：默认 1.2 为默认机标定值，轻量高推机按手感调低
        p.drag_k = _input("空气阻力", p.drag_k, 0.1)
        p.camera_angle_deg = _input("摄像头角度 (°)", p.camera_angle_deg, 1.0)
        imgui.separator()

        # 过渡期保存路径（一期遗留，Task 5 改为按 _edit_id 落预设文件）：
        # 更新运行中参数 + 写旧 drone.json
        if imgui.button("保存", 180, 40):
            # 保存独立快照：工作副本继续编辑互不影响
            self.app.drone_params = _clone_params(p)
            self.app.store.save("drone", self.app.drone_params)
        imgui.same_line()
        if imgui.button("恢复默认", 180, 40):
            self._p = default_drone_params()
        imgui.same_line()
        if imgui.button("返回列表", 180, 40):
            self.stage = "list"
            self._refresh_ids()
        imgui.end()
