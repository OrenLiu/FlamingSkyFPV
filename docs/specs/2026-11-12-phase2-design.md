# FlamingSkyFPV 二期设计文档

日期：2026-11-12
状态：已分段经用户确认（完整打通 / 双通道 / 遗留 minor 顺手纳入），待整体审查
范围依据：todo.md「第二期待实现功能」三块——无人机预设、虚拟飞控模式、飞行界面（OSD + 开关通道）。

## 1. 背景与目标

二期交付三块能力（todo.md 字面范围）：

1. **无人机预设**：多机参数管理（列表 / 编辑 / 复制 / 删除 / 当前机高亮）
2. **虚拟飞控模式**：参数模式 ↔ 虚拟飞控模式转换；虚拟飞控接入 Betaflight Configurator，PID/rate/OSD 布局/开关范围全部在地面站调整并保存回读一致
3. **飞行界面**：虚拟飞控模式下隐藏自带 HUD、渲染地面站配置的 OSD、响应开关通道（解锁 / 自稳 / 反乌龟）

**用户已裁定的范围决策**：

| 决策点 | 裁定 |
|---|---|
| MSP 打通深度 | 完整打通：Configurator 识别 + PID/rate 读写生效 + Modes 配置 + OSD 布局编辑 + EEPROM 保存回读一致 |
| 接入通道 | 双通道：TCP 直连为主（新版 Configurator）+ PTY 兼容（旧版 Electron Configurator） |
| 一期遗留 minor | 顺手纳入：输入范围钳位（未绑定轴回中位）、HUD 输入来源徽标 |
| 竞速模式 | 不做（todo.md 二期清单无此项） |
| D-Min/FF 飞控仿真 | 不做独立任务；地面站若写这些字段则存储回读但不影响仿真（诚实降级） |

## 2. 关键调研结论（设计依据）

- Betaflight Configurator 现稳定版 **2026.6.2**（Tauri + Vue3 重构，GitHub release 有 deb/AppImage）；此前 10.x 为 Electron 版。串口层为 Rust serialport-rs，**只枚举 sysfs 中有内核驱动绑定的 tty**——Python `pty` 的 `/dev/pts/N` 不会出现在其端口列表（PTY 仅旧版 Electron serialport 兼容）。
- **`tcp://` 是 Configurator 一等连接类型**：源码 `serial.ts` 注释明确 tcp:// 用于 SITL 与 Betaflight bridge（模拟器接入即官方支持形态）；桌面版经 raw TCP 字节流直通 MSP。Expert 模式下端口选择器有 manual 手动输入入口（`portOverride`），TCP 目标握手窗口 20 秒（`beginConnect`）。
- Configurator 内置 "virtual" 设备为**前端自闭环演示**（连接后不走 MSP 链路、无法注入外部数据），不可用作通道。
- Configurator 连接后按 tab 拉取 MSP 命令；`API_VERSION_1_45/1_46/1_47` 在源码中被特别接受（`API_VERSION_ACCEPTED`）。协议细节（帧格式、载荷编码、OSD 网格尺寸与位置编码、PID 缩放表）**实现期以 Configurator `MSPHelper.js` 与 Betaflight 固件 `msp.c` 源码逐命令核对**，本设计不锁定字节级细节。
- 本机环境：Configurator 未安装（M4 前需用户安装 2026.6.2）；socat 已装；用户在 dialout 组；PTY 可用。

## 3. 总体架构

```
msp/                       # 新增：MSP 服务器（不 import 渲染/UI/pygame）
  protocol.py              # MSPv1/v2 帧编解码 + CRC8-DWD-SID86(0xD5)（纯函数）
  server.py                # 传输无关服务器：TCP/PTY 双传输 + 读线程 + 命令队列
  handlers.py              # 命令处理：身份/状态/参数/模式/OSD 命令集 ↔ 虚拟飞控状态
osd.py                     # 新增：OSD 内容生成（纯函数：状态 → 30 列字符网格 + 闪烁标记）
physics/drone.py           # 扩展：FlightController 消费 boxes（ARM/ANGLE/HORIZON/FLIP_OVER_AFTER_CRASH）
input/channels.py          # 扩展：Channels + aux: list[float]（4 路，[-1,1]）
input/joystick.py          # 扩展：AUX 绑定（按钮两档/轴三段）+ 未绑定轴回中位
input/keyboard.py          # 扩展：F1/F2/F4 边缘检测 toggle AUX1/2/3
screens/drone_params.py    # 重构：列表 → 编辑（参数模式/虚拟飞控模式）子状态机
config/drones/<代号>.json  # 新增：每台预设一个文件
```

