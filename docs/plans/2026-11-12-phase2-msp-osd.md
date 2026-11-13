# FlamingSkyFPV 二期实现计划（M5-M7）

本计划为规格式实现计划（第一份 `docs/plans/2026-11-12-phase2.md` 的续篇，Task 编号自 19 续起）：覆盖设计文档 §13 里程碑 M5-M7（MSP 全命令集逐 tab 对接 / OSD 渲染与飞行集成 / 桌面版实测与文档收尾）。协议事实输入：`docs/notes/2026-11-12-msp-spike-facts.md`（§2.5/§6/§7 v1/v2 线格式与连接序列全貌，M4 已实测打通）。任务卡给精确接口与逐条行为断言，pytest 由执行者按断言逐条写出（先测后码）；纯 UI/联调任务以手动验收清单替代自动化断言。

**Goal:** MSP 全命令集（状态组 / 参数读写组 / 模式组 / OSD 组 / 固化组）接通 Configurator 各 tab；Configurator 侧可调的 PID/rate/TPA/模式开关/OSD 布局读写映射到 `virtual` 域并即时生效、EEPROM 落盘回读一致；OSD 字符集资产与内容生成（约 16 个首批元素）；飞行界面虚拟飞控模式隐藏一期 HUD 改渲 OSD；桌面版 Configurator 与 PTY 双通道实测；验收清单与 README 收尾。

**Architecture:** 沿用一期 Screen 状态机 + imgui 全权 UI；依赖单向 `screens → physics/input/msp/osd → config_store`；`msp/`、`osd.py`、`osd_font.py`、`physics/`、`input/` 禁 import 渲染/UI 库可无头单测；MSP 服务器经 state_provider/on_change 与 App 层单向耦合；OSD 位图渲染走 imgui draw list（封面纹理同款生命周期）。

**Tech Stack:** Python 3.10.6（系统 pyenv，不建 venv）、soup3D 7.0.0（vendored 副本含 rend_cached）、pygame 2.6.1、pyimgui 2.0.0、PyBullet、pytest。

**Spec:** `docs/specs/2026-11-12-phase2-design.md`（§8 MSP / §9 OSD / §10 错误表 / §12 风险 / §13 里程碑）＋ `docs/notes/2026-11-12-msp-spike-facts.md`（协议线格式权威事实）。

## Global Constraints

- 依赖方向：`screens → physics/input/msp/osd → config_store`；`msp/`、`osd.py`、`osd_font.py`、`physics/`、`input/` 禁 import 渲染/UI 库（soup3D/pygame/imgui）。`msp/handlers.py` 可 import `osd.py`（元素表与默认布局单一来源），`osd.py` 仅可 import 标准库与 config_store
- 回归基线：`python -m pytest test/ -v` 全绿（269 项基线 + 各任务新增）；参数模式零回归——`virtual_fc=False` 时一期飞行行为与 HUD 不变
- imgui 2.0 已知坑：`input_float`/`input_int`/`input_text` 返回 `(changed, value)`；中文字体加载后必须 `refresh_font_texture`；imgui 帧内禁止 GL 资源创建与字体图集操作（OSD 图集烘焙走 App 帧边界标记机制，同 shaderpacks 重载先例）
- 协议纪律：MSP 命令号与载荷编码一律以外部源码为准——Configurator master `src/js/msp/MSPCodes.ts`/`MSPHelper.ts` 与固件 4.5.1 tag `src/main/msp/msp.c`（2026-11-12 在线核对，引用行号见各任务卡）；计划标注的参考值与外部源码不一致时按外部源码实现并在提交说明记录差异；实现期抓取复核后写码（勿凭记忆，M1-M4 期间记忆错误六次的教训在案）
- 测试惯例：纯逻辑测试直接构造对象断言（参考 `test/test_msp_server.py`、`test/test_config_store.py`）；`FlightController`/`eval_boxes`/`msp`/`osd` 可无头单测；UI/联调任务以手动验收清单替代 pytest
- git 约定：中文提交信息 + `feat:`/`fix:`/`test:`/`docs:` 前缀 + 结尾 `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`；`test/`、`CLAUDE.md`、`todo.md`、`scene/*`、`.omc`、`.claude` 不入提交

## 任务依赖与执行顺序

- Task 19 → 20 → 21 → 22 → 23 → 24：MSP 命令集逐组叠加（同 `msp/handlers.py` 依次扩展）；Task 19 的 state_provider/on_change 是 20-24 全部命令的前置
- Task 25 依赖 19-24（Configurator 全 tab 手动联调）；Task 27 依赖 25 的实测元素清单校准
- Task 26 与 27 相互独立可并行；Task 28 依赖 26+27
- Task 29 依赖 28（OSD 手动验收）；Task 30 依赖 25（桌面版全流程重验）
- Task 31、32 收尾：依赖此前全部任务

---

### Task 19: 状态源架构（VfcServer state_provider / on_change 注入）

**Files:**
- Modify: `msp/server.py`、`msp/handlers.py`、`app.py`、`screens/flight.py`
- Test: `test/test_msp_server.py`

**Interfaces:** Produces
- `VfcServer(handlers: dict[int, Callable], state_provider: Callable[[], dict | None] | None = None, on_change: Callable[[], None] | None = None)`：每次 `_dispatch` 前调用一次 `state_provider()`，结果作为 handler 第二参传入；handler 在 state dict 置 `state["changed"] = True` 时，dispatch 末尾调用一次 `on_change()`（SET_* 即时生效信号）
- handlers 协议扩为 `(payload: bytes, state: dict | None) -> bytes`（`state` 带默认 `None`；一期身份组 lambda 改两参形态，单参调用点保持合法）
- `make_identity_handlers()` 保持函数名与单参兼容（lambda 签名 `lambda _payload, _state=None: ...`）
- `FlightScreen.flight_snapshot() -> dict | None`：flying 且未暂停返回 `{"armed": bool, "boxes": frozenset, "aux_us": list, "stick_us": tuple, "throttle": float, "attitude_deg": tuple, "gyro_dps": tuple, "speed_mps": float, "alt_m": float, "vario_mps": float, "flight_time_s": float}`；其余态返回 `None`（attitude_deg 取 `world.euler_deg` bullet 语义，非杆义）
- `FlightScreen.__init__` 增 `self._flight_time_s = 0.0`，`update()` flying 未暂停分支累加 `dt`
- `App._msp_state() -> dict | None`：汇集 `{"params": self.drone_params, "preset_id": self.settings.current_drone_id, "flight": flight_snapshot 或省略键}`（飞行页不在场时无 `"flight"` 键＝基础字段）
- `App._msp_on_change()`：置 `self.msp_params_dirty = True`（`__init__` 初始化 `False`）
- `App.__init__` 末尾：`self.msp_server = VfcServer(make_identity_handlers(), state_provider=self._msp_state, on_change=self._msp_on_change)`
- `FlightScreen.update()` virtual_fc 分支首行：`if self.app.msp_params_dirty: self._fc.set_params(self.app.drone_params); self.app.msp_params_dirty = False`（MSP SET 后 PID 重建即时生效）
- app.py 顶部 `from screens.flight import FlightScreen`（app → screens 方向允许）

- [ ] **Step 1: 写失败测试**（追加到 test/test_msp_server.py），断言清单：
1. 注入 `state_provider=lambda: {"k": 1}` 构造 VfcServer；注册探针 handler 记录第二参实参 → 经 loopback 请求后探针收到的 state `== {"k": 1}`
2. `state_provider=lambda: None` → handler 收到 `None`；不传 state_provider（默认参数）→ 同样收到 `None`
3. 探针 handler 置 `state["changed"]=True` → `on_change` 恰回调一次；下一请求未置 → 不再回调
4. handler 抛异常 → 空载荷应答且 `on_change` 未触发（异常路径不产生变更信号）
5. `make_identity_handlers()` 全部单参调用保持合法：`h[1](b"")==bytes([0,1,46])` 等一期断言零回归
6. `FlightScreen.flight_snapshot()` 离线构造（`_world`/`_fc` 手工赋值 `None`）→ 返回 `None`（非 flying 态防御）

- [ ] **Step 2: 运行确认失败**
`python -m pytest test/test_msp_server.py -v` → `TypeError`（VfcServer 构造不认 state_provider）

- [ ] **Step 3: 实现**（要点）
- server.py `_dispatch`：`state = self._state_provider() if self._state_provider else None`；`payload = handler(req.payload, state)`；帧构造后 `if state is not None and state.pop("changed", False) and self._on_change: self._on_change()`
- handlers.py 身份组 lambda 全部补第二参默认 None
- flight.py：`_flight_time_s` 累加与 `flight_snapshot()`（attitude_deg 用 `self._world.euler_deg`、vario 用 `self._world.velocity[2]`、speed 用 velocity 模、boxes/aux_us/throttle 缓存自 update() 求值结果）
- app.py：`_msp_state`/`_msp_on_change` 两方法 + msp_server 构造改造；FlightScreen import

