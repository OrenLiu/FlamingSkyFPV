"""遥控通道设置：按钮式通道映射 + AUX 同构行 + 配置向导（回中流程与摇杆演示图）"""
import logging
import math

import imgui
import pygame
from .base import Screen
from config_store import AxisBinding
from input.channels import NullInput
from input.joystick import JoystickInput
from input.wizard import (AxisCapture, MODE_STICKS, DIRECTION, STICK_AXIS,
                          demo_motion, verify_offset, channel_inverted)
from ui_scale import u

log = logging.getLogger(__name__)

CHANNELS = ["throttle", "yaw", "roll", "pitch"]
CHANNEL_CN = {"throttle": "油门", "yaw": "偏航", "roll": "横滚", "pitch": "俯仰"}

AUX_KEYS = ("aux1", "aux2", "aux3", "aux4")


# 行列位与演示图几何常量（imgui 坐标系 y 向下；主通道行与 AUX 行共用列位）
_COL_BTN = 90     # 第 2 列：主通道绑定按钮 / AUX 类型下拉 x 偏移
_COL_PREV = 240   # 第 3 列：主通道预览 / AUX 编号按钮 x 偏移
_COL_TAIL = 400   # 第 4 列：主通道反向 / AUX 预览 x 偏移
_DEMO_SIZE = 100  # 演示图单框边长
_DEMO_GAP = 46    # 两框间距
_DEMO_REACH = 40  # 点最大位移
_DEMO_DOT = 7     # 点半径
_DEMO_PERIOD = 1.2  # 捕获步演示动画周期（秒）

def joystick_devices():
    """枚举当前手柄 [(index, name)]"""
    pygame.joystick.init()
    return [(i, pygame.joystick.Joystick(i).get_name())
            for i in range(pygame.joystick.get_count())]

def _bar(label, value, lo, hi):
    """通道预览条"""
    imgui.text(f"{label}")
    imgui.same_line(u(90))
    # pyimgui 2.0 运行期调整：progress_bar 第二参为 size 元组而非 (size_x, size_y)，
    # brief 原式 0.0,18 传参报 TypeError，按 brief 注用仅 fraction 形式
    imgui.progress_bar((value - lo) / (hi - lo))

