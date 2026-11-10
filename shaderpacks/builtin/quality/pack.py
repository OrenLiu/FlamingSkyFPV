"""内置「画面优先」预设：目标形态为 PBR（Cook-Torrance GGX）+ 太阳方向光阴影贴图
（3×3 PCF）+ SSR 屏幕空间反射 + HDR（rgba16f）+ ACES 色调映射（spec 5.6 参考实现）。

本文件当前仅含表面类（暂与默认管线一致）；完整帧渲染路径由本文件的后续提交引入。
"""
import soup3D

# 基线阶段表面类与性能优先一致；PBRSurface/DepthSurface/QualityFrameRenderer 随
# quality 包实现逐步落入本文件
surface_class = soup3D.shader.AutoSP
