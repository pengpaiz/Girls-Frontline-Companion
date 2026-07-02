"""设置界面 — CustomTkinter 现代化 UI"""

import os
import sys
import customtkinter as ctk
from config import load, save, CONFIG_DIR


class SettingsWindow(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("AuraPet 设置")
        self.resizable(False, False)

        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        # 窗口尺寸
        w, h = 480, 420
        self.update_idletasks()
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        x = (sw - w) // 2
        y = (sh - h) // 2
        self.geometry(f"{w}x{h}+{x}+{y}")

        self.cfg = load()
        self.after_save = None

        self._build_ui()

    def _build_ui(self):
        # 标题
        self.title_label = ctk.CTkLabel(
            self, text="🐾 AuraPet 设置",
            font=ctk.CTkFont(size=20, weight="bold")
        )
        self.title_label.pack(pady=(20, 10))

        # 开机自启
        self.auto_start_var = ctk.BooleanVar(value=self.cfg.get('auto_start', False))
        self.auto_start_cb = ctk.CTkCheckBox(
            self, text="开机自动启动",
            variable=self.auto_start_var,
            font=ctk.CTkFont(size=14)
        )
        self.auto_start_cb.pack(pady=(10, 5), padx=40, anchor="w")

        # 置顶
        self.always_on_top_var = ctk.BooleanVar(value=self.cfg.get('always_on_top', True))
        self.always_on_top_cb = ctk.CTkCheckBox(
            self, text="角色始终置顶",
            variable=self.always_on_top_var,
            font=ctk.CTkFont(size=14)
        )
        self.always_on_top_cb.pack(pady=5, padx=40, anchor="w")

        # 角色选择下拉框
        self.char_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.char_frame.pack(pady=(15, 5), padx=40, fill="x")

        self.char_label = ctk.CTkLabel(
            self.char_frame, text="桌面角色:",
            font=ctk.CTkFont(size=13)
        )
        self.char_label.pack(side="left")

        self.char_dirs = self._scan_characters()
        current_char = self.cfg.get('character', 'ar15')
        self.char_var = ctk.StringVar(value=current_char)
        self.char_menu = ctk.CTkOptionMenu(
            self.char_frame,
            values=self.char_dirs,
            variable=self.char_var,
            font=ctk.CTkFont(size=13),
            width=180
        )
        self.char_menu.pack(side="right")

        if not self.char_dirs:
            self.char_menu.configure(state="disabled")
            self.char_label.configure(text="桌面角色:（无角色文件夹）")

        # 保存按钮
        self.save_btn = ctk.CTkButton(
            self, text="💾 保存设置",
            command=self._on_save,
            font=ctk.CTkFont(size=14),
            height=36
        )
        self.save_btn.pack(pady=(20, 5))

        # 关闭按钮
        self.close_btn = ctk.CTkButton(
            self, text="✕ 关闭",
            command=self._on_close,
            fg_color="transparent",
            border_width=1,
            font=ctk.CTkFont(size=13),
            height=30
        )
        self.close_btn.pack(pady=5)

    def _scan_characters(self):
        """扫描 data/ 下所有子文件夹作为可选角色（必须有 wait/ 子目录且含 png）"""
        data_dir = os.path.join(CONFIG_DIR, 'data')
        if not os.path.isdir(data_dir):
            return []
        dirs = []
        for name in sorted(os.listdir(data_dir)):
            full = os.path.join(data_dir, name)
            if os.path.isdir(full):
                wait_dir = os.path.join(full, 'wait')
                if os.path.isdir(wait_dir):
                    has_png = any(
                        f.lower().endswith('.png')
                        for f in os.listdir(wait_dir)
                    )
                    if has_png:
                        dirs.append(name)
        return dirs

    def _on_save(self):
        self.cfg['auto_start'] = self.auto_start_var.get()
        self.cfg['always_on_top'] = self.always_on_top_var.get()
        self.cfg['character'] = self.char_var.get()

        save(self.cfg)
        self._apply_auto_start()

        if self.after_save:
            self.after_save(self.cfg)

        self.save_btn.configure(text="✓ 已保存!", fg_color="#2e7d32")
        self.after(1500, lambda: self.save_btn.configure(
            text="💾 保存设置", fg_color=("#3B8ED0", "#1F6AA5")
        ))

    def _apply_auto_start(self):
        """写入 / 删除 Windows 开机启动项

        方案：VBS 静默脚本放 CONFIG_DIR，Startup 文件夹只放 .lnk 指向 VBS。
        始终清理注册表旧条目，避免重复自启动。
        """
        import platform
        if platform.system() != 'Windows':
            return
        import winreg

        REG_KEY = r'Software\Microsoft\Windows\CurrentVersion\Run'
        REG_NAME = 'AuraPet'

        startup_dir = os.path.join(
            os.getenv('APPDATA', ''),
            r'Microsoft\Windows\Start Menu\Programs\Startup'
        )

        # ── 1. 清理注册表旧条目（防止与快捷方式重复） ───────
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_KEY,
                                 access=winreg.KEY_WRITE)
            try:
                winreg.DeleteValue(key, REG_NAME)
            except FileNotFoundError:
                pass
            winreg.CloseKey(key)
        except OSError:
            pass

        # ── 2. 清理 Startup 文件夹内历史 VBS/bat 残留 ────────
        if os.path.isdir(startup_dir):
            for fname in os.listdir(startup_dir):
                if fname.lower() in ('aurapet.vbs', 'aurapet.bat'):
                    try:
                        os.remove(os.path.join(startup_dir, fname))
                    except OSError:
                        pass

        if not self.cfg['auto_start']:
            # 关闭自启动：删除快捷方式
            try:
                lnk = os.path.join(startup_dir, 'AuraPet.lnk')
                if os.path.isfile(lnk):
                    os.remove(lnk)
            except OSError:
                pass
            return

        # ── 3. 在 CONFIG_DIR 写入 VBS 静默启动脚本 ────────
        exe_path = os.path.abspath(self._get_exe_path())
        vbs_path = os.path.join(CONFIG_DIR, 'AuraPet.vbs')
        try:
            with open(vbs_path, 'w', encoding='utf-8') as f:
                f.write(
                    'WScript.Sleep 5000\n'
                    'CreateObject("WScript.Shell").Run """{}""", 0, False'.format(exe_path))
        except OSError:
            return

        # ── 4. 在 Startup 文件夹创建指向 VBS 的快捷方式 ────
        lnk_path = os.path.join(startup_dir, 'AuraPet.lnk')
        try:
            import win32com.client
            shell = win32com.client.Dispatch('WScript.Shell')
            sc = shell.CreateShortCut(lnk_path)
            sc.TargetPath = vbs_path
            sc.WorkingDirectory = CONFIG_DIR
            sc.WindowStyle = 7  # 最小化启动 WScript
            sc.save()
        except Exception:
            pass

    def _get_exe_path(self):
        if getattr(sys, 'frozen', False):
            return os.path.abspath(sys.executable)
        else:
            # 开发环境：指向项目 AuraPet.exe（打包后才有意义）
            # 如果没打包，则指向 python.exe 启动脚本
            exe = os.path.join(CONFIG_DIR, 'AuraPet.exe')
            if os.path.isfile(exe):
                return exe
            # 回退：用 python 启动 main.py
            return os.path.abspath(sys.argv[0])

    def _on_close(self):
        self.destroy()

    def set_on_save(self, callback):
        self.after_save = callback