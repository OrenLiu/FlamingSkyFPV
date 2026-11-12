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

## 3. 命令号权威事实（在线核实 Configurator master MSPCodes.ts，2026-11-12）

- MSP_API_VERSION=1、MSP_FC_VARIANT=2、MSP_FC_VERSION=3、MSP_BUILD_INFO=**5**、MSP_BOARD_INFO=**4**、MSP_UID=160、MSP_SET_RC_TUNING=204。
- 教训：命令号一律以 MSPCodes.ts 在线核实，勿凭记忆（BOARD_INFO/BUILD_INFO 记忆错位在 Task 16 审查中被发现并修正）。
- 另核实：manual 条目 i18n key `portsSelectManual`（zh-CN 文案「手动选择」），`manualModeAvailable = networkOnly || (showManualMode && isExpertModeEnabled())`。

## 4. 服务器侧已验证事实（无 Configurator 参与的自证）

- TCP/PTY 双传输 loopback 单测 10 项全绿（243 项套件）；X 下端到端：真实主循环 drain 应答 6 条身份握手（tcp://127.0.0.1:5761 + /dev/pts/N）。
- 身份组应答：API_VERSION(1)=[0,1,46]（mspProtocolVersion=0 → v1）、FC_VARIANT(2)=BTFL、FC_VERSION(3)=4.5.1、BUILD_INFO(5)、BOARD_INFO(4)、UID(160)。

## 5. 待桌面版重测后补充

- [ ] Setup 页身份显示（验证 BUILD_INFO/BOARD_INFO 载荷形态解析）
- [ ] 控制台缺失/超时命令清单（M5 命令集优先级依据）
- [ ] Modes/OSD tab 表现
