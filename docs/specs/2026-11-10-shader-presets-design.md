# 着色器预设系统设计

日期：2026-11-10
状态：待审阅

## 1. 背景与目标

用户需求：添加着色器预设功能，用户可根据喜好自制、下载或使用着色器预设。将预设包放入对应文件夹，在设置中选择即可改变游戏画质；用户可通过编写代码自制着色器包。默认内置两个包：

- **性能优先**：即当前画面效果（默认管线 + AutoSP），零行为变化。
- **画面优先**：PBR 材质（Cook-Torrance GGX）+ 方向光阴影贴图（PCF 软阴影）+ SSR 屏幕空间反射 + HDR（rgba16f）+ ACES 色调映射。

已与用户裁定的两个决策：

1. **包格式 = Python 代码包**（文件夹：`pack.json` 元信息 + `pack.py` 代码）。下载的包按模组信任模型对待——运行第三方代码，文档明示风险。
2. **画面优先范围 = PBR + 阴影 + SSR + HDR/色调映射**。不含 SSAO/Bloom（非目标）。

## 2. 方案对比与选型

| 方案 | 说明 | 结论 |
| --- | --- | --- |
| A. 游戏侧 `shaderpacks` 模块 + 引擎现成钩子 | 引擎零改动；用 `soup3D.update(render=...)` 帧级渲染器接管 + `open_obj(surface=...)` 注入表面着色器 + `RenderTarget` 离屏通道 | **选定**。上游同步零负担；引擎已有全部积木 |
| B. 引擎内建预设管线 | 引擎新增 prepare_frame 与管线调度，包只提供 GLSL 片段 | 引擎改动大，违背 vendored 副本最小改动原则，上游同步负担重 |
| C. 默认管线 + 后处理钩子 | 主通道仍走默认管线，包只做屏幕后处理 | 需要引擎加"渲进 RT"开关；阴影图无法自然融入主通道；多一次全屏拷贝 |

引擎已有的三个关键积木（探索结论）：

- `soup3D.update(render=...)`：帧级渲染器接管整帧（含清屏），引擎前后保存/恢复 GL 状态（`glPushAttrib` + FBO 绑定）。
- `shader.make_surface()` + `open_obj(surface=...)`：模型加载器按类签名内省动态适配任意表面着色器类，只传该类可接收的标准材质参数（`base_color`、`normal`、`emission`、`double_side`、`max_light_count`、`roughness`、`metallic`、`ior`、`transmission`、`shininess`、`illum`）。
- `soup3D.target.RenderTarget`：FBO + 颜色纹理（`rgba8`/`rgba16f`/`rgba32f`）+ 可选深度附着（renderbuffer/texture），可被 `uniform_tex` 直接采样，`present()` 直通上屏。

自定义帧渲染路径不会执行引擎 `frame_render()` 内的光源脏标记与 EAU 处理（约 10 行引擎内部逻辑），由游戏侧 `shaderpacks/runtime.py` 复刻，**引擎一行不改**。

## 3. 架构总览

新增游戏侧 Python 包 `shaderpacks/`（与 screens/ 平级）：

```
shaderpacks/
├── __init__.py        # 公共 API：list_packs() / PackInfo
├── registry.py        # 包发现与元信息校验（内置 + 文件夹）
├── loader.py          # pack.py 动态加载与接口契约校验
├── runtime.py         # 帧辅助：frame_preprocess() / pack_frame()
├── api.py             # 包作者便利 API（基类与辅助函数）
└── builtin/
    ├── performance/   # 内置「性能优先」（pack.json + pack.py）
    │   ├── pack.json
    │   └── pack.py
    └── quality/       # 内置「画面优先」（参考实现）
        ├── pack.json
        └── pack.py
```

用户包目录 `shader_packs/`（仓库根，与 `scene/` 平行，加入 .gitignore；用户内容不进版本库）。

数据流（画面优先包激活、飞行中每帧）：

```
app.run()
  └─ soup3D.update(render=lambda: pack_frame(renderer, screen.render_scene))
       └─ pack_frame:
            1. frame_preprocess()        # light.dirty → set_surface_light；EAU 执行清空
            2. renderer.render_frame(draw_scene)
                 a. 阴影通道：光源正交矩阵 → 深度 RT（迭代模型 faces 直绘深度着色器）
                 b. 主通道：HDR RT 清屏（天空色）→ draw_scene()  # PBR 表面 rend_cached 进 RT
                 c. SSR 通道：全屏三角形，采样 HDR 颜色 + 深度，屏幕空间光线步进
                 d. 合成通道：ACES 色调映射 + gamma → 直绘默认帧缓冲
  └─ imgui UI（不变，画在合成结果之上）
```

