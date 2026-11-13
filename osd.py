# osd.py
"""OSD 元素数据表、位置编解码与内容生成（MSP Task 23 建表；Task 27 内容
生成；Task 28 渲染消费）。

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
    2: 2286,   # CROSSHAIRS        (14,7)
    4: 2286,   # HORIZON_SIDEBARS  (14,7)
    5: 2082,   # TIMER_1           (2,1)
    6: 2114,   # TIMER_2           (2,2)
    7: 2060,   # FLYMODE           (12,0)
    8: 2538,   # CRAFT_NAME        (10,15)
    9: 2434,   # THROTTLE_POS      (2,12)
    13: 2393,  # GPS_SPEED         (25,10)
    15: 2391,  # ALTITUDE          (23,10)
    22: 2452,  # AVG_CELL_VOLTAGE  (20,12)
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
# build_frame 输出的索引进图集）。0x78 为 0x77 的水平镜像小左箭头，
# osd_font 未具名（Task 26 范围），此处补位。
GLYPH_AH_CENTER_LINE_LEFT = 0x72    # 人工地平线中线左段
GLYPH_AH_CENTER = 0x73              # 中线中心（十字中心）
GLYPH_AH_CENTER_LINE_RIGHT = 0x74   # 中线右段
GLYPH_ARROW_SMALL_RIGHT = 0x77      # 小箭头（右）
GLYPH_ARROW_SMALL_LEFT = 0x78       # 小箭头（左）

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

        if elem_id == 0:          # RSSI_VALUE（模拟恒 99）
            put_text("R 99", col, row)
        elif elem_id == 1:        # MAIN_BATT_VOLTAGE
            put_text(f"{voltage_v:.1f}V", col, row)
        elif elem_id == 2:        # CROSSHAIRS：锚点=中心字形，左右段 ±1
            put(col - 1, row, GLYPH_AH_CENTER_LINE_LEFT)
            put(col, row, GLYPH_AH_CENTER)
            put(col + 1, row, GLYPH_AH_CENTER_LINE_RIGHT)
        elif elem_id == 4:        # HORIZON_SIDEBARS：左右箭头对（锚点 ±2 列）
            offset = max(-5, min(5, round(att[0] * 0.2)))
            put(col - 2 + offset, row, GLYPH_ARROW_SMALL_LEFT)
            put(col + 2 + offset, row, GLYPH_ARROW_SMALL_RIGHT)
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
        elif elem_id == 22:       # AVG_CELL_VOLTAGE（cells 非法则不画）
            if cells > 0:
                put_text(f"{voltage_v / cells:.2f}V", col, row)
        elif elem_id == 26:       # PITCH_ANGLE（att[1]）
            put_text(f"P{att[1]:+.0f}", col, row)
        elif elem_id == 27:       # ROLL_ANGLE（att[0]）
            put_text(f"R{att[0]:+.0f}", col, row)
        elif elem_id == 29:       # DISARMED（armed 或相位关 → 整元素不画）
            if not armed and int(now_s * 2) % 2 == 0:
                put_text("DISARMED", col, row, blink=True)
        elif elem_id == 33:       # NUMERICAL_VARIO
            put_text(f"{vario_mps:+.1f}", col, row)
    return frame
