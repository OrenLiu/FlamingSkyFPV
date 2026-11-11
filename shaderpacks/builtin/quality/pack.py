"""内置「画面优先」预设（spec 5.6 参考实现）。

当前管线：太阳方向光阴影贴图通道 → HDR（rgba16f）主通道 → SSR+ACES
合并合成通道（全屏三角直绘默认帧缓冲上屏）。
"""
import weakref

import soup3D
from OpenGL.GL import (GL_ACTIVE_TEXTURE, GL_BLEND, GL_COLOR_BUFFER_BIT,
                       GL_COLOR_CLEAR_VALUE, GL_COMPILE_STATUS, GL_CURRENT_PROGRAM,
                       GL_DEPTH_BUFFER_BIT, GL_DEPTH_TEST, GL_DEPTH_WRITEMASK,
                       GL_DRAW_FRAMEBUFFER, GL_DRAW_FRAMEBUFFER_BINDING, GL_FALSE,
                       GL_FRAGMENT_SHADER, GL_FRAMEBUFFER, GL_LINK_STATUS,
                       GL_READ_FRAMEBUFFER, GL_READ_FRAMEBUFFER_BINDING,
                       GL_TEXTURE0, GL_TEXTURE1, GL_TEXTURE2, GL_TEXTURE_2D,
                       GL_TEXTURE_BINDING_2D, GL_TRIANGLES, GL_TRUE,
                       GL_VERTEX_SHADER, GL_VIEWPORT, glActiveTexture,
                       glAttachShader, glBindFramebuffer, glBindTexture,
                       glClear, glClearColor, glCompileShader,
                       glCreateProgram, glCreateShader, glDeleteProgram,
                       glDeleteShader, glDepthMask, glDisable, glDrawArrays,
                       glEnable, glGetBooleanv, glGetFloatv, glGetIntegerv,
                       glGetProgramInfoLog, glGetProgramiv, glGetShaderInfoLog,
                       glGetShaderiv, glGetUniformLocation, glIsEnabled,
                       glLinkProgram, glShaderSource, glUniform1f, glUniform1i,
                       glUniformMatrix4fv, glUseProgram, glViewport)
from pyglm import glm
from soup3D.target import RenderTarget

import shaderpacks.api as api

# ---- 包常量（spec 7 性能预算的调优入口：场景更大/性能不达标时改这里）----
SKY_COLOR = (0.55, 0.75, 0.95)   # 主通道清屏色（与 soup3D.init 一致）
SHADOW_SIZE = 2048               # 阴影贴图边长（px）
SHADOW_CENTER = (0.0, 0.0, 0.0)  # 阴影正交相机注视的场景中心（世界系，大地图改这里）
SHADOW_HALF_WIDTH = 80.0         # 阴影正交半宽（米，需覆盖整张地图）
SHADOW_NEAR = 0.1                # 阴影相机近平面
SHADOW_FAR = 300.0               # 阴影相机远平面
SHADOW_EYE_DIST = 120.0          # 太阳相机沿指向光源方向的后撤距离（米）
SHADOW_TEX_UNIT = 2              # shadowMap 纹理单元（空闲单元，避开 0/1/3 引擎约定）
DEFAULT_ROUGHNESS = 0.8         # MTL 的 Pr/Ns 均未提供时的默认粗糙度（spec 5.6）
DEFAULT_METALLIC = 0.0          # MTL 的 Pm 未提供时的默认金属度（spec 5.6）


def roughness_from_shininess(ns):
    """MTL 传统 Ns(shininess) → GGX roughness 经典换算：r = sqrt(2/(Ns+2))。

    多数建模工具的 OBJ/MTL 导出不带 PBR 扩展关键字（Pr/Pm），只带传统
    Ns——本换算让既有模型的 Ns 直接驱动 PBR 粗糙度与 SSR 反射差异化。
    :param ns: MTL 的 Ns 值（如水面 1000≈镜面 0.045、碎石 2.5≈漫反射 0.667）；
               None（MTL 无 Ns）回落 DEFAULT_ROUGHNESS
    :return:   GGX 粗糙度 [0, 1]
    """
    if ns is None:
        return DEFAULT_ROUGHNESS
    return (2.0 / (float(ns) + 2.0)) ** 0.5
