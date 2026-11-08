"""无人机参数界面：全部飞行参数可调（设计文档第 6 节），保存即时生效"""
import imgui
import pygame
from .base import Screen
from config_store import default_drone_params

RATE_TYPES = ["betaflight_actual", "linear"]
AXES = ["roll", "pitch", "yaw"]

def _slider(label, v, lo, hi, fmt="%.2f"):
    """统一滑条+数值联动的局部助手"""
    _, nv = imgui.slider_float(label, v, lo, hi, fmt)
    return nv

class DroneParamsScreen(Screen):
    def enter(self, app):
        self.app = app
        self._p = default_drone_params()   # 工作副本
        self._p.__dict__.update(app.drone_params.__dict__)
        self._p.pid = {k: type(v)(*vars(v).values())
                       for k, v in app.drone_params.pid.items()}

    def handle_event(self, event):
        if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
            self._back()

    def _back(self):
        from .main_menu import MainMenuScreen
        self.app.switch(MainMenuScreen())

    def render_ui(self):
        imgui.set_next_window_position(30, 30)
        imgui.set_next_window_size(620, 0)  # 高度自适应
        imgui.begin("无人机参数", flags=imgui.WINDOW_NO_COLLAPSE)

        p = self._p
        imgui.text("Rate（遥控曲线）")
        cur = RATE_TYPES.index(p.rate_type) if p.rate_type in RATE_TYPES else 0
        _, picked = imgui.combo("Rate 类型", cur, RATE_TYPES)
        p.rate_type = RATE_TYPES[picked]
        p.rc_rate = _slider("RC Rate", p.rc_rate, 0.1, 3.0)
        p.super_rate = _slider("Super Rate", p.super_rate, 0.0, 0.95)
        p.expo = _slider("Expo", p.expo, 0.0, 1.0)
        if p.rate_type == "linear":
            p.linear_rate_max_deg = _slider("线性满杆角速度(°/s)",
                                            p.linear_rate_max_deg, 90, 1080, "%.0f")
        imgui.separator()

        imgui.text("PID（三轴独立）")
        for ax in AXES:
            g = p.pid[ax]
            g.p = _slider(f"{ax} P", g.p, 0, 200, "%.0f")
            g.i = _slider(f"{ax} I", g.i, 0, 200, "%.0f")
            g.d = _slider(f"{ax} D", g.d, 0, 100, "%.0f")
        imgui.separator()

        imgui.text("机体")
        p.weight_g = _slider("重量 (g)", p.weight_g, 100, 2000, "%.0f")
        # pyimgui 2.0 返回 (changed, value)：按 (changed, value) 解包，否则 KV 恒为 0
        kv_changed, p.motor_kv = imgui.slider_int("电机 KV", int(p.motor_kv),
                                                  500, 3000)
        p.motor_kv = int(p.motor_kv)
        suggested = p.motor_kv / 250.0
        imgui.text(f"KV 建议推重比: {suggested:.1f}")
        p.thrust_ratio = _slider("推重比", p.thrust_ratio, 1.0, 15.0, "%.1f")
        p.camera_angle_deg = _slider("摄像头角度 (°)", p.camera_angle_deg, 0, 60, "%.0f")
        imgui.separator()

        if imgui.button("保存", 180, 40):
            self.app.drone_params.__dict__.update(p.__dict__)
            self.app.drone_params.pid = p.pid
            self.app.store.save("drone", self.app.drone_params)
        imgui.same_line()
        if imgui.button("恢复默认", 180, 40):
            d = default_drone_params()
            self._p.__dict__.update(d.__dict__)
            self._p.pid = d.pid
        imgui.same_line()
        if imgui.button("返回", 180, 40):
            self._back()
        imgui.end()
