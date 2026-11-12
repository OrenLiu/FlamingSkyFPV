"""飞控仿真：rate 映射 → PID 角速度闭环 → quad-X 混控 → 推力（设计文档第 4 节）
纯计算模块：不依赖 PyBullet/渲染，gyro 数据由 world.py 提供（杆义角速度）。"""
from input.channels import Channels
from config_store import DroneParams, RateParams

def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))

# ---- 模式开关（Mode）（二期虚拟飞控） ----
# box permanentId 常量：Betaflight 4.5.x pg/box_ids.h 参考值（Task 17 spike 会对外部
# 源码核对，若不一致以外部源码为准并在提交说明记录差异）
BOX_ARM = 0                    # 解锁（电机供能）
BOX_ANGLE = 1                  # 自稳角模式
BOX_HORIZON = 2                # 地平线模式（自稳+rate 混合）
BOX_FLIP_OVER_AFTER_CRASH = 21 # 反乌龟（翻转脱困）

# 解锁油门保护阈值：throttle < 该值才允许解锁（防炸桨；类比 BF min_check）
ARM_THROTTLE_MAX = 0.10

# ANGLE 自稳外环（设计 §7）：满杆目标倾角 ±45°；角度误差 × 外环增益
# （°/s/°）→ 内环 rate setpoint。不暴露 UI（一期量级调通即可，后续按需开放）
ANGLE_MAX_DEG = 45.0
ANGLE_P = 8.0

# HORIZON 混合过渡带：|rc| ≥ 该值后自稳权重降为 0（杆外纯 acro/rate 区）
HORIZON_TRANSITION = 0.6

# 反乌龟 FLIP_OVER_AFTER_CRASH（Task 13）：120°/90° 双阈值迟滞防抖 + 油门门控。
# 进入：box 激活 + armed + 未翻正 + (|roll|>120° 或 |pitch|>120°) + 油门<0.35；
# 退出（析取，fix round 1 controller 裁定）：两轴均 <90°（严格）或 box 撤销
# 或油门 ≥0.35。翻正 = 主导轴 setpoint 叠加 ±FLIP_OVER_RATE 朝 0° 最短方向
# （角 >90° 取负、<−90° 取正）
FLIP_OVER_RATE = 400.0    # °/s 主导轴叠加的翻正角速度
FLIP_ENTER_DEG = 120.0    # 进入阈值：任一姿态轴绝对值严格超过
FLIP_EXIT_DEG = 90.0      # 退出阈值：两轴均严格低于；叠加方向判据同值
FLIP_MAX_THROTTLE = 0.35  # 进入侧须严格低于；退出侧 ≥ 即接管（互补无重叠）

def eval_boxes(mode_ranges, aux_us) -> frozenset:
    """求值激活的模式集合（纯函数，Betaflight 语义简化版）。
    mode_ranges 条目 (box_id, aux_index, lo_us, hi_us)：aux_us[aux_index] 落入
    [lo_us, hi_us] 闭区间 → 该 box 激活。aux_index 越界的条目忽略（绑定未配置时
    不激活任何模式）；同一 box 多条目命中取并集。"""
    active = set()
    for box_id, aux_index, lo_us, hi_us in mode_ranges:
        if aux_us is not None and 0 <= aux_index < len(aux_us) \
                and lo_us <= aux_us[aux_index] <= hi_us:
            active.add(box_id)
    return frozenset(active)

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

def _angle_loop(rc: float, current_deg: float) -> float:
    """ANGLE/HORIZON 共用自稳外环（纯函数）：杆位→目标倾角（±ANGLE_MAX_DEG），
    角度误差×ANGLE_P → 内环 rate setpoint（°/s）"""
    return (rc * ANGLE_MAX_DEG - current_deg) * ANGLE_P

def _horizon_mix(rc: float, current_deg: float, rate_sp: float) -> float:
    """HORIZON 混合 setpoint（纯函数）：w×自稳外环 + (1−w)×rate 曲线输出。
    w = clamp(1−|rc|/HORIZON_TRANSITION)：中位 w=1 纯自稳（退化为 ANGLE），
    |rc|≥HORIZON_TRANSITION w=0 纯 rate（杆外 acro），之间线性过渡"""
    w = _clamp(1.0 - abs(rc) / HORIZON_TRANSITION, 0.0, 1.0)
    return w * _angle_loop(rc, current_deg) + (1.0 - w) * rate_sp

def _flip_over_dir(angle_deg: float) -> int:
    """翻正方向符号（纯函数）：|角|>90° 时朝 0° 最短——正角 −1（回滚）、
    负角 +1；|角|≤90° 返回 0 不叠加（翻正态中主导轴必 ≥90°——退出条件为两轴
    均 <90°，≤90° 仅 |roll|=|pitch| 平手时理论可达，防御兜底）"""
    if angle_deg > FLIP_EXIT_DEG:
        return -1
    if angle_deg < -FLIP_EXIT_DEG:
        return 1
    return 0

