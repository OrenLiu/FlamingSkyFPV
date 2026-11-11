# FlamingSkyFPV

开源 FPV 穿越机模拟器——一切皆可自定义：场景、赛道、障碍均可导入；参数全开放（Rate / PID / 重量 / KV / 摄像头角度），改了就飞。

## 运行

```bash
pip install pybullet pygame imgui soup3D   # 其余依赖随发行包分发
python main.py
```

无手柄时自动使用键盘兜底：W/S 油门（保持式）、A/D 偏航、↑↓ 俯仰、←→ 横滚。Esc 打开暂停菜单。

已知限制：帧时长超过 1/30s 时物理步长会被钳位（防穿透），低帧率下表现为时间膨胀（动作比真实慢）；一期不做性能优化。

## 着色器预设

设置页「画质预设」可切换着色器预设包：内置「性能优先」（默认管线）与「画面优先」（PBR + 太阳软阴影 + SSR 反射 + HDR/ACES 色调映射）。自制或下载的预设包放入 `shader_packs/<包名>/`（`pack.json` + `pack.py`），在设置页选择即可——详见 `docs/shader_packs.md`（注意：运行第三方包等于执行任意代码，只装可信来源）。

## 场景格式

`scene/<场景名>/` 目录：`config.json`（`name` 显示名、`model` OBJ 文件、可选 `scale` 缩放倍数、`spawn` 出生点 [x,y,z] 与 `sky` 天空配置）+ OBJ/MTL + 贴图。放入即出现在"开始飞行"列表中。

`sky` 段（可选）：`sun_yaw`/`sun_pitch` 太阳角度（度，同时驱动方向光与画面优先模式的天空盒太阳）、`sun_color` [r,g,b] 太阳颜色、`panorama` 场景目录内等距圆柱全景图文件名（画面优先模式的天空盒；缺省程序生成蓝天渐变+云朵+太阳）。示例：`"sky": {"sun_yaw": 120, "sun_pitch": -30, "sun_color": [1.0, 0.6, 0.2]}`。

## 技术栈

soup3D（渲染）· PyBullet（物理）· pygame（窗口）· imgui（UI）

一期范围与设计见 `docs/specs/`。