- [ ] **Step 4: 运行通过**
`python -m pytest test/ -v` 全绿（269 基线 + 新增）

- [ ] **Step 5: Commit**
```
feat: MSP 状态源架构——VfcServer 注入 state_provider/on_change

dispatch 每请求注入状态快照（预设参数+飞行遥测），handler 二参协议
（缺省 None 兼容一期调用点）；SET 写信号经 on_change 置
msp_params_dirty，飞行帧首重建飞控参数即时生效。

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
```

---

### Task 20: 状态组命令（STATUS / STATUS_EX / ATTITUDE / RAW_IMU / MSP_RC）

**Files:**
- Modify: `msp/handlers.py`
- Test: `test/test_msp_handlers.py`（新建，handler 纯函数直测：手工构造 state dict 传入）

命令号参考值（MSPCodes.ts 2026-11-12 在线核对）：`MSP_STATUS=101`、`MSP_STATUS_EX=150`、`MSP_RAW_IMU=102`、`MSP_RC=105`、`MSP_ATTITUDE=108`。

载荷布局参考值（固件 4.5.1 `msp.c` STATUS_EX 写序 1080-1128 行与 Configurator `MSPHelper.ts` 解码段 841-893 行互证；字段值可按本任务裁定）：
- STATUS/STATUS_EX 共享头：cycleTime u16、i2cError u16、activeSensors u16、mode u32、profile u8——STATUS(101) 应答共 11 字节到此为止；STATUS_EX(150) 续：cpuload u16、numProfiles u8、rateProfile u8、flagByteCount u8、armingDisableCount u8、armingDisableFlags u32、configStateFlag u8、cpuTemp u16（API 1.46 尾）
- `mode u32 = Σ(1 << box_id)`（state 快照 boxes；ARM=bit0）；armed 即 state 快照 armed，经 boxes 含 ARM 反映
- `armingDisableFlags` 编码：count u8 + flags u32 LE（源码核实布局）；模拟器无禁锁枚举 → 恒 `count=0`、`flags=0`（油门保护拒绝解锁不映射禁锁位——位号未核实不冒充，语义注记在代码注释）
- ATTITUDE(108)：3×i16 LE = `(roll×10, pitch×10, yaw)` 取整（bullet 语义姿态角，world.euler_deg）；无飞行快照 → 3×0
- RAW_IMU(102)：9×i16 LE：acc 3 轴恒 `(0, 0, 2048)`（水平 1g 静态，Configurator ÷2048 还原 g）；gyro 3 轴 = `round(dps × 4.1)`（Configurator ×4/16.4 还原 °/s）；mag 3 轴 0
- MSP_RC(105)：8×u16 LE µs = `[油门, yaw, roll, pitch, AUX1..AUX4]`；油门 µs = `1000 + throttle×1000`，其余 `1500 + v×500`；无飞行快照 → 油门 1000、其余 1500

- [ ] **Step 1: 写失败测试**（新建 test/test_msp_handlers.py），断言清单（h = make_identity_handlers()）：
1. 五个命令号键存在：`{101, 150, 102, 105, 108} <= set(h)`
2. STATUS：`h[101](b"", {"flight": {"boxes": {0, 1}}}) == struct.pack("<HHHIB", 500, 0, 33, 3, 0)`（activeSensors=33=ACC|GYRO 位；mode=0b11；共 11 字节）
3. STATUS_EX：`h[150](b"", {"flight": {"boxes": {0, 1}}}) == struct.pack("<HHHI", 500, 0, 33, 3) + struct.pack("<BHBB", 0, 0, 1, 0) + struct.pack("<BBIBH", 0, 0, 0, 0, 0)`（共 24 字节；profile=0、cpuload=0、numProfiles=1、rateProfile=0、尾段全 0）
4. STATUS_EX 无 flight 键 → mode u32==0 的等价载荷（与断言 3 仅 mode 字段不同）
5. ATTITUDE：`h[108](b"", {"flight": {"attitude_deg": (10.0, -5.0, 90.0)}}) == struct.pack("<hhh", 100, -50, 90)`
6. ATTITUDE 无 flight → `struct.pack("<hhh", 0, 0, 0)`
7. RAW_IMU：`h[102](b"", {"flight": {"gyro_dps": (100.0, 0.0, 0.0)}}) == struct.pack("<hhh", 0, 0, 2048) + struct.pack("<hhh", 410, 0, 0) + struct.pack("<hhh", 0, 0, 0)`（18 字节）
8. RC：`h[105](b"", {"flight": {"throttle": 0.5, "stick_us": (1600, 1700, 1800), "aux_us": [1900.0, 1500.0, 1200.0, 1500.0]}}) == struct.pack("<8H", 1500, 1600, 1700, 1800, 1900, 1500, 1200, 1500)`（通道序 [油门, yaw, roll, pitch, AUX1-4]；16 字节）
9. RC 无 flight → `struct.pack("<8H", 1000, 1500, 1500, 1500, 1500, 1500, 1500, 1500)`（油门 1000、其余 1500）

- [ ] **Step 2: 运行确认失败**
`python -m pytest test/test_msp_handlers.py -v` → 断言 1 失败（五个命令号键缺失，空载荷应答）

- [ ] **Step 3: 实现**（要点）
- `_status_payload(state, extended: bool)` builder（STATUS=共享头 11 字节、STATUS_EX=24 字节）+ 模块常量 `_SENSORS = 33`（ACC|GYRO 位）与 gyro 换算常数 `4.1`（注释注明 Configurator ×4/16.4 逆换算）
- struct helper：`_i16(v) = max(-32868, min(32767, round(v)))` 钳位
- state 缺键防御：`state.get("flight") or {}` 起手，缺键字段按规格零/中位值
- 断言 1 的 `<=` 集合包含检查过门（先测后码的门面）

- [ ] **Step 4: 运行通过**
`python -m pytest test/ -v` 全绿

- [ ] **Step 5: Commit**
```
feat: MSP 状态组命令——STATUS/STATUS_EX/ATTITUDE/RAW_IMU/RC

Setup 页实时遥测：mode 由快照 boxes 位或组成（ARM=bit0）；禁锁位
恒零（未核实的位号不冒充）；gyro 换算 4.1 为 Configurator 4/16.4 逆。

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
```

---

### Task 21: 参数读写组（PIDNAMES / PID / PID_ADVANCED / RC_TUNING 与对应 SET_*）

**Files:**
- Modify: `msp/handlers.py`
- Test: `test/test_msp_handlers.py` 追加

命令号参考值（MSPCodes.ts 在线核对）：`MSP_PIDNAMES=117`、`MSP_PID=112`、`MSP_SET_PID=202`、`MSP_PID_ADVANCED=94`、`MSP_SET_PID_ADVANCED=95`、`MSP_RC_TUNING=111`、`MSP_SET_RC_TUNING=204`。

**载体更正注记**：master MSPCodes.ts 无独立 RATE_PROFILE 命令——rate 曲线经 RC_TUNING(111)/SET_RC_TUNING(204) 传输（MSPHelper.ts 解码段 1147-1180 行、SET 编码段 2599-2638 行核对）；PID tab 顺带消费 PID_ADVANCED(94) 的 TPA 与 D-Min 段。

**Interfaces:** Produces（msp/handlers.py）
- `_active_tune(state) -> TuneParams`：`params.virtual_fc=True` → `params.virtual.tune` 本体；False → `params.snapshot_tune()`（只读快照）
- SET 路径恒写 `params.virtual.tune` 本体（设计 §8.3：SET 写 virtual 域；参数模式无地面站入口，写入不生效于仿真），写后置 `state["changed"] = True`
- PID(112) GET：5 条目 × 3 u8（固件 4.5.1 pid.h 79-84 行 `PID_ITEM_COUNT=5`：ROLL/PITCH/YAW/LEVEL/MAG）＝15 字节；值 = BF 展示刻度 u8 原样，换算 `u8 = int(clamp(v, 0, 255) + 0.5)`
- PID 换算表（GET/SET 对称，缩放因子 MSPHelper.ts 1182-1192/2590-2597 段核对）：
  | 仿真字段 | MSP 字段 | 换算 |
  |---|---|---|
  | pid[ax].p / .i | PID P / I u8 | ×1（默认 40 双向无损） |
  | pid[ax].d | PID D u8 | ×100（默认 0.4 ↔ 40；仿真 D 标定域与 BF 展示域差百倍，见 config_store._default_pid 注释） |
  | LEVEL/MAG 两条目 | PID 第 4/5 条目 | 恒 0,0,0（仿真无对应） |
  换算注记：yaw P 默认 400 超 u8 域 → GET 报 255、未编辑保存回读 255.0（诚实降级，Task 25 记录实测）
