
"""AuraPet — 桌面宠物主程序（PyQt5，懒加载优化版）"""

import sys, os, glob, time
from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QLabel, QSystemTrayIcon, QMenu, QAction
)
from PyQt5.QtCore import Qt, QSize, QPoint, QTimer, QRect
from PyQt5.QtGui import QPixmap, QIcon, QPainter, QColor, QImage

from config import load, save, CONFIG_DIR

# ─── 路径工具 ────────────────────────────
def _resolve_data_subdir(subfolder: str) -> str:
    if getattr(sys, 'frozen', False):
        exe_dir = os.path.dirname(sys.executable)
        for d in (exe_dir, getattr(sys, '_MEIPASS', '')):
            c = os.path.join(d, 'data', subfolder)
            if os.path.isdir(c):
                return c
        return ''
    else:
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', subfolder)


def _sync_auto_start():
    if sys.platform != 'win32':
        return
    sd = os.path.join(os.getenv('APPDATA', ''),
                      r'Microsoft\Windows\Start Menu\Programs\Startup')
    vbs = os.path.join(sd, 'AuraPet.vbs')
    for f in ('AuraPet.bat', 'AuraPet.lnk'):
        try:
            fp = os.path.join(sd, f)
            if os.path.isfile(fp):
                os.remove(fp)
        except OSError:
            pass
    cfg = load()
    exe = os.path.abspath(sys.executable) if getattr(sys, 'frozen', False) \
        else os.path.join(CONFIG_DIR, 'AuraPet.exe')
    if cfg.get('auto_start'):
        need = True
        if os.path.isfile(vbs):
            try:
                if os.path.abspath(exe) in open(vbs, encoding='utf-8').read():
                    need = False
            except OSError:
                pass
        if need:
            try:
                os.makedirs(sd, exist_ok=True)
                open(vbs, 'w', encoding='utf-8').write(
                    'CreateObject("WScript.Shell").Run """{}""", 0, False'.format(exe))
            except OSError:
                pass
    else:
        if os.path.isfile(vbs):
            try:
                os.remove(vbs)
            except OSError:
                pass


