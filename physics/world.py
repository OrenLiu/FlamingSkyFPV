"""PyBullet 物理世界：无人机刚体 + 场景碰撞网格 + bullet→render 坐标转换（设计文档第 4 节）"""
import math
import glm
import pybullet as p

ARM = 0.115          # 电机臂长（米，5 寸机）
SUBSTEPS = 4         # 每帧物理子步（60fps × 4 = 240Hz）
DRAG_K = 1.2         # 线性阻力 N·s/m（标定：8:1 推重比满杆极速 ~35m/s）
ANG_DAMP = 0.01      # 角阻尼 N·m·s（桨气动阻尼，防 PID 振荡）
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

def parse_obj_collision(obj_path: str, scale: float = 1.0):
    """解析 OBJ（仅 v/f 行，三角扇剖分），Y-up→Z-up，返回 (vertices, indices)。
    用于 bullet 静态碰撞网格；渲染模型由 soup3D.open_obj 另行加载。"""
    verts, faces = [], []
    with open(obj_path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            if line.startswith("v "):
                x, y, z = (float(t) for t in line.split()[1:4])
                verts.append((x * scale, z * scale, -y * scale))  # Y-up→Z-up
            elif line.startswith("f "):
                idx = [int(t.split("/")[0]) - 1 for t in line.split()[1:]]
                for k in range(1, len(idx) - 1):   # 三角扇
                    faces.extend((idx[0], idx[k], idx[k + 1]))
    return verts, faces

class FlightWorld:
    """PyBullet DIRECT 世界：地面 + 场景网格 + 无人机盒形刚体"""
    def __init__(self, params):
        self._params = params
        self._cid = p.connect(p.DIRECT)
        p.setGravity(0, 0, -9.81, physicsClientId=self._cid)
        p.createCollisionShape(p.GEOM_PLANE, planeNormal=(0, 0, 1),
                               physicsClientId=self._cid)  # 无限地面
        col = p.createCollisionShape(p.GEOM_BOX, halfExtents=[ARM, ARM, 0.04],
                                     physicsClientId=self._cid)
        mass = params.weight_g / 1000.0
        self._body = p.createMultiBody(mass, col, basePosition=(0, 0, 2),
                                       physicsClientId=self._cid)

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
        """施加电机推力（机体 +Z 向上、作用于臂位置）+ 桨反扭矩 + 阻力，推进 SUBSTEPS 子步"""
        h = dt / SUBSTEPS
        for _ in range(SUBSTEPS):
            pos, quat = p.getBasePositionAndOrientation(
                self._body, physicsClientId=self._cid)
            rot = glm.quat(quat[3], quat[0], quat[1], quat[2])  # bullet (x,y,z,w)→glm(w,x,y,z)
            up = rot * glm.vec3(0, 0, 1)                        # 机体 +Z 世界方向
            for arm, thrust, spin in zip(ARMS, motor_thrusts_N, PROP_SPIN):
                world_pos = pos + rot * glm.vec3(*arm)          # 臂位置世界坐标
                force = (up.x * thrust, up.y * thrust, up.z * thrust)
                p.applyExternalForce(self._body, -1, force,
                                     (world_pos.x, world_pos.y, world_pos.z),
                                     p.WORLD_FRAME, physicsClientId=self._cid)
                tq = -spin * REACT_K * thrust                   # 桨反扭矩（机体 Z 向）
                p.applyExternalTorque(self._body, -1,
                                      (up.x * tq, up.y * tq, up.z * tq),
                                      p.WORLD_FRAME, physicsClientId=self._cid)
            # 线性阻力 + 角阻尼
            (lv, av) = p.getBaseVelocity(self._body, physicsClientId=self._cid)
            p.applyExternalForce(self._body, -1,
                                 (-DRAG_K * lv[0], -DRAG_K * lv[1], -DRAG_K * lv[2]),
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
    def velocity(self) -> tuple:
        return p.getBaseVelocity(self._body, physicsClientId=self._cid)[0]

    @property
    def gyro_dps(self) -> tuple:
        """体系角速度 → 杆义 (roll右, pitch抬头, yaw右) °/s"""
        (_, av) = p.getBaseVelocity(self._body, physicsClientId=self._cid)
        inv = glm.inverse(self.quat)
        wb = inv * glm.vec3(*av)                  # 世界系→体系
        return (math.degrees(wb.x), -math.degrees(wb.y), -math.degrees(wb.z))

    def reset(self, spawn=(0, 0, 2)):
        """重置：回出生点、姿态水平、速度清零"""
        p.resetBasePositionAndOrientation(
            self._body, spawn, (0, 0, 0, 1), physicsClientId=self._cid)
        p.resetBaseVelocity(self._body, (0, 0, 0), (0, 0, 0),
                            physicsClientId=self._cid)

    def close(self):
        p.disconnect(physicsClientId=self._cid)
