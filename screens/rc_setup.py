"""遥控通道设置：手动绑定 + 实时预览（向导在 Task 11 加入同一界面）"""
import imgui
import pygame
from .base import Screen
from config_store import AxisBinding
from input.joystick import JoystickInput
from input.wizard import AxisCapture, MODE_STICKS, DIRECTION, channel_inverted

CHANNELS = ["throttle", "yaw", "roll", "pitch"]
CHANNEL_CN = {"throttle": "油门", "yaw": "偏航", "roll": "横滚", "pitch": "俯仰"}

AUX_KEYS = ("aux1", "aux2", "aux3", "aux4")
AUX_KINDS = ["none", "button", "axis3"]   # AuxBinding.kind 取值（与 config_store 对应）
AUX_KIND_CN = {"none": "未绑定", "button": "手柄按钮", "axis3": "手柄轴三段"}
# 键盘模式 AUX 拨动键提示（input/keyboard.py 的 F1/F2/F4 键位）
AUX_KEYBOARD_HINT = "键盘模式 AUX 拨动键：F1 解锁 / F2 自稳 / F4 反乌龟"

def joystick_devices():
    """枚举当前手柄 [(index, name)]"""
    pygame.joystick.init()
    return [(i, pygame.joystick.Joystick(i).get_name())
            for i in range(pygame.joystick.get_count())]