- PIDNAMES(117)：`b"ROLL;PITCH;YAW;LEVEL;MAG"`（Configurator 按 0x3b 分隔解析，条目数与 PID 一致）
- SET_PID(202)：15 字节同布局解析，roll/pitch/yaw 写回，LEVEL/MAG 忽略
- RC_TUNING(111) GET：23 字节固定布局（MSPHelper.ts 1147-1180 核对）：RC_RATE u8、RC_EXPO u8、roll_rate u8、pitch_rate u8、yaw_rate u8、reserved u8=0、throttle_MID u8=50、throttle_EXPO u8=0、reserved u16=0、RC_YAW_EXPO u8、rcYawRate u8、rcPitchRate u8、RC_PITCH_EXPO u8、throttleLimitType u8=0、throttleLimitPercent u8=0、三轴 rate_limit u16=500（参考值）、rates_type u8
- RC_TUNING 换算表（×100，双向）：`rate.roll.rc_rate`↔RC_RATE、`rate.roll.expo`↔RC_EXPO、`rate.roll.super_rate`↔roll_rate、pitch/yaw 同构（pitch rc_rate↔rcPitchRate、yaw rc_rate↔rcYawRate、pitch expo↔RC_PITCH_EXPO、yaw expo↔RC_YAW_EXPO）；throttle MID/EXPO、rate_limit、rates_type、throttleLimit 为只读参考值（SET 忽略——linear 无地面站等价档，rate_type 保持预设值，诚实降级）
- PID_ADVANCED(94) GET：59 字节固定布局（MSPHelper.ts 1959-2028 解码段核对：12×u16 + 35×u8，字段序照源码）＝ `b"\x00"*46 + pack("<B", cells) + b"\x00"*9 + pack("<BH", int(tpa_percent), 1000 + round(tpa_breakpoint×1000))`；非零语义字段仅：autoProfileCellCount(s8)=virtual.cells、tpaRate u8=tpa_percent、tpaBreakpoint u16=断点 µs（1000-2000）；D-Min/FF 等 44 字段恒 0（诚实降级：仿真无对应，SET 全部忽略）
- SET_PID_ADVANCED(95)：len≥59 才消费；仅取 autoProfileCellCount（≥1 → `virtual.cells`）与尾部 tpaRate/tpaBreakpoint（percent ← byte、breakpoint ← (µs−1000)/1000 钳位 [0,1]），置 changed

- [ ] **Step 1: 写失败测试**（追加 test/test_msp_handlers.py），断言清单（h = make_identity_handlers()；p = DroneParams()）：
1. 命令键存在：`{94, 95, 111, 112, 117, 202, 204} <= set(h)`
2. PIDNAMES：`h[117](b"", {}) == b"ROLL;PITCH;YAW;LEVEL;MAG"`
3. PID GET：`p.virtual_fc=True`、`p.virtual.tune.pid["roll"] = PIDGains(42.0, 45.0, 0.5)` → `h[112](b"", {"params": p}) == struct.pack("<15B", 42, 45, 50, 40, 40, 40, 255, 100, 0, 0, 0, 0, 0, 0, 0)`（pitch 默认 (40,40,0.4)→(40,40,40)、yaw P=400 钳 255）
4. PID GET 参数模式：`p2.virtual_fc=False` + 平铺 `pid["roll"].p = 45.0` → 应答首字节 == 45（active_tune 平铺路径）
5. SET_PID：`h[202](struct.pack("<15B", 46, 50, 45, 41, 41, 41, 255, 101, 1, 0, 0, 0, 0, 0, 0), {"params": p})` → `p.virtual.tune.pid["roll"]==(46.0, 50.0, 0.45)`、`pid["pitch"]==(41.0, 41.0, 0.41)`、`pid["yaw"]==(255.0, 101.0, 0.01)`（D ÷100）且 `state["changed"] is True`
6. RC_TUNING GET：`tune.rate["roll"] = RateParams(1.20, 0.75, 0.40)`、pitch rc_rate=1.10、yaw expo=0.35 → `h[111](b"", {"params": p}) == struct.pack("<6B", 120, 40, 75, 70, 70, 0) + struct.pack("<2B", 50, 0) + struct.pack("<H", 0) + struct.pack("<4B", 35, 100, 110, 40) + struct.pack("<2B", 0, 0) + struct.pack("<3H", 500, 500, 500) + bytes([0])`（23 字节）
7. RC_TUNING 默认参数 → RC_RATE=100、roll_rate=70、thrMID=50、limits=500×3、rates_type=0（恒定参考值段）
8. SET_RC_TUNING：23 字节载荷（RC_RATE=150、RC_EXPO=32、roll_rate=80、其余同断言 6 布局）→ `tune.rate["roll"] == RateParams(1.50, 0.80, 0.32)`（pitch/yaw 段回默认值不变）且 changed=True
9. PID_ADVANCED GET：`p.virtual.cells=6`、TPA (65.0, 0.30) → `h[94](b"", {"params": p}) == b"\x00"*46 + struct.pack("<B", 6) + b"\x00"*9 + struct.pack("<BH", 65, 1300)`（59 字节）
10. SET_PID_ADVANCED：载荷 `b"\x00"*46 + pack("<B", 4) + b"\x00"*9 + pack("<BH", 80, 1450)` → `virtual.cells==4`、`tpa_percent==80.0`、`tpa_breakpoint==0.45` 且 changed=True
11. SET_PID_ADVANCED len<59（20 字节）→ 参数不变（len 守卫防御）
12. `python -m pytest test/ -v` 全绿零回归

- [ ] **Step 2: 运行确认失败**
`python -m pytest test/test_msp_handlers.py -v` → 断言 1 失败（命令键缺失）

- [ ] **Step 3: 实现**（要点）
- 换算 helper：`_u8(v) = int(min(255.0, max(0.0, v)) + 0.5)`；`_set_changed(state)` 写 `state["changed"]=True`
- SET 解析按 GET 布局对称（`len(payload) >= N` 守卫；len<守卫值 → 整体忽略）
- PID D ×100 常数（`_PID_D_SCALE = 100.0`）注释引 config_store._default_pid 的 D=0.4 默认与 PID_MIX_SCALING 背景
- rates_type/throttle MID/EXPO/limits/rates_type 恒参考值段写成模块常量（GET/SET 对称往返）

- [ ] **Step 4: 运行通过**
`python -m pytest test/ -v` 全绿

- [ ] **Step 5: Commit**
```
feat: MSP 参数读写组——PID/PID_ADVANCED/RC_TUNING 与 SET_* 全套

PID 5 条目×3 u8（D ×100 域换算）、PID_ADVANCED 59B（TPA/cells 语义，
D-Min/FF 诚实降级）、RC_TUNING 23B（rate 三轴 ×100 换算，throttle/
limits/rates_type 只读参考）；SET 写 virtual.tune 本体并置 changed。

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
```

---

### Task 22: 模式组（BOXNAMES / BOXIDS / MODE_RANGES / MODE_RANGES_EXTRA / SET_MODE_RANGE）

**Files:**
- Modify: `msp/handlers.py`
- Test: `test/test_msp_handlers.py` 追加

命令号参考值（MSPCodes.ts 在线核对）：`MSP_BOXNAMES=116`、`MSP_BOXIDS=119`、`MSP_MODE_RANGES=34`、`MSP_SET_MODE_RANGE=35`、`MSP_MODE_RANGES_EXTRA=238`。

**Interfaces:** Produces（msp/handlers.py）
- `MODE_RANGE_SLOTS = 20`（固件 4.5.1 fc/rc_modes.h:93 `MAX_MODE_ACTIVATION_CONDITION_COUNT=20` 核对）
- BOXNAMES(116)：`b"ARM;ANGLE;HORIZON;FLIP OVER AFTER CRASH"`（分隔符 0x3b，MSPHelper.ts:1276-1295 解析段核对；显示名参考值，实现期以固件 box 名称表核对）
- BOXIDS(119)：`bytes([0, 1, 2, 21])`（permanentId 参考值——plan 1 Task 10 已按固件 box_ids.h 核对：ARM=0/ANGLE=1/HORIZON=2/FLIP_OVER_AFTER_CRASH=21）
- MODE_RANGES(34) GET：20 槽 × 4 字节（permanentId u8、auxChannelIndex u8、startStep u8、endStep u8）；µs↔step：`step = (µs−900) // 25`（Configurator MSPHelper.ts:1753-1770 解码段与 567-586 编码段核对）
- MODE_RANGES(34) GET 读 `virtual.mode_ranges`（出厂默认 `[(0, 0, 1700, 2100)]` 即 ARM=AUX1 高段，Modes 页开箱可用）；未配置槽位恒 `(0, 0, 0, 0)`
- MODE_RANGES_EXTRA(238)：count u8=20 + 每槽 (permanentId, modeLogic=0, linkedTo=0)（MSPHelper.ts:1772-1786 解码段核对；permanentId 与 MODE_RANGES 槽位对齐）
- SET_MODE_RANGE(35)：请求 7 字节 = slot u8 + permanentId u8 + auxChannelIndex u8 + startStep u8 + endStep u8 + modeLogic u8 + linkedTo u8（Configurator 编码段核对）；接受 len 5（无尾部两字节的旧形态，尾部缺省 0）；`slot >= 20` 整体忽略；写 `virtual.mode_ranges[slot] = (permanentId, aux, 900+startStep×25, 900+endStep×25)`（列表补长；条目存元组），置 changed