依赖方向保持单向：`screens → physics/input/msp/osd → config_store`；`msp/`、`osd.py`、`physics/`、`input/` 均不 import 渲染/UI 库，可独立单测。

**App 生命周期**：`app.drone_params` 变为「当前预设的 DroneParams」；MSP 服务器实例挂在 App 层（跨 Screen 存活），虚拟飞控连接状态由参数页与飞行页共享读取。

## 4. 配置层（schema v5）

### 4.1 预设存储

- 目录 `config/drones/<代号>.json`，每台一文件；文件名（去 .json）即预设 id。代号经 sanitize 后作文件名：路径分隔符与控制字符替换为 `_`，保留中文。同名复制自动追加序号。
- `Settings.current_drone_id: str = "default"`（additive 字段，不升 settings schema）。无有效值时回落列表第一个文件；列表为空自动创建「默认机」。
- **迁移**：启动时若存在旧 `config/drone.json` 且 `drones/` 目录为空 → 深拷贝为 `drones/默认机.json`，旧文件改名 `drone.json.migrated` 归档（不再读取）。
- 删除当前机 → current_drone_id 回落列表第一台；`App.save_all` 保存 settings + rc + 当前预设（列表其余预设按需加载，App 只持有当前预设对象）。

### 4.2 DroneParams v5 结构

```python
PARAMS_SCHEMA = 5
@dataclass
class DroneParams:
    ...                                # 一期字段全部保留（向后兼容加载）
    virtual_fc: bool = False
    virtual: VirtualFCParams = field(default_factory=VirtualFCParams)

@dataclass
class VirtualFCParams:
    tune: TuneParams                   # 地面站调参值（见 4.3 转换语义）
    mode_ranges: list                  # [(box_permanent_id, aux_index, lo_us, hi_us), ...]
    osd_layout: dict                   # {element_permanent_id: 位置编码（地面站格式）}
    osd_enabled: dict                  # {element_permanent_id: bool}
    craft_name: str = ""               # 地面站 MSP2_SET_TEXT 写入；空则 OSD 显示预设代号
    cells: int = 4                     # 电池节数（OSD 单芯电压 = battery_voltage/cells）
```

v4 档加载：缺 `virtual_fc`/`virtual` 键时补默认值（additive，不重置其它字段）；`params_schema != 5` 的旧规则（pid/rate 重置）沿用一期迁移逻辑并升到 5。

### 4.3 转换语义

- **调参字段集合** `TUNE_FIELDS = {rate_type, rate, pid, tpa_percent, tpa_breakpoint}`（`TuneParams` 与之一一对应）。
- 参数模式 → 虚拟飞控：UI 调参字段快照进 `virtual.tune`，`virtual_fc=True`。
- 虚拟飞控 → 参数模式：`virtual.tune` 回写 UI 字段，`virtual_fc=False`。
- **硬件参数两模式共享单份不分裂**：weight_g / motor_kv / thrust_ratio / battery_voltage / drag_k / camera_angle（+ virtual 段的 cells）。
- 飞行仿真消费规则：`virtual_fc=True` 时 PID/rate/TPA 取 `virtual.tune`（地面站为准），`False` 时取 UI 字段——封装为 `DroneParams.active_tune()`。
- 地面站写 `virtual_fc` 不可见字段（如 D-Min/FF/滤镜）：handler 接受并忽略（不落盘未知字段），详见 §7 命令集。

## 5. 无人机参数页重构（screens/drone_params.py）

**子状态机**：`list → edit`（参照 FlightScreen 模式）。

- **列表态**（todo.md 字面）：进入即列表；每行左代号、右「编辑 / 复制 / 删除」按钮；点击行内三个按钮以外区域 = 选中为当前机；当前机行高亮并在代号旁显示「当前无人机」标签。复制弹出输入框填写新代号；删除弹 imgui 模态确认。
- **编辑态（参数模式）**：一期全参数表单 + 顶部「无人机代号」输入框 + 底部「转换为虚拟飞控」按钮（确认弹窗文案："是否转换为虚拟飞控？\n转换后，需要进入 Betaflight 地面站对无人机进行调整。"）。
- **编辑态（虚拟飞控模式）**：仅硬件参数（重量 / KV / 推重比 / 电池电压 / 电池节数 / 空气阻力 / 摄像头角度）+「接入地面站」+「转换为参数模式」。
- 主菜单「无人机参数」入口 → 列表态。

