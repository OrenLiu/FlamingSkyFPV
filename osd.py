# osd.py
"""OSD 元素数据表、位置编解码与内容生成（MSP Task 23 建表；Task 27 内容
生成；Task 28 渲染消费；Task 40 用户实飞反馈——元素渲染对齐固件 4.5.1
osd_elements.c：电压/RSSI/vario 补图标字形，十字线锚点=左段，侧边栏改
固件竖轨+水平箭头；Task 47 补人工地平线/返航距离/飞行里程三元素；
Task 49 修 AH 俯仰方向（bullet 语义 pitch 正=低头 → 线上移）并扩充
11 元素：电流/电量/GPS 星数诚实零值、警告、数值航向、RTC 日期时间、
反乌龟箭头、三 profile 名、天顶天底参考点；Task 52 批二：RC 通道/
累计飞行次数/瓦时/AUX 值四数值元素 + 双摇杆叠层（字形经 .mcm 位图
实测齐备）+ WARNINGS 改实时条件 + RTC 走时（快照侧））。

仅纯数据表与无依赖纯函数，不 import 项目模块（依赖方向单向：
msp/handlers 与 screens 可 import 本表；改动需保持无 GL/pygame/imgui——
osd_font 的 GL 图集是渲染侧职责，特殊字形以同值常量输出索引）。零
import 纪律含标准库（连 math 也不用）：三角/时间换算在调用方
（screens/flight.py 快照），本表只做算术与查表。

元素序对齐固件 Betaflight 4.5.1 src/main/osd/osd.h 的 osd_items_e 枚举
（2026-11-12 在线核对：OSD_RSSI_VALUE=0 ... OSD_GPS_LAP_TIME_BEST3=79 共
80 项，与 Configurator chooseDisplayFields API 1.46 分支 80 项互证）；
未列入 OSD_IMPLEMENTED 的 id 位置恒 0（地面站列表中不可见/不可拖放）。
"""

# 首批实现元素表：id → 元素名（osd_items_e 枚举名；以 Task 25 实测清单校准）。
# Task 47 增补 3/31/47、Task 49 增补 11 元素——id 核实依据：固件 4.5.1
# osd.h osd_items_e 自 OSD_RSSI_VALUE=0 逐项机械计数（与现有表锚点
# 15/22/26/27/29/33 及 3/31/47 全部交叉吻合）。注意 UP_DOWN_REFERENCE=60
# （61 是 TX_UPLINK_POWER——简报曾标 61 系计数偏差，以固件枚举为准）
OSD_IMPLEMENTED: dict[int, str] = {
    0: "RSSI_VALUE",
    1: "MAIN_BATT_VOLTAGE",
    2: "CROSSHAIRS",
    3: "ARTIFICIAL_HORIZON",
    4: "HORIZON_SIDEBARS",
    5: "TIMER_1",
    6: "TIMER_2",
    7: "FLYMODE",
    8: "CRAFT_NAME",
    9: "THROTTLE_POS",
    11: "CURRENT_DRAW",
    12: "MAH_DRAWN",
    13: "GPS_SPEED",
    14: "GPS_SATS",
    15: "ALTITUDE",
    21: "WARNINGS",
    22: "AVG_CELL_VOLTAGE",
    26: "PITCH_ANGLE",
    27: "ROLL_ANGLE",
    29: "DISARMED",
    31: "HOME_DIST",
    32: "NUMERICAL_HEADING",
    33: "NUMERICAL_VARIO",
    38: "RTC_DATETIME",
    45: "FLIP_ARROW",
    47: "FLIGHT_DIST",
    48: "STICK_OVERLAY_LEFT",
    49: "STICK_OVERLAY_RIGHT",
    52: "RATE_PROFILE_NAME",
    53: "PID_PROFILE_NAME",
    54: "PROFILE_NAME",
    56: "RC_CHANNELS",
    59: "TOTAL_FLIGHTS",
    60: "UP_DOWN_REFERENCE",
    62: "WATT_HOURS_DRAWN",
    63: "AUX_VALUE",
}

# Task 49 跳过清单（有据不实现；id 均经 osd_items_e 逐项数出核对；
# Task 52 批二移出并实装：STICK_OVERLAY_LEFT/RIGHT(48/49)——字形经
# assets/osd_betaflight.mcm 位图实测齐备（0x0B/0x16/0x17/0x08-0x0A，
# 对照固件 drivers/osd_symbols.h 同码位，见 GLYPH_STICK_* 注释）；
# RC_CHANNELS(56)/TOTAL_FLIGHTS(59)/WATT_HOURS_DRAWN(62)/AUX_VALUE(63)
# ——数值元素，真值/诚实零值均有落点）：
# - G_FORCE(42)：world 无加速度输出——RAW_IMU(102) 的 acc 为静态常量
#   (0,0,2048)=1g 占位非真实数据，不许编造，待物理域有 acc 再接入
# - 字形依赖（不可 ASCII 化）：VTX_CHANNEL(10)、COMPASS_BAR(34)、
#   CAMERA_FRAME(57)
# - 无容量/电流模型（诚实零值原则不够——依赖容量积分）：MAIN_BATT_
#   USAGE(28)、REMAINING_TIME_ESTIMATE(37)、EFFICIENCY(58)
# - 无 GPS 域：GPS_LON/LAT(23/24)、HOME_DIR(30)
# - 无对应仿真域/简报未列：ROLL/PITCH/YAW_PIDS(16-18)、POWER(19)、
#   PIDRATE_PROFILE(20)、DEBUG(25)、ESC_TMP/RPM/RPM_FREQ(35/36/51)、
#   ADJUSTMENT_RANGE(39)、CORE_TEMPERATURE(40)、ANTI_GRAVITY(41)、
#   MOTOR_DIAG(43)、LOG_STATUS(44)、LINK_QUALITY(46)、PILOT_NAME(50)、
#   RSSI_DBM(55)、TX_UPLINK_POWER(61)、READY_MODE(64)、RSNR(65)、
#   SYS_*(66-76)、GPS_LAP_*(77-79)

