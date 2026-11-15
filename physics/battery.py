"""真实电池模拟模型（physics 层纯计算：无渲染/UI 依赖，纪律同 drone.py）。
电流模型：满油门参考电流 = 20A×机体推重比（需求锚定机体而非电池——电池
额定放电电流 Ah×C 是能力上限，只进内阻：低 C 高内阻 → 推油大压降）；实际
电流 = 参考×电机输出³均值×(V_ocv/V_full)²（功率链：功率∝V³ → 电流∝V²）；
内阻 R = K/(容量Ah×C数)；负载压降 I×R 摊单芯；耗尽后电压钳放空线（只压降
告警不干预，用户裁定）。SoC 运行时状态不落盘；满电恢复由上层调 reset()。
告警阈值不写死：警告/最低单芯电压为可配置参数（用户裁定），对应固件
vbatwarningcellvoltage / vbatmincellvoltage 语义。"""
from __future__ import annotations

# 化学体系表：full/empty = 满电/放空单芯电压(V)；r_k = 内阻系数(Ω·Ah，
# R_pack = r_k/(容量Ah×C数))；curve = 分段线性 OCV 曲线 (SoC, 单芯V)，
# SoC 降序锚点、端点即 full/empty。量级对照：1.5Ah/100C LiPo 包 R≈5mΩ、
# 满油门压降 0.75V ≈ 0.19V/芯；Li-ion 内阻高一个量级（r_k=6.0）；LiHV
# 满电 4.35V/芯（高压充电，放电曲线整体上移）
CHEMS = {
    "lipo": {
        "label": "LiPo", "full": 4.20, "empty": 3.50, "r_k": 0.75,
        # (SoC, 单芯V) 锚点：满电段陡、中段缓、放空段陡；soc<0 为过放尾段
        # （放干后继续放电电压继续下探，LAND NOW 可达——用户裁定）
        "curve": ((1.00, 4.20), (0.85, 4.05), (0.70, 3.95), (0.50, 3.86),
                  (0.30, 3.78), (0.15, 3.70), (0.05, 3.60), (0.00, 3.50),
                  (-0.10, 3.00), (-0.25, 2.50)),
    },
    "lihv": {
        "label": "LiHV", "full": 4.35, "empty": 3.55, "r_k": 0.65,
        "curve": ((1.00, 4.35), (0.85, 4.15), (0.70, 4.00), (0.50, 3.88),
                  (0.30, 3.79), (0.15, 3.72), (0.05, 3.62), (0.00, 3.55),
                  (-0.10, 3.05), (-0.25, 2.55)),
    },
    "liion": {
        "label": "Li-ion", "full": 4.20, "empty": 2.80, "r_k": 6.0,
        "curve": ((1.00, 4.20), (0.90, 4.02), (0.70, 3.85), (0.50, 3.68),
                  (0.30, 3.52), (0.15, 3.30), (0.05, 3.00), (0.00, 2.80),
                  (-0.10, 2.50), (-0.25, 2.20)),
    },
}


def ocv_cell(chem: str, soc: float) -> float:
    """单芯开路电压 V：SoC 在曲线上分段线性插值（soc 钳 [0,1]）。未知 chem
    （外部编辑/旧档脏值）回落 lipo，不抛异常"""
    c = CHEMS.get(chem, CHEMS["lipo"])
    s = min(1.0, max(c["curve"][-1][0], soc))   # 下限=尾段端点（过放域）
    for (s1, v1), (s2, v2) in zip(c["curve"], c["curve"][1:]):
        if s >= s2:                     # 曲线 SoC 降序：s 落 [s2, s1] 段
            return v2 + (v1 - v2) * (s - s2) / (s1 - s2)
    return c["empty"]                   # 浮点下越界兜底


# 满油门参考电流密度 A/(推重比单位)：默认机推重比 7 → 140A 满油门（典型
# 5 寸自由飞量级）。电流需求锚定机体而非电池——电池额定放电电流（Ah×C）
# 是能力上限，只进内阻（低 C 高内阻 → 大压降）
C_REF_A_PER_RATIO = 20.0


