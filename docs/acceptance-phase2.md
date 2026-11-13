# 二期人工验收清单

> 自动化已覆盖：424 项单元/集成测试（`python -m pytest test/ -v`，2026-11-13 全绿）与帧循环冒烟（`python main.py --frames 120` 退出码 0）。
> 状态标注：**已验证·自动化**＝对应 pytest 测试钉住（括号列测试文件）；**已验证·实测**＝真实地面站/真机操作在案（记录见 `docs/notes/2026-11-12-msp-spike-facts.md`，下称 spike 笔记，2026-11-12 用户实测）；**待实测**＝已实现待真人执行，通过后勾选并注日期（Task 29 记录进 spike 笔记 §9、Task 30 进 §10）。
> 已实测项环境：Betaflight Configurator **浏览器形态 2026.12.0-alpha**；桌面 Tauri 形态（raw TCP 通道）重验为第四节末尾 Task 30 范围。
> 六节对应设计 §13 里程碑 M1–M7（对照表见文末）。

## 一、预设管理（M1）

- [x] **列表与选中**：主菜单「无人机参数」进入预设列表；点击行内按钮以外区域选中为当前机（行高亮＋「当前无人机」标签）；当前机文件缺失回落列表第一台；列表为空自动创建「默认机」。**已验证·自动化**（test_config_store.py：current_drone_id 往返/缺省回落、列表排序忽略 .bak；test_drone_params_ui.py：列表/编辑/模态渲染）
- [x] **复制**：行内「复制」弹代号输入框（预填源代号，直接确认即得带序号新预设）；代号 sanitize（路径分隔符与控制字符替换 `_`，空白回退）；重名自动追加序号；内容深拷贝。**已验证·自动化**（test_config_store.py：copy_preset 重名追加/内容一致/sanitize 四项）
- [x] **删除**：行内「删除」弹模态确认；删除当前机后回落列表第一台；删除不存在目标静默；代号经 sanitize 防路径穿越。**已验证·自动化**（test_config_store.py：delete_preset 三项）
- [x] **改名**：编辑态顶部代号输入框，保存时「存新文件＋删旧文件」；代号冲突提示。**已验证·自动化**（test_config_store.py：save/load/delete 往返与 sanitize 组合）
- [x] **旧档迁移**：一期 `config/drone.json` 首次启动深拷贝为 `config/drones/默认机.json`，旧档改名 `.migrated` 归档不再读取；迁移幂等；v3/v4 档 additive 升级到 schema 5（调参字段保留）。**已验证·自动化**（test_config_store.py：migrate_legacy_drone 两项＋v3/v4→v5 升级三项）
- [x] **损坏预设与原子写**：坏 JSON 备份 `.bak` 并回落默认值，不阻塞其余预设；保存走临时文件原子替换。**已验证·自动化**（test_config_store.py：corrupt/atomic 四项）
- [ ] **交互实机一遍**：列表点选、复制弹窗、删除模态、改名、重启保持——渲染层只钉了不崩溃，交互链真人过一遍。**待实测**（第六节回归段顺带）

## 二、AUX 输入（M2）

- [x] **手柄按钮两档**：`aux1..4` 绑 button（未按 −1 / 按下 +1）；绑定序号超出设备按钮数按未绑定处理。**已验证·自动化**（test_input.py：aux_button_two_level＋越界防御）
- [x] **手柄轴三段**：`aux1..4` 绑 axis3（轴值 <−1/3 / 中间 / >1/3 → −1/0/+1）；未绑定恒中位；绑定轴号超出设备实测轴数按未绑定回中（一期遗留：换设备防御）。**已验证·自动化**（test_input.py：aux_axis3 三项＋主轴越界回中）
- [x] **AUX 配置界面**：遥控通道设置页增 AUX 绑定区（来源下拉＋对象选择），保存重启保持；向导不变仍只配主四通道。**已验证·自动化**（test_config_store.py：mappings aux 键默认/往返三项）
- [x] **键盘 F1/F2/F4**：拨动式 AUX1/2/3——按一下升档、松开保持、再按降档；AUX3 用 F4 避开 F3 调试覆盖层热键。**已验证·自动化**（test_input.py：F 键常量/首按升档/松开保持/再按降档/键间隔离/上升沿单次六项）
- [x] **µs 映射**：AUX 值映射 MSP RC 通道 5–8（1500＋值×500），与飞行帧 box 评估同一数据源。**已验证·自动化**（test_msp_handlers.py：rc_payload/rc_no_flight）
- [ ] **手柄实机绑定与预览**：真手柄 AUX 绑定后预览随开关/轴段变化；飞行中 AUX 进地面站 Receiver 页通道条。**待实测**（含 Task 25 遗留「Receiver 通道条 RPYT 实时动」复测）
- [ ] **HUD 输入来源徽标**（一期遗留 minor）：参数模式飞行 HUD 显示键盘/手柄设备名徽标，拔插切换时跟随实际输入源。**待实测**（视觉项，随第六节回归段实飞顺带）
- [ ] **手柄热插拔三场景**：启动后接入自动拾取→飞行/配置中拔出回落键盘并提示→重插恢复原绑定。**待实测**（Task 38 复测；拔出回落一期已验，插入恢复为新功能）

