"""Screen 抽象基类：App 状态机的界面单元（设计文档第 3 节）"""

class Screen:
    """生命周期：enter（切入）→ handle_event/update/render_ui（每帧）→ exit（切出）"""
    def enter(self, app):
        self.app = app

    def exit(self):
        pass

    def handle_event(self, event):
        """pygame 事件（imgui 事件已由 App 层先行处理）"""

    def update(self, dt: float):
        """逻辑步进（渲染之前）"""

    def render_scene(self):
        """soup3D.update() 之后的 3D 直渲钩子。
        spike 结论：soup3D 7.0.0 的 Model.show() 显示列表回放在 Mesa 上失效
        （引擎 rend() 在编译内即建即删 VAO/VBO），场景模型须每帧在此直渲；
        默认无 3D 内容。"""

    def render_ui(self):
        """imgui 界面绘制（在 soup3D.update 之后、imgui.render 之前调用）"""

    def reload(self):
        """窗口重建后重载 GL 资源（贴图/模型），默认无资源"""
