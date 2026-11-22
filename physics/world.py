"""PyBullet 物理世界：无人机刚体 + 场景碰撞网格 + bullet→render 坐标转换（设计文档第 4 节）"""
import logging
import math
import glm
import pybullet as p

log = logging.getLogger(__name__)

ARM = 0.115          # 电机臂长（米，5 寸机）
# 固定飞控 tick（帧率解耦）：闭环控制频率与渲染帧率彻底解耦——设置页帧率
# 只改渲染节拍，物理/飞控恒以 SIM_TICK 节拍推进（60fps 路径与原实现一致：
# flight.py 每渲染帧恰 1 tick × SUBSTEPS=4 子步 × 1/240s）。
SIM_TICK = 1 / 60    # 飞控/物理 tick（秒）——恒定，不随帧 dt 变
SUBSTEPS = 4         # 每 tick 物理子步（1/60 × 4 = 240Hz 子步长）
# 二次阻力默认值 N·s²/m²（F = drag_k·v²，方向沿速度反向；T13 极速标定换算：
# 默认机 550g/推重比 7 平飞极速 ~30m/s 不变）——仅作文档参照与默认锚；运行时
# 消费点在 step()，读 self._params.drag_k（用户可在参数页按机型手感调整，
# 轻量高推机建议调低，见 config_store.DroneParams）
DRAG_K = 0.0415
ANG_DAMP = 0.01      # 角阻尼 N·m·s（桨气动阻尼，防 PID 振荡；与线性阻力是两回事，
                     # 作用对象是角速度、维持 rate 跟踪稳定，不做用户可调）
REACT_K = 0.02       # 桨反扭矩系数 N·m/N（5寸桨动量理论悬停 Q/T≈0.013，取整偏稳）

# --- 隧穿（tunneling）防护：速度向扫掠射线 CCD（常开 + 姿态投影半径 + 预穿带） ---
# 根因（2026-11-19 两轮排查实证）：pybullet 离散接触检测对细分场景网格的检测
# 窗口窄——高速时单子步位移（30m/s×1/240s=12.5cm）跳过窗口即整机穿墙；低速
# 持续推力（满油压墙，~0.03m/s/子步重建）下原生接触顶不住则棘轮蠕穿。两轮
# 结论：速度门控不可用——门控窗口内推力/重力自由重建速度照样磨穿任何薄面；
# 唯一可靠阻挡 = 每子步沿速度方向 rayTestBatch 扫掠（射线走 BVH 精确几何查
# 询，不依赖接触生成管线），命中即钳位到「盒前缘贴面前 CCD_MARGIN」+ 法向
# 速度清零（非弹性）。
# 防幽灵制动三设计（历史复杂方案被回退的主因，勿重蹈）：
# 1. 投影半径姿态相关（CCD 不是固定球）：沿速度方向取盒 8 角最大投影——
#    垂直起降时半径=0.04（薄向），侧向撞墙=0.115。落地钳到面+4.5cm（盒缘贴
#    面 5mm），不是固定 0.13 悬空架高；悬停贴面时射线够不到面 → 零干预。
# 2. 5mm 预穿带（CCD_MARGIN）：钳位目标不在面外远处，盒缘停在面前 5mm——
#    原生接触可靠的面（大三角形）自然驻停接管；不可靠的面在带内锯齿稳定，
#    不蠕穿（法向速度每子步清零，推力重建的毫米/子步级速度到不了穿透）。
# 3. 只认场景 mesh 刚体（self._mesh_body）：无限地面 plane 原生接触可靠，
#    不扫不钳——防贴地悬停收敛被高频清速破坏。
# 已知限制与已证伪路线（第三轮排查，勿重试）：贴墙飞行+横向压墙分量时速度
# 方向平行于墙，本射线对侧面的墙失明，存在 ~6cm 级侧向渗入稳态；「13 射线
# 扇」（速度向+世界 6 轴向±）有离散方向洞（斜向三角形全打空）、「cps 全向
# 最近点位置投影」在真实网格深穿透态 normalOnB 方向失真越推越穿（高速直撞
# 30m/s 反而嵌 17~30cm）、「射线+cps 双防线」两法线源互相打架（侧压 -13cm）。
# 注意：本 pybullet 构建（3.2.7）单发 p.rayTest 恒返回无命中（id=-1），必须用
# p.rayTestBatch——探针实测 rayTest 对 plane/box 也全灭，非用法问题。
# 性能：单射线 batch ~4µs × 240Hz 子步 ≈ 0.1% CPU，可常开。
CCD_MARGIN = 0.005      # 盒前缘与命中面的安全间隙（预穿带宽度）m
CCD_RESTITUTION = 0.25 # 撞击法向反弹系数（保撞击扭矩/翻滚物理，非全清）
CCD_LANDING_VMAX = 6.0 # 起降豁免：朝上水平面逼近速度阈值 m/s（原生着陆物理）
CCD_CPS_MIN = 0.002    # cps 浅嵌入防线：嵌入下限 m（<2mm 是静置 settle 量级）
CCD_CPS_MAX = 0.05     # cps 浅嵌入防线：嵌入上限 m（深穿透法线失真区不碰）
CCD_MINI_STEP = 0.05   # 动态细分：逼近结构时每 mini 步最大位移 m（窗口覆盖保证）
CCD_CONTACT_VMAX = 1.5  # 接触豁免的逼近速度上限 m/s（高速压面时接触正被突破，须拦）
# 场景碰撞网格分块上限：pybullet createCollisionShape(GEOM_MESH) 数据走
# 共享内存命令缓冲，有两条独立硬上限——顶点 ≈88k（二分实测 87616 OK /
# 88209 FAIL）与索引 =524288=2^19（退化网格二分实测，524286 OK / 524289
# FAIL；mctrk 缺陷取证 2026-11-22）。超限参数被截断、落入文件名加载路径
# 报「createCollisionShape failed / Number of indices exceeds the maximum」。
# 取安全余量 60k 顶点 / 480k 索引；超限网格按三角形贪心分块，各块独立建
# 形状后以固定关节 link 合并进同一静态刚体（_mesh_body 单 id 语义不变，
# CCD 三处归属判定零改动）
MESH_CHUNK_VERTS = 60000
MESH_CHUNK_IDX = 480000
# 盒 8 角（机体系，HalfExtents 同碰撞盒）——投影半径计算用
_HALF_CORNERS = [(sx * ARM, sy * ARM, sz * 0.04)
                 for sx in (-1, 1) for sy in (-1, 1) for sz in (-1, 1)]

