# MSP spike 协议事实清单（Task 17 交付物）

日期：2026-11-12；验证环境：Betaflight App **2026.12.0-alpha**（master 开发版，Linux，**浏览器/dev-server 形态**）；模拟器提交 76bcbeb。
用途：第二份计划（docs/plans/2026-11-12-phase2-msp-osd.md，M5-M7）的协议输入。

## 1. ⚠️ 重要修正：首次"连接成功"为假阳性（2026-11-12 用户控制台日志证实）

用户在**浏览器形态**（http://localhost:8080 dev server）下操作，控制台证据：
- `checkCompatibility`: **Tauri: false**、Serial: true（WebSerial）、Chromium: true → 浏览器形态
- `main.js:170 Detected development URL` + `Automatically enabling development settings` → dev-server 形态
- `device_handler.js:408 Automatically selected device is 'virtual'` + `serial_backend.js:384 Connecting to: virtual` → **实际连到 Configurator 内置 virtual 演示飞控**（"BTFL 2025.12.0" 为内置演示数据，非我方应答）
- PID/rate 显示默认值的原因也随之修正：是 virtual 演示飞控的默认值，与我们的 MSP 服务器无关

**结论：MSP 服务器尚未经真实 Configurator 验证；需 Tauri 桌面形态重测。**

### 形态能力事实（源码 serial.ts canOpen + 本次实测佐证）

- **浏览器/dev-server 形态无 raw TCP**：`tcp://` 地址仅桌面 Tauri 形态可开（`_hasRawTcp`）；浏览器形态连 `tcp://` 会"always fail"（serial.ts 注释原文）。**桌面版是唯一可用形态**。
- 桌面形态串口枚举为 serialport-rs（ttyS0-3 等 8250），PTY 不在其列（设计 §2 已判明）。

## 2. 重测步骤（待做：Tauri 桌面版）

1. 安装 2026.6.2 deb 桌面版（稳定版；当前用户环境的 alpha 为 dev-server 运行形态，不适用）
2. 桌面版内 Expert Mode → 设备列表「手动选择」→ 地址框 `tcp://127.0.0.1:5761` → Connect
3. 验证点：Setup 页显示 BTFL/4.5.1/FSKY（我方身份）；控制台缺失命令清单；Modes/OSD tab 表现

## 2.5 MSPv1 帧格式权威事实（2026-11-12 浏览器实测 + Configurator msp.ts 解码器核实）

- **MSPv1 布局**：`$ M < len(u8) cmd(u8) payload checksum`；**len==255 为 JUMBO 转义**（后续两字节 u16 LE 为真实长度，JUMBO_FRAME_MIN_SIZE=255）；cmd 单字节（CODE_V1 单状态）；**checksum 为 XOR 逐字节异或（从 dir 字节起；CRC8-DWD-SID86 仅 MSPv2 使用）**——权威细节与浏览器实发字节见 §6 附录（§6 为定论，本节概述与其不一致处以下方勘误为准）。
- **实测证据**：浏览器版 Configurator（2026.12.0-alpha）WS 握手成功后发 API_VERSION 请求（6 字节 `24 4D 3C 00 01 01`），按错误布局（len u16+cmd u16+CRC8）实现的解析器永远判"帧未收全"→ 模拟器全静默、Configurator 500ms×2 超时断开。**教训**：MSPv1 len/cmd 均为 u8、校验和为 XOR，勿凭任何记忆（含"固件 serialHeaderV1_t"转述）——以 msp.ts 解码器状态机/encode_message_v1 为准；自洽 round-trip 单测无法发现外部格式错误，必须有真实对端字节向量。
- （勘误记录：本节初稿误写"crc8 从 dir 起覆盖"，与 §6 定论矛盾，2026-11-12 由 fix round 2 复审发现并勘误。）

## 3. 命令号权威事实（在线核实 Configurator master MSPCodes.ts，2026-11-12）

- MSP_API_VERSION=1、MSP_FC_VARIANT=2、MSP_FC_VERSION=3、MSP_BUILD_INFO=**5**、MSP_BOARD_INFO=**4**、MSP_UID=160、MSP_SET_RC_TUNING=204。
- 教训：命令号一律以 MSPCodes.ts 在线核实，勿凭记忆（BOARD_INFO/BUILD_INFO 记忆错位在 Task 16 审查中被发现并修正）。
- 另核实：manual 条目 i18n key `portsSelectManual`（zh-CN 文案「手动选择」），`manualModeAvailable = networkOnly || (showManualMode && isExpertModeEnabled())`。

## 4. 服务器侧已验证事实（无 Configurator 参与的自证）

- TCP/PTY 双传输 loopback 单测 10 项全绿（243 项套件）；X 下端到端：真实主循环 drain 应答 6 条身份握手（tcp://127.0.0.1:5761 + /dev/pts/N）。
- 身份组应答：API_VERSION(1)=[0,1,46]（mspProtocolVersion=0 → v1）、FC_VARIANT(2)=BTFL、FC_VERSION(3)=4.5.1、BUILD_INFO(5)、BOARD_INFO(4)、UID(160)。