EXPOSURE = 0.65                 # 合成曝光（合成无 gamma：材质/灯光为显示参考值，
                                # 0.65 使中间调与性能优先对齐，ACES 仅做高光滚降）
SSR_STEPS = 24                 # SSR 光线步进次数（性能预算超限时减小/置 0 关闭反射）
SSR_REFINE = 4                 # 命中后二分细化次数
SSR_THICKNESS = 0.5            # 命中判定厚度（米，视图空间）
SSR_MAX_DIST = 120.0           # 最大步进距离（米）
SSR_STRENGTH = 0.35            # SSR 掠射反射强度系数（菲涅尔项的闸，目检可调）
SSR_BASE = 0.25                # SSR 正对角反射底座（视觉驱动，非物理：菲涅尔在正对
                               # 角仅 ~4%，会把一切非掠射反射压到不可见；积水/光滑面的
                               # 「处处可辨镜面感」需要底座。粗糙度衰减×模糊仍按物理）
SSR_BLUR_RADIUS = 0.02         # SSR 反射模糊最大半径（UV，随反射点粗糙度线性缩放）

# 合成通道片段着色器（SSR + 背景直通 + 曝光 → ACES，一个全屏通道直绘默认帧缓冲）。
# 背景（深度=1）直通固定色；场景像素曝光 → ACES（无 gamma：材质/灯光为显示
# 参考值，gamma 直出会整体提亮，与性能优先观感脱节）。
# Python % 格式化：六个常量注入 #define（步数/细化为 int，强度/距离/厚度/模糊
# 半径为 float），GLSL 正文不含其他 % 字符
_COMPOSITE_FS = """
#version 330 core
#define SSR_STEPS %d
#define SSR_REFINE %d
#define SSR_STRENGTH %.2f
#define SSR_BASE %.2f
#define SSR_THICKNESS %.1f
#define SSR_MAX_DIST %.1f
#define SSR_BLUR_RADIUS %.3f

in vec2 UV;
out vec4 FragColor;

uniform sampler2D hdrColor;   // 单元0：HDR 颜色
uniform sampler2D hdrDepth;   // 单元1：HDR 深度（0..1）
uniform sampler2D roughTex;   // 单元2：粗糙度图（粗糙度通道写入）
uniform mat4 invProj;         // 透视矩阵逆（深度 → 视图空间重建）
uniform mat4 proj;            // 透视矩阵（视图空间 → 屏幕投影）
uniform float exposure;

vec3 aces(vec3 x)
{
    // ACES 近似（Narkowicz 拟合）
    return clamp((x * (2.51 * x + 0.03)) / (x * (2.43 * x + 0.59) + 0.14),
                 0.0, 1.0);
}

vec3 view_pos_at(vec2 uv, float depth)
{
    // 深度 → 视图空间位置（视图 z 为负，越远越小）
    vec4 clip = vec4(uv * 2.0 - 1.0, depth * 2.0 - 1.0, 1.0);
    vec4 view = invProj * clip;
    return view.xyz / view.w;
}

void main()
{
    float depth = texture(hdrDepth, UV).r;
    vec3 color = texture(hdrColor, UV).rgb;

    // 背景（深度=1）直通：显示就绪的清屏色，不参与曝光/ACES/gamma。
    // 空场景帧（主菜单/设置页）整屏走此分支 → 固定背景色，与性能优先观感一致
    if (depth >= 1.0) {
        FragColor = vec4(color, 1.0);
        return;
    }

    // SSR（仅场景像素）：反射方向朝屏幕外（R.z>=0）跳过
    if (depth < 1.0) {
        vec3 viewPos = view_pos_at(UV, depth);
        vec3 N = normalize(cross(dFdx(viewPos), dFdy(viewPos)));
        vec3 V = normalize(-viewPos);
        vec3 R = reflect(-V, N);
        if (R.z < 0.0) {
            float fresnel = 0.04 + 0.96 * pow(1.0 - max(dot(N, V), 0.0), 5.0);
            float stepLen = SSR_MAX_DIST / float(SSR_STEPS);
            vec2 hitUV = vec2(0.0);
            float found = 0.0;
            vec3 p = viewPos;
            for (int i = 0; i < SSR_STEPS; i++) {
                p += R * stepLen;
                vec4 clip = proj * vec4(p, 1.0);
                vec3 ndc = clip.xyz / clip.w;
                if (abs(ndc.x) > 1.0 || abs(ndc.y) > 1.0) {
                    break;   // 步出屏幕：视为不命中
                }
                vec2 suv = ndc.xy * 0.5 + 0.5;
                float sceneZ = view_pos_at(suv, texture(hdrDepth, suv).r).z;
                // 命中判定（视图 z 均为负，越远越小）：步进点落到场景表面
                // z + 厚度之后（≤）即命中
                if (p.z <= sceneZ + SSR_THICKNESS) {
                    hitUV = suv;
                    found = 1.0;
                    // 二分细化（SSR_REFINE 次）
                    float lo = stepLen * float(i);
                    float hi = stepLen * float(i + 1);
                    for (int r = 0; r < SSR_REFINE; r++) {
                        float mid = (lo + hi) * 0.5;
                        vec3 q = viewPos + R * mid;
                        vec4 c2 = proj * vec4(q, 1.0);
                        vec2 uv2 = (c2.xyz / c2.w).xy * 0.5 + 0.5;
                        float s2z = view_pos_at(uv2, texture(hdrDepth, uv2).r).z;
                        if (q.z <= s2z + SSR_THICKNESS) {
                            hi = mid;
                            hitUV = uv2;
                        } else {
                            lo = mid;
                        }
                    }
                    break;
                }
            }
            if (found > 0.5) {
                // 屏幕边缘淡出 + 反射加权混合：混合系数 = 光滑度 ×
                // (SSR_BASE 正对底座 + 菲涅尔 × SSR_STRENGTH)——纯菲涅尔在正对
                // 角仅约百分之四，会把差异化粗糙度全部压到不可见；反射色按粗糙度
                // 多点采样模糊（物理驱动 = 反射点自身粗糙度：越粗糙越弱越散）
                vec2 border = min(hitUV, 1.0 - hitUV);
                float edge = clamp(min(border.x, border.y) * 8.0, 0.0, 1.0);
                float smoothness = 1.0 - texture(roughTex, UV).r;
                vec3 refl = texture(hdrColor, hitUV).rgb;
                float r = (1.0 - smoothness) * SSR_BLUR_RADIUS;
                refl += texture(hdrColor, hitUV + vec2( r,  r)).rgb;
                refl += texture(hdrColor, hitUV + vec2( r, -r)).rgb;
                refl += texture(hdrColor, hitUV + vec2(-r,  r)).rgb;
                refl += texture(hdrColor, hitUV + vec2(-r, -r)).rgb;
                color = mix(color, refl * 0.2,
                            smoothness * smoothness
                            * (SSR_BASE + fresnel * SSR_STRENGTH) * edge);
            }
        }
    }

    // 曝光 → ACES（无 gamma：材质/灯光为显示参考值，gamma 直出会整体提亮）
    FragColor = vec4(aces(color * exposure), 1.0);
}
"""


