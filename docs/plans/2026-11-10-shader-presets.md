# FlamingSkyFPV 着色器预设系统实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement each task with TDD and per-task commits.

## Goal

按已确认的设计文档实现「着色器预设系统」：新增游戏侧 `shaderpacks/` 模块（registry / loader / runtime / api）+ 内置「性能优先」「画面优先」两包 + App / 设置页 / flight 接入。性能优先包与现状逐像素一致（零行为变化）；画面优先包 = PBR（Cook-Torrance GGX）+ 太阳方向光阴影贴图（3×3 PCF）+ SSR 屏幕空间反射 + HDR（rgba16f）+ ACES 色调映射。用户可通过在 `shader_packs/` 放入自制/下载的包（`pack.json` + `pack.py`）扩展画质，设置页下拉切换。

## Spec

本计划的唯一权威设计文档：`docs/specs/2026-11-10-shader-presets-design.md`。接口契约（pack.json 字段、pack.py 契约、包 id 约定、信任模型）以 spec 第 4 节为准；组件设计以 spec 第 5 节为准；错误处理以 spec 第 6 节为准；性能预算以 spec 第 7 节为准；测试策略以 spec 第 8 节为准。执行中如与 spec 冲突，以 spec 为准并停下确认。

## Architecture（数据流与模块边界）

```
app.run()
  └─ soup3D.update(render=lambda: shaderpacks.runtime.pack_frame(renderer, screen.render_scene))
       └─ pack_frame:
            1. frame_preprocess()        # 复刻引擎 frame_render 的光源/EAU 段（引擎零改动）
            2. renderer.render_frame(SceneDrawer(draw_scene))
                 a. 阴影通道：太阳正交矩阵 → 深度 RT（DepthSurface 逐 face 直绘）
                 b. 主通道：HDR RT 清屏 → draw_scene()（PBR 表面 rend_cached 进 RT）
                 c. 合成通道：SSR 反射 + 曝光 → ACES → gamma，全屏三角直绘默认帧缓冲
  └─ imgui UI（不变，画在合成结果之上）

场景加载：screens/flight.py _load_scene 一行注入 surface=app.preset.surface_class
性能优先包：surface_class = soup3D.shader.AutoSP，无 create_frame_renderer → 走默认管线分支
```

新增文件：

```
shaderpacks/
├── __init__.py        # 公共 API：list_packs() / PackInfo 再导出
├── registry.py        # 包发现与元信息校验（内置 + shader_packs/ 文件夹）
├── loader.py          # pack.py 动态加载与契约校验（LoadedPack / PackLoadError）
├── runtime.py         # 帧辅助：frame_preprocess() / SceneDrawer / pack_frame()
├── api.py             # 包作者便利 API：SurfaceShaderBase / FULLSCREEN_VS / FullScreenPass
└── builtin/
    ├── performance/   # 内置「性能优先」：pack.json + pack.py
    └── quality/       # 内置「画面优先」：pack.json + pack.py（Task 10-12 逐步完整）
```

修改文件：`config_store.py`（Settings 增字段）、`app.py`（预设生命周期与渲染分支）、`screens/settings.py`（画质预设下拉）、`screens/flight.py`（表面类注入一行）、`README.md`、`.gitignore`、新增 `docs/shader_packs.md`。

## Tech Stack

- Python 3（dataclass / importlib / weakref），pygame + OpenGL（PyOpenGL）窗口，soup3D 7.0.0（vendored 副本，**本计划零改动**）渲染引擎，pyglm（矩阵），imgui（UI），PyBullet（不涉及）。
- 测试：pytest（`python -m pytest test/ -v`，仓库根目录运行）；无 GL 单元测试 + 需真实 X 显示的 GL 像素测试（约定见 `test/test_vendored_soup3d.py`）。

## 任务依赖总览

Task 1（Settings 字段）→ Task 2（包骨架/内置包）→ Task 3（registry）→ Task 4（loader）→ Task 5（runtime）→ Task 6（api）→ Task 7（App 接入）→ Task 8（设置页）→ Task 9（flight 注入）→ Task 10（quality：阴影+HDR）→ Task 11（quality：PBRSurface）→ Task 12（quality：SSR+ACES 合成）→ Task 13（手动验收脚本）→ Task 14（文档）→ Task 15（最终回归）。

## Global Constraints（全部任务适用）

1. **全文简体中文**：代码注释、GLSL 注释、提交信息、文档均为中文。GLSL 内中文注释在本仓库有先例（`soup3D/shader.py` 的 AutoSP GLSL 中文注释在 Mesa 下编译通过），可放心使用。
2. **引擎零改动**：`soup3D/` 是 vendored 副本（上游同步契约见 `soup3D/VENDORED.md`），任何任务不得修改 `soup3D/` 下任何文件。需要引擎行为时在游戏侧复刻（`runtime.frame_preprocess` 顶部注释标注上游来源与对齐要求）。
3. **依赖方向**：`shaderpacks/` 只可 `import soup3D` 与标准库，**禁止** import screens / app / physics / input / scene_loader / config_store；`app.py` 与 `screens/` 可 import `shaderpacks`。
4. **TDD 顺序**：每任务先写失败测试 → 运行确认失败 → 最小实现 → 运行确认通过 → 提交。测试一律写在 `test/` 下（该目录被 `.gitignore` 忽略），**提交只 add 源码文件，绝不 add test/**。
5. **GL 资源生命周期**：禁止在 imgui 帧中间创建/销毁 GL 资源（沿用 `_pending_rebuild` 帧边界机制先例）；窗口重建（新 GL 上下文）后所有预设 GL 资源必须重建；GL 释放失败静默（引擎 `Texture.__del__` 同模式）。
6. **回落链（渲染层永不阻断游戏可用性）**：预设任何失败 → 回落 `builtin_performance` → 再失败 → 默认管线 + `preset_fallback = True` 提示，绝不抛出中断游戏。
7. **提交格式**：`git add <精确源码路径>` 后 `git commit -m $'类型: 中文标题\n\nCo-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>'`（`$'...'` 保证换行）。类型用 feat / docs。
8. **测试运行方式**：一律在仓库根目录 `python -m pytest test/ -v`（依赖 cwd 注入 repo 根到 sys.path）；GL 测试需要真实 X 显示环境（与现有 test_vendored_soup3d 同环境）。
9. **用户内容不进版本库**：`shader_packs/`（用户包目录，仓库根）将在 Task 14 加入 `.gitignore`；`shaderpacks/`（源码模块）进版本库。`scene/`、`config/` 保持忽略不动。
10. **计划外文件不动**：`docs/specs/`、`docs/plans/2026-11-10-shader-presets.md`（本计划文件，已提交）、`CLAUDE.md`、`docs/acceptance-phase1.md` 不修改、不提交。
11. **禁止占位**：不写 TBD / TODO / "略"；每个代码步骤给完整可运行代码块；禁止 `test.skip` / `.only` / 空桩。

## 前置检查（执行计划前一次）

- [ ] `cd /home/oren/project/codes/python/FlamingSkyFPV && git status --short --branch`：应在 master、改动区干净（仅 `?? docs/plans/2026-11-10-shader-presets.md` 未跟踪，属既有文件，不动）。
- [ ] `python -m pytest test/ -v`：全绿（71 项），作为回归基线。

---

## Task 1：Settings 新增 shader_preset 字段（additive，不升 schema）

### Files

- 修改：`/home/oren/project/codes/python/FlamingSkyFPV/config_store.py`
- 测试：`/home/oren/project/codes/python/FlamingSkyFPV/test/test_config_store.py`（追加两个测试）

### Interfaces

- Consumes：`config_store.Settings`（现有 dataclass，additive 字段先例 `fps_mode`）、`ConfigStore.load/save` 的 `_rebuild` 缺失键补默认机制。
- Produces：`Settings.shader_preset: str = "builtin_performance"`（包 id 存档字段；消费方为 Task 7 的 `App.reload_shader_preset`）。

### 背景

spec 5.7：`Settings` 增字段 `shader_preset: str = "builtin_performance"`。additive 字段，旧档 JSON 无该键时 `_rebuild` 自动补 dataclass 默认值，不升 schema（与 `fps_mode` / `tpa_percent` 先例一致）。失效 id 的回落逻辑在 Task 7 实现，本任务只管存档 round-trip。

### Steps

- [ ] 1. 在 `test/test_config_store.py` 末尾追加失败测试（紧跟现有 `test_settings_old_archive_missing_fps_mode_falls_back` 的写法风格）：

```python
def test_settings_shader_preset_round_trip(tmp_path):
    """shader_preset 自定义值（第三方包 id）round-trip 落盘不丢"""
    store = ConfigStore(str(tmp_path))
    s = Settings(shader_preset="my_movie_pack")
    store.save("settings", s)
    loaded = store.load("settings", Settings)
    assert loaded.shader_preset == "my_movie_pack"

def test_settings_old_archive_missing_shader_preset_falls_back(tmp_path):
    """旧档（无 shader_preset 键）加载补默认 builtin_performance，其余字段保留"""
    (tmp_path / "settings.json").write_text(
        json.dumps({"resolution": [1920, 1080], "fullscreen": True}),
        encoding="utf-8")
    loaded = ConfigStore(str(tmp_path)).load("settings", Settings)
    assert loaded.shader_preset == "builtin_performance"
    assert tuple(loaded.resolution) == (1920, 1080)  # 其余字段原样保留
    assert loaded.fullscreen is True
```

- [ ] 2. 运行确认失败（字段不存在，TypeError: unexpected keyword argument）：

```bash
cd /home/oren/project/codes/python/FlamingSkyFPV && python -m pytest test/test_config_store.py -k shader_preset -v
```

- [ ] 3. 最小实现：修改 `config_store.py` 的 `Settings`，在 `fps_mode` 字段后追加（保持注释风格）：

```python
    # 着色器预设包 id（shaderpacks.registry 的 PackInfo.id，内置为 builtin_performance /
    # builtin_quality，文件夹包为目录名）。additive 字段：旧档缺失键加载时补默认
    # "builtin_performance"，不升 schema（fps_mode 先例）。失效 id 由 App.
    # reload_shader_preset 回落 builtin_performance（spec：着色器预设系统设计 5.7）
    shader_preset: str = "builtin_performance"
```

- [ ] 4. 运行确认通过：

```bash
cd /home/oren/project/codes/python/FlamingSkyFPV && python -m pytest test/test_config_store.py -v
```

- [ ] 5. 提交（只含源码，test/ 不入提交）：

```bash
cd /home/oren/project/codes/python/FlamingSkyFPV && git add config_store.py && git commit -m $'feat: 设置新增 shader_preset 字段（additive 不升 schema）\n\nCo-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>'
```
## Task 2：shaderpacks 模块骨架与内置两包（元信息 + 性能优先包 + quality 基线）

### Files

- 新增：`/home/oren/project/codes/python/FlamingSkyFPV/shaderpacks/__init__.py`
- 新增：`/home/oren/project/codes/python/FlamingSkyFPV/shaderpacks/builtin/performance/pack.json`
- 新增：`/home/oren/project/codes/python/FlamingSkyFPV/shaderpacks/builtin/performance/pack.py`
- 新增：`/home/oren/project/codes/python/FlamingSkyFPV/shaderpacks/builtin/quality/pack.json`
- 新增：`/home/oren/project/codes/python/FlamingSkyFPV/shaderpacks/builtin/quality/pack.py`

### Interfaces

- Consumes：`soup3D.shader.AutoSP`（open_obj 的默认表面类）。
- Produces：内置两包的 `pack.json`（元信息，registry 校验对象）与 `pack.py`（`surface_class` 模块级属性；性能优先包不提供 `create_frame_renderer`）。

### 背景

spec 4.1/4.2/5.5：包 = 目录（`pack.json` 元信息 + `pack.py` 代码）。内置包位于 `shaderpacks/builtin/<名>/`（id = `builtin_` + 目录名）。性能优先包 `surface_class = soup3D.shader.AutoSP`、无帧渲染器 → 主循环走默认管线分支（零行为变化）。quality 包本任务先落基线文件（`pack.json` + 最小 `pack.py`），其完整实现由 Task 10-12 重写扩充——registry 校验只读 `pack.json`，所以基线阶段 quality 已可被发现且 valid。

### Steps

- [ ] 1. 创建 `shaderpacks/__init__.py`（先只放包文档；registry 在 Task 3 才存在，本任务不得提前 import）：

```python
"""着色器预设包系统（设计：docs/specs/2026-11-10-shader-presets-design.md）。

包格式：文件夹 = pack.json（元信息）+ pack.py（代码）。用户包放仓库根 shader_packs/。
"""
```

- [ ] 2. 创建 `shaderpacks/builtin/performance/pack.json`：

```json
{
  "name": "性能优先",
  "description": "默认渲染管线（AutoSP 表面），与出厂画面一致，性能最好",
  "author": "FlamingSkyFPV",
  "version": "1.0"
}
```

- [ ] 3. 创建 `shaderpacks/builtin/performance/pack.py`：

```python
"""内置「性能优先」预设：默认管线 + AutoSP（spec 5.5），与当前画面逐像素一致。

不提供 create_frame_renderer：主循环走默认管线分支，零行为变化。
"""
import soup3D

# 表面着色器类：open_obj 的 surface 参数默认值本就是 AutoSP，注入等价于现状
surface_class = soup3D.shader.AutoSP
```

- [ ] 4. 创建 `shaderpacks/builtin/quality/pack.json`：

```json
{
  "name": "画面优先",
  "description": "PBR 材质 + 太阳软阴影 + SSR 反射 + HDR/ACES 色调映射",
  "author": "FlamingSkyFPV",
  "version": "1.0"
}
```

- [ ] 5. 创建 `shaderpacks/builtin/quality/pack.py`（基线：仅表面类，registry 校验只读 pack.json，故本阶段 quality 已可被发现且 valid）：

```python
"""内置「画面优先」预设：目标形态为 PBR（Cook-Torrance GGX）+ 太阳方向光阴影贴图
（3×3 PCF）+ SSR 屏幕空间反射 + HDR（rgba16f）+ ACES 色调映射（spec 5.6 参考实现）。

本文件当前仅含表面类（暂与默认管线一致）；完整帧渲染路径由本文件的后续提交引入。
"""
import soup3D

# 基线阶段表面类与性能优先一致；PBRSurface/DepthSurface/QualityFrameRenderer 随
# quality 包实现逐步落入本文件
surface_class = soup3D.shader.AutoSP
```

- [ ] 6. 验证两包代码可导入且暴露 surface_class（无 GL 需求，模块级只做 import 与类引用）：

```bash
cd /home/oren/project/codes/python/FlamingSkyFPV && python -c "
import shaderpacks.builtin.performance.pack as p
import shaderpacks.builtin.quality.pack as q
assert callable(p.surface_class) and callable(q.surface_class)
print('内置两包导入 OK')
"
```

- [ ] 7. 提交：

```bash
cd /home/oren/project/codes/python/FlamingSkyFPV && git add shaderpacks/ && git commit -m $'feat: shaderpacks 模块骨架与内置性能优先包\n\nCo-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>'
```

---

## Task 3：registry.py——包发现与元信息校验（TDD）

### Files

- 新增：`/home/oren/project/codes/python/FlamingSkyFPV/shaderpacks/registry.py`
- 修改：`/home/oren/project/codes/python/FlamingSkyFPV/shaderpacks/__init__.py`（再导出）
- 测试：`/home/oren/project/codes/python/FlamingSkyFPV/test/test_shader_packs.py`（新建）

### Interfaces

- Consumes：Task 2 的 `shaderpacks/builtin/*/pack.json`；标准库 json / pathlib / dataclasses。
- Produces：
  - `PackInfo` dataclass：字段 `id: str`、`dir: Path`、`name: str`、`description: str`、`author: str`、`version: str`、`builtin: bool`、`valid: bool`、`error: str`。
  - `list_packs(packs_root: str = "shader_packs") -> list[PackInfo]`：内置在前（performance、quality 顺序），文件夹包按目录名排序。
  - `shaderpacks/__init__.py` 再导出 `PackInfo`、`list_packs`（App 与设置页经 `from shaderpacks import list_packs` 使用）。

### 背景

spec 5.1：非法包标记 invalid 而非剔除（镜像 `scene_loader` 哲学），`error` 记原因；设置页把 invalid 文件夹包列为文字行、不进下拉。校验只读 `pack.json`（JSON 可解析、`name` 为非空字符串、文件夹包目录名 `builtin_` 前缀 → invalid 保留内置前缀）；`pack.py` 的代码级校验归 Task 4 的 loader。

### Steps

- [ ] 1. 新建 `test/test_shader_packs.py`，写入以下内容（registry 失败测试；helper 供 Task 4/7 复用）：

```python
# test/test_shader_packs.py
"""着色器预设系统单元测试（无 GL）：registry / loader / runtime / 契约与回落逻辑。

运行方式：仓库根目录 python -m pytest test/test_shader_packs.py -v（依赖 cwd 注入
repo 根到 sys.path）。GL 行为测试在 test_shader_quality_gl.py，手动验收脚本在
test/manual/smoke_shader_quality.py。
"""
import json