## 三、虚拟飞控运行时（M3）

- [x] **ARM 油门保护**：仅虚拟飞控模式启用（参数模式无模式系统保持一期行为）；ARM 框激活且油门 <0.10 才解锁；激活但油门高保持加锁；ARM 撤销 disarm＋三轴 PID 积分清零；Esc 重置位置自动 disarm。**已验证·自动化**（test_drone_modes.py：arm gate 三项＋leaving disarm＋参数模式跳过）
- [x] **未解锁输出 0**：四电机无杆量响应，每帧重置 PID。**已验证·自动化**（test_drone_modes.py：disarmed_frames_reset_pid）
- [x] **ANGLE 自稳**：目标倾角＝杆量×45°，外环 8 °/s/° 收敛替换内环 rate setpoint；yaw 保持 rate 闭环；无姿态数据时回落 rate 模式。**已验证·自动化**（test_drone_modes.py Task 11 六项）
- [x] **HORIZON**：中位自稳、杆外 acro（权重 w＝clamp(1−|rc|/0.6)）；与 ANGLE 同开时 ANGLE 优先。**已验证·自动化**（test_drone_modes.py Task 12 五项）
- [x] **反乌龟**：armed＋（|roll|>120° 或 |pitch|>120°）＋油门 <0.35＋FLIP box 激活 → 翻正态（主导轴叠加朝最短翻正方向 ±400°/s）；三路析取退出：角度迟滞（回 ±90° 内）或 FLIP box 撤销或油门 ≥0.35 接管。**已验证·自动化**（test_drone_modes.py Task 13 十项，含迟滞带、三路退出、disarm 复位）
- [x] **box 评估**：mode_ranges → 激活 box 集合，闭区间边界；飞行每帧与 MSP STATUS 应答共用同一实现。**已验证·自动化**（test_drone_modes.py：eval_boxes 四项）
- [x] **参数页双模式转换**：参数模式→虚拟飞控＝调参字段快照进 `virtual.tune`；虚拟飞控→参数模式回写 UI；硬件参数（重量/KV/推重比/电压/节数/阻力/摄像头角度）两模式共享单份；飞行消费 `active_tune()` 按 virtual_fc 切换。**已验证·自动化**（test_config_store.py：snapshot 深拷贝/active_tune 切换/sync 解耦/往返五项）
- [x] **飞行中地面站写参数保 armed**：MSP SET → 参数重建但 armed 保持（脏标志消费点回写），PID 积分清零按重建语义。**已验证·自动化**（test_drone_modes.py：msp_dirty_reapply_keeps_armed）
- [ ] **实飞生效**：地面站 Modes 页把 ARM/ANGLE/FLIP OVER AFTER CRASH 绑到 AUX1/2/3 → 飞行页 F1 解锁起飞、F2 自稳悬停收敛、F4 倒扣翻正后推油门接管。**待实测**（Task 30 Step 2 第 3 项；单层逻辑全部自动化，此为整机行为复验）

## 四、MSP 地面站（M4＋M5＋M7 传输）

### 通道与接入

- [x] **三通道同启**：「接入地面站」一次开启 TCP＋WebSocket＋PTY 三传输，提示框显示 `tcp://127.0.0.1:5761`、`ws://127.0.0.1:5762` 与 PTY slave 路径；「断开连接」全关且可重新接入。**已验证·自动化**（test_msp_server.py 18 项：TCP/PTY loopback、启停重开；test_msp_websocket.py 16 项：RFC6455 握手/二进制帧/ping-pong/分片重组）
- [x] **端口占用与创建失败**：OSError → 错误提示，不崩溃、不改变连接状态。**已验证·自动化**（test_msp_server.py）
- [x] **飞行中保持连接**：参数页开启后进飞行页连接保持，地面站可观察实时姿态。**已验证·实测**（三轮复测姿态仪实时数据 ✓，飞行页运行中）

### 连接与身份（M4）