## 6. AUX 开关通道与输入层

- `Channels` 加 `aux: list[float]`（4 路 AUX，`[-1,1]`，0=中位）。映射 MSP RC 通道 5–8：µs = 1500 + 值×500。
- `RCConfig.mappings` 加 `aux1..aux4: AuxBinding(kind, index)`：`kind="button"` 两档（未按 −1 / 按下 +1）；`kind="axis3"` 三段（轴值 <−1/3 / 中间 / >1/3 → −1/0/+1）；`kind="none"` 恒中位。主四通道绑定结构不变。
- 键盘兜底：**F1/F2/F4 = AUX1/2/3 拨动键**（KeyboardInput 内做上升沿检测；**裁定：避开 F3**——app.py 全局 F3 调试覆盖层热键与 AUX3 拨动同键会双触发，飞行中误触调试层），F1 解锁 / F2 自稳 / F4 反乌龟的推荐用法在虚拟飞控参数页与 HUD 指引文案中说明（用户在地面站 Modes 页把功能绑到 AUX1/2/3 即可）。
- **一期 minor 顺手纳入**：JoystickInput 轴越界防御（绑定轴号超出设备实测轴数时按未绑定处理返回中位，防换设备后 `get_axis` 异常或读错轴）；飞行 HUD 增加输入来源徽标（键盘 / 手柄名）。
- rc_setup 页增加 AUX 绑定区（来源下拉 + 对象选择），向导不变（仍只配主四通道）。

## 7. 虚拟飞控运行时（physics/drone.py 扩展）

`FlightController.update(ch, gyro_dps, dt, aux_us, boxes)`（boxes 由调用方按 mode_ranges 评估传入；评估函数 `eval_boxes(mode_ranges, aux_us) -> set` 是 **physics/drone.py 纯函数**，飞行每帧与 MSP STATUS 应答共用同一实现）。

- **暴露的 box（permanentId 以 BF 源码为准）**：ARM、ANGLE、HORIZON、FLIP_OVER_AFTER_CRASH。
- **ARM 状态机**：**仅在 virtual_fc=True 时启用**（参数模式无模式系统，保持一期行为）。ARM ∈ boxes 且油门 < 0.10 → armed；ARM ∈ boxes 但油门 ≥ 0.10 → 保持加锁（HUD/OSD 提示一次）。ARM ∉ boxes → disarm + 三轴 PID 积分清零。Esc「重置位置」→ 自动 disarm。**模拟器偏离真机 BF（无油门保护）**：防误触弹射破坏体验，设计文档明示。
- **未解锁**：四电机输出 0（杆量无响应），每帧重置 PID。
- **自稳 ANGLE**：roll/pitch 外环姿态闭环：目标倾角 = rc×45°，外环输出 = (目标角 − 当前角) × 8 °/s/°，替换内环 rate setpoint；yaw 保持 rate 闭环。外环增益不暴露 UI（YAGNI）。
- **HORIZON**：中位自稳、杆外 acro：自稳权重 w = clamp(1 − |rc|/0.6, 0, 1)，setpoint = w×自稳外环输出 + (1−w)×rate 曲线输出。
- **反乌龟 FLIP_OVER_AFTER_CRASH**：armed 且 (|roll|>120° 或 |pitch|>120°) 且油门 < 0.35 且 box 激活 → 进入翻正态，主导轴（|roll|>|pitch| 取 roll 否则 pitch）内环 setpoint 叠加朝最短翻正方向 ±400°/s。**退出为三路析取**（实现期与实测共同裁定，对齐真机"撤 box 即停"语义与接管直觉）：角度迟滞（|roll|<90° 且 |pitch|<90°）**或** FLIP box 撤销**或**油门 ≥ 0.35（进入侧 <0.35 严格、退出侧 ≥0.35 互补无重叠）。物理简化实现（真机逐电机反转甩正），明示偏离。
- 虚拟飞控模式下 `drone_params.camera_angle_deg` 等硬件参数仍即时生效（参数页编辑）。

