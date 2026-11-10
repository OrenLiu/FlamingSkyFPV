"""内置「画面优先」预设（spec 5.6 参考实现）。

当前版本：太阳方向光阴影贴图通道 + HDR（rgba16f）主通道 + present 直通呈现
（spec 5.6 前半管线；PBR 表面与 SSR/ACES 合成通道由后续提交引入本文件）。
"""
import weakref

import soup3D
from OpenGL.GL import (GL_COLOR_BUFFER_BIT, GL_COLOR_CLEAR_VALUE, GL_DEPTH_BUFFER_BIT,
                       GL_DEPTH_WRITEMASK, GL_FALSE, GL_TRUE, glClear, glClearColor,
                       glDepthMask, glGetBooleanv, glGetFloatv)
from pyglm import glm
from soup3D.target import RenderTarget

# ---- 包常量（spec 7 性能预算的调优入口：场景更大/性能不达标时改这里）----
SKY_COLOR = (0.55, 0.75, 0.95)   # 主通道清屏色（与 soup3D.init 一致）
SHADOW_SIZE = 2048               # 阴影贴图边长（px）
SHADOW_CENTER = (0.0, 0.0, 0.0)  # 阴影正交相机注视的场景中心（世界系，大地图改这里）
SHADOW_HALF_WIDTH = 80.0         # 阴影正交半宽（米，需覆盖整张地图）
SHADOW_NEAR = 0.1                # 阴影相机近平面
SHADOW_FAR = 300.0               # 阴影相机远平面
SHADOW_EYE_DIST = 120.0          # 太阳相机沿指向光源方向的后撤距离（米）
SHADOW_TEX_UNIT = 2              # shadowMap 纹理单元（空闲单元，避开 0/1/3 引擎约定）

# ---- 阴影状态广播（spec 5.6 下发机制）----

class _ShadowRegistry:
    """阴影通道状态：QualityFrameRenderer 每帧写入（有变化才递增 version），
    DepthSurface/PBRSurface.update 比对 version 决定是否补传阴影 uniform。"""

    def __init__(self):
        self.light_view_proj = None   # glm.mat4（太阳正交矩阵 proj*view）或 None
        self.shadow_tex = None        # RenderTargetTexture（阴影 RT 深度附着）或 None
        self.enabled = False          # 场景中是否存在 on 状态的 Direct 太阳
        self.version = 0              # 变更计数（表面侧缓存比对）

    def update(self, light_view_proj, shadow_tex, enabled):
        """写入状态；有变化才递增 version（无变化帧零开销）"""
        changed = (self.enabled != enabled
                   or self.shadow_tex is not shadow_tex
                   or (self.light_view_proj is None) != (light_view_proj is None))
        if not changed and light_view_proj is not None:
            changed = any(a != b for a, b in zip(tuple(self.light_view_proj),
                                                 tuple(light_view_proj)))
        if changed:
            self.light_view_proj = light_view_proj
            self.shadow_tex = shadow_tex
            self.enabled = enabled
            self.version += 1


_shadow_registry = _ShadowRegistry()


class _ShadowSurfaceBase(soup3D.shader.AutoSP):
    """DepthSurface/PBRSurface 公共基类：阴影 uniform 按 version 缓存补传。

    update() 先补传阴影 uniform（version 变了才做），再 super().update() 让
    AutoSP 末尾的 shader_program.update() 一并 flush。
    """

    def __init__(self, *args, **kwargs):
        self._shadow_version = -1
        super().__init__(*args, **kwargs)

    def _upload_shadow_uniforms(self):
        """补传阴影 uniform：shadowMap 单元绑定 + 开关 + 太阳矩阵"""
        sp = self.shader_program
        reg = _shadow_registry
        if reg.shadow_tex is not None:
            sp.uniform_tex("shadowMap", reg.shadow_tex, SHADOW_TEX_UNIT)
        sp.uniform("shadowEnabled", soup3D.INT_VEC1, 1 if reg.enabled else 0)
        sp.uniform("lightSpaceMat", soup3D.ARRAY_MATRIX_VEC4, 1, GL_FALSE,
                   glm.value_ptr(reg.light_view_proj
                                 if reg.light_view_proj is not None
                                 else glm.mat4(1.0)))

    def update(self):
        if self._shadow_version != _shadow_registry.version:
            self._shadow_version = _shadow_registry.version
            self._upload_shadow_uniforms()
        super().update()


class DepthSurface(_ShadowSurfaceBase):
    """阴影通道深度表面：只写深度，alpha<0.5 discard（透明材质不投影）。

    继承 AutoSP 获得 rend_cached 拆分/VAO 缓存；重写 create_shader_program()
    换太阳相机 GLSL（引擎 BoneBinderSP 同模式）。VS 无 view/projection/light
    uniforms——AutoSP.update 对 location=-1 的 uniform 静默忽略，无副作用。
    """

    def __init__(self, base_color):
        super().__init__(base_color, double_side=True, max_light_count=1)

    def create_shader_program(self):
        vertex_shader = """
        #version 330 core
        layout(location = 0) in vec3 VertPos;
        layout(location = 1) in vec2 VertUV;

        out vec2 TexCoord;
        uniform mat4 model;
        uniform mat4 lightSpaceMat;   // 太阳正交矩阵（proj*view）

        void main()
        {
            gl_Position = lightSpaceMat * model * vec4(VertPos, 1.0);
            TexCoord = vec2(VertUV.x, 1-VertUV.y);
        }
        """
        fragment_shader = """
        #version 330 core
        in vec2 TexCoord;
        uniform sampler2D baseColor;
        void main()
        {
            // 透明片元不投影（alpha<0.5 视为镂空，避免透明材质整面遮挡）
            if (texture(baseColor, TexCoord).a < 0.5) {
                discard;
            }
        }
        """
        shader_program = soup3D.shader.ShaderProgram(
            vertex_shader, fragment_shader,
            vbo_type=[soup3D.FLOAT, soup3D.FLOAT, soup3D.FLOAT])
        shader_program.uniform_tex("baseColor", self.base_color, 0)
        return shader_program