场景加载（flight.py `_load_scene`）注入包表面类：

```
soup3D.open_obj(str(obj), mtl, surface=app.preset.surface_class)
```

性能优先包 `surface_class = soup3D.shader.AutoSP`，与现状完全一致（`open_obj` 默认本就是 AutoSP）。

## 4. 包格式与接口契约

### 4.1 目录结构

```
shader_packs/<包目录名>/
├── pack.json   # 元信息
└── pack.py     # 代码
```

### 4.2 pack.json

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `name` | 是 | 显示名（如「我的电影感包」） |
| `description` | 否 | 一句话描述（设置页显示） |
| `author` | 否 | 作者 |
| `version` | 否 | 版本字符串 |

### 4.3 pack.py 模块级契约

| 属性 | 必填 | 说明 |
| --- | --- | --- |
| `surface_class` | 是 | 表面着色器类/工厂。可声明接收 `SURFACE_MATERIAL_PARAMS` 标准材质参数的任意子集（加载器经 `make_surface` 内省过滤）；`roughness`/`metallic` 来自 MTL 的 `Pr`/`Pm`，文件未提供时不传 |
| `skinned_surface_class` | 否 | 蒙皮表面类（GLTF 场景用）。缺省 = `surface_class`。一期游戏无蒙皮场景，接口预留 |
| `create_frame_renderer(width, height)` | 否 | 返回帧渲染器实例；缺省 = 无（默认管线直渲窗口） |
| `post_load(model, mtl_path)` | 否 | 场景加载钩子（2026-11-11 扩展）：open_obj 之后、首帧前调用，供包做逐材质扩展（如 MTL `map_Ns` 逐像素粗糙度贴图注入——积水地面等"同一材质内部分镜面部分漫反射"的表达）。mtl_path 为场景 MTL 路径或 None |

帧渲染器实例契约：

| 方法 | 必填 | 说明 |
| --- | --- | --- |
| `render_frame(draw_scene)` | 是 | 接管整帧。`draw_scene()` 把当前屏幕的 3D 场景内容画进**当前绑定的渲染目标**（内部即 `screen.render_scene`：逐 face `use → rend_cached → unuse`）。引擎光源/EAU 由游戏 runtime 在调用前自动处理（`pack_frame`，包无需自调）。`draw_scene` 带 `.faces` 属性（见下） |
| `dispose()` | 否 | GL 上下文销毁前释放资源（RT/程序/纹理）。缺省依赖 GC + `__del__` |
| `set_sky(sun_yaw, sun_pitch, sun_color, panorama_path=None)` | 否 | 场景天空配置钩子（2026-11-11 扩展）：生成/加载等距圆柱天空盒（自带全景图优先，否则程序生成蓝天渐变+噪声云朵+太阳光斑），画面优先背景与 SSR miss 回退消费；太阳参数与场景方向光同源，保证太阳、光照、反射三者一致 |

`draw_scene.faces`：`[(model, face), ...]` 列表，本帧场景的全部模型面（阴影通道迭代用；深度着色需要模型矩阵，故以 (model, face) 对给出，同模型 faces 连续排列）。无场景时为空列表。由游戏 runtime 的 `SceneDrawer` 包装提供（包作者只读，不感知 screen 内部）。

包代码自由 `import soup3D` 使用全部引擎公开 API；`shaderpacks.api` 只提供便利层，不设权限边界。

### 4.4 包 id 约定

- 内置包 id：`builtin_performance`（性能优先）、`builtin_quality`（画面优先）。
- 文件夹包 id = 目录名。目录名以 `builtin_` 开头 → 标记 invalid（保留前缀，避免与内置冲突）。
- 选择存档存 id；`Settings.shader_preset` 缺失或指向无效包 → 启动/切换时回落 `builtin_performance`，设置页显示回落提示。

### 4.5 信任模型

运行下载的 `pack.py` = 执行任意第三方代码（可访问文件系统与网络）。与 Minecraft 模组同等的信任模型：只安装来源可信的包。`docs/shader_packs.md` 包作者指南开头明示此风险。不做沙箱（Python 沙箱不可靠，非目标）。

## 5. 组件详细设计

### 5.1 registry.py — 发现与元信息