class BatterySim:
    """真实电池模拟运行时状态机（运行时状态不落盘，armed 同族——满电恢复由
    上层 reset() 触发；set_params 重建时配置未变则保留 SoC，判定见
    config_matches）。逐帧推进顺序：目标电流（按上一帧电机负载）→ 一阶惯性
    平滑（τ=50ms）→ 负载压降 I×R 摊单芯 → 钳放空线 → 电量积分（mAh =
    I·dt/3.6）→ SoC 钳 0（放电单向，不模拟充电）"""
    I_SMOOTH_TAU_S = 0.05    # 电流一阶惯性时间常数（秒）

    def __init__(self, bp, thrust_ratio: float = 7.0):
        self._bp = bp
        self._ratio = max(1.0, thrust_ratio)   # 机体推重比（电流需求锚）
        self.reset()

    def set_thrust_ratio(self, thrust_ratio: float) -> None:
        """机体推重比更新（set_params 配置未变路径刷新——同一块电池，
        电流需求随机体而变）"""
        self._ratio = max(1.0, thrust_ratio)

    def reset(self) -> None:
        """恢复满电（= 换新电池）：SoC/消耗/电流回初值"""
        chem = CHEMS.get(self._bp.chem, CHEMS["lipo"])
        self._soc_raw = 1.0
        self._mah_drawn = 0.0
        self._i = 0.0                   # 平滑电流（A）
        self._v = max(1, self._bp.cells) * chem["full"]

    def config_matches(self, bp) -> bool:
        """电池配置一致性判定（set_params 保留 SoC：配置未变=同一块电池，
        变更=换电池）"""
        fields = ("sim_enabled", "chem", "cells", "capacity_mah", "c_rating")
        return all(getattr(bp, k) == getattr(self._bp, k) for k in fields)

    def update(self, motors, dt: float) -> None:
        """按四电机输出 [0,1] + tick 时长推进状态（dt≤0 跳过）。motors 取
        上一帧值（消费侧统一在 update 入口推进，电流-压降-消耗一帧不落）"""
        if dt <= 0:
            return
        b = self._bp
        chem = CHEMS.get(b.chem, CHEMS["lipo"])
        cap_ah = max(1, b.capacity_mah) / 1000.0
        c = max(1, b.c_rating)
        r_pack = chem["r_k"] / (cap_ah * c)     # 内阻 Ω：容量/C 数越大越低
        cells = max(1, b.cells)
        v_full = cells * chem["full"]
        mean3 = sum(m ** 3 for m in motors) / 4.0    # 电机输出³均值
        v_oc = cells * ocv_cell(b.chem, self._soc_raw)
        # 电流需求锚定机体推重比（电池额定值只进内阻——额定放电电流是电池
        # 能力上限而非需求；需求侧若锚定容量/C 数，会出现同油门下高 C 电池
        # 耗电更快的悖论）。功率链：I ∝ V²（功率∝V³）
        i_ref = C_REF_A_PER_RATIO * self._ratio
        i_target = i_ref * mean3 * (v_oc / v_full) ** 2
        k = min(1.0, dt / self.I_SMOOTH_TAU_S)       # 电流惯性（大 dt 一步到位）
        self._i += (i_target - self._i) * k
        sag = self._i * r_pack / cells    # 单芯压降 V
        # 下限=曲线尾段端点（过放极限压），非放空线——放干后继续放电电压
        # 继续下探（用户裁定：要能完全放干）
        v_cell = max(chem["curve"][-1][1],
                     ocv_cell(b.chem, self._soc_raw) - sag)
        self._v = cells * v_cell
        # 电量积分：mAh = I(A)·dt(s)/3.6；SoC 原始值可负（过放域），对外
        # 视图钳 0（soc 属性）
        self._mah_drawn += self._i * dt / 3.6
        self._soc_raw = 1.0 - self._mah_drawn / 1000.0 / cap_ah

    # ---- 只读视图（flight_snapshot / 单测消费） ----

    @property
    def cell_voltage(self) -> float:
        """单芯负载电压 V（告警状态机输入——固件 vbat ADC 同为负载实测压）"""
        return self._v / max(1, self._bp.cells)

    @property
    def state(self) -> str:
        """告警状态 "ok"|"warning"|"critical"（负载芯压 vs 可配置阈值，
        阈值不写死）。空载电压回弹后告警自然解除（真机同行为）"""
        cv = self.cell_voltage
        if cv < self._bp.min_cell_v:
            return "critical"
        if cv < self._bp.warning_cell_v:
            return "warning"
        return "ok"

    @property
    def voltage(self) -> float:
        """总压负载电压 V"""
        return self._v

    @property
    def ocv_v(self) -> float:
        """当前 SoC 的开路电压 V（空载回弹目标）"""
        return max(1, self._bp.cells) * ocv_cell(self._bp.chem, self._soc_raw)

    @property
    def current_a(self) -> float:
        """平滑后电流 A"""
        return self._i

    @property
    def soc(self) -> float:
        """剩余电量 [0,1]（过放域原始值为负，对外钳 0）"""
        return max(0.0, self._soc_raw)

    @property
    def mah_drawn(self) -> int:
        """累计消耗 mAh（取整，固件 u16 口径）"""
        return int(self._mah_drawn)

    @property
    def cells(self) -> int:
        """节数（BatterySim 内 1 下限钳位视图）"""
        return max(1, self._bp.cells)

    @property
    def capacity_mah(self) -> int:
        """容量 mAh（快照/单测消费）"""
        return self._bp.capacity_mah

    def snapshot(self) -> dict:
        """OSD compose_state / MSP BATTERY_STATE(130) 消费的实时域快照"""
        return {
            "voltage_v": self._v,
            "ocv_v": self.ocv_v,
            "cells": self.cells,
            "capacity_mah": self._bp.capacity_mah,
            "mah_drawn": self.mah_drawn,
            "amperage_a": self._i,
            "state": self.state,
            "soc": self.soc,
        }
