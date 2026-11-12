# MSP spike 协议事实清单（Task 17 交付物）

日期：2026-11-12；验证环境：Betaflight App **2026.12.0-alpha**（master 开发版，Linux）；模拟器提交 76bcbeb。
用途：第二份计划（docs/plans/2026-11-12-phase2-msp-osd.md，M5-M7）的协议输入。

## 1. 连接握手（已实测成功）

- **通道**：Configurator Expert Mode → 设备列表「手动选择」（i18n key `portsSelectManual`）→ PortOverride 地址框输入 `tcp://127.0.0.1:5761` → Connect。**桌面版（Tauri）raw TCP 可用**；串口授权弹窗只在未开 Expert Mode 时出现（默认 USB 路径）。
- **版本兼容**：master alpha（2026.12.0）实测可连——比设计锚定的 2026.6.2 更新的版本同样工作（身份组协议稳定）。用户环境无需回装稳定版。
- **MSP 帧版本协商**：应答 MSP_API_VERSION(1) = `[mspProtocolVersion=0, 1, 46]`，Configurator 以 MSPv1 帧通信（实测未发 $X v2 帧）。
- **身份组**：FC_VARIANT(2)=`BTFL`、FC_VERSION(3)=`4.5.1`、BUILD_INFO(5)、BOARD_INFO(4)、UID(160) 全部被接受，连接保持。
- **未知命令容错**：空载荷应答不导致断连（Configurator 对未实现命令回空即继续）。

## 2. 已知表现与 M5 待实现项

- **PID tab / Rate 显示默认值**：Configurator 请求 PID/rate 系列命令得到空载荷 → 回落 UI 默认。M5 需实现：MSP_PID、MSP_PID_ADVANCED、MSP_RC_TUNING（rate 读写，cmd 号以 MSPCodes.ts 核对——注意 204=MSP_SET_RC_TUNING）、以及对应的 SET_* 写命令 → `virtual.tune`。
- **Modes / OSD tab**：同属 M5（MODE_RANGES 系 / OSD_CONFIG 系）。

## 3. 命令号权威事实（在线核实 Configurator master MSPCodes.ts，2026-11-12）

- MSP_API_VERSION=1、MSP_FC_VARIANT=2、MSP_FC_VERSION=3、MSP_BUILD_INFO=**5**、MSP_BOARD_INFO=**4**、MSP_UID=160、MSP_SET_RC_TUNING=204。
- 教训：命令号一律以 MSPCodes.ts 在线核实，勿凭记忆（本次 BOARD_INFO/BUILD_INFO 记忆错位在 Task 16 审查中被执行者发现、controller 在线核实修正）。

## 4. 待补充观察（等用户反馈）

- [ ] Setup 页显示的板卡/版本信息内容（验证 BOARD_INFO/BUILD_INFO 载荷形态是否被正确解析）
- [ ] Configurator 控制台缺失/超时命令清单（连接后逐 tab 点开时的报错命令号）→ 决定 M5 命令集优先级
- [ ] Modes tab / OSD tab 的可用性表现（是否报错/灰置）