def _gl_scalar(value) -> int:
    """把 PyOpenGL 标量查询结果归一化为 int（同 soup3D/target.py 的同款辅助）"""
    try:
        return int(value[0])
    except (TypeError, IndexError):
        return int(value)


def _compile_program(vs_src, fs_src):
    """裸 GL 程序编译（照 soup3D/target.py _ensure_present_program 的模式）"""
    vs = glCreateShader(GL_VERTEX_SHADER)
    glShaderSource(vs, vs_src)
    glCompileShader(vs)
    if glGetShaderiv(vs, GL_COMPILE_STATUS) != GL_TRUE:
        raise RuntimeError("vertex shader compile failed: " + str(glGetShaderInfoLog(vs)))
    fs = glCreateShader(GL_FRAGMENT_SHADER)
    glShaderSource(fs, fs_src)
    glCompileShader(fs)
    if glGetShaderiv(fs, GL_COMPILE_STATUS) != GL_TRUE:
        raise RuntimeError("fragment shader compile failed: " + str(glGetShaderInfoLog(fs)))
    program = int(glCreateProgram())
    glAttachShader(program, vs)
    glAttachShader(program, fs)
    glLinkProgram(program)
    if glGetProgramiv(program, GL_LINK_STATUS) != GL_TRUE:
        raise RuntimeError("program link failed: " + str(glGetProgramInfoLog(program)))
    glDeleteShader(vs)
    glDeleteShader(fs)
    return program


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

