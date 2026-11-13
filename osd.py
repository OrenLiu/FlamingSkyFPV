# osd.py
"""OSD 元素数据表、位置编解码与内容生成（MSP Task 23 建表；Task 27 内容
生成；Task 28 渲染消费；Task 40 用户实飞反馈——元素渲染对齐固件 4.5.1
osd_elements.c：电压/RSSI/vario 补图标字形，十字线锚点=左段，侧边栏改
固件竖轨+水平箭头）。

仅纯数据表与无依赖纯函数，不 import 项目模块（依赖方向单向：
msp/handlers 与 screens 可 import 本表；改动需保持无 GL/pygame/imgui——
osd_font 的 GL 图集是渲染侧职责，特殊字形以同值常量输出索引）。

元素序对齐固件 Betaflight 4.5.1 src/main/osd/osd.h 的 osd_items_e 枚举
（2026-11-12 在线核对：OSD_RSSI_VALUE=0 ... OSD_GPS_LAP_TIME_BEST3=79 共
80 项，与 Configurator chooseDisplayFields API 1.46 分支 80 项互证）；
未列入 OSD_IMPLEMENTED 的 id 位置恒 0（地面站列表中不可见/不可拖放）。
"""

# 首批实现元素表：id → 元素名（osd_items_e 枚举名；以 Task 25 实测清单校准）
OSD_IMPLEMENTED: dict[int, str] = {
    0: "RSSI_VALUE",
    1: "MAIN_BATT_VOLTAGE",
    2: "CROSSHAIRS",
    4: "HORIZON_SIDEBARS",
    5: "TIMER_1",
    6: "TIMER_2",
    7: "FLYMODE",
    8: "CRAFT_NAME",
    9: "THROTTLE_POS",
    13: "GPS_SPEED",
    15: "ALTITUDE",
    22: "AVG_CELL_VOLTAGE",
    26: "PITCH_ANGLE",
    27: "ROLL_ANGLE",
    29: "DISARMED",
    33: "NUMERICAL_VARIO",
}

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
    4: 2254,   # HORIZON_SIDEBARS  (14,6)——固件 (midCol-1,midRow-1)；Task 40
               # 起两元素锚点对齐固件/Configurator 默认（原 (14,7) 双双居中
               # 系建表期近似；十字线锚点=左段格后 (13,6) 使中心格落 midCol）
    5: 2082,   # TIMER_1           (2,1)
    6: 2114,   # TIMER_2           (2,2)
    7: 2060,   # FLYMODE           (12,0)
    8: 2538,   # CRAFT_NAME        (10,15)
    9: 2434,   # THROTTLE_POS      (2,12)
    13: 2393,  # GPS_SPEED         (25,10)
    15: 2391,  # ALTITUDE          (23,10)
    22: 2451,  # AVG_CELL_VOLTAGE  (19,12)——Task 40 起图标化 6 格，默认锚点
               # 左移 1 列（原 (20,12) 5 格）避免与 vario 默认 (25,12) 首格重叠
    26: 2479,  # PITCH_ANGLE       (15,13)
    27: 2466,  # ROLL_ANGLE        (2,13)
    29: 2188,  # DISARMED          (12,4)
    33: 2457,  # NUMERICAL_VARIO   (25,12)
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
GLYPH_BATT_FULL = 0x90               # 电池图标族·满格（SYM_BATT_FULL）
GLYPH_BATT_EMPTY = 0x96              # 电池图标族·空（SYM_BATT_EMPTY；段数
                                     # = 0x96-图标，0..6 白段自下而上递增）
GLYPH_MPS = 0x9F                     # m/s 单位字形（SYM_MPS，公制 vario 尾缀）

# 固件侧边栏几何（osd_elements.c AH_SIDEBAR_WIDTH_POS/HEIGHT_POS）：
# 锚点 ±7 列竖轨、锚点行 ±3 共 7 行，水平箭头在 ±(7-1) 列
_AH_SIDEBAR_WIDTH_POS = 7
_AH_SIDEBAR_HEIGHT_POS = 3

