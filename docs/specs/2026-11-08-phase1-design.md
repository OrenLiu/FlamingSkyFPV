# FlamingSkyFPV 一期设计文档

日期：2026-11-08
状态：已经用户批准（方案 A：Screen 状态机 + imgui 全权 UI）

## 1. 背景与目标

FlamingSkyFPV 是开源 FPV 穿越机模拟器，核心特色：一切皆可自定义（场景、赛道、障碍），调参环境仿真（二期通过虚拟 serial 设备接入真实地面站）。一期交付一个可飞的花式飞行模拟器：主界面、场景化花式飞行、设置、无人机参数（全参数可调且真实影响飞行手感）、遥控通道设置（HID 手柄，手动 + 向导两种配置方式）。

**一期不做**：竞速模式（入口灰置）、地面站 serial 打通、网络/录像/回放、电机惯性响应、空气动力学细节（仅线性阻力）。

## 2. 技术选型与运行环境

| 组件 | 选型 | 说明 |
|---|---|---|
| 渲染 | soup3D 7.0.0（已装） | 自研 OpenGL 引擎，pygame 承载窗口与事件 |
| 窗口/事件 | pygame 2.6.1（已装） | 唯一主循环载体 |
| UI | pyimgui 2.0.0（已装） | PygameRenderer 集成，所有界面含主菜单 |
| 物理 | PyBullet（**开发环境待装**：`pip install pybullet`） | 运行时不做依赖自检，发布时环境随包分发 |
| Python | 系统 pyenv 3.10.6 | 用户选定，不建项目 venv |

引擎 API 参考文档：`/home/oren/project/codes/python/#tools/soup3D/docs/`（路径含 `#`，shell 中需引号）。

## 3. 总体架构

```
main.py               # 入口：pygame → imgui → soup3D 初始化，创建 App
app.py                # App：主循环 + 当前 Screen 切换；持有 AppContext
context.py            # AppContext：三份配置 + 场景列表 + 输入设备状态
screens/
  base.py             # Screen 抽象基类
  main_menu.py        # 主界面
  flight.py           # 场景选择 → 飞行；Esc 暂停菜单是 flight 内嵌 imgui 弹窗
  settings.py         # 设置
  drone_params.py     # 无人机参数
  rc_setup.py         # 遥控通道设置（手动/向导）
physics/
  world.py            # PyBullet 世界：刚体、场景碰撞网格、坐标转换
  drone.py            # 飞控仿真：rate 映射 → PID → 混控 → 电机推力（纯计算，不依赖 GL）
input/
  channels.py         # RCInput 协议 + Channels 数据类
  joystick.py         # pygame.joystick HID 实现
  keyboard.py         # 键盘兜底实现
scene_loader.py       # 枚举/校验 scene/*/config.json
config_store.py       # JSON 持久化
```

依赖方向严格单向：`screens → physics/input/scene_loader → config_store`。`physics/` 与 `input/` 不 import 渲染/UI 库，可独立单测。

App 只维护**当前 Screen**（非栈式）；飞行内 Esc 菜单、场景选择均为 flight screen 内部子状态。

## 4. 飞行仿真核心

### 每帧数据流

```
RCInput.read() → Channels(throttle∈[0,1], yaw/roll/pitch∈[-1,1])
  → drone.py 飞控仿真（每个 bullet substep 执行，240Hz）:
      ① rate 映射：rc 值 → 目标角速度 (°/s)
      ② PID：目标 vs PyBullet 实测角速度 → 三轴修正
      ③ 混控：quad-X → 4 电机油门 [0,1]
      ④ 推力：T_i = T_max · throttle_i²，施加于机体四臂位置
  → bullet stepSimulation（1/240 substep × 每帧 4 次）
  → 刚体位姿 → world.py 坐标转换 + 摄像头仰角合成
  → soup3D.camera.goto() + turn_quat() → soup3D.update() → imgui HUD
```

### 物理模型（一期保真度）

- **刚体**：质量 = 无人机参数.重量；盒形惯性矩（按 5 寸机尺寸标定）
- **rate 映射**：`betaflight_actual`（rcRate/superRate/expo 完整曲线）与 `linear`（简单比例）两种
- **PID**：角速度闭环，roll/pitch/yaw 三轴独立 P/I/D；每 substep 更新（240Hz）
- **电机**：单电机最大推力 T_max = 推重比 × 重量 / 4，实际推力 = T_max × motor²；KV 通过决定推重比的推荐默认值影响飞行（thrust_ratio 默认 = kv / 250，如 1750KV → 7），用户可在参数页自由覆盖
- **阻力**：F = -k·v 线性阻力，k 标定至平飞极速量级 ~30 m/s
- **摄像头角度**：仅视角合成（机体姿态绕 pitch 旋转 camera_angle），不改物理
- **碰撞**：场景 OBJ → bullet 三角网格碰撞体 + 无限平面地面；碰撞解算全交 PyBullet
- **重置**：回到出生点（场景原点上方，速度清零）