# PID 增益刻度换算（类比 Betaflight PID_MIXER_SCALING）：增益入参/存档为 BF 量级
# 刻度（P≈40 量级，二期地面站 MSP 协议同单位），控制器内部换算到电机油门域
PID_MIX_SCALING = 1e5

# 推力电压缩放参考电压（4S 标称 14.8V）。物理依据：电机空载转速 ∝ KV×V，推力
# ∝ 转速² → 满油门推力 = 参考值 × (battery_voltage/REF_VOLTAGE)²。thrust_ratio
# 语义 = 本参考电压下的推重比（DroneParams 注释同）
REF_VOLTAGE = 14.8

# TPA 油门记忆初值：悬停近似（1/sqrt(7)≈0.38，取 0.35）；首帧后即被实际电机均值
# 覆盖。set_params 重建与加锁重置共用同一常量，防两处漂移
TPA_THROTTLE_INIT = 0.35

def tpa_factor(throttle: float, percent: float, breakpoint: float) -> float:
    """TPA 衰减因子（Throttle PID Attenuation）：油门断点以下 1.0（悬停区不动）；
    以上线性衰减，满油门 = 1 - percent/100；断点 ≥1 时油门永远够不到衰减区 → 恒 1.0
    （clamp 保护）。油门钳位 [0,1]，超界不外推。"""
    if breakpoint >= 1.0:
        return 1.0
    t = _clamp(throttle, 0.0, 1.0)
    if t <= breakpoint:
        return 1.0
    return 1.0 - (percent / 100.0) * (t - breakpoint) / (1.0 - breakpoint)

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
    """组合：三轴 rate 目标 → PID(实测杆义角速度) → TPA 衰减 → 混控 → 电机油门"""
    def __init__(self, params: DroneParams):
        self.set_params(params)

    def set_params(self, params: DroneParams):
        self._params = params
        # 调参域：virtual_fc=True 消费 virtual.tune（地面站调参生效），False 消费
        # 平铺字段快照（一期行为，active_tune 内部深拷贝）。建控时取一次存引用，
        # update 逐帧只读不重建（避免每帧深拷贝分配）
        t = params.active_tune()
        self._tune = t
        g = t.pid
        self._pid = {
            "roll": PIDController(g["roll"].p, g["roll"].i, g["roll"].d),
            "pitch": PIDController(g["pitch"].p, g["pitch"].i, g["pitch"].d),
            "yaw": PIDController(g["yaw"].p, g["yaw"].i, g["yaw"].d),
        }
        # TPA 油门记忆：初值见 TPA_THROTTLE_INIT；首帧后即被实际电机均值覆盖。
        # set_params 重建（换机体/参数页保存），不复用旧值
        self._tpa_throttle = TPA_THROTTLE_INIT
        # 飞控模式与解锁状态：set_params = 重建飞控（换机体/参数页保存）→ 回锁定态
        self._virtual_fc = params.virtual_fc
        self.armed = False
        self.arm_denied_once = False
        self._flipping = False   # 反乌龟翻正态（Task 13）：重建回未翻正

    def update(self, ch: Channels, gyro_dps: tuple, dt: float,
               aux_us=None, boxes=frozenset(), angles_deg=None) -> list:
        """gyro_dps = (roll右, pitch抬头, yaw右) 杆义角速度 °/s → 四电机油门 [0,1]
        二期扩展（缺省参数保证一期三参调用零修改）：boxes = 本帧激活模式集合
        （eval_boxes 求值结果）。virtual_fc=True 时 ARM 门控生效——ARM 激活且油门
        <ARM_THROTTLE_MAX 才解锁，加锁期间电机归零、每帧清 PID；virtual_fc=False
        门控整段跳过（一期行为不变）。angles_deg = (roll, pitch, yaw) 杆义姿态角
        度（与 gyro_dps 同义系），ANGLE/HORIZON 模式外环与反乌龟倒置检测消费，
        None 且相关模式激活时防御性跳过对应逻辑（飞行接线层保证传入）。aux_us
        为 MSP 任务预留，本任务不消费。"""
        if self._virtual_fc:
            if BOX_ARM in boxes:
                if not self.armed:
                    if ch.throttle < ARM_THROTTLE_MAX:
                        self.armed = True
                        self.arm_denied_once = False
                    else:
                        # 油门保护：高油门尝试解锁被拒（ latch 一次供 UI 提示）
                        self.arm_denied_once = True
            else:
                self.armed = False    # 撤 ARM → 立即加锁
            if not self.armed:
                # 加锁期每帧重置（幂等）：解锁瞬间 = 干净初态——三轴 PID 积分/D
                # 状态清零 + TPA 油门记忆回悬停初值 + 翻正态复位，等价全新飞控
                for pid in self._pid.values():
                    pid.reset()
                self._tpa_throttle = TPA_THROTTLE_INIT
                self._flipping = False
                return [0.0, 0.0, 0.0, 0.0]
        rt = self._tune.rate_type
        targets = (rate_setpoint(ch.roll, self._tune.rate["roll"], rt),
                   rate_setpoint(ch.pitch, self._tune.rate["pitch"], rt),
                   rate_setpoint(ch.yaw, self._tune.rate["yaw"], rt))
        # ANGLE 自稳外环 / HORIZON 混合：替换 roll/pitch 内环 setpoint；yaw 恒
        # rate 曲线（自稳不掺 yaw 偏航语义）。合取 self.armed——模式只在解锁后
        # 生效（virtual_fc 加锁期上方提前 return 已隐式保证，此处显式化防御加固；
        # virtual_fc=False 时 armed 恒 False，模式分支天然不进入，一期路径不变）。
        # ANGLE 优先于 HORIZON：同激活取纯外环
        if BOX_ANGLE in boxes and angles_deg is not None and self.armed:
            targets = (_angle_loop(ch.roll, angles_deg[0]),
                       _angle_loop(ch.pitch, angles_deg[1]),
                       targets[2])
        elif BOX_HORIZON in boxes and angles_deg is not None and self.armed:
            # HORIZON：中位自稳、杆外 acro（加权式见 _horizon_mix）
            targets = (_horizon_mix(ch.roll, angles_deg[0], targets[0]),
                       _horizon_mix(ch.pitch, angles_deg[1], targets[1]),
                       targets[2])
        # 反乌龟 FLIP_OVER_AFTER_CRASH（Task 13）：倒置检测状态机 + 主导轴最短
        # 方向翻正叠加，120°/90° 迟滞防抖、油门 ≥0.35 拒进（常量注释含语义）。
        # 退出为析取式（fix round 1 controller 裁定）：角度迟滞（两轴均 <90°，
        # 自动翻正完成）或 FLIP box 撤销（真机肌肉记忆：撤 box 即停）或油门
        # ≥0.35（用户接管意图，不被 ±400°/s 对抗）。overlay 在 setpoint 管线
        # 末端（上述模式之后、PID 之前）只加主导轴；进入检测与退出同帧完成
        # 先于叠加。合取 self.armed 沿模式分支惯例；angles 缺失整段跳过（防御，
        # 状态保持不动）
        if angles_deg is not None and self.armed:
            roll_a, pitch_a = angles_deg[0], angles_deg[1]
            if self._flipping:
                if ((abs(roll_a) < FLIP_EXIT_DEG and abs(pitch_a) < FLIP_EXIT_DEG)
                        or BOX_FLIP_OVER_AFTER_CRASH not in boxes
                        or ch.throttle >= FLIP_MAX_THROTTLE):
                    self._flipping = False
            elif (BOX_FLIP_OVER_AFTER_CRASH in boxes
                    and ch.throttle < FLIP_MAX_THROTTLE
                    and (abs(roll_a) > FLIP_ENTER_DEG
                         or abs(pitch_a) > FLIP_ENTER_DEG)):
                self._flipping = True
            if self._flipping:
                if abs(roll_a) > abs(pitch_a):
                    targets = (targets[0] + _flip_over_dir(roll_a) * FLIP_OVER_RATE,
                               targets[1], targets[2])
                else:
                    targets = (targets[0], targets[1] + _flip_over_dir(pitch_a)
                               * FLIP_OVER_RATE, targets[2])
        # TPA：衰减因子乘到三轴 PID 输出后进混控。油门取上一帧四电机均值——比杆量
        # 多含饱和信息（饱和时均值≠杆量），且用历史值无代数环。真实 BF 只衰减 P/I
        # （真机 D 项重滤波、噪声主导，衰减反放大噪声）；本仿真 gyro 干净、D 无滤波，
        # D 增益同样随油门放大并越离散稳定边界（D×K 天花板），故 P/I/D 三项全衰减
        f = tpa_factor(self._tpa_throttle, self._tune.tpa_percent,
                       self._tune.tpa_breakpoint)
        outs = (self._pid["roll"].update(targets[0], gyro_dps[0], dt) * f,
                self._pid["pitch"].update(targets[1], gyro_dps[1], dt) * f,
                self._pid["yaw"].update(targets[2], gyro_dps[2], dt) * f)
        motors = mix_quad_x(ch.throttle, outs[2], outs[0], outs[1])
        self._tpa_throttle = sum(motors) / len(motors)
        return motors

    def motor_thrusts(self, motors: list) -> list:
        """单电机最大推力 = 推重比(参考电压下)×重量/4 × (V/REF_VOLTAGE)²；实际 = T_max×motor²。
        电池电压越高推力越大（满电 4S 16.8V → ×1.288、3S 11.1V → ×0.563），
        悬停油门相应降低"""
        weight_kg = self._params.weight_g / 1000.0
        v_scale = (self._params.battery_voltage / REF_VOLTAGE) ** 2
        t_max = weight_kg * 9.81 * self._params.thrust_ratio * v_scale / 4.0
        return [t_max * m * m for m in motors]