## 8. MSP 服务器（msp/）

### 8.1 传输层（server.py）

- **TCP 传输**：监听 `127.0.0.1:5761`（默认固定端口；端口占用时报错提示，修订：一期实现不提供面板改端口 UI，措辞以实现为准——改端口属 M5 可选项）。
- **WebSocket 传输**（用户裁定扩展，2026-11-12）：监听 `127.0.0.1:5762`（默认）。依据：Configurator 浏览器形态的 "tcp" 槽位即 WebSocket（serial.ts 注释原文），`WebSocket.ts` 为 MSP 字节流二进制透传——浏览器版 Configurator「手动选择」输入 `ws://127.0.0.1:5762` 即可连接。实现为 RFC6455 最小子集（握手 SHA1/base64、二进制帧收发、ping/pong、close、分片重组），msp/ 保持纯标准库；MSP 协议层零改动。
- **PTY 传输**：`pty.openpty()`，master 由服务器读线程持有，slave 路径显示给用户（旧版 Electron Configurator 兼容通道；新版枚举不到 PTY 时 UI 明示用 TCP）。
- **线程模型**：每连接一个读线程（字节流 → 帧解析 → 请求入队）；主循环每帧 drain 请求队列并在主线程生成应答（写回发送队列，读线程刷出）。≤1 帧延迟（60fps 下 16ms），状态访问全单线程，无锁竞争。
- 「接入地面站」按钮 → 开启双传输并弹提示框（todo.md 字面文案「打开地面站即可进行调参」+ 两通道地址）；「断开连接」→ 关闭全部连接与传输。飞行页不自动开启 MSP（只有参数页开启后飞行期间保持连接，供地面站观察实时姿态）。

### 8.2 协议层（protocol.py）

- MSPv1 帧编解码（`$M<` / `$M>`，CRC8-DWD-SID86 poly 0xD5）为主；识别并兼容 MSPv2 帧（`$X<`）；应答 `MSP_API_VERSION` 的 mspProtocolVersion=0 引导 Configurator 走 V1。实现期以 Configurator `msp/MSPHelper.js` 与固件 `msp.c` 核对。

### 8.3 命令集与自报身份（handlers.py）

- **自报**：FC_VARIANT `BTFL`、FC_VERSION 4.5.x、API_VERSION 1.46（Configurator 源码特别接受的档位）、BOARD_INFO/UID/BUILD_INFO 固定值。
- **命令集分层交付**（M4 → M5）：
  1. 身份组：API_VERSION / FC_VARIANT / FC_VERSION / BUILD_INFO / BOARD_INFO / UID → M4 spike 验证识别
  2. 实时状态组：STATUS_EX（含 armingDisableFlags）/ ATTITUDE / RC（8 通道 µs）/ RAW_IMU → Setup/Modes 页可用
  3. 参数读写组：PID_NAMES / PID / PID_ADVANCED（TPA）/ RATE_PROFILE 与对应 SET_* → PID tab 可调
  4. 模式组：BOXNAMES / BOXIDS / MODE_RANGES / MODE_RANGES_EXTRA 与 SET_* → Modes tab 配开关范围
  5. OSD 组：OSD_CONFIG / SET_OSD_CONFIG（元素布局与开关）/ MSP2_GET_TEXT / MSP2_SET_TEXT → OSD tab 编辑
  6. 固化组：EEPROM_WRITE → 虚拟飞控段落盘预设文件；CF_SERIAL_CONFIG（UART1 functionMask 恒含 MSP 位，防 Ports 页误关）；RESET_CONF → 断开重连模拟
- **参数落点**：SET_PID/SET_RATE 等写 `virtual.tune`；EEPROM_WRITE 时整体保存预设文件。虚拟飞控连接期间参数页若同屏编辑，以最后保存者为准（提示框注明）。
- 未知/未实现命令：回空载荷或 NACK，打印调试日志，不断连接。

## 9. OSD（osd.py + flight.py）

