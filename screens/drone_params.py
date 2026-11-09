"""无人机参数界面：全部飞行参数可调（设计文档第 6 节），保存即时生效。
用户裁定：PID 与 rate 三轴独立 + 全参数数值输入框（量级跨度大，滑条精度不足）。"""
import imgui
import pygame
from .base import Screen
from config_store import DroneParams, PIDGains, RateParams, default_drone_params

RATE_TYPES = ["betaflight_actual", "linear"]
AXES = ["roll", "pitch", "yaw"]
AXIS_CN = {"roll": "横滚", "pitch": "俯仰", "yaw": "偏航"}

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
    def enter(self, app):
        self.app = app
        self._p = _clone_params(app.drone_params)   # 工作副本

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
        p.camera_angle_deg = _input("摄像头角度 (°)", p.camera_angle_deg, 1.0)
        imgui.separator()

        if imgui.button("保存", 180, 40):
            # 保存独立快照：工作副本继续编辑互不影响
            self.app.drone_params = _clone_params(p)
            self.app.store.save("drone", self.app.drone_params)
        imgui.same_line()
        if imgui.button("恢复默认", 180, 40):
            self._p = default_drone_params()
        imgui.same_line()
        if imgui.button("返回", 180, 40):
            self._back()
        imgui.end()
