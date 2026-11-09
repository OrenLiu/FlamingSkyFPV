"""飞控仿真：rate 映射 → PID 角速度闭环 → quad-X 混控 → 推力（设计文档第 4 节）
纯计算模块：不依赖 PyBullet/渲染，gyro 数据由 world.py 提供（杆义角速度）。"""
from input.channels import Channels
from config_store import DroneParams, RateParams

def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))

def rate_setpoint(rc: float, rate: RateParams, rate_type: str) -> float:
    """rc∈[-1,1] + 单轴 rate 参数 → 目标角速度 °/s（纯函数，三轴各自调用）。
    betaflight_actual：expo 混合 + superRate 超线性（BF 4.x Actual Rates 公式）；
    linear：线性比例。"""
    rc = _clamp(rc, -1.0, 1.0)
    a = abs(rc)
    if rate_type == "linear":
        return rc * rate.linear_max_deg
    # expo：中位柔化（对 ±1 无效：1³×expo + 1×(1-expo) = 1）
    rc = rc * (a * a * a) * rate.expo + rc * (1.0 - rate.expo)
    super_rate = min(rate.super_rate, 0.99)
    super_factor = 1.0 / max(1.0 - a * super_rate, 0.01)
    return 200.0 * rate.rc_rate * rc * super_factor

# PID 增益刻度换算（类比 Betaflight PID_MIXER_SCALING）：增益入参/存档为 BF 量级
# 刻度（P≈40 量级，二期地面站 MSP 协议同单位），控制器内部换算到电机油门域
PID_MIX_SCALING = 1e5

class PIDController:
    """单轴 PID：增益为 Betaflight 量级刻度（P≈40 量级），内部换算到电机油门域。
    D on measurement（无 setpoint 阶跃踢），积分限幅抗饱和
    （output_limit 限定 I 项幅值：电机域限幅，积分器直接累积 i*error*dt 并
    钳位 ±output_limit；P/D 项不受限——纯 P 输出可远超限幅值，方向由混控末端钳位兜底）"""
    def __init__(self, p: float, i: float, d: float, output_limit: float = 1.0):
        # BF 刻度 → 电机域：与存档/参数页展示同一刻度，update() 数学在电机域自洽
        self.p, self.i, self.d = (p / PID_MIX_SCALING, i / PID_MIX_SCALING,
                                  d / PID_MIX_SCALING)
        self.output_limit = output_limit
        self._integral = 0.0
        self._prev_measurement = None

    def reset(self):
        self._integral = 0.0
        self._prev_measurement = None

    def update(self, setpoint: float, measurement: float, dt: float) -> float:
        error = setpoint - measurement
        self._integral = _clamp(self._integral + self.i * error * dt,
                                -self.output_limit, self.output_limit)
        deriv = 0.0 if self._prev_measurement is None else \
            -(measurement - self._prev_measurement) / max(dt, 1e-6)
        self._prev_measurement = measurement
        return self.p * error + self._integral + self.d * deriv

# 电机顺序约定：[FL, FR, BL, BR]（前左/前右/后左/后右）
def mix_quad_x(throttle: float, yaw_cmd: float, roll_cmd: float,
               pitch_cmd: float) -> list:
    """quad-X 混控。cmd 为 PID 输出（杆义：yaw右+ roll右+ pitch抬头+）。
    pitch 抬头 = 前端上翘 → 前电机加力（标定实测：旧版后电机加产生低头，
    pitch 开环增益为负 → 闭环正反馈；test_mixer_pitch_front_motors_up 裁判）。
    roll/yaw 方向由 test_world 闭环测试裁判。"""
    motors = (
        throttle + pitch_cmd + roll_cmd + yaw_cmd,  # FL
        throttle + pitch_cmd - roll_cmd - yaw_cmd,  # FR
        throttle - pitch_cmd + roll_cmd - yaw_cmd,  # BL
        throttle - pitch_cmd - roll_cmd + yaw_cmd,  # BR
    )
    return [_clamp(m, 0.0, 1.0) for m in motors]

class FlightController:
    """组合：三轴 rate 目标 → PID(实测杆义角速度) → 混控 → 电机油门"""
    def __init__(self, params: DroneParams):
        self.set_params(params)

    def set_params(self, params: DroneParams):
        self._params = params
        g = params.pid
        self._pid = {
            "roll": PIDController(g["roll"].p, g["roll"].i, g["roll"].d),
            "pitch": PIDController(g["pitch"].p, g["pitch"].i, g["pitch"].d),
            "yaw": PIDController(g["yaw"].p, g["yaw"].i, g["yaw"].d),
        }

    def update(self, ch: Channels, gyro_dps: tuple, dt: float) -> list:
        """gyro_dps = (roll右, pitch抬头, yaw右) 杆义角速度 °/s → 四电机油门 [0,1]"""
        rt = self._params.rate_type
        targets = (rate_setpoint(ch.roll, self._params.rate["roll"], rt),
                   rate_setpoint(ch.pitch, self._params.rate["pitch"], rt),
                   rate_setpoint(ch.yaw, self._params.rate["yaw"], rt))
        outs = (self._pid["roll"].update(targets[0], gyro_dps[0], dt),
                self._pid["pitch"].update(targets[1], gyro_dps[1], dt),
                self._pid["yaw"].update(targets[2], gyro_dps[2], dt))
        return mix_quad_x(ch.throttle, outs[2], outs[0], outs[1])

    def motor_thrusts(self, motors: list) -> list:
        """单电机最大推力 = 推重比×重量/4；实际 = T_max×motor²"""
        weight_kg = self._params.weight_g / 1000.0
        t_max = weight_kg * 9.81 * self._params.thrust_ratio / 4.0
        return [t_max * m * m for m in motors]