- [ ] **Step 1: 写失败测试**（追加 test/test_msp_handlers.py），断言清单：
1. 命令键存在：`{34, 35, 116, 119, 238} <= set(h)`
2. BOXNAMES：`h[116](b"", {}) == b"ARM;ANGLE;HORIZON;FLIP OVER AFTER CRASH"`
3. BOXIDS：`h[119](b"", {}) == bytes([0, 1, 2, 21])`
4. MODE_RANGES GET 默认：`virtual.mode_ranges=[(0,0,1700,2100)]` → `h[34](b"", {"params": p}) == struct.pack("<4B", 0, 0, 32, 48) + struct.pack("<76B", *([0]*76))`（80 字节；startStep=(1700−900)/25=32、endStep=48）
5. 自定义条目 `[(21, 2, 1700, 2100)]` → 槽 0 = `pack("<4B", 21, 2, 32, 48)`
6. MODE_RANGES_EXTRA：`h[238](b"", {"params": p}) == struct.pack("<B", 20) + bytes(60)`（61 字节）
7. SET_MODE_RANGE 7 字节 `(0, 21, 2, 32, 48, 0, 0)` → `virtual.mode_ranges[0]` 值组 == (21, 2, 1700, 2100) 且 changed=True
8. SET slot=3 → 列表补长后槽 3 == (21, 2, 1700, 2100)，槽 1/2 保持缺省 (0, 0, 0, 0)
9. SET len=5 旧形态 `(0, 21, 2, 32, 48)` → 尾部两字节缺省 0，写入同断言 7
10. SET 清空形态 `(0, 1, 0, 0, 0, 0, 0)` → 槽值 == (0, 1, 900, 900)（µs∈[1000,2000] 永不激活，eval_boxes 语义下等价清空）
11. SET slot=25 → 忽略不抛（slot 守卫）
12. 全绿零回归

- [ ] **Step 2: 运行确认失败**
`python -m pytest test/test_msp_handlers.py -v` → 断言 1 失败（命令键缺失）

- [ ] **Step 3: 实现**（要点）
- step↔µs helper：`_step_to_us(s) = 900 + s*25`、`_us_to_step(us) = (us−900)//25`
- GET 组帧：遍历 20 槽取 `virtual.mode_ranges` 条目或缺省 (0, 0, 0, 0)；EXTRA 恒零语义段
- SET：slot 守卫 + 列表补长写元组 + changed；len 5/7 双形态兼容

- [ ] **Step 4: 运行通过**
`python -m pytest test/ -v` 全绿

- [ ] **Step 5: Commit**
```
feat: MSP 模式组——BOXNAMES/BOXIDS/MODE_RANGES 与 SET_MODE_RANGE

20 槽 ×4B 步进编码（µs=900+step×25）；出厂默认 ARM=AUX1 高段开箱
可用；EXTRA 恒零语义段；SET 兼容 5/7 字节双形态并置 changed。

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
```

---

### Task 23: OSD 组（OSD_CONFIG / SET_OSD_CONFIG / MSP2_GET_TEXT 扩展 / MSP2_SET_TEXT）

**Files:**
- Create: `osd.py`（本任务只建数据表段：元素表与默认布局；内容生成 Task 27 扩展）
- Modify: `msp/handlers.py`
- Test: `test/test_msp_handlers.py` 追加

命令号参考值（MSPCodes.ts 在线核对）：`MSP_OSD_CONFIG=84`、`MSP_SET_OSD_CONFIG=85`、`MSP2_SET_TEXT=0x3007`（MSP2_GET_TEXT=0x3006 已有）。

**Interfaces:** Produces
- `osd.py` 数据表段（供本任务与 Task 27/28 共用，元素序对齐固件 4.5.1 osd.h `osd_items_e`）：
  - `OSD_IMPLEMENTED: dict[int, str]`：16 个首批元素 {0:RSSI_VALUE, 1:MAIN_BATT_VOLTAGE, 2:CROSSHAIRS, 4:HORIZON_SIDEBARS, 5:TIMER_1, 6:TIMER_2, 7:FLYMODE, 8:CRAFT_NAME, 9:THROTTLE_POS, 13:GPS_SPEED, 15:ALTITUDE, 22:AVG_CELL_VOLTAGE, 26:PITCH_ANGLE, 27:ROLL_ANGLE, 29:DISARMED, 33:NUMERICAL_VARIO}（元素序对齐 4.5.1 osd.h osd_items_e；以 Task 25 实测清单校准增删）
  - `DEFAULT_LAYOUT: dict[int, int]`：出厂布局 u16 值表（规则 `raw = col | (row << 5) | 0x0800`，30×16 网格 col 0-29、row 0-15）：

    | 元素 | (col,row) | u16 |
    |---|---|---|
    | 0 RSSI_VALUE | (2,10) | 2370 |
    | 1 MAIN_BATT_VOLTAGE | (9,12) | 2441 |
    | 2 CROSSHAIRS | (14,7) | 2286 → **2253 (13,6)**（Task 40 更正，见下注） |
    | 4 HORIZON_SIDEBARS | (14,7) | 2286 → **2254 (14,6)**（Task 40 更正，见下注） |
    | 5 TIMER_1 | (2,1) | 2082 |
    | 6 TIMER_2 | (2,2) | 2114 |
    | 7 FLYMODE | (12,0) | 2060 |
    | 8 CRAFT_NAME | (10,15) | 2538 |
    | 9 THROTTLE_POS | (2,12) | 2434 |
    | 13 GPS_SPEED | (25,10) | 2393 |
    | 15 ALTITUDE | (23,10) | 2391 |
    | 22 AVG_CELL_VOLTAGE | (20,12) | 2452 → **2451 (19,12)**（Task 40：图标化 6 格左移避让 vario） |
    | 26 PITCH_ANGLE | (15,13) | 2479 |
    | 27 ROLL_ANGLE | (2,13) | 2466 |
    | 29 DISARMED | (12,4) | 2188 |
    | 33 NUMERICAL_VARIO | (25,12) | 2457 |

    （换算自检锚点：id 1 → 9|384|2048=2441；id 33 → 25|384|2048=2457。
    **Task 40 实现期更正**：id 2/4 原表 (14,7) 双双居中系建表期近似——固件
    pgResetFn_osdElementConfig 为 CROSSHAIRS (midCol-2,midRow-1)=(13,6)、
    HORIZON_SIDEBARS (midCol-1,midRow-1)=(14,6)，与 Configurator osd.js
    defaultPosition 公式（cols/2 + (rows/2-2)·cols − 2/−1）互证；已改为
    2253/2254，十字线锚点语义同步改为「锚点=左段格」见 Task 40 执行记录）
- OSD_CONFIG(84) GET：190 字节固定布局（固件 4.5.1 msp.c 937-1053 写序与 Configurator osd.ts decode 2265-2350 解码段互证；**实现期更正**：item_count=80、统计段 count=0——原 194/81 系简报笔误，见 Task 23 执行记录）：
  - 头部 10 字节：osdFlags u8=0x31（bit0 FEATURE|bit4 MAX7456|bit5 DEVICE_DETECTED）、video_system u8=1（PAL → Configurator VIDEO_TYPES[1]="PAL" → 网格 30×16，osd_constants.ts:25 核对）、unit_mode u8=0、rssi_alarm u8=0、cap_alarm u16=0、obsolete u8=0、item_count u8=**80**（4.5.1 osd.h osd_items_e 机械计数 0..79；Task 23 实现期更正，原 81 系简报笔误）、alt_alarm u16=0
  - item_pos u16×81：`pos = stored & 0x07FF`，`stored = virtual.osd_layout.get(id, DEFAULT_LAYOUT.get(id, 0))`；可见位合成 `raw = pos | (0x0800 if virtual.osd_enabled.get(id, pos != 0) else 0)`（未配置元素的可见性跟随位置位非零）
  - 统计段 count u8=**0**（不写条目——Configurator decodeStatItems 为无边界守卫的 count 驱动循环，非零且缺条目会错位解码）；计时器段 count u8=**2** + u16×2=0；警告段 obsolete u16=0 + count u8=**19** + flags u32=0；OSD profile 段 number u8=1、selected u8=1；overlay_radio_mode u8=0；camera_frame w/h u8=24/11；link_quality_alarm u16=0（API 1.46 尾）
  - 应答总长恒 **190** 字节（10+160+1+**5**+7+2+1+2+2）