# 元素总数 = 固件 osd.h OSD_ITEM_COUNT（MSP_OSD_CONFIG 的 item_count 与
# SET_OSD_CONFIG 元素 addr 上界）
OSD_ITEM_COUNT = 80

# 出厂默认布局 u16 值表（与 OSD_IMPLEMENTED 键对齐）：
# raw = col | (row << 5) | 0x0800（30×16 PAL 网格 col 0-29、row 0-15；
# bit11=VISIBLE 可见位，Configurator osd_constants.ts 同值）
DEFAULT_LAYOUT: dict[int, int] = {
    0: 2370,   # RSSI_VALUE        (2,10)
    1: 2441,   # MAIN_BATT_VOLTAGE (9,12)
    2: 2253,   # CROSSHAIRS        (13,6)——固件 pgResetFn (midCol-2,midRow-1)
    3: 0,      # ARTIFICIAL_HORIZON——Task 47 出厂未摆放（Configurator
               # defaultPosition=-1 同义：列表可选、默认不显示，用户在 OSD
               # tab 拖放后生效；Task 49 新增 11 元素同此约定）
    4: 2254,   # HORIZON_SIDEBARS  (14,6)——固件 (midCol-1,midRow-1)；Task 40
               # 起两元素锚点对齐固件/Configurator 默认（原 (14,7) 双双居中
               # 系建表期近似；十字线锚点=左段格后 (13,6) 使中心格落 midCol）
    5: 2082,   # TIMER_1           (2,1)
    6: 2114,   # TIMER_2           (2,2)
    7: 2060,   # FLYMODE           (12,0)
    8: 2538,   # CRAFT_NAME        (10,15)
    9: 2434,   # THROTTLE_POS      (2,12)
    11: 0,     # CURRENT_DRAW——Task 49 出厂未摆放（同 3 注释）
    12: 0,     # MAH_DRAWN——同上
    13: 2393,  # GPS_SPEED         (25,10)
    14: 0,     # GPS_SATS——Task 49 出厂未摆放（同 3 注释）
    15: 2391,  # ALTITUDE          (23,10)
    21: 0,     # WARNINGS——Task 49 出厂未摆放（同 3 注释）
    22: 2451,  # AVG_CELL_VOLTAGE  (19,12)——Task 40 起图标化 6 格，默认锚点
               # 左移 1 列（原 (20,12) 5 格）避免与 vario 默认 (25,12) 首格重叠
    26: 2479,  # PITCH_ANGLE       (15,13)
    27: 2466,  # ROLL_ANGLE        (2,13)
    29: 2188,  # DISARMED          (12,4)
    31: 0,     # HOME_DIST——Task 47 出厂未摆放（同 3 注释）
    32: 0,     # NUMERICAL_HEADING——Task 49 出厂未摆放（同 3 注释）
    33: 2457,  # NUMERICAL_VARIO   (25,12)
    38: 0,     # RTC_DATETIME——Task 49 出厂未摆放（同 3 注释）
    45: 0,     # FLIP_ARROW——同上
    47: 0,     # FLIGHT_DIST——Task 47 出厂未摆放（同 3 注释）
    48: 0,     # STICK_OVERLAY_LEFT——Task 52 出厂未摆放（同 3 注释）
    49: 0,     # STICK_OVERLAY_RIGHT——同上
    52: 0,     # RATE_PROFILE_NAME——Task 49 出厂未摆放（同 3 注释）
    53: 0,     # PID_PROFILE_NAME——同上
    54: 0,     # PROFILE_NAME——同上
    56: 0,     # RC_CHANNELS——Task 52 出厂未摆放（同 3 注释）
    59: 0,     # TOTAL_FLIGHTS——同上
    60: 0,     # UP_DOWN_REFERENCE——Task 49 出厂未摆放（同 3 注释）
    62: 0,     # WATT_HOURS_DRAWN——Task 52 出厂未摆放（同 3 注释）
    63: 0,     # AUX_VALUE——同上
}

# 可见位掩码（osd_constants.ts VISIBLE=0x0800；固件单 OSD profile 的
# VISIBLE 语义同源）与位置位掩码（osd.h OSD_POS_MAX=0x7FF）
OSD_VISIBLE = 0x0800
OSD_POS_MASK = 0x07FF


def unpack_pos(raw: int) -> tuple[int, int]:
    """u16 布局值 → (col, row)：剥可见位后取 5 位域（osd.h OSD_X/OSD_Y）。"""
    pos = raw & OSD_POS_MASK
    return pos & 0x1F, (pos >> 5) & 0x1F


def pack_pos(col: int, row: int, visible: bool) -> int:
    """(col, row, 可见) → u16 布局值（域外位截断防漂移；变体位恒 0）。"""
    pos = (col & 0x1F) | ((row & 0x1F) << 5)
    return pos | (OSD_VISIBLE if visible else 0)


# ---- 内容生成段（Task 27；Task 28 飞行页渲染消费） ----

OSD_COLS = 30   # MAX7456 PAL 字符网格（Configurator VIDEO_DEFAULTS PAL=30×16）
OSD_ROWS = 16

