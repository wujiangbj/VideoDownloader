# VideoDownloader

Windows 桌面视频下载工具，基于 yt-dlp + playwright，支持 1000+ 网站。

## 功能特性

- **页面嗅探**：自动发现网页中的视频资源
- **浏览器深嗅**：通过 Chrome/Edge 加载 JS 动态页面，提取隐藏视频源
- **缓存采集**：浏览器拦截网络请求，边播放边下载（适用于 SSL 不兼容的 CDN）
- **格式选择**：列出所有可用分辨率/编码，按需选择
- **DASH/HLS 自动合并**：分离的音视频流自动下载并合并为 MP4
- **B站 登录**：内嵌浏览器登录，获取 1080p 高清格式
- **直链下载**：支持 mp4/m3u8/mpd/webm 等直链 URL

## 依赖

| 组件 | 用途 | 安装方式 |
|------|------|---------|
| yt-dlp.exe | 视频提取引擎 | [GitHub Releases](https://github.com/yt-dlp/yt-dlp/releases) |
| ffmpeg.exe + ffprobe.exe | 音视频合并 | [FFmpeg Builds](https://github.com/yt-dlp/FFmpeg-Builds/releases) |
| Python 3.12 | 开发/打包 | `pip install -r requirements.txt` |

> Python 依赖：`tkinter`（内置）、`requests`、`beautifulsoup4`、`playwright`

## 使用方式

### 直接运行（开发模式）

```bash
# 安装依赖
pip install requests beautifulsoup4 playwright
playwright install chromium

# 下载 yt-dlp.exe 和 ffmpeg 到项目目录
# 或运行: python build.py

# 启动
python main.py
```

### 打包成 EXE

```bash
# 先确保 yt-dlp.exe 和 ffmpeg 在项目目录
# 放入 ffmpeg-master-latest-win64-gpl/bin/ffmpeg.exe 和 ffprobe.exe

pip install pyinstaller
python build.py
# 输出: dist/VideoDownloader/VideoDownloader.exe
```

## 按键说明

| 按钮 | 功能 |
|------|------|
| 🔍 嗅探视频 | 自动探测页面中的视频格式 |
| ⬇️ 开始下载 | 下载选中的格式 |
| 📡 缓存采集 | 浏览器拦截模式（适用于特殊网站） |
| 🔑 浏览器登录 | 登录 B站 获取高清 cookies |

## 注意事项

- **cookies.txt**：登录后的 cookies 保存在 EXE 同目录下，不同网站共用同一文件
- **缓存采集**：浏览器窗口打开后，播放视频再关闭窗口即可自动保存
- **Python 3.12**：打包时必须用 Python 3.12（3.13 与 PyInstaller 有兼容问题）

## 参考

- [yt-dlp](https://github.com/yt-dlp/yt-dlp)
- [cat-catch](https://github.com/xifangczy/cat-catch) — 浏览器视频嗅探插件（灵感来源）
