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
        """写入 / 删除 Windows 开机启动项（.vbs 脚本，完全静默）"""
        import platform
        if platform.system() != 'Windows':
            return

        startup_dir = os.path.join(
            os.getenv('APPDATA', ''),
            r'Microsoft\Windows\Start Menu\Programs\Startup'
        )
        vbs_path = os.path.join(startup_dir, 'AuraPet.vbs')

        # 清理所有历史残留（.bat / .lnk）
        for fname in ('AuraPet.bat', 'AuraPet.lnk'):
            fp = os.path.join(startup_dir, fname)
            try:
                if os.path.isfile(fp):
                    os.remove(fp)
            except OSError:
                pass

        if self.cfg['auto_start']:
            exe_path = self._get_exe_path()
            # VBS 脚本：完全静默，无控制台窗口
            vbs_content = (
                'CreateObject("WScript.Shell").Run '
                '"""{}""", 0, False'
            ).format(exe_path)
            try:
                with open(vbs_path, 'w', encoding='utf-8') as f:
                    f.write(vbs_content)
            except OSError:
                pass
        else:
            try:
                if os.path.isfile(vbs_path):
                    os.remove(vbs_path)
            except OSError:
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