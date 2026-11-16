"""UI 等比缩放（分辨率自适应）：以 720p 为设计基准，尺寸与字号随窗口高度线性缩放。

三个消费入口：
- scale(h)：纯函数比例因子（可单测）；
- u(x)：屏幕布局用当前缩放换算像素（各界面固定像素尺寸全部经此换算）；
- font_px(h)：字号档位（4px 步长量化——全 CJK 字体图集烘焙一次 ~0.5s，窗口
  resize 时按档量化 + 去抖 + 帧边界重建，见 app.App._apply_pending_resized_font）。

基准 720p 下一切与历史像素一致（scale=1.0、字号 24px）。
"""
import imgui

REF_H = 720.0          # 设计基准高度（1280×720）
SCALE_MIN = 0.5        # 极小窗口 UI 下限（可读性）
SCALE_MAX = 3.0        # 大屏上限（1440p=2.0、4K 封顶 3.0）
FONT_BASE_PX = 24      # 基准字号（历史值）
FONT_STEP_PX = 4       # 字号档位步长：粗档减少 CJK 图集重建次数
FONT_PX_MIN = 12
# 上限 48：全 CJK 全量字形图集 48px 档已达 16384 宽（256MB 纹理，桌面 GPU
# 上限普遍 16384）；更高档（4K→72px）宽 32768 必超 GPU 上限 → 上传失败字符
# 消失。4K 屏字号按 48 渲（略小于严格等比）
FONT_PX_MAX = 48


def scale(display_h: float) -> float:
    """纯函数：显示高度 → UI 缩放因子，钳位 [0.5, 3.0]；高度非法回落 1.0"""
    if not display_h or display_h <= 0:
        return 1.0
    return min(SCALE_MAX, max(SCALE_MIN, display_h / REF_H))


def current_scale() -> float:
    """当前帧缩放因子（imgui io.display_size 实时值；无 imgui 上下文回落 1.0，
    逻辑单测无显示环境也可调用 u()）"""
    try:
        return scale(imgui.get_io().display_size[1])
    except Exception:
        return 1.0


def u(x: float) -> int:
    """设计像素 x 按当前缩放取整换算（imgui 像素尺寸统一入口）"""
    return max(1, round(x * current_scale()))


def font_px(display_h: float) -> int:
    """纯函数：显示高度 → 字号档位（4px 步长量化，钳 [12, 96]）"""
    raw = FONT_BASE_PX * scale(display_h)
    q = max(FONT_PX_MIN, min(FONT_PX_MAX, round(raw / FONT_STEP_PX) * FONT_STEP_PX))
    return int(q)