# 特殊字形索引：与 osd_font.GLYPH_* 同值（本模块不 import osd_font——其
# GL 段属渲染侧；同值锁定由 test/test_osd.py 拦截漂移，Task 28 直接拿
# build_frame 输出的索引进图集）。命名与固件 osd_symbols.h 的 SYM_* 对应，
# 索引逐一对照实装字体位图核对（Task 40；0x77/0x78 小右/左箭头仅
# osd_font 侧具名——本模块无消费者，固件侧边栏实际用 AH_LEFT/AH_RIGHT）。
GLYPH_RSSI = 0x01                    # RSSI 信号条图标（SYM_RSSI）
GLYPH_AH_RIGHT = 0x02                # 侧边栏水平指示箭头·右（SYM_AH_RIGHT）
GLYPH_AH_LEFT = 0x03                 # 侧边栏水平指示箭头·左（SYM_AH_LEFT）
GLYPH_VOLT = 0x06                    # 电压单位 V 专用字形（SYM_VOLT）
GLYPH_AH_DECORATION = 0x13           # 侧边栏竖轨菱形装饰点（SYM_AH_DECORATION）
GLYPH_AH_CENTER_LINE_LEFT = 0x72    # 人工地平线中线左段
GLYPH_AH_CENTER = 0x73              # 中线中心（十字中心）
GLYPH_AH_CENTER_LINE_RIGHT = 0x74   # 中线右段
GLYPH_ARROW_SMALL_UP = 0x75          # vario 上升小箭头（SYM_ARROW_SMALL_UP）
GLYPH_ARROW_SMALL_DOWN = 0x76        # vario 下降小箭头（SYM_ARROW_SMALL_DOWN）
GLYPH_ARROW_SOUTH = 0x60             # 罗盘大箭头·下（Task 49，FLIP_ARROW
GLYPH_ARROW_EAST = 0x64              # 消费；SYM_ARROW_SOUTH/EAST/NORTH/WEST
GLYPH_ARROW_NORTH = 0x68             # 十六方位族 0x60-0x6F 的四个主方位，
GLYPH_ARROW_WEST = 0x6C              # 固件 osdElementCrashFlipArrow 同族）
GLYPH_BATT_FULL = 0x90               # 电池图标族·满格（SYM_BATT_FULL）
GLYPH_BATT_EMPTY = 0x96              # 电池图标族·空（SYM_BATT_EMPTY；段数
                                     # = 0x96-图标，0..6 白段自下而上递增）
GLYPH_MPS = 0x9F                     # m/s 单位字形（SYM_MPS，公制 vario 尾缀）
GLYPH_STICK_SPRITE_HIGH = 0x08       # 摇杆叠层光标三片 sprite（SYM_STICK_
                                     # OVERLAY_SPRITE_HIGH/MID/LOW=0x08/09/0A，
GLYPH_STICK_SPRITE_MID = 0x09        # 固件 drivers/osd_symbols.h L157-159 同
GLYPH_STICK_SPRITE_LOW = 0x0A        # 码；光标片号 = HIGH + cursorY%3）
GLYPH_STICK_CENTER = 0x0B            # 叠层十字中心（SYM_STICK_OVERLAY_CENTER）
GLYPH_STICK_VERTICAL = 0x16          # 叠层竖杆（SYM_STICK_OVERLAY_VERTICAL）
GLYPH_STICK_HORIZONTAL = 0x17        # 叠层横杆（SYM_STICK_OVERLAY_HORIZONTAL）
                                     # 以上五族字形经 assets/osd_betaflight.
                                     # mcm 位图实测齐备（0x0B=十字、0x16=
                                     # 竖线、0x17=横线、0x08-0x0A=上/中/
                                     # 下三片光标点，'_' 0x5F 同款核对手法）

# 固件侧边栏几何（osd_elements.c AH_SIDEBAR_WIDTH_POS/HEIGHT_POS）：
# 锚点 ±7 列竖轨、锚点行 ±3 共 7 行，水平箭头在 ±(7-1) 列
_AH_SIDEBAR_WIDTH_POS = 7
_AH_SIDEBAR_HEIGHT_POS = 3

# 固件摇杆叠层几何（osd_elements.c L181-184 OSD_STICK_OVERLAY_* 同值）：
# 7×5 十字背景（竖杆在中央列、横杆在中央行、交叉格用十字中心字形），
# 光标纵向 5 行 × 每行 3 片 sprite = 15 个子位（亚字形精度）
_STICK_W = 7
_STICK_H = 5
_STICK_SPRITE_H = 3
_STICK_VPOS = 15

# FLYMODE 消费的 box id（与 physics.drone.BOX_ANGLE/BOX_HORIZON 同源；
# 本模块不 import physics，保持依赖方向单向。FLIP_ARROW Task 54 起改门
# 控 state["flip_latched"]（fc 解锁锁存）——box21 实时值不再被本模块消费）
_BOX_ANGLE = 1
_BOX_HORIZON = 2

# ---- Task 49 新元素常量（零 import：三角值以字面量钉死并注明来源） ----
_SINE_25_DEG = 0.422618261740699        # 固件 osd_elements.c SINE_25_DEG 同值
# UP_DOWN_REFERENCE 偏移斜率：固件 scaleRangef(±π/4→±14/±8) 的斜率
# 28/(π/2)=56/π 与 16/(π/2)=32/π（π 字面量写入分母保可读）
_UPDOWN_PSI_SCALE = 56.0 / 3.141592653589793    # 列偏移/psiB
_UPDOWN_THETA_SCALE = 32.0 / 3.141592653589793  # 行偏移/thetaB
# 反乌龟箭头杆死区（归一杆量）：drone.CRASHFLIP_IDLE_MIN=0.02 同源值
# （固件 CRASH_FLIP_DEADBAND=20 换算），本地重声明保零 import
_FLIP_STICK_EPS = 0.02
# 十六方位字母表（顺时针序，扇区 0=N；固件用同序专用箭头字形族，我们无
# 对应字母字形族故用 ASCII——osdGetDirectionSymbolFromHeading 的离散化）
_HEADING16 = ("N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
              "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW")

# 字体 ASCII 恒等区（osd_font 注释同源：0x20-0x5A 直接 ord(ch) 寻址）
_ASCII_LO = 0x20
_ASCII_HI = 0x5A
_GLYPH_UNDERSCORE = 0x5F  # 下划线（Task 49：0x5F 字体位图实测为 '_'，
                          # MAX7456 ASCII 段 0x20-0x5A 的顺延——profile 名
                          # "RATE_1" 等消费；0x5B-0x5E 无消费者不放开）
_GLYPH_QUESTION = 0x3F  # 非 ASCII 域归一字符 '?'


