"""FlamingSkyFPV 入口。--frames N 用于自动化冒烟（N 帧后自动退出）"""
import argparse

def main():
    parser = argparse.ArgumentParser(description="FlamingSkyFPV 开源 FPV 穿越机模拟器")
    parser.add_argument("--frames", type=int, default=None, help="运行 N 帧后退出（冒烟测试）")
    args = parser.parse_args()
    from app import App
    app = App()
    try:
        app.run(max_frames=args.frames)
    finally:
        app.save_all()

if __name__ == "__main__":
    main()