- `PackInfo` dataclass：`id`、`dir`（Path，内置包为包内路径）、`name`、`description`、`author`、`version`、`builtin`（bool）、`valid`、`error`。
- `list_packs(packs_root="shader_packs") at-> list[PackInfo]`：先枚举内置（`shaderpacks/builtin/*/pack.json`），再枚举文件夹。非法包标记 invalid 而非剔除（镜像 `scene_loader` 哲学），`error` 记原因；文件夹无效包在设置页下方文字行显示，不进下拉。
- 排序：内置在前（performance、quality），文件夹包按目录名排序。
- 校验只读 pack.json（JSON 可解析、`name` 为非空字符串、`builtin_` 前缀冲突）；pack.py 的代码级校验在 loader。

### 5.2 loader.py — 代码加载与契约校验

- `load_pack(info: PackInfo) -> LoadedPack`：`importlib.util.spec_from_file_location` 加载 `pack.py`（模块名含包 id 防冲突）；校验 `surface_class` 可调用、`create_frame_renderer`（若有）可调用；异常捕获转 invalid。
- `LoadedPack`：`info`、`surface_class`、`skinned_surface_class`（缺省=surface_class）、`create_frame_renderer` 或 None、`raw_module`。
- 不缓存模块对象（同一 id 重复加载用 `sys.modules` 覆盖语义：先从 `sys.modules` 弹出旧条目再加载，避免旧实例残留）。

### 5.3 runtime.py — 帧辅助

- `frame_preprocess()`：复刻 `soup3D.__init__.frame_render` 的光源与 EAU 段（顶部注释标注来源与上游对齐要求）：
  ```python
  if soup3D.light.dirty:
      soup3D.light.set_surface_light()
  eau = soup3D.EAU + soup3D.light.EAU  # 语义与引擎一致：合并 → 逐项执行 → 双清空
  # 逐项调用；执行中长度变化抛异常（引擎同款防护）
  soup3D.EAU = []
  soup3D.light.EAU = []
  ```
- `pack_frame(renderer, draw_scene)`：`frame_preprocess()` + `renderer.render_frame(SceneDrawer(draw_scene))`。`SceneDrawer` 为 runtime 内包装类：无参可调用（转调 `draw_scene`），`.faces` 属性经 `draw_scene.__self__` 反射当前 screen 的 `_model`（`getattr(screen, "_model", None)` → `model.faces` 或空列表，映射为 `(model, face)` 对列表）。
- 契约：预设路径不渲染引擎 `stable_shapes`/`render_queue`（本游戏从不使用 `show()/paint()`，两队列恒空；`docs/shader_packs.md` 注明此约定）。

### 5.4 api.py — 包作者便利 API

- `frame_preprocess()`（= runtime 同名函数再导出）：游戏 runtime 每帧自动调用（`pack_frame` 内）；导出仅为文档完整性与高级用法（包完全自管整帧、绕开 `pack_frame` 时需自调）。
- `FullScreenPass`：全屏三角形通道辅助。惰性创建空 VAO，`draw(program)` 用 `gl_VertexID` 生成全屏三角（与 `soup3D.target._ensure_present_program` 同模式），包作者写 SSR/合成通道直接用，不必自管顶点缓冲。
- `SurfaceShaderBase = soup3D.shader.AutoSP` 别名 + 文档：包作者继承后重写 `create_shader_program()` 返回自定义 GLSL 的 `ShaderProgram`，即可获得 `rend_cached` 拆分/VAO 缓存与 `make_surface` 参数内省（引擎 `BoneBinderSP` 即此模式的先例）；需补充 uniform 时重写 `update()`（先 `super().update()`）。
- `SKIN_SURFACE_HINT`：蒙皮类继承 `soup3D.shader.BoneBinderSP` 的文档说明（一期不实现示例）。

### 5.5 内置性能优先包（builtin/performance）

- `pack.py`：`surface_class = soup3D.shader.AutoSP`。无 `create_frame_renderer`。
- 效果：与当前画面逐像素一致；不设帧渲染器 → 主循环走默认管线分支，零行为变化。

### 5.6 内置画面优先包（builtin/quality）

**PBR 表面类 `PBRSurface(AutoSP)`**：

