"""帧辅助：frame_preprocess() / SceneDrawer / pack_frame()（spec 5.3）。

自定义帧渲染路径不会执行引擎 frame_render() 内的光源脏标记与 EAU 处理，
由本模块复刻该段逻辑，引擎一行不改。上游同步核对点：soup3D/__init__.py
的 frame_render()（vendored 7.0.0）——上游更新时人工比对下面两段与其一致
（VENDORED.md 流程不变）。
"""
import soup3D


def frame_preprocess() -> None:
    """复刻引擎 frame_render 的光源与 EAU 段（来源：soup3D/__init__.py frame_render）。

    上游对齐要求：上游同步时人工核对本函数与引擎 frame_render 的对应段逐行一致。
    """
    # 设置光源：有光源变动时让所有着色器响应（引擎同款）
    if soup3D.light.dirty:
        soup3D.light.set_surface_light()

    # 执行更新执行队列：合并 → 写回全局 → 逐项执行 → 双清空（引擎同款语义：
    # 引擎为 EAU += light.EAU 后遍历全局本体，执行中入队可被长度守卫捕获）
    eau = soup3D.EAU + soup3D.light.EAU
    soup3D.EAU = eau
    eau_len = len(eau)
    for args in soup3D.EAU:
        args[0](*args[1:])
        if eau_len != len(soup3D.EAU):
            raise Exception(f"EAU length changed while running: "
                            f"{args[0].__name__}{(*args[1:],)}")

    soup3D.EAU = []
    soup3D.light.EAU = []


class SceneDrawer:
    """draw_scene 包装（spec 4.3/5.3）：包作者只读的 (model, face) 反射视图。

    无参可调用（转调 draw_scene）；.faces 属性经 draw_scene.__self__._model
    反射当前 screen 的模型（getattr(screen, "_model", None) → model.faces 或
    空列表，映射为 (model, face) 对列表；同模型 faces 连续排列）。
    """

    def __init__(self, draw_scene):
        self._draw_scene = draw_scene

    def __call__(self):
        self._draw_scene()

    @property
    def faces(self) -> list:
        screen = getattr(self._draw_scene, "__self__", None)
        model = getattr(screen, "_model", None)
        if model is None:
            return []
        return [(model, face) for face in model.faces]


def pack_frame(renderer, draw_scene) -> None:
    """预设帧组装点：光源/EAU 前置处理 + 帧渲染器接管（spec 5.3 契约）。

    由 App 渲染分支传入 soup3D.update(render=...)；包作者通常不自调本函数
    （游戏 runtime 每帧自动处理引擎光源/EAU，包无需自调）。
    """
    frame_preprocess()
    renderer.render_frame(SceneDrawer(draw_scene))