# 电机臂位置（机体系：+X 前 / +Y 左 / +Z 上），顺序 [FL, FR, BL, BR]
ARMS = [(ARM, ARM, 0), (ARM, -ARM, 0), (-ARM, ARM, 0), (-ARM, -ARM, 0)]
# 桨转向符号（绕机体 +Z 右手系）：FL/BR 逆时针、FR/BL 顺时针（对角同向、相邻反向，
# 悬停等推时反扭矩互消为零）。桨反扭矩是 yaw 差动的唯一力矩来源——四推力沿机体
# +Z 共线，推力力臂几何对 Z 轴力矩恒为 0（计划「坐标系与符号约定」）；
# 方向由 test_yaw_closed_loop_direction 闭环裁判。
PROP_SPIN = (1, -1, -1, 1)   # [FL, FR, BL, BR]

def bullet_to_render(pos) -> tuple:
    """PyBullet(Z-up) → soup3D(Y-up)：(x,y,z) → (x,z,-y)"""
    x, y, z = pos
    return (x, z, -y)

# 基变换矩阵 M（列主序构造）：M·(x,y,z) = (x,z,-y)；C 为其四元数
# 已实测验证：glm.mat3(1,0,0, 0,0,-1, 0,1,0) * (1,2,3) = (1,3,-2)
_C = glm.quat(glm.mat3(1, 0, 0, 0, 0, -1, 0, 1, 0))
_C_INV = glm.inverse(_C)

def quat_bullet_to_render(q) -> glm.quat:
    """旋转四元数基变换：q' = C·q·C⁻¹"""
    gq = q if isinstance(q, glm.quat) else glm.quat(q[0], q[1], q[2], q[3])
    return _C * gq * _C_INV

def compose_camera_quat(q_bullet, camera_angle_deg: float) -> glm.quat:
    """FPV 相机姿态合成：bullet 机体系 → soup3D 相机系。
    相机约定前向 q·(0,0,-1)、上向 q·(0,1,0)；机头在渲染系为 +X。
    R_ALIGN 把相机 -Z 转到机头 +X（绕 Y 转 -90°），再叠摄像头仰角
    （绕 X 正旋=上仰）。勿用 glm.quat(angle, axis)——那是 (w,x,y,z) 构造，
    产出非单位四元数（人工验收曾致悬停画面颠倒）。"""
    q_r = quat_bullet_to_render(q_bullet)
    R_ALIGN = glm.angleAxis(glm.radians(-90.0), glm.vec3(0, 1, 0))
    pitch = glm.angleAxis(glm.radians(camera_angle_deg), glm.vec3(1, 0, 0))
    return q_r * R_ALIGN * pitch

