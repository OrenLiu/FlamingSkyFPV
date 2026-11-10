# 着色器预设包作者指南

> **信任模型（先读）**：运行下载的 `pack.py` = 执行任意第三方代码，可访问你的
文件系统与网络——与 Minecraft 模组同等的信任模型：**只安装来源可信的包**。
本系统不做沙箱（Python 沙箱不可靠，非目标）。

## 目录结构与安装

把包放入仓库根 `shader_packs/<包目录名>/`（与 `scene/` 平行，用户内容不入版本库），
启动游戏后在设置页「画质预设」下拉选择；生效时机 = 下次进入飞行场景（场景以
当时的表面类加载）。选择存档存包 id：内置为 `builtin_performance`（性能优先）、
`builtin_quality`（画面优先）；文件夹包 id = 目录名，目录名以 `builtin_` 开头无效。

```
shader_packs/<包目录名>/
├── pack.json   # 元信息（name 必填，其余可选）
└── pack.py     # 代码（模块级 surface_class 必填）
```

非法包不会被剔除：设置页下方以文字行列出原因（如「我的包（无效：pack.json 解析
失败：…）已跳过」），存档 id 失效时回落性能优先并在设置页提示。

## pack.json 字段

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `name` | 是 | 显示名（非空字符串，如「我的电影感包」） |
| `description` | 否 | 一句话描述（设置页显示） |
| `author` | 否 | 作者 |
| `version` | 否 | 版本字符串 |

## pack.py 契约

| 属性 | 必填 | 说明 |
| --- | --- | --- |
| `surface_class` | 是 | 表面着色器类/工厂。加载器经 `shader.make_surface` 内省，只传该类可接收的标准材质参数：`base_color`、`normal`、`emission`、`double_side`、`max_light_count`、`roughness`、`metallic`、`ior`、`transmission`、`shininess`、`illum`（`roughness`/`metallic` 来自 MTL 的 `Pr`/`Pm`，文件未提供时不传） |
| `skinned_surface_class` | 否 | 蒙皮表面类（GLTF 场景用）。缺省 = `surface_class`。一期游戏无蒙皮场景，接口预留 |
| `create_frame_renderer(width, height)` | 否 | 返回帧渲染器实例；缺省 = 无（默认管线直渲窗口） |

帧渲染器实例契约：

| 方法 | 必填 | 说明 |
| --- | --- | --- |
| `render_frame(draw_scene)` | 是 | 接管整帧（含清屏）。`draw_scene()` 把场景内容画进**当前绑定的渲染目标**；`draw_scene.faces` 为 `[(model, face), ...]`（阴影通道迭代用；同模型 faces 连续排列；无场景时为空列表）。引擎光源/EAU 由游戏 runtime 在调用前自动处理，包无需自调 |
| `dispose()` | 否 | GL 上下文销毁前释放资源（RT/程序/纹理）。缺省依赖 GC + `__del__` |

## 约定与注意

- **纹理单元**：`baseColor=0`、`normal=1`、`shadowMap=2`（阴影预设用）、`emission=3`。
- **stable_shapes / render_queue 不渲染**：预设帧渲染路径不执行引擎的
  `stable_shapes` 与 `render_queue` 回放（本游戏从不使用 `Model.show()/paint()`，
  两队列恒空）。
- **运行路径**：游戏从仓库根启动（`python main.py`），包代码可 `import soup3D`
  与 `import shaderpacks.api`。
- **窗口重建**（改分辨率/全屏）会销毁 GL 上下文：预设资源经 `dispose()`/重载
  重建，包内 RT/程序不可跨上下文缓存句柄。
- **性能**：llvmpipe（无 GPU 环境）下画面优先会显著掉帧，属预期；帧渲染器内
  部常量（阴影尺寸、SSR 步数、曝光等）集中在 `shaderpacks/builtin/quality/pack.py`
  文件顶部，可作调优参照。

## 便利 API（shaderpacks.api）

- `frame_preprocess()`：引擎光源/EAU 前置处理。游戏 runtime 每帧经 `pack_frame`
  自动调用；导出仅为文档完整性与高级用法（包完全自管整帧、绕开 `pack_frame`
  时需自调）。
- `SurfaceShaderBase`：= `soup3D.shader.AutoSP` 别名。继承后重写
  `create_shader_program()` 返回自定义 GLSL 的 `ShaderProgram` 即可（引擎
  `BoneBinderSP` 即此模式先例）；需补充 uniform 时重写 `update()`（先补传
  自己的，再 `super().update()`）。
- `FULLSCREEN_VS` + `FullScreenPass`：全屏三角形通道辅助（`gl_VertexID` 生成
  顶点），写 SSR/合成通道直接用，不必自管顶点缓冲。`draw` 只归位 VAO 绑定；
  着色程序、深度测试等其余状态由调用方自管（参照内置 quality 包
  `_render_composite` 的保存/恢复写法）。