import soup3D  # noqa: F401  runtime/契约测试需要引擎模块；本文件全部测试不触 GL
import pytest

from shaderpacks.registry import PackInfo, list_packs


def _write_pack(tmp_path, dir_name, meta=None, pack_py=None):
    """合成用户包目录：meta=None 用默认元信息；pack_py=None 不写 pack.py"""
    d = tmp_path / dir_name
    d.mkdir()
    if meta is None:
        meta = {"name": dir_name + " 显示名"}
    (d / "pack.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    if pack_py is not None:
        (d / "pack.py").write_text(pack_py, encoding="utf-8")
    return d


def test_builtin_packs_discovered_in_order():
    """内置两包被发现且 valid；顺序 performance、quality 在前"""
    packs = list_packs()
    assert [p.id for p in packs[:2]] == ["builtin_performance", "builtin_quality"]
    perf, qual = packs[0], packs[1]
    assert perf.valid and qual.valid
    assert perf.builtin and qual.builtin
    assert perf.name == "性能优先"
    assert qual.name == "画面优先"


def test_folder_packs_sorted_after_builtin(tmp_path):
    """文件夹包按目录名排序，排在内置包之后"""
    _write_pack(tmp_path, "zz_pack")
    _write_pack(tmp_path, "aa_pack")
    packs = list_packs(str(tmp_path))
    assert [p.id for p in packs] == ["builtin_performance", "builtin_quality",
                                     "aa_pack", "zz_pack"]
    assert all(p.valid for p in packs[2:])
    assert not packs[2].builtin


def test_folder_pack_meta_fields(tmp_path):
    """description/author/version 从 pack.json 读取，缺省为空串"""
    _write_pack(tmp_path, "full", meta={"name": "完整包", "description": "描述",
                                        "author": "某人", "version": "2.3"})
    p = next(p for p in list_packs(str(tmp_path)) if p.id == "full")
    assert (p.name, p.description, p.author, p.version) == \
        ("完整包", "描述", "某人", "2.3")


def test_pack_json_bad_json_invalid(tmp_path):
    """pack.json 解析失败 → invalid + error"""
    d = tmp_path / "bad_json"
    d.mkdir()
    (d / "pack.json").write_text("不是 JSON", encoding="utf-8")
    p = next(p for p in list_packs(str(tmp_path)) if p.id == "bad_json")
    assert not p.valid and p.error


def test_pack_json_missing_name_invalid(tmp_path):
    """缺 name / 空 name / 非 name 字符串 → invalid + error"""
    d1 = tmp_path / "no_name"
    d1.mkdir()
    (d1 / "pack.json").write_text("{}", encoding="utf-8")
    d2 = tmp_path / "empty_name"
    d2.mkdir()
    (d2 / "pack.json").write_text(json.dumps({"name": ""}), encoding="utf-8")
    d3 = tmp_path / "num_name"
    d3.mkdir()
    (d3 / "pack.json").write_text(json.dumps({"name": 123}), encoding="utf-8")
    for pid in ("no_name", "empty_name", "num_name"):
        p = next(p for p in list_packs(str(tmp_path)) if p.id == pid)
        assert not p.valid and p.error


def test_pack_json_top_level_not_dict_invalid(tmp_path):
    """pack.json 顶层非对象 → invalid"""
    d = tmp_path / "top_list"
    d.mkdir()
    (d / "pack.json").write_text("[1, 2]", encoding="utf-8")
    p = next(p for p in list_packs(str(tmp_path)) if p.id == "top_list")
    assert not p.valid and p.error


def test_builtin_prefix_folder_invalid(tmp_path):
    """文件夹目录名以 builtin_ 开头 → invalid（保留前缀，避免与内置 id 冲突）"""
    _write_pack(tmp_path, "builtin_evil")
    p = next(p for p in list_packs(str(tmp_path)) if p.id == "builtin_evil")
    assert not p.valid and "builtin_" in p.error


def test_missing_pack_json_invalid(tmp_path):
    """目录无 pack.json → invalid"""
    d = tmp_path / "no_meta"
    d.mkdir()
    (d / "pack.py").write_text("surface_class = None\n", encoding="utf-8")
    p = next(p for p in list_packs(str(tmp_path)) if p.id == "no_meta")
    assert not p.valid and "pack.json" in p.error
```

- [ ] 2. 运行确认失败（ModuleNotFoundError: No module named 'shaderpacks.registry'）：

```bash
cd /home/oren/project/codes/python/FlamingSkyFPV && python -m pytest test/test_shader_packs.py -v
```

- [ ] 3. 实现 `shaderpacks/registry.py`：

```python
"""包发现与元信息校验（spec 5.1）：枚举内置包与用户文件夹包。

非法包标记 invalid 而非剔除（镜像 scene_loader 哲学），error 记原因；
文件夹无效包由设置页以文字行展示、不进下拉。校验只读 pack.json；
pack.py 的代码级校验在 loader.load_pack。
"""
import dataclasses
import json
from pathlib import Path

# 内置包根目录（shaderpacks/builtin/<目录名>/pack.json）
_BUILTIN_ROOT = Path(__file__).resolve().parent / "builtin"

# 内置包展示顺序（performance、quality 在前；新增内置包不在表内时按目录名排后）
_BUILTIN_ORDER = {"performance": 0, "quality": 1}


@dataclasses.dataclass
class PackInfo:
    """包元信息。invalid 包保留在列表中，error 记原因。"""
    id: str
    dir: Path
    name: str
    description: str = ""
    author: str = ""
    version: str = ""
    builtin: bool = False
    valid: bool = True
    error: str = ""


def list_packs(packs_root: str = "shader_packs") -> list:
    """枚举全部预设包：内置在前（performance、quality），文件夹包按目录名排序。

    :param packs_root: 用户包根目录（仓库根 shader_packs/，与 scene/ 平行）
    :return: list[PackInfo]，含 valid 与 invalid 条目
    """
    packs = []
    # 1. 内置包：shaderpacks/builtin/<目录名>/pack.json，id = "builtin_" + 目录名
    builtin_dirs = sorted((d for d in _BUILTIN_ROOT.iterdir() if d.is_dir()),
                          key=lambda d: (_BUILTIN_ORDER.get(d.name, 2), d.name))
    for d in builtin_dirs:
        packs.append(_make_pack_info(d, pack_id="builtin_" + d.name, builtin=True))
    # 2. 用户文件夹包：<packs_root>/<目录名>/，id = 目录名；根目录不存在则无此类
    root = Path(packs_root)
    if root.is_dir():
        folder_dirs = sorted((x for x in root.iterdir() if x.is_dir()),
                             key=lambda x: x.name)
        for d in folder_dirs:
            packs.append(_make_pack_info(d, pack_id=d.name, builtin=False))
    return packs


def _make_pack_info(d: Path, pack_id: str, builtin: bool) -> PackInfo:
    """读 pack.json 构造 PackInfo；非法包标记 invalid（error 记原因），不剔除。"""
    info = PackInfo(id=pack_id, dir=d, name=d.name, builtin=builtin)
    meta_path = d / "pack.json"
    if not meta_path.is_file():
        info.valid = False
        info.error = "缺少 pack.json"
        return info
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as e:
        info.valid = False
        info.error = f"pack.json 解析失败：{e}"
        return info
    if not isinstance(meta, dict):
        info.valid = False
        info.error = "pack.json 顶层必须是对象"
        return info
    name = meta.get("name")
    if not isinstance(name, str) or not name:
        info.valid = False
        info.error = "name 必须是非空字符串"
        return info
    # 文件夹包目录名以 builtin_ 开头 → invalid（内置保留前缀，防 id 冲突）。
    # 内置包不受此限制（id 本就含 builtin_ 前缀）
    if not builtin and pack_id.startswith("builtin_"):
        info.valid = False
        info.error = "目录名不可用 builtin_ 前缀（内置包保留前缀）"
        return info
    info.name = name
    info.description = meta.get("description") if isinstance(meta.get("description"), str) else ""
    info.author = meta.get("author") if isinstance(meta.get("author"), str) else ""
    info.version = meta.get("version") if isinstance(meta.get("version"), str) else ""
    return info
```

- [ ] 4. 更新 `shaderpacks/__init__.py`（追加再导出；文档头保持）：

```python
"""着色器预设包系统（设计：docs/specs/2026-11-10-shader-presets-design.md）。

包格式：文件夹 = pack.json（元信息）+ pack.py（代码）。用户包放仓库根 shader_packs/。
"""
from .registry import PackInfo, list_packs

__all__ = ["PackInfo", "list_packs"]
```

- [ ] 5. 运行确认通过：

```bash
cd /home/oren/project/codes/python/FlamingSkyFPV && python -m pytest test/test_shader_packs.py -v
```

- [ ] 6. 提交：

```bash
cd /home/oren/project/codes/python/FlamingSkyFPV && git add shaderpacks/__init__.py shaderpacks/registry.py && git commit -m $'feat: 着色器预设注册表（发现与元信息校验）\n\nCo-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>'
```
## Task 4：loader.py——pack.py 动态加载与契约校验（TDD）

### Files

- 新增：`/home/oren/project/codes/python/FlamingSkyFPV/shaderpacks/loader.py`
- 测试：`/home/oren/project/codes/python/FlamingSkyFPV/test/test_shader_packs.py`（追加 loader 测试段）

### Interfaces

- Consumes：Task 3 的 `PackInfo`（`info.dir` 定位 pack.py）；标准库 importlib / sys / dataclasses / types。
- Produces：
  - `PackLoadError(Exception)`：pack.py 加载/契约失败异常（App 回落链与设置页提示的消费对象）。
  - `LoadedPack` dataclass：`info: PackInfo`、`surface_class`、`skinned_surface_class`（缺省 = surface_class）、`create_frame_renderer`（可调用对象或 None）、`raw_module`。
  - `load_pack(info: PackInfo) -> LoadedPack`。

### 背景

spec 5.2：`importlib.util.spec_from_file_location` 加载 pack.py（模块名含包 id 防冲突）；校验 `surface_class` 可调用、`create_frame_renderer`（若有）可调用；异常捕获转 PackLoadError。`skinned_surface_class` 缺省 = surface_class（spec 4.3，一期游戏无蒙皮场景，接口预留）。不缓存模块对象——同一 id 重复加载先从 sys.modules 弹出旧条目再加载（覆盖语义，避免旧实例残留）。信任模型（spec 4.5）：运行下载的 pack.py = 执行任意第三方代码，不做沙箱。

### Steps

- [ ] 1. 在 `test/test_shader_packs.py` 顶部 import 区追加 `sys`，并在文件末尾追加 loader 失败测试（import 行追加 `from shaderpacks.loader import LoadedPack, PackLoadError, load_pack`）：

```python
# ---- loader（Task 4）----

# 假包代码：声明接收 base_color/double_side/max_light_count 的表面类（供 make_surface 内省）
FAKE_PACK_PY = '''
class FakeSurface:
    def __init__(self, base_color, double_side=True, max_light_count=8):
        self.base_color = base_color
'''


def _find_info(tmp_path, pid):
    """从 list_packs 结果里按 id 取 PackInfo"""
    return next(p for p in list_packs(str(tmp_path)) if p.id == pid)


def test_load_pack_valid(tmp_path):
    """合法假包：surface_class / skinned 缺省 / 无帧渲染器"""
    _write_pack(tmp_path, "good", pack_py=FAKE_PACK_PY)
    loaded = load_pack(_find_info(tmp_path, "good"))
    assert loaded.surface_class.__name__ == "FakeSurface"
    assert loaded.skinned_surface_class is loaded.surface_class
    assert loaded.create_frame_renderer is None
    assert loaded.raw_module is not None


def test_load_pack_with_skinned(tmp_path):
    """声明 skinned_surface_class 的包：LoadedPack 采用包内值"""
    pack_py = FAKE_PACK_PY + '''

class FakeSkin:
    def __init__(self, base_color):
        self.base_color = base_color

skinned_surface_class = FakeSkin
'''
    _write_pack(tmp_path, "with_skin", pack_py=pack_py)
    loaded = load_pack(_find_info(tmp_path, "with_skin"))
    assert loaded.skinned_surface_class.__name__ == "FakeSkin"
    assert loaded.surface_class.__name__ == "FakeSurface"


def test_load_pack_missing_surface_class(tmp_path):
    """缺 surface_class（或非可调用）→ PackLoadError"""
    _write_pack(tmp_path, "no_surface", pack_py="other = 1\n")
    with pytest.raises(PackLoadError):
        load_pack(_find_info(tmp_path, "no_surface"))


def test_load_pack_import_error(tmp_path):
    """pack.py import 抛异常 → PackLoadError，且 sys.modules 不留残骸"""
    _write_pack(tmp_path, "boom", pack_py="raise RuntimeError('导入即炸')\n")
    with pytest.raises(PackLoadError):
        load_pack(_find_info(tmp_path, "boom"))
    leftover = [k for k in sys.modules if k.startswith("shaderpacks.pack_code.")]
    assert leftover == []


def test_load_pack_frame_renderer_not_callable(tmp_path):
    """create_frame_renderer 存在但非可调用 → PackLoadError"""
    _write_pack(tmp_path, "bad_cfr",
                pack_py=FAKE_PACK_PY + "\ncreate_frame_renderer = 42\n")
    with pytest.raises(PackLoadError):
        load_pack(_find_info(tmp_path, "bad_cfr"))


def test_load_pack_repeated_load_no_stale_module(tmp_path):
    """重复加载同一 id：旧模块不残留（sys.modules 覆盖语义）"""
    d = _write_pack(tmp_path, "reload", pack_py="MARK = 1\n" + FAKE_PACK_PY)
    first = load_pack(_find_info(tmp_path, "reload"))
    (d / "pack.py").write_text("MARK = 2\n" + FAKE_PACK_PY, encoding="utf-8")
    second = load_pack(_find_info(tmp_path, "reload"))
    assert second.raw_module is not first.raw_module
    assert second.raw_module.MARK == 2
    names = [k for k in sys.modules if k.startswith("shaderpacks.pack_code.")]
    assert names == ["shaderpacks.pack_code.reload"]
```

- [ ] 2. 运行确认失败（ModuleNotFoundError: No module named 'shaderpacks.loader'）：

```bash
cd /home/oren/project/codes/python/FlamingSkyFPV && python -m pytest test/test_shader_packs.py -v
```

- [ ] 3. 实现 `shaderpacks/loader.py`：

```python
"""pack.py 动态加载与接口契约校验（spec 5.2）。

信任模型（spec 4.5）：运行下载的 pack.py = 执行任意第三方代码（可访问文件系统
与网络），与 Minecraft 模组同等待遇——只安装来源可信的包；不做沙箱。
不缓存模块对象：同一 id 重复加载先从 sys.modules 弹出旧条目再加载（覆盖语义），
避免旧实例残留。
"""
import dataclasses
import importlib.util
import sys
import types

from .registry import PackInfo


class PackLoadError(Exception):
    """pack.py 加载或契约校验失败（调用方回落/提示，不中断游戏）"""


@dataclasses.dataclass
class LoadedPack:
    """load_pack 的产物：pack.py 契约校验后的加载结果（spec 4.3）。"""
    info: PackInfo
    surface_class: type
    skinned_surface_class: type
    create_frame_renderer: object = None
    raw_module: types.ModuleType = None


def load_pack(info: PackInfo) -> LoadedPack:
    """加载 info.dir/pack.py 并校验契约；失败抛 PackLoadError。

    契约（spec 4.3）：模块级 surface_class 必填且可调用；create_frame_renderer
    可选（存在则必须可调用）；skinned_surface_class 缺省 = surface_class。
    """
    pack_py = info.dir / "pack.py"
    # 模块名含包 id 防冲突；目录名无需是合法标识符（仅作 sys.modules 键字符串）
    module_name = "shaderpacks.pack_code." + info.id
    # 同 id 重复加载：先弹出旧条目（sys.modules 覆盖语义，避免旧实例残留）
    sys.modules.pop(module_name, None)
    try:
        spec = importlib.util.spec_from_file_location(module_name, pack_py)
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
    except Exception as e:
        # 半初始化模块不留残骸
        sys.modules.pop(module_name, None)
        raise PackLoadError(f"pack.py 加载失败（{info.id}）：{e}") from e
    surface_class = getattr(module, "surface_class", None)
    if not callable(surface_class):
        raise PackLoadError(f"pack.py 缺少可调用的 surface_class（{info.id}）")
    cfr = getattr(module, "create_frame_renderer", None)
    if cfr is not None and not callable(cfr):
        raise PackLoadError(f"create_frame_renderer 不是可调用对象（{info.id}）")
    skinned = getattr(module, "skinned_surface_class", surface_class)
    return LoadedPack(info=info,
                      surface_class=surface_class,
                      skinned_surface_class=skinned,
                      create_frame_renderer=cfr,
                      raw_module=module)
```

- [ ] 4. 运行确认通过：

```bash
cd /home/oren/project/codes/python/FlamingSkyFPV && python -m pytest test/test_shader_packs.py -v
```

- [ ] 5. 提交：

```bash
cd /home/oren/project/codes/python/FlamingSkyFPV && git add shaderpacks/loader.py && git commit -m $'feat: 着色器预设代码加载器（契约校验）\n\nCo-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>'
```

- [ ] 6. 提交后自检清单：
  - `grep -n "shaderpacks.pack_code." shaderpacks/loader.py` 应出现 3 处（module_name 定义、sys.modules 弹出、sys.modules 赋值）。
  - `git show --stat HEAD`：本次提交只含 `shaderpacks/loader.py`。
## Task 5：runtime.py——帧辅助 frame_preprocess / SceneDrawer / pack_frame（TDD）

### Files

- 新增：`/home/oren/project/codes/python/FlamingSkyFPV/shaderpacks/runtime.py`
- 测试：`/home/oren/project/codes/python/FlamingSkyFPV/test/test_shader_packs.py`（追加 runtime 测试段）

### Interfaces

- Consumes：`soup3D.light`（`dirty` 全局、`set_surface_light()`、`EAU` 全局）、`soup3D.EAU`、`soup3D.shader.set_mat_queue`；screen 的 `render_scene` 绑定方法与 `_model` 属性（flight.py 现有结构，反射读取）。
- Produces：
  - `frame_preprocess() -> None`：复刻 `soup3D.__init__.frame_render` 的光源与 EAU 段。
  - `SceneDrawer(draw_scene)`：无参可调用（转调 draw_scene），`.faces` 属性 = `[(model, face), ...]`（经 `draw_scene.__self__._model` 反射；无场景时为空列表）。
  - `pack_frame(renderer, draw_scene) -> None`：`frame_preprocess()` + `renderer.render_frame(SceneDrawer(draw_scene))`。

### 背景

spec 5.3：自定义帧渲染路径不执行引擎 `frame_render()` 内的光源脏标记与 EAU 处理，由游戏侧复刻，**引擎一行不改**。`frame_preprocess` 顶部注释标注来源与上游对齐要求（spec 11 风险表）。`pack_frame` 是 App 渲染分支（Task 7）传入引擎 `update(render=...)` 的回调组装点；`SceneDrawer.faces` 供包的阴影通道迭代 `(model, face)` 对（spec 4.3，同模型 faces 连续排列）。

### Steps

- [ ] 1. 在 `test/test_shader_packs.py` 顶部 import 区追加 `from types import SimpleNamespace`，文件末尾追加 runtime 失败测试（import 区追加 `from shaderpacks import runtime`）：

```python
# ---- runtime（Task 5）----

@pytest.fixture
def engine_state():
    """保存/恢复引擎全局队列与脏标记，避免污染其他测试"""
    old = (soup3D.EAU, soup3D.light.EAU, soup3D.light.dirty)
    yield
    soup3D.EAU = old[0]
    soup3D.light.EAU = old[1]
    soup3D.light.dirty = old[2]


def test_frame_preprocess_dirty_calls_set_surface_light(engine_state, monkeypatch):
    """light.dirty 为 True → 调 set_surface_light（帧前置处理的光源段）"""
    calls = []
    monkeypatch.setattr(soup3D.light, "set_surface_light", lambda: calls.append(1))
    soup3D.light.dirty = True
    runtime.frame_preprocess()
    assert calls == [1]


def test_frame_preprocess_dirty_cleared_by_real_function(engine_state):
    """真实 set_surface_light：dirty True → 调用后 False（set_mat_queue 为空，无 GL 依赖）"""
    soup3D.light.dirty = True
    runtime.frame_preprocess()
    assert soup3D.light.dirty is False


def test_frame_preprocess_eau_execute_and_double_clear(engine_state):
    """EAU 合并执行 + 双清空（soup3D.EAU 与 soup3D.light.EAU 都执行并清空）"""
    ran = []
    soup3D.EAU.append((lambda v: ran.append(("eau", v)), 7))
    soup3D.light.EAU.append((lambda: ran.append(("light",)),))
    runtime.frame_preprocess()
    assert ran == [("eau", 7), ("light",)]
    assert soup3D.EAU == []
    assert soup3D.light.EAU == []


def test_frame_preprocess_eau_length_change_raises(engine_state):
    """EAU 执行中变长 → 抛异常（引擎同款防护）"""
    def poison():
        soup3D.EAU.append((lambda: None,))
    soup3D.EAU.append((poison,))
    with pytest.raises(Exception):
        runtime.frame_preprocess()


class _FakeScreen:
    """带 _model 的假 screen（同 flight.py 结构；render_scene 可统计调用）"""

    def __init__(self, model=None):
        self._model = model
        self.drawn = 0

    def render_scene(self):
        self.drawn += 1


def test_scene_drawer_faces_reflection(engine_state):
    """SceneDrawer.faces 反射 draw_scene.__self__._model → (model, face) 对列表"""
    faces = [SimpleNamespace(mode=1, vertex=[]), SimpleNamespace(mode=1, vertex=[])]
    model = SimpleNamespace(faces=faces)
    screen = _FakeScreen(model)
    drawer = runtime.SceneDrawer(screen.render_scene)
    assert drawer.faces == [(model, faces[0]), (model, faces[1])]
    drawer()                      # __call__ 转调 draw_scene
    assert screen.drawn == 1


def test_scene_drawer_without_model(engine_state):
    """无 _model（None）→ faces 空列表（无场景帧不炸）"""
    screen = _FakeScreen(None)
    drawer = runtime.SceneDrawer(screen.render_scene)
    assert drawer.faces == []


def test_pack_frame_wraps_and_preprocesses(engine_state, monkeypatch):
    """pack_frame = frame_preprocess + renderer.render_frame(SceneDrawer(draw_scene))"""
    seen = {}

    class _Renderer:
        def render_frame(self, drawer):
            seen["drawer"] = drawer

    monkeypatch.setattr(runtime, "frame_preprocess",
                        lambda: seen.setdefault("pre", True))
    screen = _FakeScreen(SimpleNamespace(faces=[]))
    renderer = _Renderer()
    runtime.pack_frame(renderer, screen.render_scene)
    assert seen.get("pre") is True
    assert isinstance(seen["drawer"], runtime.SceneDrawer)
    assert seen["drawer"].faces == []
    seen["drawer"]()
    assert screen.drawn == 1
```

- [ ] 2. 运行确认失败（AttributeError: module 'shaderpacks' has no attribute 'runtime'）：

```bash
cd /home/oren/project/codes/python/FlamingSkyFPV && python -m pytest test/test_shader_packs.py -v
```

- [ ] 3. 实现 `shaderpacks/runtime.py`：

```python
"""帧辅助：frame_preprocess() / SceneDrawer / pack_frame()（spec 5.3）。

自定义帧渲染路径不会执行引擎 frame_render() 内的光源脏标记与 EAU 处理，
由本模块复刻该段逻辑，引擎一行不改。上游同步核对点：soup3D/__init__.py
的 frame_render()（vendored 7.0.0）——上游更新时人工比对下面两段与其一致
（VENDORED.md 流程不变）。
"""
import soup3D


def frame_preprocess() -> None:
    """复刻引擎 frame_render 的光源与 EAU 段（来源：soup3D/__init__.py frame_render）。

    上游对齐要求：上游同步时人工核对本函数与引擎 frame_render 的对应段逐行一致。
    """
    # 设置光源：有光源变动时让所有着色器响应（引擎同款）
    if soup3D.light.dirty:
        soup3D.light.set_surface_light()

    # 执行更新执行队列：合并 → 写回全局 → 逐项执行 → 双清空（引擎同款语义：
    # 引擎为 EAU += light.EAU 后遍历全局本体，执行中入队可被长度守卫捕获）
    eau = soup3D.EAU + soup3D.light.EAU
    soup3D.EAU = eau
    eau_len = len(eau)
    for args in soup3D.EAU:
        args[0](*args[1:])
        if eau_len != len(soup3D.EAU):
            raise Exception(f"EAU length changed while running: "
                            f"{args[0].__name__}{(*args[1:],)}")

    soup3D.EAU = []
    soup3D.light.EAU = []


class SceneDrawer:
    """draw_scene 包装（spec 4.3/5.3）：包作者只读的 (model, face) 反射视图。

    无参可调用（转调 draw_scene）；.faces 属性经 draw_scene.__self__._model
    反射当前 screen 的模型（getattr(screen, "_model", None) → model.faces 或
    空列表，映射为 (model, face) 对列表；同模型 faces 连续排列）。
    """

    def __init__(self, draw_scene):
        self._draw_scene = draw_scene

    def __call__(self):
        self._draw_scene()

    @property
    def faces(self) -> list:
        screen = getattr(self._draw_scene, "__self__", None)
        model = getattr(screen, "_model", None)
        if model is None:
            return []
        return [(model, face) for face in model.faces]


def pack_frame(renderer, draw_scene) -> None:
    """预设帧组装点：光源/EAU 前置处理 + 帧渲染器接管（spec 5.3 契约）。

    由 App 渲染分支传入 soup3D.update(render=...)；包作者通常不自调本函数
    （游戏 runtime 每帧自动处理引擎光源/EAU，包无需自调）。
    """
    frame_preprocess()
    renderer.render_frame(SceneDrawer(draw_scene))
```

- [ ] 4. 运行确认通过：

```bash
cd /home/oren/project/codes/python/FlamingSkyFPV && python -m pytest test/test_shader_packs.py -v
```

- [ ] 5. 提交：

```bash
cd /home/oren/project/codes/python/FlamingSkyFPV && git add shaderpacks/runtime.py && git commit -m $'feat: 预设帧辅助 runtime（frame_preprocess/pack_frame）\n\nCo-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>'
```

- [ ] 6. 提交后自检清单：
  - `grep -c "frame_preprocess" shaderpacks/runtime.py` ≥ 3（定义、pack_frame 调用、docstring）。
  - `git show --stat HEAD`：本次提交只含 `shaderpacks/runtime.py`。
## Task 6：api.py——包作者便利 API（import 级 TDD）

### Files

- 新增：`/home/oren/project/codes/python/FlamingSkyFPV/shaderpacks/api.py`
- 测试：`/home/oren/project/codes/python/FlamingSkyFPV/test/test_shader_packs.py`（追加 api 测试段）

### Interfaces

- Consumes：`shaderpacks.runtime.frame_preprocess`（Task 5）、`soup3D.shader.AutoSP`、`OpenGL.GL`。
- Produces（包作者经 `import shaderpacks.api as api` 使用；`docs/shader_packs.md` 文档对象）：
  - `api.frame_preprocess()`：runtime 同名函数再导出（游戏 runtime 每帧经 `pack_frame` 自动调用；包完全自管整帧、绕开 pack_frame 时需自调）。
  - `api.SurfaceShaderBase`：`soup3D.shader.AutoSP` 别名——包作者继承后重写 `create_shader_program()` 返回自定义 GLSL 的 `ShaderProgram`（引擎 `BoneBinderSP` 即此模式先例）；需补充 uniform 时重写 `update()`（先补传自己的，再 `super().update()`）。
  - `api.FULLSCREEN_VS`：全屏三角形顶点着色器源码常量（与 `soup3D/target.py _ensure_present_program` 同模式，`gl_VertexID` 生成顶点）。
  - `api.SKIN_SURFACE_HINT`：蒙皮类继承 `soup3D.shader.BoneBinderSP` 的文档说明常量（一期无蒙皮示例，接口预留，spec 5.4）。
  - `api.FullScreenPass`：全屏通道辅助类，惰性空 VAO + `draw(program)` + `dispose()`。

### 背景

spec 5.4：api 只提供便利层，不设权限边界。FullScreenPass 让包作者写 SSR/合成通道时不必自管顶点缓冲。TDD 按 spec 8 的口径只做 import 级测试；GL 行为由 Task 10-12 的 GL 测试与 Task 13 手动脚本覆盖。

### Steps

- [ ] 1. 在 `test/test_shader_packs.py` 文件末尾追加 api 失败测试（import 级，无 GL）：

```python
# ---- api（Task 6）----

def test_api_exports():
    """api 公共导出：SurfaceShaderBase 别名、frame_preprocess 再导出、FULLSCREEN_VS 源码常量"""
    from shaderpacks import api
    assert api.SurfaceShaderBase is soup3D.shader.AutoSP
    assert api.frame_preprocess is runtime.frame_preprocess
    assert "#version 330 core" in api.FULLSCREEN_VS
    assert "gl_VertexID" in api.FULLSCREEN_VS
    assert "BoneBinderSP" in api.SKIN_SURFACE_HINT


def test_fullscreen_pass_lazy_vao():
    """FullScreenPass 构造不触 GL（惰性 VAO）；dispose 未创建时安全幂等"""
    from shaderpacks import api
    p = api.FullScreenPass()
    assert p._vao is None
    p.dispose()
    assert p._vao is None
```

- [ ] 2. 运行确认失败（ModuleNotFoundError: No module named 'shaderpacks.api'）：

```bash
cd /home/oren/project/codes/python/FlamingSkyFPV && python -m pytest test/test_shader_packs.py -v
```

- [ ] 3. 实现 `shaderpacks/api.py`：

```python
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
```

- [ ] 4. 运行确认通过：

```bash
cd /home/oren/project/codes/python/FlamingSkyFPV && python -m pytest test/test_shader_packs.py -v
```

- [ ] 5. 提交：

```bash
cd /home/oren/project/codes/python/FlamingSkyFPV && git add shaderpacks/api.py && git commit -m $'feat: 包作者便利 API（SurfaceShaderBase/FullScreenPass）\n\nCo-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>'
```
## Task 7：app.py 集成——预设生命周期、回落链与渲染分支（TDD + 冒烟）

### Files

- 修改：`/home/oren/project/codes/python/FlamingSkyFPV/app.py`
- 测试：`/home/oren/project/codes/python/FlamingSkyFPV/test/test_shader_packs.py`（追加 App 回落链测试段）

### Interfaces

- Consumes：`shaderpacks.registry.list_packs`（Task 3）、`shaderpacks.loader.load_pack`（Task 4）、`shaderpacks.runtime.pack_frame`（Task 5）、`Settings.shader_preset`（Task 1）、`soup3D.update(render=...)`（引擎帧级渲染器接管，异常时 finally 恢复 GL 状态）、`soup3D.proj_width/proj_height`（`soup3D.init` 写入的引擎全局）。
- Produces：
  - `App` 新字段：`packs: list[PackInfo]`、`preset: LoadedPack | None`（reload 后保证非 None，内置损坏除外）、`_frame_renderer`（None = 默认管线直渲）、`preset_fallback: bool`、`_pending_preset_reload: bool`。
  - `App._dispose_frame_renderer() -> None`：释放当前帧渲染器（dispose 契约可选）。
  - `App._find_pack_info(pack_id: str) -> PackInfo | None`：按 id 找 valid 包。
  - `App.reload_shader_preset() -> None`：完整回落链；代码级失败回落 `builtin_performance`，applied id ≠ 存档 id 时置 `preset_fallback`。
  - `run()` 渲染分支：`_frame_renderer` 非 None → `soup3D.update(render=lambda: runtime.pack_frame(...))`（异常当帧起回落默认管线）；None → 原两行不变。
  - `run()` 帧边界：`elif self._pending_preset_reload:` 执行 `reload_shader_preset()`（与 `_pending_rebuild` 同机制；两者同置位只走 rebuild）。
  - `rebuild_window()`：`_open_window()` 之后、`screen.reload()` 之前调 `reload_shader_preset()`。

### 背景

spec 5.7/6：`__init__` 在 `_open_window()` 之后调一次 `reload_shader_preset()`（`_open_window` 自身不调，避免与 rebuild 路径双重调用）；运行期兜底 = `pack_frame` 抛异常当帧起回落默认管线（引擎 `update(render=...)` 的 finally 已恢复 GL 状态，异常后状态干净）；`_pending_preset_reload` 与 `_pending_rebuild` 同机制（GL 资源创建不在 imgui 帧中做）。

### Steps

- [ ] 1. 在 `test/test_shader_packs.py` 顶部 import 区追加 `from pathlib import Path`、`from app import App`、`from config_store import Settings`、`from shaderpacks.loader import LoadedPack`、`from shaderpacks.registry import PackInfo`（`SimpleNamespace`、`soup3D`、`PackLoadError`、`pytest` 此前任务已导入），并在文件末尾追加以下失败测试：

```python
# ---- App 回落链（Task 7）----

def _make_app_stub():
    """绕过 App.__init__（不建窗口/GL）构造最小实例：reload 只依赖这几个字段"""
    s = App.__new__(App)
    s.settings = Settings()
    s.packs = []
    s.preset = None
    s._frame_renderer = None
    s.preset_fallback = False
    return s


def _fake_packs():
    """假包列表：两内置包 + 一个失效 id（dir 无效，配合假 load_pack 使用）"""
    return [
        PackInfo(id="builtin_performance", dir=Path("x"), name="性能优先", builtin=True),
        PackInfo(id="builtin_quality", dir=Path("x"), name="画面优先", builtin=True),
        PackInfo(id="gone_pack", dir=Path("x"), name="消失的包"),
    ]


def _fake_load_pack_factory(cfr_by_id=None):
    """假 load_pack 工厂：cfr_by_id 映射包 id → create_frame_renderer（缺省 None）"""
    def fake_load_pack(info):
        cfr = (cfr_by_id or {}).get(info.id)
        return LoadedPack(info=info, surface_class=soup3D.shader.AutoSP,
                          skinned_surface_class=soup3D.shader.AutoSP,
                          create_frame_renderer=cfr,
                          raw_module=object())
    return fake_load_pack


def _patch_pack_layer(monkeypatch, packs, fake_load_pack):
    """把 app 模块命名空间的 list_packs/load_pack 换成假货（无文件系统/GL 依赖）"""
    import app as app_module
    monkeypatch.setattr(app_module, "list_packs", lambda packs_root="shader_packs": packs)
    monkeypatch.setattr(app_module, "load_pack", fake_load_pack)


def test_reload_normal_selects_requested_pack(monkeypatch):
    """正常路径：按存档 id 加载，无回落、无帧渲染器（性能优先包）"""
    stub = _make_app_stub()
    _patch_pack_layer(monkeypatch, _fake_packs(), _fake_load_pack_factory())
    App.reload_shader_preset(stub)
    assert stub.preset.info.id == "builtin_performance"
    assert stub._frame_renderer is None
    assert stub.preset_fallback is False


def test_reload_missing_id_falls_back(monkeypatch):
    """存档 id 不在 valid 列表 → 回落 builtin_performance + fallback 标记"""
    stub = _make_app_stub()
    stub.settings.shader_preset = "gone_pack"
    _patch_pack_layer(monkeypatch, _fake_packs(), _fake_load_pack_factory())
    App.reload_shader_preset(stub)
    assert stub.preset.info.id == "builtin_performance"
    assert stub.preset_fallback is True


def test_reload_renderer_instantiation_fails_falls_back(monkeypatch):
    """create_frame_renderer 实例化抛异常 → 回落性能优先 + fallback 标记"""
    stub = _make_app_stub()
    stub.settings.shader_preset = "builtin_quality"

    def boom_cfr(w, h):
        raise RuntimeError("实例化炸")

    fake_load_pack = _fake_load_pack_factory(cfr_by_id={"builtin_quality": boom_cfr})
    _patch_pack_layer(monkeypatch, _fake_packs(), fake_load_pack)
    App.reload_shader_preset(stub)
    assert stub.preset.info.id == "builtin_performance"
    assert stub.preset_fallback is True


def test_reload_frame_renderer_kept_when_ok(monkeypatch):
    """实例化成功 → _frame_renderer 保持实例，无回落"""
    stub = _make_app_stub()
    stub.settings.shader_preset = "builtin_quality"
    renderer = SimpleNamespace()
    fake_load_pack = _fake_load_pack_factory(
        cfr_by_id={"builtin_quality": lambda w, h: renderer})
    _patch_pack_layer(monkeypatch, _fake_packs(), fake_load_pack)
    App.reload_shader_preset(stub)
    assert stub._frame_renderer is renderer
    assert stub.preset_fallback is False


def test_reload_catastrophic_leaves_default_pipeline(monkeypatch):
    """连性能优先都失败 → 空预设走默认管线，不抛出中断游戏"""
    stub = _make_app_stub()
    _patch_pack_layer(monkeypatch, _fake_packs(),
                      lambda info: (_ for _ in ()).throw(PackLoadError("全炸")))
    App.reload_shader_preset(stub)
    assert stub.preset is None
    assert stub._frame_renderer is None
    assert stub.preset_fallback is True
```

- [ ] 2. 运行确认失败（AttributeError: type object 'App' has no attribute 'reload_shader_preset'）：

```bash
cd /home/oren/project/codes/python/FlamingSkyFPV && python -m pytest test/test_shader_packs.py -v
```

- [ ] 3. 修改 `app.py`（七处改动）：

  - 3a. 顶部 import 区追加（`import traceback` 放在现有 `import time` 之后；shaderpacks 三行放在 `from scene_loader import list_scenes` 之后）：

```python
import traceback
```

```python
from shaderpacks import runtime
from shaderpacks.loader import load_pack
from shaderpacks.registry import list_packs
```

  - 3b. `__init__` 中 `self._pending_rebuild = False` 行后追加五个字段：

```python
        # 着色器预设（shaderpacks）：包列表与当前生效预设（spec 5.7）
        self.packs = list_packs()            # 预设包列表（reload_shader_preset 每次重扫）
        self.preset = None                   # LoadedPack；reload 后保证非 None（内置损坏除外）
        self._frame_renderer = None          # 预设帧渲染器；None = 默认管线直渲
        self.preset_fallback = False         # 预设回落提示标记（设置页显示）
        self._pending_preset_reload = False  # 帧边界执行的预设重载标记（同 _pending_rebuild）
```

  - 3c. `__init__` 中 `self._open_window()` 行后追加首次加载：

```python
        # 着色器预设：GL 上下文就绪后加载一次（帧渲染器的 RT/程序在此创建）；
        # _open_window 自身不调（避免与 rebuild 路径双重调用，spec 5.7）
        self.reload_shader_preset()
```

  - 3d. `rebuild_window` 中 `self._open_window()` 行后、`new_font = load_cjk_font()` 行前插入：

```python
            # 新 GL 上下文：预设 RT/程序属旧上下文必须重建（失败由回落链兜底）；
            # 置于 screen.reload() 之前，保证场景以新包表面类重载（spec 5.7）
            self.reload_shader_preset()
```

  - 3e. `run()` 中 `if self._pending_rebuild:` 块整体替换为（追加清 `_pending_preset_reload` 与 `elif` 分支）：

```python
            if self._pending_rebuild:
                # 帧边界执行重建：imgui 帧中禁止字体图集操作（add_font 在帧中
                # 失败被吞 → font 置 None → push/pop 失衡 → render 断言崩溃，
                # 用户实飞「保存并应用」触发过；故 UI 处理器只置标记，此处执行）
                self._pending_rebuild = False
                self._pending_preset_reload = False  # rebuild 尾部已 reload，避免二次执行
                self.rebuild_window()
            elif self._pending_preset_reload:
                # 帧边界执行预设重载：GL 资源创建禁止在 imgui 帧中进行（同 _pending_rebuild）
                self._pending_preset_reload = False
                self.reload_shader_preset()
```

  - 3f. `run()` 中渲染段两行（`soup3D.update()               # 先渲 3D（spike 验证的顺序）` 与 `self.screen.render_scene()    # 3D 直渲钩子（显示列表失效，见 base.py）`）替换为：

```python
            if self._frame_renderer is not None:
                fr = self._frame_renderer
                try:
                    soup3D.update(render=lambda: runtime.pack_frame(fr, self.screen.render_scene))
                except Exception:
                    # 运行期兜底（spec 5.7/6）：预设帧渲染抛异常 → 当帧起回落默认
                    # 管线（当帧画面残缺可接受），不中断游戏。引擎 update(render=...)
                    # 的 finally 已恢复 GL 状态，异常后状态干净
                    traceback.print_exc()
                    self._dispose_frame_renderer()
                    self.preset_fallback = True
                    soup3D.update()
                    self.screen.render_scene()
            else:
                soup3D.update()               # 先渲 3D（spike 验证的顺序）
                self.screen.render_scene()    # 3D 直渲钩子（显示列表失效，见 base.py）
```

  - 3g. 在 `_render_debug_overlay` 方法定义前（App 类内）追加三个方法：

```python
    # ---- 着色器预设（shaderpacks）----
    def _dispose_frame_renderer(self):
        """释放当前帧渲染器（dispose 契约可选；释放失败打印不阻断回落链）"""
        if self._frame_renderer is None:
            return
        dispose = getattr(self._frame_renderer, "dispose", None)
        if callable(dispose):
            try:
                dispose()
            except Exception:
                traceback.print_exc()
        self._frame_renderer = None

    def _find_pack_info(self, pack_id: str):
        """在 self.packs 里找 id 匹配且 valid 的包（无则 None）"""
        return next((p for p in self.packs if p.id == pack_id and p.valid), None)

    def reload_shader_preset(self):
        """按 settings.shader_preset 重载预设（spec 5.7 回落链）。

        旧渲染器释放 → 重扫包列表 → 找 valid 包 → load_pack → 实例化帧渲染器。
        代码级失败（import/契约/实例化，含目标 id 不存在时 load_pack(None) 的
        AttributeError）整体回落 builtin_performance；连性能优先都失败（内置包
        损坏）→ 空预设走默认管线。applied id 与存档不一致时置 preset_fallback
        （设置页显示回落提示）。
        """
        self._dispose_frame_renderer()
        self.preset_fallback = False
        try:
            self.packs = list_packs()
            info = self._find_pack_info(self.settings.shader_preset)
            try:
                loaded = load_pack(info)
                renderer = None
                if loaded.create_frame_renderer is not None:
                    renderer = loaded.create_frame_renderer(soup3D.proj_width,
                                                            soup3D.proj_height)
            except Exception:
                # 代码级失败：整体回落性能优先
                traceback.print_exc()
                info = self._find_pack_info("builtin_performance")
                loaded = load_pack(info)
                renderer = None
                if loaded.create_frame_renderer is not None:
                    renderer = loaded.create_frame_renderer(soup3D.proj_width,
                                                            soup3D.proj_height)
            self.preset = loaded
            self._frame_renderer = renderer
            self.preset_fallback = loaded.info.id != self.settings.shader_preset
        except Exception:
            # 兜底：连性能优先包都加载失败（内置包损坏的异常情形）→ 空预设走默认
            # 管线；flight 注入点对 preset=None 会 AttributeError → 场景加载失败进
            # 错误弹窗，游戏本体仍可用（spec 6 错误表的最终降级）
            traceback.print_exc()
            self.preset = None
            self._frame_renderer = None
            self.preset_fallback = True
```

- [ ] 4. 运行确认通过（先本文件，再全量确认无回归）：

```bash
cd /home/oren/project/codes/python/FlamingSkyFPV && python -m pytest test/test_shader_packs.py -v && python -m pytest test/ -v
```

- [ ] 5. 冒烟验证（默认性能优先 → 默认管线分支，行为与现状一致）：

```bash
cd /home/oren/project/codes/python/FlamingSkyFPV && python main.py --frames 60
```

- [ ] 6. 提交（只含 app.py）：

```bash
cd /home/oren/project/codes/python/FlamingSkyFPV && git add app.py && git commit -m $'feat: App 接入着色器预设（回落链 + 帧渲染分支）\n\nCo-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>'
```
## Task 8：设置页画质预设下拉与回落提示（TDD）

### Files

- 修改：`/home/oren/project/codes/python/FlamingSkyFPV/screens/settings.py`
- 测试：`/home/oren/project/codes/python/FlamingSkyFPV/test/test_shader_packs.py`（追加设置页 UI 测试段）

### Interfaces

- Consumes：`app.packs`（Task 7 的 `list[PackInfo]`）、`app.preset_fallback`（Task 7）、`Settings.shader_preset`（Task 1）、`app._pending_rebuild` / `app._pending_preset_reload`（Task 7）、`app.restart_needed`、`app.store.save`、`RESOLUTIONS` / `FPS_MODES`（settings.py 现有常量）。
- Produces：
  - `SettingsScreen._preset_id` 工作副本（enter 时非法存量值回落 `"builtin_performance"`，与 fps_mode 先例一致）。
  - 「画质预设」下拉：候选项 = valid 包（内置在前），label = `PackInfo.name`，value 存 id。
  - 「保存并应用」：写 `settings.shader_preset` + 存盘 + id 有变化时置 `_pending_preset_reload = True`。
  - 页面回落提示文字（`preset_fallback` 为 True 时）与 invalid 文件夹包文字行。

### 背景

spec 5.8：下拉候选项 = valid 包列表（内置在前），label = `name`；value 存 id；非法存量值在 enter 时即回落显示（fps_mode 先例）；当前 id 不在 valid 列表 → 显示回落提示；invalid 文件夹包在页面下方文字行列出（`<name>（无效：<error>）已跳过`，镜像 flight 场景选择）；「保存并应用」写存档 + 置帧边界标记（生效时机 = 下次进飞行，场景加载时以当时 surface_class 实例化）。

### Steps

- [ ] 1. 在 `test/test_shader_packs.py` 顶部 import 区追加 `import imgui` 与 `import pygame`（此前未导入），文件末尾追加设置页 UI 失败测试（需真实 X 显示，fixture 约定同 test_flight_select_ui.py）：

```python
# ---- 设置页（Task 8，需真实 X 显示）----

@pytest.fixture(scope="module")
def ui_ctx():
    """设置页 UI 测试的真实 GL + imgui 上下文（模块级小窗）"""
    pygame.init()
    pygame.display.set_mode((800, 600), pygame.DOUBLEBUF | pygame.OPENGL)
    imgui.create_context()
    from imgui.integrations.pygame import PygameRenderer as _PR
    impl = _PR()
    imgui.get_io().display_size = (800, 600)
    yield impl
    pygame.display.quit()


def _make_settings_app(preset_id="builtin_performance", fallback=False):
    """设置页假 app（SimpleNamespace，同 test_flight_select_ui 约定）"""
    return SimpleNamespace(
        settings=Settings(resolution=(1280, 720), fullscreen=False,
                          fps_mode="60", shader_preset=preset_id),
        packs=list_packs(),
        preset_fallback=fallback,
        restart_needed=False,
        store=SimpleNamespace(save=lambda name, obj: None),
        _pending_rebuild=False,
        _pending_preset_reload=False,
    )


def _render_frames(screen, frames=3):
    for _ in range(frames):
        imgui.new_frame()
        screen.render_ui()
        imgui.render()


def test_settings_screen_renders_with_preset_dropdown(ui_ctx):
    """设置页含画质预设下拉时不崩溃（假 app 渲染冒烟）"""
    from screens.settings import SettingsScreen
    s = SettingsScreen()
    s.enter(_make_settings_app())
    _render_frames(s)
    s.exit()


def test_settings_invalid_preset_id_falls_back_on_enter(ui_ctx):
    """存量 id 失效 → enter 时工作副本回落 builtin_performance（fps_mode 先例）"""
    from screens.settings import SettingsScreen
    s = SettingsScreen()
    s.enter(_make_settings_app(preset_id="deleted_pack"))
    assert s._preset_id == "builtin_performance"
    s.exit()
```

- [ ] 2. 运行确认失败（AttributeError: 'SettingsScreen' object has no attribute '_preset_id'）：

```bash
cd /home/oren/project/codes/python/FlamingSkyFPV && python -m pytest test/test_shader_packs.py -v
```

- [ ] 3. 修改 `screens/settings.py`（四处改动）：

  - 3a. `enter` 方法末尾（`self._fps = ...` 之后）追加画质预设工作副本：

```python
        # 画质预设工作副本：非法存量值 enter 时即回落显示（fps_mode 先例）
        valid_ids = [p.id for p in app.packs if p.valid]
        self._preset_id = app.settings.shader_preset if app.settings.shader_preset in valid_ids \
            else "builtin_performance"
```

  - 3b. `render_ui` 中「最大帧率」下拉块之后、「全屏开关」之前插入画质预设下拉：

```python
        # 画质预设下拉：候选项 = valid 包（内置在前），label = name，value 存 id
        valid_packs = [p for p in self.app.packs if p.valid]
        preset_labels = [p.name for p in valid_packs]
        preset_ids = [p.id for p in valid_packs]
        preset_idx = preset_ids.index(self._preset_id) if self._preset_id in preset_ids else 0
        _, preset_picked = imgui.combo("画质预设", preset_idx, preset_labels)
        if preset_ids[preset_picked] != self._preset_id:
            self._preset_id = preset_ids[preset_picked]; changed = True
```

  - 3c. 「全屏开关」之后、「保存并应用」按钮之前插入回落提示；「保存并应用」按钮块替换为（追加 shader_preset 写入与画质标记）；文件末尾 `imgui.end()` 前追加 invalid 包文字行。保存按钮块现状：

```python
        if imgui.button("保存并应用", 200, 40):
            self.app.settings.resolution = tuple(self._res)
            self.app.settings.fullscreen = self._full
            self.app.settings.fps_mode = self._fps
            self.app.store.save("settings", self.app.settings)
            # 不在此处直接 rebuild_window()：此刻处于 imgui 帧中间，字体图集操作
            # （load_cjk_font → add_font）帧中失败被吞 → font 置 None → 主循环
            # push/pop 失衡 → render 断言崩溃（用户实飞触发过）。只置标记，
            # run() 在帧边界（下一帧 tick 后）执行重建
            self.app._pending_rebuild = True
```

替换为：

```python
        if self.app.preset_fallback:
            imgui.text("画质预设无效，已回落性能优先")

        if imgui.button("保存并应用", 200, 40):
            preset_changed = self._preset_id != self.app.settings.shader_preset
            self.app.settings.resolution = tuple(self._res)
            self.app.settings.fullscreen = self._full
            self.app.settings.fps_mode = self._fps
            self.app.settings.shader_preset = self._preset_id
            self.app.store.save("settings", self.app.settings)
            # 不在此处直接 rebuild_window()：此刻处于 imgui 帧中间，字体图集操作
            # （load_cjk_font → add_font）帧中失败被吞 → font 置 None → 主循环
            # push/pop 失衡 → render 断言崩溃（用户实飞触发过）。只置标记，
            # run() 在帧边界（下一帧 tick 后）执行重建
            self.app._pending_rebuild = True
            if preset_changed:
                # 画质变更走帧边界 reload（rebuild 分支会先清本标记：rebuild 尾部
                # 已含 reload_shader_preset，两者同置位时只走 rebuild，spec 5.7）
                self.app._pending_preset_reload = True
```

  - 3d. `render_ui` 末尾 `if self.app.restart_needed:` 提示行之后、`imgui.end()` 之前追加：

```python
        # 无效文件夹包：页面下方文字行列出（镜像 flight 场景选择，不进下拉）
        for p in self.app.packs:
            if not p.valid:
                imgui.text(f"{p.name}（无效：{p.error}）已跳过")
```

- [ ] 4. 运行确认通过：

```bash
cd /home/oren/project/codes/python/FlamingSkyFPV && python -m pytest test/test_shader_packs.py -v
```

- [ ] 5. 提交（只含 screens/settings.py）：

```bash
cd /home/oren/project/codes/python/FlamingSkyFPV && git add screens/settings.py && git commit -m $'feat: 设置页画质预设下拉与回落提示\n\nCo-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>'
```

---

## Task 9：flight.py 场景加载注入预设表面类（一行改动 + 测试接线）

### Files

- 修改：`/home/oren/project/codes/python/FlamingSkyFPV/screens/flight.py`
- 修改（测试接线，不入提交）：`/home/oren/project/codes/python/FlamingSkyFPV/test/test_flight_select_ui.py`

### Interfaces

- Consumes：`app.preset`（Task 7 保证非 None，`LoadedPack.surface_class`）、`soup3D.open_obj` 的 `surface` 参数（引擎 `shader.make_surface` 按类签名内省过滤材质参数）。
- Produces：场景模型以当前预设表面类实例化（性能优先 = AutoSP 与现状逐像素一致；画面优先 = PBRSurface，见 Task 11）。

### 背景

spec 5.9：`_load_scene` 一行改动——`surface=self.app.preset.surface_class`。其余（灯光、ambient、物理）不动。注意：`test_flight_select_ui.py` 现有 `test_exit_removes_scene_light_from_engine_queue` 的假 app 没有 `preset` 属性，注入后 `_load_scene` 读 `self.app.preset.surface_class` 会 AttributeError，必须同步给假 app 补 `preset`。

### Steps

- [ ] 1. 修改 `test/test_flight_select_ui.py` 的 `test_exit_removes_scene_light_from_engine_queue`（假 app 补 preset；该文件不入 git 提交，但必须改，否则套件红）：

```python
    app = SimpleNamespace(scenes=scenes, drone_params=DroneParams(),
                          preset=SimpleNamespace(surface_class=soup3D.shader.AutoSP))
```

- [ ] 2. 在 `test/test_flight_select_ui.py` 文件末尾追加注入回归测试：

```python
def test_load_scene_injects_preset_surface_class(ui_ctx, tmp_path, monkeypatch):
    """_load_scene 把 app.preset.surface_class 注入 open_obj（spec 5.9 一行改动）"""
    scenes = _make_scene(tmp_path, with_cover=False)

    class _SentinelSurface:
        """哨兵类：仅用于断言注入传递"""

    captured = {}

    def fake_open_obj(obj, mtl=None, surface=None, **kw):
        captured["surface"] = surface
        return SimpleNamespace(size=lambda *s: None, faces=[])

    monkeypatch.setattr(soup3D, "open_obj", fake_open_obj)
    app = SimpleNamespace(scenes=scenes, drone_params=DroneParams(),
                          preset=SimpleNamespace(surface_class=_SentinelSurface))
    fs = FlightScreen()
    fs.enter(app)
    fs._scene = scenes[0]
    fs.stage = "loading"
    fs.update(1 / 60)          # loading 阶段触发 _load_scene
    assert captured["surface"] is _SentinelSurface
    fs.exit()
```

- [ ] 3. 运行确认失败（现有两测试断言 captured 缺失/字段缺失）：

```bash
cd /home/oren/project/codes/python/FlamingSkyFPV && python -m pytest test/test_flight_select_ui.py -v
```

- [ ] 4. 修改 `screens/flight.py` 的 `_load_scene`（精确替换这一行）：

现状：

```python
        self._model = soup3D.open_obj(str(obj), str(mtl) if mtl.exists() else None)
```

替换为：

```python
        self._model = soup3D.open_obj(str(obj), str(mtl) if mtl.exists() else None,
                                      surface=self.app.preset.surface_class)
```

- [ ] 5. 运行确认通过（含既有测试回归）：

```bash
cd /home/oren/project/codes/python/FlamingSkyFPV && python -m pytest test/test_flight_select_ui.py -v && python -m pytest test/ -v
```

- [ ] 6. 提交（只含 screens/flight.py；test/ 不入提交）：

```bash
cd /home/oren/project/codes/python/FlamingSkyFPV && git add screens/flight.py && git commit -m $'feat: 场景加载注入预设表面类\n\nCo-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>'
```
## Task 10：quality 包（一）——_ShadowRegistry + DepthSurface + QualityFrameRenderer 最小版（GL 测试）

### Files

- 重写：`/home/oren/project/codes/python/FlamingSkyFPV/shaderpacks/builtin/quality/pack.py`（覆盖 Task 2 基线，全文件清单见步骤 3）
- 新增：`/home/oren/project/codes/python/FlamingSkyFPV/test/test_shader_quality_gl.py`

### Interfaces

- Consumes：`soup3D.shader.AutoSP/ShaderProgram/MixChannel`、`soup3D.light.light_queue`（Direct/_calc_direction）、`soup3D.INT_VEC1/ARRAY_MATRIX_VEC4/FLOAT`（name.py 常量）、`soup3D.target.RenderTarget`（`bind/unbind/to_pixels/present`、`depth` 附着为 RenderTargetTexture）、`shaderpacks.runtime.pack_frame`（Task 5）。
- Produces：
  - 模块级单例 `_shadow_registry`（`light_view_proj: glm.mat4 | None`、`shadow_tex`、`enabled: bool`、`version: int`；`update()` 有变化才递增 version）。
  - `_ShadowSurfaceBase(AutoSP)`：阴影 uniform 按 version 缓存补传（`shadowMap` 单元 2、`shadowEnabled`、`lightSpaceMat`）。
  - `DepthSurface(_ShadowSurfaceBase)`：阴影通道深度表面（alpha<0.5 discard）。
  - `QualityFrameRenderer(width, height)`：`render_frame(draw_scene)` = 阴影状态 → 阴影通道 → HDR 主通道 → `RenderTarget.present()`；`dispose()`。
  - GL 测试文件 `test/test_shader_quality_gl.py`（约定同 test_vendored_soup3d.py）。

### 背景

spec 5.6 帧渲染器流程 1-4 步与"深度表面类"小节；阴影相机参数与集中常量；纹理单元 baseColor=0、normal=1、shadowMap=2、emission=3。Task 2 的 quality 基线 `pack.py` 本任务整体重写（surface_class 仍为 AutoSP；`create_frame_renderer` 契约导出留待 Task 12——此前 quality 预设走默认管线分支，行为与性能优先一致，避免中间提交改变游戏行为）。

### Steps

- [ ] 1. 新建 `test/test_shader_quality_gl.py`（失败测试）：

```python
"""画面优先包 GL 测试：阴影通道深度 + HDR 主通道 + present 直通（真实 X 显示）。

test/ 被 .gitignore 忽略，不随仓库提交。窗口与 HDR RT 尺寸 320×240。
断言用统计式像素比较（深度值、区域统计、直通等值），不依赖精确坐标
（约定同 test_vendored_soup3d.py）。
"""
import numpy as np
import pygame
import pytest

import soup3D
from OpenGL.GL import (GL_DEPTH_COMPONENT, GL_FLOAT, GL_RGBA, GL_UNSIGNED_BYTE,
                       glReadPixels)

from shaderpacks.loader import load_pack
from shaderpacks.registry import list_packs
from shaderpacks.runtime import pack_frame


@pytest.fixture(scope="module")
def gl_ctx():
    """模块级真实 GL 上下文（小窗 + 引擎初始化，约定同 test_vendored_soup3d.py）"""
    pygame.init()
    pygame.display.set_mode((320, 240), pygame.DOUBLEBUF | pygame.OPENGL)
    soup3D.init(320, 240, 75, (0.55, 0.75, 0.95), 0.1, 2048)
    yield
    pygame.display.quit()


# ---- 场景与渲染辅助 ----

def _load_quality_module():
    """加载 quality 包模块实例（load_pack 的 sys.modules 覆盖语义：模块级
    _shadow_registry 单例随新模块隔离，测试间互不污染）"""
    info = next(p for p in list_packs() if p.id == "builtin_quality" and p.valid)
    return load_pack(info).raw_module


def _make_sun():
    """太阳 + 环境光（返回 Direct 供 teardown destroy()；light_queue 强引用跨测试泄漏）"""
    sun = soup3D.light.Direct((30, -60, 0), (0.8, 0.8, 0.75))
    sun.turn_on()
    soup3D.light.ambient(0.45, 0.45, 0.45)
    return sun


def _quad(a, b, c, d):
    """四边形 → 两三角形六顶点（uv 简单铺 0/1）"""
    return [(*a, 0.0, 0.0), (*b, 1.0, 0.0), (*c, 1.0, 1.0),
            (*a, 0.0, 0.0), (*c, 1.0, 1.0), (*d, 0.0, 1.0)]


def _cube_verts(half, z0, z1):
    """以原点为中心的立方体 36 顶点（x,y ∈ ±half，z ∈ [z0,z1]）"""
    s = half
    return (_quad((-s, -s, z0), (s, -s, z0), (s, s, z0), (-s, s, z0))      # 底
            + _quad((-s, -s, z1), (s, -s, z1), (s, s, z1), (-s, s, z1))    # 顶
            + _quad((-s, -s, z0), (s, -s, z0), (s, -s, z1), (-s, -s, z1))  # y- 侧
            + _quad((s, -s, z0), (s, s, z0), (s, s, z1), (s, -s, z1))      # x+ 侧
            + _quad((s, s, z0), (-s, s, z0), (-s, s, z1), (s, s, z1))      # y+ 侧
            + _quad((-s, s, z0), (-s, -s, z0), (-s, -s, z1), (-s, s, z1))) # x- 侧


def _build_scene(surface_class):
    """代码生成场景：灰色地面 80×80 + 原点立方体（半宽 2、z 0..4），合并单 Model"""
    gray = soup3D.shader.MixChannel((1, 1), 0.5, 0.5, 0.5, 1.0)
    red = soup3D.shader.MixChannel((1, 1), 0.9, 0.3, 0.2, 1.0)
    ground_face = soup3D.Face("triangle_b", surface_class(gray),
                              _quad((-40, -40, 0), (40, -40, 0),
                                    (40, 40, 0), (-40, 40, 0)))
    cube_face = soup3D.Face("triangle_b", surface_class(red), _cube_verts(2, 0, 4))
    return soup3D.Model(0, 0, 0, ground_face, cube_face)


class _FakeScreen:
    """render_scene 绑定对象：_model 供 SceneDrawer.faces 反射（同 flight.py 结构）"""

    def __init__(self, model):
        self._model = model

    def render_scene(self):
        for face in self._model.faces:
            surface = face.surface
            if surface.is_dirty():
                surface.update()
            surface.use()
            surface.rend_cached(face.mode, face.vertex)
            surface.unuse()


def _setup_camera():
    soup3D.camera.goto(0, -18, 3)
    soup3D.camera.turn(0, -10, 0)


def _read_screen():
    """默认帧缓冲 → top-origin uint8 (240, 320, 4)"""
    raw = glReadPixels(0, 0, 320, 240, GL_RGBA, GL_UNSIGNED_BYTE)
    return np.frombuffer(raw, dtype=np.uint8).reshape(240, 320, 4)[::-1].copy()


def _read_depth_px(rt, x, y):
    """读渲染目标的深度缓冲单像素（bind/unbind 公共 API；glReadPixels 默认回 bytes）"""
    rt.bind()
    try:
        raw = glReadPixels(x, y, 1, 1, GL_DEPTH_COMPONENT, GL_FLOAT)
    finally:
        rt.unbind()
    return float(np.frombuffer(raw, dtype=np.float32)[0])


# ---- 测试 ----

def test_shadow_pass_writes_depth(gl_ctx):
    """阴影通道：阴影贴图中心有立方体深度（<0.99），无遮挡角落为清屏深度 1.0"""
    sun = _make_sun()
    try:
        model = _build_scene(soup3D.shader.AutoSP)
        module = _load_quality_module()
        renderer = module.QualityFrameRenderer(soup3D.proj_width, soup3D.proj_height)
        try:
            _setup_camera()
            pack_frame(renderer, _FakeScreen(model).render_scene)
            center = _read_depth_px(renderer._shadow_rt, 1024, 1024)
            corner = _read_depth_px(renderer._shadow_rt, 50, 50)
            assert center < 0.99, f"阴影贴图中心应有立方体深度，实际 {center}"
            assert corner >= 0.999, f"阴影贴图角落应为清屏深度 1.0，实际 {corner}"
        finally:
            renderer.dispose()
    finally:
        sun.destroy()


def test_hdr_main_pass_and_present(gl_ctx):
    """主通道：HDR RT 非均匀内容 + 中心非天空色；present 直通后屏幕 ≈ HDR 像素"""
    sun = _make_sun()
    try:
        model = _build_scene(soup3D.shader.AutoSP)
        module = _load_quality_module()
        renderer = module.QualityFrameRenderer(soup3D.proj_width, soup3D.proj_height)
        try:
            _setup_camera()
            pack_frame(renderer, _FakeScreen(model).render_scene)
            hdr = renderer._hdr_rt.to_pixels()      # top-origin float32 (240,320,4)
            assert float(np.std(hdr[..., :3])) > 0.02, "HDR 内容应非均匀（有物体）"
            sky = np.array([0.55, 0.75, 0.95])
            cube_px = hdr[120, 160, :3]
            assert float(np.abs(cube_px - sky).max()) > 0.1, "中心像素不应是天空色"
            # present 直通：屏幕像素 ≈ HDR 像素（rgba8 量化容差 ±2）
            screen = _read_screen()
            expect = np.round(hdr[120, 160, :3] * 255.0)
            got = screen[120, 160, :3].astype(int)
            assert np.abs(got - expect).max() <= 2, \
                f"present 直通偏差过大：屏幕 {got} vs HDR*255 {expect}"
        finally:
            renderer.dispose()
    finally:
        sun.destroy()
```

- [ ] 2. 运行确认失败（AttributeError: module ... has no attribute 'QualityFrameRenderer'）：

```bash
cd /home/oren/project/codes/python/FlamingSkyFPV && python -m pytest test/test_shader_quality_gl.py -v
```

- [ ] 3. 整体重写 `shaderpacks/builtin/quality/pack.py`（全文件清单；surface_class 仍为 AutoSP，`create_frame_renderer` 导出留待 Task 12）：

```python
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
            glClearColor(*prev_clear[:3])
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
            glClearColor(*prev_clear[:3])
            draw_scene()
        finally:
            rt.unbind()
```

- [ ] 4. 运行确认通过：

```bash
cd /home/oren/project/codes/python/FlamingSkyFPV && python -m pytest test/test_shader_quality_gl.py -v && python -m pytest test/test_shader_packs.py -v
```

- [ ] 5. 提交（只含 quality 包）：

```bash
cd /home/oren/project/codes/python/FlamingSkyFPV && git add shaderpacks/builtin/quality/pack.py && git commit -m $'feat: 画面优先包：阴影通道与 HDR 主通道\n\nCo-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>'
```
## Task 11：quality 包（二）——PBRSurface（Cook-Torrance GGX + PCF 阴影 + cameraPos）（GL 测试）

### Files

- 修改：`/home/oren/project/codes/python/FlamingSkyFPV/shaderpacks/builtin/quality/pack.py`（在 Task 10 文件上做三处精确改动，改动块均为完整代码）
- 测试：`/home/oren/project/codes/python/FlamingSkyFPV/test/test_shader_quality_gl.py`（追加 PBRSurface 阴影对比测试）

### Interfaces

- Consumes：Task 10 的 `_ShadowSurfaceBase`（阴影 uniform 按 version 补传）、`_shadow_registry`、`SHADOW_TEX_UNIT`；`soup3D.camera.X/Y/Z`（GGX 视线方向）；`soup3D.shader.ShaderProgram/MixChannel`；`soup3D.FLOAT_VEC1/FLOAT_VEC3`。
- Produces：
  - 常量 `DEFAULT_ROUGHNESS = 0.8`、`DEFAULT_METALLIC = 0.0`（MTL 的 Pr/Pm 未提供时的类默认）。
  - `PBRSurface(_ShadowSurfaceBase)`：构造签名 `(base_color, normal=(0.5,0.5,1), emission=(0,0,0), double_side=True, max_light_count=8, roughness=0.8, metallic=0.0)`——`make_surface` 内省接收 MTL 的 `roughness`/`metallic`（未提供时不传，用默认）。
  - `surface_class = PBRSurface`（模块级契约；画面优先包的场景材质自此起走 PBR）。

### 背景

spec 5.6 PBR 表面类小节：顶点着色器与 AutoSP 相同另输出 `LightSpacePos`；片段着色器纹理单元沿用 0/1/3、阴影图用 2；Light 结构体与 AutoSP 完全同布局；Cook-Torrance GGX（D=GGX、G=Smith–Schlick、F=Schlick(f0=mix(0.04, albedo, metallic))、漫反射 Lambert (1−metallic)·albedo/π）；阴影 3×3 PCF（9 taps）、bias = max(0.05·(1−N·L), 0.005)、proj.z>1 视为受光；环境光项 = ambient·albedo + 0.04·f0·ambient 近似环境高光。`update()` 重写：cameraPos 每帧补传 + `super().update()`（阴影 uniform 由基类按 version 补传，最后统一 flush）。

### Steps

- [ ] 1. 在 `test/test_shader_quality_gl.py` 末尾追加 PBRSurface 阴影对比失败测试：

```python
# ---- PBRSurface 阴影对比（Task 11）----

def _luma_stats(img, x0, x1, y0, y1):
    """区域亮度统计（Rec.601 加权）；img 为 top-origin (H,W,4) ndarray"""
    region = img[y0:y1, x0:x1, :3].astype(np.float64)
    lum = region @ np.array([0.299, 0.587, 0.114])
    return float(lum.min()), float(lum.max()), float(lum.mean())


def test_pbr_shadow_darker_than_lit_ground(gl_ctx):
    """PBRSurface：地面阴影区显著暗于受光区；关太阳后地面整体回到环境光水平"""
    sun = _make_sun()
    try:
        module = _load_quality_module()
        model = _build_scene(module.PBRSurface)
        renderer = module.QualityFrameRenderer(soup3D.proj_width, soup3D.proj_height)
        try:
            _setup_camera()
            pack_frame(renderer, _FakeScreen(model).render_scene)
            screen = _read_screen()
            # 采样地面下半区（避开立方体本体与天空：地面占屏幕行 57 以下，
            # 立方体约行 100-140，取 150 以下纯地面，含立方体落影）
            lo_on, hi_on, mean_on = _luma_stats(screen, 40, 280, 150, 235)
            assert hi_on >= 40, f"地面区域应非黑（最亮 {hi_on}）"
            assert lo_on < 0.8 * hi_on, \
                f"地面应有阴影对比（最暗 {lo_on} vs 最亮 {hi_on}）"
            # A/B 对照：关太阳（shadowEnabled=0 且无直射光）后地面均值应下降
            sun.turn_off()
            pack_frame(renderer, _FakeScreen(model).render_scene)
            screen2 = _read_screen()
            lo_off, hi_off, mean_off = _luma_stats(screen2, 40, 280, 150, 235)
            assert mean_off < mean_on, \
                f"关太阳后地面均值应下降（on {mean_on} → off {mean_off}）"
        finally:
            renderer.dispose()
    finally:
        sun.destroy()
```

- [ ] 2. 运行确认失败（module has no attribute 'PBRSurface'）：

```bash
cd /home/oren/project/codes/python/FlamingSkyFPV && python -m pytest test/test_shader_quality_gl.py -v
```

- [ ] 3. 修改 `shaderpacks/builtin/quality/pack.py`（三处改动，按顺序）：

  - 3a. 常量块追加两行（`SHADOW_TEX_UNIT = 2` 之后）：

```python
DEFAULT_ROUGHNESS = 0.8         # MTL 的 Pr 未提供时的默认粗糙度（spec 5.6）
DEFAULT_METALLIC = 0.0          # MTL 的 Pm 未提供时的默认金属度（spec 5.6）
```

  - 3b. `DepthSurface` 类定义之后、`QualityFrameRenderer` 类定义之前插入 `PBRSurface` 完整类：

```python
class PBRSurface(_ShadowSurfaceBase):
    """PBR 表面（Cook-Torrance GGX + 3×3 PCF 太阳阴影）。

    每材质一个实例（make_surface 内省接收 roughness/metallic，来自 MTL 的
    Pr/Pm，文件未提供时用类默认）。cameraPos 每帧补传（GGX 视线方向）；
    阴影 uniform 由 _ShadowSurfaceBase 按 registry version 缓存补传。
    """

    def __init__(self, base_color, normal=(0.5, 0.5, 1), emission=(0, 0, 0),
                 double_side=True, max_light_count=8,
                 roughness=DEFAULT_ROUGHNESS, metallic=DEFAULT_METALLIC):
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
```

  - 3c. 文件末尾追加模块级契约（`QualityFrameRenderer` 类定义之后，文件最后一行）：

```python

# 表面类契约（spec 4.3）：场景以 surface_class 实例化材质（make_surface 内省过滤，
# 只传该类可接收的标准材质参数）
surface_class = PBRSurface
```

- [ ] 4. 运行确认通过：

```bash
cd /home/oren/project/codes/python/FlamingSkyFPV && python -m pytest test/test_shader_quality_gl.py -v
```

- [ ] 5. 提交（只含 quality 包）：

```bash
cd /home/oren/project/codes/python/FlamingSkyFPV && git add shaderpacks/builtin/quality/pack.py && git commit -m $'feat: 画面优先包：PBR 表面（Cook-Torrance GGX + PCF 阴影）\n\nCo-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>'
```
## Task 12：quality 包（三）——SSR + ACES 合成通道、create_frame_renderer 契约导出、dispose（GL + 无 GL 测试）

### Files

- 修改：`/home/oren/project/codes/python/FlamingSkyFPV/shaderpacks/builtin/quality/pack.py`（在 Task 11 文件上做八处精确改动，改动块均为完整代码）
- 测试：`/home/oren/project/codes/python/FlamingSkyFPV/test/test_shader_quality_gl.py`（删除 present 直通断言测试，追加合成通道两测试）
- 测试：`/home/oren/project/codes/python/FlamingSkyFPV/test/test_shader_packs.py`（追加无 GL 契约测试）

### Interfaces

- Consumes：Task 6 的 `shaderpacks.api`（`FULLSCREEN_VS`、`FullScreenPass`）；`soup3D.get_projection_mat()`（引擎透视矩阵及其逆）；`soup3D.target.RenderTarget` 的 `color`/`depth` 附着；Task 11 的 `PBRSurface`。
- Produces：
  - 包常量：`EXPOSURE = 3.0`、`SSR_STEPS = 24`、`SSR_REFINE = 4`、`SSR_THICKNESS = 0.5`、`SSR_MAX_DIST = 120.0`（集中文件顶部，spec 7 性能预算调优入口）。
  - `QualityFrameRenderer.render_frame` 末段以 `_render_composite()` 替换 `self._hdr_rt.present()`：SSR（24 步 + 4 次二分，屏幕空间光线步进）+ 曝光 → ACES（Narkowicz）→ gamma 2.2，全屏三角直绘默认帧缓冲。
  - `dispose()` 扩展：删 RT + 合成程序 + 全屏 VAO。
  - 模块级 `create_frame_renderer(width, height)` 契约导出（spec 4.3；此后画面优先预设激活帧渲染路径）。

### 背景

spec 5.6 帧渲染器流程第 5/6 步（coordinator 裁定合并为单一合成通道：SSR 与 ACES 一个全屏通道直绘默认帧缓冲）；SSR 细节 = 深度重建视图空间（invProj）→ 法线 = normalize(cross(dFdx, dFdy)) → R=reflect(−V,N)，R.z≥0 跳过 → 命中判定 = 步进点视图 z ≤ 场景表面视图 z + 厚度 → 菲涅尔加权 + 屏幕边缘淡出；天空像素（深度=1）直通。EXPOSURE=3.0 为 Lambert/π 漫反射后整体偏暗的曝光补偿（目检可调）。SSR_STEPS 可 monkeypatch 为 0 做 A/B 对照（程序在渲染器构造时以当时常量编译）。

### Steps

- [ ] 1. 改造 `test/test_shader_quality_gl.py`：删除 `test_hdr_main_pass_and_present`（present 直通已被合成通道取代，断言不再成立），文件末尾追加两个合成通道失败测试：

```python
# ---- 合成通道（Task 12）----

def test_composite_aces_sky_math(gl_ctx):
    """合成通道：天空像素（深度=1 直通）经 曝光→ACES→gamma 的数学可精确复算"""
    sun = _make_sun()
    try:
        module = _load_quality_module()
        model = _build_scene(module.PBRSurface)
        renderer = module.QualityFrameRenderer(soup3D.proj_width, soup3D.proj_height)
        try:
            _setup_camera()
            pack_frame(renderer, _FakeScreen(model).render_scene)
            hdr = renderer._hdr_rt.to_pixels()
            screen = _read_screen()

            def aces(x):
                # 与 _COMPOSITE_FS 相同的 Narkowicz 拟合
                return (x * (2.51 * x + 0.03)) / (x * (2.43 * x + 0.59) + 0.14)

            h, w = 2, 2   # 角落天空像素（深度=1，无物体）
            for c in range(3):
                x = float(hdr[h, w, c]) * module.EXPOSURE
                expect = round(pow(min(aces(x), 1.0), 1.0 / 2.2) * 255.0)
                got = int(screen[h, w, c])
                assert abs(got - expect) <= 2, \
                    f"天空通道 {c}: 屏幕 {got} vs 期望 {expect}（HDR {float(hdr[h, w, c])}）"
        finally:
            renderer.dispose()
    finally:
        sun.destroy()


def test_ssr_floor_reflection_a_b(gl_ctx, monkeypatch):
    """SSR A/B：SSR_STEPS=24 vs 0（monkeypatch 后重建渲染器），镜面地板差异像素集中在下半屏"""
    sun = _make_sun()
    try:
        module = _load_quality_module()
        # 镜面地板：低粗糙度地面（掠射角菲涅尔加权强，反射可见）
        gray = soup3D.shader.MixChannel((1, 1), 0.5, 0.5, 0.5, 1.0)
        red = soup3D.shader.MixChannel((1, 1), 0.9, 0.3, 0.2, 1.0)
        ground = soup3D.Face(
            "triangle_b", module.PBRSurface(gray, roughness=0.05),
            _quad((-40, -40, 0), (40, -40, 0), (40, 40, 0), (-40, 40, 0)))
        cube = soup3D.Face(
            "triangle_b", module.PBRSurface(red),
            _cube_verts(2, 0, 4))
        model = soup3D.Model(0, 0, 0, ground, cube)
        # 先构造全量渲染器（常量正常），再 patch 常量构造对照渲染器（构造时编译）
        renderer_on = module.QualityFrameRenderer(soup3D.proj_width, soup3D.proj_height)
        try:
            _setup_camera()
            pack_frame(renderer_on, _FakeScreen(model).render_scene)
            screen_on = _read_screen()
            monkeypatch.setattr(module, "SSR_STEPS", 0)
            renderer_off = module.QualityFrameRenderer(soup3D.proj_width,
                                                       soup3D.proj_height)
            try:
                pack_frame(renderer_off, _FakeScreen(model).render_scene)
                screen_off = _read_screen()
            finally:
                renderer_off.dispose()
        finally:
            renderer_on.dispose()
        # 下半屏（镜面地板）：A/B 差异像素显著（反射只发生在合成通道）
        diff = np.abs(screen_on[120:, :, :3].astype(int)
                      - screen_off[120:, :, :3].astype(int)).max(axis=2)
        n_diff_bottom = int((diff > 6).sum())
        # 上半屏（纯天空区，行 0-95 避开立方体）：天空直通，差异应很小
        diff_top = np.abs(screen_on[:95, :, :3].astype(int)
                          - screen_off[:95, :, :3].astype(int)).max(axis=2)
        n_diff_top = int((diff_top > 6).sum())
        assert n_diff_bottom > 200, f"镜面地板应出现反射差异像素（{n_diff_bottom}）"
        assert n_diff_top <= 100, f"天空区不应有显著差异（{n_diff_top}）"
    finally:
        sun.destroy()
```

- [ ] 2. 运行确认失败（ACES 数学断言不成立：present 直通无色调映射；SSR A/B 无差异；契约未导出）：

```bash
cd /home/oren/project/codes/python/FlamingSkyFPV && python -m pytest test/test_shader_quality_gl.py -v
```

- [ ] 3. 修改 `shaderpacks/builtin/quality/pack.py`（八处改动，按顺序）：

  - 3a. import 块整体替换为：

```python
import weakref

import soup3D
from OpenGL.GL import (GL_ACTIVE_TEXTURE, GL_BLEND, GL_COLOR_BUFFER_BIT,
                       GL_COLOR_CLEAR_VALUE, GL_COMPILE_STATUS, GL_CURRENT_PROGRAM,
                       GL_DEPTH_BUFFER_BIT, GL_DEPTH_TEST, GL_DEPTH_WRITEMASK,
                       GL_DRAW_FRAMEBUFFER, GL_DRAW_FRAMEBUFFER_BINDING, GL_FALSE,
                       GL_FRAGMENT_SHADER, GL_FRAMEBUFFER, GL_LINK_STATUS,
                       GL_READ_FRAMEBUFFER, GL_READ_FRAMEBUFFER_BINDING,
                       GL_TEXTURE0, GL_TEXTURE1, GL_TEXTURE_2D,
                       GL_TEXTURE_BINDING_2D, GL_TRIANGLES, GL_TRUE,
                       GL_VERTEX_SHADER, GL_VIEWPORT, glActiveTexture,
                       glAttachShader, glBindFramebuffer, glBindTexture,
                       glCompileShader, glCreateProgram, glCreateShader,
                       glDeleteProgram, glDeleteShader, glDisable, glDrawArrays,
                       glEnable, glGetBooleanv, glGetFloatv, glGetIntegerv,
                       glGetProgramInfoLog, glGetProgramiv, glGetShaderInfoLog,
                       glGetShaderiv, glGetUniformLocation, glIsEnabled,
                       glLinkProgram, glShaderSource, glUniform1f, glUniform1i,
                       glUniformMatrix4fv, glUseProgram, glViewport)
from pyglm import glm
from soup3D.target import RenderTarget

import shaderpacks.api as api
```

  - 3b. 常量块追加（`DEFAULT_METALLIC` 之后）：

```python
EXPOSURE = 3.0                 # 合成曝光（Lambert/π 后整体偏暗的补偿，目检可调）
SSR_STEPS = 24                 # SSR 光线步进次数（性能预算超限时减小/置 0 关闭反射）
SSR_REFINE = 4                 # 命中后二分细化次数
SSR_THICKNESS = 0.5            # 命中判定厚度（米，视图空间）
SSR_MAX_DIST = 120.0           # 最大步进距离（米）
```

  - 3c. 常量块之后、`_ShadowRegistry` 类之前插入合成通道 GLSL 与裸 GL 程序编译辅助：

```python
# 合成通道片段着色器（SSR + 曝光 → ACES → gamma，一个全屏通道直绘默认帧缓冲）。
# Python % 格式化：仅两个 %d（SSR 步数/细化次数），GLSL 正文不含其他 % 字符
_COMPOSITE_FS = """
#version 330 core
#define SSR_STEPS %d
#define SSR_REFINE %d

in vec2 UV;
out vec4 FragColor;

uniform sampler2D hdrColor;   // 单元0：HDR 颜色
uniform sampler2D hdrDepth;   // 单元1：HDR 深度（0..1）
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

    // SSR：深度=1 的天空像素直通；反射方向朝屏幕外（R.z>=0）跳过
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
                // 屏幕边缘淡出 + 菲涅尔加权混合
                vec2 border = min(hitUV, 1.0 - hitUV);
                float edge = clamp(min(border.x, border.y) * 8.0, 0.0, 1.0);
                color = mix(color, texture(hdrColor, hitUV).rgb, fresnel * edge);
            }
        }
    }

    // 曝光 → ACES → gamma 2.2
    color = aces(color * exposure);
    FragColor = vec4(pow(color, vec3(1.0 / 2.2)), 1.0);
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
```

  - 3d. `QualityFrameRenderer.__init__` 整体替换为（追加合成通道资源）：

```python
    def __init__(self, width, height):
        self._shadow_rt = RenderTarget(SHADOW_SIZE, SHADOW_SIZE, "rgba8", "texture")
        self._hdr_rt = RenderTarget(width, height, "rgba16f", "texture")
        # DepthSurface 按 Face 对象弱引用缓存（face 释放自动清理，规避 id 复用风险）
        self._depth_surfaces = weakref.WeakKeyDictionary()
        # 合成通道：全屏三角辅助 + 以当前常量编译的程序（SSR_STEPS 可在构造前
        # monkeypatch 做 A/B；A/B 测试即依赖此语义）
        self._composite_pass = api.FullScreenPass()
        self._composite_prog = _compile_program(api.FULLSCREEN_VS,
                                                _COMPOSITE_FS % (SSR_STEPS, SSR_REFINE))
```

  - 3e. `render_frame` 整体替换为（末段 present 换合成通道）：

```python
    def render_frame(self, draw_scene):
        has_sun = self._update_shadow_registry(draw_scene.faces)
        if has_sun:
            self._render_shadow_pass(draw_scene.faces)
        self._render_main_pass(draw_scene)
        self._render_composite()
```

  - 3f. `dispose` 整体替换为：

```python
    def dispose(self):
        """GL 上下文销毁前释放：删 RT/合成程序/全屏 VAO（引擎同模式，失败静默）"""
        self._depth_surfaces.clear()
        self._shadow_rt = None
        self._hdr_rt = None
        try:
            if self._composite_prog is not None:
                glDeleteProgram(self._composite_prog)
                self._composite_prog = None
        except Exception:
            pass
        if self._composite_pass is not None:
            self._composite_pass.dispose()
            self._composite_pass = None
```

  - 3g. `_render_main_pass` 方法之后追加合成通道方法：

```python
    def _render_composite(self):
        """合成通道：全屏三角直绘默认帧缓冲——HDR 颜色 + 深度重建做 SSR，
        曝光 → ACES → gamma 后上屏。GL 状态卫生照 RenderTarget.present 的
        保存/恢复模式。"""
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
        glUniform1i(glGetUniformLocation(prog, "hdrColor"), 0)
        glUniform1i(glGetUniformLocation(prog, "hdrDepth"), 1)
        proj = soup3D.get_projection_mat()
        glUniformMatrix4fv(glGetUniformLocation(prog, "proj"), 1, GL_FALSE,
                           glm.value_ptr(proj))
        glUniformMatrix4fv(glGetUniformLocation(prog, "invProj"), 1, GL_FALSE,
                           glm.value_ptr(glm.inverse(proj)))
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
```

  - 3h. 文件末尾（`surface_class = PBRSurface` 之后）追加帧渲染器契约导出：

```python

# 帧渲染器契约（spec 4.3）：游戏以当前窗口尺寸实例化，每帧经 pack_frame 调用；
# 引擎光源/EAU 由游戏 runtime 在调用前自动处理，包无需自调
def create_frame_renderer(width, height):
    return QualityFrameRenderer(width, height)
```

- [ ] 4. 追加无 GL 契约测试到 `test/test_shader_packs.py` 末尾：

```python
# ---- 画面优先包契约（Task 12，无 GL）----

def test_quality_pack_contract():
    """load_pack(quality)：create_frame_renderer 可调用、surface_class 为 PBRSurface、
    skinned 缺省 = surface_class（模块级 import 不触 GL）"""
    info = next(p for p in list_packs() if p.id == "builtin_quality" and p.valid)
    loaded = load_pack(info)
    assert callable(loaded.create_frame_renderer)
    assert loaded.surface_class.__name__ == "PBRSurface"
    assert loaded.skinned_surface_class is loaded.surface_class
```

- [ ] 5. 运行确认通过（GL 全绿 + 无 GL 契约 + 全套件回归）：

```bash
cd /home/oren/project/codes/python/FlamingSkyFPV && python -m pytest test/test_shader_quality_gl.py test/test_shader_packs.py -v && python -m pytest test/ -v
```

- [ ] 6. 提交（只含 quality 包）：

```bash
cd /home/oren/project/codes/python/FlamingSkyFPV && git add shaderpacks/builtin/quality/pack.py && git commit -m $'feat: 画面优先包：SSR + ACES 合成通道与契约导出\n\nCo-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>'
```
## Task 13：端到端手动验收脚本 test/manual/smoke_shader_quality.py

### Files

- 新增：`/home/oren/project/codes/python/FlamingSkyFPV/test/manual/smoke_shader_quality.py`（test/ 被 .gitignore 忽略，**本任务无源码提交**）

### Interfaces

- Consumes：`App.reload_shader_preset` / `App._frame_renderer`（Task 7）、`shaderpacks.runtime.pack_frame`（Task 5）、`App.scenes` / `FlightScreen`（现有）；`scene/` 本地场景（用户内容，如 bando_factory）。
- Produces：真实飞行界面整帧渲染（画面优先包全管线），第 N 帧截图 `smoke_shader_quality.png` 供人工目检（spec 8：阴影 / 反射 / 色调映射人工目检辅助）。

### 背景

spec 8 GL 手动测试约定（test/manual/，需显示环境）。脚本复刻 `app.run()` 的帧序列（含 Task 7 的预设渲染分支），内存直切 `settings.shader_preset`（不写盘，不影响用户真实配置）。结构与 `test/manual/smoke_flight.py` 一致。

### Steps

- [ ] 1. 新建 `test/manual/smoke_shader_quality.py`（完整脚本）：

```python
"""画面优先包端到端手动验收：真实进飞行界面渲染，保存截图人工目检

用法: python test/manual/smoke_shader_quality.py [帧数] [截图帧]
与 test/manual/smoke_flight.py 同帧序列，但渲染段走 App 的预设分支（pack_frame），
并把 settings.shader_preset 内存直切 builtin_quality（不写盘，不影响真实配置）。
断言留人工：阴影 / 反射 / HDR-ACES 色调映射属视觉验收（spec 第 8 节）。
需本地场景（scene/ 为用户内容，如 bando_factory）。llvmpipe（无 GPU）环境会
显著掉帧，属预期（spec 7），目检画面正确性即可。
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # 项目根

import pygame
import imgui
import soup3D

from shaderpacks import runtime


def main():
    frames = int(sys.argv[1]) if len(sys.argv) > 1 else 240
    shot_at = int(sys.argv[2]) if len(sys.argv) > 2 else 20

    from app import App
    from screens.flight import FlightScreen

    app = App()
    # 内存直切画质预设（不写盘，不影响用户真实配置）
    app.settings.shader_preset = "builtin_quality"
    app.reload_shader_preset()
    assert app._frame_renderer is not None, \
        "画面优先包未实例化帧渲染器（GL 环境异常？检查控制台回落栈）"
    fs = FlightScreen()
    app.switch(fs)
    fs._scene = next(s for s in app.scenes if s.valid)
    fs.stage = "loading"   # 下一帧 update 走 _load_scene

    t_start = time.perf_counter()
    for i in range(frames):
        dt = app.clock.tick(60) / 1000.0
        pygame.event.get()
        app.screen.update(dt)

        # 与 app.run() 渲染分支一致：预设帧渲染器接管整帧
        if app._frame_renderer is not None:
            fr = app._frame_renderer
            soup3D.update(render=lambda: runtime.pack_frame(fr, app.screen.render_scene))
        else:
            soup3D.update()
            app.screen.render_scene()

        imgui.new_frame()
        if app.font:
            imgui.push_font(app.font)
        app.screen.render_ui()
        if app.font:
            imgui.pop_font()
        imgui.render()
        app._impl.render(imgui.get_draw_data())

        if i == shot_at:
            # glReadPixels 读 back buffer 存 PNG（合成结果含 ACES/gamma）
            from OpenGL.GL import glReadPixels, GL_RGBA, GL_UNSIGNED_BYTE
            import numpy as np
            import imageio.v2 as imageio
            surf = pygame.display.get_surface()
            w, h = surf.get_size()
            raw = glReadPixels(0, 0, w, h, GL_RGBA, GL_UNSIGNED_BYTE)
            img = np.frombuffer(raw, dtype=np.uint8).reshape(h, w, 4)[::-1]
            imageio.imwrite("smoke_shader_quality.png", img)

        pygame.display.flip()
    print(f"OK: {frames} 帧跑完，总耗时 {time.perf_counter()-t_start:.1f}s，"
          f"截图 smoke_shader_quality.png（第 {shot_at} 帧）；请目检：阴影边缘、"
          f"地板反射、HDR/ACES 色调映射")


if __name__ == "__main__":
    main()
```

- [ ] 2. 运行脚本（需本地场景与 X 显示）：

```bash
cd /home/oren/project/codes/python/FlamingSkyFPV && python test/manual/smoke_shader_quality.py 120 20
```

- [ ] 3. 人工目检 `smoke_shader_quality.png`：物体投出软阴影（PCF 边缘）、低粗糙度地面/金属面有屏幕空间反射、整体色调映射无过曝死白与暗部截断；同时确认飞行 HUD 正常叠加。异常时对照控制台回落栈（`reload_shader_preset` / run 兜底打印）定位。
- [ ] 4. 本任务无 git 提交（test/ 不入版本库）。

---

## Task 14：文档交付——docs/shader_packs.md + README.md + .gitignore

### Files

- 新增：`/home/oren/project/codes/python/FlamingSkyFPV/docs/shader_packs.md`
- 修改：`/home/oren/project/codes/python/FlamingSkyFPV/README.md`（功能清单追加一段）
- 修改：`/home/oren/project/codes/python/FlamingSkyFPV/.gitignore`（追加 shader_packs/）

### Interfaces

- Consumes：Task 2-12 的最终实现形态（契约、常量、内置包源码位置）。
- Produces：包作者指南（spec 9 全部条目：信任模型声明、目录结构、pack.json 字段、pack.py 契约、最小可行示例、进阶指引、stable_shapes 契约说明）；README 功能段；shader_packs/ 忽略规则。

### Steps

- [ ] 1. 新建 `docs/shader_packs.md`（全文如下）：

````markdown
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
  顶点），写 SSR/合成通道直接用，不必自管顶点缓冲。
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
````

- [ ] 2. `README.md` 在「## 场景格式」段之前插入功能段：

```markdown
## 着色器预设

设置页「画质预设」可切换着色器预设包：内置「性能优先」（默认管线）与「画面优先」（PBR + 太阳软阴影 + SSR 反射 + HDR/ACES 色调映射）。自制或下载的预设包放入 `shader_packs/<包名>/`（`pack.json` + `pack.py`），在设置页选择即可——详见 `docs/shader_packs.md`（注意：运行第三方包等于执行任意代码，只装可信来源）。

```

- [ ] 3. `.gitignore` 末尾追加：

```gitignore

# 用户着色器预设包
shader_packs/
```

- [ ] 4. 验证文档内示例语法可解析（不执行，仅编译检查）：

```bash
cd /home/oren/project/codes/python/FlamingSkyFPV && python - <<'PY'
import ast
import re

text = open("docs/shader_packs.md", encoding="utf-8").read()
blocks = re.findall(r"```python\n(.*?)```", text, re.S)
assert blocks, "未找到 python 代码块"
for i, b in enumerate(blocks):
    ast.parse(b)
print(f"docs/shader_packs.md：{len(blocks)} 个 python 代码块语法 OK")
PY
```

- [ ] 5. 提交（docs + README + .gitignore）：

```bash
cd /home/oren/project/codes/python/FlamingSkyFPV && git add docs/shader_packs.md README_zh.md .gitignore && git commit -m $'docs: 着色器预设包作者指南与 README/.gitignore\n\nCo-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>'
```

---

## Task 15：最终回归与清点（无提交）

### Steps

- [ ] 1. 全量测试回归：

```bash
cd /home/oren/project/codes/python/FlamingSkyFPV && python -m pytest test/ -v
```

预期全绿（原 71 项 + 本计划新增项；`test_shader_packs.py` 无 GL 段 + 设置页 UI 段、`test_shader_quality_gl.py` GL 段、`test_flight_select_ui.py` 注入回归）。

- [ ] 2. 性能优先冒烟（默认配置，行为与现状一致）：

```bash
cd /home/oren/project/codes/python/FlamingSkyFPV && python main.py --frames 120
```

- [ ] 3. 画面优先端到端（截图人工目检，见 Task 13）：

```bash
cd /home/oren/project/codes/python/FlamingSkyFPV && python test/manual/smoke_shader_quality.py 120 20
```

- [ ] 4. 提交清点：`git log --oneline` 应见本计划 12 次提交（Task 1-12、14 各一次；Task 13/15 无提交），`git status --short` 除既有未跟踪的 `docs/plans/2026-11-10-shader-presets.md` 外无计划外改动；确认全程未改动 `soup3D/`、`docs/specs/`、`docs/acceptance-phase1.md`、`CLAUDE.md`。
- [ ] 5. 性能预算抽查（用户离散 GPU 上，1080p，spec 7）：设置切「画面优先」实飞 bando_factory，观察垂直同步下不掉帧（60fps）；不达标时按 `shaderpacks/builtin/quality/pack.py` 顶部常量调优（SSR 半分辨率/减步数、阴影尺寸）。llvmpipe 环境不以此为准（文档已注明）。