- SET_OSD_CONFIG(85) 请求消费规则：addr u8 + value u16（+可选 screen u8 忽略）：
  - `addr == 0xFF`（通用设置块）、`addr == 0xFE`（计时器块）、统计屏开关（screen=0 形态）→ 整体忽略
  - `addr < 81` → 元素写：`virtual.osd_layout[addr] = value & 0x07FF`（仅位置位）、`virtual.osd_enabled[addr] = bool(value & 0x0800)`，置 changed
  - 变体位（bits 14-15）忽略
- MSP2_GET_TEXT(0x3006) 扩展（_get_text 改造为 state 感知）：
  - 类型 2 CRAFT_NAME：`virtual.craft_name or preset_id`，UTF-8 编码（中文经地面站显示可能乱码为可接受参考语义，OSD 侧渲染有 ASCII 归一）
  - 类型 3 PID_PROFILE_NAME / 4 RATE_PROFILE_NAME：恒 `b"FSKY"`（参考值）
  - 类型 1/5/6/11 与未知类型：保持既有空串/空载荷降级
- MSP2_SET_TEXT(0x3007)：请求 `[类型 u8][文本字节]`；类型 2 → `virtual.craft_name = text.decode("utf-8", "replace")[:32]`、置 changed；其余类型整体忽略（空载荷 ack）

- [ ] **Step 1: 写失败测试**（追加 test/test_msp_handlers.py），断言清单（h = make_identity_handlers()；p = DroneParams()）：
1. 命令键存在：`{84, 85, 0x3007} <= set(h)`（0x3006 已有）
2. OSD_CONFIG GET 默认预设（layout/enabled 空）→ `r = h[84](b"", {"params": p})`：`len(r)==190`、`r[0]==0x31`、`r[1]==1`（PAL）、`r[7]==80`（item_count 在偏移 7）
3. 电压元素（id 1）默认位置：`struct.unpack_from("<H", r, 12)[0] == 2441`（偏移 10+1×2；DEFAULT_LAYOUT[1]）
4. 未实现元素（id 14 GPS_SATS）位置 u16 == 0（偏移 10+14×2）
5. 覆盖布局：`p.virtual.osd_layout = {1: 0x0043}`、`osd_enabled = {1: True}` → id 1 位置 u16 == 0x0843；`osd_enabled = {1: False}` → == 0x0043
6. SET_OSD_CONFIG 元素写 `h[85](struct.pack("<BH", 1, 0x0843), {"params": p})` → `osd_layout[1]==0x0043`、`osd_enabled[1] is True`、changed=True
7. SET value=0x0043 → `osd_enabled[1] is False`（可见位清除语义）
8. SET 通用设置块 addr=0xFF（12 字节载荷 `pack("<BH", 0xFF, 0) + bytes(9)`）→ virtual 两 dict 不变
9. SET addr=0xFE 计时器块 → 忽略
10. MSP2_GET_TEXT：`virtual.craft_name="FSKY-1"` → `h[0x3006](bytes([2]), state) == b"\x02FSKY-1"`；craft_name 空 + preset_id="测试机" → `== bytes([2]) + "测试机".encode()`
11. GET_TEXT 类型 3/4 → `b"\x03FSKY"` / `b"\x04FSKY"`；类型 5 → `bytes([5])`
12. MSP2_SET_TEXT：`h[0x3007](b"\x02Beta-1", {"params": p})` → `craft_name=="Beta-1"` 且 changed=True；类型 5 → craft_name 不变
13. 全绿零回归

- [ ] **Step 2: 运行确认失败**
`python -m pytest test/test_msp_handlers.py -v` → 断言 1 失败（命令键缺失）

- [ ] **Step 3: 实现**（要点）
- `_osd_config_payload(state)` 组帧 helper；`_elem_pos(state, id)` 合成位置/可见位
- `osd.py` 只含数据表与合成函数（无 GL/pygame import）；handlers.py import osd 的两张表
- SET 解析：len≥3 消费 addr+value；len≥3 后的 screen 字节读出忽略

- [ ] **Step 4: 运行通过**
`python -m pytest test/ -v` 全绿

- [ ] **Step 5: Commit**
```
feat: MSP OSD 组——OSD_CONFIG/SET_OSD_CONFIG 与 GET_TEXT 扩展/SET_TEXT

81 元素布局读写（位置 11 位 + 可见位 0x0800 分解到 osd_layout/osd_enabled）；
出厂默认布局 16 元素开箱可用；craft name 地面站写入 virtual 域。

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
```

---

### Task 24: 固化组与顺路批（EEPROM_WRITE / CF_SERIAL_CONFIG / no-op 降噪 / 存储健壮性）

**Files:**
- Modify: `msp/handlers.py`、`msp/server.py`（print→logging）、`config_store.py`、`screens/drone_params.py`
- Test: `test/test_msp_handlers.py` 追加、`test/test_config_store.py` 追加

命令号参考值（MSPCodes.ts 在线核对）：`MSP_EEPROM_WRITE=250`、`MSP_CF_SERIAL_CONFIG=54`、`MSP_SET_CF_SERIAL_CONFIG=55`、`MSP_RESET_CONF=208`、`MSP_SET_RTC=246`。

**Interfaces:** Produces
- EEPROM_WRITE(250)：空载荷 ack；执行 `save_preset(DRONES_DIR, state["preset_id"], state["params"])` 落盘（config_store 常量与函数 import 进 handlers——msp → config_store 方向允许）；落盘动作不置 changed（落盘非参数变更信号）
- CF_SERIAL_CONFIG(54)：单端口应答 `struct.pack("<BHBBBB", 0, 1, 5, 5, 5, 5)`＝8 字节（identifier 0=UART1（serial.h:81-99 核对）、functionMask u16=1=MSP 位（serial.h:37 `FUNCTION_MSP = 1<<0` 核对）、四 baud 索引 5=115200（MSPHelper.ts BAUD_RATES 数组核对））
- SET_CF_SERIAL_CONFIG(55)：载荷整体忽略不消费——UART1 functionMask 恒含 MSP 位，Ports 页保存无法误关（设计 §8.3）
- RESET_CONF(208)、SET_RTC(246)：显式空载荷 no-op handler（连接序列杂项静音；未知命令保持既有空载荷降级）
- 顺路批（M5 批 deferred minors，全部本任务收口）：
  - ConfigStore.save 原子写（config_store.py:257-260 直写改 tmp + `os.replace`；异常路径清理 tmp 不损坏旧文件）
  - active_tune docstring 本体引用说明（config_store.py:139-143：`virtual_fc=True` 时返回 `virtual.tune` 本体非拷贝，MSP SET 路径为唯一合法写方）
  - delete_preset sanitize（config_store.py:300-305：id 经 `sanitize_preset_id` 后删除）
  - `_gs_error` 列表态可见性（drone_params.py `_render_list` 增错误文本行，同 `_render_edit` 315-316 行形态）
  - cells 下限钳位（drone_params.py:368-369 `p.virtual.cells = max(1, int(cells))`）
  - print→logging（msp/ 全目录：模块级 `log = logging.getLogger(__name__)`；handler 缺席→debug、handler 异常→warning、传输降级/清理→info；不调 basicConfig——根 logger 默认态 debug/info 丢弃、warning 经 lastResort 落 stderr，stdout 干净）

- [ ] **Step 1: 写失败测试**，断言清单（h = make_identity_handlers()）：
1. 命令键存在：`{54, 55, 208, 246, 250} <= set(h)`
2. CF_SERIAL_CONFIG：`h[54](b"", {}) == struct.pack("<BHBBBB", 0, 1, 5, 5, 5, 5)`（8 字节）
3. EEPROM_WRITE 落盘：tmp_path 建预设目录 + `p.virtual.tune.pid["roll"] = PIDGains(42.0, 45.0, 0.5)` + state `{"params": p, "preset_id": "测试机"}` → `h[250](b"", state)` 后 `load_preset(dir, "测试机").virtual.tune.pid["roll"].p == 42.0`
4. EEPROM 不置 changed：断言 3 调用后 `state.get("changed")` 非 True
5. SET_CF_SERIAL_CONFIG 任意载荷 → 应答 b"" 且参数不变
6. RESET_CONF / SET_RTC → 应答 b""
7. ConfigStore.save 原子写（test_config_store.py 追加）：save 后目录无 `.tmp` 后缀残留；monkeypatch `json.dump` 抛异常 → 原文件内容保留、tmp 已清理
8. delete_preset sanitize：`delete_preset(dir, "a/b")` → 删除的是 `a_b.json`、不抛异常
9. print→logging 机械校验：`grep -rn "print(" msp/` 零命中
10. 全绿零回归