- `SKIN_SURFACE_HINT`：蒙皮类继承 `soup3D.shader.BoneBinderSP` 的文档说明
  （一期无蒙皮示例）。

## 最小可行示例：换材质着色器

```
shader_packs/tinted/
├── pack.json   # {"name": "青色调包"}
└── pack.py     # 如下
```

```python
import soup3D


class TintedSurface(soup3D.shader.AutoSP):
    """整体加青色调的表面着色器（教学示例）"""

    def create_shader_program(self):
        vertex_shader = """
        #version 330 core
        layout(location = 0) in vec3 VertPos;
        layout(location = 1) in vec2 VertUV;
        layout(location = 2) in vec3 VertNormal;
        out vec2 TexCoord;
        out vec3 FragPos;
        out vec3 Normal;
        uniform mat4 model;
        uniform mat4 view;
        uniform mat4 projection;
        void main()
        {
            FragPos = vec3(model * vec4(VertPos, 1.0));
            mat3 normalMatrix = transpose(inverse(mat3(model)));
            Normal = normalMatrix * VertNormal;
            gl_Position = projection * view * vec4(FragPos, 1.0);
            TexCoord = vec2(VertUV.x, 1-VertUV.y);
        }
        """
        fragment_shader = """
        #version 330 core
        in vec2 TexCoord;
        in vec3 FragPos;
        in vec3 Normal;
        out vec4 FragColor;
        uniform sampler2D baseColor;
        uniform sampler2D normal;
        uniform sampler2D emission;
        struct Light { vec3 position; vec3 direction; vec3 color;
                       float attenuation; float angle; float cosAngle; int type; };
        uniform Light lights[%i];
        uniform int lightCount;
        uniform vec3 ambient;
        void main()
        {
            vec3 SideNormal = Normal;
            %s
            vec4 base = texture(baseColor, TexCoord);
            vec4 norm_tex = texture(normal, TexCoord);
            vec3 norm = normalize(SideNormal) + vec3(norm_tex.rg*2-1, norm_tex.b-1);
            vec4 emi = texture(emission, TexCoord);
            vec3 diffuse = vec3(0.0);
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
                } else {
                    lightDir = normalize(lights[i].direction);
                }
                float diff = max(dot(norm, lightDir), 0.0);
                diffuse += lights[i].color * diff * attenuation;
            }
            vec3 result = (ambient + diffuse) * base.rgb + base.rgb * emi.rgb;
            result *= vec3(0.9, 1.05, 1.05);   // 教学示例：青色调增益
            FragColor = vec4(result, base.a);
        }
        """
        if self.double_side:
            fragment_shader = fragment_shader % (self.max_light_count,
                "if (!gl_FrontFacing) { SideNormal = -Normal; }")
        else:
            fragment_shader = fragment_shader % (self.max_light_count,
                "if (!gl_FrontFacing) { discard; }")
        shader_program = soup3D.shader.ShaderProgram(
            vertex_shader, fragment_shader,
            vbo_type=[soup3D.FLOAT, soup3D.FLOAT, soup3D.FLOAT])
        shader_program.uniform_tex("baseColor", self.base_color, 0)
        if isinstance(self.normal, (list, tuple)):
            shader_program.uniform_tex(
                "normal", soup3D.shader.MixChannel((1, 1), *self.normal), 1)
        else:
            shader_program.uniform_tex("normal", self.normal, 1)
        if isinstance(self.emission, (list, tuple)):
            shader_program.uniform_tex(
                "emission", soup3D.shader.MixChannel((1, 1), *self.emission), 3)
        else:
            shader_program.uniform_tex("emission", self.emission, 3)
        return shader_program


surface_class = TintedSurface
```

## 进阶：帧渲染器 / 多通道

写帧渲染器时以内置「画面优先」包为参考实现（PBR + 太阳阴影贴图 + SSR + HDR/
ACES 的完整多通道管线）：

- `shaderpacks/builtin/quality/pack.py`：`QualityFrameRenderer`（阴影通道 → HDR
  主通道 → SSR+ACES 合成通道）与 `PBRSurface` / `DepthSurface`；
- `shaderpacks/api.py`：`FullScreenPass` / `FULLSCREEN_VS` / `frame_preprocess`；
- `soup3D/target.py`：`RenderTarget`（离屏渲染目标，`uniform_tex` 可直接采样其
  `color` / `depth` 附着，`present()` 直通上屏）。

阴影正交相机、SSR 步数、曝光等常量集中在 `quality/pack.py` 顶部，大地图或
性能不达标时照此调整。