class RCSetupScreen(Screen):
    def enter(self, app):
        self.app = app
        self._dev_index = 0
        self._msg = ""
        self._dt = 0.0   # 最近一帧时长（update 缓存，预览条用）
        self._wizard = None   # 向导状态机（非 None 时 render_ui 改渲染向导界面）
        self._listen = None      # 映射监听中的通道键（主通道或 auxN；None=未监听）
        self._listen_kind = None # 监听类型："axis" 主通道拨杆 / "auto" AUX 自动识别
        self._listen_cap = None  # 轴监听捕获器（auto/axis 共用）
        self._listen_base = frozenset()   # 按钮基线：begin 时已按住的按钮（随松开收缩）
        self._listen_exbtns = frozenset() # 永久排除按钮：其他 AUX 已绑按钮
        self._anim_t = 0.0       # 向导演示动画时钟（update 累加）

    def update(self, dt: float):
        self._dt = dt   # 主循环在 render_ui 之前调用，同帧预览条可拿到真实 dt
        self._anim_t += dt

    def handle_event(self, event):
        if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
            if self._listen is not None:
                self._cancel_listen()   # 监听中 ESC：先取消映射监听，不退屏
            else:
                self._back()

    def _back(self):
        from .main_menu import MainMenuScreen
        self.app.switch(MainMenuScreen())

    def render_ui(self):
        if self._wizard is not None:
            self._render_wizard()
            return
        imgui.set_next_window_position(u(30), u(30))
        imgui.set_next_window_size(u(560), 0)
        imgui.begin("遥控通道设置", flags=imgui.WINDOW_NO_COLLAPSE)
        # 每帧只读一次输入（顶部提示区与各通道行预览共用）
        ch = self.app.rc_input.read(self._dt)
        devs = joystick_devices()
        joy = self._safe_joystick()
        if devs:
            self._dev_index = min(self._dev_index, len(devs) - 1)
        self._feed_listen(joy)   # 每帧推进映射监听（渲染接线）
        if getattr(self.app, "joystick_lost", False):
            imgui.text("⚠ 手柄已断开")
        elif getattr(self.app, "joystick_recovered", False):
            imgui.text("✓ 手柄已自动连接")
        imgui.separator()
        if joy is None:
            imgui.text("未检测到手柄（插入后自动出现）")
            # 设备行位置：无手柄时向导按钮保留在原位但不可用（点击给提示）
            if imgui.button("启动配置向导", u(180), u(40)):
                self._msg = "未检测到手柄，无法启动向导"
        else:
            labels = [f"{i}: {name}" for i, name in devs]
            _, self._dev_index = imgui.combo("设备", self._dev_index, labels,
                                             0 if len(labels) < 15 else len(labels))
            # 向导独占一行：设备选择之下、所有通道设置行之上
            if imgui.button("启动配置向导", u(180), u(40)):
                self._wizard = self._wizard_new()
        imgui.separator()
        if joy is not None:
            n_axis = joy.get_numaxes()
            imgui.text("通道绑定（点按钮后拨动/按压对应控件自动识别）")
            for c in CHANNELS:
                self._render_axis_row(c, ch, n_axis)
            imgui.separator()
            imgui.text("AUX 开关绑定（模式开关等；右键绑定按钮清除）")
            n_btn = joy.get_numbuttons()
            for i, key in enumerate(AUX_KEYS, start=1):
                self._render_aux_row(i, key, n_btn, n_axis, ch)
        if self._msg:
            imgui.text(self._msg)
        # 底部按钮行：左「返回」右「保存并应用」
        if imgui.button("返回", u(180), u(40)):
            self._back()
        imgui.same_line()
        imgui.set_cursor_pos_x(u(364))   # 窗口宽 560：右缘对齐「保存并应用」
        if imgui.button("保存并应用", u(180), u(40)):
            if joy is not None and devs:
                self.app.rc_config.device_name = devs[self._dev_index][1]
                log.info("rc config saved: device='%s'", self.app.rc_config.device_name)
                self.app.store.save("rc", self.app.rc_config)
                self.app.rc_input = self._make_joystick_input()
                self._msg = "已应用手柄绑定"
            else:
                self._msg = "未检测到手柄，无可保存"
        imgui.end()
    def _render_axis_row(self, key, ch, n_axis):
        """主通道行：通道名 | 绑定按钮（点击进入监听，拨杆自动识别；越界警示
        并入按钮文本）| 预览条 | 反向复选框。列位与 AUX 行对齐（_COL_*）。"""
        b = self.app.rc_config.mappings[key]
        imgui.push_id(key)
        imgui.text(CHANNEL_CN[key])
        imgui.same_line(u(_COL_BTN))
        if self._listen == key:
            if imgui.button("拨动摇杆…", u(140)):
                self._cancel_listen()
        else:
            label = f"轴 {b.axis}" if b.axis >= 0 else "未绑定"
            if b.axis >= n_axis:
                label += " ⚠"   # 越界警示并入按钮文本（Task 45：警示不静默改配置）
            if imgui.button(label, u(140)):
                self._begin_listen(key, "axis", self._safe_joystick())
        imgui.same_line(u(_COL_PREV))
        self._preview_bar(key, ch)
        imgui.same_line(u(_COL_TAIL))
        _, b.inverted = imgui.checkbox("反向", b.inverted)
        imgui.pop_id()
    def _preview_bar(self, key, ch):
        """通道预览条：油门 0..1、其余 −1..1 映射到进度比例，宽度 150 对齐各列"""
        v = getattr(ch, key)
        lo, hi = (0.0, 1.0) if key == "throttle" else (-1.0, 1.0)
        imgui.progress_bar((v - lo) / (hi - lo), (u(150), 0))
    def _safe_joystick(self):
        """取当前设备手柄；断开/索引越界/构造异常返回 None（裸取会抛 pygame.error）"""
        if self._dev_index >= pygame.joystick.get_count():
            return None
        try:
            joy = pygame.joystick.Joystick(self._dev_index)
            joy.init()
            return joy
        except pygame.error:
            return None

    # ---- 映射监听状态机（imgui 无关：UI 只负责点按钮/画状态，推进在此） ----

    def _begin_listen(self, key, kind, joy):
        """进入映射监听。kind："axis" 主通道（拨杆识别轴）/ "auto" AUX（按键与
        拨杆同时盯：谁先动按谁绑定，控件类型自动判定）。
        轴排除集=其他主通道与已绑 AUX 的轴（本通道自身轴可重绑）；按钮基线=
        当帧已按住的按钮（防误绑），其他 AUX 已绑按钮永久排除（_listen_exbtns）。"""
        self._listen = key
        self._listen_kind = kind
        self._listen_exbtns = frozenset()
        exclude = set()
        for o in CHANNELS:
            if o != key:
                a = self.app.rc_config.mappings[o].axis
                if a >= 0:
                    exclude.add(a)
        if kind == "auto":
            exbtns = set()
            for k2 in AUX_KEYS:
                if k2 != key:
                    ab = self.app.rc_config.mappings[k2]
                    if ab.kind == "axis3" and ab.index >= 0:
                        exclude.add(ab.index)
                    if ab.kind == "button" and ab.index >= 0:
                        exbtns.add(ab.index)
            self._listen_exbtns = frozenset(exbtns)
            self._listen_base = frozenset(
                i for i in range(joy.get_numbuttons()) if joy.get_button(i))
        cap = AxisCapture(threshold=0.3, exclude=exclude)
        cap.begin([joy.get_axis(j) for j in range(joy.get_numaxes())])
        self._listen_cap = cap

    def _cancel_listen(self):
        """取消监听（再点同钮/ESC/手柄断开共用），配置不动"""
        self._listen = None
        self._listen_kind = None
        self._listen_cap = None
        self._listen_base = frozenset()
        self._listen_exbtns = frozenset()

    def _clear_aux(self, key):
        """清除 AUX 绑定（回未绑定态）"""
        b = self.app.rc_config.mappings[key]
        b.kind = "none"
        b.index = -1
        self._msg = f"{key.upper()} 已清除绑定"

    def _listen_label(self, key):
        """监听提示里的通道显示名：主通道用中文名，AUX 用 AUXn"""
        return CHANNEL_CN.get(key, key.upper())
    def _finish_listen(self, target, kind_resolved):
        """命中绑定：按监听对象写配置（主通道→axis；AUX→kind+index，类型由
        自动识别判定）、提示、退出监听"""
        key = self._listen
        b = self.app.rc_config.mappings[key]
        if key in CHANNELS:
            b.axis = target
        else:
            b.kind = kind_resolved
            b.index = target
        unit = "按钮" if kind_resolved == "button" else "轴"
        self._msg = f"{self._listen_label(key)} 已映射到{unit} {target}"
        self._cancel_listen()

    def _feed_listen(self, joy):
        """监听帧推进（render 每帧调用，joy 可为 None=无手柄）：auto 监听按键
        优先、其次轴位移；主通道监听只看轴位移。方向不在此判定——主通道方向
        由「反向」复选框管，AUX 两/三档开关无方向概念。"""
        if self._listen is None:
            return
        if joy is None:
            self._cancel_listen()
            self._msg = "手柄已断开，映射取消"
            return
        if self._listen_kind == "auto":
            # 按键优先：基线随松开收缩；其他 AUX 已绑按钮永久排除
            self._listen_base = frozenset(i for i in self._listen_base
                                          if joy.get_button(i))
            for i in range(joy.get_numbuttons()):
                if joy.get_button(i) and i not in self._listen_base \
                        and i not in self._listen_exbtns:
                    self._finish_listen(i, "button")
                    return
            cap = self._listen_cap
            n = joy.get_numaxes()
            cap.feed([joy.get_axis(i) for i in range(n)])
            found = cap.result(n)
            if found is not None:
                self._finish_listen(found[0], "axis3")
            return
        cap = self._listen_cap
        n = joy.get_numaxes()
        cap.feed([joy.get_axis(i) for i in range(n)])
        found = cap.result(n)
        if found is not None:
            self._finish_listen(found[0], "axis")
    # ---- 向导状态机（imgui 无关：回中 → 逐通道捕获 → 验证） ----

    def _wizard_new(self, mode=None):
        """向导状态（选类型/重来共用入口）：mode=None → 类型选择页；
        否则进「回中」步（先回中再逐通道，方向判定共用一份基线）"""
        return {"stage": "capture" if mode else "mode",
                "phase": "center", "mode": mode,
                "order": list(CHANNELS), "bound": {}, "inverted": {},
                "capture": None, "baseline": None}

    def _wizard_continue(self, wz, joy):
        """「回中」步点「继续」：采样各轴基线并武装首通道捕获（基线=点继续
        当帧的杆位，油门不回中也成立；后续各通道复用同一基线）"""
        wz["baseline"] = [joy.get_axis(i) for i in range(joy.get_numaxes())]
        wz["phase"] = "channel"
        self._wizard_arm(wz)

    def _wizard_arm(self, wz):
        """为下一未绑定通道武装捕获（已绑定轴排除；基线复用回中采样）"""
        cap = AxisCapture(threshold=0.25, exclude=set(wz["bound"].values()))
        cap.begin(wz["baseline"])
        wz["capture"] = cap

    def _wizard_feed(self, wz, joy):
        """捕获帧推进：喂轴值；命中 → 记录绑定（pitch 极性按 POLARITY_FLIP
        翻转，见 input/wizard.channel_inverted），回「回中」步或转入验证页。
        每通道之间强制重新回中：用户回杆的误触不污染下一通道方向判定"""
        cap = wz["capture"]
        if cap is None:   # 非捕获期（回中步）被误喂：忽略，不崩
            return
        n = joy.get_numaxes()
        cap.feed([joy.get_axis(i) for i in range(n)])
        found = cap.result(n)
        if found is None:
            return
        axis, inverted = found
        ch = wz["order"][len(wz["bound"])]
        wz["bound"][ch] = axis
        wz["inverted"][ch] = channel_inverted(ch, inverted)
        wz["capture"] = None
        if len(wz["bound"]) == 4:
            wz["stage"] = "verify"
        else:
            # 回中步「继续」时重新采样基线，等效一次干净捕获的开场
            wz["phase"] = "center"

    def _wizard_finish(self, wz, joy):
        """验证页「完成并保存」：绑定写入配置、持久化、重建输入源、关闭向导"""
        for c, axis in wz["bound"].items():
            self.app.rc_config.mappings[c] = AxisBinding(
                axis=axis, inverted=wz["inverted"][c])
        self.app.rc_config.device_name = joy.get_name()
        log.info("rc wizard finished: device='%s' bound=%s inverted=%s",
                 joy.get_name(), wz["bound"], wz["inverted"])
        self.app.store.save("rc", self.app.rc_config)
        self.app.rc_input = self._make_joystick_input()
        self._wizard = None

    def _render_aux_row(self, i, key, n_btn, n_axis, ch):
        """单行 AUX 绑定，四列与主通道行同构：绑定按钮（点击自动识别；右键清除）
        | 预览条 | 反向复选框。列位完全同主通道行。"""
        b = self.app.rc_config.mappings[key]
        imgui.push_id(key)
        imgui.text(f"AUX{i}")
        imgui.same_line(u(_COL_BTN))
        if self._listen == key:
            if imgui.button("拨杆或按键…", u(140)):
                self._cancel_listen()
        elif b.kind == "none":
            if imgui.button("点击映射", u(140)):
                self._begin_listen(key, "auto", self._safe_joystick())
        else:
            unit = "按钮" if b.kind == "button" else "轴"
            label = f"{unit} {b.index}"
            n = n_btn if b.kind == "button" else n_axis
            if b.index >= n:
                label += " ⚠"
            if imgui.button(label, u(140)):
                self._begin_listen(key, "auto", self._safe_joystick())
            if imgui.is_item_clicked(1):
                self._clear_aux(key)   # 右键：清除绑定（独立清除按钮占列位，并入右键）
        imgui.same_line(u(_COL_PREV))
        imgui.progress_bar((ch.aux[i - 1] + 1.0) / 2.0, (u(150), 0))
        imgui.same_line(u(_COL_TAIL))
        _, b.inverted = imgui.checkbox("反向", b.inverted)
        imgui.pop_id()
    def _make_joystick_input(self):
        joy = self._safe_joystick()
        if joy is None:
            # 同帧拔柄的极小概率兜底：空输入，避免 rc_input 悬空（同 App 层策略）
            return NullInput()
        # AUX 按钮源 + 轴数/按钮数必须传入（同 App._make_rc_input），旧两参
        # 构造下 button 绑定的 AUX 求值恒 0、无越界防御
        return JoystickInput(joy.get_axis, self.app.rc_config.mappings,
                             get_button_fn=joy.get_button,
                             num_axes=joy.get_numaxes(),
                             num_buttons=joy.get_numbuttons())

    # ---- 向导（回中 → 逐通道捕获 → 验证，每步带摇杆演示图） ----

    def _render_wizard(self):
        wz = self._wizard
        joy = self._safe_joystick()
        if joy is None:
            # 向导中手柄丢失：取消向导回落主界面（先于 begin 返回，防裸取崩溃）
            self._wizard = None
            self._msg = "手柄已断开，向导已取消"
            return
        # 固定尺寸容纳演示图与验证页全部控件（高度 0 自适应会裁切，见一期台账）。
       # 验证页内容高（24px 字体下四条预览条各约 40px + 演示图 + 按钮区）实测
       # 约 456px，380 不够、底部按钮被挤出窗口需滚轮——按阶段加高
        win_h = u(600) if wz["stage"] == "verify" else u(380)
        imgui.set_next_window_size(u(500), win_h)
        imgui.begin("配置向导", flags=imgui.WINDOW_NO_COLLAPSE)
        if wz["stage"] == "mode":
            imgui.text("你的遥控是哪种模式？")
            for m in MODE_STICKS:
                if imgui.button(m, u(200), u(40)):
                    self._wizard = self._wizard_new(m)
        elif wz["stage"] == "capture":
            if wz["phase"] == "center":
                imgui.text("请将所有摇杆回中")
                imgui.text("回中用于记录各杆静止位，作方向判定基准")
                self._draw_stick_demo((0.0, 0.0), (0.0, 0.0))
                if imgui.button("继续", u(180), u(40)):
                    self._wizard_continue(wz, joy)
            elif wz["phase"] == "channel":
                ch_name = wz["order"][len(wz["bound"])]
                stick = MODE_STICKS[wz["mode"]][ch_name]
                imgui.text(f"请将{CHANNEL_CN[ch_name]}{DIRECTION[ch_name]}打满（{stick}）")
                side, _, vec = demo_motion(wz["mode"], ch_name)
                amount = math.sin(math.pi * ((self._anim_t % _DEMO_PERIOD) / _DEMO_PERIOD))
                off = (vec[0] * amount, vec[1] * amount)
                if side == "左":
                    self._draw_stick_demo(off, (0.0, 0.0), active=(True, False))
                else:
                    self._draw_stick_demo((0.0, 0.0), off, active=(False, True))
                if wz["capture"] is not None:
                    self._wizard_feed(wz, joy)   # 每帧喂捕获（渲染接线）
                    imgui.text(f"识别中…（{CHANNEL_CN[ch_name]} / {stick}）")
        elif wz["stage"] == "verify":
            imgui.text("验证：推动各通道，确认预览方向正确")
            # 预览用向导新绑定构造的临时输入源——app.rc_input 仍是旧配置
            tmp = JoystickInput(joy.get_axis,
                                {c: AxisBinding(axis=a, inverted=wz["inverted"][c])
                                 for c, a in wz["bound"].items()})
            tmp_ch = tmp.read(self._dt)
            _bar("油门", tmp_ch.throttle, 0, 1)
            _bar("偏航", tmp_ch.yaw, -1, 1)
            _bar("横滚", tmp_ch.roll, -1, 1)
            _bar("俯仰", tmp_ch.pitch, -1, 1)
            imgui.text("俯仰方向：推杆低头（−），拉杆抬头（+）")
            left, right = self._verify_demo_offsets(wz, tmp_ch)
            self._draw_stick_demo(left, right, active=(True, True))
            if imgui.button("完成并保存", u(200), u(40)):
                self._wizard_finish(wz, joy)
            imgui.same_line()
            if imgui.button("重来", u(120), u(40)):
                self._wizard = self._wizard_new()
        if imgui.button("取消向导", u(140), u(30)):
            self._wizard = None
        imgui.end()

    def _verify_demo_offsets(self, wz, tmp_ch):
        """验证页演示点：按当前模式把四通道值映射到左右两点位移（imgui y 向下）"""
        left = [0.0, 0.0]
        right = [0.0, 0.0]
        for ch_name in CHANNELS:
            side, ax = STICK_AXIS[wz["mode"]][ch_name]
            dx, dy = verify_offset(ch_name, getattr(tmp_ch, ch_name))
            if side == "左":
                if ax == "左右":
                    left[0] += dx
                else:
                    left[1] += dy
            else:
                if ax == "左右":
                    right[0] += dx
                else:
                    right[1] += dy
        return tuple(left), tuple(right)

    def _draw_stick_demo(self, left_off, right_off, active=(False, False)):
        """窗口内绘制摇杆演示：左右两框（下方小字「左杆/右杆」）各含一点，
        off=(dx,dy)∈[−1,1] 为单位杆量位移（imgui y 向下）。active 决定点色
        （琥珀=本步动画或实时，灰=静止）。先画后 dummy 占位推进光标。"""
        dl = imgui.get_window_draw_list()
        x, y = imgui.get_cursor_screen_pos()
        win_w = imgui.get_window_size()[0]
        size = u(_DEMO_SIZE); gap = u(_DEMO_GAP)
        total = 2 * size + gap
        x0 = x + (win_w - total) / 2.0
        y0 = y + u(6)
        reach = u(_DEMO_REACH)
        r = u(_DEMO_DOT)
        col_border = imgui.get_color_u32_rgba(0.85, 0.85, 0.85, 0.55)
        col_cross = imgui.get_color_u32_rgba(1.0, 1.0, 1.0, 0.12)
        col_idle = imgui.get_color_u32_rgba(0.55, 0.55, 0.55, 1.0)
        col_active = imgui.get_color_u32_rgba(1.0, 0.62, 0.15, 1.0)
        col_label = imgui.get_color_u32_rgba(0.9, 0.9, 0.9, 0.8)
        boxes = [
            ("左杆", x0, left_off, active[0]),
            ("右杆", x0 + size + gap, right_off, active[1]),
        ]
        for label, bx, off, act in boxes:
            cx = bx + size / 2.0
            cy = y0 + size / 2.0
            dl.add_rect(bx, y0, bx + size, y0 + size,
                        col_border, thickness=2.0)
            dl.add_line(bx + u(8), cy, bx + size - u(8), cy, col_cross, 1.0)
            dl.add_line(cx, y0 + u(8), cx, y0 + size - u(8), col_cross, 1.0)
            dx, dy = off
            dl.add_circle_filled(cx + dx * reach, cy + dy * reach, r,
                                 col_active if act else col_idle)
        lx = x0 + size / 2.0 - u(12)
        dl.add_text(lx, y0 + size + u(8), col_label, "左杆")
        dl.add_text(lx + size + gap, y0 + size + u(8), col_label, "右杆")
        # 预留高度须盖住标签行（顶距6+框高+24px行高≈28+余量），否则标签压到下方控件
        imgui.dummy(total, size + u(44))