def _to_glyph(ch: str) -> int:
    """单字符 → 字形索引（MAX7456 ASCII 域归一）。

    ASCII 恒等区直接给字符码；'_' 给 0x5F（位图实测，见常量注释）；小写
    映射为大写同形（本字体无小写区，0x60+ 是罗盘/地平线特殊字形——
    'h'(0x68) 是罗盘北箭头）；其余（含中文等非 ASCII）归一 '?'，长度
    不变由调用方保证。
    """
    code = ord(ch)
    if _ASCII_LO <= code <= _ASCII_HI:
        return code
    if ch == "_":
        return _GLYPH_UNDERSCORE
    if 0x61 <= code <= 0x7A:
        return code - 0x20
    return _GLYPH_QUESTION


def _osd_float(value: float, dp: int, do_round: bool) -> tuple[int, str]:
    """固件 osd.c osdPrintFloat 数值段：value×10^dp 取整后拆整数/定宽小数。

    do_round=True 用 lrintf 最近偶舍入（Python round 同语义，主压/vario）；
    False 为 (int) 截断（固件 round=false 路径；当前无消费者——avg cell
    数值段 fix round 1 起改走 _avg_cell_cv 整数路径，浮点舍入尘不模拟）。
    输入约定非负（vario 在调用前取 abs，固件对负值仅输出 '-' 且图标元素
    恒非负）。dp=0 时小数段为空串。
    """
    scaled = value * (10 ** dp)
    n = round(scaled) if do_round else int(scaled)
    ip, fp = divmod(n, 10 ** dp)
    return ip, (f"{fp:0{dp}d}" if dp else "")