- [ ] **Step 2: 运行确认失败**
`python -m pytest test/test_msp_handlers.py -v` → 断言 1 失败（命令键缺失）

- [ ] **Step 3: 实现**（要点）
- EEPROM handler 引 config_store 的 `DRONES_DIR`/`save_preset`；preset_id 取 `state["preset_id"]`，`state["params"]` 缺键防御（任一缺键 → 只回 ack 不落盘）
- no-op 表并入 make_identity_handlers：`{55, 208, 246}` 三键恒 `b""`
- 原子写：字符串路径 → `tmp = path + ".tmp"`，写 tmp → `os.replace(tmp, path)`，except 分支删除 tmp 后原样抛出
- logging 改造：server.py/handlers.py 模块级 logger；`[msp]` 前缀消息改 log.debug/info/warning 分级，删除 print 与 sys.stderr 引用
- `_gs_error` 可见性与 cells 钳位两处 UI 改动走 Task 25 手动列表态检查

- [ ] **Step 4: 运行通过**
`python -m pytest test/ -v` 全绿

- [ ] **Step 5: Commit**
```
feat: MSP 固化组与顺路批——EEPROM 落盘/串口组/no-op 降噪 + 存储健壮性

EEPROM_WRITE 落盘当前预设；CF_SERIAL_CONFIG 单端口 UART1 恒含 MSP
位；SET_RTC/RESET_CONF/SET_CF_SERIAL_CONFIG 静音 ack；ConfigStore.save
原子写与 delete sanitize；msp/ print 全量改 logging 分级。

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
```

---

### Task 25: Configurator 全 tab 手动对接验证（手动任务）

**Files:**
- Create: `docs/notes/2026-11-12-msp-spike-facts.md` §8 追加「M5 全命令集实测记录」节

**交付物：** M6 需要的实测记录（OSD tab 实际暴露元素清单 + 缺失命令清单）。操作性步骤而非 TDD；需用户配合（浏览器版 Configurator 现可用，桌面版在 Task 30）。

- [ ] **Step 1: 模拟器侧准备**
- `python main.py` → 无人机参数 → 编辑当前机 → 转虚拟飞控 → 接入地面站（提示框三地址）

- [ ] **Step 2: 浏览器版 Configurator（ws://127.0.0.1:5762）逐 tab 实测**
1. Setup tab：身份显示（FSKY/4.5.1/BTFL）+ 实时数据区出现姿态/仪表数值（非空、随飞行页姿态变化）
2. PID tab：读取显示默认 PID/rate/TPA 值；修改 roll P 40→45 → 保存 → 断开重连 → 回读 45（round-trip 一致）；飞行中改 P 即时手感变化（msp_params_dirty 生效）
3. Modes tab：ARM/ANGLE/HORIZON/FLIP OVER AFTER CRASH 四行名显示；配置 ARM=AUX1 高段与反乌龟=AUX3 高段 → 保存 → 重连回读一致；模拟器侧按 F1/F4 飞行验证模式生效
4. OSD tab：元素清单与预览渲染出现（记录 OSD tab 实际暴露的元素清单全文——M6 Task 27 校准输入）；编辑电压/高度元素位置 → 保存 → 重连回读一致
5. Ports tab：UART1 显示 MSP 功能位（CF_SERIAL_CONFIG 应答形态）
6. Options→固件保存（EEPROM）：保存动作后模拟器预设文件落盘（`config/drones/<id>.json` 修改时间更新）→ 重连回读一致
7. 全程记录控制台缺失命令清单（与 Task 18 spike 同格式）写入 spike 事实 §8

- [ ] **Step 3: 模拟器侧回归冒烟**
`python main.py --frames 120` 退出码 0；参数模式预设不受影响

- [ ] **Step 4: Commit**
```
docs: M5 全命令集实测记录——全 tab 联调事实入 spike 笔记 §8

Setup 实时数据/PID 修改保存回读/Modes 配置回读/OSD 布局编辑/EEPROM
落盘回读与缺失命令清单（Task 27 元素清单校准输入）。

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
```

---

### Task 26: MAX7456 字符集资产（.mcm 解析 → GL 纹理图集）

**Files:**
- Create: `assets/osd_betaflight.mcm`（自 Configurator 仓库 `resources/osd/1/betaflight.mcm` 下载入库，jsDelivr 目录 API 核实的路径；GPL-3.0 来源在 osd_font.py 文件头标注）
- Create: `osd_font.py`（.mcm 解析纯函数 + GL 图集烘焙/释放）
- Test: `test/test_osd_font.py`（新建，解析器离线单测）

**Interfaces:** Produces（osd_font.py）
- `parse_mcm(path: str) -> list[list[list[int]]]`：解析 .mcm 文本格式（头行 `MAX7456` + 256 字符 × 54 字节（ASCII 十六进制字符），每字节 4 像素 2bpp）→ 256 个 12×18 网格（值 0-3）；格式细节实现期以实际文件为准
- `MAX7456_FORMAT = ("MAX7456", 256, 12, 18, 54)` 格式常量
- `GLYPH_*` 命名常量（Task 27 消费：十字线/水平线箭头等特殊字形索引）——实现期以 betaflight.mcm 实际位图核对（BF 字符集 ASCII 区与 ASCII 码恒等，特殊字形在 0xD0-0xFE 区）

- [ ] **Step 1: 写失败测试**（新建 test/test_osd_font.py），断言清单：
1. 资产文件存在：`os.path.exists("assets/osd_betaflight.mcm")` 且文件头 7 字节 == b"MAX7456"
2. `parse_mcm("assets/osd_betaflight.mcm")` 返回 256 字符
3. 每字符网格 12×18，像素值 ∈ {0, 1, 2, 3}
4. ASCII 恒等区：'0'（0x30）..'9'、'A'（0x41）..'Z' 各字符位图互不相同且非全透明
5. 特殊字形区非空：0xD0-0xFE 区至少 10 个字符非全透明（BF 特殊字形存在性）

- [ ] **Step 2: 运行确认失败**
`python -m pytest test/test_osd_font.py -v` → `ModuleNotFoundError: No module named 'osd_font'`

- [ ] **Step 3: 实现**（要点）
- parse_mcm 纯解析（标准库 re/split；256 段 × 54 字节文本 → 2bpp 展开）
- GL 段（osd_font.py 下半部，封面缓存 cover_cache.py 同款生命周期）：`bake_atlas(chars) -> (tex_id, w, h)` 按 256×(12×18) 平铺单通道纹理（alpha 门限 2bpp 值≥2 → 白、1 → 半透明）；`free_atlas(tex_id)`
- GL 段禁入 imgui 帧（App 帧边界标记机制，同 shaderpacks 重载先例）
- osd_font.py 文件头标注来源 URL 与 GPL-3.0 许可

- [ ] **Step 4: 运行通过**
`python -m pytest test/ -v` 全绿

- [ ] **Step 5: Commit**
```
feat: MAX7456 字符集资产与解析——.mcm 位图解析 + GL 图集烘焙

assets/osd_betaflight.mcm 入库（GPL-3.0 来源标注）；256 字符 12×18
2bpp 解析离线单测；GL 图集走封面纹理同款生命周期（帧边界烘焙）。

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
```

---

### Task 27: osd.py 内容生成（状态 → 30×16 字符网格 + 闪烁标记）

**Files:**
- Modify: `osd.py`（扩展 Task 23 建立的模块：数据表段之上加内容生成段）
- Test: `test/test_osd.py`（新建，无头单测）

**Interfaces:** Produces（osd.py）
- `OSD_COLS, OSD_ROWS = 30, 16`（MAX7456 PAL 网格，Configurator VIDEO_DEFAULTS PAL=30×16 核对）
- `build_frame(state: dict) -> dict[tuple[int, int], tuple[int, bool]]`：设计 §9 纯函数；键 (col, row)、值 (glyph_index, blink)；越界钳位与截断在生成端完成
- `compose_state(params, flight, preset_id, now_s) -> dict`：调用方（Task 28 flight.py）与单测共用的 state 组装函数——
  `{"layout": params.virtual.osd_layout, "enabled": params.virtual.osd_enabled, "armed": ..., "boxes": ..., "throttle": ..., "voltage_v": params.battery_voltage, "cells": params.virtual.cells, "speed_mps": ..., "alt_m": ..., "vario_mps": ..., "attitude_deg": ..., "flight_time_s": ..., "craft_name": virtual.craft_name or preset_id, "now_s": now_s}`