# 无太阳分支 lightSpaceMat 的占位矩阵（模块级常量永生，value_ptr 指针恒有效；
# 若在调用点写 glm.mat4(1.0) 临时对象，会在 uniform 队列 flush 前被 GC 释放，
# 传入悬垂指针指向的垃圾矩阵）
_IDENTITY_MAT4 = glm.mat4(1.0)


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
                                 else _IDENTITY_MAT4))

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


class RoughnessSurface(soup3D.shader.AutoSP):
    """粗糙度通道表面：把材质 roughness 作为颜色渲进粗糙度 RT（SSR 模糊/衰减采样）。

    继承 AutoSP 获得 rend_cached 拆分/VAO 缓存与 set_mat_queue 的
    model/view/projection 推送（相机 POV 直绘，与主通道同视角）；VS 只消费
    位置（uv/normal 属性使能但未被 program 消费，GL 合法，DepthSurface 先例）。
    FS 输出 alpha=1，blend 下无实际混合，无需改混合状态。
    """

    def __init__(self, roughness):
        self._roughness = float(roughness)
        # AutoSP 构造需要一个 base_color（uniform_tex 单元 0 绑定占位，FS 无此
        # 采样器，location=-1 被引擎静默忽略）
        super().__init__(soup3D.shader.MixChannel((1, 1), 1.0, 1.0, 1.0, 1.0),
                         double_side=True, max_light_count=1)

    def create_shader_program(self):
        vertex_shader = """
        #version 330 core
        layout(location = 0) in vec3 VertPos;
        layout(location = 1) in vec2 VertUV;
        layout(location = 2) in vec3 VertNormal;

        uniform mat4 model;
        uniform mat4 view;
        uniform mat4 projection;

        void main()
        {
            gl_Position = projection * view * vec4(VertPos, 1.0);
        }
        """
        fragment_shader = """
        #version 330 core
        uniform float roughness;
        out vec4 FragColor;
        void main()
        {
            FragColor = vec4(roughness, 0.0, 0.0, 1.0);
        }
        """
        shader_program = soup3D.shader.ShaderProgram(
            vertex_shader, fragment_shader,
            vbo_type=[soup3D.FLOAT, soup3D.FLOAT, soup3D.FLOAT])
        shader_program.uniform("roughness", soup3D.FLOAT_VEC1, self._roughness)
        return shader_program