def parse_obj_collision(obj_path: str, scale: float = 1.0):
    """解析 OBJ（仅 v/f 行，三角扇剖分），Y-up→Z-up，返回 (vertices, indices)。
    用于 bullet 静态碰撞网格；渲染模型由 soup3D.open_obj 另行加载。
    映射为 bullet→render 正变换 (x,z,-y) 的逆：(x,y,z) → (x,-z,y)，OBJ 的 +Y(上) → bullet +Z(上)。"""
    verts, faces = [], []
    with open(obj_path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            if line.startswith("v "):
                x, y, z = (float(t) for t in line.split()[1:4])
                verts.append((x * scale, -z * scale, y * scale))  # Y-up→Z-up（逆向基变换）
            elif line.startswith("f "):
                idx = [int(t.split("/")[0]) - 1 for t in line.split()[1:]]
                for k in range(1, len(idx) - 1):   # 三角扇
                    faces.extend((idx[0], idx[k], idx[k + 1]))
    return verts, faces

class FlightWorld:
    """PyBullet DIRECT 世界：地面 + 场景网格 + 无人机盒形刚体"""
    def __init__(self, params):
        self._params = params
        self._last_step_h = None   # 上次 setTimeStep 的步长（变化才重设）
        self._cid = p.connect(p.DIRECT)
        p.setGravity(0, 0, -9.81, physicsClientId=self._cid)
        ground = p.createCollisionShape(p.GEOM_PLANE, planeNormal=(0, 0, 1),
                                        physicsClientId=self._cid)  # 无限地面
        # Task 53 修复：地面挂载静态刚体（mass=0）。缺陷史：此处曾只创建碰撞
        # 形状、从未 createMultiBody——「孤儿形状」从不参与碰撞（Task 50 探针
        # 顺带发现并经最小 DIRECT 实验实证：孤儿形状下盒子自由穿地，挂刚体后
        # 正常停地）。真机会话一直靠场景 mesh 地板接住、掩盖了缺陷；无 mesh
        # 地板的场景或飞出场景边界会无限坠落。修复后 plane 与场景地板 mesh
        # 共面双接触，实测静置 z 波动 <1mm、无抖动弹跳（test_world.py
        # Task 53 场景 A/B 探针；反乌龟探针五项结论不变，见
        # test_crashflip_physics.py）。
        p.createMultiBody(0, ground, basePosition=(0, 0, 0),
                          physicsClientId=self._cid)
        col = p.createCollisionShape(p.GEOM_BOX, halfExtents=[ARM, ARM, 0.04],
                                     physicsClientId=self._cid)
        mass = params.weight_g / 1000.0
        self._body = p.createMultiBody(mass, col, basePosition=(0, 0, 2),
                                       physicsClientId=self._cid)
        # 标定实测：bullet 默认 linearDamping/angularDamping=0.04 是隐藏阻尼（零外力
        # 20m/s 滑行 0.5s 衰减至 14m/s，不在 getDynamicsInfo 返回中），使 DRAG_K 失真
        # ~23% → 显式清零，线性/角阻尼全部由下方 DRAG_K/ANG_DAMP 显式承担
        p.changeDynamics(self._body, -1, linearDamping=0.0, angularDamping=0.0,
                         physicsClientId=self._cid)
        # 桨反转标志（Task 50 v4）：False=正常飞行（推力沿机体 +Z）；True=反乌龟
        # 双向 DShot 桨反转（推力沿机体 −Z，见 set_prop_reverse）——逐帧由接线层设置
        self._prop_reverse = False
        # 场景 mesh 刚体 id（CCD 扫掠命中归属用；None=未加载场景）
        self._mesh_body = None
        # 渲染插值基准初始化（接线层 reset 前防御：body 初生位姿即基准）
        self._prev_pos, self._prev_quat = self._pose_snapshot()

    def load_scene_mesh(self, obj_path: str, scale: float = 1.0):
        """场景 OBJ → 静态三角网格碰撞体（mass=0 → BVH 三角网格）。
        mesh 刚体 id 记入 self._mesh_body（CCD 扫掠命中归属）。
        顶点超 MESH_CHUNK_VERTS 或索引超 MESH_CHUNK_IDX 时按三角形贪心分块
        （mctrk 缺陷修复）：每块局部顶点重编号，独立 createCollisionShape
        （单命令不超 pybullet 顶点/索引命令缓冲上限），再以固定关节 link
        全部挂到同一 mass=0 静态刚体——rayTest/getClosestPoints 对 link
        碰撞体天然归属同一 body id，下游 CCD 判定（== self._mesh_body）
        无需感知分块"""
        verts, idx = parse_obj_collision(obj_path, scale)
        # 三角形贪心分块：块内顶点 ≤MESH_CHUNK_VERTS 且索引 ≤MESH_CHUNK_IDX
        # （边界顶点跨块重复，总顶点略增无妨；按面序分块保空间局部性，有利
        # BVH 查询）
        chunks = []                       # [(局部顶点表, 局部索引表)]
        cur_v, cur_i, remap = [], [], {}
        for t in range(0, len(idx), 3):
            tri = idx[t:t + 3]
            n_new = sum(1 for v in tri if v not in remap)
            if cur_i and (len(cur_v) + n_new > MESH_CHUNK_VERTS
                          or len(cur_i) + 3 > MESH_CHUNK_IDX):
                chunks.append((cur_v, cur_i))
                cur_v, cur_i, remap = [], [], {}
            local = []
            for v in tri:
                if v not in remap:
                    remap[v] = len(cur_v)
                    cur_v.append(verts[v])
                local.append(remap[v])
            cur_i.extend(local)
        if cur_i:
            chunks.append((cur_v, cur_i))
        log.info("collision mesh loaded: %s (%d tris, %d chunk(s))",
                 obj_path, len(idx) // 3, len(chunks))
        shapes = [
            p.createCollisionShape(
                p.GEOM_MESH, vertices=cv, indices=ci,
                meshScale=(1, 1, 1), physicsClientId=self._cid)
            for cv, ci in chunks]
        if len(shapes) == 1:
            # 单块：原路径（既有场景行为逐位不变）
            self._mesh_body = p.createMultiBody(
                0, shapes[0], basePosition=(0, 0, 0),
                physicsClientId=self._cid)
        else:
            # 多块：固定关节 link 合并为单静态刚体（base 无碰撞形状）
            self._mesh_body = p.createMultiBody(
                0, -1, basePosition=(0, 0, 0),
                linkMasses=[0.0] * len(shapes),
                linkCollisionShapeIndices=shapes,
                linkVisualShapeIndices=[-1] * len(shapes),
                linkPositions=[(0, 0, 0)] * len(shapes),
                linkOrientations=[[0, 0, 0, 1]] * len(shapes),
                linkInertialFramePositions=[(0, 0, 0)] * len(shapes),
                linkInertialFrameOrientations=[[0, 0, 0, 1]] * len(shapes),
                linkParentIndices=[0] * len(shapes),
                linkJointTypes=[p.JOINT_FIXED] * len(shapes),
                linkJointAxis=[[1, 0, 0]] * len(shapes),
                physicsClientId=self._cid)

    def motor_thrusts_hover(self) -> list:
        """测试辅助：精确悬停单电机推力"""
        return [self._params.weight_g / 1000.0 * 9.81 / 4.0] * 4

    def step(self, motor_thrusts_N: list, dt: float):
        """施加电机推力（机体 +Z 向上、作用于臂位置）+ 桨反扭矩 + 阻力，推进 SUBSTEPS 子步。
        生产路径接线层恒传 SIM_TICK → 子步长恒 (SIM_TICK/SUBSTEPS)=1/240（需显式 setTimeStep：pybullet 默认 fixedTimeStep
        固定 1/240，不随 dt 变化，模拟时间会与真实时间脱钩）。
        二次空气阻力 N = drag_k × v²（方向沿速度反向；drag_k 单位 N·s²/m²，来源
        self._params.drag_k，默认 0.0415 = T13 极速标定换算，用户可在参数页按机型
        调整；自由落体终端速度 = sqrt(m·g/drag_k)）；角阻尼 ANG_DAMP 与平动阻力
        是两回事（作用角速度、保 rate 跟踪稳定），不可调。
        桨反转（Task 50 v4）：set_prop_reverse(True) 时推力与桨反扭矩整体取反
        （双向 DShot 反乌龟——推力沿机体 −Z）。实现为 sign 乘子（±1.0），
        False 路径 1.0×x 与原实现 IEEE 逐位一致，零行为改动"""
        # 渲染插值基准快照：prev = 本 tick 起点位姿（bullet 现值即 tick 末，
        # render_pose 在两态间插值；flight.py 每 tick 调 step → 插值窗恒 SIM_TICK）
        self._prev_pos, self._prev_quat = self._pose_snapshot()
        # 动态子步细分（高速隧穿防线第三层）：单子步位移超过检测窗口宽度
        # 时拦截依赖相位运气（用户取证：48m/s 撞墙 0.2m/子步，取证那次被
        # 原生接触碰巧接住，回放相位偏 7cm 即穿）。仅在「高速 + 前方近距
        # （本 tick 行程+30cm）有场景结构」时把子步细分到每 mini 步位移
        # ≤CCD_MINI_STEP——自由飞行不细分，闭环对象动力学不变（TPA 标定
        # 语义保持）。mini 步 ≤5cm 且 CCD 射线前瞻 ~0.3m，窗口覆盖有数学
        # 保证（任何相位必有一个 mini 步落入检测窗）。
        nsub = SUBSTEPS
        if self._mesh_body is not None:
            (lv0, _) = p.getBaseVelocity(self._body, physicsClientId=self._cid)
            spd0 = math.sqrt(lv0[0]*lv0[0] + lv0[1]*lv0[1] + lv0[2]*lv0[2])
            travel_tick = spd0 * dt
            if travel_tick / SUBSTEPS > CCD_MINI_STEP and spd0 > 1e-6:
                pos0 = self.pos
                reach0 = travel_tick + 0.3
                rs0 = p.rayTestBatch(
                    [pos0],
                    [(pos0[0] + lv0[0]/spd0 * reach0,
                      pos0[1] + lv0[1]/spd0 * reach0,
                      pos0[2] + lv0[2]/spd0 * reach0)],
                    physicsClientId=self._cid)[0]
                if rs0[0] == self._mesh_body and rs0[2] < 1.0:
                    nsub = min(64, math.ceil(travel_tick / CCD_MINI_STEP))
        h = dt / nsub
        drag_k = self._params.drag_k
        # setTimeStep 只在步长变化时设置（原每子步重复调用 PyBullet 包装，
        # 每帧 4 次冗余 C 包装调用与 tuple 分配；步长不变时 GL 不变）
        if h != self._last_step_h:
            p.setTimeStep(h, physicsClientId=self._cid)
            self._last_step_h = h
        sign = -1.0 if self._prop_reverse else 1.0   # 桨反转：推力/反扭矩取反
        for _ in range(nsub):
            pos, quat = p.getBasePositionAndOrientation(
                self._body, physicsClientId=self._cid)
            rot = glm.quat(quat[3], quat[0], quat[1], quat[2])  # bullet (x,y,z,w)→glm(w,x,y,z)
            up = rot * glm.vec3(0, 0, 1)                        # 机体 +Z 世界方向
            for arm, thrust, spin in zip(ARMS, motor_thrusts_N, PROP_SPIN):
                world_pos = pos + rot * glm.vec3(*arm)          # 臂位置世界坐标
                force = (sign * up.x * thrust, sign * up.y * thrust,
                         sign * up.z * thrust)
                p.applyExternalForce(self._body, -1, force,
                                     (world_pos.x, world_pos.y, world_pos.z),
                                     p.WORLD_FRAME, physicsClientId=self._cid)
                tq = -sign * spin * REACT_K * thrust            # 桨反扭矩（机体 Z 向）
                p.applyExternalTorque(self._body, -1,
                                      (up.x * tq, up.y * tq, up.z * tq),
                                      p.WORLD_FRAME, physicsClientId=self._cid)
            # 二次空气阻力（drag_k 用户可调，N·s²/m²）：合力 = drag_k·|v|²、
            # 方向沿速度反向 → 分量式 −drag_k·|v|·v_i；低速阻力自然变小，
            # 自由落体终端速度 sqrt(m·g/drag_k)（修复线性模型 21km/h 天花板）
            (lv, av) = p.getBaseVelocity(self._body, physicsClientId=self._cid)
            speed = (lv[0] * lv[0] + lv[1] * lv[1] + lv[2] * lv[2]) ** 0.5
            p.applyExternalForce(self._body, -1,
                                 (-drag_k * speed * lv[0],
                                  -drag_k * speed * lv[1],
                                  -drag_k * speed * lv[2]),
                                 pos, p.WORLD_FRAME, physicsClientId=self._cid)
            p.applyExternalTorque(self._body, -1,
                                  (-ANG_DAMP * av[0], -ANG_DAMP * av[1], -ANG_DAMP * av[2]),
                                  p.WORLD_FRAME, physicsClientId=self._cid)
            # 隧穿防护（速度向扫掠射线，5cm/s 起扫）：命中 → 钳到盒前缘贴面
            # 前 CCD_MARGIN（5mm 预穿带）+ 法向速度清零（非弹性）。阈值 0.05
            # 避开静置 settle 噪声（mm/s 级微速扫掠会在 mesh 地板上与重力锯齿
            # 抖动，Task 53 共面静置回归实证）。「很低速直撞」（用户实测 1~
            # 3m/s 级）仍在覆盖内。见模块头注释。
            # 已知限制：贴墙飞行+横向压墙分量时速度方向平行于墙，本射线对
            # 侧面的墙失明，存在 ~6cm 级侧向渗入（真实场景实测稳态）；离散
            # 射线扇/cps 全向投影两路线均已试败（方向洞/法线失真越推越穿），
            # 勿在未取证的几何代理上再叠加防层。
            if speed > 0.05 and self._mesh_body is not None:
                travel = speed * h
                # 速度向前缘投影半径（盒 8 角沿速度方向最大投影）
                ux, uy, uz = lv[0] / speed, lv[1] / speed, lv[2] / speed
                wc = [rot * glm.vec3(cx_, cy_, cz_)
                      for cx_, cy_, cz_ in _HALF_CORNERS]
                rmax = max(0.0, max(o.x * ux + o.y * uy + o.z * uz
                                    for o in wc))
                reach = travel + rmax + CCD_MARGIN
                rs = p.rayTestBatch(
                    [(pos[0], pos[1], pos[2])],
                    [(pos[0] + ux * reach, pos[1] + uy * reach,
                      pos[2] + uz * reach)],
                    physicsClientId=self._cid)[0]
                if rs[0] == self._mesh_body and rs[2] < 1.0:
                    hit, n = rs[3], rs[4]
                    # 法向几何（斜向命中正确）：间隙与钳位沿命中法线——沿射线
                    # 回退在斜向时法向投影不足，角点仍过面（取证：质心只退到
                    # 面外 3cm、角点嵌 8cm）。rmax_n = 盒 8 角沿法线最大投影。
                    rmax_n = max(0.0, max(o.x * n[0] + o.y * n[1] + o.z * n[2]
                                          for o in wc))
                    gap = ((pos[0] - hit[0]) * n[0] + (pos[1] - hit[1]) * n[1]
                           + (pos[2] - hit[2]) * n[2]) - rmax_n
                    vn = lv[0] * n[0] + lv[1] * n[1] + lv[2] * n[2]
                    # 起降豁免：低速落向朝上水平面（|nz|>0.7 且逼近速度 <6m/s）
                    # 不拦截——水平大面接触生成可靠（bando 平台实测 10/10 落
                    # 稳），原生撞击物理（翻滚/滑行/回弹手感）完整保留；CCD
                    # 拦截会软化撞击（倾斜坠落翻滚 800→427°/s 实证）。高速
                    # 俯冲（>6m/s）与竖直墙/斜面撞击不豁免（隧穿风险场景）。
                    if n[2] > 0.7 and -vn < CCD_LANDING_VMAX:
                        pass
                    # 接触态豁免：接触管线正在工作（含地面 plane 接触）时绝不
                    # 干预——正常接触压缩（bullet allowedPenetration 4cm 内）
                    # 的任何清速/推挤都会破坏接触约束（crashflip 压地滚转失
                    # 控 6567°、静置 jitter 教训）。撞墙后进入接触态的压制由
                    # 本豁免+法向弹性自然平衡（MT 真值 6/6，勿再加"深嵌干预"
                    # ——夹层结构会被推挤楔入，历史案例 #3 教训）。
                    elif (not p.getContactPoints(
                            bodyA=self._body, physicsClientId=self._cid)
                          or -vn > CCD_CONTACT_VMAX) \
                            and gap - travel < CCD_MARGIN:
                        safe = rmax_n + CCD_MARGIN
                        p.resetBasePositionAndOrientation(
                            self._body,
                            (hit[0] + n[0] * safe, hit[1] + n[1] * safe,
                             hit[2] + n[2] * safe),
                            quat, physicsClientId=self._cid)
                        # 法向速度部分弹性（e=0.25）：非全清——全清会杀死撞击
                        # 物理的接触扭矩（倾斜坠落翻滚 800→392°/s 回归实证），
                        # 反弹 25% 保住「砰感」与翻滚；位置已钳带内不影响防穿。
                        if vn < 0:
                            k = 1.0 + CCD_RESTITUTION
                            p.resetBaseVelocity(
                                self._body,
                                (lv[0] - k * vn * n[0], lv[1] - k * vn * n[1],
                                 lv[2] - k * vn * n[2]),
                                av, physicsClientId=self._cid)
            # cps 浅嵌入防线（速度向射线盲区补漏，常开）：速度与面掠射角大
            # 时（用户取证案例：撞墙弹跳后下沉中缓慢北移，78° 掠射——速度向
            # 射线在 reach 内法向前进 3.6cm，打不到 3cm 外的墙）盒角浅嵌入
            # 渐进推进直至整盒穿越（正确判据实测前倾满油 6/12 穿越一半）。
            # cps 在浅嵌区（-CCD_CPS_MAX < gap < -CCD_CPS_MIN）翻回法线可信；
            # 深穿透区（normalOnB 失真，历史「越推越穿」教训）与 <2mm 微嵌
            # （静置 settle 量级）不碰。同样受起降/接触豁免约束（crashflip
            # 压地浅嵌有接触 → 豁免；静置接触闪烁的无接触子步嵌入 <2mm →
            # 不触发）。
            if self._mesh_body is not None:
                cps = p.getClosestPoints(self._body, self._mesh_body, 0.0,
                                         physicsClientId=self._cid)
                best = None               # 最深浅嵌点对
                for cp in cps:
                    g = cp[8]
                    if -CCD_CPS_MAX < g < -CCD_CPS_MIN and \
                            (best is None or g < best[0]):
                        best = (g, cp)
                if best is not None:
                    g, cp = best
                    n = cp[7]
                    if g < 0:
                        n = (-n[0], -n[1], -n[2])   # 穿透态翻回推离方向
                    vn = lv[0] * n[0] + lv[1] * n[1] + lv[2] * n[2]
                    if not (n[2] > 0.7 and -vn < CCD_LANDING_VMAX) and \
                            (not p.getContactPoints(
                                bodyA=self._body, physicsClientId=self._cid)
                             or -vn > CCD_CONTACT_VMAX):
                        pen = CCD_MARGIN - g
                        cur = p.getBasePositionAndOrientation(
                            self._body, physicsClientId=self._cid)[0]
                        p.resetBasePositionAndOrientation(
                            self._body,
                            (cur[0] + n[0] * pen, cur[1] + n[1] * pen,
                             cur[2] + n[2] * pen),
                            quat, physicsClientId=self._cid)
                        if vn < 0:
                            k = 1.0 + CCD_RESTITUTION
                            p.resetBaseVelocity(
                                self._body,
                                (lv[0] - k * vn * n[0],
                                 lv[1] - k * vn * n[1],
                                 lv[2] - k * vn * n[2]),
                                av, physicsClientId=self._cid)
            p.stepSimulation(physicsClientId=self._cid)

    @property
    def pos(self) -> tuple:
        return p.getBasePositionAndOrientation(self._body,
                                               physicsClientId=self._cid)[0]

    @property
    def quat(self) -> glm.quat:
        q = p.getBasePositionAndOrientation(self._body,
                                            physicsClientId=self._cid)[1]
        return glm.quat(q[3], q[0], q[1], q[2])

    @property
    def euler_deg(self) -> tuple:
        """(roll, pitch, yaw) 角度制，bullet 语义（HUD 显示用杆义见 flight.py）"""
        e = p.getEulerFromQuaternion(
            p.getBasePositionAndOrientation(self._body,
                                            physicsClientId=self._cid)[1])
        return tuple(math.degrees(x) for x in e)

    @property
    def euler_stick_deg(self) -> tuple:
        """(roll, pitch, yaw) 杆义姿态角度制（Task 14）：bullet euler 的 y/z 轴
        正方向与杆义相反（同 gyro_dps 的符号翻转）——ANGLE/HORIZON 自稳外环与
        反乌龟倒置检测消费（与 gyro_dps 同义系，保证闭环符号一致）"""
        e = p.getEulerFromQuaternion(
            p.getBasePositionAndOrientation(self._body,
                                            physicsClientId=self._cid)[1])
        d = [math.degrees(x) for x in e]
        return (d[0], -d[1], -d[2])

    @property
    def velocity(self) -> tuple:
        return p.getBaseVelocity(self._body, physicsClientId=self._cid)[0]

    @property
    def gyro_dps(self) -> tuple:
        """体系角速度 → 杆义 (roll右, pitch抬头, yaw右) °/s"""
        (_, av) = p.getBaseVelocity(self._body, physicsClientId=self._cid)
        inv = glm.inverse(self.quat)
        wb = inv * glm.vec3(*av)                  # 世界系→体系
        return (math.degrees(wb.x), -math.degrees(wb.y), -math.degrees(wb.z))

    def set_prop_reverse(self, active: bool) -> None:
        """桨反转标志设置（Task 50 v4，双向 DShot 反乌龟）：active=True 时
        step() 的电机推力沿机体 −Z（桨反向旋转 → 推力向量取反）、桨反扭矩
        同步取反。真机出处：Betaflight 反乌龟靠双向 DShot 反转螺旋桨——倒扣
        时被驱动侧推力从「压向地面」变为「抬离地面」，翻面几何难题消解
        （用户实机经验裁定）。physics 层不感知调用方语义（crashflip box/
        解锁态由 flight.py 裁定），只收 bool；非激活路径经 IEEE 乘 1.0
        与原实现逐位一致（见 step 注释）"""
        self._prop_reverse = bool(active)

    def _pose_snapshot(self) -> tuple:
        """当前位姿快照 (pos tuple, glm.quat)。glm.quat 为 (w,x,y,z)，
        bullet 返回 (x,y,z,w)"""
        pos, quat = p.getBasePositionAndOrientation(
            self._body, physicsClientId=self._cid)
        return pos, glm.quat(quat[3], quat[0], quat[1], quat[2])

    def render_pose(self, alpha: float) -> tuple:
        """tick 间渲染插值位姿：alpha=0 → 上一 tick 末（prev 快照）、
        alpha=1 → bullet 现值（本 tick 末）。位置线性插值 + 四元数 nlerp
        （相邻 tick 60Hz 角距小，nlerp 足够且稳定）。alpha 钳位 [0,1]。
        flight.py 每帧以 acc/SIM_TICK 调用：固定 tick 后 bullet 状态 60Hz
        更新，高帧率渲染设置下不插值会出现帧重复顿挫"""
        a = max(0.0, min(1.0, alpha))
        cur = self.pos
        pos = tuple(self._prev_pos[i] + (cur[i] - self._prev_pos[i]) * a
                    for i in range(3))
        qa, qb = self._prev_quat, self.quat
        d = qa.w*qb.w + qa.x*qb.x + qa.y*qb.y + qa.z*qb.z
        s = -1.0 if d < 0.0 else 1.0        # 短弧：dot<0 取反后再插值
        q = glm.quat(qa.w + a*(s*qb.w - qa.w),
                     qa.x + a*(s*qb.x - qa.x),
                     qa.y + a*(s*qb.y - qa.y),
                     qa.z + a*(s*qb.z - qa.z))
        n = q.w*q.w + q.x*q.x + q.y*q.y + q.z*q.z
        return pos, glm.quat(q.w/n, q.x/n, q.y/n, q.z/n)

    def reset(self, spawn=(0, 0, 2), quat=(0, 0, 0, 1)):
        """重置：回出生点、指定姿态（quat 为 bullet (x,y,z,w)，默认水平；
        倒扣姿态注入等测试/复位场景用）、速度清零。桨反转标志一并回
        False（Task 50 v4 裁定复位点：重置=全新起手，防反转态跨重置残留；
        正常路径接线层每帧 step 前都会重设，此处防御性兜底）"""
        p.resetBasePositionAndOrientation(
            self._body, spawn, quat, physicsClientId=self._cid)
        p.resetBaseVelocity(self._body, (0, 0, 0), (0, 0, 0),
                            physicsClientId=self._cid)
        self._prop_reverse = False
        # 插值快照同步：复位后 prev = cur = 出生位姿（render_pose 首帧无滑移）
        self._prev_pos, self._prev_quat = self._pose_snapshot()

    def close(self):
        p.disconnect(physicsClientId=self._cid)
