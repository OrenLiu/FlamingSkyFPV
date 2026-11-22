"""FlamingSkyFPV 入口。--frames N 用于自动化冒烟（N 帧后自动退出）"""
import argparse
import logging

def main():
    parser = argparse.ArgumentParser(description="FlamingSkyFPV 开源 FPV 穿越机模拟器")
    parser.add_argument("--frames", type=int, default=None, help="运行 N 帧后退出（冒烟测试）")
    args = parser.parse_args()
    import bootstrap
    bootstrap.ensure_user_files()  # 数据根 chdir + 缺失辅助文件生成(先于一切相对路径 IO)
    import log_setup
    log_setup.setup_logging()      # 日志双写 stderr + logs/<启动时间>.log（须在 chdir 后）
    logging.getLogger(__name__).info("FlamingSkyFPV starting, frames=%s", args.frames)
    from app import App
    app = App()
    try:
        app.run(max_frames=args.frames)
    finally:
        app.save_all()
        app.msp_server.stop()  # 退出显式停服：释放端口可重绑（未启动时幂等空操作）
        logging.getLogger(__name__).info("FlamingSkyFPV exiting")

if __name__ == "__main__":
    main()