- 顶点着色器：与 AutoSP 相同（`FragPos`/`Normal`/`TexCoord` 输出），另输出 `lightSpacePos = lightSpaceMat * vec4(FragPos, 1)`。
- 片段着色器：
  - 纹理单元沿用引擎约定：`baseColor`=0、`normal`=1、`emission`=3；`shadowMap`=2（空闲单元，避开冲突）。
  - 材质 uniform：`roughness`、`metallic`（每材质 float；`make_surface` 从 MTL `Pr`/`Pm` 供给，缺省 0.8 / 0.0）。
  - 光照循环结构体 `Light lights[]` 与 AutoSP 完全同布局（点光锥角衰减 + 方向光分支照抄，保证引擎灯光上传逻辑零改动适配）。
  - Cook-Torrance GGX：`D = a²/(π((n·h)²(a²-1)+1)²)`，G = Smith–Schlick，F = Schlick(f0 = mix(0.04, albedo, metallic))；漫反射 Lambert `(1-metallic)·albedo/π`。
  - 阴影：`lightSpacePos` 投影 → UV/depth；bias = `max(0.05·(1+n·l), 0.005)`；3×3 PCF（9 taps）。
  - 环境光项保持引擎 `ambient` 联动：`ambient·albedo + 0.04·f0·ambient` 近似环境高光。
- 每材质实例一个 `PBRSurface`（继承 AutoSP 模式，`make_surface` 内省接收 `roughness`/`metallic`，文件未提供时用类默认 0.8/0.0）；`update()` 重写：`super().update()` 后补传阴影相关 uniform（`lightSpaceMat`、`shadowMap` 单元号 2、`shadowEnabled`）。下发机制：pack.py 模块级单例 `registry`——`PBRSurface.__init__` 自登记，`QualityFrameRenderer` 每帧（或初始化时）向登记表写入 `lightSpaceMat`（按太阳方向计算）、`shadowMap` 采样句柄与开关；`update()` 检测到矩阵变化才补传 uniform。
- 深度表面类 `DepthSurface(AutoSP)`（阴影通道用）：

- 顶点：`gl_Position = lightMat * vec4(VertPos, 1)`；片段：采样 `baseColor`（单元 0）`alpha < 0.5` discard（避免透明材质整面投影），不写颜色。
- 渲染器按 Face 对象弱引用缓存 face → `DepthSurface`（`weakref.WeakKeyDictionary`，face 释放自动清理，规避 id 复用风险），复用 `rend_cached` 拆分/VAO 缓存；每帧按模型分组 `set_model_mat` 同步模型矩阵。

**帧渲染器 `QualityFrameRenderer`**：

- 资源（构造时创建）：阴影 RT（`2048×2048`，`depth="texture"`）；HDR RT（窗口尺寸，`rgba16f` + `depth="texture"`）；`FullScreenPass` ×2（SSR、合成）；合成/SSR 着色器程序；包内常量集中文件顶部（阴影范围、PCF taps、SSR 步数、曝光）。
- `render_frame(draw_scene)` 流程：
  1. `frame_preprocess()`。
  2. 收集太阳：遍历 `soup3D.light.light_queue` 取 on 状态 `Direct`（无 → 跳过阴影通道，shader 走无阴影分支 uniform `shadowEnabled=0`）。光源正交矩阵：eye = 场景中心 − 来光方向×距离，`glm.lookAt` + `glm.ortho`；场景中心/范围用包常量（默认中心 (0,0,0)、半宽 80、深度 0.1~300；场景更大时改常量）。
  3. 阴影通道：绑定阴影 RT → clear → 以 `draw_scene.faces`（(model, face) 对列表）为准，同模型 faces 连续处理：先 `set_model_mat(model.get_model_mat())`，再逐 face 用 `DepthSurface.rend_cached` 直绘；`DepthSurface` 按 Face 对象弱引用缓存（`weakref.WeakKeyDictionary`，face 释放自动清理，规避 id 复用风险）。
  4. 主通道：绑定 HDR RT → 清屏（天空色 (0.55, 0.75, 0.95)，与 `soup3D.init` 一致）→ `draw_scene()`（PBR 表面画进 HDR RT）。
  5. SSR 通道：全屏三角形；输入 HDR 颜色（单元 0）+ HDR 深度（单元 1）+ 逆投影矩阵；深度重建视图空间位置，法线 = `normalize(cross(dFdx, dFdy))`；反射方向屏幕空间光线步进（默认 24 步 + 命中后 4 次二分细化，常量可调）；屏幕边缘与步数耗尽淡出；菲涅尔加权混合；天空像素（深度=1）跳过。
  6. 合成通道：全屏三角形直绘默认帧缓冲；HDR 颜色 → 曝光 → ACES 近似（Narkowicz 拟合）→ gamma 2.2。
- `dispose()`：删全部 RT 与程序。