def _avg_cell_cv(voltage_v: float, cells: int) -> int:
    """平均芯压 cV 整数（battery.c getBatteryAverageCellVoltage 语义：
    总压 cV 整数 // cells，cells=0 → 0）。图标段与数值段共源——浮点除
    会有 0.01V 级下偏（20.15V/5S 浮点 4.0299… 截断 "4.02"，整数 403 cV
    → "4.03"；fix round 1，与固件逐格同构）。
    """
    return (round(voltage_v * 100) // cells) if cells > 0 else 0


def _battery_glyph(voltage_v: float, cells: int) -> int:
    """固件 osdGetBatterySymbol：平均芯压 → 电池图标段数字形。

    cv 见 _avg_cell_cv；段数 = scaleRange(cv, 330, 430, 0, 8) 即
    (8×(cv-330))//100 截断除（maths.c 整数版），钳 0..6；图标 =
    SYM_BATT_EMPTY(0x96) - 段数（330/430 = vbatmin/maxcellvoltage 厘伏
    默认值）。BATTERY_CRITICAL → SYM_MAIN_BATT(0x97) 分支不模拟：
    模拟器 battery_voltage 为静态配置、无电量消耗/告警状态机（恒
    BATTERY_OK 语义）。
    """
    cv = _avg_cell_cv(voltage_v, cells)
    seg = min(6, max(0, (8 * (cv - 330)) // 100))
    return GLYPH_BATT_EMPTY - seg


def _int_keyed(d) -> dict:
    """osd_layout/osd_enabled 键 int 归一：dict[int] 经 config_store JSON
    round-trip 后键变 str（json 模块限制），消费端统一转回；不可归一
    条目丢弃。非 dict 输入返回空表（缺键防御）。"""
    out = {}
    if not isinstance(d, dict):
        return out
    for k, v in d.items():
        try:
            out[int(k)] = v
        except (TypeError, ValueError):
            continue
    return out


def _stick_rp(stick_us) -> tuple:
    """stick_us 4 元组 → (roll, pitch) 归一杆量 [-1,1]（1500 中心 ±500）。

    FLIP_ARROW 消费（杆向判箭头）；缺省/长度不足 → (0, 0)（杆回中语义，
    快照 stick_us 恒 4 元组，防御裸 dict 直喂场景）。
    """
    if not stick_us or len(stick_us) < 2:
        return (0.0, 0.0)
    return ((stick_us[0] - 1500.0) / 500.0, (stick_us[1] - 1500.0) / 500.0)


def compose_state(params, flight, preset_id, now_s) -> dict:
    """飞行页与单测共用的 state 组装（Task 28 flight.py 消费）。

    flight 为 flight_snapshot() dict 或 None（未起飞/暂停：遥测字段全零、
    armed False——缺键防御同语义，产出的 state 恒全键）；craft_name 取
    virtual.craft_name，空则回落 preset_id。Task 49 增四键：arm_denied
    （解锁拒绝闩锁）、rtc_text（SET_RTC 时间串，空=未设置）、stick_rp
    （归一杆量）、up_body（世界 +Z 机体坐标，None=无姿态数据→元素不渲染
    ——up_body 无诚实零值，(0,0,0) 会误判 UP_DOWN_REFERENCE 阈内）。
    Task 52 增三键：total_flights（virtual 累计飞行次数，TOTAL_FLIGHTS
    元素）、stick_us（原始 4 通道 µs，RC_CHANNELS/摇杆叠层消费；None=
    缺键→两元素不渲染）、arming_blocked（实时解锁被拒条件，WARNINGS
    消费——闩锁 arm_denied 键保留但渲染侧不再消费）。Task 54 增
    flip_latched（反乌龟解锁锁存，FLIP_ARROW 门控消费——与实时 boxes
    分离：锁存会话中途关开关箭头不消失）。
    """
    f = flight or {}
    return {
        "layout": params.virtual.osd_layout,
        "enabled": params.virtual.osd_enabled,
        "armed": bool(f.get("armed", False)),
        "boxes": f.get("boxes") or frozenset(),
        "flip_latched": bool(f.get("flip_latched", False)),
        "throttle": f.get("throttle", 0.0),
        "voltage_v": params.battery_voltage,
        "cells": params.virtual.cells,
        "speed_mps": f.get("speed_mps", 0.0),
        "alt_m": f.get("alt_m", 0.0),
        "dist_home_m": f.get("dist_home_m", 0),      # Task 47：无起飞点/
        "dist_flown_m": f.get("dist_flown_m", 0),    # 从未解锁 → 0（显示 0m）
        "vario_mps": f.get("vario_mps", 0.0),
        "attitude_deg": f.get("attitude_deg") or (0.0, 0.0, 0.0),
        "flight_time_s": f.get("flight_time_s", 0.0),
        "craft_name": params.virtual.craft_name or preset_id,
        "now_s": now_s,
        "arm_denied": bool(f.get("arm_denied", False)),
        "rtc_text": f.get("rtc_text", ""),
        "stick_rp": _stick_rp(f.get("stick_us")),
        "up_body": f.get("up_body"),
        "total_flights": getattr(getattr(params, "virtual", None),
                                 "total_flights", 0),
        "stick_us": f.get("stick_us"),
        "arming_blocked": bool(f.get("arming_blocked", False)),
    }


def build_frame(state: dict) -> dict:
    """设计 §9：飞行状态 → {(col, row): (glyph_index, blink)} 纯函数。

    键 (col, row) 为 30×16 网格坐标，值 (glyph_index, blink)——
    glyph_index 可被 osd_font 图集直接索引（ASCII 字符=字符码恒等，
    特殊字形=GLYPH_* 常量）；blink=True 仅 DISARMED（2Hz，相位关
    int(now_s*2)%2==1 时整元素不画，十字线/侧边栏等不受相位影响）。

    布局合成：锚点 = layout.get(id) 覆盖，缺省 DEFAULT_LAYOUT；可见性 =
    enabled.get(id) 覆盖，缺省「位置位非零」（与 MSP 侧 handlers._elem_pos
    同语义，地面站改位/开关后经 config_store round-trip 一致生效）。
    越界钳位与截断在生成端完成：锚点钳入网格、文本逐字符截断到 30 列、
    稀疏字形出界格不画。未实现元素（OSD_IMPLEMENTED 之外）忽略。
    空 state 返回空网格（防御）。
    """
    if not state:
        return {}
    layout = _int_keyed(state.get("layout"))
    enabled = _int_keyed(state.get("enabled"))
    boxes = state.get("boxes") or frozenset()
    flip_latched = state.get("flip_latched", False)   # Task 54：反乌龟解锁锁存
    armed = bool(state.get("armed", False))
    throttle = state.get("throttle", 0.0)
    voltage_v = state.get("voltage_v", 0.0)
    cells = state.get("cells", 0)
    speed_mps = state.get("speed_mps", 0.0)
    alt_m = state.get("alt_m", 0.0)
    dist_home = state.get("dist_home_m")     # None=缺键（裸 state 防御）→ 不画
    dist_flown = state.get("dist_flown_m")
    vario_mps = state.get("vario_mps", 0.0)
    att = state.get("attitude_deg") or (0.0, 0.0, 0.0)
    flight_time_s = max(0.0, state.get("flight_time_s", 0.0))
    craft_name = state.get("craft_name", "")
    now_s = state.get("now_s", 0.0)
    rtc_text = state.get("rtc_text")         # None=缺键 → 不渲染（dist_home 惯例）
    stick_rp = state.get("stick_rp") or (0.0, 0.0)
    up_body = state.get("up_body")           # None=缺键/无姿态 → 不渲染
    total_flights = state.get("total_flights", 0)
    stick_us = state.get("stick_us")         # None=缺键 → RC_CHANNELS/叠层不渲染
    arming_blocked = bool(state.get("arming_blocked", False))
    # 注：state["arm_denied"]（Task 49 闩锁键，compose_state 恒填充）自
    # Task 52 起 WARNINGS 改读实时 arming_blocked，不再消费——键保留供
    # 快照层既有消费方（flight_snapshot 字段/单测）向后兼容

    frame = {}

    def put(col: int, row: int, glyph: int, blink: bool = False) -> None:
        if 0 <= col < OSD_COLS and 0 <= row < OSD_ROWS:
            frame[(col, row)] = (glyph, blink)

    def put_text(text: str, col: int, row: int, blink: bool = False) -> None:
        for i, ch in enumerate(text):
            put(col + i, row, _to_glyph(ch), blink)

    for elem_id in OSD_IMPLEMENTED:
        raw = layout.get(elem_id)
        if raw is None:
            raw = DEFAULT_LAYOUT.get(elem_id, 0)
        try:
            pos = int(raw) & OSD_POS_MASK
        except (TypeError, ValueError):
            continue
        visible = enabled.get(elem_id)
        if visible is None:
            visible = pos != 0
        if not visible:
            continue
        col, row = unpack_pos(pos)
        col = min(col, OSD_COLS - 1)
        row = min(row, OSD_ROWS - 1)

        if elem_id == 0:          # RSSI_VALUE：信号条图标+%2d（模拟恒 99）
            put(col, row, GLYPH_RSSI)
            put_text(f"{99:2d}", col + 1, row)
        elif elem_id == 1:        # MAIN_BATT_VOLTAGE：电池图标+数值+V 字形
            # 固件 osdElementMainBatteryVoltage：图标按平均芯压分段；
            # 电压 ≥10V 一位小数、否则两位（osdPrintFloat round=lrintf）
            dp = 1 if voltage_v >= 10 else 2
            ip, fp = _osd_float(voltage_v, dp, True)
            text = f"{ip}.{fp}"
            put(col, row, _battery_glyph(voltage_v, cells))
            put_text(text, col + 1, row)
            put(col + 1 + len(text), row, GLYPH_VOLT)
        elif elem_id == 2:        # CROSSHAIRS：锚点=左段格（固件 buff 自
            put(col, row, GLYPH_AH_CENTER_LINE_LEFT)   # elemPosX 起写三字形）
            put(col + 1, row, GLYPH_AH_CENTER)
            put(col + 2, row, GLYPH_AH_CENTER_LINE_RIGHT)
        elif elem_id == 3:        # ARTIFICIAL_HORIZON（Task 47；设计更正
            # round 1——固件 osdElementArtificialHorizon 语义）：9 列阶梯
            # 倾斜线，非平移线。每列 x∈[-4,+4] 行号 = 基准行 -
            # round(roll_deg·x/60)——右滚（roll 正）→ 线右端上翘、左端
            # 下沉（固件 y = -roll·x/64 - pitch_scaled，rollAngle 单位
            # 0.1°：x=+4、45° 时右端上移 28 子格 ≈ 3 字符行；/60 为同构
            # 比例约定，45° 两端各偏 3 行与固件一致）。基准行 = 锚点行+4+
            # 钳(-round(pitch/10), ±4)（Task 51 修锚点偏移：OSD 元素锚点=
            # 元素区顶格，固件 osdElementArtificialHorizon 水平姿态 y=41 →
            # 行偏 y/9=4、Configurator osd.ts preview 中线 j=4，双源一致——
            # 水平中线画在锚点下方 4 行，原实现画在锚点行本身致飞行中线
            # 比用户在 OSD tab 摆放的预期高 4 行；pitch 钳位由 ±5 收紧 ±4，
            # 保证基准行恒落锚点起 9 行区界 [锚点+0, 锚点+8]（与固件区高
            # 一致）。Task 49 修复符号链：att[1] 为 bullet 语义
            # world.euler_deg 的 pitch——正=低头（=BF 惯例，固件
            # imu.c "negative is backwards"；euler_stick_deg 翻 y 的反义）；
            # 固件 AH 中 pitch 正（低头）→ 地平线线上移（行号减小）、
            # pitch 负（抬头）→ 线下移，与固件及 Configurator 姿态仪一致
            # ——原实现按杆义抬头正取 +round 恰好反向，俯仰测试一并翻转）。
            # 字符网格简化仍用 '-'（侧倾主体表达由本元素阶梯承担，
            # HORIZON_SIDEBARS(4) 竖轨+水平箭头辅助）；逐格行号出界跳过
            # （put 越界忽略 = 文件头稀疏字形规则）
            drow = min(4, max(-4, -round(att[1] / 10.0)))
            for x in range(-4, 5):
                put(col + x, row + 4 + drow - round(att[0] * x / 60.0), ord("-"))
        elif elem_id == 4:        # HORIZON_SIDEBARS：固件静态背景——±7 列
            # 竖轨（锚点行 ±3）+ ±6 列 AH_LEFT/AH_RIGHT 水平指示箭头；
            # 与姿态无关（roll 平移指示属 ARTIFICIAL_HORIZON(3)，Task 47
            # 起已实现——本元素保持固件静态背景语义）
            for dy in range(-_AH_SIDEBAR_HEIGHT_POS, _AH_SIDEBAR_HEIGHT_POS + 1):
                put(col - _AH_SIDEBAR_WIDTH_POS, row + dy, GLYPH_AH_DECORATION)
                put(col + _AH_SIDEBAR_WIDTH_POS, row + dy, GLYPH_AH_DECORATION)
            put(col - _AH_SIDEBAR_WIDTH_POS + 1, row, GLYPH_AH_LEFT)
            put(col + _AH_SIDEBAR_WIDTH_POS - 1, row, GLYPH_AH_RIGHT)
        elif elem_id in (5, 6):   # TIMER_1/2（均计飞行时间，计划规格）
            mm, ss = int(flight_time_s // 60), int(flight_time_s % 60)
            put_text(f"{mm:02d}:{ss:02d}", col, row)
        elif elem_id == 7:        # FLYMODE（ANGLE 优先于 HORIZON）
            if _BOX_ANGLE in boxes:
                put_text("ANGLE", col, row)
            elif _BOX_HORIZON in boxes:
                put_text("HORZN", col, row)
            else:
                put_text("ACRO", col, row)
        elif elem_id == 8:        # CRAFT_NAME（非 ASCII 由 _to_glyph 归一 '?'）
            put_text(craft_name, col, row)
        elif elem_id == 9:        # THROTTLE_POS
            put_text(f"{throttle * 100:.0f}", col, row)
        elif elem_id == 11:       # CURRENT_DRAW（Task 49）：无电流计量模型
            put_text("0.0A", col, row)   # ——诚实零值（BATTERY_STATE(130)
            # 电流恒 0 同源；固件格式 %3u+2 位小数+SYM_AMP 图标，无模型
            # 不模拟其字形序列）
        elif elem_id == 12:       # MAH_DRAWN（Task 49）：同上诚实零值
            put_text("0mAh", col, row)   # （固件 %4d+SYM_MAH；'m' 小写
            # 映射大写字形，渲染观感 "0MAH"——HOME_DIST 同惯例）
        elif elem_id == 13:       # GPS_SPEED（小写映射大写字形）
            put_text(f"{speed_mps * 3.6:.0f}Kmh", col, row)
        elif elem_id == 14:       # GPS_SATS（Task 49）：无 GPS 域——真机
            put_text("0", col, row)   # 无 fix 同款数值 0（固件 SYM_SAT
            # 图标对+%2d；模拟器连 GPS 设备都无，只显示数值）
        elif elem_id == 15:       # ALTITUDE
            put_text(f"{alt_m:.0f}M", col, row)
        elif elem_id == 21:       # WARNINGS（Task 49；Task 52 改实时条件）：
            # 有数据源的警告子集——ARMING DISABLED。原读 fc.arm_denied_once
            # 闩锁在放弃解锁后仍持续闪烁；固件 renderOsdWarning（osd_
            # warnings.c）按实时状态逐帧求值，故改读快照实时字段
            # arming_blocked（ARM box 激活 ∧ 油门 ≥ ARM_THROTTLE_MAX ∧
            # 未解锁，flight_snapshot 逐帧计算——drone.py 闩锁本体保留供
            # UI 既有消费）。blink=True 2Hz 闪烁（与 DISARMED 同相位源）。
            # 固件全量警告（osd_warnings.c：电池/FAILSAFE/CRASH FLIP/蜂鸣/
            # 温度/RSSI 等 19 项）无对应仿真域，仅实现此一项
            if arming_blocked:
                put_text("ARMING DISABLED", col, row, blink=True)
        elif elem_id == 22:       # AVG_CELL_VOLTAGE：图标+2 位小数+V（cells
            if cells > 0:         # 非法则不画；数值段走 cV 整数整除路径
                cell_cv = _avg_cell_cv(voltage_v, cells)   # （固件两处
                text = f"{cell_cv // 100}.{cell_cv % 100:02d}"  # 同读 getBattery-
                put(col, row, _battery_glyph(voltage_v, cells))  # AverageCell-
                put_text(text, col + 1, row)               # Voltage）
                put(col + 1 + len(text), row, GLYPH_VOLT)
        elif elem_id == 26:       # PITCH_ANGLE（att[1]）
            put_text(f"P{att[1]:+.0f}", col, row)
        elif elem_id == 27:       # ROLL_ANGLE（att[0]）
            put_text(f"R{att[0]:+.0f}", col, row)
        elif elem_id == 29:       # DISARMED（armed 或相位关 → 整元素不画）
            if not armed and int(now_s * 2) % 2 == 0:
                put_text("DISARMED", col, row, blink=True)
        elif elem_id == 31:       # HOME_DIST（Task 47）：到起飞点水平距离
            if dist_home is not None:   # int 米+'m'（小写映射大写字形）；
                put_text(f"{int(dist_home)}m", col, row)  # 缺键跳过不抛
        elif elem_id == 32:       # NUMERICAL_HEADING（Task 49）：数值航向
            # + 十六方位字母。固件 osdElementNumericalHeading 为 "%c%03d"
            # （专用箭头字形族）；我们无该字形族的字母对应，按十六方位
            # 顺时针表用 ASCII（N/NNE/NE/…）。航向语义（测试钉死）：北=
            # 出生机头方向（bullet 世界 +X，reset 姿态 yaw=0）、罗盘顺时
            # 针增；att[2] 为 bullet euler z——正=机头左转（俯视逆时针，
            # euler_stick_deg 翻 z 的反义），故 heading = -att[2] 归一
            # 0..359（朝东=机头右转 90°→att[2]=-90→90/"E090"）。方位
            # 离散化同固件 osdGetHeadingIntoDiscreteDirections：
            # (heading*16+180)//360 %16（半扇区偏置，heading 整数 0..359）
            heading = int(round(-att[2])) % 360
            put_text(f"{_HEADING16[(heading * 16 + 180) // 360 % 16]}"
                     f"{heading:03d}", col, row)
        elif elem_id == 33:       # NUMERICAL_VARIO：升/降箭头+|v| 一位小数
            arrow = (GLYPH_ARROW_SMALL_DOWN if vario_mps < 0
                     else GLYPH_ARROW_SMALL_UP)   # 固件 <0 降、≥0 升；方向由
            ip, fp = _osd_float(abs(vario_mps), 1, True)  # 箭头表达，数值无符号
            text = f"{ip}.{fp}"
            put(col, row, arrow)
            put_text(text, col + 1, row)
            put(col + 1 + len(text), row, GLYPH_MPS)
        elif elem_id == 38:       # RTC_DATETIME（Task 49）：rtc_text 由飞行
            if rtc_text:          # 层快照格式化传入（SET_RTC(246) 存 unix
                put_text(rtc_text, col, row)  # 秒 → "%Y-%m-%d %H:%M:%S"
            # 全格式，19 字符；固件 dateTimeFormatLocalShort
            # "%04u-%02u-%02u %02u:%02u:%02u" 同式——本表不格式化只画
            # 文本）。0=未设置空串。osd 本体零 import 纪律——time 属
            # 调用方职责；缺键/空串不渲染不抛
        elif elem_id == 45:       # FLIP_ARROW（Task 49；Task 54 门控锁存
            # 化）：反乌龟解锁锁存（fc.flip_over_latched——固件
            # osdElementCrashFlipArrow L874 门控消费 isFlipOverAfterCrash
            # Active() 锁存标志而非实时开关；锁存会话中途关开关箭头不
            # 消失，与接管/桨反转接线同源一致）时按杆向显示箭头，指示打
            # 杆翻转方向。固件以姿态主导轴选箭头（|pitch|>|roll| 纵/横
            # + 2:1 对角带专用斜箭头，另有未解锁倒置提示分支）；我们按
            # 简报实现杆向版：主导轴判据同固件（|p|>|r| 纵向、否则横向；
            # 2:1 对角带归并主导轴），杆死区内不画（阈值同 drone.
            # CRASHFLIP_IDLE_MIN 源值）。杆义：roll 右正、pitch 拉杆正
            # （channels.py）→ 前推=NORTH 上箭头、拉杆=SOUTH 下、右=
            # EAST、左=WEST（osd_font 十六方位大箭头字形族 0x60-0x6F）
            if flip_latched:
                roll_s, pitch_s = stick_rp
                if max(abs(roll_s), abs(pitch_s)) > _FLIP_STICK_EPS:
                    if abs(pitch_s) > abs(roll_s):
                        glyph = (GLYPH_ARROW_SOUTH if pitch_s > 0
                                 else GLYPH_ARROW_NORTH)
                    else:
                        glyph = (GLYPH_ARROW_EAST if roll_s > 0
                                 else GLYPH_ARROW_WEST)
                    put(col, row, glyph)
        elif elem_id == 47:       # FLIGHT_DIST（Task 47）：本次解锁累计水平
            if dist_flown is not None:   # 里程（锁定后由 flight 层冻结）
                put_text(f"{int(dist_flown)}m", col, row)
        elif elem_id in (48, 49):  # STICK_OVERLAY_LEFT/RIGHT（Task 52）：
            # 7×5 十字背景 + 杆位光标（固件 osdBackgroundStickOverlay/
            # osdElementStickOverlay）。通道布局按 overlay_radio_mode（出厂
            # 默认 2=美国手：左杆=油门/偏航、右杆=俯仰/横滚，radioModes 表
            # osd_elements.c L204-209；模拟器无该 OSD 配置域也无持久化的
            # 手型选择，固定按固件默认 Mode 2 布局）。光标纵向 15 子位（5
            # 行×3 片 sprite）、横向 7 列，换算同固件 scaleRange 截断除。
            # 油门例外：sim stick_us[3] 是 1500 中心 ±500 约定（MSP RC 线
            # 上同款），固件 rcData[THROTTLE] 则为 1000-2000 单边——物理
            # 油门杆零位在下端，故油门光标改由 state["throttle"]（0..1）
            # 直接映射 1000+1000t（t=0=底行），其余三轴直读 µs。stick_us
            # 缺键（裸 state）→ 整元素不渲染（up_body 惯例）
            if stick_us is not None and len(stick_us) >= 4:
                # 背景：中央列竖杆（跳过中央行）+ 央行横排（中央格十字）
                mid_r = (_STICK_H - 1) // 2
                for r2 in range(_STICK_H):
                    if r2 != mid_r:
                        put(col + (_STICK_W - 1) // 2, row + r2,
                            GLYPH_STICK_VERTICAL)
                for c2 in range(_STICK_W):
                    put(col + c2, row + mid_r,
                        GLYPH_STICK_CENTER if c2 == (_STICK_W - 1) // 2
                        else GLYPH_STICK_HORIZONTAL)
                # 光标（Mode 2：LEFT=油门/偏航、RIGHT=俯仰/横滚）——
                # 后写覆盖背景同格（固件背景每帧重画、光标最后写同序）
                if elem_id == 48:
                    v_us = 1000.0 + 1000.0 * max(0.0, min(1.0, throttle))
                    h_us = stick_us[2]
                else:
                    v_us = stick_us[1]
                    h_us = stick_us[0]
                cx = int((min(max(h_us, 1000.0), 1999.0) - 1000.0)
                         * _STICK_W / 1000.0)
                cy = _STICK_VPOS - 1 - int(
                    (min(max(v_us, 1000.0), 1999.0) - 1000.0)
                    * _STICK_VPOS / 1000.0)
                put(col + cx, row + cy // _STICK_SPRITE_H,
                    GLYPH_STICK_SPRITE_HIGH + cy % _STICK_SPRITE_H)
        elif elem_id == 52:       # RATE_PROFILE_NAME（Task 49）：固件
            put_text("RATE_1", col, row)  # osdElementRateProfileName 默认名
        elif elem_id == 53:       # "RATE_%u"（profileName 空 + index+1）；
            put_text("PID_1", col, row)   # PID_PROFILE_NAME 同款 "PID_%u"；
        elif elem_id == 54:       # PROFILE_NAME（OSD profile）"OSD_%u"——
            put_text("OSD_1", col, row)   # 模拟器单 profile 恒 1
        elif elem_id == 56:       # RC_CHANNELS（Task 52）：4 主通道逐行
            if stick_us is not None:   # %5d 右对齐（固件 osdElementRcChannels
                for i, us in enumerate(stick_us[:4]):   # ：rcData 经
                    # scaleRange(1000,2000,→-1000,1000)=2×µs−3000 整数刻度
                    # （简报「µs−1500」标注系笔误——1500→0 两式同值，但
                    # 2000→+1000 非 +500，以固件函数为准）、每通道一行纵向
                    # 排列；固件 rcChannels[] 映射默认 -1 全不显示，模拟器
                    # 无该配置域，固定显示 roll/pitch/yaw/throttle 四主通道
                    # （行序=stick_us 序）。缺键 → 不渲染
                    put_text(f"{int(round(us * 2.0 - 3000.0)):5d}",
                             col, row + i)
        elif elem_id == 59:       # TOTAL_FLIGHTS（Task 52）：固件
            put_text(f"#{total_flights}", col, row)  # osdElementTotalFlights
            # "#%d"（statsConfig()->stats_total_flights 持久统计；固件计数
            # 时机 = 加锁沿且解锁时长 ≥ stats_min_armed_time_s——出厂
            # STATS_OFF=-1 不计数，模拟器无 stats PG 配置域，按阈值 0 的
            # 计数开启语义在 flight._track_flight_stats 解锁→加锁沿 +1）
        elif elem_id == 60:       # UP_DOWN_REFERENCE（Task 49）：天顶/天底
            # 参考点（固件 osdElementUpDownReference）。显示条件：世界 +Z
            # 单位向量在机体的 y/z 分量均 < sin25°（机头接近正上/正下
            # ±25° 带内才显示）；up_body 由飞行层从 att 推导传入（快照
            # 字段，None=无姿态数据不渲染）——up_y=+sin(roll)cos(pitch)
            # （机体 +Y=左）、up_z=+cos(roll)cos(pitch)（机体 +Z=上），
            # 与 yaw 无关。方向与偏移同构固件：低头（att[1]>0）→ 天底
            # D（thetaB=up_z、psiB=up_y）、抬头 → 天顶 U（两分量取负）；
            # 行偏移=round(thetaB·32/π)、列偏移=round(psiB·56/π)
            # （scaleRangef ±π/4→±8/±14 斜率，本地常量保零 import）；
            # U/D 为 ASCII（固件同款字符）。id=60：61 是 TX_UPLINK_POWER
            # （简报曾标 61 系计数偏差，以固件 osd_items_e 为准）
            if up_body is not None:
                up_y, up_z = up_body[1], up_body[2]
                if abs(up_z) < _SINE_25_DEG and abs(up_y) < _SINE_25_DEG:
                    if att[1] > 0.0:
                        theta_b, psi_b = up_z, up_y
                        letter = "D"
                    else:
                        theta_b, psi_b = -up_z, -up_y
                        letter = "U"
                    put(col + round(psi_b * _UPDOWN_PSI_SCALE),
                        row + round(theta_b * _UPDOWN_THETA_SCALE),
                        ord(letter))
        elif elem_id == 62:       # WATT_HOURS_DRAWN（Task 52）：无电流/容量
            put_text(f"{0:3d}MWH", col, row)  # 模型 → 诚实零值（0Wh < 1 走
            # 固件 osdElementWattHoursDrawn 毫瓦分支 "%3dMWH" = "  0MWH"
            # 逐字对齐；CURRENT_DRAW/MAH_DRAWN 同语义零值）
        elif elem_id == 63:       # AUX_VALUE（Task 52）：固件 "%c%d"（
            put_text("A0", col, row)   # aux_symbol 出厂 'A'，osd.c PG 模板
            # L411；数值段为 aux 通道缩放读数（osd.c L1271），模拟器无
            # AUX 值域模型 → 诚实零值 A0
    return frame