- 元素文本规格（ASCII 文本 → 逐字符 glyph（ASCII 区恒等），特殊字形用 GLYPH_*；
  **Task 40 实现期更正**：电压/RSSI/vario 按固件 4.5.1 osd_elements.c 补图标
  字形、十字线/侧边栏改固件绘制语义——原表的纯文本规格系计划期近似，已按下表
  覆盖，详见 task-40-report.md）：
  | 元素 | 文本/绘制规格 |
  |---|---|
  | RSSI_VALUE(0) | `f"R 99"` → **SYM_RSSI 图标 + `f"{99:2d}"`**（固件 `"%c%2d"`） |
  | MAIN_BATT_VOLTAGE(1) | `f"{v:.1f}V"` → **电池图标（芯压分段）+ 数值 + SYM_VOLT；小数位 v≥10?1:2** |
  | AVG_CELL_VOLTAGE(22) | `f"{v/cells:.2f}V"` → **电池图标 + 2 位小数（截断）+ SYM_VOLT** |
  | GPS_SPEED(13) | `f"{speed_mps*3.6:.0f}Kmh"` |
  | ALTITUDE(15) | `f"{alt_m:.0f}M"` |
  | NUMERICAL_VARIO(33) | `f"{vario_mps:+.1f}"` → **升/降箭头 + `f"{abs(v):.1f}"` + SYM_MPS（无符号）** |
  | PITCH_ANGLE(26) | `f"P{pitch:+.0f}"` |
  | ROLL_ANGLE(27) | `f"R{roll:+.0f}"` |
  | TIMER_1/2(5,6) | `f"{mm:02d}:{ss:02d}"`（flight_time_s） |
  | FLYMODE(7) | ANGLE∈boxes→"ANGLE"、HORIZON→"HORZN"、else "ACRO" |
  | CRAFT_NAME(8) | craft_name 非 ASCII 逐字符替换 "?"（MAX7456 ASCII 域） |
  | THROTTLE_POS(9) | `f"{thr*100:.0f}"` |
  | DISARMED(29) | "DISARMED"，`blink = int(now_s*2) % 2 == 1`（2Hz，相位关时整元素不画） |
  | CROSSHAIRS(2) | 中心十字 GLYPH_* 字形组合（字形实现期以字体资产核对）→ **锚点=左段格，0x72/0x73/0x74 自锚点起写**（固件 buff 写序） |
  | HORIZON_SIDEBARS(4) | ~~左右箭头对居中，列偏移 = clamp(round(roll×0.2), -5, 5)~~ → **固件静态背景：±7 列竖轨 0x13（锚点行 ±3）+ ±6 列 AH_LEFT/RIGHT 水平箭头，与姿态无关** |
- 可见性合成：`visible(id) = osd_enabled.get(id, raw!=0 判定)`；元素锚点 = `layout.get(id, DEFAULT_LAYOUT.get(id, None))`；`layout/enabled` 全空 → 默认布局生效（Task 29 默认可用性）
- 未实现元素（OSD_IMPLEMENTED 之外的 id）忽略不画（设计 §9）

- [ ] **Step 1: 写失败测试**（新建 test/test_osd.py），断言清单：
1. `build_frame({}) == {}`（空 state 防御；compose_state 缺键防御同语义）
2. 默认布局可用：state 仅含 layout/enabled 空字典与 now_s=10.0 等其余字段 → 网格含电压元素（(9,12) 起非空）与 craft name 元素（(10,15) 起非空）
3. enabled 关闭：`enabled={1: False}` → 电压元素格子消失
4. 覆盖位置：`layout={1: 0x0083}`（(3,4)；**实现期更正**：原 0x0043 解出 (3,2) 与 (3,4) 预期自相矛盾，系计划笔误）→ 电压文本锚点移到 (3,4)（(x,y) 转换 `col = raw & 0x1F | ((raw>>5) & 0x20)`、`row = (raw>>5) & 0x1F`
5. armed=True → DISARMED 无格子；armed=False + now_s=0.0 → DISARMED 在 (12,4) 出现
6. blink 相位：armed=False + now_s=0.6 → DISARMED 无格子（`int(now_s*2)%2` 相位窗 0.5s：0.0-0.49 显示、0.5-0.99 不显示）
7. 电压文本：voltage_v=14.8、cells=4 → AVG_CELL 文本 "3.70V" 的 ASCII 字形序列出现在锚点行
8. craft name 中文 "测试机" → 渲染为 "?" 串（ASCII 归一，长度不变）
9. 未实现元素 id 14 → 无格子
10. 文本越界：元素锚点 col=28 处 5 字符文本 → 截断到 30 列内（2 字符）
11. compose_state 全键填充断言（params=DroneParams() + flight=None → armed False、craft_name==preset_id）
12. 全绿零回归

- [ ] **Step 2: 运行确认失败**
`python -m pytest test/test_osd.py -v` → `AssertionError`（build_frame/compose_state 未实现）

- [ ] **Step 3: 实现**（要点）
- build_frame 主体：遍历 OSD_IMPLEMENTED → 可见性/锚点合成 → 元素文本规格表逐元素生成（文本→glyph 映射 ASCII 恒等 + GLYPH_* 特殊字形）
- 文本生成 helper：`_text_cells(text, col, row, blink=False)`（越界截断）
- DISARMED 相位关时跳过整元素（含十字线/侧边栏不受相位影响）
- osd.py 保持无 GL/pygame/imgui import（纯函数 + 数据表）

- [ ] **Step 4: 运行通过**
`python -m pytest test/ -v` 全绿

- [ ] **Step 5: Commit**
```
feat: OSD 内容生成——状态到 30×16 字符网格的纯函数

16 元素文本规格与 DISARMED 2Hz 闪烁；默认布局空配置可用；craft name
ASCII 归一；越界截断；compose_state 供飞行页与单测共用组装。

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
```

---

### Task 28: 飞行界面集成（虚拟飞控模式渲染 OSD）

**Files:**
- Modify: `screens/flight.py`、`screens/drone_params.py`
- Test: 手动验收清单（OSD 内容与可见性逻辑已由 Task 27 单测覆盖）

**Interfaces:** Produces / Consumes
- flight.py `render_ui`：`virtual_fc=True` 且 flying 态 → 跳过 `_render_hud`（含输入徽标一并隐藏，设计 §9），改调 `_render_osd()`；Esc 菜单保留；参数模式行为完全不变
- `_render_osd()`：`compose_state(params, flight_snapshot(), preset_id, time.monotonic())` → `build_frame` → imgui draw list 位图绘制：锚点屏幕 (24, 24)（一期 HUD 同位）、字符格 12×18、glyph UV 从 osd_font 图集取；blink 相位关的格子跳过
- 图集生命周期：FlightScreen.enter 置脏标记、帧边界 `bake_atlas`（imgui 帧外）、`exit()`/`reload()` 释放 `free_atlas`（封面纹理同款手法，imgui 帧内禁 GL 创建）
- drone_params.py `_render_vfc_form` 顺路批（M6 deferred minors）：
  - F 键提示行：表单头部提示文案行「开关通道：F1 解锁 / F2 自稳 / F4 反乌龟（在地面站 Modes 页绑定到 AUX1/2/3）」（Task 8 deferred）
  - separator（外观）：机体硬件段与电池节数输入之间加 `imgui.separator()`
- flight.py `_render_hud` 顺路批（M6 deferred）：「（n 路未绑定）」计数仅 joystick 模式显示（keyboard 模式无绑定概念，噪音抑制——Task 9 deferred）

- [ ] **Step 1: 手动验收清单**（逐条核对）
1. 参数模式飞行：HUD 与输入徽标原样显示（一期行为零变化）
2. 虚拟飞控模式飞行：一期 HUD（含输入徽标）隐藏，OSD 出现在左上原位；电压/单芯/速度/高度/vario/姿态/计时器/FLYMODE/craft name/油门/RSSI 元素按默认布局显示且数值随飞行变化
3. DISARMED 以 2Hz 闪烁显示；F1 解锁后消失
4. 十字线与水平线箭头居中显示；滚转时箭头对横向偏移
5. Esc 暂停菜单在 OSD 之上正常弹出与操作
6. Configurator OSD tab 调整元素位置并 EEPROM 保存 → 飞行页 OSD 布局随保存变化（回读一致）
7. vfc 表单显示 F 键提示行；电池节数输入 ≤1 时钳位为 1（Task 24 顺路批手动核对）
8. vfc 表单机体段与电池节数之间有分隔线
9. 键盘模式参数模式飞行：HUD 不再显示「（n 路未绑定）」文案
10. `python main.py --frames 120` 退出码 0（冒烟）

