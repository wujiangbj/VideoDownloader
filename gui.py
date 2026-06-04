"""
视频下载器 GUI - Windows 应用程序

- URL 输入 → 自动嗅探 → 格式选择 → 下载合并
- 现代化 tkinter 界面
"""

import os
import sys
import json
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path

# 添加当前目录到 path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from engine import DownloadEngine


class VideoDownloaderApp:
    """视频下载器主窗口"""

    APP_TITLE = "视频下载器"
    APP_VERSION = "1.0.0"
    APP_SIZE = "900x650"

    @staticmethod
    def _get_user_dir():
        """获取 EXE 所在目录（与 engine 一致，存 cookies 和 config）"""
        if getattr(sys, 'frozen', False):
            return os.path.dirname(sys.executable)
        return os.path.dirname(os.path.abspath(__file__))

    @staticmethod
    def _get_config_path():
        return os.path.join(VideoDownloaderApp._get_user_dir(), 'config.json')

    @staticmethod
    def _load_config():
        """加载持久化配置"""
        cf = VideoDownloaderApp._get_config_path()
        if os.path.exists(cf):
            try:
                with open(cf, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except:
                pass
        return {}

    @staticmethod
    def _save_config(data):
        """保存持久化配置"""
        cf = VideoDownloaderApp._get_config_path()
        try:
            with open(cf, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except:
            pass

    def __init__(self):
        self.root = tk.Tk()
        self.root.title(self.APP_TITLE)
        self.root.geometry(self.APP_SIZE)
        self.root.minsize(700, 500)

        # 设置图标（如果有的话）
        icon_path = os.path.join(os.path.dirname(__file__), 'icon.ico')
        if os.path.exists(icon_path):
            self.root.iconbitmap(icon_path)

        # 下载引擎
        self.engine = DownloadEngine(
            progress_callback=self._on_progress,
            log_callback=self._on_log
        )

        # 当前选中的格式
        self.current_formats = []
        self.current_info = None

        # 加载持久化配置（保存目录 + 浏览器选择）
        config = self._load_config()
        saved_dir = config.get('output_dir', '')
        if saved_dir and os.path.isdir(saved_dir):
            default_dir = saved_dir
        else:
            default_dir = str(Path.home() / "Downloads" / "VideoDownloader")

        # 浏览器选择
        self.browser_options = {
            'auto': '🖥️ 自动检测',
            'chrome': '🌐 Chrome',
            'msedge': '🌐 Edge',
        }
        saved_browser = config.get('browser', 'auto')
        if saved_browser not in self.browser_options:
            saved_browser = 'auto'
        self.browser_var = tk.StringVar(value=saved_browser)

        # 默认下载目录（必须在 _build_ui 前定义）
        self.output_dir = tk.StringVar(value=default_dir)

        # 构建界面
        self._build_ui()

    def _build_ui(self):
        """构建界面"""
        # 样式配置
        style = ttk.Style()
        style.theme_use('clam')

        # 颜色主题
        bg_color = '#f0f4f8'
        accent_color = '#4a90d9'
        text_color = '#2c3e50'
        btn_bg = '#4a90d9'
        btn_fg = '#ffffff'

        self.root.configure(bg=bg_color)

        # 主容器
        main_frame = tk.Frame(self.root, bg=bg_color, padx=20, pady=15)
        main_frame.pack(fill=tk.BOTH, expand=True)

        # ======== 标题栏 ========
        title_frame = tk.Frame(main_frame, bg=bg_color)
        title_frame.pack(fill=tk.X, pady=(0, 10))

        tk.Label(
            title_frame,
            text="🎬 视频下载器",
            font=("Microsoft YaHei", 18, "bold"),
            fg=text_color,
            bg=bg_color,
        ).pack(side=tk.LEFT)

        tk.Label(
            title_frame,
            text=f"v{self.APP_VERSION} | Prod. by WuJiang",
            font=("Microsoft YaHei", 9),
            fg='#95a5a6',
            bg=bg_color,
        ).pack(side=tk.LEFT, padx=(10, 0), pady=(5, 0))

        # ======== URL 输入区 ========
        url_frame = tk.LabelFrame(
            main_frame, text="视频 URL", font=("Microsoft YaHei", 10, "bold"),
            bg=bg_color, fg=text_color, padx=10, pady=10
        )
        url_frame.pack(fill=tk.X, pady=(0, 10))

        url_input_frame = tk.Frame(url_frame, bg=bg_color)
        url_input_frame.pack(fill=tk.X)

        self.url_entry = tk.Entry(
            url_input_frame,
            font=("Consolas", 11),
            bg='#ffffff',
            fg=text_color,
            insertbackground=text_color,
            relief=tk.FLAT,
            bd=2,
        )
        self.url_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=5)
        self.url_entry.bind('<Return>', lambda e: self._on_sniff())
        self.url_entry.insert(0, '')

        button_frame = tk.Frame(url_input_frame, bg=bg_color)
        button_frame.pack(side=tk.RIGHT, padx=(10, 0))

        # 浏览器选择
        browser_label = tk.Label(
            button_frame, text="浏览器:", font=("Microsoft YaHei", 9),
            bg=bg_color, fg=text_color
        )
        browser_label.pack(side=tk.LEFT, padx=(0, 3))

        self.browser_combo = ttk.Combobox(
            button_frame,
            textvariable=self.browser_var,
            values=list(self.browser_options.keys()),
            state='readonly',
            width=8,
            font=("Microsoft YaHei", 9),
        )
        self.browser_combo.pack(side=tk.LEFT, padx=(0, 8))
        # 浏览器切换时自动保存
        self.browser_var.trace_add('write', lambda *a: self._save_config({
            'output_dir': self.output_dir.get(),
            'browser': self.browser_var.get(),
        }))

        self.sniff_btn = tk.Button(
            button_frame,
            text="🔍 嗅探视频",
            font=("Microsoft YaHei", 10, "bold"),
            bg=btn_bg,
            fg=btn_fg,
            relief=tk.FLAT,
            padx=15,
            pady=5,
            cursor='hand2',
            command=self._on_sniff,
        )
        self.sniff_btn.pack(side=tk.LEFT, padx=(0, 5))

        self.paste_btn = tk.Button(
            button_frame,
            text="📋 粘贴",
            font=("Microsoft YaHei", 9),
            bg='#95a5a6',
            fg='#ffffff',
            relief=tk.FLAT,
            padx=10,
            pady=5,
            cursor='hand2',
            command=self._on_paste,
        )
        self.paste_btn.pack(side=tk.LEFT)

        # ======== 格式选择区 ========
        format_frame = tk.LabelFrame(
            main_frame, text="可用格式", font=("Microsoft YaHei", 10, "bold"),
            bg=bg_color, fg=text_color, padx=10, pady=10
        )
        format_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 10))

        # Treeview 显示格式列表
        columns = ('resolution', 'ext', 'size', 'codec', 'note')
        self.format_tree = ttk.Treeview(
            format_frame,
            columns=columns,
            show='headings',
            height=6,
            selectmode='browse',
        )

        self.format_tree.heading('resolution', text='分辨率')
        self.format_tree.heading('ext', text='格式')
        self.format_tree.heading('size', text='大小')
        self.format_tree.heading('codec', text='编码')
        self.format_tree.heading('note', text='备注')

        self.format_tree.column('resolution', width=110, anchor='center')
        self.format_tree.column('ext', width=60, anchor='center')
        self.format_tree.column('size', width=90, anchor='center')
        self.format_tree.column('codec', width=140, anchor='center')
        self.format_tree.column('note', width=200)

        format_scroll = ttk.Scrollbar(format_frame, orient=tk.VERTICAL, command=self.format_tree.yview)
        self.format_tree.configure(yscrollcommand=format_scroll.set)

        self.format_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        format_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        # ======== 选项区 ========
        options_frame = tk.Frame(main_frame, bg=bg_color)
        options_frame.pack(fill=tk.X, pady=(0, 10))

        # 输出目录
        tk.Label(
            options_frame, text="保存到:", font=("Microsoft YaHei", 9),
            bg=bg_color, fg=text_color
        ).pack(side=tk.LEFT)

        dir_entry = tk.Entry(
            options_frame,
            textvariable=self.output_dir,
            font=("Consolas", 9),
            bg='#ffffff',
            fg=text_color,
            relief=tk.FLAT,
            bd=1,
            width=50,
        )
        dir_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(5, 5))

        browse_btn = tk.Button(
            options_frame,
            text="📁 浏览",
            font=("Microsoft YaHei", 9),
            bg='#ecf0f1',
            fg=text_color,
            relief=tk.FLAT,
            padx=10,
            cursor='hand2',
            command=self._on_browse_dir,
        )
        browse_btn.pack(side=tk.LEFT)

        # ======== 下载按钮 ========
        action_frame = tk.Frame(main_frame, bg=bg_color)
        action_frame.pack(fill=tk.X, pady=(0, 10))

        self.download_btn = tk.Button(
            action_frame,
            text="⬇️  开始下载",
            font=("Microsoft YaHei", 12, "bold"),
            bg='#27ae60',
            fg='#ffffff',
            relief=tk.FLAT,
            padx=30,
            pady=8,
            cursor='hand2',
            command=self._on_download,
        )
        self.download_btn.pack(side=tk.LEFT, padx=(0, 10))

        self.login_btn = tk.Button(
            action_frame,
            text="🔑 浏览器登录",
            font=("Microsoft YaHei", 10),
            bg='#f39c12',
            fg='#ffffff',
            relief=tk.FLAT,
            padx=15,
            pady=5,
            cursor='hand2',
            command=self._on_login,
        )
        self.login_btn.pack(side=tk.LEFT, padx=(0, 10))

        self.capture_btn = tk.Button(
            action_frame,
            text="📡 缓存采集",
            font=("Microsoft YaHei", 10),
            bg='#8e44ad',
            fg='#ffffff',
            relief=tk.FLAT,
            padx=15,
            pady=5,
            cursor='hand2',
            command=self._on_cache_capture,
        )
        self.capture_btn.pack(side=tk.LEFT, padx=(0, 10))

        self.cancel_btn = tk.Button(
            action_frame,
            text="⏹ 取消",
            font=("Microsoft YaHei", 10),
            bg='#e74c3c',
            fg='#ffffff',
            relief=tk.FLAT,
            padx=20,
            pady=5,
            cursor='hand2',
            state=tk.DISABLED,
            command=self._on_cancel,
        )
        self.cancel_btn.pack(side=tk.LEFT)

        self.open_btn = tk.Button(
            action_frame,
            text="📂 打开下载目录",
            font=("Microsoft YaHei", 10),
            bg='#ecf0f1',
            fg=text_color,
            relief=tk.FLAT,
            padx=20,
            pady=5,
            cursor='hand2',
            command=self._on_open_dir,
        )
        self.open_btn.pack(side=tk.RIGHT)

        # ======== 进度条 ========
        progress_frame = tk.Frame(main_frame, bg=bg_color)
        progress_frame.pack(fill=tk.X, pady=(0, 5))

        self.progress_var = tk.DoubleVar(value=0)
        self.progress_bar = ttk.Progressbar(
            progress_frame,
            variable=self.progress_var,
            maximum=100,
            mode='determinate',
        )
        self.progress_bar.pack(fill=tk.X, side=tk.LEFT, expand=True, padx=(0, 10))

        self.progress_label = tk.Label(
            progress_frame,
            text="就绪",
            font=("Microsoft YaHei", 9),
            bg=bg_color,
            fg='#7f8c8d',
            width=20,
            anchor=tk.W,
        )
        self.progress_label.pack(side=tk.RIGHT)

        # ======== 日志区 ========
        log_frame = tk.LabelFrame(
            main_frame, text="日志", font=("Microsoft YaHei", 10, "bold"),
            bg=bg_color, fg=text_color, padx=10, pady=10
        )
        log_frame.pack(fill=tk.BOTH, expand=True)

        self.log_text = tk.Text(
            log_frame,
            font=("Consolas", 9),
            bg='#1e272e',
            fg='#d2dae2',
            insertbackground='#ffffff',
            relief=tk.FLAT,
            state=tk.DISABLED,
            wrap=tk.WORD,
            height=8,
        )
        log_scroll = ttk.Scrollbar(log_frame, orient=tk.VERTICAL, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_scroll.set)

        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        log_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        # 初始日志
        self._log_message("VideoDownloader 启动")
        self._log_message("支持: 页面视频嗅探 / HLS(m3u8) / DASH(mpd) / 直链下载")
        self._log_message("基于 yt-dlp 引擎，支持 1000+ 网站")
        self._log_message("准备就绪，请输入视频 URL 开始嗅探")

    # ===================== 事件处理 =====================

    def _on_sniff(self):
        """嗅探按钮点击"""
        url = self.url_entry.get().strip()
        if not url:
            messagebox.showwarning("提示", "请输入视频 URL")
            return

        if not url.startswith('http'):
            url = 'https://' + url
            self.url_entry.delete(0, tk.END)
            self.url_entry.insert(0, url)

        # 清空旧数据
        for item in self.format_tree.get_children():
            self.format_tree.delete(item)
        self.current_formats = []
        self._clear_log()

        self.sniff_btn.configure(state=tk.DISABLED, text="⏳ 分析中...")
        self.download_btn.configure(state=tk.DISABLED)
        self.cancel_btn.configure(state=tk.DISABLED)

        def _run():
            # 获取用户选择的浏览器
            browser_choice = self.browser_var.get()
            if browser_choice == 'auto':
                browser_override = None
            else:
                browser_override = browser_choice

            info = self.engine.probe_with_ytdlp(url)
            self.current_info = info
            self.current_formats = info.formats

            if info.formats:
                self.root.after(0, self._update_format_list, info)
                self.root.after(0, lambda: self._log_message(
                    f"\n✅ 发现 {len(info.formats)} 个视频格式 | 标题: {info.title}"
                ))
            else:
                self.root.after(0, lambda: self._log_message(
                    "\n❌ 标准探测未发现视频"
                ))
                self.root.after(0, lambda: self._log_message(
                    "\n💡 尝试浏览器深度嗅探（JS 动态页面）..."
                ))
                # 尝试深度嗅探（传入浏览器选择）
                deep_info = self.engine.deep_sniff(url, browser_override=browser_override)
                if deep_info.formats:
                    self.current_info = deep_info
                    self.current_formats = deep_info.formats
                    self.root.after(0, self._update_format_list, deep_info)
                    self.root.after(0, lambda: self._log_message(
                        f"\n✅ 深度嗅探发现视频! | 标题: {deep_info.title}"
                    ))
                else:
                    self.root.after(0, lambda: self._log_message(
                        "\n❌ 深度嗅探也未发现视频"
                    ))
                    self.root.after(0, lambda: self._log_message(
                        "\n💡 提示: 可尝试安装浏览器嗅探引擎（运行 安装依赖.bat）"
                    ))

            self.root.after(0, self._reset_buttons)

        threading.Thread(target=_run, daemon=True).start()

    def _update_format_list(self, info):
        """更新格式列表"""
        for i, fmt in enumerate(info.formats):
            self.format_tree.insert('', tk.END, iid=str(i), values=(
                fmt.get('resolution', '?'),
                fmt.get('ext', 'mp4'),
                fmt.get('filesize_str', '?'),
                fmt.get('codec', '?'),
                fmt.get('note', ''),
            ))

        if info.formats:
            self.format_tree.selection_set('0')
            self.download_btn.configure(state=tk.NORMAL)

    def _on_download(self):
        """下载按钮点击"""
        if not self.current_formats:
            messagebox.showwarning("提示", "请先嗅探视频")
            return

        selection = self.format_tree.selection()
        if not selection:
            messagebox.showwarning("提示", "请选择一个视频格式")
            return

        idx = int(selection[0])
        if idx >= len(self.current_formats):
            return

        fmt = self.current_formats[idx]
        url = self.url_entry.get().strip()

        self.download_btn.configure(state=tk.DISABLED)
        self.cancel_btn.configure(state=tk.NORMAL)
        self.sniff_btn.configure(state=tk.DISABLED)
        self.progress_var.set(0)
        self.progress_label.configure(text="准备下载...")

        def _run():
            # 传入嗅探到的视频标题作为文件名
            video_title = self.current_info.title if self.current_info else None
            # 传入用户选择的格式 ID（如果选择了）
            selected_fmt_id = fmt.get('id')
            success, filepath, title = self.engine.smart_download(
                url,
                output_dir=self.output_dir.get(),
                filename=video_title,
                format_id=selected_fmt_id,
            )

            self.root.after(0, lambda: self._on_download_done(success, filepath, title))

        threading.Thread(target=_run, daemon=True).start()

    def _on_download_done(self, success, filepath, title):
        """下载完成处理"""
        self.cancel_btn.configure(state=tk.DISABLED)
        self._reset_buttons()

        if success:
            self.progress_label.configure(text=f"✅ 完成: {os.path.basename(filepath)}")
            self._log_message(f"\n{'='*50}")
            self._log_message(f"✅ 下载成功!")
            self._log_message(f"📁 文件: {filepath}")
            self._log_message(f"📏 大小: {self.engine._format_size(os.path.getsize(filepath))}")

            # 询问是否打开
            if messagebox.askyesno("下载完成", f"视频下载完成!\n\n{os.path.basename(filepath)}\n\n是否打开文件所在目录?"):
                os.startfile(os.path.dirname(filepath))
        else:
            self.progress_label.configure(text="❌ 下载失败")
            self._log_message("\n❌ 下载失败，请查看日志")

    def _on_cancel(self):
        """取消下载"""
        self.engine.cancel()
        self.cancel_btn.configure(state=tk.DISABLED)
        self._log_message("\n⏹ 正在取消下载...")

    def _on_login(self):
        """浏览器登录按钮"""
        self.login_btn.configure(state=tk.DISABLED, text="⏳ 登录中...")
        self.sniff_btn.configure(state=tk.DISABLED)
        self.download_btn.configure(state=tk.DISABLED)
        self._log_message("\n🔑 正在打开浏览器登录窗口...")

        def _run():
            success = self.engine.login_with_browser()
            self.root.after(0, lambda: self._on_login_done(success))

        threading.Thread(target=_run, daemon=True).start()

    def _on_login_done(self, success):
        """登录完成"""
        self._reset_buttons()
        self.login_btn.configure(state=tk.NORMAL, text="🔑 浏览器登录")
        if success:
            self._log_message("\n✅ 登录成功！现在可以下载高画质视频了")
            messagebox.showinfo("登录成功", "已获取 B 站 cookies！\n\n现在可以下载 1080p 等高画质视频了。")
        else:
            self._log_message("\n⚠️ 登录未完成或未获取到 cookie")

    def _on_cache_capture(self):
        """缓存采集按钮"""
        url = self.url_entry.get().strip()
        if not url:
            messagebox.showwarning("提示", "请输入视频 URL")
            return
        if not url.startswith('http'):
            url = 'https://' + url

        self._clear_log()
        self._log_message(f"📡 缓存采集: {url}")
        self._log_message("浏览器将打开，请在页面中播放视频后关闭窗口")
        self._log_message("软件会自动拦截并保存视频数据\n")

        self.sniff_btn.configure(state=tk.DISABLED)
        self.download_btn.configure(state=tk.DISABLED)
        self.capture_btn.configure(state=tk.DISABLED, text="📡 采集中...")
        self.cancel_btn.configure(state=tk.NORMAL)

        def _run():
            success, filepath, title = self.engine.cache_capture(
                url, output_dir=self.output_dir.get())
            self.root.after(0, lambda: self._on_capture_done(success, filepath, title))

        threading.Thread(target=_run, daemon=True).start()

    def _on_capture_done(self, success, filepath, title):
        """缓存采集完成"""
        self._reset_buttons()

        if success:
            self.progress_label.configure(text=f"✅ 采集完成: {os.path.basename(filepath)}")
            self._log_message(f"\n✅ 采集成功!")
            self._log_message(f"📁 文件: {filepath}")
            self._log_message(f"📏 大小: {self.engine._format_size(os.path.getsize(filepath))}")
            if messagebox.askyesno("采集完成", f"视频采集完成!\n\n{os.path.basename(filepath)}\n\n是否打开文件所在目录?"):
                os.startfile(os.path.dirname(filepath))
        else:
            self.progress_label.configure(text="❌ 采集失败")
            self._log_message("\n❌ 采集失败，请查看日志")

    def _on_paste(self):
        """粘贴剪贴板内容"""
        try:
            text = self.root.clipboard_get()
            if text.strip():
                self.url_entry.delete(0, tk.END)
                self.url_entry.insert(0, text.strip())
        except:
            pass

    def _on_browse_dir(self):
        """浏览输出目录"""
        path = filedialog.askdirectory(
            title="选择下载保存目录",
            initialdir=self.output_dir.get(),
        )
        if path:
            self.output_dir.set(path)
            self.engine.set_download_dir(path)
            # 持久化保存目录和浏览器选择
            self._save_config({
                'output_dir': path,
                'browser': self.browser_var.get(),
            })

    def _on_open_dir(self):
        """打开下载目录"""
        path = self.output_dir.get()
        os.makedirs(path, exist_ok=True)
        os.startfile(path)

    # ===================== 回调处理 =====================

    def _on_progress(self, percent, speed, eta, status):
        """进度回调（在后台线程调用）"""
        self.root.after(0, self._update_progress, percent, speed, eta, status)

    def _update_progress(self, percent, speed, eta, status):
        """更新进度条（主线程）"""
        self.progress_var.set(percent)

        if speed > 0:
            speed_str = self.engine._format_size(speed) + '/s'
        else:
            speed_str = ''

        if eta > 0:
            eta_str = f"剩余 {int(eta)}秒"
        elif eta == 0 and percent < 100:
            eta_str = "计算中..."
        else:
            eta_str = ""

        parts = [status]
        if speed_str:
            parts.append(speed_str)
        if eta_str:
            parts.append(eta_str)

        self.progress_label.configure(text=" | ".join(parts))

    def _on_log(self, message):
        """日志回调"""
        self.root.after(0, self._log_message, message)

    def _log_message(self, message):
        """写日志到界面"""
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, message + '\n')
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)

    def _clear_log(self):
        """清空日志"""
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.delete('1.0', tk.END)
        self.log_text.configure(state=tk.DISABLED)

    def _reset_buttons(self):
        """恢复按钮状态"""
        self.sniff_btn.configure(state=tk.NORMAL, text="🔍 嗅探视频")
        self.capture_btn.configure(state=tk.NORMAL, text="📡 缓存采集")
        self.cancel_btn.configure(state=tk.DISABLED)
        if self.current_formats:
            self.download_btn.configure(state=tk.NORMAL)
        else:
            self.download_btn.configure(state=tk.DISABLED)

    # ===================== 运行 =====================

    def run(self):
        """启动应用"""
        self.root.mainloop()


def main():
    app = VideoDownloaderApp()
    app.run()


if __name__ == '__main__':
    main()