### 5.7 App 集成

- `Settings` 增字段：`shader_preset: str = "builtin_performance"`（additive 字段，旧档缺失自动补默认，不升 schema，与 `fps_mode`/`tpa_percent` 先例一致）。
- `App.__init__`：`self.packs = list_packs()`；`self.preset = None`；`self._frame_renderer = None`；`self.preset_fallback = False`；`self._pending_preset_reload = False`。`soup3D.init` 之后（`__init__` 中 `_open_window()` 调用之后）调用一次 `reload_shader_preset()`；`_open_window` 自身不调（避免与 rebuild 路径双重调用）。
- `App.reload_shader_preset()`：
  1. 旧渲染器 `dispose()`（若实现）。
  2. 按 `settings.shader_preset` 在 `self.packs` 找 valid 包；找不到/invalid → 回落 `builtin_performance` 并置 `preset_fallback = True`。
  3. `loader.load_pack()` 失败 → 同样回落。
  4. 有 `create_frame_renderer` → 以当前 `soup3D.proj_width/height` 实例化；实例化抛异常 → 回落性能优先并置 fallback 标记。
  5. `self.preset = LoadedPack`；`self._frame_renderer = renderer or None`。
  - 整个方法体 try/except 兜底：任何未预期异常 → 回落性能优先（渲染层永不阻断游戏可用性）。
- `rebuild_window()`：在 `_open_window()`（新 GL 上下文）之后、`self.screen.reload()` 之前调用 `reload_shader_preset()`——保证场景以新包表面类重载；失败路径（`restart_needed=True`）同样调用（新上下文已存在而旧 preset 资源属旧上下文，必须重建；失败再回落性能优先）。
- 主循环 `run()` 渲染段改为：
  ```python
  if self._frame_renderer is not None:
      fr = self._frame_renderer
      soup3D.update(render=lambda: runtime.pack_frame(fr, self.screen.render_scene))
  else:
      soup3D.update()
      self.screen.render_scene()
  ```
- 运行期兜底：`run()` 中渲染分支 try/except——`pack_frame` 抛异常时置 `self._frame_renderer = None`（当帧起回落默认管线，当帧画面残缺可接受）+ `preset_fallback = True` + 控制台输出异常栈；不中断游戏。
- `_pending_preset_reload` 标记：设置保存涉及画质变更时置位，`run()` 在帧边界执行 `reload_shader_preset()`（与 `_pending_rebuild` 同机制；GL 资源创建不在 imgui 帧中做）。`rebuild_window` 已含 preset 重载，两者同时置位时只走 rebuild（rebuild 尾部已 reload）。

### 5.8 settings.py — 画质预设下拉

- 下拉「画质预设」：候选项 = valid 包列表（内置在前），label = `name`；value 存 id。
- 工作副本 `_preset_id`；非法存量值在 enter 时即回落显示（与 fps_mode 先例一致）。
- 当前 id 不在 valid 列表 → 显示回落提示文字。
- invalid 文件夹包在页面下方文字行列出（`<name>（无效：<error>）已跳过`，镜像 flight 场景选择）。
- 「保存并应用」：写 `settings.shader_preset` + 存盘 + `_pending_preset_reload = True`（若 id 有变化）。生效时机 = 下次进飞行（设置页只在主菜单可达，天然满足；场景在进入时以当时 surface_class 加载）。

### 5.9 flight.py — 场景加载注入

`_load_scene` 一行改动：

```python
self._model = soup3D.open_obj(str(obj), str(mtl) if mtl.exists() else None,
                              surface=self.app.preset.surface_class)
```

`app.preset` 保证非 None（App 启动即回落加载性能优先）。其余（灯光、ambient、物理）不动。

## 6. 错误处理汇总

| 场景 | 处理 |
| --- | --- |
| pack.json 非法/缺失 | 包标记 invalid，设置页文字行提示，不可选 |
| pack.py import 失败/缺契约 | 同上（load 阶段） |
| 选中包 id 失效（删包/改名） | 回落 `builtin_performance` + 设置页回落提示 |
| `create_frame_renderer` 实例化失败 | 回落性能优先 + 提示 |
| render_frame 运行期异常 | 当帧起回落默认管线 + 提示 + 打印异常（不闪退） |
| 窗口重建（分辨率/全屏） | rebuild 尾部 reload_shader_preset；失败继续回落链 |
| GL 上下文销毁 | dispose 契约 + GC/`__del__` 兜底（引擎 Texture/RenderTarget 同模式） |
| 下载第三方包执行任意代码 | 文档明示信任模型（模组惯例），不做沙箱 |

