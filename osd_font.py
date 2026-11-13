"""OSD 字符集（MAX7456 .mcm）解析与 GL 图集烘焙

资产来源（GPL-3.0）：
  assets/osd_betaflight.mcm 下载自 betaflight-configurator 仓库
  resources/osd/1/betaflight.mcm
  https://github.com/betaflight/betaflight-configurator
  （jsDelivr 镜像 https://cdn.jsdelivr.net/gh/betaflight/betaflight-configurator@master/resources/osd/1/betaflight.mcm）
  该字体资产随 Configurator 以 GPL-3.0 发布，此处入库仅供本项目 OSD 渲染。

文件格式（以实际 betaflight.mcm + Configurator osdFont.ts 解析器核对）：
  头行 "MAX7456"，随后 16384 行文本，每行 8 个 '0'/'1' 字符表示 1 字节
  （二进制文本，非十六进制）。256 字符 × 64 字节/字符；前 54 字节为像素
  数据（18 行 × 3 字节，每字节 4 像素 2bpp、MSB 在前），尾 10 字节无效。
  像素值语义（实测位图与 Configurator 渲染器一致）：
  0=黑（描边）、1=透明（背景）、2=白、3=闪烁（本资产未出现）。

依赖约束：解析段纯标准库；GL 段仅 OpenGL 底层（不 import imgui/pygame），
bake/free 须在 imgui 帧边界调用（GL 资源创建禁入帧内，
同 app.py _pending_preset_reload / shaderpacks 重载先例，封面纹理同款生命周期）。
"""
import re

from OpenGL.GL import (glActiveTexture, GL_TEXTURE0, glGenTextures,
                       glBindTexture, glTexParameteri, glTexImage2D,
                       glPixelStorei, glDeleteTextures, GL_TEXTURE_2D,
                       GL_TEXTURE_MIN_FILTER, GL_TEXTURE_MAG_FILTER,
                       GL_NEAREST, GL_ALPHA, GL_UNSIGNED_BYTE,
                       GL_UNPACK_ALIGNMENT)

# 格式常量：(魔数头行, 字符数, 字形宽, 字形高, 每字符像素字节数)
MAX7456_FORMAT = ("MAX7456", 256, 12, 18, 54)
# NVM 字段实为 64 字节/字符（尾 10 字节无效，Configurator osdFont.ts 同规）
_CHAR_FIELD_BYTES = 64

# 像素值语义常量
PIXEL_BLACK = 0        # 黑（字形描边/阴影）
PIXEL_TRANSPARENT = 1  # 透明（背景）
PIXEL_WHITE = 2        # 白（字形主体）
PIXEL_BLINK = 3        # 闪烁（静态图集按透明处理，同 Configurator 预览）

# GLYPH_*：BF 特殊字形索引（OSD 渲染消费；索引已对照实际位图逐一核对，
# 命名与 Configurator SYMBOL_CODES 对应。ASCII 恒等区 0x20-0x5A 直接用
# ord(ch) 寻址，不需要常量）
GLYPH_HOME_FLAG = 0x11             # 回家旗标（房子图标）
GLYPH_HEADING_N = 0x18             # 航向带方位字母 N（S/E/W 依次 +1）
GLYPH_HEADING_S = 0x19
GLYPH_HEADING_E = 0x1A
GLYPH_HEADING_W = 0x1B
GLYPH_HEADING_DIVIDED_LINE = 0x1C  # 航向带长刻度（逢十）
GLYPH_HEADING_LINE = 0x1D          # 航向带短刻度
GLYPH_BLANK = 0x20                 # 空格（全透明）
GLYPH_ARROW_SOUTH = 0x60           # 罗盘大箭头（N/E/S/W 十六方位族的首区）
GLYPH_ARROW_EAST = 0x64
GLYPH_ARROW_NORTH = 0x68
GLYPH_ARROW_WEST = 0x6C
GLYPH_AH_CENTER_LINE_LEFT = 0x72   # 人工地平线中线左段
GLYPH_AH_CENTER = 0x73             # 中线中心（十字中心）
GLYPH_AH_CENTER_LINE_RIGHT = 0x74  # 中线右段
GLYPH_ARROW_SMALL_UP = 0x75        # 小箭头（上/右，数值微调用）
GLYPH_ARROW_SMALL_RIGHT = 0x77

# ---- 解析段（纯标准库） ----

_MCM_LINE_RE = re.compile(r"[01]{8}")


