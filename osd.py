# osd.py
"""OSD 元素数据表与位置编解码（MSP Task 23 建；Task 27/28 内容渲染共用）。

仅纯数据表与无依赖合成函数，不 import 项目模块（依赖方向单向：
msp/handlers 与 screens 可 import 本表；改动需保持无 GL/pygame/imgui）。

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