class QualityFrameRenderer:
    """画面优先帧渲染器：阴影通道 → HDR 主通道 → present 直通呈现。

    资源（构造时创建，要求 GL 上下文就绪）：阴影 RT（固定 2048²）+ HDR RT
    （窗口尺寸 rgba16f + 深度纹理）。dispose() 供 GL 上下文销毁前释放。
    """

    def __init__(self, width, height):
        self._shadow_rt = RenderTarget(SHADOW_SIZE, SHADOW_SIZE, "rgba8", "texture")
        self._hdr_rt = RenderTarget(width, height, "rgba16f", "texture")
        # DepthSurface 按 Face 对象弱引用缓存（face 释放自动清理，规避 id 复用风险）
        self._depth_surfaces = weakref.WeakKeyDictionary()

    def render_frame(self, draw_scene):
        has_sun = self._update_shadow_registry(draw_scene.faces)
        if has_sun:
            self._render_shadow_pass(draw_scene.faces)
        self._render_main_pass(draw_scene)
        self._hdr_rt.present()

    def dispose(self):
        """GL 上下文销毁前释放：清空引用（RT/GL 对象随 GC __del__ 删除，引擎同模式）"""
        self._depth_surfaces.clear()
        self._shadow_rt = None
        self._hdr_rt = None

    # ---- 阴影状态与通道 ----
    def _update_shadow_registry(self, faces):
        """收集太阳（首个 on 状态 Direct）写入 _shadow_registry；返回是否有太阳。

        无太阳 → 不跑阴影通道，shader 走无阴影分支（shadowEnabled=0；
        shadowMap 深度清除值 1.0 天然无遮挡）。
        """
        sun = next((l for l in soup3D.light.light_queue.values()
                    if l.on and isinstance(l, soup3D.light.Direct)), None)
        if sun is None:
            _shadow_registry.update(None, self._shadow_rt.depth, False)
            return False
        d = glm.normalize(glm.vec3(*sun._calc_direction()))  # 指向光源的方向
        center = glm.vec3(*SHADOW_CENTER)
        eye = center + d * SHADOW_EYE_DIST
        up = glm.vec3(0, 0, 1) if abs(d.z) > 0.99 else glm.vec3(0, 1, 0)
        view = glm.lookAt(eye, center, up)
        proj = glm.ortho(-SHADOW_HALF_WIDTH, SHADOW_HALF_WIDTH,
                         -SHADOW_HALF_WIDTH, SHADOW_HALF_WIDTH,
                         SHADOW_NEAR, SHADOW_FAR)
        _shadow_registry.update(proj * view, self._shadow_rt.depth, True)
        return True

    def _render_shadow_pass(self, faces):
        """阴影通道：太阳正交矩阵 → 深度 RT；同模型 faces 连续处理（spec 4.3）。"""
        rt = self._shadow_rt
        rt.bind()
        try:
            # 保存/恢复清屏色与深度写掩码（不污染引擎全局状态）
            prev_clear = glGetFloatv(GL_COLOR_CLEAR_VALUE)
            prev_mask = glGetBooleanv(GL_DEPTH_WRITEMASK)
            glClearColor(1.0, 1.0, 1.0, 1.0)
            glDepthMask(GL_TRUE)
            glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
            glDepthMask(prev_mask)
            glClearColor(*prev_clear)
            last_model = None
            model_mat = None
            for model, face in faces:
                if model is not last_model:
                    # 同模型 faces 连续排列（spec 4.3）：组首计算模型矩阵一次，
                    # 组内每个 face 的 DepthSurface 实例都要 set（各自持有矩阵）
                    last_model = model
                    model_mat = model.get_model_mat()
                ds = self._depth_surface_for(face)
                ds.set_model_mat(model_mat)
                if ds.is_dirty():
                    ds.update()
                ds.use()
                ds.rend_cached(face.mode, face.vertex)
                ds.unuse()
        finally:
            rt.unbind()

    def _depth_surface_for(self, face):
        """按 Face 对象弱引用缓存 DepthSurface（face 释放自动清理，规避 id 复用）"""
        ds = self._depth_surfaces.get(face)
        if ds is None:
            base = getattr(face.surface, "base_color", None)
            if base is None:
                base = soup3D.shader.MixChannel((1, 1), 1.0, 1.0, 1.0, 1.0)
            ds = DepthSurface(base)
            self._depth_surfaces[face] = ds
        return ds

    def _render_main_pass(self, draw_scene):
        """主通道：HDR RT 清屏（天空色）→ draw_scene()（表面 rend_cached 进 RT）。"""
        rt = self._hdr_rt
        rt.bind()
        try:
            prev_clear = glGetFloatv(GL_COLOR_CLEAR_VALUE)
            prev_mask = glGetBooleanv(GL_DEPTH_WRITEMASK)
            glClearColor(*SKY_COLOR, 1.0)
            glDepthMask(GL_TRUE)
            glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
            glDepthMask(prev_mask)
            glClearColor(*prev_clear)
            draw_scene()
        finally:
            rt.unbind()


# 基线阶段表面类与性能优先一致；PBRSurface 落地后切换（create_frame_renderer
# 契约导出留待 Task 12——此前 quality 预设走默认管线分支，行为与性能优先一致）
surface_class = soup3D.shader.AutoSP