def parse_mcm(path: str) -> list:
    """解析 .mcm 字体文件为 256 个 12×18 网格（像素值 0-3）。

    返回 [256][18][12]；头行不符、行数/行格式非法抛 ValueError。
    """
    with open(path, "r", encoding="ascii") as f:
        lines = f.read().splitlines()
    header, count, gw, gh, nbytes = MAX7456_FORMAT
    if not lines or lines[0].strip() != header:
        raise ValueError(f"{path}: 缺少 {header!r} 头行，不是 .mcm 字体文件")
    data = lines[1:]
    expected = count * _CHAR_FIELD_BYTES
    if len(data) != expected or any(not _MCM_LINE_RE.fullmatch(l) for l in data):
        raise ValueError(
            f"{path}: 字符数据行数/格式非法（期望 {expected} 行 8 位二进制文本，"
            f"实际 {len(data)} 行）")
    chars = []
    for ci in range(count):
        block = [int(l, 2) for l in
                 data[ci * _CHAR_FIELD_BYTES:ci * _CHAR_FIELD_BYTES + nbytes]]
        glyph = []
        for r in range(gh):                      # 18 行 × 3 字节
            row = []
            for b in range(gw // 4):             # 每字节 4 像素，MSB 在前
                byte = block[r * (gw // 4) + b]
                row.extend((byte >> s) & 3 for s in (6, 4, 2, 0))
            glyph.append(row)
        chars.append(glyph)
    return chars


# ---- GL 图集段（帧边界调用；封面纹理 cover_cache.py 同款生命周期） ----

# 图集布局：16×16 格平铺（避免 3072 像素宽的超宽纹理）
ATLAS_COLS = 16
ATLAS_W = ATLAS_COLS * MAX7456_FORMAT[2]   # 16×12 = 192
ATLAS_H = ATLAS_COLS * MAX7456_FORMAT[3]   # 16×18 = 288

# 像素值 → 单通道图集亮度/透明度查找表：
# 白(2)→255 不透明、黑(0)→160 半透明阴影（单通道白叠加无法表现纯黑，
# 以半透明近似描边）、透明(1)/闪烁(3)→0
PIXEL_ALPHA_LUT = (160, 0, 255, 0)


def bake_atlas(chars) -> tuple:
    """将 parse_mcm 的 256 字符网格烘焙为单通道（GL_ALPHA）图集纹理。

    16×16 格平铺，字形 i 的格子 UV 原点为 (i%16)*12, (i//16)*18；
    NEAREST 过滤（MAX7456 硬边观感 + 图集相邻格零渗色）。
    返回 (tex_id, ATLAS_W, ATLAS_H)；输入非法或 GL 失败返回 None
    （OSD 为增强层，失败不允许影响主功能）。禁止在 imgui 帧内调用。
    """
    try:
        count = MAX7456_FORMAT[1]
        if not isinstance(chars, (list, tuple)) or len(chars) != count:
            return None
        gw, gh = MAX7456_FORMAT[2], MAX7456_FORMAT[3]
        data = bytearray(ATLAS_W * ATLAS_H)     # 缺省 0 = 透明
        for idx, glyph in enumerate(chars):
            col, row = idx % ATLAS_COLS, idx // ATLAS_COLS
            for gy in range(gh):
                base = (row * gh + gy) * ATLAS_W + col * gw
                src = glyph[gy]
                for gx in range(gw):
                    data[base + gx] = PIXEL_ALPHA_LUT[src[gx]]
        glActiveTexture(GL_TEXTURE0)   # 归位活动单元（f075efc：绑定落错单元坑）
        tex = int(glGenTextures(1))    # PyOpenGL 返回 np.uint32，需原生 int
        glBindTexture(GL_TEXTURE_2D, tex)
        glPixelStorei(GL_UNPACK_ALIGNMENT, 1)
        glTexImage2D(GL_TEXTURE_2D, 0, GL_ALPHA, ATLAS_W, ATLAS_H, 0,
                     GL_ALPHA, GL_UNSIGNED_BYTE, bytes(data))
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST)
        return tex, ATLAS_W, ATLAS_H
    except Exception:
        return None


def free_atlas(tex_id) -> None:
    """删除图集纹理；非法句柄/重复释放由 GL 忽略，不抛异常（UI 层免簿记）"""
    try:
        glDeleteTextures([int(tex_id)])
    except Exception:
        pass