# ─── DesktopPet ───────────────────────────
class DesktopPet(QWidget):
    PET_W, PET_H = 533, 400
    CLICK_CD = 30       # 点击动画冷却秒数
    INTERVAL = 42       # 默认帧间隔 ms
    CLICK_INTERVAL = 28 # 点击动画帧间隔 ms（加快 1/3）

    def __init__(self, character='ar15', always_on_top=True,
                 saved_x=None, saved_y=None):
        super().__init__()
        self.character = character
        self.always_on_top = always_on_top
        # 只存文件路径，每帧按需加载（内存 ~10MB vs 原 ~200MB）
        self.wait_paths = []
        self.click_paths = []
        self.pick_paths = []
        self.current_paths = []
        self.current_idx = 0
        self._cd_timer = 0.0
        self._cd_active = False
        self._pre_cd_paths = []
        self._hit_rect = None  # 非透明像素包围矩形
        self.initUI(saved_x, saved_y)

    def initUI(self, saved_x, saved_y):
        flags = Qt.FramelessWindowHint | Qt.SubWindow
        if self.always_on_top:
            flags |= Qt.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WA_TranslucentBackground, True)

        d = _resolve_data_subdir(self.character)
        self.wait_paths = self._scan(os.path.join(d, 'wait'))
        self.click_paths = self._scan(os.path.join(d, 'click'))
        self.pick_paths = self._scan(os.path.join(d, 'pick'))
        if not self.wait_paths and self.click_paths:
            self.wait_paths = self.click_paths
        if not self.pick_paths:
            self.pick_paths = self.wait_paths or self.click_paths
        self.current_paths = self.wait_paths

        # 综合 wait/click/pick 所有动画首帧，取并集包围矩形
        self._hit_rect = self._calc_union_hit_rect()

        screen = QApplication.primaryScreen()
        a = screen.availableGeometry()
        if saved_x is not None and saved_y is not None:
            x = max(0, min(saved_x, a.width() - self.PET_W))
            y = max(0, min(saved_y, a.height() - self.PET_H))
        else:
            x = a.width() - self.PET_W
            y = a.height() - self.PET_H
        self.setGeometry(x, y, self.PET_W, self.PET_H)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.label = QLabel(self)
        self.label.setFixedSize(self.PET_W, self.PET_H)
        layout.addWidget(self.label, 0, Qt.AlignCenter)

        if self.current_paths:
            self._show(self.current_paths[0])

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.next_frame)
        self.timer.setInterval(self.INTERVAL)
        self.timer.start()

    def _scan(self, folder):
        if not os.path.isdir(folder):
            return []
        return sorted(glob.glob(os.path.join(folder, '*.png')))

    def _load(self, path):
        """加载单帧：缩放 → 画到固定画布 → 释放原图"""
        src = QPixmap(path)
        if src.isNull():
            return None
        scaled = src.scaled(QSize(self.PET_W, self.PET_H),
                            Qt.KeepAspectRatio, Qt.SmoothTransformation)
        canvas = QPixmap(self.PET_W, self.PET_H)
        canvas.fill(QColor(0, 0, 0, 1))  # 1/255 alpha，肉眼不可见但 Windows 识别
        p = QPainter(canvas)
        p.drawPixmap((self.PET_W - scaled.width()) // 2,
                     (self.PET_H - scaled.height()) // 2, scaled)
        p.end()
        return canvas

    def _show(self, path):
        pm = self._load(path)
        if pm:
            self.label.setPixmap(pm)

    def next_frame(self):
        if not self.current_paths:
            return
        self.current_idx = (self.current_idx + 1) % len(self.current_paths)
        if self.current_idx == 0 and self._cd_active:
            self._cd_active = False
            self.current_paths = self._pre_cd_paths or self.wait_paths
            self.timer.setInterval(self.INTERVAL)
        self._show(self.current_paths[self.current_idx])

    def change_character(self, character):
        self.character = character
        d = _resolve_data_subdir(character)
        self.click_paths = self._scan(os.path.join(d, 'click'))
        self.wait_paths = self._scan(os.path.join(d, 'wait'))
        self.pick_paths = self._scan(os.path.join(d, 'pick'))
        if not self.wait_paths and self.click_paths:
            self.wait_paths = self.click_paths
        if not self.pick_paths:
            self.pick_paths = self.wait_paths or self.click_paths
        self._cd_active = False
        self.current_paths = self.wait_paths
        self.current_idx = 0
        self._hit_rect = self._calc_union_hit_rect()
        if self.current_paths:
            self._show(self.current_paths[0])

    def update_settings(self, always_on_top):
        if always_on_top == self.always_on_top:
            return
        self.always_on_top = always_on_top
        self.hide()
        flags = Qt.FramelessWindowHint | Qt.SubWindow
        if always_on_top:
            flags |= Qt.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.show()

    # ── 鼠标 ────────────────────────────────
    def _calc_hit_rect(self, paths):
        """扫描图片列表，找出所有非透明像素的包围矩形"""
        if not paths:
            return None
        # 用 QImage 加载源文件，保留 alpha 通道
        img = QImage(paths[0])
        if img.isNull():
            return None
        # 确保格式为 ARGB32
        img = img.convertToFormat(QImage.Format_ARGB32)
        img = img.scaled(QSize(self.PET_W, self.PET_H),
                         Qt.KeepAspectRatio, Qt.SmoothTransformation)
        min_x, min_y = img.width(), img.height()
        max_x, max_y = -1, -1
        for y in range(img.height()):
            for x in range(img.width()):
                if img.pixel(x, y) >> 24:  # 直接提取 alpha 通道
                    if x < min_x: min_x = x
                    if x > max_x: max_x = x
                    if y < min_y: min_y = y
                    if y > max_y: max_y = y
        if max_x < 0:
            return None
        # 加上居中偏移，映射到画布（widget）坐标
        ox = (self.PET_W - img.width()) // 2
        oy = (self.PET_H - img.height()) // 2
        return QRect(min_x + ox, min_y + oy,
                     max_x - min_x + 1, max_y - min_y + 1)

    def _calc_union_hit_rect(self):
        """综合 wait/click/pick 所有动画的首帧，取并集包围矩形"""
        rects = []
        for paths in (self.wait_paths, self.click_paths, self.pick_paths):
            r = self._calc_hit_rect(paths)
            if r:
                rects.append(r)
        if not rects:
            return None
        # 取并集
        union = rects[0]
        for r in rects[1:]:
            union = union.united(r)
        return union

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        # 必须成功计算出 hit rect 才允许操作
        if self._hit_rect is None:
            return
        if not self._hit_rect.contains(event.pos()):
            return
        self._press = event.globalPos()
        self._drag = False
        try:
            from ctypes import windll
            windll.user32.ReleaseCapture()
        except Exception:
            pass

    def mouseMoveEvent(self, event):
        if not (event.buttons() == Qt.LeftButton and hasattr(self, '_press')):
            return
        d = event.globalPos() - self._press
        if not self._drag and d.manhattanLength() >= 8:
            self._drag = True
            if self.pick_paths:
                self.current_paths = self.pick_paths
                self.current_idx = 0
        if self._drag:
            self.move(self.pos() + d)
            self._press = event.globalPos()

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        # 框外点击不触发任何操作
        if self._hit_rect is None or not self._hit_rect.contains(event.pos()):
            return
        if getattr(self, '_drag', False):
            if self.wait_paths:
                self.current_paths = self.wait_paths
                self.current_idx = 0
                self._show(self.current_paths[0])
        else:
            self._try_click()
        self._save_pos()
        for a in ('_press', '_drag'):
            if hasattr(self, a):
                delattr(self, a)

    def _try_click(self):
        now = time.time()
        if now < self._cd_timer or not self.click_paths:
            return
        self._cd_timer = now + self.CLICK_CD
        self._cd_active = True
        self._pre_cd_paths = self.wait_paths
        self.current_paths = self.click_paths
        self.current_idx = 0
        self.timer.setInterval(self.CLICK_INTERVAL)
        self._show(self.current_paths[0])

    def _save_pos(self):
        cfg = load()
        cfg['pos_x'] = self.x()
        cfg['pos_y'] = self.y()
        save(cfg)


# ─── 应用入口 ─────────────────────────────
def main():
    _sync_auto_start()
    cfg = load()
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    logo = os.path.join(CONFIG_DIR, 'logo.ico')
    if os.path.isfile(logo):
        app.setWindowIcon(QIcon(logo))

    pet = DesktopPet(cfg.get('character', 'an94'),
                     cfg.get('always_on_top', True),
                     cfg.get('pos_x'), cfg.get('pos_y'))
    pet.show()

    logo = os.path.join(CONFIG_DIR, 'logo.ico')
    if os.path.isfile(logo):
        # 托盘图标放大 3 倍：用 QPixmap 加载后缩放
        pm = QPixmap(logo)
        if not pm.isNull():
            big = pm.scaled(48, 48, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            tray = QSystemTrayIcon(QIcon(big))
        else:
            tray = QSystemTrayIcon(QIcon(logo))
    elif pet.current_paths:
        tray = QSystemTrayIcon(QIcon(pet._load(pet.current_paths[0])))
    else:
        tray = QSystemTrayIcon()

    tray.setToolTip("AuraPet")
    menu = QMenu()
    a1 = QAction("⚙  设置", menu)
    a1.triggered.connect(lambda: open_settings(pet, tray))
    menu.addAction(a1)
    menu.addSeparator()
    a2 = QAction("⏻  退出", menu)
    a2.triggered.connect(lambda: (tray.hide(), pet.close(), app.quit()))
    menu.addAction(a2)
    tray.setContextMenu(menu)
    tray.activated.connect(
        lambda r: open_settings(pet, tray) if r == QSystemTrayIcon.DoubleClick else None)
    tray.show()

    app.exec_()


def open_settings(pet, tray):
    from settings_ui import SettingsWindow
    w = SettingsWindow()

    def cb(cfg):
        pet.change_character(cfg.get('character', 'an94'))
        pet.update_settings(cfg.get('always_on_top', True))
        lp = os.path.join(CONFIG_DIR, 'logo.ico')
        if os.path.isfile(lp):
            pm = QPixmap(lp)
            tray.setIcon(QIcon(pm.scaled(48, 48, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                         if not pm.isNull() else QIcon(lp))
        elif pet.current_paths:
            tray.setIcon(QIcon(pet._load(pet.current_paths[0])))
        else:
            tray.setIcon(QIcon())

    w.set_on_save(cb)
    w.mainloop()


if __name__ == '__main__':
    main()