## 7. 性能预算（验收标尺）

| 路径 | 预算（1080p，用户离散 GPU） |
| --- | --- |
| 性能优先 | 与现状逐像素一致；帧耗增量 ≤ 1ms（基本为零） |
| 画面优先·阴影通道（2048²，单太阳） | ≤ 2ms |
| 画面优先·主通道（PBR 增量） | ≤ 2ms |
| 画面优先·SSR（全分辨率 ≤32 步） | ≤ 4ms；超预算启用半分辨率常量 |
| 画面优先·合成 | ≤ 1ms |
| 总体目标 | 60fps（垂直同步下不掉帧）；bando_factory 实测调优 |

llvmpipe（无 GPU 环境）下画面优先会显著掉帧：文档注明，不阻塞。

## 8. 测试策略

单元测试（无 GL，进 test/，pytest）：

- `test/test_shader_packs.py`：
  - registry：内置两包被发现且 valid；临时文件夹包发现/排序；pack.json 非法 → invalid + error；`builtin_` 前缀目录 invalid。
  - loader：合法假包（tmp 目录写 pack.json + pack.py）加载出 surface_class；缺 `surface_class` → invalid；import 抛异常 → invalid；重复加载不残留旧模块。
  - `Settings.shader_preset` additive roundtrip（旧档 JSON 无该键 → 加载补默认；保存含该键）。
  - 回落选择逻辑（构造 packs 列表 + settings，选失效 id → 回落 builtin_performance）。
  - `runtime.frame_preprocess`：monkeypatch `soup3D.light`（dirty=True 调 set_surface_light 并清 dirty；EAU 队列执行与双清空；EAU 执行中变长抛异常）。
  - `SceneDrawer` 包装：`.faces` 反射（fake screen 带/不带 `_model`）。

GL 手动测试（test/manual/，需显示环境）：

- `test/manual/smoke_shader_quality.py`：创建窗口 → 直接实例化 QualityFrameRenderer + 代码生成测试场景（地面平面 + 立方体 + 太阳 Direct）→ 渲一帧 → `RenderTarget.to_pixels()` 截图存 PNG；粗校验断言：物体阴影区域像素显著暗于受光区域、镜面地板区域出现非天空色反射。人工目检辅助。

冒烟与回归：

- `python main.py --frames N`（默认性能优先，行为与现状一致）。
- `python -m pytest test/ -v` 全绿（71 项 + 新增）。
- 画面优先视觉验收：设置切「画面优先」→ bando_factory 实飞目检（阴影/反射/色调映射）。

## 9. 文档交付

- `docs/shader_packs.md`（新增）：包作者指南——信任模型声明、目录结构、pack.json 字段、pack.py 契约、最小可行示例（换材质着色器）、进阶指引（帧渲染器/多通道；指路内置 quality 包源码作为参考实现）、`stable_shapes` 不渲染的契约说明。
- `README.md`：功能清单加着色器预设一段（指向上述文档）。
- `docs/acceptance-phase1.md` 不动；本功能验收标准见本设计第 7、8 节，随实现计划落地。

## 10. 非目标（YAGNI）

- SSAO、Bloom、MRT、IBL/环境贴图。
- 包内一键下载/市场（用户手动放 `shader_packs/`）。
- 点光源阴影（一期只有太阳方向光阴影）。
- GLTF 蒙皮场景的 PBR 实装与验证（`skinned_surface_class` 仅接口预留）。
- 沙箱化执行第三方包。
- 飞行中实时切换画质（设置仅主菜单可达，场景加载时生效）。

## 11. 风险与对策

| 风险 | 对策 |
| --- | --- |
| `runtime.frame_preprocess` 与上游 `frame_render` 逻辑漂移 | 顶部注释标注来源；上游同步时人工核对（VENDORED.md 流程不变） |
| PBR 表面纹理单元与引擎约定冲突 | 沿用 0/1/3，阴影图固定用 2；单元分配写入 api 文档 |
| Mesa 显示列表问题 | 预设路径全直渲（rend_cached），不触碰 `show()/glCallList` |
| 包作者误在 render_frame 里不清状态 | 引擎 `update(render=...)` 前后保存/恢复 GL 状态兜底；`FullScreenPass`/`RenderTarget` 封装归位状态 |
| 性能不达标 | SSR 半分辨率、步数、阴影尺寸均为包顶部常量，可快速调 |
