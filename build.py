"""
打包脚本 - 将 VideoDownloader 打包为单个 Windows EXE

使用 PyInstaller 打包，自动包含 FFmpeg。
"""

import os
import sys
import shutil
import zipfile
import subprocess
import urllib.request
from pathlib import Path

PROJECT_DIR = Path(__file__).parent.absolute()
DIST_DIR = PROJECT_DIR / "dist"
BUILD_DIR = PROJECT_DIR / "build"


def download_ffmpeg():
    """
    下载 FFmpeg Windows 便携版
    用于视频分段合并（替代 cat-catch 的 mux.js）
    """
    ffmpeg_dir = PROJECT_DIR / "ffmpeg"
    ffmpeg_exe = ffmpeg_dir / "bin" / "ffmpeg.exe"

    if ffmpeg_exe.exists():
        print(f"[FFmpeg] 已存在: {ffmpeg_exe}")
        return str(ffmpeg_dir)

    print("[FFmpeg] 正在下载 FFmpeg (约 30MB)...")

    # FFmpeg Windows 构建 (gyan.dev 静态链接版)
    url = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"

    zip_path = PROJECT_DIR / "ffmpeg.zip"

    try:
        urllib.request.urlretrieve(url, zip_path)
        print("[FFmpeg] 下载完成，正在解压...")

        with zipfile.ZipFile(zip_path, 'r') as zf:
            # 找到 ffmpeg.exe 所在的目录
            for member in zf.namelist():
                if member.endswith('ffmpeg.exe'):
                    ffmpeg_inner_dir = member.split('/')[0]
                    break

            zf.extractall(PROJECT_DIR)

        # 重命名为 ffmpeg
        extracted = PROJECT_DIR / ffmpeg_inner_dir
        if ffmpeg_dir.exists():
            shutil.rmtree(ffmpeg_dir)
        extracted.rename(ffmpeg_dir)

        # 清理
        os.remove(zip_path)
        print(f"[FFmpeg] 安装完成: {ffmpeg_dir}")

    except Exception as e:
        print(f"[FFmpeg] 下载失败: {e}")
        print("[FFmpeg] 程序仍可运行，但分段合并功能需要手动安装 FFmpeg")
        return None

    return str(ffmpeg_dir)


def build_exe():
    """PyInstaller 打包"""
    print("=" * 60)
    print("  VideoDownloader 打包工具")
    print("=" * 60)

    # 确保在项目目录
    os.chdir(PROJECT_DIR)

    # 清理旧的构建
    for d in [DIST_DIR, BUILD_DIR]:
        if d.exists():
            print(f"[清理] 删除 {d}")
            shutil.rmtree(d)

    # 删除旧的 spec 文件
    spec_file = PROJECT_DIR / "VideoDownloader.spec"
    if spec_file.exists():
        os.remove(spec_file)

    # 准备 FFmpeg
    ffmpeg_dir = download_ffmpeg()
    if ffmpeg_dir:
        ffmpeg_bin = str(Path(ffmpeg_dir) / "bin")
    else:
        ffmpeg_bin = None

    # PyInstaller 命令
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onefile",          # 单个 EXE
        "--windowed",         # 无控制台窗口（GUI 模式）
        "--name", "VideoDownloader",
        "--add-data", f"gui.py{os.pathsep}.",
        "--add-data", f"engine.py{os.pathsep}.",
        "--hidden-import", "yt_dlp",
        "--hidden-import", "yt_dlp.extractor",
        "--hidden-import", "yt_dlp.downloader",
        "--hidden-import", "yt_dlp.postprocessor",
        "--hidden-import", "requests",
        "--hidden-import", "bs4",
        "--hidden-import", "m3u8",
        "--hidden-import", "tkinter",
        "--collect-all", "yt_dlp",
    ]

    # 如果找到了 FFmpeg，一起打包
    if ffmpeg_bin and os.path.exists(ffmpeg_bin):
        ffmpeg_exe = os.path.join(ffmpeg_bin, "ffmpeg.exe")
        ffprobe_exe = os.path.join(ffmpeg_bin, "ffprobe.exe")
        if os.path.exists(ffmpeg_exe):
            cmd.extend([
                "--add-binary", f"{ffmpeg_exe}{os.pathsep}ffmpeg",
            ])
        if os.path.exists(ffprobe_exe):
            cmd.extend([
                "--add-binary", f"{ffprobe_exe}{os.pathsep}ffmpeg",
            ])

    cmd.append("main.py")

    print(f"[PyInstaller] 开始打包...")
    print(f"[PyInstaller] 命令: {' '.join(cmd)}")

    result = subprocess.run(cmd, capture_output=False)

    if result.returncode == 0:
        exe_path = DIST_DIR / "VideoDownloader.exe"
        if exe_path.exists():
            size_mb = os.path.getsize(exe_path) / (1024 * 1024)
            print(f"\n{'='*60}")
            print(f"  ✅ 打包成功!")
            print(f"  📁 输出: {exe_path}")
            print(f"  📏 大小: {size_mb:.1f} MB")
            print(f"{'='*60}")
        else:
            print(f"[错误] EXE 文件未找到")
    else:
        print(f"[错误] PyInstaller 打包失败 (退出码: {result.returncode})")


if __name__ == '__main__':
    build_exe()