def _bar(label, value, lo, hi):
    """通道预览条"""
    imgui.text(f"{label}")
    imgui.same_line(90)
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

    def update(self, dt: float):
        self._dt = dt   # 主循环在 render_ui 之前调用，同帧预览条可拿到真实 dt

    def handle_event(self, event):
        if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
            self._back()

    def _back(self):
        from .main_menu import MainMenuScreen
        self.app.switch(MainMenuScreen())

    def render_ui(self):
        if self._wizard is not None:   # 向导激活：改渲染向导弹窗（模态）
            self._render_wizard()
            return
        imgui.set_next_window_position(30, 30)
        imgui.set_next_window_size(560, 0)
        imgui.begin("遥控通道设置", flags=imgui.WINDOW_NO_COLLAPSE)

        # 每帧只读一次输入（AUX 行与预览区共用）：KeyboardInput.read 有油门积分
        # 副作用，多处各调一次会把油门加速 N 倍
        ch = self.app.rc_input.read(self._dt)
        mode = self.app.rc_config.mode
        imgui.text(f"当前模式: {'手柄' if mode == 'joystick' else '键盘（兜底）'}")
        if getattr(self.app, "joystick_lost", False):
            imgui.text("⚠ 手柄已断开，已自动切回键盘")
        elif getattr(self.app, "joystick_recovered", False):
            # Task 38 对称提示：拔出重插 / 启动后接入被自动拾取（清 lost 时置位）
            imgui.text("✓ 手柄已自动连接")

        imgui.separator()
        devs = joystick_devices()
        if devs:
            # 拔柄/重排后陈旧索引越界钳位（台账 T10 minor）
            self._dev_index = min(self._dev_index, len(devs) - 1)
        joy = self._safe_joystick()
        if joy is None:
            imgui.text("未检测到手柄（插入后自动出现）")
            imgui.text(AUX_KEYBOARD_HINT)   # 键盘模式 AUX 区替换为键位提示
            # 无手柄：向导按钮保持不可用（裸按钮，无点击分支）
            imgui.button("启动配置向导", 180, 40)
        else:
            labels = [f"{i}: {name}" for i, name in devs]
            _, self._dev_index = imgui.combo("设备", self._dev_index, labels,
                                             0 if len(labels) < 15 else len(labels))
            imgui.separator()
            imgui.text("手动绑定（轴 0 ~ N，-1 未绑定）")
            axis_count = joy.get_numaxes()
            for c in CHANNELS:
                b = self.app.rc_config.mappings[c]
                _, b.axis = imgui.slider_int(f"{CHANNEL_CN[c]} 轴",
                                             b.axis, -1, axis_count - 1)
                _, b.inverted = imgui.checkbox(f"{CHANNEL_CN[c]} 反向", b.inverted)

            imgui.separator()
            imgui.text("AUX 开关绑定（模式开关等）")
            if mode != "joystick":
                # 插着手柄但当前仍键盘模式：键盘拨动键仍生效，键位提示一并给出
                imgui.text(AUX_KEYBOARD_HINT)
            n_btn = joy.get_numbuttons()
            for i, key in enumerate(AUX_KEYS, start=1):
                self._render_aux_row(i, key, n_btn, axis_count, ch)

            if imgui.button("应用并保存", 180, 40):
                self.app.rc_config.mode = "joystick"
                self.app.rc_config.device_name = devs[self._dev_index][1]
                self.app.store.save("rc", self.app.rc_config)
                self.app.rc_input = self._make_joystick_input()
                self._msg = "已应用手柄绑定"
            imgui.same_line()
            if imgui.button("启动配置向导", 180, 40):   # _dev_index 已由设备下拉框写好
                self._wizard = {"stage": "mode", "capture": None,
                                "bound": {}, "inverted": {}, "mode": None}

        imgui.separator()
        imgui.text("通道实时预览")
        _bar("油门", ch.throttle, 0, 1)
        _bar("偏航", ch.yaw, -1, 1)
        _bar("横滚", ch.roll, -1, 1)
        _bar("俯仰", ch.pitch, -1, 1)
        if self._msg:
            imgui.text(self._msg)
        if imgui.button("返回主菜单", 180, 40):
            self._back()
        imgui.end()

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

    def _render_aux_row(self, i: int, key: str, n_btn: int, n_axis: int, ch):
        """单行 AUX 绑定：kind 下拉 + 编号滑条 + 实时预览条（读当帧缓存的 ch）"""
        b = self.app.rc_config.mappings[key]   # AuxBinding（kind/index）
        imgui.push_id(key)   # 四行同标签「类型/编号」，须隔离 ID 防状态串扰
        kind_idx = AUX_KINDS.index(b.kind) if b.kind in AUX_KINDS else 0   # 坏值按未绑定
        imgui.set_next_item_width(120)
        changed, picked = imgui.combo("类型", kind_idx,
                                      [AUX_KIND_CN[k] for k in AUX_KINDS])
        if changed:
            b.kind = AUX_KINDS[picked]
            b.index = -1 if b.kind == "none" else 0   # 换类型重置编号，防 -1 残留
        imgui.same_line()
        if b.kind != "none":
            n = n_btn if b.kind == "button" else n_axis
            if n <= 0:
                imgui.text("（设备无按钮）" if b.kind == "button" else "（设备无轴）")
            elif b.index >= n:
                # Task 45 越界警示：绑定编号超出当前设备轴/按钮数（换设备/
                # 改配置残留）——JoystickInput 越界防御返回中位、模式永远
                # 无法激活。不静默钳位自愈（否则警示一帧即逝且配置被篡改），
                # 越界值保留使警示持续；重绑走「类型」下拉重选（既有换类型
                # 重置编号为 0 的逻辑）。index<0（未绑定）不在此分支，不算越界
                imgui.text("⚠ 越界：该设备无此编号")
            else:
                b.index = min(max(b.index, 0), n - 1)   # 换设备后编号越界自愈
                imgui.set_next_item_width(140)
                _, b.index = imgui.slider_int("编号", b.index, 0, n - 1)
        imgui.same_line()
        imgui.text(f"AUX{i}")
        imgui.same_line()
        # 预览条：aux ∈ {-1,0,1} → fraction ∈ [0,1]（同 _bar 手法）
        imgui.progress_bar((ch.aux[i - 1] + 1.0) / 2.0)
        imgui.pop_id()

    def _make_joystick_input(self):
        joy = self._safe_joystick()
        if joy is None:
            # 同帧拔柄的极小概率兜底：回落键盘，避免 rc_input 悬空（同 App 层策略）
            from input.keyboard import KeyboardInput
            return KeyboardInput()
        # AUX 按钮源 + 轴数/按钮数必须传入（同 App._make_rc_input），旧两参
        # 构造下 button 绑定的 AUX 求值恒 0、无越界防御
        return JoystickInput(joy.get_axis, self.app.rc_config.mappings,
                             get_button_fn=joy.get_button,
                             num_axes=joy.get_numaxes(),
                             num_buttons=joy.get_numbuttons())

    def _render_wizard(self):
        wz = self._wizard
        joy = self._safe_joystick()
        if joy is None:
            # 向导中手柄丢失：取消向导回落主界面（每帧裸取手柄会崩，须先于 begin 返回）
            self._wizard = None
            self._msg = "手柄已断开，向导已取消"
            return
        # 固定尺寸：方向文案（如「请将偏航向右打满（左杆左右）」16 全角字
        # ≈ 24px×16）超出默认自动收缩宽度会右缘截断；高度须容纳 verify 阶段
        # 全部预览条与按钮（高度 0 实测不再按内容自适应，会下缘裁切按钮）
        imgui.set_next_window_size(480, 300)
        imgui.begin("配置向导", flags=imgui.WINDOW_NO_COLLAPSE)
        n_axis = joy.get_numaxes()

        if wz["stage"] == "mode":   # 第一步：遥控类型
            imgui.text("你的遥控是哪种模式？")
            for m in MODE_STICKS:
                if imgui.button(m, 200, 40):
                    wz["mode"] = m
                    wz["stage"] = "capture"
                    wz["order"] = ["throttle", "yaw", "roll", "pitch"]
                    wz["bound"] = {}
                    wz["inverted"] = {}
                    wz["capture"] = None
        elif wz["stage"] == "capture":   # 第二步：逐通道捕获
            ch_name = wz["order"][len(wz["bound"])]
            stick = MODE_STICKS[wz["mode"]][ch_name]
            if wz["capture"] is None:
                imgui.text(f"请将{CHANNEL_CN[ch_name]}{DIRECTION[ch_name]}打满（{stick}）")
                if imgui.button("开始识别", 180, 40):
                    wz["capture"] = AxisCapture(
                        threshold=0.25, exclude=set(wz["bound"].values()))
                    # 按下当帧采样基线（杆静止位；油门不回中也成立）
                    wz["capture"].begin([joy.get_axis(i) for i in range(n_axis)])
            else:
                wz["capture"].feed([joy.get_axis(i) for i in range(n_axis)])
                imgui.text(f"识别中…（{CHANNEL_CN[ch_name]} / {stick}）")
                found = wz["capture"].result(n_axis)
                if found is not None:
                    axis, inverted = found
                    wz["bound"][ch_name] = axis
                    # 捕获判的是原始轴方向；pitch 正杆义与指令方向相反，须按
                    # POLARITY_FLIP 翻转（否则飞行推杆=抬头，俯仰反向）
                    wz["inverted"][ch_name] = channel_inverted(ch_name, inverted)
                    wz["capture"] = None
                    if len(wz["bound"]) == 4:
                        wz["stage"] = "verify"
                else:
                    imgui.text("等待更大的杆量…")
        elif wz["stage"] == "verify":   # 第三步：验证
            imgui.text("验证：推动各通道，确认预览方向正确")
            # 预览用向导新绑定构造的临时输入源——app.rc_input 仍是旧配置，验证不了新绑定
            tmp = JoystickInput(joy.get_axis,
                                {c: AxisBinding(axis=a, inverted=wz["inverted"][c])
                                 for c, a in wz["bound"].items()})
            ch = tmp.read(self._dt)
            _bar("油门", ch.throttle, 0, 1); _bar("偏航", ch.yaw, -1, 1)
            _bar("横滚", ch.roll, -1, 1); _bar("俯仰", ch.pitch, -1, 1)
            imgui.text("俯仰方向：推杆低头（−），拉杆抬头（+）")
            if imgui.button("完成并保存", 200, 40):
                for c, axis in wz["bound"].items():
                    self.app.rc_config.mappings[c] = AxisBinding(
                        axis=axis, inverted=wz["inverted"][c])
                self.app.rc_config.mode = "joystick"
                self.app.rc_config.device_name = joy.get_name()
                self.app.store.save("rc", self.app.rc_config)
                self.app.rc_input = self._make_joystick_input()
                self._wizard = None
            imgui.same_line()
            if imgui.button("重来", 120, 40):
                self._wizard = {"stage": "mode", "capture": None,
                                "bound": {}, "inverted": {}, "mode": None}
        if imgui.button("取消向导", 140, 30):
            self._wizard = None
        imgui.end()