## 5. 最终验证结果（2026-11-12，用户浏览器实测定论）

- **✅ 连接序列走完**：WS 通道（ws://127.0.0.1:5762）→ 身份组（v1）→ GET_TEXT 三连（v2：BUILDKEY 空 / CRAFT_NAME="FSKY-SIM" / PILOT_NAME）→ v1 空载荷降级命令 → **Configurator 成功进入 Setup 页**（2026.12.0-alpha 浏览器形态）。
- **✅ 身份显示**：`Fw git rev: -11-12`（解析自我方 BUILD_INFO build 串）。
- **Setup 页数据为空 = 预期**：实时数据（姿态/仪表）来自 MSP_STATUS(101)/ATTITUDE(81)/RAW_IMU(102) 等状态轮询——M5 范围（空载荷→显示空）。
-历经三轮实测驱动的协议修复：子协议协商（binary）→ MSPv1 帧布局（len/cmd u8 + XOR）→ MSPv2 解析与应答（GET_TEXT）。全部教训与权威线格式见 §2.5/§6/§7。

## 5.1 待桌面版重测后补充（TCP 通道同栈，非阻塞）

- [ ] Setup 页身份显示（验证 BUILD_INFO/BOARD_INFO 载荷形态解析）
- [ ] 控制台缺失/超时命令清单（M5 命令集优先级依据）
- [ ] Modes/OSD tab 表现

## 6. 附录（Task 18 fix round 2，2026-11-12）：MSPv1 真实线格式权威事实

浏览器实测（WS 通道）暴露 protocol.py 帧布局实现错误，已修复（fix 提交见 task-18-report.md）。**M5-M7 计划据此为准**：

- **MSPv1 线格式（双向）**：`$M dir len(u8) cmd(u8) payload checksum`，checksum = **XOR** 校验和（len ^ cmd ^ payload 逐字节异或，**非 CRC8**；CRC8-DWD-SID86 仅 MSPv2 使用）。
- **jumbo 扩展**：`len==0xFF` 为标记，线序 `$M dir FF cmd len(u16LE) payload crc`（cmd 在长度之前），crc 多异或 0xFF 与两个长度字节。
- **v1 cmd 为 u8**：Configurator `code <= 254 ? encode_message_v1 : encode_message_v2`（msp.ts:484）——命令号 >254 一律走 v2。
- 权威源：Configurator master `src/js/msp.ts`（encode_message_v1 / encode_message_v2 / 解码器 BYTE_HANDLERS 全状态机，2026-11-12 抓取核对）；msp.js 已在 master 拆分为 msp.ts + msp/ 目录（MSPCodes.ts、MSPHelper.ts、mspBytes.ts 等）。
- 浏览器实发 MSP_API_VERSION 请求字节：`24 4D 3C 00 01 01`（6 字节）。
- 重试语义：请求超时 TIMEOUT=1000ms × MAX_RETRIES=3 重发同一 buffer（msp.ts constants；用户日志 "MSP: data request timed-out" 为重试日志）。

## 7. 附录（Task 18 fix round 3，2026-11-12）：MSPv2 与连接序列权威事实

浏览器重测确认 v1 身份组打通（握手链 1/2/3/4/5/160 全部成功）后，连接收尾卡在 v2 命令。已修复并提交（见 task-18-report.md）：

- **MSPv2 线格式（双向）**：`$X dir flag(u8) cmd(u16LE) len(u16LE) payload crc8`——**len 是 u16LE 不是 u24**（encode_message_v2 与解码器 PAYLOAD_LENGTH_V2_LOW/HIGH 两态一致；任务早期 u24 读法系误记）。crc8-DWD-SID86 覆盖 flag+cmd+len+payload（不含 `$X`/dir）。v2 无 jumbo。
- **12294 = MSP2_GET_TEXT（0x3006），不是 BUILD_KEY**——MSPCodes.ts 无 BUILD_KEY 命令；build key / craft name / pilot name 都是 MSP2_GET_TEXT 的类型字节区分（MSP2TextType：PILOT_NAME=1、CRAFT_NAME=2、PID_PROFILE_NAME=3、RATE_PROFILE_NAME=4、BUILDKEY=5、RELEASENAME=6、BATTERY_PROFILE_NAME=11）。请求 payload=[类型 u8]，应答 payload=[类型 u8]+文本。
- **连接序列全貌**（serial_backend.ts）：身份组 → MSP_UID(160) → MSP2_GET_TEXT[BUILDKEY=5] → MSP2_GET_TEXT[CRAFT_NAME=2] → MSP2_GET_TEXT[PILOT_NAME=1] → disableArming → MSP_SET_RTC → finishOpen（connectionValid=true，Setup 页完成）。BUILDKEY 应答非 32 字长则跳过云构建选项拉取。
- **v2 必答**：v2 请求被静默丢弃（旧策略）会被 Configurator 判超时（"stopped responding"）断开——修复后 v2 解析入队、应答按请求版本包 v2 帧、未知 v2 命令空载荷降级。

## 8. 附录（Task 45-47 修复批，2026-11-13）：Modes 亮灯/节数/OSD 元素权威事实

