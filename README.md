# FlamingSkyFPV

开源 FPV 穿越机模拟器——一切皆可自定义：场景、赛道、障碍均可导入；参数全开放（Rate / PID / 重量 / KV / 摄像头角度），改了就飞。

## 运行

```bash
pip install pybullet pygame imgui soup3D   # 其余依赖随发行包分发
python main.py
```

无手柄时自动使用键盘兜底：W/S 油门（保持式）、A/D 偏航、↑↓ 俯仰、←→ 横滚。Esc 打开暂停菜单。

已知限制：帧时长超过 1/30s 时物理步长会被钳位（防穿透），低帧率下表现为时间膨胀（动作比真实慢）；一期不做性能优化。

## 虚拟飞控模式与地面站调参

每台无人机在参数页有两种模式：「参数模式」为本程序 UI 直接调参（一期行为）；「虚拟飞控模式」把 Rate/PID/TPA 调参域移交地面站。参数页编辑态底部点「转换为虚拟飞控」并确认（调参字段快照进虚拟飞控域），此后表单只保留硬件参数（重量/KV/推重比/电池电压与节数/空气阻力/摄像头角度，两模式共享），Rate/PID/TPA 在 Betaflight 地面站中修改、保存后模拟器即时生效；「转换为参数模式」可随时换回（调参域回写 UI）。

接入地面站：虚拟飞控模式表单点「接入地面站」，提示框显示三个通道地址，任选其一在地面站连接（连接后在飞行界面保持，地面站可实时观察姿态）：

| 通道 | 地址 | 适用形态 |
|---|---|---|
| TCP | `tcp://127.0.0.1:5761` | 桌面版（Tauri；验收锁定 2026.6.2） |
| WebSocket | `ws://127.0.0.1:5762` | 浏览器/dev-server 形态 |
| PTY 串口 | 提示框显示的路径（形如 `/dev/pts/N`，仅 Linux） | 旧版 Electron 形态或串口工具 |

接入前提：地面站开启 **Expert Mode**，设备选择「手动选择」后填入上表地址再 Connect（浏览器形态默认自动连接内置 virtual 演示飞控，务必手动选择）。

飞行键位（键盘兜底的 AUX 拨动键）：**F1 解锁 / F2 自稳 / F4 反乌龟**（分别拨动 AUX1/2/3；F3 为调试覆盖层热键故避开）。在地面站 Modes 页把 ARM / ANGLE / FLIP OVER AFTER CRASH 绑到对应 AUX 通道区间即可生效。虚拟飞控模式飞行界面显示 Betaflight 风格 OSD（MAX7456 字符网格，30×16），元素与布局可在地面站 OSD 页编辑。

二期验收清单见 `docs/acceptance-phase2.md`。

## 着色器预设

设置页「画质预设」可切换着色器预设包：内置「性能优先」（默认管线）与「画面优先」（PBR + 太阳软阴影 + SSR 反射 + HDR/ACES 色调映射）。自制或下载的预设包放入 `shader_packs/<包名>/`（`pack.json` + `pack.py`），在设置页选择即可——详见 `docs/shader_packs.md`（注意：运行第三方包等于执行任意代码，只装可信来源）。

## 场景格式

`scene/<场景名>/` 目录：`config.json`（`name` 显示名、`model` OBJ 文件、可选 `scale` 缩放倍数、`spawn` 出生点 [x,y,z] 与 `sky` 天空配置）+ OBJ/MTL + 贴图。放入即出现在"开始飞行"列表中。

`sky` 段（可选）：`sun_yaw`/`sun_pitch` 太阳角度（度，同时驱动方向光与画面优先模式的天空盒太阳）、`sun_color` [r,g,b] 太阳颜色、`panorama` 场景目录内等距圆柱全景图文件名（画面优先模式的天空盒；缺省程序生成蓝天渐变+云朵+太阳）。示例：`"sky": {"sun_yaw": 120, "sun_pitch": -30, "sun_color": [1.0, 0.6, 0.2]}`。

顶层快捷字段（可选）：`sun_toward` [yaw, pitch, roll]（度，与引擎光源 toward 参数同语义，roll 对平行光方向无效）等价于 `sky` 段的 `sun_yaw`+`sun_pitch`；`sun_color` [r,g,b]、`ambient` [r,g,b] 分别是太阳颜色与环境光颜色的顶层写法。`sky` 段显式指定时优先于顶层字段。示例：`"sun_toward": [0, -15, 0], "ambient": [0.45, 0.45, 0.45]`。

## 技术栈

soup3D（渲染）· PyBullet（物理）· pygame（窗口）· imgui（UI）

一期范围与设计见 `docs/specs/`。
