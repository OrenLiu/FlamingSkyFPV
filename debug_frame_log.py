"""卡顿诊断记录仪：用户真实游玩会话的逐帧相位耗时 JSONL 记录。

环境变量 FLAME_STUTTER_LOG 门控（模块级读取，进程启动后不重读）：
- 未设置：frame_log_enabled=False，App 侧不实例化（self._flog=None，run()
  各打点全守卫，零开销，行为与无记录仪完全一致）；
- 设置（值=输出文件路径）：每帧写一行 JSONL（tick/ev/inp/upd/rnd/cnt/flip
  各相位毫秒 + 本帧 GC 事件），F9 热键写用户体感标记行。
"""
import atexit
import gc
import json
import os
import platform
import time

# 模块级读环境变量：空串/未设置 = 记录仪关闭
FLAME_STUTTER_LOG = os.environ.get("FLAME_STUTTER_LOG", "")
frame_log_enabled = bool(FLAME_STUTTER_LOG)

# frame() 输出的固定相位键序（与 App.run() 各段末尾 mark 的顺序一致）
_PHASE_KEYS = ("tick", "ev", "inp", "upd", "rnd", "cnt", "flip")


class FrameLogger:
    """逐帧相位耗时记录仪（App 持有；仅 FLAME_STUTTER_LOG 设置时实例化）"""

    def __init__(self, app):
        # 输出目录不存在则建：用户给任意路径即可记录
        os.makedirs(os.path.dirname(os.path.abspath(FLAME_STUTTER_LOG)),
                    exist_ok=True)
        self._f = open(FLAME_STUTTER_LOG, "w")  # 默认缓冲，close/定时 flush
        self._t0 = time.perf_counter()          # 相对启动秒的时间基准
        self._last_t = None                     # 上次 mark 时刻（帧首为 None）
        self._phases = {}                       # 当前帧各相位累计毫秒
        self._gc_t0 = {}                        # GC start 时刻（按代）
        self._gc_events = []                    # 本帧 GC 事件（frame 收口取走）
        self._last_flush = time.perf_counter()
        # 首行 meta：会话环境快照（分辨率/全屏为设置档值）
        meta = {"meta": True, "ts": time.time(),
                "python": platform.python_version(),
                "resolution": list(app.settings.resolution),
                "fullscreen": bool(app.settings.fullscreen),
                "preset": app.settings.shader_preset,
                "fps_mode": app.settings.fps_mode}
        self._f.write(json.dumps(meta) + "\n")
        gc.callbacks.append(self._gc_cb)  # GC 事件收集（回调内全防御）
        atexit.register(self.close)       # 进程退出自关（含崩溃路径兜底）

    # ---- GC 回调（解释器低层触发，任何异常必须静默不影响游戏） ----
    def _gc_cb(self, phase, info):
        try:
            g = info.get("generation", 0)
            if phase == "start":
                self._gc_t0[g] = time.perf_counter()
            else:
                t0 = self._gc_t0.pop(g, None)
                if t0 is not None:
                    self._gc_events.append(
                        {"g": g, "ms": round((time.perf_counter() - t0) * 1000.0, 3)})
        except Exception:
            pass

    def mark(self, key):
        """相位打点：距上次 mark 的毫秒累计到当前 key（App.run 各段末尾调用）"""
        now = time.perf_counter()
        if self._last_t is not None:
            self._phases[key] = (self._phases.get(key, 0.0)
                                 + (now - self._last_t) * 1000.0)
        self._last_t = now

    def frame(self, dt_ms):
        """帧收口：写本帧行并隐式重置帧首（dt_ms = clock.tick 返回值×1000）"""
        row = {"t": round(time.perf_counter() - self._t0, 4),
               "dt_ms": round(dt_ms, 3)}
        for k in _PHASE_KEYS:
            row[k] = round(self._phases.get(k, 0.0), 3)
        if self._gc_events:  # 本帧有 GC 事件才写 gc 键（省空间）
            row["gc"] = self._gc_events
            self._gc_events = []
        self._f.write(json.dumps(row) + "\n")
        self._phases = {}    # 帧首隐式重置：相位累计清零、mark 基准失效
        self._last_t = None
        if time.perf_counter() - self._last_flush >= 2.0:  # 2 秒 flush 一次
            self._f.flush()
            self._last_flush = time.perf_counter()

    def mark_user(self):
        """用户体感卡顿标记（F9 触发）：立即写行并 flush（供对齐帙帙时间轴）"""
        self._f.write(json.dumps(
            {"t": round(time.perf_counter() - self._t0, 4), "mark": "user"}) + "\n")
        self._f.flush()

    def close(self):
        """flush 并关文件（幂等；atexit 已注册自关）"""
        if self._f.closed:
            return
        try:
            gc.callbacks.remove(self._gc_cb)
        except ValueError:
            pass
        self._f.flush()
        self._f.close()
