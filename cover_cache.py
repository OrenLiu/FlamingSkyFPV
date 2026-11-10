"""场景封面纹理缓存：图片文件 → OpenGL 纹理（句柄供 imgui.image_button 显示）"""
import pygame
from OpenGL.GL import (glActiveTexture, GL_TEXTURE0, glGenTextures, glBindTexture,
                       glTexParameteri, GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER,
                       GL_TEXTURE_MAG_FILTER, GL_LINEAR, glTexImage2D, GL_RGBA,
                       GL_UNSIGNED_BYTE, glDeleteTextures)

# 选择页卡片封面区尺寸（flight.py 网格卡片用；放这里供纯函数与 UI 共用）
COVER_BOX_W = 220
COVER_BOX_H = 124


def cover_uv_crop(img_w: float, img_h: float, box_w: float, box_h: float) -> tuple:
    """居中裁剪 UV：图片按纵横比裁剪填满盒子（cover-crop，不变形）。
    返回 (u0, v0, u1, v1)；宽高非正数回退全图 UV"""
    if img_w <= 0 or img_h <= 0 or box_w <= 0 or box_h <= 0:
        return (0.0, 0.0, 1.0, 1.0)
    img_aspect = img_w / img_h
    box_aspect = box_w / box_h
    if img_aspect > box_aspect:            # 图更宽 → 左右居中裁
        frac = box_aspect / img_aspect
        u0 = (1.0 - frac) / 2.0
        return (u0, 0.0, 1.0 - u0, 1.0)
    if img_aspect < box_aspect:            # 图更高 → 上下居中裁
        frac = img_aspect / box_aspect
        v0 = (1.0 - frac) / 2.0
        return (0.0, v0, 1.0, 1.0 - v0)
    return (0.0, 0.0, 1.0, 1.0)            # 纵横比一致 → 全图


def load_cover_texture(path) -> "tuple | None":
    """加载图片文件为 GL 纹理，返回 (tex_id, 宽, 高)；失败返回 None。
    封面是装饰字段，任何加载失败都不允许影响场景选择功能。
    上传前归位活动纹理单元 GL_TEXTURE0：引擎/imgui 混用 GL 的历史坑
    （f075efc：活动单元非 0 时绑定落错单元 → 后续 UI 显示异常）"""
    try:
        surf = pygame.image.load(str(path))
        data = pygame.image.tostring(surf, "RGBA")
        w, h = surf.get_size()
        glActiveTexture(GL_TEXTURE0)
        tex = int(glGenTextures(1))   # PyOpenGL 返回 np.uint32，imgui 需要原生 int 句柄
        glBindTexture(GL_TEXTURE_2D, tex)
        glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, w, h, 0, GL_RGBA,
                     GL_UNSIGNED_BYTE, data)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
        return tex, w, h
    except Exception:
        return None


def free_cover_texture(tex_id) -> None:
    """删除 GL 纹理；非法句柄/重复释放由 GL 忽略，不抛异常（UI 层免簿记）"""
    try:
        glDeleteTextures([int(tex_id)])
    except Exception:
        pass