class PBRSurface(_ShadowSurfaceBase):
    """PBR 表面（Cook-Torrance GGX + 3×3 PCF 太阳阴影）。

    每材质一个实例（make_surface 内省接收 roughness/metallic，来自 MTL 的
    Pr/Pm，文件未提供时用类默认）。cameraPos 每帧补传（GGX 视线方向）；
    阴影 uniform 由 _ShadowSurfaceBase 按 registry version 缓存补传。
    """

    def __init__(self, base_color, normal=(0.5, 0.5, 1), emission=(0, 0, 0),
                 double_side=True, max_light_count=8,
                 roughness=None, metallic=DEFAULT_METALLIC, shininess=None):
        # 粗糙度回退链：显式 Pr(roughness) > Ns(shininess) 换算 > 类默认。
        # 引擎 open_mtl 把 Pr 读进 roughness、Ns 读进 shininess，均仅在文件
        # 提供时传入（make_surface 内省过滤）——两者皆缺即默认哑光
        if roughness is None:
            roughness = roughness_from_shininess(shininess)
        self.roughness = float(roughness)
        self.metallic = float(metallic)
        self._last_cam = None
        super().__init__(base_color, normal=normal, emission=emission,
                         double_side=double_side, max_light_count=max_light_count)

    def create_shader_program(self):
        vertex_shader = """
        #version 330 core
        layout(location = 0) in vec3 VertPos;
        layout(location = 1) in vec2 VertUV;
        layout(location = 2) in vec3 VertNormal;

        out vec2 TexCoord;
        out vec3 FragPos;
        out vec3 Normal;
        out vec4 LightSpacePos;

        uniform mat4 model;
        uniform mat4 view;
        uniform mat4 projection;
        uniform mat4 lightSpaceMat;   // 太阳正交矩阵（阴影 uniform 下发）

        void main()
        {
            FragPos = vec3(model * vec4(VertPos, 1.0));
            mat3 normalMatrix = transpose(inverse(mat3(model)));
            Normal = normalMatrix * VertNormal;
            gl_Position = projection * view * vec4(FragPos, 1.0);
            TexCoord = vec2(VertUV.x, 1-VertUV.y);
            LightSpacePos = lightSpaceMat * vec4(FragPos, 1.0);
        }
        """
        fragment_shader = """
        #version 330 core
        in vec2 TexCoord;
        in vec3 FragPos;
        in vec3 Normal;
        in vec4 LightSpacePos;
        out vec4 FragColor;

        // 材质属性（纹理单元照引擎约定：baseColor=0、normal=1、shadowMap=2、emission=3）
        uniform sampler2D baseColor;
        uniform sampler2D normal;
        uniform sampler2D shadowMap;
        uniform sampler2D emission;
        uniform float roughness;
        uniform float metallic;
        uniform vec3 cameraPos;      // 引擎相机（GGX 视线方向，update 每帧补传）
        uniform mat4 lightSpaceMat;
        uniform int shadowEnabled;

        // 光照结构体与 AutoSP 完全同布局（引擎灯光上传逻辑零改动适配）
        struct Light {
            vec3 position;
            vec3 direction;
            vec3 color;
            float attenuation;
            float angle;
            float cosAngle;
            int type;
        };
        uniform Light lights[%i];
        uniform int lightCount;
        uniform vec3 ambient;

        // 3×3 PCF 太阳阴影（返回被遮挡比例 0..1）
        float shadow_sample(vec4 lsPos, vec3 n, vec3 l)
        {
            if (shadowEnabled == 0) {
                return 0.0;
            }
            vec3 proj = lsPos.xyz / lsPos.w;
            if (proj.z > 1.0) {
                return 0.0;   // 超出阴影相机远平面 → 视为受光
            }
            vec2 uv = proj.xy * 0.5 + 0.5;
            if (uv != clamp(uv, vec2(0.0), vec2(1.0))) {
                return 0.0;   // 阴影贴图范围外 → 受光
            }
            float bias = max(0.05 * (1.0 - dot(n, l)), 0.005);
            float shadowed = 0.0;
            vec2 texel = 1.0 / vec2(textureSize(shadowMap, 0));
            for (int x = -1; x <= 1; x++) {
                for (int y = -1; y <= 1; y++) {
                    float d = texture(shadowMap, uv + vec2(x, y) * texel).r;
                    shadowed += (d < proj.z * 0.5 + 0.5 - bias) ? 1.0 : 0.0;
                }
            }
            return shadowed / 9.0;
        }

        void main()
        {
            vec3 SideNormal = Normal;
            %s

            vec4 base = texture(baseColor, TexCoord);
            vec4 norm_tex = texture(normal, TexCoord);
            vec3 N = normalize(normalize(SideNormal) + vec3(norm_tex.rg*2-1, norm_tex.b-1));
            vec4 emi = texture(emission, TexCoord);
            vec3 V = normalize(cameraPos - FragPos);
            vec3 f0 = mix(vec3(0.04), base.rgb, metallic);

            // 环境光项保持引擎 ambient 联动：ambient·albedo + 0.04·f0·ambient 近似环境高光
            vec3 color = ambient * base.rgb + 0.04 * f0 * ambient;

            // 逐光源 Cook-Torrance（点光锥角衰减 + 方向光分支照抄 AutoSP 光照循环）
            for (int i = 0; i < lightCount; i++) {
                vec3 lightDir;
                float attenuation = 1.0;
                float spotFactor = 1.0;
                if (lights[i].type == 0) {
                    lightDir = normalize(lights[i].position - FragPos);
                    float distance = length(lights[i].position - FragPos);
                    attenuation = 1.0 / (1.0 + lights[i].attenuation * distance);
                    vec3 spotDir = normalize(-lights[i].direction);
                    float cosTheta = dot(lightDir, spotDir);
                    if (cosTheta > lights[i].cosAngle) {
                        float epsilon = lights[i].cosAngle - lights[i].cosAngle * 0.9;
                        spotFactor = clamp((cosTheta - lights[i].cosAngle) / epsilon, 0.0, 1.0);
                    } else {
                        spotFactor = 0.0;
                    }
                    attenuation *= spotFactor;
                } else { // 方向光
                    lightDir = normalize(lights[i].direction);
                }

                vec3 L = lightDir;
                float ndl = max(dot(N, L), 0.0);
                if (ndl <= 0.0) {
                    continue;   // 背光面无直接光贡献
                }
                float shadow = shadow_sample(LightSpacePos, N, L);
                float lit = (1.0 - shadow) * attenuation;
                if (lit <= 0.0) {
                    continue;
                }

                vec3 H = normalize(V + L);
                float ndv = max(dot(N, V), 0.0);
                float ndh = max(dot(N, H), 0.0);
                float vdh = max(dot(V, H), 0.0);
                // GGX：D = a²/(π((n·h)²(a²-1)+1)²)
                float a = roughness * roughness;
                float a2 = a * a;
                float dd = ndh * ndh * (a2 - 1.0) + 1.0;
                float D = a2 / (3.14159265 * dd * dd);
                // G = Smith–Schlick（k = (r+1)²/8）
                float k = (roughness + 1.0) * (roughness + 1.0) / 8.0;
                float G = (ndv / (ndv * (1.0 - k) + k)) * (ndl / (ndl * (1.0 - k) + k));
                // F = Schlick（f0 = mix(0.04, albedo, metallic)）
                vec3 F = f0 + (1.0 - f0) * pow(1.0 - vdh, 5.0);
                vec3 specular = D * G * F / max(4.0 * ndv * ndl, 1e-4);
                // 漫反射 Lambert：(1-metallic)·albedo/π
                vec3 kd = (1.0 - metallic) * base.rgb;
                vec3 direct = (kd / 3.14159265 * ndl + specular * ndl) * lights[i].color;
                color += direct * lit;
            }

            // 自发光（与 AutoSP 同式：base.rgb * emi.rgb）
            color += base.rgb * emi.rgb;
            FragColor = vec4(color, base.a);
        }
        """

        if self.double_side:
            fragment_shader = fragment_shader % (
                self.max_light_count,
                """
                if (!gl_FrontFacing) {
                    SideNormal = -Normal;
                }
                """
            )
        else:
            fragment_shader = fragment_shader % (
                self.max_light_count,
                """
                if (!gl_FrontFacing) {
                    discard;
                }
                """
            )

        shader_program = soup3D.shader.ShaderProgram(
            vertex_shader, fragment_shader,
            vbo_type=[soup3D.FLOAT, soup3D.FLOAT, soup3D.FLOAT])

        # 纹理单元照抄 AutoSP 的 uniform_tex 绑定序列（rend_cached 的 texture_val
        # 循环会逐帧自动重绑全部单元）：baseColor=0、normal=1、emission=3
        shader_program.uniform_tex("baseColor", self.base_color, 0)
        if isinstance(self.normal, (list, tuple)):
            shader_program.uniform_tex("normal",
                                       soup3D.shader.MixChannel((1, 1), *self.normal), 1)
        else:
            shader_program.uniform_tex("normal", self.normal, 1)
        if isinstance(self.emission, (list, tuple)):
            shader_program.uniform_tex("emission",
                                       soup3D.shader.MixChannel((1, 1), *self.emission), 3)
        else:
            shader_program.uniform_tex("emission", self.emission, 3)

        # 材质参数初始上传
        shader_program.uniform("roughness", soup3D.FLOAT_VEC1, self.roughness)
        shader_program.uniform("metallic", soup3D.FLOAT_VEC1, self.metallic)
        return shader_program

    def update(self):
        # cameraPos 每帧跟随引擎相机（GGX 视线方向）：相机移动经 set_view_mat
        # 置脏 → 渲染循环按 is_dirty 调 update，此处相机位置变化即补传
        cam = (soup3D.camera.X, soup3D.camera.Y, soup3D.camera.Z)
        if cam != self._last_cam:
            self._last_cam = cam
            self.shader_program.uniform("cameraPos", soup3D.FLOAT_VEC3, *cam)
        super().update()   # _ShadowSurfaceBase.update：阴影 uniform 补传 + AutoSP flush