- **内容生成**（纯函数，可单测）：`build_frame(state) -> {(x,y): (glyph_index, blink), ...}`。state = 位置/速度/姿态/油门/电压/armed/boxes/时间/预设代号 + `osd_layout`/`osd_enabled`。
- **首批元素子集（约 20 个）**：主电池电压、单芯电压、电池剩余百分比（按电压线性估算）、速度、高度、vario、姿态、计时器 1/2、RSSI（固定模拟值 99）、craft name（= 预设代号）、DISARMED 闪烁警告（2Hz）、十字线、水平中线左右箭头。完整元素表实现期对照 Configurator OSD tab 核对；地面站能配置但我们未实现的元素忽略不画并在调试日志记录。
- **渲染**：内置 MAX7456 256 字符位图字体（12×18/字符；提取 Betaflight Configurator 默认字体位图数据入库，GPL 来源在文件头标注）→ 首次使用时烘焙 GL 纹理图集 → imgui draw list 按网格绘制。OSD 网格尺寸（30×13 或 30×16）与位置编码以 Configurator 为准。
- **飞行集成**：虚拟飞控模式飞机隐藏一期 HUD（含输入徽标一并隐藏），渲染 OSD；Esc 暂停菜单保留；参数模式行为完全不变。

## 10. 错误处理

| 情形 | 行为 |
|---|---|
| 预设文件损坏 | 备份 .bak，列表中标记无效可删除，不阻塞其余预设 |
| 预设代号重复 / 非法字符 | 复制时自动追加序号；输入实时 sanitize |
| Configurator 连接中预设被删除 | 连接保持但保存时目标缺失 → 提示并断开 |
| TCP 端口被占用 | 提示框显示错误并建议改端口 |
| PTY 创建失败（非 Linux 等） | 仅提供 TCP 通道，UI 注明 |
| MSP 未知命令 / 解码错帧 | 丢弃该帧，日志记录，不断连接 |
| Configurator 侧断开 | 服务器清连接状态，提示框可重新等待接入 |

## 11. 测试策略

- **pytest 单测**（不依赖 GL/bullet）：MSP 帧编解码 round-trip 与 CRC、命令 handler 请求→应答快照、转换语义（双向字段一致）、预设存储/迁移/删除回落、box 判定、ARM 状态机（含油门保护）、ANGLE/HORIZON 外环收敛、反乌龟触发与退出、AUX 档位映射（按钮/轴/键盘）、OSD 内容生成快照、JoystickInput 未绑定回中。
- **手动验收**（docs/acceptance-phase2.md）：安装 Configurator 2026.6.2 + Expert 模式 → tcp:// 连接识别 → PID tab 修改保存 → Modes 配 ARM/ANGLE/反乌龟 → OSD 布局编辑保存 → EEPROM 回读一致 → 飞行验证解锁/自稳/反乌龟/OSD 显示 → 旧版 Electron + PTY 通道连通 → 参数模式回归（一期验收项重跑）。

## 12. 风险

1. **MSP 命令载荷细节**（PID 缩放、OSD 位置编码、STATUS_EX 结构）——中风险：以 Configurator/固件源码逐命令核对，M4 spike 尽早暴露问题；未知命令优雅降级不断连。
2. **Configurator 版本演化**（master 已是 2026.12.0-dev）——低风险：锁定验收版本 2026.6.2，协议分层交付便于适配。
3. **旧版 Electron + PTY 通道**无法在本机完整验证（旧版需另装）——低风险：PTY 通道协议与 TCP 完全同栈，手动验收可降级为「PTY 可被 socat/cat 读写验证」。
4. **imgui draw list 位图字体渲染与 soup3D 共存**——低风险：沿用封面纹理与调试覆盖层的成熟路径。
5. **参数页与地面站并发编辑**——设计上以「最后保存者为准」+ 提示文案规避，不做实时双向同步（YAGNI）。

## 13. 里程碑（M1–M7，详见实现计划）

1. M1 预设存储与列表 UI + 旧档迁移
2. M2 AUX 通道 + 遗留 minor（输入钳位、HUD 徽标）
3. M3 飞控模式运行时（ARM/自稳/反乌龟）+ 参数页双模式转换
4. M4 **MSP spike**：TCP 服务器 + 身份组 → Configurator 2026.6.2 连接识别验证
5. M5 MSP 全命令集（状态/参数读写/模式/OSD/EEPROM，逐 tab 对接）
6. M6 OSD 渲染 + 飞行界面集成
7. M7 PTY 传输 + 双通道手动验收清单 + README/验收文档更新
