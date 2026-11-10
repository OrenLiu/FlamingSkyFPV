"""包作者便利 API（spec 5.4）：frame_preprocess 再导出、SurfaceShaderBase、FullScreenPass。

便利层，不设权限边界：包代码自由 import soup3D 使用全部引擎公开 API。纹理单元
约定（包作者契约）：baseColor=0、normal=1、shadowMap=2（阴影预设用）、emission=3；
rend_cached 的 texture_val 循环逐帧自动重绑全部已注册单元。
"""
import soup3D

from OpenGL.GL import (GL_TRIANGLES, glBindVertexArray, glDeleteVertexArrays,
                       glDrawArrays, glGenVertexArrays, glUseProgram)

from .runtime import frame_preprocess

# 包作者继承的表面着色器基类（AutoSP 别名）：继承后重写 create_shader_program()
# 返回自定义 GLSL 的 ShaderProgram（引擎 BoneBinderSP 即此模式先例）；需补充
# uniform 时重写 update()（先补传自己的，再 super().update() 让基类末尾的
# shader_program.update() 一并 flush）
SurfaceShaderBase = soup3D.shader.AutoSP

# 全屏三角形顶点着色器（与 soup3D/target.py _ensure_present_program 同模式：
# 顶点由 gl_VertexID 生成，无需顶点缓冲；UV 覆盖 0..1）
FULLSCREEN_VS = """
#version 330 core
out vec2 UV;
void main() {
    vec2 pos = vec2(float((gl_VertexID << 1) & 2), float(gl_VertexID & 2));
    UV = pos;
    gl_Position = vec4(pos * 2.0 - 1.0, 0.0, 1.0);
}
"""

# 蒙皮类约定说明（一期无蒙皮场景，接口预留，spec 5.4）：GLTF 蒙皮表面类继承
# soup3D.shader.BoneBinderSP 并按需重写 create_shader_program/update
SKIN_SURFACE_HINT = ("GLTF 蒙皮表面类请继承 soup3D.shader.BoneBinderSP "
                     "（一期接口预留，暂无蒙皮场景示例）")


class FullScreenPass:
    """全屏三角形通道辅助：惰性创建空 VAO，draw(program) 关键状态由调用方管理。

    包作者写 SSR/合成通道直接用，不必自管顶点缓冲。构造不触 GL（可在无上下文期
    创建实例）；首次 draw 时才创建 VAO。
    """

    def __init__(self):
        self._vao = None

    def draw(self, program: int) -> None:
        """用给定程序绘制一次全屏三角（glUseProgram + 绑定 VAO + draw）"""
        if self._vao is None:
            self._vao = int(glGenVertexArrays(1))
        glUseProgram(program)
        glBindVertexArray(self._vao)
        glDrawArrays(GL_TRIANGLES, 0, 3)
        glBindVertexArray(0)

    def dispose(self):
        """释放 VAO（GL 上下文销毁前调用；失败静默，同引擎 __del__ 模式）"""
        if self._vao is not None:
            try:
                glDeleteVertexArrays(1, [self._vao])
            except Exception:
                pass
            self._vao = None
