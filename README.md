# FlamingSkyFPV

[English](README_en.md) | [简体中文](README.md)

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.10%20%7C%203.11-3776AB?logo=python&logoColor=white)](https://www.python.org/)
![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey)

[![Rendering](https://img.shields.io/badge/Rendering-soup3D-E34F26)](https://pypi.org/project/soup3D/)
[![Physics](https://img.shields.io/badge/Physics-PyBullet-76B900)](https://pybullet.org/)
[![UI](https://img.shields.io/badge/UI-Dear%20ImGui-20B2AA)](https://github.com/ocornut/imgui)
[![Window & Input](https://img.shields.io/badge/Window%20%26%20Input-pygame-6C5CE7)](https://www.pygame.org/)

开源 FPV 穿越机模拟器，一切皆可自定义：场景、赛道、障碍均可导入；参数全开放（Rate / PID / 重量 / KV / 摄像头角度），改了就飞。

## QQ交流群
欢迎加入模拟器交流群！如果您对我们的模拟器有任何看法，都可以进群交流！  
群号：[1137017281](https://qm.qq.com/q/NcUYOhlHG0)

## 兼容设备
支持 64 位的 Windows、macOS 与 Linux。

### 最低配置
- **CPU**  
  双核处理器（Intel Core i3 / AMD Ryzen 3 级别）
- **GPU**  
  支持 OpenGL 3.3 的显卡，集成显卡即可
- **内存**  
  4 GB
- **遥控/手柄**  
  支持`HID`协议的遥控或手柄

### 推荐配置
- **CPU**  
  四核及以上处理器（Intel Core i5 / AMD Ryzen 5 级别）
- **GPU**  
  独立显卡，或较新的集成显卡（「画面优先」画质预设包含 PBR、软阴影与屏幕空间反射，对显卡要求更高）
- **内存**  
  8 GB
- **遥控/手柄**  
  支持`HID`协议的遥控或手柄

## 运行
1. 在该项目的 `Releases` 中根据自己的操作系统下载对应版本的文件
2. 解压下载下来的文件
3. 运行可执行文件(如windows，可执行文件为`FlamingSkyFPV.exe`)

## 自行构建
1. **准备环境**  
   安装 Python 3.10 或 3.11（开发与打包所用版本为 3.10；依赖 imgui 2.0.0 的预编译包只提供到 3.11 和 x86_64 版 macOS，更高版本的 Python 或 Apple Silicon 需要自行编译它）。建议使用干净的虚拟环境，然后在仓库根目录安装依赖：
   ```bash
   pip install pybullet pygame imgui "soup3D>=7.1" Pillow
   ```
   soup3D 是渲染引擎，安装时会自动带上它的依赖 PyOpenGL、pyglm、numpy、imageio；Pillow 由「画面优先」画质预设使用。PyInstaller 无需手动安装，构建脚本检测到缺失时会自动安装。装好依赖后，在仓库根目录运行`python main.py`即可直接启动，不必打包
2. **运行构建脚本**  
   运行`python build.py`即可开始构建可执行文件  
   可选参数：`--console`（控制台版，便于排障）、`--no-scene`（不随包携带场景）、`--zip`（构建后压缩为 zip）
3. **获取产物**  
   最终产物将存放在`dist`文件夹下
   - Windows：`dist/FlamingSkyFPV/FlamingSkyFPV.exe`
   - macOS：`dist/FlamingSkyFPV.app`
   - Linux：`dist/FlamingSkyFPV/FlamingSkyFPV`

   注意：PyInstaller 无法交叉编译，要得到哪个平台的可执行文件，就需要在哪个平台上构建

## 注意：首次执行需对遥控器/手柄进行初始设置！
1. 依次进入`遥控通道设置 > 启动配置向导`
2. 选择您的遥控器类型，通常，左手油门为美国手
3. 根据提示信息操作遥控器进行配置
4. 操作完成后，拨动摇杆，查看对应通道是否向正确的方向移动

## 模拟器介绍

FlamingSkyFPV 是一款开源 FPV 穿越机模拟器，主打「一切皆可自定义」。它模拟的不只是飞行：场景、赛道、障碍都能自己导入，调参环节也被完整模拟——程序可以化身为一台「虚拟飞控」，让你用真实的 Betaflight 地面站给模拟器里的无人机调参。

### 功能特性

- **场景自定义**：场景、赛道、障碍都是以文件夹组织的场景单元，放进`scene/`目录并重新启动程序，即可出现在飞行列表中。
- **参数全开放**：Rate、PID、TPA、摄像头角度、重量、电机 KV、电池、空气阻力等全部可调，改了就飞；支持多套无人机预设，可新建、复制、重命名、删除并随时切换。
- **两种调参方式**：「参数模式」直接在程序内调参；「虚拟飞控模式」把 Rate / PID / TPA 交给 Betaflight 地面站，保存后即时生效。
- **真实电池模拟**：按电池类型、节数、容量与放电倍率模拟电压跌落和电量消耗，电压过低时 OSD 报警。
- **Betaflight 风格 OSD**：虚拟飞控模式下显示 MAX7456 字符网格 OSD，显示哪些元素、放在什么位置都在地面站中编辑。
- **遥控器 / 手柄**：支持 HID 协议的遥控或手柄，既可以用「配置向导」逐步引导，也可以在「通道绑定」中手动指定每个通道，支持热插拔。
- **画质预设与着色器包**：内置「性能优先」与「画面优先」两种预设，也可以加载自制的着色器包。
- **跨平台**：同一套代码在 Windows、macOS、Linux 上运行，发行版解压即可使用，无需安装。

### 主菜单

| 菜单 | 说明 |
| --- | --- |
| 开始飞行 | 选择场景开始飞行。目前提供花式飞行，竞速飞行计划在后续版本加入 |
| 设置 | 显示模式（全屏 / 固定大小窗口化 / 窗口化）、分辨率、最大帧率、画质预设 |
| 无人机参数 | 管理无人机预设、调整飞行参数、接入地面站 |
| 遥控通道设置 | 配置遥控器 / 手柄各通道，或启动配置向导 |
| 退出 | 关闭程序 |

### 飞行操作

- **`F3`**：显示或隐藏调试信息（帧率与相机位置），任何界面都可用。
- **`Esc`**：打开暂停菜单——「继续飞行」「重置位置」「返回主菜单」。「重置位置」会回到出生点、自动上锁，并把电池恢复为满电。

**参数模式**下没有解锁步骤，推油门即起飞。**虚拟飞控模式**与真机一致：需要打开 ARM 开关才能解锁，且解锁时油门必须低于 10%；若因油门过高被拒绝，要先关闭再打开 ARM 开关才能重新解锁。出厂预设把 ARM 绑定在 AUX1、反乌龟（FLIP OVER AFTER CRASH）绑定在 AUX3、ANGLE（自稳）绑定在 AUX4，对应关系可以在地面站 Modes 页修改。

模拟器不检测坠机：炸机后电机会继续运转，需要手动上锁、用反乌龟翻面，或「重置位置」。

### 调参

「无人机参数」页以列表管理所有无人机预设：点击一行切换当前无人机，行内可「编辑」「复制」「删除」，也可以「+新建无人机」。可调参数如下：

| 分类 | 参数 |
| --- | --- |
| Rate | Rate 类型：`betaflight_actual`（Betaflight Actual Rates）或`linear`（线性，另有「线性满杆°/s」一项）；横滚、俯仰、偏航三轴各自的 RC Rate、Super Rate、Expo |
| PID | 三轴各自的 P、I、D，增益为 Betaflight 量级 |
| TPA | 衰减百分比、断点 |
| 机体 | 重量、电机 KV、推重比、空气阻力、摄像头角度 |
| 电池 | 「真实电池模拟」开启时：电池类型（LiPo / LiHV / Li-ion）、节数、容量、放电倍率、警告 / 最低 / 最高单芯电压；关闭时直接设置电池电压 |

每台无人机有两种调参模式，在编辑态底部切换：

- **参数模式**：上表中的参数都在界面里直接调整。
- **虚拟飞控模式**：点「转换为虚拟飞控」并确认后，Rate / PID / TPA 转交给地面站，界面只保留机体、电池等硬件参数；点「转换为参数模式」可把地面站的调参结果写回界面；警告 / 最低 / 最高单芯电压此时只能在地面站的 Power & Battery 页修改。出厂预设中的`virtual_FC`就处于这个模式。

开启「真实电池模拟」后，推力会随电压下降而衰减，电量按电流积分消耗，可以一直放到空；虚拟飞控模式的 OSD 会在单芯电压低于警告电压时闪 LOW BATTERY，低于最低电压时闪 LAND NOW（参数模式的 HUD 不显示电量）。每次进入飞行或「重置位置」都相当于换上一块满电电池。

### 用 Betaflight 地面站调参

1. 在「无人机参数」页选中一台无人机并点「编辑」；如果它还不是虚拟飞控模式，点「转换为虚拟飞控」并确认。
2. 点「接入地面站」。屏幕上会出现常驻的「地面站连接」浮窗，列出可用的连接地址（Windows 上没有 PTY 串口这一项），任选其一：

   | 通道 | 地址 | 适用形态 |
   | --- | --- | --- |
   | TCP | `tcp://127.0.0.1:5761` | 桌面版 Betaflight Configurator |
   | WebSocket | `ws://127.0.0.1:5762` | 浏览器版 Betaflight Configurator |
   | PTY 串口 | 浮窗中显示的路径（Linux 形如`/dev/pts/N`，macOS 形如`/dev/ttysNNN`） | 串口工具，仅 Linux / macOS |

3. 在地面站中开启 **Expert Mode**，设备选「手动选择」并填入上面的地址，再点 Connect。浏览器版默认会自动连接它内置的演示飞控，务必手动选择。
4. 之后可以在 PID tuning、Rates、Modes、OSD、Power & Battery 等页面调参，保存后模拟器即时生效。点浮窗中的「断开」或退出程序即可断开连接。

模拟器会把自己标识为 Betaflight 4.5.1 飞控。虚拟飞控模式飞行时，界面显示 Betaflight 风格的 OSD，显示哪些元素、放在什么位置都在地面站的 OSD 页编辑。

### 导入场景

`scene/`目录下每个子文件夹是一个场景，放入后重新启动程序，就会出现在「开始飞行」的场景列表中：

```
scene/<场景名>/
├── config.json      # 场景配置
├── <模型>.obj       # 主模型，与配套的 .mtl 材质文件一起使用
├── <贴图>           # 由 .mtl 引用
└── <封面图>         # 可选，场景列表中显示的封面
```

`config.json`示例：

```json
{
  "name": "我的场景",
  "model": "map.obj",
  "cover": "cover.png",
  "scale": 1.0,
  "spawn": [0, 0, 2],
  "sky": { "sun_yaw": 120, "sun_pitch": -30, "sun_color": [1.0, 0.6, 0.2] }
}
```

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `name` | 是 | 场景显示名 |
| `model` | 是 | 场景目录内的 OBJ 模型文件名，渲染与碰撞都用它 |
| `cover` | 否 | 封面图文件名，相对场景目录 |
| `scale` | 否 | 模型缩放倍数，默认`1.0` |
| `spawn` | 否 | 出生点`[x, y, z]`，单位米，默认`[0, 0, 2]` |
| `sky` | 否 | `sun_yaw`、`sun_pitch`为太阳角度（度，默认`30`、`-60`）；`sun_color`为太阳颜色`[r, g, b]`；`panorama`为场景目录内等距圆柱全景图的文件名，用作「画面优先」预设的天空盒，缺省为程序生成的天空 |
| `sun_toward` | 否 | 顶层的简写：太阳方向`[yaw, pitch]`（度），等价于`sky`里的`sun_yaw`与`sun_pitch`；`sky`里显式写了就以`sky`为准 |
| `sun_color`、`ambient` | 否 | 顶层的简写：太阳颜色与环境光颜色`[r, g, b]`，默认`[0.8, 0.8, 0.75]`与`[0.45, 0.45, 0.45]` |

模型目前只支持 OBJ + MTL 格式。`config.json`不合法或缺少模型文件的场景会标注为「无效」并被跳过，不会从列表里消失。场景放在哪里见下文「数据目录」。

### 画质预设与着色器包

在「设置 → 画质预设」中切换着色器预设包：

| 预设 | 效果 |
| --- | --- |
| 性能优先（默认） | 默认渲染管线，性能最好 |
| 画面优先 | PBR 材质 + 太阳软阴影 + SSR 反射 + HDR/ACES 色调映射 |

自制或下载的预设包放入`shader_packs/<包名>/`（包含`pack.json`与`pack.py`），重新启动程序后在设置页中选择，下次进入飞行场景时生效。编写方法见[着色器预设包作者指南](docs/shader_packs.md)。注意：运行第三方`pack.py`等于执行任意代码，本程序不做沙箱，只安装来源可信的包。

### 数据目录

设置、无人机预设、遥控配置、场景、自制着色器包和日志都存放在同一个数据目录下：

| 路径 | 内容 |
| --- | --- |
| `config/settings.json` | 设置 |
| `config/rc.json` | 遥控通道配置 |
| `config/drones/` | 无人机预设 |
| `scene/` | 场景 |
| `shader_packs/` | 自制着色器包 |
| `logs/` | 运行日志，每次启动生成一个文件 |

数据目录的位置：源码运行时是仓库根目录；Windows 与 Linux 发行版是可执行文件所在目录（该目录不可写时改用用户数据目录）；macOS 是`~/Library/Application Support/FlamingSkyFPV/`。也可以用环境变量`FLAME_DATA_DIR`直接指定。

### 已知限制

- 竞速飞行尚未实现，目前只有花式飞行。
- 帧时长超过 1/30 秒时物理步长会被钳位以防穿透，所以帧率过低时动作会比真实时间慢。
- PID 模拟不包含前馈（Feedforward）、D-Min 与 I-term Relax，在地面站里调整这些参数不会生效。
- 设置中的「垂直同步」目前只在 Linux（X11）下生效，在 Windows 与 macOS 上选择它等同于「无限制」。

## 许可协议

本项目基于 [MIT License](LICENSE) 开源，Copyright (c) 2026 刘申怿。任何人都可以自由使用、复制、修改、分发与商用，但须在副本中保留版权声明与许可声明；软件按「原样」提供，不附带任何担保。

本项目基于以下开源项目构建，在此致谢：

| 项目 | 用途 | 许可协议 |
| --- | --- | --- |
| [soup3D](https://pypi.org/project/soup3D/) | 3D 渲染引擎 | MIT |
| [PyBullet](https://pybullet.org/) | 物理引擎 | zlib |
| [Dear ImGui](https://github.com/ocornut/imgui) / [pyimgui](https://github.com/pyimgui/pyimgui) | 界面 | MIT / BSD-3-Clause |
| [pygame](https://www.pygame.org/) | 窗口与输入 | LGPL-2.1 |
| [NumPy](https://numpy.org/) | 数值计算 | BSD-3-Clause |
| [PyOpenGL](https://pyopengl.sourceforge.io/) | OpenGL 绑定 | BSD |
| [PyGLM](https://github.com/Zuzu-Typ/PyGLM) | 数学库 | zlib |
| [imageio](https://imageio.github.io/) | 图像读取 | BSD-2-Clause |
| [Pillow](https://python-pillow.org/) | 图像处理 | HPND |

发行包由 [PyInstaller](https://pyinstaller.org/) 打包，其引导程序以 GPL-2.0-or-later 发布，并附带允许用于非自由软件的例外条款。