- **STATUS flightModeFlags 按 BOXNAMES 列表索引打位，非 BOXIDS permanentId**
  ——Configurator AuxiliaryTab.vue 判亮灯用 `bit_check(fcStore.config.mode,
  mode.index)`（mode.index = BOXNAMES 位置）。我方曾按 permanentId 打位
  （FLIP=bit21）→ Modes 页永不亮（ARM/ANGLE/HORIZON 因 id==索引 巧合正常）。
  修复：按 _BOX_IDS 列表索引打位（Task 45，commit 7b15769/4e8fb76）。
- **节数自动识别**（固件 4.5.1 sensors/battery.c batteryUpdatePresence 实读）：
  手动强制（forceBatteryCellCount≠0）优先；否则 cells = int(V/4.30)+1
  （VBAT_CELL_VOLTAGE_DEFAULT_MAX=430），钳上限 8（MAX_AUTO_DETECT_CELL_COUNT）；
  真机仅电池接入瞬间检测。仿真域补下限 1、恒重算（Task 46）。
  PID 页「Cell Count」下拉=profile 切换条件三态（-1 切换/0 禁用/1-8 强制），
  非节数本体；我方 GET 94@48 auto→0xFF、SET ≥1→手动/≤0→auto 与其对齐。
- **OSD 元素 id**（固件 4.5.1 osd.h osd_items_e 逐项计数 + 现有锚点交叉验证）：
  ARTIFICIAL_HORIZON=3、HOME_DIST=31、FLIGHT_DIST=47（Task 47 入表）。
  4.5.x 布局注意：battery 在 src/main/sensors/、osd 在 src/main/osd/。
- **AH 渲染语义**（osdElementArtificialHorizon 实读）：9 列阶梯倾斜线，
  每列 y = −rollAngle·x/64 − pitch_scaled（rollAngle 0.1°、x∈[−4,+4]）；
  右滚→右端上翘；45° 时两端各偏基准 ≈3 行、彼此 ≈6 行。仿真按
  行号=基准−round(roll·x/60) 等比实现。
- **教训补记**：GitHub raw/jsdelivr 对部分网络不可达时，`gh api contents`
  配 `tr -d '\n' | base64 -d`（小文件）或 `--depth 1 --branch <tag> --filter
  =blob:none --sparse` 克隆（大文件/整目录）为可靠取源路径；contents API
  目录列表默认 per_page=30 会静默截断（fc/ 30+ 文件漏 battery.c 的假象）。

## 9. 附录（R2 修复批 Task 48-49，2026-11-13）：crashflip/AH 符号链/RTC 权威事实

- **crashflip 开环模型**（mixer.c L275-345 + mixer_init.c 实读）：不走 PID、
  油门完全不参与；电机=f(杆量)：expo 曲线（crashflip_expo PG 默认 35）、
  死区在 expo 域（expo(0.15)≈0.0987 非 0.15 字面）、三轴主导仲裁
  （abs 比较但长度替换用 expo）、cos30° 非对角独占、逐电机负截断
  （crashflip_motor_percent PG 默认 0）、杆回中全停待命；退出唯一路径=
  撤 box。教训：任务书常量须按固件原文域写（死区两处坑：字面值与域）。
- **AH 符号链**：world.euler_deg=bullet 语义 pitch 正=低头
  （euler_stick_deg 取 −y/z 得杆义）；ATTITUDE(108) 同源 → 与固件惯例
  （imu.c:447 "negative is backwards"）一致。固件 AH：pitch 正（低头）→
  线上移。教训：同一姿态量跨层消费时先钉符号链再写渲染公式（R1 即栽在
  把 bullet 语义当杆义用）。flight.py 快照 docstring 曾误标「pitch抬头」。
- **osd_items_e id**：UP_DOWN_REFERENCE=**60**（61=TX_UPLINK_POWER）；
  72-76=SYS 族续、77-79=GPS_LAP 三件、79=GPS_LAP_TIME_BEST3。
  教训：简报手写 id 必机械复数（本轮 team-lead 笔误 61，实现者纠出）。
- **SET_RTC(246) 载荷** = u32 unix 秒 + u16 毫秒（Configurator
  MSPHelper.ts:3126-3134 push32/push16）。仿真存 virtual.rtc_unix_s
  additive 字段；**不置 changed**——Configurator 握手即发 246，置了会在
  飞行中误触发 set_params 重建。
- **字形取证**：GLYPH_ARROW_N/S/E/W=0x60/0x64/0x68/0x6C（官方
  osd_symbols.h 逐一相符）；'_'=0x5F（.mcm 位图实测下划线）。
- **G_FORCE(42) 跳过依据**：world 无加速度输出、RAW_IMU acc 为静态 1g
  占位——无数据源不编造。
- **取源教训补**：gh api contents 目录列表 per_page 默认 30 静默截断；
  raw/jsdelivr 不可达时 `git clone --depth 1 --branch <tag> --filter=blob:
  none --sparse` 可靠；部分克隆 blob 按需取可能瞬时失败（重试或换源）。