- [x] **连接识别**：身份组（API_VERSION 1.46 / FC_VARIANT BTFL / FC_VERSION 4.5.1 / BUILD_INFO / BOARD_INFO / UID）→ MSP2_GET_TEXT 三连（BUILDKEY/CRAFT_NAME/PILOT_NAME）→ 未实现命令空载荷降级 → Configurator 进入 Setup 页，固件身份显示 `Fw git rev: -11-12`。**已验证·实测**（spike 笔记 §5：2026-11-12 浏览器形态全序列走完）
- [x] **MSPv1/v2 帧协议**：v1 `$M` len/cmd 均 u8＋XOR 校验和（len=0xFF jumbo 转义）；v2 `$X` flag/cmd u16LE/len u16LE＋CRC8-DWD-SID86；未知命令空载荷降级不断连。**已验证·自动化**（test_msp_protocol.py）＋**实测**（spike 笔记 §2.5/§6/§7：三轮实测驱动修复后的线格式定论）

### 各 tab 对接（M5）

- [x] **Setup 页与姿态仪**：STATUS_EX（含 armingDisableFlags）/ATTITUDE/RC/RAW_IMU 实时数据驱动；飞行页未开时快照恒零（预期）。**已验证·实测**（三轮复测姿态仪 ✓）
- [x] **PID tab 回读**：PID 数据组应答与 `virtual.tune` 一致。**已验证·实测**（首轮复测 PID 回读 ✓）；「修改→保存→回读一致」桌面全流程**待实测**（Task 30 Step 2 第 2 项）
- [x] **rate/TPA 写入**：RC_TUNING 与 PID_ADVANCED（TPA/cells）写回 `virtual.tune`，短帧防御。**已验证·自动化**（test_msp_handlers.py：rc_tuning get/set、set_pid_advanced 写回、short_guard）
- [x] **Modes 配置回读**：BOXNAMES（尾分号修复后四开关齐全）/BOXIDS/MODE_RANGES(_EXTRA) 应答。**已验证·实测**（Modes 回读 ✓）；**反乌龟行显示与可配置保存回读待实测**（Task 37 复测：Modes 页四行齐全）
- [x] **Receiver 页识别**：Receiver Mode 显示 MSP 接收机（MIXER_CONFIG 补全加载链）。**已验证·实测**（三轮复测 ✓）；**通道条 RPYT/CH5-8 实时动待实测**（Task 25 遗留）
- [x] **OSD tab 配置侧**：元素清单＋预览网格＋PAL 30×16＋计时器/警告全渲染。**已验证·实测**（二轮复测截图，M6 元素表校准输入）
- [x] **Ports 页**：UART1 行显示（functionMask 恒含 MSP 位防误关）。**已验证·实测**（三轮复测 ✓）
- [x] **CLI**：`#` 进入、version 输出、dump 诚实错误行、exit 返回 MSP 模式；坏帧不误切。**已验证·实测**（三轮复测 ✓）＋**自动化**（test_msp_cli.py 21 项）
- [x] **EEPROM 落盘**：EEPROM_WRITE → 虚拟飞控段整体原子保存预设文件；重复写不置脏。**已验证·自动化**（test_msp_handlers.py：eeprom_write 三项）；桌面版「保存→重连回读一致」**待实测**（Task 30 Step 2 第 4 项）
- [x] **摄像头角度双向**：地面站写 camera_angle 写回共享硬件参数。**已验证·自动化**（test_msp_handlers.py）；实机回读**待实测**（Task 25 遗留）

### 桌面版与 PTY（M7 收尾，Task 30）

- [ ] **桌面版 raw TCP 全流程**：安装 2026.6.2 deb 桌面版＋Expert Mode → 手动选择 → `tcp://127.0.0.1:5761`：① 连接序列/Setup 身份显示；② PID 修改保存回读一致；③ Modes 配 ARM/ANGLE/反乌龟＋飞行页 F1/F2/F4 生效；④ OSD 布局编辑→EEPROM 保存→重连回读一致＋飞行页布局变化；⑤ 控制台缺失命令清单记录（spike 笔记 §10）。**待实测**（Task 30 Step 1/2，spike 笔记 §5.1 三项随之勾选）
- [ ] **PTY 通道降级验证**：`socat -d -d pty,raw,echo=0 pty,raw,echo=0` 建 PTY 对 → 模拟器接入地面站后向 slave 写 API_VERSION 请求帧（v1 六字节 `24 4D 3C 00 01 01`）→ 对端读到应答帧字节。**待实测**（Task 30 Step 3；PTY loopback 自动化已绿，此为外部工具互证）

## 五、OSD（M6）

