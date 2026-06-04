"""
视频下载引擎 - subprocess 模式

通过调用 yt-dlp CLI 工具实现下载功能，避免 PyInstaller 打包 yt-dlp Python 模块时的依赖分析问题。
同时支持：
- 页面嗅探（cat-catch 风格）
- 直链下载
- 完整下载流程
"""

import os
import re
import sys
import json
import time
import shutil
import hashlib
import threading
import subprocess
import urllib.request
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

# Windows: 隐藏子进程的控制台窗口（GUI 模式下启动控制台程序时）
if sys.platform == "win32":
    CREATE_NO_WINDOW = 0x08000000
else:
    CREATE_NO_WINDOW = 0


class VideoInfo:
    """视频信息数据结构"""
    def __init__(self):
        self.title = ""
        self.url = ""
        self.formats = []
        self.duration = 0
        self.thumbnail = ""
        self.website = ""


class DownloadEngine:
    """
    视频下载引擎 - subprocess 模式
    核心依赖：yt-dlp.exe（同目录下的独立可执行文件）
    """

    # 视频文件扩展名模式
    VIDEO_EXT_PATTERNS = [
        r'\.mp4(\?|$)', r'\.webm(\?|$)', r'\.mkv(\?|$)', r'\.flv(\?|$)',
        r'\.avi(\?|$)', r'\.mov(\?|$)', r'\.wmv(\?|$)', r'\.m4v(\?|$)',
        r'\.m3u8(\?|$)', r'\.mpd(\?|$)', r'\.ts(\?|$)', r'\.ogg(\?|$)',
    ]

    def __init__(self, progress_callback=None, log_callback=None):
        self.progress_callback = progress_callback or (lambda *a: None)
        self.log_callback = log_callback or (lambda m: None)
        self.cancel_flag = threading.Event()
        self.current_process = None
        self.download_dir = str(Path.home() / "Downloads" / "VideoDownloader")

    def set_download_dir(self, path):
        self.download_dir = path
        os.makedirs(self.download_dir, exist_ok=True)

    def cancel(self):
        self.cancel_flag.set()
        if self.current_process:
            try:
                self.current_process.terminate()
            except Exception:
                pass

    def reset(self):
        self.cancel_flag.clear()
        self.current_process = None

    @staticmethod
    def _get_app_dir():
        """获取程序目录（EXE 或脚本所在目录，用于查找 yt-dlp/ffmpeg 等组件）"""
        if getattr(sys, 'frozen', False):
            return os.path.dirname(sys.executable)
        return os.path.dirname(os.path.abspath(__file__))

    @staticmethod
    def _get_user_dir():
        """获取 EXE 所在目录（cookies 和 config 存于此）"""
        if getattr(sys, 'frozen', False):
            return os.path.dirname(sys.executable)
        return os.path.dirname(os.path.abspath(__file__))

    @staticmethod
    def _get_cookie_file(site=None):
        """获取 cookies 文件路径（按站点名区分，Netscape 格式，与 EXE 同目录）"""
        if site:
            return os.path.join(DownloadEngine._get_user_dir(), f'cookies_{site}.txt')
        return os.path.join(DownloadEngine._get_user_dir(), 'cookies.txt')

    @staticmethod
    def _add_cookie_args(args, url=None):
        """
        加载 cookies.txt（所有网站共用，Netscape 格式自带 domain 字段不冲突）
        """
        cf = DownloadEngine._get_cookie_file()
        if os.path.exists(cf):
            args.extend(['--cookies', cf])
        return args

    def login_with_browser(self):
        """
        打开浏览器让用户登录（获取高画质所需的 cookies）
        登录后关闭窗口即可自动保存 cookies 用于后续下载
        """
        self.log_callback("=" * 40)
        self.log_callback("[登录] 正在打开浏览器...")

        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            self.log_callback("[登录] playwright 未内置")
            return False

        # 补丁：隐藏 node.exe 控制台窗口
        if sys.platform == "win32":
            _orig_Popen = subprocess.Popen
            class _SilentPopen(subprocess.Popen):
                def __init__(self, *a, **kw):
                    kw.setdefault('creationflags', 0)
                    kw['creationflags'] |= CREATE_NO_WINDOW
                    super().__init__(*a, **kw)
            subprocess.Popen = _SilentPopen
            _patched = True
        else:
            _patched = False

        try:
            with sync_playwright() as p:
                # 使用系统 Chrome（而非 playwright 内置的 Chromium）
                launch_kwargs = {'headless': False}
                browser_channel = self._detect_browser_channel()
                if browser_channel:
                    launch_kwargs['channel'] = browser_channel
                    self.log_callback(f"[登录] 使用系统浏览器: {browser_channel}")
                browser = p.chromium.launch(**launch_kwargs)
                context = browser.new_context(no_viewport=True)
                page = context.new_page()
                page.goto('https://www.bilibili.com', wait_until='domcontentloaded')

                self.log_callback("[登录] 浏览器已打开，请登录 B 站")
                self.log_callback("[登录] 登录完成后关闭浏览器窗口即可")

                # 等待用户关闭浏览器
                try:
                    page.wait_for_event('close', timeout=300000)  # 5 分钟
                except:
                    pass

                # 获取 cookies 并保存为 Netscape 格式
                cookies = context.cookies()
                browser.close()

                if not cookies:
                    self.log_callback("[登录] 未获取到 cookie")
                    return False

                # 写 Netscape 格式，追加到 cookies.txt（与已有 cookie 合并）
                cf = self._get_cookie_file()
                # 读取已有 cookies（如果有的话，避免覆盖其他网站的 cookie）
                existing_lines = set()
                if os.path.exists(cf):
                    with open(cf, 'r', encoding='utf-8') as ef:
                        for line in ef:
                            stripped = line.strip()
                            if stripped and not stripped.startswith('#'):
                                existing_lines.add(stripped)

                with open(cf, 'w', encoding='utf-8') as f:
                    f.write("# Netscape HTTP Cookie File\n")
                    f.write("# Saved by VideoDownloader\n\n")
                    # 先写入已有的 cookie（保留其他网站登录的）
                    for line in sorted(existing_lines):
                        f.write(line + '\n')
                    # 再写入新 cookie（覆盖同 domain+name 的旧值）
                    written_keys = set()
                    for c in cookies:
                        domain = c.get('domain', '')
                        flag = 'TRUE' if domain.startswith('.') else 'FALSE'
                        path = c.get('path', '/')
                        secure = 'TRUE' if c.get('secure') else 'FALSE'
                        expires = int(c.get('expires', -1) or -1)
                        name = c.get('name', '')
                        value = c.get('value', '')
                        if not name:
                            continue
                        key = f"{domain}\t{name}"
                        if key not in written_keys:
                            f.write(f"{domain}\t{flag}\t{path}\t{secure}\t{expires}\t{name}\t{value}\n")
                            written_keys.add(key)

                self.log_callback(f"[登录] 已保存 {len(cookies)} 个 cookie")
                self.log_callback("[登录] 现在可以使用高画质下载了！")
                return True

        except Exception as e:
            self.log_callback(f"[登录] 出错: {e}")
            return False
        finally:
            if _patched:
                subprocess.Popen = _orig_Popen

    @staticmethod
    def _get_ytdlp_path():
        """获取 yt-dlp.exe 路径"""
        # 优先使用同目录下的
        exe_dir = os.path.dirname(os.path.abspath(__file__))
        local = os.path.join(exe_dir, 'yt-dlp.exe')
        if os.path.exists(local):
            return local

        # PyInstaller 环境：检查 dist 根目录
        if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
            dist_root = os.path.dirname(sys.executable)
            dist_ytdlp = os.path.join(dist_root, 'yt-dlp.exe')
            if os.path.exists(dist_ytdlp):
                return dist_ytdlp

        # 系统 PATH
        for p in ['yt-dlp.exe', 'yt-dlp']:
            if shutil.which(p):
                return p

        # 没找到，返回默认路径（后续会自动下载）
        return local

    def _ensure_ytdlp(self):
        """
        确保 yt-dlp.exe 存在，如果不存在则自动下载
        Returns: (path, downloaded)
        """
        path = self._get_ytdlp_path()
        if os.path.exists(path):
            return path, False

        # 也检查 dist 根目录
        if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
            alt_path = os.path.join(os.path.dirname(sys.executable), 'yt-dlp.exe')
            if os.path.exists(alt_path):
                return alt_path, False
            path = alt_path
        else:
            # 下载到同目录
            path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'yt-dlp.exe')

        self.log_callback("[安装] yt-dlp 未找到，正在自动下载...")
        try:
            # 从 GitHub 下载最新版 yt-dlp.exe
            url = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe"
            self.log_callback(f"[安装] 下载: {url}")
            import urllib.request
            urllib.request.urlretrieve(url, path)
            if os.path.exists(path) and os.path.getsize(path) > 1000000:
                self.log_callback(f"[安装] 下载成功: {os.path.getsize(path)//1024//1024} MB")
                return path, True
            else:
                self.log_callback("[安装] 下载失败，文件太小")
                return None, False
        except Exception as e:
            self.log_callback(f"[安装] 下载失败: {e}")
            self.log_callback("[安装] 请手动下载: https://github.com/yt-dlp/yt-dlp/releases")
            return None, False

    def _find_ffmpeg(self):
        """查找 ffmpeg.exe"""
        # 与 yt-dlp 同目录
        for search_dir in [
            os.path.dirname(self._get_ytdlp_path()),
        ]:
            if not search_dir or not os.path.isdir(search_dir):
                continue
            candidate = os.path.join(search_dir, 'ffmpeg.exe')
            if os.path.exists(candidate):
                return os.path.dirname(candidate)
        # 可执行文件同目录（PyInstaller dist root）
        if getattr(sys, 'frozen', False):
            exe_dir = os.path.dirname(sys.executable)
            if os.path.exists(os.path.join(exe_dir, 'ffmpeg.exe')):
                return exe_dir
        # 系统 PATH
        ff = shutil.which('ffmpeg')
        if ff:
            return os.path.dirname(ff)
        return None

    def _find_ffprobe(self):
        """查找 ffprobe.exe"""
        ffmpeg_dir = self._find_ffmpeg()
        if ffmpeg_dir:
            fp = os.path.join(ffmpeg_dir, 'ffprobe.exe')
            if os.path.exists(fp):
                return fp
        fp = shutil.which('ffprobe')
        return fp if fp else None

    def _ensure_ffmpeg(self):
        """
        确保 ffmpeg.exe 存在（用于合并音视频），不存在则自动下载
        Returns: (ffmpeg_dir, success)
        """
        ffmpeg_dir = self._find_ffmpeg()
        if ffmpeg_dir:
            return ffmpeg_dir, True

        # 下载到 yt-dlp 同目录
        dest_dir = os.path.dirname(self._get_ytdlp_path())
        if not dest_dir or not os.path.isdir(dest_dir):
            dest_dir = os.path.dirname(os.path.abspath(__file__))

        self.log_callback("[安装] ffmpeg 未找到，正在自动下载（用于合并音视频）...")
        try:
            zip_url = "https://github.com/yt-dlp/FFmpeg-Builds/releases/latest/download/ffmpeg-master-latest-win64-gpl.zip"
            self.log_callback(f"[安装] 下载: {zip_url}")
            import urllib.request
            import zipfile
            from io import BytesIO

            # 下载并解压
            resp = urllib.request.urlopen(zip_url)
            zip_data = BytesIO(resp.read())
            extracted = False
            with zipfile.ZipFile(zip_data) as zf:
                for name in zf.namelist():
                    fname = os.path.basename(name)
                    if fname in ('ffmpeg.exe', 'ffprobe.exe'):
                        zf.extract(name, dest_dir)
                        # 移到根目录
                        src = os.path.join(dest_dir, name)
                        dst = os.path.join(dest_dir, fname)
                        if os.path.exists(src) and src != dst:
                            if os.path.exists(dst):
                                os.remove(dst)
                            os.rename(src, dst)
                            extracted = True
                # 清理空目录
                for root, dirs, files in os.walk(dest_dir, topdown=False):
                    if root != dest_dir:
                        try:
                            os.rmdir(root)
                        except:
                            pass

            ffmpeg_exe = os.path.join(dest_dir, 'ffmpeg.exe')
            if os.path.exists(ffmpeg_exe):
                self.log_callback(f"[安装] ffmpeg 下载成功: {os.path.getsize(ffmpeg_exe)//1024//1024} MB")
                return dest_dir, True
            else:
                self.log_callback("[安装] ffmpeg 解压失败")
                return None, False
        except Exception as e:
            self.log_callback(f"[安装] ffmpeg 下载失败: {e}")
            self.log_callback("[安装] 可手动安装: https://ffmpeg.org/download.html")
            return None, False

    def _run_ytdlp(self, args, timeout=60):
        """
        运行 yt-dlp 命令

        Returns: (returncode, stdout, stderr)
        """
        ytdlp = self._get_ytdlp_path()
        if not os.path.exists(ytdlp):
            ytdlp, _ = self._ensure_ytdlp()
            if not ytdlp:
                return -1, "", "yt-dlp 未安装且自动下载失败"
        cmd = [ytdlp] + args
        self.log_callback(f"[CMD] yt-dlp {' '.join(args[:5])}...")

        try:
            process = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                encoding='utf-8',
                errors='replace',
                creationflags=CREATE_NO_WINDOW,
            )
            # cookie 提取失败（Edge 正在运行）是非致命错误
            stderr_lower = process.stderr.lower()
            is_cookie_error = 'could not copy' in stderr_lower and 'cookie' in stderr_lower
            if is_cookie_error and process.stdout.strip():
                self.log_callback("[探测] ⚠️ Edge 浏览器正在运行，部分高画质可能不可用")
                return 0, process.stdout, process.stderr
            return process.returncode, process.stdout, process.stderr
        except subprocess.TimeoutExpired:
            return -1, "", "命令超时"
        except FileNotFoundError:
            return -1, "", f"yt-dlp 未找到: {ytdlp}"
        except Exception as e:
            return -1, "", str(e)

    # ===================== 视频嗅探 =====================

    def sniff_video(self, url):
        """
        cat-catch 风格视频嗅探：
        解析页面 HTML，查找 video 标签、source 标签、URL 模式匹配
        """
        self.log_callback(f"[嗅探] 正在分析页面: {url}")

        info = VideoInfo()
        info.url = url
        info.title = self._guess_title(url)
        info.website = urlparse(url).netloc

        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                              '(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            }
            resp = requests.get(url, headers=headers, timeout=30, allow_redirects=True)
            resp.raise_for_status()
            html = resp.text
            soup = BeautifulSoup(html, 'html.parser')

            video_urls = set()

            # 检测 <video> 标签
            for video in soup.find_all('video'):
                src = video.get('src', '')
                if src:
                    video_urls.add(urljoin(url, src))
                for source in video.find_all('source'):
                    src = source.get('src', '')
                    if src:
                        video_urls.add(urljoin(url, src))

            # URL 模式匹配（用 finditer 代替 findall，避免捕获组问题）
            for pattern in self.VIDEO_EXT_PATTERNS:
                try:
                    combined = r'https?://[^\s"\'<>]+' + pattern[1:]
                    for m in re.finditer(combined, html, re.IGNORECASE):
                        video_urls.add(m.group(0).rstrip('.,;:!?)'))
                except re.error:
                    continue

            # script 中的 video URL
            json_patterns = [
                r'["\'](https?://[^"\']+\.(?:m3u8|mp4|flv|mkv|mpd|ts))["\']',
                r'["\']url["\']\s*:\s*["\']([^"\']+)["\']',
            ]
            for pat in json_patterns:
                matches = re.findall(pat, html, re.IGNORECASE)
                for m in matches:
                    if any(ext in m.lower() for ext in ['.m3u8', '.mp4', '.flv', '.mkv', '.mpd', '.ts']):
                        video_urls.add(m)

            # 分类
            for vurl in video_urls:
                vurl_lower = vurl.lower()
                if '.m3u8' in vurl_lower:
                    info.formats.append({
                        "id": "hls", "ext": "mp4", "resolution": "auto",
                        "filesize": 0, "filesize_str": "未知",
                        "url": vurl, "note": "HLS 分片流(自动合并)", "codec": "h264/aac",
                    })
                elif '.mpd' in vurl_lower:
                    info.formats.append({
                        "id": "dash", "ext": "mp4", "resolution": "auto",
                        "filesize": 0, "filesize_str": "未知",
                        "url": vurl, "note": "DASH 分片流(自动合并)", "codec": "h264/aac",
                    })
                elif any(ext in vurl_lower for ext in ['.mp4', '.webm', '.flv', '.mkv', '.ts', '.avi', '.mov']):
                    ext = vurl_lower.split('.')[-1].split('?')[0]
                    info.formats.append({
                        "id": f"direct_{len(info.formats)}", "ext": ext,
                        "resolution": "auto", "filesize": 0, "filesize_str": "未知",
                        "url": vurl, "note": "直链", "codec": "auto",
                    })

            if soup.title and soup.title.string:
                info.title = self._sanitize_filename(soup.title.string.strip())

            if not info.formats:
                for pattern in self.VIDEO_EXT_PATTERNS:
                    if re.search(pattern, url, re.IGNORECASE):
                        ext_match = re.search(r'\.(\w+)(\?|$)', url)
                        ext = ext_match.group(1) if ext_match else 'mp4'
                        info.formats.append({
                            "id": "direct", "ext": ext, "resolution": "auto",
                            "filesize": 0, "filesize_str": "未知",
                            "url": url, "note": "直链", "codec": "auto",
                        })
                        break

            self.log_callback(f"[嗅探] 发现 {len(info.formats)} 个视频资源")

        except Exception as e:
            self.log_callback(f"[嗅探] 出错: {e}")

        return info

    # ===================== 浏览器深度嗅探（Python playwright） =====================

    def _detect_browser_channel(self):
        """
        检测系统已安装的浏览器，优先使用（无需额外下载 Chromium）
        """
        chrome_paths = [
            r'C:\Program Files\Google\Chrome\Application\chrome.exe',
            r'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe',
            os.path.expanduser(r'~\AppData\Local\Google\Chrome\Application\chrome.exe'),
        ]
        for p in chrome_paths:
            if os.path.exists(p):
                return 'chrome'

        edge_paths = [
            r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
            r'C:\Program Files\Microsoft\Edge\Application\msedge.exe',
        ]
        for p in edge_paths:
            if os.path.exists(p):
                return 'msedge'

        return None

    def deep_sniff(self, url, browser_override=None):
        """
        浏览器深度嗅探 - 使用 Python playwright 加载 JS 动态页面获取视频源

        Args:
            url: 视频页面 URL
            browser_override: 浏览器选择 ('chrome', 'msedge', None=自动检测)

        适用于 JS 动态加载的视频页面（如央视直播、B站等）。
        使用系统已安装的 Chrome/Edge，无需额外下载浏览器。
        playwright Python 包已内置于 EXE 中。
        """
        import time, json

        info = VideoInfo()
        info.url = url

        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            self.log_callback("[深嗅] playwright 未内置，请重新下载完整版 EXE")
            return info

        # 补丁: playwright 内部会启动 node.exe（控制台程序），
        # 从 GUI 模式 EXE 启动时会弹出黑框。用 CREATE_NO_WINDOW 隐藏。
        if sys.platform == "win32":
            _orig_Popen = subprocess.Popen
            class _SilentPopen(subprocess.Popen):
                def __init__(self, *args, **kwargs):
                    kwargs.setdefault('creationflags', 0)
                    kwargs['creationflags'] |= CREATE_NO_WINDOW
                    super().__init__(*args, **kwargs)
            subprocess.Popen = _SilentPopen
            _patched = True
        else:
            _patched = False

        # 检测系统浏览器（支持用户指定）
        browser_channel = None
        if browser_override and browser_override in ('chrome', 'msedge'):
            browser_channel = browser_override
            self.log_callback(f"[深嗅] 使用指定浏览器: {browser_override}")
        else:
            browser_channel = self._detect_browser_channel()
            if browser_channel:
                self.log_callback(f"[深嗅] 使用系统浏览器: {browser_channel}")
            else:
                self.log_callback(f"[深嗅] 未找到 Chrome/Edge，但 playwright 已内置")

        self.log_callback(f"[深嗅] 正在加载页面: {url}")

        source_url = ""
        page_title = ""
        page_config = None

        try:
            with sync_playwright() as p:
                # 启动浏览器
                launch_kwargs = {
                    'headless': True,
                    'args': [
                        '--no-sandbox',
                        '--disable-gpu',
                        '--disable-software-rasterizer',
                    ]
                }
                if browser_channel:
                    launch_kwargs['channel'] = browser_channel
                browser = p.chromium.launch(**launch_kwargs)

                page = browser.new_page()
                page.goto(url, wait_until='domcontentloaded', timeout=30000)

                # 轮询等待页面 JS 加载完 __playerConfig__
                for i in range(20):
                    if self.cancel_flag.is_set():
                        break
                    try:
                        config = page.evaluate('() => window.__playerConfig__')
                        if config and isinstance(config, dict) and config.get('source'):
                            source_url = config['source']
                            page_title = config.get('title', '') or page.evaluate('document.title')
                            page_config = config
                            self.log_callback(f"[深嗅] 第 {i+1} 次尝试，找到视频源!")
                            break
                    except:
                        pass

                    # 也检查 video 元素（过滤 blob: URL，B站 MSE 会使用）
                    try:
                        video_src = page.evaluate('''() => {
                            var v = document.querySelector('video');
                            return v ? (v.currentSrc || v.src || '') : '';
                        }''')
                        if video_src and not video_src.startswith('blob:'):
                            source_url = video_src
                            page_title = page.evaluate('document.title')
                            self.log_callback(f"[深嗅] 第 {i+1} 次尝试，从 video 标签找到视频!")
                            break
                    except:
                        pass

                    time.sleep(1.5)

                browser.close()

        except Exception as e:
            self.log_callback(f"[深嗅] 浏览器加载出错: {e}")

        # 恢复子进程 Popen
        if _patched:
            subprocess.Popen = _orig_Popen

        if source_url:
            self.log_callback(f"[深嗅] 视频地址: {source_url}")
            info.title = self._sanitize_filename(page_title or url.split('?')[0].split('/')[-1])
            fmt_id = "hls" if '.m3u8' in source_url.lower() else \
                     "dash" if '.mpd' in source_url.lower() else "direct"
            note = "HLS 分片流" if '.m3u8' in source_url.lower() else \
                   "DASH 分片流" if '.mpd' in source_url.lower() else "直链"
            info.formats.append({
                "id": fmt_id, "ext": "mp4", "resolution": "auto",
                "filesize": 0, "filesize_str": "未知",
                "url": source_url, "note": f"{note} (深嗅)", "codec": "auto",
            })
            self.log_callback(f"[深嗅] 成功! 标题: {info.title}")
        else:
            self.log_callback(f"[深嗅] 未能找到视频源")

        return info

    # ===================== 缓存采集模式（浏览器拦截网络请求） =====================

    def cache_capture(self, url, output_dir=None, filename=None):
        """
        缓存采集模式：浏览器拦截视频网络请求，边播放边存
        适用：SSL 不兼容的 CDN、需要浏览器环境才能播放的视频站
        使用：在浏览器中完整播放一次视频，关闭窗口后自动合成
        提示：拖动进度条可加速（但不要回拖以免重复下载同一段）
        """
        import time
        import tempfile
        import hashlib

        self.log_callback("=" * 50)
        self.log_callback("[采集] 缓存采集模式")
        self.log_callback(f"[采集] 目标: {url}")
        self.log_callback("[采集] ═══════════════════════════════════")
        self.log_callback("[采集] 浏览器窗口已打开")
        self.log_callback("[采集] 请在浏览器中完整播放一次视频")
        self.log_callback("[采集] 💡 拖动进度条可加速下载")
        self.log_callback("[采集] 💡 播放完成后关闭浏览器窗口")
        self.log_callback("[采集] ═══════════════════════════════════")
        self.log_callback("=" * 50)

        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            self.log_callback("[采集] playwright 未内置")
            return False, "", ""

        if output_dir:
            self.download_dir = output_dir
        os.makedirs(self.download_dir, exist_ok=True)

        if sys.platform == "win32":
            _orig_Popen = subprocess.Popen
            class _SilentPopen(subprocess.Popen):
                def __init__(self, *a, **kw):
                    kw.setdefault('creationflags', 0)
                    kw['creationflags'] |= CREATE_NO_WINDOW
                    super().__init__(*a, **kw)
            subprocess.Popen = _SilentPopen
            _patched = True
        else:
            _patched = False

        seen_urls = set()         # 去重：已捕获的完整 URL
        seen_hashes = set()       # 去重：已捕获的内容哈希（防止同一内容不同 URL）
        captured_segments = []
        page_title = ""
        temp_dir = tempfile.mkdtemp(prefix='vd_capture_')

        try:
            with sync_playwright() as p:
                browser_channel = self._detect_browser_channel()
                launch_kwargs = {
                    'headless': False,
                    'args': ['--no-sandbox', '--disable-gpu',
                            '--autoplay-policy=no-user-gesture-required'],
                }
                if browser_channel:
                    launch_kwargs['channel'] = browser_channel

                browser = p.chromium.launch(**launch_kwargs)
                context = browser.new_context(no_viewport=True)
                page = context.new_page()

                def _handle_route(route):
                    nonlocal captured_segments, seen_urls, seen_hashes
                    req = route.request
                    req_url = req.url

                    # 检查是否是视频相关请求
                    url_lower = req_url.lower()
                    is_video_url = any(ext in url_lower for ext in
                                       ['.mp4', '.webm', '.mkv', '.flv', '.ts', '.m4s',
                                        '.m3u8', '.mpd', '.avi', '.mov'])
                    is_m3u8 = '.m3u8' in url_lower

                    try:
                        resp = route.fetch()
                    except Exception:
                        route.continue_()
                        return

                    content_type = (resp.headers.get('content-type', '') or '').lower()
                    is_video_ct = any(ct in content_type for ct in
                                      ['video/', 'audio/',
                                       'application/vnd.apple.mpegurl',
                                       'application/x-mpegurl',
                                       'application/octet-stream'])

                    if is_video_url or is_m3u8 or is_video_ct:
                        try:
                            body = resp.body()
                            if not body or len(body) < 2000:
                                route.fulfill(response=resp)
                                return

                            # 去重：URL 级别
                            if req_url in seen_urls:
                                route.fulfill(response=resp)
                                return

                            # 去重：内容哈希（防止同一段视频用不同 URL 请求两次）
                            body_hash = hashlib.md5(body[:4096]).hexdigest()
                            if body_hash in seen_hashes:
                                route.fulfill(response=resp)
                                return

                            seen_urls.add(req_url)
                            seen_hashes.add(body_hash)

                            if is_m3u8 or 'mpegurl' in content_type:
                                self.log_callback(f"[采集] 捕获 M3U8 播放列表")
                            else:
                                seg_name = f"seg_{len(captured_segments):04d}.ts"
                                seg_path = os.path.join(temp_dir, seg_name)
                                with open(seg_path, 'wb') as sf:
                                    sf.write(body)
                                captured_segments.append({
                                    'url': req_url, 'path': seg_path, 'size': len(body),
                                })
                                size_mb = len(body) / (1024 * 1024)
                                total_mb = sum(s['size'] for s in captured_segments) / (1024 * 1024)
                                self.log_callback(
                                    f"[采集] 片段 #{len(captured_segments)} "
                                    f"({size_mb:.1f}MB) 累计 {total_mb:.1f}MB")
                                self.progress_callback(
                                    min(len(captured_segments) * 5, 95), 0, 0,
                                    f"已采集 {total_mb:.1f} MB / {len(captured_segments)} 片段")
                        except Exception:
                            pass

                    route.fulfill(response=resp)

                # 拦截所有视频相关请求
                page.route("**/*", _handle_route)

                page.goto(url, wait_until='domcontentloaded', timeout=30000)
                page_title = page.evaluate('document.title') or url
                self.log_callback(f"[采集] 页面: {page_title}")
                self.log_callback(f"[采集] ═══════════════════════════════════")
                self.log_callback(f"[采集] ▶ 请播放视频，播放完成后关闭窗口")
                self.log_callback(f"[采集] ═══════════════════════════════════")

                # 每 3 秒弹一个提示（最多弹 3 次）
                hint_count = 0
                def _show_hint():
                    nonlocal hint_count
                    if hint_count < 3:
                        try:
                            page.evaluate('''() => {
                                let d = document.createElement('div');
                                d.id = '__vd_hint__';
                                d.style.cssText = 'position:fixed;top:10px;right:10px;z-index:99999;'
                                    + 'background:#8e44ad;color:white;padding:12px 20px;border-radius:8px;'
                                    + 'font-size:14px;font-family:Microsoft YaHei;box-shadow:0 4px 12px rgba(0,0,0,0.3)';
                                d.textContent = '播放完成后关闭窗口即可自动保存视频';
                                document.body.appendChild(d);
                                setTimeout(() => d.remove(), 4000);
                            }''')
                        except Exception:
                            pass
                        hint_count += 1

                # 30秒后提示一次
                import threading
                def _delayed_hints():
                    time.sleep(30)
                    if not self.cancel_flag.is_set():
                        _show_hint()
                    time.sleep(60)
                    if not self.cancel_flag.is_set():
                        _show_hint()

                hint_thread = threading.Thread(target=_delayed_hints, daemon=True)
                hint_thread.start()

                try:
                    page.wait_for_event('close', timeout=600000)
                except Exception:
                    pass

                browser.close()

        except Exception as e:
            self.log_callback(f"[采集] 浏览器出错: {e}")
        finally:
            if _patched:
                subprocess.Popen = _orig_Popen

        # --- 处理结果 ---
        total_size = sum(s['size'] for s in captured_segments)
        total_mb = total_size / (1024 * 1024)
        self.log_callback(f"[采集] ═══════════════════════════════════")
        self.log_callback(f"[采集] 共采集 {len(captured_segments)} 片段 ({total_mb:.1f} MB)")

        if not captured_segments:
            self.log_callback("[采集] 未捕获到视频数据")
            self.log_callback("[采集] 提示: 确保在浏览器中播放了视频")
            try:
                import shutil
                shutil.rmtree(temp_dir, ignore_errors=True)
            except Exception:
                pass
            return False, "", ""

        if filename:
            safe_name = self._sanitize_filename(filename)
        else:
            safe_name = self._sanitize_filename(page_title or 'video')
        output_path = os.path.join(self.download_dir, f"{safe_name}.mp4")

        self.log_callback(f"[采集] 正在合并为 {os.path.basename(output_path)}...")
        self.progress_callback(95, 0, 0, "合并处理中...")

        if len(captured_segments) == 1:
            src = captured_segments[0]['path']
            ffmpeg_dir = self._find_ffmpeg()
            if ffmpeg_dir:
                ffmpeg_exe = os.path.join(ffmpeg_dir, 'ffmpeg.exe')
                subprocess.run([ffmpeg_exe, '-y', '-i', src, '-c', 'copy', output_path],
                              capture_output=True, timeout=300,
                              creationflags=CREATE_NO_WINDOW)
            else:
                import shutil
                shutil.copy2(src, output_path)
        else:
            concat_file = os.path.join(temp_dir, 'concat.txt')
            with open(concat_file, 'w', encoding='utf-8') as f:
                for seg in captured_segments:
                    f.write(f"file '{seg['path'].replace(chr(92), '/')}'\n")

            ffmpeg_dir = self._find_ffmpeg()
            if ffmpeg_dir:
                ffmpeg_exe = os.path.join(ffmpeg_dir, 'ffmpeg.exe')
                result = subprocess.run(
                    [ffmpeg_exe, '-y', '-f', 'concat', '-safe', '0',
                     '-i', concat_file, '-c', 'copy', output_path],
                    capture_output=True, text=True, timeout=600,
                    creationflags=CREATE_NO_WINDOW,
                )
                if result.returncode != 0:
                    self.log_callback(f"[采集] 无损合并失败，尝试转码...")
                    subprocess.run(
                        [ffmpeg_exe, '-y', '-f', 'concat', '-safe', '0',
                         '-i', concat_file, '-c:v', 'libx264', '-c:a', 'aac',
                         output_path],
                        capture_output=True, timeout=600,
                        creationflags=CREATE_NO_WINDOW,
                    )
            else:
                self.log_callback("[采集] ffmpeg 不可用")
                output_path = temp_dir

        try:
            import shutil
            shutil.rmtree(temp_dir, ignore_errors=True)
        except Exception:
            pass

        if os.path.exists(output_path) and os.path.isfile(output_path):
            size_mb = os.path.getsize(output_path) / (1024 * 1024)
            if size_mb < 0.05:
                self.log_callback(f"[采集] 文件过小 ({size_mb:.2f} MB)")
                try:
                    os.remove(output_path)
                except Exception:
                    pass
                return False, "", ""

            self.log_callback(f"[采集] ✅ 完成: {os.path.basename(output_path)} ({size_mb:.1f} MB)")
            self.progress_callback(100, 0, 0, "完成!")
            return True, output_path, page_title
        else:
            self.log_callback("[采集] ❌ 合并失败")
            return False, "", ""

    # ===================== 深度学习探测（yt-dlp CLI） =====================

    def probe_with_ytdlp(self, url):
        """
        使用 yt-dlp CLI 探测视频格式
        支持 1000+ 网站（B站、YouTube、抖音等）
        """
        self.log_callback(f"[探测] yt-dlp 分析: {url}")

        # 使用 yt-dlp --dump-json 获取视频信息
        # 必须加 User-Agent，否则 B站 返回 HTTP 412
        # 加 --no-check-certificates 应对小站 SSL 配置不标准
        probe_args = [
            '--dump-json', '--no-playlist', '--no-warnings',
            '--no-check-certificates',
            '--user-agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36',
        ]
        self._add_cookie_args(probe_args, url)
        probe_args.append(url)

        ret, stdout, stderr = self._run_ytdlp(probe_args, timeout=30)

        if ret != 0 or not stdout.strip():
            self.log_callback(f"[探测] yt-dlp 失败，回退到页面嗅探")
            info = self.sniff_video(url)
            if not info.formats:
                self.log_callback(f"[探测] 页面嗅探无结果，尝试浏览器深嗅...")
                info = self.deep_sniff(url)
            return info

        try:
            data = json.loads(stdout.split('\n')[0])
        except json.JSONDecodeError:
            self.log_callback(f"[探测] JSON 解析失败，回退到页面嗅探")
            info = self.sniff_video(url)
            if not info.formats:
                self.log_callback(f"[探测] 页面嗅探无结果，尝试浏览器深嗅...")
                info = self.deep_sniff(url)
            return info

        info = VideoInfo()
        info.url = url
        info.title = self._sanitize_filename(data.get('title', 'unknown'))
        info.duration = data.get('duration', 0)
        info.thumbnail = data.get('thumbnail', '')
        info.website = urlparse(url).netloc

        formats = data.get('formats', [])
        seen_keys = set()  # 去重 key：(resolution, format_note, vcodec, fps)
        for f in formats:
            fmt_id = f.get('format_id', '')
            ext = f.get('ext', 'mp4')
            resolution = f.get('resolution', '')
            filesize = f.get('filesize', 0) or 0
            note = f.get('format_note', '')
            protocol = f.get('protocol', '')
            vcodec = f.get('vcodec', '')
            acodec = f.get('acodec', '')
            fps = f.get('fps', 0)

            # 跳过纯音频（无视频轨道）
            if vcodec == 'none':
                continue

            width = f.get('width', 0) or 0
            height = f.get('height', 0) or 0
            if not resolution and width and height:
                resolution = f"{width}x{height}"

            # 归一元数据：空字符串、'?'、None 都视为"无备注"
            if not note or note == '?':
                note = ''
            # 标准化 vcodec：某些 extractor 会返回 'none' 字符串而非空
            if not vcodec or vcodec == 'none':
                vcodec = ''

            # 去重：yt-dlp 对 B站等 DASH 站点会返回相同分辨率+编码的多个条目
            # 例如 30280（视频-only）和 30280+30200（视频+音频）编码相同但 format_id 不同
            # key 包含 vcodec 以保留同分辨率不同编码的合法选项（avc1 vs hevc vs av1）
            # 不包含 fps：组合格式可能没有 fps 字段，会导致错误的非匹配
            dedup_key = (resolution, note, vcodec[:30])
            if dedup_key in seen_keys:
                # 已存在同 key 的条目，检查是否当前条目更优（带音频）
                if acodec and acodec != 'none':
                    # 当前带音频，替换之前的（可能是不带音频的 video-only 条目）
                    for j, existing in enumerate(info.formats):
                        existing_res = existing.get('resolution', '?')
                        existing_note = existing.get('note', '')
                        existing_codec = existing.get('codec', '').split('/')[0]
                        if (existing_res, existing_note, existing_codec[:30]) == dedup_key:
                            info.formats[j] = {
                                "id": fmt_id,
                                "ext": ext,
                                "resolution": resolution or '?',
                                "filesize": filesize,
                                "filesize_str": self._format_size(filesize),
                                "url": f.get('url', ''),
                                "note": note,
                                "fps": fps,
                                "is_fragmented": protocol in ('m3u8', 'm3u8_native', 'http_dash_segments'),
                                "codec": f"{vcodec}/{acodec}",
                            }
                            break
                # 否则跳过（当前不带音频且已有同 key 条目）
                continue

            seen_keys.add(dedup_key)
            info.formats.append({
                "id": fmt_id,
                "ext": ext,
                "resolution": resolution or '?',
                "filesize": filesize,
                "filesize_str": self._format_size(filesize),
                "url": f.get('url', ''),
                "note": note,
                "fps": fps,
                "is_fragmented": protocol in ('m3u8', 'm3u8_native', 'http_dash_segments'),
                "codec": f"{vcodec}/{acodec}",
            })

        if not info.formats:
            best_url = data.get('url', '')
            if best_url:
                info.formats.append({
                    "id": "best", "ext": "mp4",
                    "resolution": f"{data.get('width', '?')}x{data.get('height', '?')}",
                    "filesize": data.get('filesize', 0),
                    "filesize_str": self._format_size(data.get('filesize', 0)),
                    "url": best_url, "note": "最佳质量",
                    "is_fragmented": False, "codec": "auto",
                })

        # 按分辨率排序（高→低）
        def sort_key(f):
            try:
                h = int(f['resolution'].split('x')[1]) if 'x' in f['resolution'] else 0
                return -h
            except:
                return 0

        info.formats.sort(key=sort_key)

        self.log_callback(f"[探测] 发现 {len(info.formats)} 个可用格式")
        return info

    # ===================== 视频下载（yt-dlp CLI） =====================

    def download(self, url, format_id=None, output_dir=None, filename=None):
        """
        使用 yt-dlp 下载视频，HLS/DASH 自动合并为 MP4

        Returns: (success: bool, filepath: str)
        """
        self.reset()

        if output_dir:
            self.download_dir = output_dir
        os.makedirs(self.download_dir, exist_ok=True)

        # 使用指定文件名，或让 yt-dlp 自动命名
        if filename and self._sanitize_filename(filename):
            safe_name = self._sanitize_filename(filename)
            output_template = os.path.join(self.download_dir, f'{safe_name}.%(ext)s')
        else:
            output_template = os.path.join(self.download_dir, '%(title).100s.%(ext)s')
        progress_file = os.path.join(self.download_dir, '.progress.json')

        # 构建 yt-dlp 参数
        args = [
            '--newline',
            '--no-playlist',
            '--no-warnings',
            '--no-check-certificates',
            '--merge-output-format', 'mp4',
            '--output', output_template,
            '--print-to-file', 'after_move:filepath', progress_file,
            '--user-agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36',
            '-f', format_id or 'bestvideo+bestaudio/best',
        ]

        # 确保 ffmpeg 可用（已内置在 EXE 中，无需在线下载）
        ffmpeg_dir = self._find_ffmpeg()
        if ffmpeg_dir:
            args.extend(['--ffmpeg-location', ffmpeg_dir])

        # 如果有 cookies 文件，智能加载（按域名匹配）
        self._add_cookie_args(args, url)

        # 如果是直接分片流 URL，添加 referer 头并强制通用提取器
        # 也处理直链 mp4/flv/mkv 等，避免 yt-dlp 用域名提取器（如 CCTV extractor）报错
        direct_exts = ('.m3u8', '.mpd', '.mp4', '.flv', '.mkv', '.ts', '.webm', '.avi', '.mov')
        if any(ext in url.lower() for ext in direct_exts):
            parsed = urlparse(url)
            base_url = f"{parsed.scheme}://{parsed.netloc}"
            self.log_callback(f"[下载] 直链 URL，添加 Referer: {base_url}")
            self.log_callback(f"[下载] 使用通用提取器（避免域名专用提取器报错）")
            args.extend([
                '--force-generic-extractor',
                '--add-headers', f'Referer:{base_url}/',
                '--add-headers', 'User-Agent:Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            ])

        # 添加 URL 到末尾
        args.append(url)

        self.log_callback(f"[下载] 开始: {url}")
        self.log_callback(f"[下载] 格式: {format_id or 'bestvideo+bestaudio/best'}")
        self.log_callback(f"[下载] 输出: {self.download_dir}")
        if ffmpeg_dir:
            self.log_callback(f"[下载] ffmpeg: {ffmpeg_dir}")
        if os.path.exists(self._get_cookie_file()):
            self.log_callback("[下载] cookies: 已加载")

        ytdlp = self._get_ytdlp_path()
        if not os.path.exists(ytdlp):
            ytdlp, _ = self._ensure_ytdlp()
            if not ytdlp:
                self.log_callback("[下载] yt-dlp 未安装且自动下载失败")
                return False, ""
        self.current_process = subprocess.Popen(
            [ytdlp] + args,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding='utf-8',
            errors='replace',
            bufsize=1,
            creationflags=CREATE_NO_WINDOW,
        )

        # 实时读取输出，解析进度
        last_progress = ""
        all_output = []  # 收集所有输出，出错时显示
        filepath = ""
        try:
            for line in iter(self.current_process.stdout.readline, ''):
                if self.cancel_flag.is_set():
                    self.current_process.terminate()
                    self.log_callback("[下载] 用户取消")
                    return False, ""

                line = line.strip()
                if not line:
                    continue

                all_output.append(line)
                if len(all_output) > 50:  # 只保留最近 50 行
                    all_output.pop(0)

                # yt-dlp 进度格式: [download]  百分比 ...
                if '[download]' in line and '%' in line:
                    # 解析百分比、速度、ETA
                    try:
                        pct_match = re.search(r'(\d+\.?\d*)%', line)
                        if pct_match:
                            percent = float(pct_match.group(1))
                            speed = 0
                            eta = 0

                            # 速度
                            speed_match = re.search(r'at\s+([\d.]+\w+/s)', line)
                            if speed_match:
                                speed = self._parse_speed(speed_match.group(1))

                            # ETA
                            eta_match = re.search(r'ETA\s+(\S+)', line)
                            if eta_match:
                                eta = self._parse_eta(eta_match.group(1))

                            self.progress_callback(percent, speed, eta, line[:100])

                    except Exception:
                        pass

                elif '[Merger]' in line or '[ExtractAudio]' in line:
                    self.progress_callback(99, 0, 0, "合并中...")
                    self.log_callback(f"[处理] {line}")

                elif '[Merger]' in line:
                    self.progress_callback(99, 0, 0, "合并处理中...")
                    self.log_callback(f"[合并] {line}")

                last_progress = line

            self.current_process.wait()

            if self.current_process.returncode != 0:
                self.log_callback(f"[下载] yt-dlp 退出码: {self.current_process.returncode}")
                # 显示最后几行 yt-dlp 输出（通常是错误原因）
                error_lines = [l for l in all_output[-15:] if l.strip()]
                error_text = '\n'.join(error_lines).lower()
                if error_lines and not all('[download]' in l for l in error_lines):
                    self.log_callback("[下载] yt-dlp 错误详情:")
                    for err_line in error_lines:
                        self.log_callback(f"  {err_line}")

                # 如果是 m3u8 流下载失败，尝试直接下载（可能只需改参数）
                if '.m3u8' in url.lower() and 'hls' in str(all_output).lower():
                    self.log_callback("[提示] HLS 流下载失败，可能需在 URL 前加 referer 或 cookies")
                # SSL 错误：试试 Python 直链下载（绕过 yt-dlp 的 SSL 栈）
                if 'ssl:' in error_text or 'SSL:' in error_text:
                    direct_exts = ('.mp4', '.webm', '.mkv', '.flv', '.avi', '.mov', '.wmv', '.m4v', '.ts')
                    if any(ext in url.lower() for ext in direct_exts):
                        self.log_callback("[下载] yt-dlp SSL 错误，尝试 Python 直接下载...")
                        return self._direct_download(url, output_dir, filename)
                return False, ""

            # 查找下载的 mp4 文件
            if os.path.exists(progress_file):
                try:
                    with open(progress_file, 'r', encoding='utf-8') as f:
                        filepath = f.read().strip()
                    os.remove(progress_file)
                except:
                    pass

            if not filepath or not os.path.exists(filepath):
                # 搜索下载目录中最近创建的 mp4 文件
                mp4_files = sorted(
                    Path(self.download_dir).glob('*.mp4'),
                    key=lambda p: p.stat().st_mtime, reverse=True
                )
                if mp4_files:
                    filepath = str(mp4_files[0])

            if filepath and os.path.exists(filepath):
                size_mb = os.path.getsize(filepath) / (1024 * 1024)
                self.log_callback(f"[下载] 完成: {os.path.basename(filepath)} ({size_mb:.1f} MB)")
                self.progress_callback(100, 0, 0, "完成!")
                return True, filepath

            # yt-dlp 可能把音视频分开下载了（无 ffmpeg 时），手工合并
            merged = self._merge_if_split(self.download_dir)
            if merged:
                filepath = merged
                size_mb = os.path.getsize(filepath) / (1024 * 1024)
                self.log_callback(f"[合并] 完成: {os.path.basename(filepath)} ({size_mb:.1f} MB)")
                self.progress_callback(100, 0, 0, "完成!")
                return True, filepath

            self.log_callback("[下载] 未找到输出文件")
            return False, ""

        except Exception as e:
            self.log_callback(f"[下载] 异常: {e}")
            return False, ""

    def _retry_download(self, ytdlp, args, output_dir):
        """cookie 失败后重试下载（不带 cookie）"""
        self.log_callback("[重试] 正在重新启动下载...")
        self.current_process = subprocess.Popen(
            [ytdlp] + args,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding='utf-8',
            errors='replace',
            bufsize=1,
            creationflags=CREATE_NO_WINDOW,
        )
        # 重新读输出（简化版）
        filepath = ""
        try:
            for line in iter(self.current_process.stdout.readline, ''):
                if self.cancel_flag.is_set():
                    self.current_process.terminate()
                    self.log_callback("[下载] 用户取消")
                    return False, ""
                line = line.strip()
                if not line:
                    continue
                if '[download]' in line and '%' in line:
                    try:
                        pct_match = re.search(r'(\d+\.?\d*)%', line)
                        if pct_match:
                            percent = float(pct_match.group(1))
                            self.progress_callback(percent, 0, 0, line[:80])
                    except:
                        pass

            self.current_process.wait()
            if self.current_process.returncode != 0:
                self.log_callback(f"[重试] 退出码: {self.current_process.returncode}")
                return False, ""
        except Exception as e:
            self.log_callback(f"[重试] 异常: {e}")
            return False, ""

        # 查找输出文件
        progress_file = os.path.join(self.download_dir, '.progress.json')
        if os.path.exists(progress_file):
            try:
                with open(progress_file, 'r', encoding='utf-8') as f:
                    filepath = f.read().strip()
                os.remove(progress_file)
            except:
                pass
        if not filepath or not os.path.exists(filepath):
            mp4_files = sorted(
                Path(self.download_dir).glob('*.mp4'),
                key=lambda p: p.stat().st_mtime, reverse=True
            )
            if mp4_files:
                filepath = str(mp4_files[0])
        if filepath and os.path.exists(filepath):
            size_mb = os.path.getsize(filepath) / (1024 * 1024)
            self.log_callback(f"[下载] 完成: {os.path.basename(filepath)} ({size_mb:.1f} MB)")
            self.progress_callback(100, 0, 0, "完成!")
            return True, filepath
        return False, ""

    def _direct_download(self, url, output_dir=None, filename=None):
        """
        Python 直链下载（绕过 yt-dlp 的 SSL 栈）
        用于处理 yt-dlp SSL 错误（WRONG_VERSION_NUMBER 等）
        """
        import ssl
        import time

        if output_dir:
            self.download_dir = output_dir
        os.makedirs(self.download_dir, exist_ok=True)

        # 确定输出文件名和扩展名
        if filename:
            safe_name = self._sanitize_filename(filename)
        else:
            safe_name = url.split('/')[-1].split('?')[0] or 'video'
        # 从 URL 提取扩展名
        url_ext = os.path.splitext(urlparse(url).path)[1]
        if url_ext:
            safe_name = os.path.splitext(safe_name)[0] + url_ext

        if not os.path.splitext(safe_name)[1]:
            safe_name += '.mp4'
        output_path = os.path.join(self.download_dir, safe_name)

        self.log_callback(f"[直链] 下载: {url[:100]}")
        self.log_callback(f"[直链] 保存: {output_path}")

        # 构建多种 SSL 上下文尝试（从最宽松到最严格）
        def _make_ctx_legacy():
            """最宽松：允许旧版 SSL/TLS，不验证"""
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            ctx.set_ciphers('DEFAULT:@SECLEVEL=0')
            # Python 3.12+: 允许旧版服务端重协商
            if hasattr(ctx, 'minimum_version'):
                try:
                    ctx.minimum_version = ssl.TLSVersion.MINIMUM_SUPPORTED
                except Exception:
                    pass
            if hasattr(ctx, 'maximum_version'):
                try:
                    ctx.maximum_version = ssl.TLSVersion.MAXIMUM_SUPPORTED
                except Exception:
                    pass
            # OP_LEGACY_SERVER_CONNECT (0x4) - 允许不安全的旧版重协商
            try:
                ctx.options |= 0x4
            except Exception:
                pass
            return ctx

        # 尝试 1: requests + 最宽松 SSL（处理 Steam CDN 等特殊服务器）
        try:
            import requests
            from requests.adapters import HTTPAdapter
            from urllib3.poolmanager import PoolManager

            class LegacySSLAdapter(HTTPAdapter):
                def init_poolmanager(self, *args, **kwargs):
                    kwargs['ssl_context'] = _make_ctx_legacy()
                    return super().init_poolmanager(*args, **kwargs)

            session = requests.Session()
            session.mount('https://', LegacySSLAdapter())
            session.headers.update({
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36',
                'Referer': f"{urlparse(url).scheme}://{urlparse(url).netloc}/",
            })

            self.log_callback("[直链] 尝试 requests + 宽松 SSL...")
            resp = session.get(url, timeout=30, stream=True)
            resp.raise_for_status()

            content_type = resp.headers.get('Content-Type', '')
            if 'text/html' in content_type:
                self.log_callback(f"[直链] 服务器返回 HTML 而非视频 (Content-Type: {content_type})")
                resp.close()
                raise Exception("Not a video file (HTML response)")

            total = int(resp.headers.get('Content-Length', 0))
            if total > 0 and total < 50000:
                self.log_callback(f"[直链] 文件太小 ({total} bytes)，可能不是视频")
                resp.close()
                raise Exception(f"File too small ({total} bytes)")

            downloaded = 0
            start_time = time.time()
            with open(output_path, 'wb') as f:
                for chunk in resp.iter_content(chunk_size=65536):
                    if self.cancel_flag.is_set():
                        return False, ""
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total > 0:
                            pct = min(downloaded / total * 100, 99.9)
                            elapsed = time.time() - start_time
                            speed = downloaded / elapsed if elapsed > 0 else 0
                            eta = (total - downloaded) / speed if speed > 0 else 0
                            self.progress_callback(pct, speed, eta,
                                f"{self._format_size(downloaded)}/{self._format_size(total)}")
                        else:
                            self.progress_callback(50, 0, 0,
                                f"已下载 {self._format_size(downloaded)}")

            size_mb = os.path.getsize(output_path) / (1024 * 1024)
            if size_mb < 0.05:
                self.log_callback(f"[直链] 文件异常小 ({size_mb:.2f} MB)，删除")
                os.remove(output_path)
                raise Exception("File too small, likely not a video")

            self.log_callback(f"[直链] 完成: {os.path.basename(output_path)} ({size_mb:.1f} MB)")
            self.progress_callback(100, 0, 0, "完成!")
            return True, output_path

        except Exception as e:
            self.log_callback(f"[直链] requests 失败: {e}")

        # 尝试 2: HTTP 降级
        if url.startswith('https://'):
            http_url = url.replace('https://', 'http://', 1)
            self.log_callback(f"[直链] 尝试 HTTP 降级: {http_url[:100]}")
            try:
                import requests
                resp = requests.get(http_url, headers={
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                }, timeout=30, stream=True)
                resp.raise_for_status()

                content_type = resp.headers.get('Content-Type', '')
                if 'text/html' in content_type:
                    resp.close()
                    raise Exception("HTTP response is HTML, not video")

                total = int(resp.headers.get('Content-Length', 0))
                if total > 0 and total < 50000:
                    resp.close()
                    raise Exception(f"File too small ({total} bytes)")

                downloaded = 0
                with open(output_path, 'wb') as f:
                    for chunk in resp.iter_content(chunk_size=65536):
                        if self.cancel_flag.is_set():
                            return False, ""
                        if chunk:
                            f.write(chunk)
                            downloaded += len(chunk)
                            if total > 0:
                                pct = min(downloaded / total * 100, 99.9)
                                self.progress_callback(pct, 0, 0,
                                    f"{self._format_size(downloaded)}/{self._format_size(total)}")

                size_mb = os.path.getsize(output_path) / (1024 * 1024)
                if size_mb < 0.05:
                    os.remove(output_path)
                    raise Exception("File too small")

                self.log_callback(f"[直链] 完成: {os.path.basename(output_path)} ({size_mb:.1f} MB)")
                self.progress_callback(100, 0, 0, "完成!")
                return True, output_path
            except Exception as e:
                self.log_callback(f"[直链] HTTP 降级失败: {e}")

        # 尝试 3: subprocess curl (系统自带，SSL 兼容性最好)
        try:
            import subprocess
            curl_path = shutil.which('curl.exe') or shutil.which('curl')
            if curl_path:
                self.log_callback(f"[直链] 尝试 curl: {url[:100]}")
                cmd = [curl_path, '-L', '-k', '-o', output_path,
                       '-H', 'User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                       '-H', f'Referer: {urlparse(url).scheme}://{urlparse(url).netloc}/',
                       '--connect-timeout', '30', '--max-time', '600',
                       url]
                process = subprocess.run(cmd, capture_output=True, text=True, timeout=600,
                                        creationflags=0x08000000 if sys.platform == 'win32' else 0)
                if process.returncode == 0 and os.path.exists(output_path):
                    size_mb = os.path.getsize(output_path) / (1024 * 1024)
                    if size_mb > 1:
                        self.log_callback(f"[直链] curl 完成: {size_mb:.1f} MB")
                        self.progress_callback(100, 0, 0, "完成!")
                        return True, output_path
                    else:
                        os.remove(output_path)
                        self.log_callback(f"[直链] curl 下载文件过小 ({size_mb:.1f} MB)")
                else:
                    self.log_callback(f"[直链] curl 失败: {process.stderr[:200]}")
        except Exception as e:
            self.log_callback(f"[直链] curl 失败: {e}")

        # 清理空文件
        if os.path.exists(output_path) and os.path.getsize(output_path) < 50000:
            try:
                os.remove(output_path)
            except:
                pass
        return False, ""

    def _merge_if_split(self, directory):
        """
        yt-dlp 无 ffmpeg 时会把音视频分开下载。
        本方法检测并手工合并。
        Returns: 合并后的 mp4 路径，或 None
        """
        # 查找最近下载的 mp4（视频）和 m4a（音频）
        mp4_files = sorted(
            Path(directory).glob('*.mp4'), key=lambda p: p.stat().st_mtime, reverse=True
        )
        m4a_files = sorted(
            Path(directory).glob('*.m4a'), key=lambda p: p.stat().st_mtime, reverse=True
        )
        webm_files = sorted(
            Path(directory).glob('*.webm'), key=lambda p: p.stat().st_mtime, reverse=True
        )

        if not mp4_files:
            return None

        # yt-dlp 命名规律：title.f30032.mp4 + title.f30280.m4a  → 合并为 title.mp4
        video_file = mp4_files[0]
        base_name = video_file.stem  # 去掉 .mp4

        # 查找匹配的音频文件（同 base_name 或相近）
        audio_file = None
        for af in m4a_files + webm_files:
            af_stem = af.stem
            # 尝试匹配：如果视频是 xxx.f30032，音频可能是 xxx.f30280
            v_base = re.sub(r'\.f\d+$', '', base_name)
            a_base = re.sub(r'\.f\d+$', '', af_stem)
            if v_base == a_base:
                audio_file = af
                break
            # 也尝试直接同 stem（去掉 .fxxx）
            if base_name in af_stem or af_stem in base_name:
                audio_file = af
                break

        if not audio_file:
            # 没找到匹配的音频，按时间匹配（最后 2 分钟内）
            for af in m4a_files + webm_files:
                if abs(video_file.stat().st_mtime - af.stat().st_mtime) < 120:
                    audio_file = af
                    break

        if not audio_file:
            return None

        # 确保 ffmpeg 可用（已在 EXE 中内置）
        ffmpeg_dir = self._find_ffmpeg()
        if not ffmpeg_dir:
            self.log_callback("[合并] ffmpeg 不可用")
            return None

        ffmpeg_exe = os.path.join(ffmpeg_dir, 'ffmpeg.exe')

        # 输出文件名：去掉 .fxxx 后缀
        import re as _re
        clean_name = _re.sub(r'\.f\d+$', '', base_name)
        merged_path = os.path.join(directory, f'{clean_name}.mp4')

        # 如果已存在，换个名字
        if os.path.exists(merged_path):
            merged_path = os.path.join(directory, f'{clean_name}_merged.mp4')

        self.log_callback(f"[合并] 视频: {os.path.basename(str(video_file))}")
        self.log_callback(f"[合并] 音频: {os.path.basename(str(audio_file))}")
        self.log_callback(f"[合并] 输出: {os.path.basename(merged_path)}")

        try:
            result = subprocess.run(
                [ffmpeg_exe, '-y', '-i', str(video_file), '-i', str(audio_file),
                 '-c', 'copy', '-map', '0:v:0', '-map', '1:a:0',
                 '-shortest', merged_path],
                capture_output=True, text=True,
                timeout=300, creationflags=CREATE_NO_WINDOW,
            )
            if result.returncode == 0 and os.path.exists(merged_path):
                # 删除原始分离文件
                try:
                    os.remove(str(video_file))
                    os.remove(str(audio_file))
                except:
                    pass
                return merged_path
            else:
                self.log_callback(f"[合并] ffmpeg 失败: {result.stderr[:200]}")
                return None
        except Exception as e:
            self.log_callback(f"[合并] 异常: {e}")
            return None

    def smart_download(self, url, output_dir=None, filename=None, format_id=None):
        """
        完整流程：探测 → 下载 → 合并

        Args:
            url: 视频页面 URL
            output_dir: 下载保存目录
            filename: 指定文件名（不含扩展名），为空时用 yt-dlp 自动命名
            format_id: 用户选择的 yt-dlp 格式 ID，为 None 时用自动最佳

        Returns: (success, filepath, title)
        """
        self.log_callback("=" * 50)
        self.log_callback("视频下载器")
        self.log_callback(f"目标: {url}")
        self.log_callback("=" * 50)

        info = self.probe_with_ytdlp(url)

        if not info.formats:
            self.log_callback("[错误] 未发现可下载的视频格式")
            return False, "", ""

        self.log_callback(f"[选择] 标题: {info.title}")
        self.log_callback(f"[选择] 来源: {info.website}")

        best = info.formats[0]
        for fmt in info.formats:
            if fmt['ext'] == 'mp4':
                best = fmt
                break

        # 优先使用 yt-dlp 探测到的视频直链 URL
        # 避免重新解析页面（某些 CDN 有 SSL 兼容问题，直接下载更可靠）
        download_url = url  # 默认使用原始页面 URL
        direct_url = best.get('url', '')
        is_direct_stream = False
        if direct_url and (direct_url.startswith('https://') or direct_url.startswith('http://')):
            # yt-dlp 已成功提取了视频直链，直接用这个 URL 下载
            download_url = direct_url
            is_direct_stream = True
            self.log_callback(f"[选择] 使用探测到的直链下载")

        # 格式选择：
        # - yt-dlp 探测到的格式 ID：仅对页面 URL 传 -f（直链 URL 不用）
        # - 直链（深嗅或 yt-dlp 提取的）：不传 -f，让 yt-dlp 通用提取器自动处理
        if format_id and not is_direct_stream:
            fmt_id = f"{format_id}+bestaudio/best"
            self.log_callback(f"[选择] 用户选择格式: {format_id} + 自动合并最佳音频")
        elif format_id and is_direct_stream:
            fmt_id = None
            self.log_callback(f"[选择] 直链下载，不指定格式")
        else:
            fmt_id = None  # 页面 URL，使用 bestvideo+bestaudio/best 合并音视频

        # 使用嗅探到的视频标题作为文件名（优先使用传入的 filename）
        if filename:
            # 确保文件名合法
            safe_name = self._sanitize_filename(filename)
            if safe_name:
                download_filename = safe_name
            else:
                download_filename = None
        else:
            info_title = self._sanitize_filename(info.title)
            download_filename = info_title if info_title and info_title not in ('unknown', 'video') else None

        self.log_callback(f"[选择] 最佳格式: {best['resolution']} {best['ext']} "
                         f"({best.get('filesize_str', '未知')})")
        self.log_callback(f"[选择] 下载地址: {download_url[:80]}...")
        if download_filename:
            self.log_callback(f"[选择] 文件名: {download_filename}.mp4")

        success, filepath = self.download(download_url, fmt_id, output_dir, download_filename)
        return success, filepath, info.title

    # ===================== 工具方法 =====================

    @staticmethod
    def _sanitize_filename(name):
        return re.sub(r'[\\/:*?"<>|]', '_', name).strip()[:200]

    @staticmethod
    def _format_size(size_bytes):
        if size_bytes == 0:
            return "未知"
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size_bytes < 1024:
                return f"{size_bytes:.1f} {unit}"
            size_bytes /= 1024
        return f"{size_bytes:.1f} TB"

    @staticmethod
    def _parse_speed(speed_str):
        """解析速度字符串如 '5.2MiB/s'"""
        try:
            num = float(re.search(r'[\d.]+', speed_str).group())
            if 'Gi' in speed_str:
                num *= 1024 * 1024 * 1024
            elif 'Mi' in speed_str:
                num *= 1024 * 1024
            elif 'Ki' in speed_str:
                num *= 1024
            return num
        except:
            return 0

    @staticmethod
    def _parse_eta(eta_str):
        """解析 ETA 字符串如 '1:30'"""
        try:
            parts = eta_str.split(':')
            if len(parts) == 2:
                return int(parts[0]) * 60 + int(parts[1])
            elif len(parts) == 3:
                return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
            return 0
        except:
            return 0

    @staticmethod
    def _guess_title(url):
        parsed = urlparse(url)
        path = parsed.path.strip('/')
        if path:
            parts = path.split('/')
            name = parts[-1].split('?')[0].split('.')[0]
            if name and len(name) > 2:
                return DownloadEngine._sanitize_filename(name)
        return f"video_{int(time.time())}"
