"""设置界面：显示模式（全屏/固定大小窗口化/窗口化）+ 分辨率/帧率/画质预设；
保存即写盘并热重建（失败回落重启提示）"""
import logging

import imgui
import pygame
from .base import Screen
from app import RESOLUTIONS
from config_store import DISPLAY_MODES
from ui_scale import u

log = logging.getLogger(__name__)

# 最大帧率下拉：标签（显示）↔ fps_mode 存档值（config_store.Settings）双向映射
FPS_MODES = ["vsync", "30", "60", "120", "144", "165", "240", "unlimited"]
FPS_LABELS = ["垂直同步", "30", "60", "120", "144", "165", "240", "无限制"]

# 显示模式下拉：存档值 ↔ 中文标签（config_store.DISPLAY_MODES 顺序即下拉顺序）
DISPLAY_MODE_LABELS = {
    "fullscreen": "全屏",
    "fixed_window": "固定大小窗口化",
    "window": "窗口化",
}

class SettingsScreen(Screen):
    def enter(self, app):
        self.app = app
        # 工作副本：取消时不污染生效配置（非法存量值回落，fps_mode 先例）
        self._mode = (app.settings.display_mode
                      if app.settings.display_mode in DISPLAY_MODES
                      else "fixed_window")
        self._res = list(app.settings.resolution)
        # fps_mode 工作副本；非法存量值（不在候选表）回落 "60"
        self._fps = app.settings.fps_mode if app.settings.fps_mode in FPS_MODES \
            else "60"
        # 画质预设工作副本：非法存量值 enter 时即回落显示（fps_mode 先例）
        valid_ids = [p.id for p in app.packs if p.valid]
        self._preset_id = app.settings.shader_preset if app.settings.shader_preset in valid_ids \
            else "builtin_performance"

    def handle_event(self, event):
        if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
            self.app.switch(_main_menu())

    def render_ui(self):
        imgui.set_next_window_position(0, 0)
        io = imgui.get_io()
        imgui.set_next_window_size(io.display_size[0], io.display_size[1])
        imgui.begin("##settings", flags=imgui.WINDOW_NO_TITLE_BAR | imgui.WINDOW_NO_MOVE)
        imgui.text("设置")
        # 显示模式下拉：三态（存档值映射见模块头 DISPLAY_MODE_LABELS）
        modes = list(DISPLAY_MODES)
        _, picked = imgui.combo("显示模式", modes.index(self._mode),
                                [DISPLAY_MODE_LABELS[m] for m in modes])
        self._mode = modes[picked]
        # 分辨率：窗口化跟随窗口（只读文本）；全屏/固定窗口 = 下拉候选
        if self._mode == "window":
            cw, ch = pygame.display.get_surface().get_size()
            imgui.text(f"分辨率：跟随窗口大小（当前 {cw} × {ch}，拖拽窗口边缘或最大化调整）")
        else:
            labels = [f"{w} × {h}" for w, h in RESOLUTIONS]
            current = next((i for i, r in enumerate(RESOLUTIONS)
                            if list(r) == self._res), 0)
            _, picked = imgui.combo("分辨率", current, labels)
            self._res = list(RESOLUTIONS[picked])
        # 最大帧率下拉（vsync/无限制/数字档）
        fps_idx = FPS_MODES.index(self._fps)
        _, fps_picked = imgui.combo("最大帧率", fps_idx, FPS_LABELS)
        self._fps = FPS_MODES[fps_picked]
        # 画质预设下拉：候选项 = valid 包（内置在前），label = name，value 存 id
        valid_packs = [p for p in self.app.packs if p.valid]
        preset_labels = [p.name for p in valid_packs]
        preset_ids = [p.id for p in valid_packs]
        preset_idx = preset_ids.index(self._preset_id) if self._preset_id in preset_ids else 0
        _, preset_picked = imgui.combo("画质预设", preset_idx, preset_labels)
        self._preset_id = preset_ids[preset_picked]
        if self.app.preset_fallback:
            imgui.text("画质预设无效，已回落性能优先")
        # 底部按钮行：左「返回」右「保存并应用」
        if imgui.button("返回", u(200), u(40)):
            self.app.switch(_main_menu())
        imgui.same_line()
        if imgui.button("保存并应用", u(200), u(40)):
            preset_changed = self._preset_id != self.app.settings.shader_preset
            if self._mode == "window":
                # 分辨率跟随窗口：以当下窗口尺寸记忆，下次启动恢复
                self.app.settings.window_size = tuple(
                    pygame.display.get_surface().get_size())
            else:
                self.app.settings.resolution = tuple(self._res)
            self.app.settings.display_mode = self._mode
            self.app.settings.fps_mode = self._fps
            self.app.settings.shader_preset = self._preset_id
            log.info("settings saved: mode=%s res=%s window=%s fps=%s preset=%s",
                     self.app.settings.display_mode, self.app.settings.resolution,
                     self.app.settings.window_size, self.app.settings.fps_mode,
                     self.app.settings.shader_preset)
            self.app.store.save("settings", self.app.settings)
            # 不在此处直接 rebuild_window()：此刻处于 imgui 帧中间，字体图集操作
            # （load_cjk_font → add_font）帧中失败被吞 → font 置 None → 主循环
            # push/pop 失衡 → render 断言崩溃（用户实飞触发过）。只置标记，
            # run() 在帧边界（下一帧 tick 后）执行重建
            self.app._pending_rebuild = True
            if preset_changed:
                # 画质变更走帧边界 reload（rebuild 分支会先清本标记：rebuild 尾部
                # 已含 reload_shader_preset，两者同置位时只走 rebuild，spec 5.7）
                self.app._pending_preset_reload = True
        if self.app.restart_needed:
            imgui.text("窗口重建失败：设置将在下次启动生效")
        # 无效文件夹包：页面下方文字行列出（镜像 flight 场景选择，不进下拉）
        for p in self.app.packs:
            if not p.valid:
                imgui.text(f"{p.name}（无效：{p.error}）已跳过")
        imgui.end()

def _main_menu():
    from .main_menu import MainMenuScreen
    return MainMenuScreen()
