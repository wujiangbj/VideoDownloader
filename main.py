"""
VideoDownloader - 视频下载器 (Windows EXE)

- 输入 URL → 自动嗅探视频 → 分析格式 → 下载 + 自动合并为 MP4

运行方式：
    python main.py            # 启动 GUI
    python main.py --cli URL  # 命令行模式（计划中）
"""

import sys
import os

# 确保能找到 engine 模块
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def main():
    try:
        from gui import VideoDownloaderApp
        app = VideoDownloaderApp()
        app.run()
    except ImportError as e:
        print(f"[ERROR] 无法加载 GUI 模块: {e}")
        print("请确保已安装必要的依赖: pip install -r requirements.txt")
        sys.exit(1)
    except Exception as e:
        print(f"[ERROR] 程序异常: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