# FLYMODE 消费的 box id（与 physics.drone.BOX_ANGLE/BOX_HORIZON 同源；
# 本模块不 import physics，保持依赖方向单向）
_BOX_ANGLE = 1
_BOX_HORIZON = 2

# 字体 ASCII 恒等区（osd_font 注释同源：0x20-0x5A 直接 ord(ch) 寻址）
_ASCII_LO = 0x20
_ASCII_HI = 0x5A
_GLYPH_QUESTION = 0x3F  # 非 ASCII 域归一字符 '?'


def _to_glyph(ch: str) -> int:
    """单字符 → 字形索引（MAX7456 ASCII 域归一）。

    ASCII 恒等区直接给字符码；小写映射为大写同形（本字体无小写区，
    0x60+ 是罗盘/地平线特殊字形——'h'(0x68) 是罗盘北箭头）；其余
    （含中文等非 ASCII）归一 '?'，长度不变由调用方保证。
    """
    code = ord(ch)
    if _ASCII_LO <= code <= _ASCII_HI:
        return code
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


def compose_state(params, flight, preset_id, now_s) -> dict:
    """飞行页与单测共用的 state 组装（Task 28 flight.py 消费）。

    flight 为 flight_snapshot() dict 或 None（未起飞/暂停：遥测字段全零、
    armed False——缺键防御同语义，产出的 state 恒全键）；craft_name 取
    virtual.craft_name，空则回落 preset_id。
    """
    f = flight or {}
    return {
        "layout": params.virtual.osd_layout,
        "enabled": params.virtual.osd_enabled,
        "armed": bool(f.get("armed", False)),
        "boxes": f.get("boxes") or frozenset(),
        "throttle": f.get("throttle", 0.0),
        "voltage_v": params.battery_voltage,
        "cells": params.virtual.cells,
        "speed_mps": f.get("speed_mps", 0.0),
        "alt_m": f.get("alt_m", 0.0),
        "vario_mps": f.get("vario_mps", 0.0),
        "attitude_deg": f.get("attitude_deg") or (0.0, 0.0, 0.0),
        "flight_time_s": f.get("flight_time_s", 0.0),
        "craft_name": params.virtual.craft_name or preset_id,
        "now_s": now_s,
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
    armed = bool(state.get("armed", False))
    throttle = state.get("throttle", 0.0)
    voltage_v = state.get("voltage_v", 0.0)
    cells = state.get("cells", 0)
    speed_mps = state.get("speed_mps", 0.0)
    alt_m = state.get("alt_m", 0.0)
    vario_mps = state.get("vario_mps", 0.0)
    att = state.get("attitude_deg") or (0.0, 0.0, 0.0)
    flight_time_s = max(0.0, state.get("flight_time_s", 0.0))
    craft_name = state.get("craft_name", "")
    now_s = state.get("now_s", 0.0)

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
        elif elem_id == 4:        # HORIZON_SIDEBARS：固件静态背景——±7 列
            # 竖轨（锚点行 ±3）+ ±6 列 AH_LEFT/AH_RIGHT 水平指示箭头；
            # 与姿态无关（roll 偏移属于未实现的 ARTIFICIAL_HORIZON 元素）
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
        elif elem_id == 13:       # GPS_SPEED（小写映射大写字形）
            put_text(f"{speed_mps * 3.6:.0f}Kmh", col, row)
        elif elem_id == 15:       # ALTITUDE
            put_text(f"{alt_m:.0f}M", col, row)
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
        elif elem_id == 33:       # NUMERICAL_VARIO：升/降箭头+|v| 一位小数
            arrow = (GLYPH_ARROW_SMALL_DOWN if vario_mps < 0
                     else GLYPH_ARROW_SMALL_UP)   # 固件 <0 降、≥0 升；方向由
            ip, fp = _osd_float(abs(vario_mps), 1, True)  # 箭头表达，数值无符号
            text = f"{ip}.{fp}"
            put(col, row, arrow)
            put_text(text, col + 1, row)
            put(col + 1 + len(text), row, GLYPH_MPS)
    return frame
