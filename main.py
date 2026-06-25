"""AuraPet — 桌面宠物主程序（PyQt5）"""

import sys
import os
import glob
from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QLabel, QSystemTrayIcon, QMenu, QAction
)
from PyQt5.QtCore import Qt, QSize, QPoint, QTimer
from PyQt5.QtGui import QPixmap, QIcon

from config import load, save, CONFIG_DIR


# ─── 路径工具 ────────────────────────────
def _resolve_data_subdir(subfolder: str) -> str:
    """给定 data/ 下的子文件夹名，返回绝对路径（兼容 PyInstaller）"""
    if getattr(sys, 'frozen', False):
        exe_dir = os.path.dirname(sys.executable)
        for base_dir in (exe_dir, getattr(sys, '_MEIPASS', '')):
            candidate = os.path.join(base_dir, 'data', subfolder)
            if os.path.isdir(candidate):
                return candidate
        return ''
    else:
        base_dir = os.path.dirname(os.path.abspath(__file__))
        return os.path.join(base_dir, 'data', subfolder)


# ─── 桌面宠物窗口 ─────────────────────────
class DesktopPet(QWidget):
    def __init__(self, frames_dir: str, always_on_top: bool = True):
        super().__init__()
        self.frames_dir = frames_dir
        self.always_on_top = always_on_top
        self.drag_pos = QPoint()
        self.initUI()

    def initUI(self):
        flags = Qt.FramelessWindowHint | Qt.SubWindow
        if self.always_on_top:
            flags |= Qt.WindowStaysOnTopHint
        self.setWindowFlags(flags)

        self.setAttribute(Qt.WA_TranslucentBackground, True)

        screen = QApplication.primaryScreen()
        available_geometry = screen.availableGeometry()

        pet_width = 533
        pet_height = 400
        x = available_geometry.width() - pet_width
        y = available_geometry.height() - pet_height

        self.setGeometry(x, y, pet_width, pet_height)

        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)

        self.label = QLabel()
        self.layout.addWidget(self.label)

        # 帧动画
        self.frames = []
        self.current_frame = 0
        self.frame_size = QSize(pet_width, pet_height)
        self.load_frames()

        if self.frames:
            self.label.setPixmap(self.frames[0])

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.next_frame)
        self.timer.start(42)

    def update_settings(self, always_on_top: bool):
        """热更新窗口置顶"""
        if always_on_top == self.always_on_top:
            return
        self.always_on_top = always_on_top
        self.hide()
        flags = Qt.FramelessWindowHint | Qt.SubWindow
        if self.always_on_top:
            flags |= Qt.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.show()

    def load_frames(self):
        """从 frames_dir 加载所有 PNG，按文件名排序"""
        self.frames.clear()
        png_files = sorted(glob.glob(os.path.join(self.frames_dir, '*.png')))
        for filepath in png_files:
            pixmap = QPixmap(filepath)
            if not pixmap.isNull():
                pixmap = pixmap.scaled(
                    self.frame_size,
                    Qt.KeepAspectRatio,
                    Qt.SmoothTransformation
                )
                self.frames.append(pixmap)

    def next_frame(self):
        """切换到下一帧"""
        if not self.frames:
            return
        self.current_frame = (self.current_frame + 1) % len(self.frames)
        self.label.setPixmap(self.frames[self.current_frame])

    def change_character(self, frames_dir: str):
        """切换角色 — 重新加载 PNG 序列"""
        self.frames_dir = frames_dir
        self.load_frames()
        self.current_frame = 0
        if self.frames:
            self.label.setPixmap(self.frames[0])

    # ── 鼠标拖拽 ──────────────────────────
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.drag_pos = event.globalPos()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.LeftButton and not self.drag_pos.isNull():
            delta = event.globalPos() - self.drag_pos
            self.move(self.pos() + delta)
            self.drag_pos = event.globalPos()

    def mouseReleaseEvent(self, event):
        self.drag_pos = QPoint()


# ─── 应用入口 ─────────────────────────────
def main():
    cfg = load()
    frames_dir = _resolve_data_subdir(cfg['character'])

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    # 创建桌面宠物
    pet = DesktopPet(
        frames_dir,
        always_on_top=cfg['always_on_top']
    )
    pet.show()

    # ── 系统托盘 ───────────────────────────
    logo_path = os.path.join(CONFIG_DIR, 'logo.ico')
    if os.path.isfile(logo_path):
        tray = QSystemTrayIcon(QIcon(logo_path))
    elif pet.frames:
        tray = QSystemTrayIcon(QIcon(pet.frames[0]))
    else:
        tray = QSystemTrayIcon(app.style().standardIcon(1))

    tray.setToolTip("AuraPet")

    # 托盘菜单
    menu = QMenu()
    settings_action = QAction("⚙  设置", menu)
    settings_action.triggered.connect(lambda: open_settings(pet, tray))
    menu.addAction(settings_action)

    menu.addSeparator()

    quit_action = QAction("⏻  退出", menu)
    quit_action.triggered.connect(lambda: shutdown(app, pet, tray))
    menu.addAction(quit_action)

    tray.setContextMenu(menu)
    tray.activated.connect(lambda reason: (
        open_settings(pet, tray) if reason == QSystemTrayIcon.DoubleClick else None
    ))
    tray.show()

    app.exec_()


def open_settings(pet: DesktopPet, tray: QSystemTrayIcon):
    """打开设置窗口（CustomTkinter）"""
    from settings_ui import SettingsWindow
    win = SettingsWindow()

    def on_config_saved(new_cfg: dict):
        """设置保存后立即更新宠物"""
        # 切换角色
        frames_dir = _resolve_data_subdir(new_cfg['character'])
        pet.change_character(frames_dir)

        # 更新置顶
        pet.update_settings(new_cfg['always_on_top'])

        # 更新托盘图标
        logo_path = os.path.join(CONFIG_DIR, 'logo.ico')
        if os.path.isfile(logo_path):
            tray.setIcon(QIcon(logo_path))
        elif pet.frames:
            tray.setIcon(QIcon(pet.frames[0]))

    win.set_on_save(on_config_saved)
    win.mainloop()


def shutdown(app: QApplication, pet: DesktopPet, tray: QSystemTrayIcon):
    """优雅退出"""
    tray.hide()
    pet.close()
    app.quit()


if __name__ == '__main__':
    main()