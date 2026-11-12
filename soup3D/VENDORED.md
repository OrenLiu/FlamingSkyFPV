# VENDORED — soup3D 引擎本地副本

- **来源**: `/home/oren/project/codes/python/#tools/soup3D/soup3D`（原仓库只读，未做任何改动）
- **上游版本**: 7.0.0（见原仓库 `setup.py`）
- **复制日期**: 2026-11-09
- **复制方式**: `cp -r` 整包复制；项目根运行 `python main.py` 时 cwd 优先于 site-packages，
  `import soup3D` 自动解析到本副本（`python -c "import soup3D; print(soup3D.__file__)"` 已验证）。

## 改造内容清单

仅改动 `shader.py`，新增两个方法与配套字段，**既有 API（含 `rend` / `AutoSP.rend`）一字未动**：

| 位置 | 改动 |
| --- | --- |
| `shader.py` 模块级 | 新增 `_INT_TYPE_MAP` 常量（整型 GL 类型 → numpy dtype，供 `rend_cached` 使用；`rend` 内原有局部映射保留不动） |
| `ShaderProgram.__init__` | 新增 `_cached_vao` / `_cached_vbo` / `_cached_vertex_count` 缓存字段初始化 |
| `ShaderProgram.rend_cached` | 新方法：与 `rend` 相同入参语义，首次调用创建 VAO/VBO 并缓存在 self，后续直接 bind + 绘制，不删除缓冲（静态顶点契约，见其 docstring） |
| `ShaderProgram.__del__` | 追加缓存 VAO/VBO 的删除（GL 上下文已释放时静默） |
| `AutoSP.__init__` | 新增 `_cached_split` / `_cached_split_src` 拆分缓存字段初始化 |
| `AutoSP.rend_cached` | 新方法：拆分逻辑与 `AutoSP.rend` 相同，拆分结果按入参 vertex 对象 id 缓存，之后调 `ShaderProgram.rend_cached` |

包内其他文件（`__init__.py`、`camera.py`、`light.py`、`scene.py`、`s3dm.py`、`skeleton.py`、
`target.py`、`ui.py`、`name.py`）复制即用，未改动。

## 改造动机

飞行循环剖析（2026-11-09，bando_factory，1600x900）：`render_scene` 段 42.9ms/帧（23fps），
其中 ① `AutoSP.rend` 每帧纯 Python 拆分 ~5 万顶点为三列表；② `ShaderProgram.rend`
每帧每面「新建 VAO/VBO → 上传 → 绘制 → 删除」。静态地图模型顶点数据不变，
逐帧重建是纯浪费。`rend_cached` 缓冲复用后实测 `render_scene` 段 0.3ms，
渲染结果与旧路径逐像素一致（`bench_rend.png` vs `bench_rend_cached.png`，`np.array_equal` 为 True；
截图存于本地 `.superpowers/sdd/2026-11-08-phase1/`，该目录被 gitignore）。

## 编码规则声明

本副本的改动遵循上游 README「Code Standards」六条：

1. 新增方法均为 `reStructuredText` 格式注释（含设计说明与契约）
2. 已测试（`test/test_vendored_soup3d.py` + `test/manual/bench_render.py`，本地 gitignore 不提交）
3. 需求已与作者（本项目维护者）确认后实施
4. 新特性不影响既有调用方式：只新增方法与字段，`rend` / `AutoSP.rend` 等既有路径一字未动
5. 未新增任何 import（文件头部 import 保持不变）
6. 无嵌套函数定义，新增均为类成员方法

## 上游同步

如需吸收上游更新：在原仓库查看变更后，将对应文件改动手工移植到本副本，
并在本文件追加改造记录；`shader.py` 以外的文件可直接整文件覆盖。

## GC 升代治理改造（2026-11-11）

飞行界面每 8.5s 周期性卡顿（gen2 GC 停顿 26ms）根治：消除每帧渲染路径的
Python 对象分配（升代燃料）。改动与上游同步实施：

| 位置 | 改动 |
| --- | --- |
| 模块级 | 新增 OpenGL.raw.GL.VERSION.GL_2_0 的 19 个 glUniform* raw 直调导入；type_map 的 uniform 上传函数全部换 raw 层（无包装层每调用分配） |
| ShaderProgram.update | uniform_loc 待上传队列改 clear() 复用（原每帧 `= {}` 重建 dict） |
| AutoSP.__init__ | 矩阵初始化后建持久 value_ptr（_model_ptr/_view_ptr/_proj_ptr） |
| AutoSP.set_model/view/projection_mat | 替换引用 → 原地拷贝（矩阵对象恒定，持久指针保持有效；pyglm 整行 vec4 赋值已验证） |
| AutoSP.update | 三处 glm.value_ptr() 调用换持久指针 |

效果（bando_factory 1080p unlimited，探针 45s）：>10ms 帧尖峰 5→0，
gen2 GC 45s 内 5 次×26ms → 2 次×≤12ms，帧率 436→464fps。
测试 145 项全绿。编码规则自查：仅第 5 条偏离（新增 raw import，
替换既有函数来源无法避免），其余全部符合。
