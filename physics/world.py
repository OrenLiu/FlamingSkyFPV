"""PyBullet 物理世界：无人机刚体 + 场景碰撞网格 + bullet→render 坐标转换（设计文档第 4 节）"""
import math
import glm
import pybullet as p

ARM = 0.115          # 电机臂长（米，5 寸机）
# 固定飞控 tick（帧率解耦）：闭环控制频率与渲染帧率彻底解耦——设置页帧率
# 只改渲染节拍，物理/飞控恒以 SIM_TICK 节拍推进（60fps 路径与原实现一致：
# flight.py 每渲染帧恰 1 tick × SUBSTEPS=4 子步 × 1/240s）。
SIM_TICK = 1 / 60    # 飞控/物理 tick（秒）——恒定，不随帧 dt 变
SUBSTEPS = 4         # 每 tick 物理子步（1/60 × 4 = 240Hz 子步长）
# 线性阻力默认值 N·s/m（T13 标定：默认机 550g/推重比 7 极速 ~30m/s）——仅作文档
# 参照与 DroneParams.drag_k 的出厂默认；运行时消费点在 step()，读 self._params.drag_k
# （用户可在参数页按机型手感调整，轻量高推机建议调低，见 config_store.DroneParams）
DRAG_K = 1.2
ANG_DAMP = 0.01      # 角阻尼 N·m·s（桨气动阻尼，防 PID 振荡；与线性阻力是两回事，
                     # 作用对象是角速度、维持 rate 跟踪稳定，不做用户可调）
REACT_K = 0.02       # 桨反扭矩系数 N·m/N（5寸桨动量理论悬停 Q/T≈0.013，取整偏稳）

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
        # 渲染插值基准初始化（接线层 reset 前防御：body 初生位姿即基准）
        self._prev_pos, self._prev_quat = self._pose_snapshot()

    def load_scene_mesh(self, obj_path: str, scale: float = 1.0):
        """场景 OBJ → 静态三角网格碰撞体（mass=0 → BVH 三角网格）"""
        verts, idx = parse_obj_collision(obj_path, scale)
        mesh = p.createCollisionShape(
            p.GEOM_MESH, vertices=verts, indices=idx,
            meshScale=(1, 1, 1), physicsClientId=self._cid)
        p.createMultiBody(0, mesh, basePosition=(0, 0, 0),
                          physicsClientId=self._cid)

    def motor_thrusts_hover(self) -> list:
        """测试辅助：精确悬停单电机推力"""
        return [self._params.weight_g / 1000.0 * 9.81 / 4.0] * 4

    def step(self, motor_thrusts_N: list, dt: float):
        """施加电机推力（机体 +Z 向上、作用于臂位置）+ 桨反扭矩 + 阻力，推进 SUBSTEPS 子步。
        生产路径接线层恒传 SIM_TICK → 子步长恒 (SIM_TICK/SUBSTEPS)=1/240（需显式 setTimeStep：pybullet 默认 fixedTimeStep
        固定 1/240，不随 dt 变化，模拟时间会与真实时间脱钩）。
        线性空气阻力 N = drag_k × v（drag_k 单位 N·s/m，来源 self._params.drag_k，
        默认 1.2 = T13 标定值，用户可在参数页按机型调整）；角阻尼 ANG_DAMP 与线性
        阻力是两回事（作用角速度、保 rate 跟踪稳定），不可调。
        桨反转（Task 50 v4）：set_prop_reverse(True) 时推力与桨反扭矩整体取反
        （双向 DShot 反乌龟——推力沿机体 −Z）。实现为 sign 乘子（±1.0），
        False 路径 1.0×x 与原实现 IEEE 逐位一致，零行为改动"""
        # 渲染插值基准快照：prev = 本 tick 起点位姿（bullet 现值即 tick 末，
        # render_pose 在两态间插值；flight.py 每 tick 调 step → 插值窗恒 SIM_TICK）
        self._prev_pos, self._prev_quat = self._pose_snapshot()
        h = dt / SUBSTEPS
        drag_k = self._params.drag_k
        # setTimeStep 只在步长变化时设置（原每子步重复调用 PyBullet 包装，
        # 每帧 4 次冗余 C 包装调用与 tuple 分配；步长不变时 GL 不变）
        if h != self._last_step_h:
            p.setTimeStep(h, physicsClientId=self._cid)
            self._last_step_h = h
        sign = -1.0 if self._prop_reverse else 1.0   # 桨反转：推力/反扭矩取反
        for _ in range(SUBSTEPS):
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
            # 线性阻力（drag_k 用户可调，N·s/m）+ 角阻尼（定值）
            (lv, av) = p.getBaseVelocity(self._body, physicsClientId=self._cid)
            p.applyExternalForce(self._body, -1,
                                 (-drag_k * lv[0], -drag_k * lv[1], -drag_k * lv[2]),
                                 pos, p.WORLD_FRAME, physicsClientId=self._cid)
            p.applyExternalTorque(self._body, -1,
                                  (-ANG_DAMP * av[0], -ANG_DAMP * av[1], -ANG_DAMP * av[2]),
                                  p.WORLD_FRAME, physicsClientId=self._cid)
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
