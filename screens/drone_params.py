"""无人机参数界面：二期列表/编辑子状态机（设计文档第 6 节 + 二期预设管理）。
列表态：预设清单 + 整行选中当前机 + 编辑/复制/删除；编辑态双模式表单：
参数模式（一期全参数 + 代号改名）| 虚拟飞控模式（仅硬件 7 项，调参经
snapshot_tune/sync_tune_to_ui 转入 virtual 域由地面站调整，转换语义 Task 1
单测覆盖）。保存一律落预设文件（save_preset），一期 store.save("drone")
路径已退役。用户裁定（一期沿用）：PID 与 rate 三轴独立 + 全参数数值输入框
（量级跨度大，滑条精度不足）。"""
import imgui
import pygame
from .base import Screen
from config_store import (DRONES_DIR, DroneParams, PIDGains, RateParams,
                          TuneParams, VirtualFCParams, default_drone_params,
                          DEFAULT_PRESET_ID, copy_preset, delete_preset,
                          list_preset_ids, load_preset, save_preset,
                          sanitize_preset_id, set_cells_auto, set_cells_manual)

RATE_TYPES = ["betaflight_actual", "linear"]
AXES = ["roll", "pitch", "yaw"]
AXIS_CN = {"roll": "横滚", "pitch": "俯仰", "yaw": "偏航"}

ROW_H = 36        # 列表行高（行内按钮同高对齐）
ROW_BTN_W = 64    # 行内按钮宽（编辑/复制/删除）
BTN_W = 180       # 编辑态底部常规按钮宽（一期尺寸）
BTN_H = 40
CONV_BTN_W = 220  # 转换/地面站按钮宽（7 字中文文案在 24px 字体下不溢出）

def _input(label, v, step, fmt="%.5g"):
    """数值输入框助手：pyimgui 2.0 返回 (changed, value)，changed=False 时回写幂等"""
    _, nv = imgui.input_float(label, v, step, step * 10.0, fmt)
    return nv

def _clone_params(src: DroneParams) -> DroneParams:
    """工作副本/保存快照深拷贝：嵌套 pid/rate/virtual 段逐层重建，
    避免参数页编辑直接污染运行中的 app.drone_params（含地面站调参的
    virtual.tune 域，两域互不共享嵌套实例）"""
    p = default_drone_params()
    p.__dict__.update(src.__dict__)
    p.pid = {k: PIDGains(**vars(g)) for k, g in src.pid.items()}
    p.rate = {k: RateParams(**vars(r)) for k, r in src.rate.items()}
    # virtual 段深拷贝：tune 逐轴 dataclass 重建（同上手法）；mode_ranges
    # 浅拷贝（条目按不可变约定使用）；osd 两字典浅拷贝（值为坐标/开关）
    vir = src.virtual
    t = TuneParams(**vars(vir.tune))
    t.rate = {k: RateParams(**vars(r)) for k, r in vir.tune.rate.items()}
    t.pid = {k: PIDGains(**vars(g)) for k, g in vir.tune.pid.items()}
    p.virtual = VirtualFCParams(tune=t, mode_ranges=list(vir.mode_ranges),
                                osd_layout=dict(vir.osd_layout),
                                osd_enabled=dict(vir.osd_enabled),
                                craft_name=vir.craft_name, cells=vir.cells,
                                cells_auto=vir.cells_auto)
    return p