### 坐标系约定

PyBullet 为 Z-up 右手系；soup3D 为 OpenGL 惯例（Y-up，相机默认视向 -Z）。`world.py` 内建唯一转换层（bullet→渲染：x=x, y=z, z=-y，实现期以实际场景验证），相机四元数同步经同一转换，禁止在多处散写转换。

### 无人机默认参数（5 寸机典型值，仿真标定）

weight=550g、motor_kv=1750、thrust_ratio=8、camera_angle=20°、rate=betaflight_actual (rcRate 1.0 / superRate 0.70 / expo 0.32)、PID roll(P45/I80/D35) pitch(P50/I90/D38) yaw(P60/I90/D0)。默认值集中于 `drone.py` 常量，参数页提供"恢复默认"。

## 5. 输入系统

- `RCInput` 协议：`read() -> Channels`；手柄/键盘两个实现可互换，飞行代码不感知来源
- **键盘兜底**（固定映射，不可配置）：W/S 油门（保持式——按住每秒 ±100% 增减油门值，松开保持）、A/D 偏航、↑↓ 俯仰、←→ 横滚（三者均为瞬态——按下满杆、松开回中，等效expo=0的方波输入）
- **手动配置**：选 HID 设备 → 每通道绑定轴 + 反向开关
- **配置向导**：选遥控类型（美国手/中国手/日本手）→ 依次提示操作每根杆 → 捕获位移最大轴自动绑定 → 验证页确认各通道响应
- 无手柄/手柄拔出：自动切键盘并在 UI 明示"键盘模式"，不静默降级

## 6. 界面规格

| 界面 | 规格 |
|---|---|
| 主界面 | 标题 FlamingSkyFPV 居中；左侧按钮列：开始飞行 / 设置 / 无人机参数 / 遥控通道设置 / 退出 |
| 开始飞行 | 花式飞行（可选）、竞速飞行（灰置）；→ 场景列表（显示 config.name）→ 进入 |
| 飞行 | 3D + 透明 HUD（高度/水平速度/姿态角/油门）；Esc 暂停：继续 / 重置 / 返回主菜单 |
| 设置 | 分辨率下拉、全屏开关；保存即写盘并热重建窗口；GL 上下文重建失败时回落"重启后生效"并提示 |
| 无人机参数 | Rate 类型+三参数、三轴 PID、摄像头角度、重量、KV、推重比；滑条+数值输入；恢复默认 |
| 遥控通道设置 | 手动/向导双入口 + 当前绑定一览 |

## 7. 配置持久化

`config/`（加入 .gitignore），首启动无文件自动以默认值生成：

- `settings.json`：`{"resolution": [1280, 720], "fullscreen": false}`
- `drone.json`：第 4 节全部参数 + `{"rate_type": "...", "pid": {"roll": {"p":..,"i":..,"d":..}, ...}}`
- `rc.json`：`{"device": 名字或null, "mode": "keyboard|joystick", "mappings": {"throttle": {"axis": 1, "inverted": false}, ...}}`

场景约定：`scene/<名>/config.json` 必填 `name`（显示名）、`model`（OBJ 文件名）；可选 `spawn`（出生点，默认原点上方 2m）。

## 8. 错误处理

| 情形 | 行为 |
|---|---|
| 场景 config.json 缺失/非法 | 场景列表跳过该项并标注"无效" |
| OBJ/贴图加载失败 | 提示后返回场景选择 |
| 无手柄 / 运行中拔出 | 切键盘模式并 UI 明示 |
| 配置文件损坏 | 忽略并重建默认（备份原文件为 .bak） |

## 9. 测试策略

- **pytest 单测**（不依赖 GL/bullet，`test/` 本地存在、按用户 gitignore 约定不入库）：rate 映射曲线端点/单调性、PID 收敛行为、混控输出与钳位、config round-trip、scene_loader 校验、坐标转换层
- **手动冒烟**：imgui+soup3D 同窗 GL 共存（实现期第一个验证点，先于铺开 UI）、飞行手感、参数修改即时机身反应、手柄向导全流程

## 10. 风险

1. **imgui 与 soup3D 的 GL 状态共存**（渲染顺序、纹理/着色器状态互踩）——最高风险，实施第一步先做最小验证
2. **分辨率/全屏热切换**需重建 GL 上下文（soup3D 重 init、imgui 字体图集重上传、场景模型重载）——有降级路径（重启生效）
3. **大 OBJ 场景**加载耗时与 bullet 碰撞网格构建耗时——进入飞行时显示加载提示；模型仍由 soup3D 一次性加载
4. **坐标系/四元数约定差异**——集中转换层 + 单测锁定