- [ ] **Step 2: Commit**
```
feat: 飞行界面集成——虚拟飞控模式 OSD 渲染与 HUD 隐藏

imgui draw list 位图绘制（图集帧边界烘焙、封面纹理同款生命周期）；
Esc 菜单保留；参数模式零变化；顺路收口 M6 批 deferred（F 键提示行/
separator/键盘 HUD 噪音抑制）。

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
```

---

### Task 29: OSD 手动验收（手动任务）

**Files:**
- Modify: `docs/notes/2026-11-12-msp-spike-facts.md` §9 追加「OSD 手动验收记录」节

**交付物：** Task 31 验收清单 OSD 段的原始素材；操作性步骤而非 TDD。

- [ ] **Step 1: 验收操作**（虚拟飞控模式飞行，需用户配合）
1. 默认布局飞行：16 元素显示与数值动态（电压/单芯/速度/高度/vario/姿态/计时器/craft name/油门/RSSI/FLYMODE）
2. F1 解锁 → DISARMED 消失；上锁 → 2Hz 闪烁重现
3. 滚转/俯仰时姿态元素数值与水平线箭头偏移方向正确
4. 无地面站布局时默认布局可用性（fresh 预设直接飞）
5. 姿态角超过 ±90° 后（倒置）OSD 数值与闪烁行为正常
6. 冒烟：`python main.py --frames 120` 退出码 0

- [ ] **Step 2: Commit**
```
docs: OSD 手动验收记录——16 元素/闪烁/默认布局实飞确认

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
```

---

### Task 30: 桌面版实测与 PTY 通道验证（手动任务）

**Files:**
- Modify: `docs/notes/2026-11-12-msp-spike-facts.md` §5.1 勾选 + §10 追加「桌面版实测记录」

**交付物：** 桌面版（Tauri raw TCP）全流程重验记录。操作性步骤；需用户配合（安装 deb）。

- [ ] **Step 1: 环境准备**（用户操作）
- 安装 Betaflight Configurator **2026.6.2** deb 桌面版（spike 事实 §2：alpha 为 dev-server 形态不适用）；Expert Mode 开启

- [ ] **Step 2: tcp:// 直连全流程重验**（桌面版 raw TCP 通道，地址 `tcp://127.0.0.1:5761`）
1. 连接走通身份组 → GET_TEXT 三连 → 空载荷降级命令 → Setup 页进入成功
2. PID tab 修改保存回读一致（Task 25 同款流程在 raw TCP 通道复验）
3. Modes 配 ARM/ANGLE/反乌龟 → 保存回读一致；模拟器飞行页 F1/F2/F4 生效
4. OSD tab 布局编辑 → EEPROM 保存 → 重连回读一致
5. 记录控制台缺失命令清单入 §10（与浏览器版记录对照差异）

- [ ] **Step 3: PTY 通道降级验证**（设计 §12.3：协议与 TCP 同栈，socat/cat 读写验证）
1. `socat -d -d pty,raw,echo=0 pty,raw,echo=0` 建一对 PTY → 模拟器接入地面站后向 slave 写 API_VERSION 请求帧字节（v1 6 字节 `24 4D 3C 00 01 01`）→ 另端 cat 读到应答帧字节
2. 记录 PTY slave 路径与往返字节

- [ ] **Step 4: 记录与提交**
- §5.1 三项 checkbox 勾选 + §10 记录节完成；差异（若有）按协议纪律在提交说明记录

- [ ] **Step 5: Commit**
```
docs: 桌面版实测与 PTY 通道验证记录——§5.1 勾选与 §10 实测节

2026.6.2 raw TCP 全流程（连接/调参/保存回读）+ socat PTY 降级验证。

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
```

---

### Task 31: 验收清单与文档

**Files:**
- Create: `docs/acceptance-phase2.md`
- Modify: `README.md`

**Interfaces:** Produces
- `docs/acceptance-phase2.md`：二期全功能手动验收清单（参照一期 docs/acceptance-phase1.md 结构）：
  - 预设管理段（M1 验证项回放）：列表/选中/复制/删除/改名/旧档迁移
  - AUX 输入段（M2）：手柄按钮/轴三段绑定与预览、键盘 F1/F2/F4
  - 虚拟飞控运行时段（M3）：ARM 油门保护/ANGLE/HORIZON/反乌龟迟滞与三路析取退出
  - MSP 段（M4+M5）：连接识别、Setup 实时数据、PID/rate/TPA 修改保存回读一致、Modes 配置回读、OSD 布局编辑保存回读、EEPROM 落盘回读、三通道地址与接入/断开
  - OSD 段（M6）：16 元素显示与数值动态、DISARMED 2Hz、默认布局可用性（Task 29 记录并入）
  - 回归段：参数模式一期验收项重跑（设计 §11 手动验收）
- `README.md` 更新：虚拟飞控模式与地面站接入说明（参数页转换流程、三通道地址 tcp://127.0.0.1:5761 / ws://127.0.0.1:5762 / PTY 路径、F1/F2/F4 键位指引、Configurator 2026.6.2 桌面版与浏览器版两种形态的接入方式）

- [ ] **Step 1: 编写**（对照已完成任务的回执逐节整理；引用 spike 事实 §8/§10 实测记录）
- [ ] **Step 2: 自查**：清单覆盖设计 §13 M1-M7 全部里程碑；无占位词
- [ ] **Step 3: Commit**
```
docs: 二期验收清单与 README——虚拟飞控/地面站接入/F 键指引

acceptance-phase2.md 覆盖 M1-M7 手动验收（含 M4 已验证项回放）；
README 增地面站接入三通道地址与键位说明。

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
```

---

### Task 32: M7 批 deferred 与收尾

**Files:**
- Modify: `main.py`、`docs/specs/2026-11-12-phase2-design.md`（§13 里程碑状态同步）
- Test: 冒烟 + 台账核对

**Interfaces / 内容**（M7 批 deferred minors 与台账式收尾，全部本任务收口）：
- `main.py` `finally` 块增 `app.msp_server.stop()`（QUIT 退出路径显式停服务器；与「App QUIT 显式 stop」同一事项，合并于此）
- 设计 §13 里程碑行尾状态同步：M5/M6/M7 完成标注（日期 + 计划文件名）
- 台账式收尾核对（逐项确认在本计划内收口；不新增范围）：
  - M5 批（Task 24）：active_tune docstring / delete_preset sanitize / _gs_error 可见性 / cells 钳位 / print→logging / ConfigStore.save 原子写
  - M6 批（Task 28）：键盘 HUD 噪音抑制 / vfc 表单 F 键提示行 / vfc 表单 separator
  - M7 批（本任务）：main.py finally stop / 设计 §13 同步 / GUI 实机验收项（Task 30 记录核对）
  - 已销项不进计划确认：Task 15 两项（fix round 3 取代）、Task 1 迁移注释（fix wave 已修）
- 手动项（GUI 实机）：正常退出（QUIT）后服务器端口释放（重开进程 5761 可再绑定）

- [ ] **Step 1: 实现 main.py finally stop + 设计 §13 状态同步**
- [ ] **Step 2: 台账逐项核对**（本任务卡台账清单逐项勾选；发现未收口项回报，不扩范围）
- [ ] **Step 3: 冒烟**：`python main.py --frames 120` 退出码 0
- [ ] **Step 4: Commit**
```
feat: M7 收尾——main.py finally 停服 + 设计里程碑状态同步

QUIT 退出显式 msp_server.stop()；设计 §13 M5-M7 完成标注；M5/M6/M7
三批 deferred minors 台账核对收口。

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
```

---

## Plan self-review（自审清单）

- **规格覆盖**：Task 19-32 逐一对应设计 §8（MSP 命令集分层 6 组全部落卡：状态组/参数读写组/模式组/OSD 组/固化组＋连接序列杂项）与 §9（字符集资产/内容生成/飞行集成）与 §13（M5-M7）；三批 deferred minors 分批并入（M5→Task 24、M6→Task 28、M7→Task 32）；已销项（Task 15 两项、Task 1 迁移注释）与永久搁置清单均未入计划；简报中 RATE_PROFILE 名词按 MSPCodes.ts 在线核实更正为 RC_TUNING 组（Task 21 载体更正注记在案）
- **无占位符**：机械校验 grep 零命中（证据见交付汇报）
- **类型一致性**：VfcServer/handlers 二参协议与既有单参调用兼容（state 默认 None，一期身份组断言零回归）；换算表字段名与 config_store 的 TuneParams/PIDGains/RateParams、physics/drone 的 eval_boxes/常量、msp/server 的 drain 分派逐一核对；断言数值经手算复核并给自检锚点（DEFAULT_LAYOUT 2441/2457、MODE_RANGES step 32/48、STATUS_EX 24 字节、OSD_CONFIG **190** 字节、RC_TUNING 23 字节、CF_SERIAL_CONFIG 8 字节）