class QualityFrameRenderer:
    """画面优先帧渲染器：阴影通道 → 粗糙度通道 → HDR 主通道 → SSR+ACES
    合并合成通道（全屏三角直绘默认帧缓冲上屏）。

    资源（构造时创建，要求 GL 上下文就绪）：阴影 RT（固定 2048²）+ HDR RT
    （窗口尺寸 rgba16f + 深度纹理）+ 粗糙度 RT（窗口尺寸 rgba8）。
    dispose() 供 GL 上下文销毁前释放。
    """

    def __init__(self, width, height):
        self._shadow_rt = RenderTarget(SHADOW_SIZE, SHADOW_SIZE, "rgba8", "texture")
        self._hdr_rt = RenderTarget(width, height, "rgba16f", "texture")
        self._rough_rt = RenderTarget(width, height, "rgba8", "renderbuffer")
        # DepthSurface/RoughnessSurface 按 Face 对象弱引用缓存（face 释放自动
        # 清理，规避 id 复用风险）
        self._depth_surfaces = weakref.WeakKeyDictionary()
        self._rough_surfaces = weakref.WeakKeyDictionary()
        # 合成通道：全屏三角辅助 + 以当前常量编译的程序（SSR_STEPS 可在构造前
        # monkeypatch 做 A/B；A/B 测试即依赖此语义）
        self._composite_pass = api.FullScreenPass()
        self._composite_prog = _compile_program(
            api.FULLSCREEN_VS,
            _COMPOSITE_FS % (SSR_STEPS, SSR_REFINE, SSR_STRENGTH, SSR_BASE,
                             SSR_THICKNESS, SSR_MAX_DIST, SSR_BLUR_RADIUS))

    def render_frame(self, draw_scene):
        has_sun = self._update_shadow_registry(draw_scene.faces)
        if has_sun:
            self._render_shadow_pass(draw_scene.faces)
        self._render_roughness_pass(draw_scene.faces)
        self._render_main_pass(draw_scene)
        self._render_composite()

    def dispose(self):
        """GL 上下文销毁前释放：删 RT/合成程序/全屏 VAO（引擎同模式，失败静默）"""
        self._depth_surfaces.clear()
        self._rough_surfaces.clear()
        self._shadow_rt = None
        self._hdr_rt = None
        self._rough_rt = None
        try:
            if self._composite_prog is not None:
                glDeleteProgram(self._composite_prog)
                self._composite_prog = None
        except Exception:
            pass
        if self._composite_pass is not None:
            self._composite_pass.dispose()
            self._composite_pass = None

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

    def _render_roughness_pass(self, faces):
        """粗糙度通道：每材质 roughness 渲进粗糙度 RT（相机 POV，与主通道同
        视角），SSR 据此做反射模糊与衰减。清屏白色 = 全粗糙（语义保守：无数据
        处无反射）。FS 输出 alpha=1，blend 下无实际混合，无需改混合状态。"""
        rt = self._rough_rt
        rt.bind()
        try:
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
                    # 同模型 faces 连续排列（spec 4.3）：组首计算模型矩阵一次
                    last_model = model
                    model_mat = model.get_model_mat()
                rs = self._rough_surface_for(face)
                rs.set_model_mat(model_mat)
                if rs.is_dirty():
                    rs.update()
                rs.use()
                rs.rend_cached(face.mode, face.vertex)
                rs.unuse()
        finally:
            rt.unbind()

    def _rough_surface_for(self, face):
        """按 Face 对象弱引用缓存 RoughnessSurface（face 释放自动清理）"""
        rs = self._rough_surfaces.get(face)
        if rs is None:
            rough = getattr(face.surface, "roughness", DEFAULT_ROUGHNESS)
            rs = RoughnessSurface(rough)
            # 引擎表面矩阵模型 = 「构造时默认单位阵 + 相机事件推送」；本表面创建
            # 晚于 Model 构造与相机事件，必须补推当前矩阵，否则 view/projection
            # 恒为单位阵、顶点全部落在裁剪体之外（粗糙度 RT 全为清屏色的根因）。
            # 创建时补推一次即可：此后 camera 事件经 set_mat_queue 持续覆盖。
            rs.set_view_mat(soup3D.camera.get_view_mat())
            rs.set_projection_mat(soup3D.get_projection_mat())
            self._rough_surfaces[face] = rs
        return rs

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

    def _render_composite(self):
        """合成通道：全屏三角直绘默认帧缓冲——HDR 颜色 + 深度 + 粗糙度重建做
        SSR（粗糙度调制模糊与衰减），背景（深度=1）直通固定色，场景像素
        曝光 → ACES 后上屏。GL 状态卫生照 RenderTarget.present 的保存/恢复
        模式。"""
        prog = self._composite_prog
        prev_viewport = tuple(int(v) for v in glGetIntegerv(GL_VIEWPORT))
        prev_draw_fbo = _gl_scalar(glGetIntegerv(GL_DRAW_FRAMEBUFFER_BINDING))
        prev_read_fbo = _gl_scalar(glGetIntegerv(GL_READ_FRAMEBUFFER_BINDING))
        prev_depth = glIsEnabled(GL_DEPTH_TEST)
        prev_blend = glIsEnabled(GL_BLEND)
        prev_program = _gl_scalar(glGetIntegerv(GL_CURRENT_PROGRAM))
        prev_active_tex = _gl_scalar(glGetIntegerv(GL_ACTIVE_TEXTURE))
        prev_unit0_tex = _gl_scalar(glGetIntegerv(GL_TEXTURE_BINDING_2D))
        # 切默认帧缓冲，禁深度/混合，绑定 HDR 颜色(单元0) + HDR 深度(单元1)
        glBindFramebuffer(GL_FRAMEBUFFER, 0)
        glDisable(GL_DEPTH_TEST)
        glDisable(GL_BLEND)
        glUseProgram(prog)
        glActiveTexture(GL_TEXTURE0)
        glBindTexture(GL_TEXTURE_2D, self._hdr_rt.color.get_texture_id())
        glActiveTexture(GL_TEXTURE1)
        glBindTexture(GL_TEXTURE_2D, self._hdr_rt.depth.get_texture_id())
        glActiveTexture(GL_TEXTURE2)
        glBindTexture(GL_TEXTURE_2D, self._rough_rt.color.get_texture_id())
        glUniform1i(glGetUniformLocation(prog, "hdrColor"), 0)
        glUniform1i(glGetUniformLocation(prog, "hdrDepth"), 1)
        glUniform1i(glGetUniformLocation(prog, "roughTex"), 2)
        proj = soup3D.get_projection_mat()
        # inverse 先存局部变量再取 value_ptr：对临时对象取指针会在 PyOpenGL
        # 参数转换链中被 GC（上传已释放内存的垃圾矩阵，Task 10 注释同款坑）
        inv_proj = glm.inverse(proj)
        glUniformMatrix4fv(glGetUniformLocation(prog, "proj"), 1, GL_FALSE,
                           glm.value_ptr(proj))
        glUniformMatrix4fv(glGetUniformLocation(prog, "invProj"), 1, GL_FALSE,
                           glm.value_ptr(inv_proj))
        glUniform1f(glGetUniformLocation(prog, "exposure"), EXPOSURE)
        self._composite_pass.draw(prog)
        # 恢复状态（present 同模式）
        glActiveTexture(GL_TEXTURE0)
        glBindTexture(GL_TEXTURE_2D, prev_unit0_tex)
        glActiveTexture(prev_active_tex)
        glUseProgram(prev_program)
        if prev_depth:
            glEnable(GL_DEPTH_TEST)
        else:
            glDisable(GL_DEPTH_TEST)
        if prev_blend:
            glEnable(GL_BLEND)
        else:
            glDisable(GL_BLEND)
        glBindFramebuffer(GL_DRAW_FRAMEBUFFER, prev_draw_fbo)
        glBindFramebuffer(GL_READ_FRAMEBUFFER, prev_read_fbo)
        glViewport(prev_viewport[0], prev_viewport[1], prev_viewport[2], prev_viewport[3])


# 表面类契约（spec 4.3）：场景以 surface_class 实例化材质（make_surface 内省过滤，
# 只传该类可接收的标准材质参数）
surface_class = PBRSurface


# 帧渲染器契约（spec 4.3）：游戏以当前窗口尺寸实例化，每帧经 pack_frame 调用；
# 引擎光源/EAU 由游戏 runtime 在调用前自动处理，包无需自调
def create_frame_renderer(width, height):
    return QualityFrameRenderer(width, height)
