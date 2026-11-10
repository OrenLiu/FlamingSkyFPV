"""内置「性能优先」预设：默认管线 + AutoSP（spec 5.5），与当前画面逐像素一致。

不提供 create_frame_renderer：主循环走默认管线分支，零行为变化。
"""
import soup3D

# 表面着色器类：open_obj 的 surface 参数默认值本就是 AutoSP，注入等价于现状
surface_class = soup3D.shader.AutoSP