class DroneParamsScreen(Screen):
    """stage 子状态机（FlightScreen 同款）："list" 预设列表 | "edit" 参数表单"""

    def enter(self, app):
        self.app = app
        self.stage = "list"       # enter 默认列表态；主菜单入口不变
        self._ids = list_preset_ids(DRONES_DIR)   # 列表缓存，操作后重取
        self._edit_id = None      # 编辑态目标预设 id
        self._p = None            # 编辑态工作副本
        self._name_buf = ""       # 编辑态代号输入缓冲（进入编辑时置为当前 id）
        self._known_ids = set()   # 编辑态代号冲突检测缓存（进入编辑时重建）
        self._conv_ask = False    # 「转换为虚拟飞控」确认弹窗开关
        self._gs_error = None     # 接入地面站的错误信息（None=无；连接提示框
                                  # 由 App 层常驻浮窗渲染，Task 43）
        self._copy_src = None     # 复制弹窗源 id（None=弹窗未开）
        self._copy_buf = ""       # 复制弹窗新代号输入缓冲
        self._del_target = None   # 删除弹窗目标 id（None=弹窗未开）

    def handle_event(self, event):
        if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
            if self._copy_src is not None or self._del_target is not None \
                    or self._conv_ask:
                # 弹窗开时 Esc 只关弹窗：清标记后不再提交 begin_popup_modal，
                # imgui 帧末回收未提交弹窗（flight.py Esc 关「暂停」同机制）
                self._copy_src = None
                self._del_target = None
                self._conv_ask = False
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
        if self._gs_error:
            imgui.text(self._gs_error)   # 列表态错误可见（与编辑态同形态）
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
            self._begin_edit(pid)
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
        if not imgui.is_popup_open("复制无人机"):
            imgui.open_popup("复制无人机")
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
        if not imgui.is_popup_open("删除无人机"):
            imgui.open_popup("删除无人机")
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

    # ---- 编辑态（双模式：参数模式 | 虚拟飞控模式） ----
    def _begin_edit(self, pid):
        """进入编辑态：载入预设为工作副本 + 代号缓冲/冲突缓存初始化"""
        self._edit_id = pid
        self._p = _clone_params(load_preset(DRONES_DIR, pid))
        self._name_buf = pid
        # 冲突检测以目录实时状态为准（self._ids 为列表态缓存，上次编辑改名后
        # 可能过期——真实 UI 流返回列表时会刷新，此处不依赖该前提）
        self._known_ids = set(list_preset_ids(DRONES_DIR))
        self._conv_ask = False
        self.stage = "edit"

    def _persist(self):
        """落盘工作副本到 _edit_id；编辑对象为当前机时同步运行对象——
        参数即时生效（一期语义），并防退出 save_all 把旧运行对象覆盖回
        预设文件（编辑非当前机时不碰运行对象，杜绝跨机串写）"""
        save_preset(DRONES_DIR, self._edit_id, self._p)
        if self._edit_id == self.app.settings.current_drone_id:
            self.app.drone_params = _clone_params(self._p)

    def _save_edit(self):
        """保存按钮：代号经 sanitize 后与旧 id 比较，改名走「新文件 + 删旧
        文件 + 当前机跟随」；冲突（≠自身旧 id 且已被占用）不落盘（渲染层
        已提示「代号已存在」，此处双保险）"""
        new_id = sanitize_preset_id(self._name_buf)
        old_id = self._edit_id
        if new_id == old_id:
            self._persist()
            return
        if new_id in self._known_ids:
            return
        # 先写新文件再删旧文件：中途异常时数据不丢
        save_preset(DRONES_DIR, new_id, self._p)
        delete_preset(DRONES_DIR, old_id)
        self._known_ids.discard(old_id)
        self._known_ids.add(new_id)
        self._edit_id = new_id
        if old_id == self.app.settings.current_drone_id:
            # 当前机改名：记忆指向新 id 即时落盘 + 运行对象换新（防 save_all
            # 把旧对象写进新 id 文件覆盖编辑值）
            self.app.settings.current_drone_id = new_id
            self.app.store.save("settings", self.app.settings)
            self.app.drone_params = _clone_params(self._p)

    def _convert_to_vfc(self):
        """转虚拟飞控：平铺调参字段快照进 virtual.tune（此后调参由地面站
        经 MSP 接管），开关翻转 + 落盘，表单随 virtual_fc 即时切换"""
        self._p.virtual.tune = self._p.snapshot_tune()
        self._p.virtual_fc = True
        self._persist()

    def _convert_to_param(self):
        """转参数模式：virtual.tune 深拷贝回写平铺字段（地面站调参成果带回
        参数页，回写后两域脱钩），开关翻转 + 落盘，表单即时切换"""
        self._p.sync_tune_to_ui()
        self._p.virtual_fc = False
        self._persist()

    def _render_edit(self):
        imgui.set_next_window_position(30, 30)
        io = imgui.get_io()
        # 高度固定理由同列表态：0=自适应会把底部按钮压出屏幕外
        imgui.set_next_window_size(620, io.display_size[1] - 60)
        imgui.begin("无人机参数", flags=imgui.WINDOW_NO_COLLAPSE)

        # 代号（两模式共用）：保存时 sanitize 后与旧 id 比较；冲突即时提示
        _, self._name_buf = imgui.input_text("代号", self._name_buf, 64)
        new_id = sanitize_preset_id(self._name_buf)
        name_conflict = new_id != self._edit_id and new_id in self._known_ids
        if name_conflict:
            imgui.text("代号已存在")
        imgui.separator()

        if self._p.virtual_fc:
            self._render_vfc_form(self._p)
        else:
            self._render_param_form(self._p)

        imgui.separator()
        # 底部按钮：首行三常规按钮（一期布局），次行按模式分派转换/地面站
        if imgui.button("保存", BTN_W, BTN_H) and not name_conflict:
            self._save_edit()
        imgui.same_line()
        if imgui.button("恢复默认", BTN_W, BTN_H):
            # 默认机即参数模式机：virtual_fc 一并回 False，表单随之切回
            self._p = default_drone_params()
        imgui.same_line()
        if imgui.button("返回列表", BTN_W, BTN_H):
            self.stage = "list"
            self._refresh_ids()
        if self._p.virtual_fc:
            if imgui.button("接入地面站", CONV_BTN_W, BTN_H):
                self._gs_start()
            imgui.same_line()
            if imgui.button("转换为参数模式", CONV_BTN_W, BTN_H):
                self._convert_to_param()
        else:
            if imgui.button("转换为虚拟飞控", CONV_BTN_W, BTN_H):
                self._conv_ask = True   # 先确认（有损动作：调参域易手）
        if self._gs_error:
            imgui.text(self._gs_error)
        imgui.end()
        # 弹窗在主窗口 end 之后、栈根处开（flight.py 同位置）；地面站连接
        # 提示框已上移 App 层常驻浮窗（Task 43），本页不再渲染
        self._render_convert_modal()

    def _render_param_form(self, p):
        """参数模式表单：一期全参数表单原样迁移（控件与布局零改动）"""
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
        self._render_body_form(p)

    def _render_vfc_form(self, p):
        """虚拟飞控模式表单：仅硬件参数（Rate/PID/TPA 已转入 virtual 域，
        由地面站经 MSP 调整，参数页不再展示）"""
        imgui.text("虚拟飞控模式：调参请接入地面站调整")
        # F 键提示行（Task 8 deferred → M6 顺路批）：飞行页键盘 AUX 拨动键，
        # 键定义见 input/keyboard.py（F4 避开 F3 调试热键）
        imgui.text("开关通道：F1 解锁 / F2 自稳 / F4 反乌龟（仅键盘模式生效）")
        imgui.text("手柄模式：在「遥控通道设置」页核对 AUX1-4 实时条是否随手柄开关动作，再在地面站 Modes 页把模式区间画在该开关激活位")
        imgui.separator()
        self._render_body_form(p)
        imgui.separator()   # 机体段与电池节数分隔（Task 8 deferred 外观项 → M6 顺路批）
        # 电池节数（virtual 域，MSP 电池配置同源）：地面站电压计算基准。
        # 默认自动识别（Task 46：按电池电压 detect_cell_count，固件同款
        # 公式），只读显示 + 可转手动；手动态 input_int 编辑（0/负钳 1，
        # 防电压计算除零/失真）+ 可恢复自动。切换/重算逻辑走
        # set_cells_manual/set_cells_auto 纯函数（与 MSP 95 同路径）
        if p.virtual.cells_auto:
            imgui.text(f"电池节数：自动识别 {p.virtual.cells}S（依据电池电压）")
            if imgui.button("手动指定", 120, 30):
                set_cells_manual(p, p.virtual.cells)  # 以当前识别值为起点转手动
        else:
            _, cells = imgui.input_int("电池节数", int(p.virtual.cells), 1, 10)
            set_cells_manual(p, max(1, int(cells)))
            imgui.same_line()
            if imgui.button("恢复自动", 120, 30):
                set_cells_auto(p)

    def _render_body_form(self, p):
        """机体硬件参数（两模式共用段，控件同一期）"""
        p.weight_g = _input("重量 (g)", p.weight_g, 10.0)
        # input_int 返回 (changed, int)，与 input_float 同模式；step=50、快进 500
        _, p.motor_kv = imgui.input_int("电机 KV", int(p.motor_kv), 50, 500)
        p.motor_kv = int(p.motor_kv)
        imgui.text(f"KV 建议推重比: {p.motor_kv / 250.0:.1f}")
        p.thrust_ratio = _input("推重比", p.thrust_ratio, 0.1)
        # 电池电压 V：推力按 (V/14.8)² 缩放（参考 4S 标称；满电 4S=16.8、3S=11.1）
        p.battery_voltage = _input("电池电压 (V)", p.battery_voltage, 0.1)
        # 自动识别态同帧重算节数（Task 46）：电压编辑即联动，_render_vfc_form
        # 的节数显示行同帧生效（重算在文本渲染前；非虚拟飞控模式同样维护
        # 派生值，转 vfc 时自洽）
        if p.virtual.cells_auto:
            set_cells_auto(p)
        # 线性空气阻力 N·s/m：默认 1.2 为默认机标定值，轻量高推机按手感调低
        p.drag_k = _input("空气阻力", p.drag_k, 0.1)
        p.camera_angle_deg = _input("摄像头角度 (°)", p.camera_angle_deg, 1.0)

    def _render_convert_modal(self):
        """「转换为虚拟飞控」确认弹窗（有损动作：调参域易手，先确认）"""
        if not self._conv_ask:
            return
        if not imgui.is_popup_open("转换为虚拟飞控"):
            imgui.open_popup("转换为虚拟飞控")
        if imgui.begin_popup_modal("转换为虚拟飞控",
                                   flags=imgui.WINDOW_ALWAYS_AUTO_RESIZE)[0]:
            imgui.text("是否转换为虚拟飞控？")
            imgui.text("转换后，需要进入 Betaflight 地面站对无人机进行调整。")
            if imgui.button("确认", 160, 40):
                self._conv_ask = False
                self._convert_to_vfc()
                imgui.close_current_popup()
            imgui.same_line()
            if imgui.button("取消", 160, 40):
                self._conv_ask = False
                imgui.close_current_popup()
            imgui.end_popup()

    # ---- 地面站（VfcServer 由 Task 16 挂接 app.msp_server，本类只按
    # start 契约调用，属性缺失全防御。连接提示框与断开入口均由 App 层
    # 常驻浮窗承担——Task 43，本类只剩「接入地面站」功能入口） ----
    def _gs_start(self):
        """接入地面站：启动 VfcServer（TCP + PTY + WS 三传输）；未挂接或
        端口占用（OSError）→ 错误提示（按钮旁显示），不崩溃不改变连接
        状态。启动成功后提示框由 App 层浮窗跨页常驻显示"""
        srv = getattr(self.app, "msp_server", None)
        if srv is None:
            self._gs_error = "地面站服务尚未接入，请等待后续版本"
            return
        try:
            srv.start()
        except OSError as e:
            self._gs_error = f"接入失败：{e}"
            return
        self._gs_error = None