- [x] **字符集与图集**：MAX7456 256 字符 .mcm 位图解析＋GL 纹理图集烘焙（GPL 来源文件头标注）。**已验证·自动化**（test_osd_font.py 11 项：字形网格/ASCII 可见性/烘焙像素映射）
- [x] **内容生成**：16 元素子集（RSSI/主电压/十字线/水平线/计时器 1/计时器 2/FLYMODE/craft name/油门/速度/高度/单芯电压/俯仰角/横滚角/DISARMED/vario）按 30×16 PAL 网格生成；位置编码与 Configurator 同构（bit11 可见位）；地面站配了未实现元素时忽略不画。**已验证·自动化**（test_osd.py 27 项：默认布局锚点/可见开关/闪烁相位窗/文本截断/各元素数值格式）
- [x] **craft name 语义**：地面站 MSP2_SET_TEXT 写入优先，空则显示预设代号；非 ASCII 归一化。**已验证·自动化**（test_osd.py＋test_msp_handlers.py）
- [x] **冒烟**：`python main.py --frames 120` 退出码 0。**已验证**（2026-11-13 本清单生成时复跑）
- [ ] **默认布局实飞**：16 元素显示且数值随飞行变化（电压/单芯/速度/高度/vario/姿态/计时器/FLYMODE/油门/RSSI）。**待实测**（Task 29 第 1 项）
- [ ] **DISARMED 闪烁与解锁**：上锁 2Hz 闪烁；F1 解锁后消失、上锁重现。**待实测**（Task 29 第 2 项）
- [ ] **姿态方向**：滚转/俯仰时姿态数值与水平线箭头偏移方向正确。**待实测**（Task 29 第 3 项）
- [ ] **fresh 预设默认布局**：从未接过地面站的预设直接飞，出厂默认布局可用。**待实测**（Task 29 第 4 项）
- [ ] **倒置行为**：姿态角超 ±90° 倒置后 OSD 数值与闪烁正常。**待实测**（Task 29 第 5 项）
- [ ] **模式切换渲染**：虚拟飞控模式隐藏一期 HUD（含输入徽标）只显 OSD 于左上原位；参数模式 HUD 原样零变化；Esc 暂停菜单在 OSD 之上可弹出可操作。**待实测**（Task 29 实飞顺带）
- [ ] **地面站布局联动**：Configurator OSD tab 调整元素位置 → EEPROM 保存 → 飞行页 OSD 布局随之变化。**待实测**（与 Task 30 Step 2 第 4 项同一次操作）
- [ ] **vfc 表单顺路项**：F 键提示行显示；电池节数输入 ≤1 钳位为 1；机体段与节数间分隔线；键盘模式参数飞行 HUD 不再显示「（n 路未绑定）」。**待实测**（Task 29/30 实测顺带；节数钳位写回已自动化）

## 六、回归段（一期验收项重跑，参数模式）

- [x] **自动化回归**：一期测试全部存续且全绿（套件自一期 71 项增长至 424 项，M1–M7 开发全程零回归约束）。**已验证·自动化**（2026-11-13 全量复跑）
- [x] **参数模式行为零变化**：一期 HUD/输入徽标/Esc 菜单在参数模式原样（模式系统与 OSD 仅在 virtual_fc=True 生效）。**已验证·自动化**（test_drone_modes.py 参数模式跳过项＋test_flight_select_ui.py）
- [ ] **一期手动验收项重跑**：`docs/acceptance-phase1.md` 第 1–13 项（飞行手感 6＋参数配置 2＋手柄 3＋界面 2）在参数模式下全部重跑。**待实测**（与 Task 29 同一真人场次完成；手柄项与第二节热插拔复测合并）
- [x] **一期已知限制复核**：低帧率时间膨胀——性能优化已在本分支交付（缓存渲染/着色器预设，60fps；见 README「着色器预设」节），一期清单该项不再适用。**已验证**（README 与 docs/shader_packs.md 在案）

---

**里程碑对照**（设计 §13）：M1＝第一节；M2＝第二节（含一期 minor 输入钳位/HUD 徽标）；M3＝第三节（含参数页双模式转换）；M4＝第四节「连接与身份」；M5＝第四节「各 tab 对接」；M6＝第五节；M7＝第四节「桌面版与 PTY」＋本清单与 README 更新自身。全部里程碑已交付；「待实测」项集中在 Task 29（OSD 实飞）、Task 30（桌面版与 PTY）与衍生复测（Modes 反乌龟行、热插拔三场景、Receiver 通道条、摄像头角度实机回读），实测通过后由后续提交勾选。
