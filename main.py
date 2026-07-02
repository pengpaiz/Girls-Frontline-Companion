"""AuraPet — 桌面宠物主程序（PyQt5，缓存优化版）"""

import sys, os, glob, time, gc
from collections import OrderedDict
from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QLabel, QSystemTrayIcon, QMenu, QAction
)
from PyQt5.QtCore import Qt, QSize, QPoint, QTimer, QRect, QThread, pyqtSignal
from PyQt5.QtGui import QPixmap, QIcon, QPainter, QColor, QImage

from config import load, save, CONFIG_DIR
from logger import logger, perf_monitor

# ─── 角色动画速度配置 ─────────────────────────
# 每个角色可以配置不同动画的速度倍率（帧间隔除数）
CHARACTER_ANIM_SPEED = {
    'ar15': {
        'move': 3.0,    # 移动动画 3x 速度
        'click': 3.0,   # 点击动画 3x 速度
    },
    # 其他角色可以在这里添加配置
}

# ─── 图像缓存 ────────────────────────────
class ImageCache:
    """LRU 缓存，存储缩放后的 QPixmap"""
    def __init__(self, max_size=100):
        self._cache = OrderedDict()
        self._max_size = max_size

    def get(self, key):
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        return None

    def put(self, key, value):
        if key in self._cache:
            self._cache.move_to_end(key)
        else:
            if len(self._cache) >= self._max_size:
                self._cache.popitem(last=False)
        self._cache[key] = value

    def clear(self):
        self._cache.clear()

# 全局缓存实例
_image_cache = ImageCache(max_size=150)

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
    """同步开机自启动设置

    方案：VBS 静默脚本放 CONFIG_DIR（不受杀软拦截），
    仅在 Startup 文件夹放一个 .lnk 快捷方式指向 VBS。
    始终清理注册表旧条目，避免重复自启动。
    """
    if sys.platform != 'win32':
        return
    import winreg

    REG_KEY = r'Software\Microsoft\Windows\CurrentVersion\Run'
    REG_NAME = 'AuraPet'

    sd = os.path.join(os.getenv('APPDATA', ''),
                      r'Microsoft\Windows\Start Menu\Programs\Startup')

    cfg = load()
    exe = os.path.abspath(sys.executable) if getattr(sys, 'frozen', False) \
        else os.path.join(CONFIG_DIR, 'AuraPet.exe')

    # ── 1. 清理注册表旧条目（防止与快捷方式重复） ───────
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_KEY,
                             access=winreg.KEY_WRITE)
        try:
            winreg.DeleteValue(key, REG_NAME)
            logger.info('已清理注册表旧自启动项')
        except FileNotFoundError:
            pass
        winreg.CloseKey(key)
    except OSError as e:
        logger.debug(f'清理注册表跳过: {e}')

    # ── 2. 清理 Startup 文件夹内历史残留文件 ──────────
    for fname in os.listdir(sd) if os.path.isdir(sd) else []:
        if fname.lower() in ('aurapet.vbs', 'aurapet.bat'):
            try:
                os.remove(os.path.join(sd, fname))
            except OSError:
                pass

    if not cfg.get('auto_start'):
        # 关闭自启动：删除快捷方式
        try:
            lnk = os.path.join(sd, 'AuraPet.lnk')
            if os.path.isfile(lnk):
                os.remove(lnk)
                logger.info('已移除开机自启动')
        except OSError as e:
            logger.error(f'移除开机自启动失败: {e}')
        return

    # ── 3. 在 CONFIG_DIR 写入 VBS 静默启动脚本 ────────
    vbs_path = os.path.join(CONFIG_DIR, 'AuraPet.vbs')
    try:
        with open(vbs_path, 'w', encoding='utf-8') as f:
            f.write(
                'WScript.Sleep 5000\n'
                'CreateObject("WScript.Shell").Run """{}""", 0, False'.format(exe))
    except OSError as e:
        logger.error(f'创建 VBS 失败: {e}')
        return

    # ── 4. 在 Startup 文件夹创建指向 VBS 的快捷方式 ────
    lnk_path = os.path.join(sd, 'AuraPet.lnk')
    try:
        import win32com.client
        shell = win32com.client.Dispatch('WScript.Shell')
        sc = shell.CreateShortCut(lnk_path)
        sc.TargetPath = vbs_path
        sc.WorkingDirectory = CONFIG_DIR
        sc.WindowStyle = 7  # 最小化启动 WScript
        sc.save()
        logger.info(f'已创建开机自启动快捷方式: {lnk_path} → {vbs_path}')
    except Exception as e:
        logger.error(f'创建快捷方式失败: {e}')


# ─── 后台预加载工作线程 ──────────────────
# 使用 QImage 做缩放（线程安全），主线程收到结果后再转 QPixmap 显示
class PreloadWorker(QThread):
    """后台缩放图片，finished 信号发射 {path: QImage} 字典（线程安全）"""
    progress = pyqtSignal(int)        # 已完成帧数
    finished = pyqtSignal(object)     # {path: QImage}

    def __init__(self, paths, width, height, parent=None):
        super().__init__(parent)
        self._paths = list(paths)
        self._w = width
        self._h = height

    def run(self):
        result = {}
        size = QSize(self._w, self._h)
        total = len(self._paths)
        for i, path in enumerate(self._paths):
            img = QImage(path)
            if not img.isNull():
                result[path] = img.scaled(
                    size, Qt.KeepAspectRatio, Qt.SmoothTransformation
                )
            if i % 10 == 0:
                self.progress.emit(i)
        self.finished.emit(result)


# ─── DesktopPet ───────────────────────────
class DesktopPet(QWidget):
    PET_W, PET_H = 533, 400
    CLICK_CD = 30       # 点击动画冷却秒数
    BASE_INTERVAL = 66  # 基础帧间隔 ms（~15 FPS）
    BASE_CLICK_INTERVAL = 50  # 基础点击动画帧间隔 ms

    def __init__(self, character='ar15', always_on_top=True,
                 saved_x=None, saved_y=None):
        super().__init__()
        self.character = character
        self.always_on_top = always_on_top
        # 根据角色计算实际帧间隔
        self.INTERVAL = self.BASE_INTERVAL
        self.CLICK_INTERVAL = self._get_click_interval()
        self.MOVE_INTERVAL = self._get_move_interval()
        # 只存文件路径，每帧按需加载（内存 ~10MB vs 原 ~200MB）
        self.wait_paths = []
        self.click_paths = []
        self.pick_paths = []
        self.current_paths = []
        self.current_idx = 0
        self._cd_timer = 0.0
        self._cd_active = False
        self._pre_cd_paths = []
        self.after_click_paths = []
        self._original_wait_paths = []  # 过渡前的原始 wait，供淡入淡出用
        self._transition_phase = 0      # 0=无, 1=首轮after_click, 2=淡出, 3=淡入click, 4=淡入sit, 5=淡出sit
        self._transition_idx = 0
        self._transition_steps = 20     # 淡出帧数（减少，加快过渡）
        self._transition_steps_in = 12  # 淡入帧数（减少，加快过渡）
        self._transition_from_paths = [] # 过渡源路径
        self._transition_from_idx = 0
        self._sit_transition_from_paths = []  # 坐下过渡源路径
        self._hit_rect = None  # 非透明像素包围矩形
        # ── 缓存（有上限，防止内存泄漏） ──────────────
        self._canvas_cache = OrderedDict()   # 预渲染 canvas 缓存，上限 80 条
        self._canvas_cache_max = 80
        self._crossfade_cache = OrderedDict()  # crossfade 结果缓存，上限 60 条
        self._crossfade_cache_max = 60
        self._hot_frames = {}  # path -> QImage/QPixmap（已缩放），当前模式帧常驻内存
        self._last_shown_key = None  # (path, mirror) 上一次显示的帧，用于跳过冗余 setPixmap
        self._autosnap_counter = 0  # _auto_snap 节流计数器
        # ── 复用画布（减少 QPainter 创建和 GC 压力） ────────
        self._canvas = None           # _load 复用的持久 QPixmap
        self._crossfade_canvas = None # _show_crossfade 复用的持久 QPixmap
        # ── 后台预加载 ──────────────────────────────────────
        self._preload_thread = None
        self._preload_pending_paths = []
        # ── 位置保存防抖 ────────────────────────
        self._save_timer = QTimer()
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(1000)  # 1 秒防抖
        self._save_timer.timeout.connect(self._do_save)
        self._pos_dirty = False
        self._action_dirty = False
        # ── 拖拽节流 ────────────────────────────
        self._last_move_time = 0.0
        # ── 自动散步 ──────────────────────────
        self.move_paths = []
        self._moving = False
        self._move_target_x = 0
        self._move_mirror = False
        self._idle_frames = 0        # 空闲帧计数，用于触发散步
        self._walk_interval = 90     # ~6s (90×66ms)，减少散步频率
        self._move_loop_count = 0    # 当前已播放循环次数
        self._move_max_loops = 0     # 本次散步最大循环次数
        # ── 坐下模式 ──────────────────────────
        self.sit_paths = []
        self._sit_mode = False
        self._sit_switch_time = 0.0  # 上次切换坐下的时间
        # ── 动作冷却 ──────────────────────────
        self._mode_switch_time = 0.0  # 上次模式切换时间（click/sit）
        self._mode_cooldown = 5.0     # 模式切换后 5s 内不散步
        self.initUI(saved_x, saved_y)

    def _get_anim_speed(self, anim_type: str) -> float:
        """获取角色的动画速度倍率"""
        char_config = CHARACTER_ANIM_SPEED.get(self.character, {})
        return char_config.get(anim_type, 1.0)

    def _get_click_interval(self) -> int:
        """根据角色计算点击动画帧间隔（最低 33ms = 30FPS，避免抢占 CPU）"""
        speed = self._get_anim_speed('click')
        return max(33, int(self.BASE_CLICK_INTERVAL / speed))

    def _get_move_interval(self) -> int:
        """根据角色计算移动动画帧间隔（最低 33ms = 30FPS，避免抢占 CPU）"""
        speed = self._get_anim_speed('move')
        return max(33, int(self.BASE_INTERVAL / speed))

    def initUI(self, saved_x, saved_y):
        flags = Qt.FramelessWindowHint | Qt.Tool
        if self.always_on_top:
            flags |= Qt.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WA_TranslucentBackground, True)

        d = _resolve_data_subdir(self.character)
        self.wait_paths = self._scan(os.path.join(d, 'wait'))
        self.click_paths = self._scan(os.path.join(d, 'click'))
        self.pick_paths = self._scan(os.path.join(d, 'pick'))
        self.after_click_paths = self._scan(os.path.join(d, 'after_click'))
        self.move_paths = self._scan(os.path.join(d, 'move'))
        self.sit_paths = self._scan(os.path.join(d, 'sit'))
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

        # 预加载基础动画帧到内存（消除运行时的磁盘 I/O）
        # 合并为一次调用，避免第二次调用取消第一次的后台任务
        self._preload_paths(self.wait_paths + [p for p in self.click_paths if p not in self.wait_paths])

    def _scan(self, folder):
        if not os.path.isdir(folder):
            return []
        files = sorted(glob.glob(os.path.join(folder, '*.png')))
        if not files:
            # 尝试扫描子目录
            for sub in os.listdir(folder):
                sub_path = os.path.join(folder, sub)
                if os.path.isdir(sub_path):
                    files = sorted(glob.glob(os.path.join(sub_path, '*.png')))
                    if files:
                        break
        return files

    def _preload_paths(self, paths: list):
        """异步预加载一组动画帧到 _hot_frames（QThread 后台缩放，不阻塞 UI）

        若帧已在 _hot_frames 中（QPixmap 类型）则跳过；
        若已有预加载任务在跑则取消旧任务、启动新任务。
        """
        if not paths:
            return
        # 所有帧已就绪（Pixmap 类型） → 无需重做
        if all(p in self._hot_frames and not isinstance(self._hot_frames[p], QImage)
               for p in paths):
            return
        # 取消正在进行的预加载
        if self._preload_thread is not None and self._preload_thread.isRunning():
            try:
                self._preload_thread.finished.disconnect(self._on_preload_done)
            except TypeError:
                pass
            self._preload_thread.quit()
            self._preload_thread.wait()
            self._preload_thread = None
        self._preload_pending_paths = list(paths)
        worker = PreloadWorker(paths, self.PET_W, self.PET_H)
        worker.finished.connect(self._on_preload_done)
        self._preload_thread = worker
        worker.start()
        logger.debug(f'后台预加载已启动: {len(paths)} 帧')

    def _on_preload_done(self, result: dict):
        """后台预加载完成回调（主线程执行），将 QImage 结果写入 _hot_frames"""
        self._hot_frames.update(result)
        self._preload_pending_paths.clear()
        self._preload_thread = None
        logger.debug(f'后台预加载完成: {len(result)} 帧已写入热缓存')

    def trim_hot_frames(self):
        """模式切换时清理非当前动画帧的 _hot_frames，释放内存并触发 GC"""
        active = set()
        for paths in (self.wait_paths, self.click_paths, self.move_paths,
                      self.sit_paths, self.pick_paths, self.after_click_paths,
                      self.current_paths):
            active.update(paths)
        if self._transition_phase in (1, 2):
            active.update(self._original_wait_paths)
            active.update(self.after_click_paths)
        if self._transition_phase == 3:
            active.update(self._transition_from_paths)
            active.update(self.click_paths)
        if self._transition_phase == 4:
            active.update(self._sit_transition_from_paths)
            active.update(self.sit_paths)
        if self._transition_phase == 5:
            active.update(self.sit_paths)
            active.update(self.wait_paths)
        stale = [k for k in self._hot_frames if k not in active]
        if not stale:
            return
        for k in stale:
            del self._hot_frames[k]
        logger.debug(f'修剪热缓存: 移除 {len(stale)} 帧，保留 {len(self._hot_frames)} 帧')
        gc.collect()

    def _load(self, path, mirror=False):
        """加载单帧：使用缓存避免重复缩放和渲染"""
        cache_key = (path, mirror)

        # 检查本地画布缓存（LRU: 命中时移到末尾）
        if cache_key in self._canvas_cache:
            self._canvas_cache.move_to_end(cache_key)
            perf_monitor.record_cache_hit()
            return self._canvas_cache[cache_key]

        # 优先查热缓存（预加载的帧，零磁盘 I/O）
        # 后台预加载返回 QImage（线程安全），首次使用时转 QPixmap 并回写缓存
        hot = self._hot_frames.get(path)
        if hot is not None:
            if isinstance(hot, QImage):
                hot = QPixmap.fromImage(hot)
                self._hot_frames[path] = hot       # 回写，后续调用直接命中 QPixmap
            scaled = hot.toImage().mirrored(True, False) if mirror else hot
        else:
            # 热缓存未命中，走全局 LRU 缓存 → 磁盘
            scale_key = (path, self.PET_W, self.PET_H, mirror)
            scaled = _image_cache.get(scale_key)

            if scaled is None:
                perf_monitor.record_cache_miss()
                try:
                    if mirror:
                        src = QImage(path)
                        if src.isNull():
                            logger.warning(f'图片加载失败: {path}')
                            return None
                        src = src.mirrored(True, False)
                        scaled = src.scaled(QSize(self.PET_W, self.PET_H),
                                            Qt.KeepAspectRatio, Qt.SmoothTransformation)
                    else:
                        src = QPixmap(path)
                        if src.isNull():
                            logger.warning(f'图片加载失败: {path}')
                            return None
                        scaled = src.scaled(QSize(self.PET_W, self.PET_H),
                                            Qt.KeepAspectRatio, Qt.SmoothTransformation)
                    _image_cache.put(scale_key, scaled)
                except Exception as e:
                    logger.error(f'图片处理错误 [{path}]: {e}', exc_info=True)
                    return None

        # 兜底：任何 QImage 类型统一转为 QPixmap
        if isinstance(scaled, QImage):
            scaled = QPixmap.fromImage(scaled)

        # 渲染到复用画布（self._canvas 避免每帧 new QPixmap + QPainter + GC）
        try:
            if self._canvas is None:
                self._canvas = QPixmap(self.PET_W, self.PET_H)
            self._canvas.fill(QColor(0, 0, 0, 1))
            p = QPainter(self._canvas)
            try:
                p.drawPixmap((self.PET_W - scaled.width()) // 2,
                             (self.PET_H - scaled.height()) // 2, scaled)
            finally:
                p.end()

            # 缓存 copy()：防止下一次 fill/draw 污染已缓存的帧
            cached = self._canvas.copy()

            # 存入本地缓存（LRU: 超限时淘汰最久未用，并触发 GC 释放显存）
            if len(self._canvas_cache) >= self._canvas_cache_max:
                self._canvas_cache.popitem(last=False)
                gc.collect()
            self._canvas_cache[cache_key] = cached
            return cached
        except Exception as e:
            logger.error(f'画布渲染错误 [{path}]: {e}', exc_info=True)
            return None

    def _show(self, path, mirror=False):
        """显示单帧，使用缓存 + 帧去重（画面未变则跳过 setPixmap，降低 DWM 合成压力）"""
        show_key = (path, mirror)
        if show_key == self._last_shown_key:
            return  # 画面完全相同，跳过重绘
        pm = self._load(path, mirror)
        if pm:
            self.label.setPixmap(pm)
            self._last_shown_key = show_key

    def _stop_moving(self):
        """停止移动并恢复到等待状态"""
        logger.debug(f'散步结束: 最终位置=({self.x()}, {self.y()})')
        self._moving = False
        self._idle_frames = 0
        self.timer.setInterval(self.INTERVAL)
        self.current_paths = self.wait_paths
        self.current_idx = 0
        self._show(self.current_paths[0])
        self._save_pos()
        self.trim_hot_frames()  # 散步结束，清理 move 帧释放内存

    def next_frame(self):
        """主帧更新循环"""
        # 记录帧时间（性能监控）
        perf_monitor.record_frame()

        if not self.current_paths:
            return

        # ── Phase 4: 淡入 sit（进入坐下模式）──────
        if self._transition_phase == 4:
            self._transition_idx += 1
            progress = min(self._transition_idx / self._transition_steps_in, 1.0)
            from_path = self._sit_transition_from_paths[self._transition_from_idx % len(self._sit_transition_from_paths)]
            self._transition_from_idx += 1
            self._show_crossfade(from_path, self.sit_paths[0], progress)
            if progress >= 1.0:
                self._transition_phase = 0
                self.current_paths = self.sit_paths
                self.current_idx = 0
            return

        # ── Phase 5: 淡出 sit（退出坐下模式）──────
        if self._transition_phase == 5:
            self._transition_idx += 1
            progress = min(self._transition_idx / self._transition_steps, 1.0)
            sit_idx = self._transition_idx % len(self.sit_paths)
            wait_idx = self._transition_idx % len(self.wait_paths)
            self._show_crossfade(self.sit_paths[sit_idx], self.wait_paths[wait_idx], progress)
            if progress >= 1.0:
                self._transition_phase = 0
                self._sit_mode = False
                self.current_paths = self.wait_paths
                self.current_idx = wait_idx
                self._show(self.current_paths[self.current_idx])
            return

        # ── 坐下模式：固定播放 sit 动画 ──────────
        if self._sit_mode:
            self.current_idx = (self.current_idx + 1) % len(self.sit_paths)
            self._show(self.sit_paths[self.current_idx])
            return

        # ── Phase 3: 淡入 click ─────────────────
        if self._transition_phase == 3:
            self._transition_idx += 1
            progress = min(self._transition_idx / self._transition_steps_in, 1.0)
            from_path = self._transition_from_paths[self._transition_from_idx]
            self._transition_from_idx = (self._transition_from_idx + 1) % len(self._transition_from_paths)
            self._show_crossfade(from_path, self.click_paths[0], progress)
            if progress >= 1.0:
                self._transition_phase = 0
                self._cd_active = True
                self._pre_cd_paths = self.wait_paths
                self.current_paths = self.click_paths
                self.current_idx = 0
                self.timer.setInterval(self.CLICK_INTERVAL)
            return

        # ── 自动散步：移动中 ────────────────────
        if self._moving:
            if self.move_paths:
                self.current_idx = (self.current_idx + 1) % len(self.move_paths)
                if self.current_idx == 0:
                    self._move_loop_count += 1
                self._show(self.move_paths[self.current_idx], self._move_mirror)

            # 循环次数达到上限，停止散步
            if self._move_max_loops > 0 and self._move_loop_count >= self._move_max_loops:
                self._stop_moving()
                return

            # 向目标移动
            dx = self._move_target_x - self.x()
            step = 4  # 每帧移动像素
            if abs(dx) <= step:
                # 到达目标，吸附边缘
                self.move(self._move_target_x, self.y())
                self._stop_moving()
            else:
                self.move(self.x() + (step if dx > 0 else -step), self.y())
            return

        # ── 非移动状态：正常帧动画 ──────────────
        self.current_idx = (self.current_idx + 1) % len(self.current_paths)

        if self.current_idx == 0 and self._cd_active:
            self._cd_active = False
            self.timer.setInterval(self.INTERVAL)
            if self.after_click_paths:
                self._original_wait_paths = self.wait_paths
                self.wait_paths = self.after_click_paths
                self._pre_cd_paths = []
                self._transition_phase = 1
            self.current_paths = self._pre_cd_paths or self.wait_paths
            self.save_action_state()  # click 冷却结束，持久化当前状态

        # ── 过渡状态机 ────────────────────────
        if self._transition_phase == 1 and self.current_idx == 0:
            if self._original_wait_paths:
                self._transition_phase = 2
                self._transition_idx = 0

        if self._transition_phase == 2:
            self._transition_idx += 1
            progress = min(self._transition_idx / self._transition_steps, 1.0)
            after_idx = self.current_idx % len(self.after_click_paths)
            wait_idx = self.current_idx % len(self._original_wait_paths)
            self._show_crossfade(
                self.after_click_paths[after_idx],
                self._original_wait_paths[wait_idx],
                progress
            )
            if progress >= 1.0:
                self._transition_phase = 0
                self.wait_paths = self._original_wait_paths
                self.current_paths = self.wait_paths
                self.current_idx = wait_idx
                self._show(self.current_paths[self.current_idx])
            return

        self._show(self.current_paths[self.current_idx])

        # ── 全局自动吸附（每 30 帧执行一次，减少系统调用）─────
        self._autosnap_counter += 1
        if self._autosnap_counter >= 30:
            self._autosnap_counter = 0
            self._auto_snap()

        # ── 散步计时器 ─────────────────────────
        self._idle_frames += 1
        # 模式切换后 5s 内禁止散步（click/sit 切换）
        mode_cooldown = time.time() - self._mode_switch_time < self._mode_cooldown
        if (self._idle_frames >= self._walk_interval
                and self.move_paths
                and self._transition_phase == 0
                and not self._cd_active
                and not mode_cooldown):
            self._start_walk()

    def _start_walk(self):
        """随机走向屏幕一侧"""
        self._preload_paths(self.move_paths)  # 确保 move 帧在内存中
        import random
        screen = QApplication.primaryScreen()
        a = screen.availableGeometry()
        cur_x = self.x()

        left_dist = cur_x - a.left()
        right_dist = a.right() - self.PET_W - cur_x
        min_walk = a.width() // 4  # 最短路程 = 屏幕宽度的1/4

        # 选方向：优先选满足最短路程的方向
        if left_dist >= min_walk and right_dist >= min_walk:
            # 两个方向都满足，按距离加权随机
            if random.random() < right_dist / (left_dist + right_dist + 1):
                target_x = cur_x - min_walk
                self._move_mirror = True
            else:
                target_x = cur_x + min_walk
                self._move_mirror = False
        elif left_dist >= min_walk:
            target_x = cur_x - min_walk
            self._move_mirror = True
        elif right_dist >= min_walk:
            target_x = cur_x + min_walk
            self._move_mirror = False
        else:
            # 都不满足最短路程，走较远方向到边缘
            if left_dist > right_dist:
                target_x = a.left()
                self._move_mirror = True
            else:
                target_x = a.right() - self.PET_W
                self._move_mirror = False

        self._move_target_x = target_x
        self._moving = True
        self._idle_frames = 0
        self._move_loop_count = 0
        self._move_max_loops = random.randint(8, 15)  # 减少循环次数
        self.timer.setInterval(self.MOVE_INTERVAL)    # 使用角色特定的移动帧率
        self.current_paths = self.move_paths
        self.current_idx = 0
        logger.debug(f'开始散步: 目标={target_x}, 循环次数={self._move_max_loops}')
        self._show(self.move_paths[0], self._move_mirror)

    def _show_crossfade(self, path_a, path_b, progress):
        """progress: 0.0 = 纯 A, 1.0 = 纯 B，使用缓存优化"""
        # 量化 progress 到离散步进，减少缓存条目
        quantized_progress = round(progress * 20) / 20  # 20 个离散级别
        cache_key = (path_a, path_b, quantized_progress)

        # 检查缓存（LRU: 命中时移到末尾）
        if cache_key in self._crossfade_cache:
            self._crossfade_cache.move_to_end(cache_key)
            self.label.setPixmap(self._crossfade_cache[cache_key])
            return

        # 加载图片（使用缓存的缩放版本）
        src_a = self._load_scaled(path_a)
        src_b = self._load_scaled(path_b)
        if src_a is None or src_b is None:
            return

        # 渲染 crossfade（复用 self._crossfade_canvas 避免反复分配）
        if self._crossfade_canvas is None:
            self._crossfade_canvas = QPixmap(self.PET_W, self.PET_H)
        self._crossfade_canvas.fill(QColor(0, 0, 0, 1))
        p = QPainter(self._crossfade_canvas)
        try:
            p.setOpacity(1.0 - progress)
            p.drawPixmap((self.PET_W - src_a.width()) // 2,
                         (self._crossfade_canvas.height() - src_a.height()) // 2, src_a)
            p.setOpacity(progress)
            p.drawPixmap((self.PET_W - src_b.width()) // 2,
                         (self._crossfade_canvas.height() - src_b.height()) // 2, src_b)
        finally:
            p.end()

        # 缓存 copy()：防止下次 fill/draw 污染已缓存帧
        cached = self._crossfade_canvas.copy()
        # 存入缓存（LRU: 超限时淘汰最久未用，并触发 GC 释放显存）
        if len(self._crossfade_cache) >= self._crossfade_cache_max:
            self._crossfade_cache.popitem(last=False)
            gc.collect()
        self._crossfade_cache[cache_key] = cached
        self.label.setPixmap(cached)
        self._last_shown_key = None  # crossfade 是混合帧，重置去重标记

    def _load_scaled(self, path):
        """加载并缩放图片，使用全局缓存"""
        scale_key = (path, self.PET_W, self.PET_H, False)
        scaled = _image_cache.get(scale_key)

        if scaled is None:
            src = QPixmap(path)
            if src.isNull():
                return None
            scaled = src.scaled(QSize(self.PET_W, self.PET_H),
                                Qt.KeepAspectRatio, Qt.SmoothTransformation)
            _image_cache.put(scale_key, scaled)

        return scaled

    def change_character(self, character):
        """切换角色时清空缓存并重新计算帧间隔"""
        logger.info(f'切换角色: {self.character} → {character}')
        self.character = character
        # 重新计算帧间隔
        self.INTERVAL = self.BASE_INTERVAL
        self.CLICK_INTERVAL = self._get_click_interval()
        self.MOVE_INTERVAL = self._get_move_interval()
        logger.debug(f'帧间隔: base={self.INTERVAL}ms, click={self.CLICK_INTERVAL}ms, move={self.MOVE_INTERVAL}ms')
        # 取消正在进行的后台预加载
        if self._preload_thread is not None and self._preload_thread.isRunning():
            try:
                self._preload_thread.finished.disconnect(self._on_preload_done)
            except TypeError:
                pass
            self._preload_thread.quit()
            self._preload_thread.wait()
            self._preload_thread = None
        # 清空本地缓存 + 复用画布，并触发 GC 释放显存
        self._canvas_cache.clear()
        self._crossfade_cache.clear()
        self._hot_frames.clear()
        self._canvas = None
        self._crossfade_canvas = None
        self._last_shown_key = None
        gc.collect()
        d = _resolve_data_subdir(character)
        self.click_paths = self._scan(os.path.join(d, 'click'))
        self.wait_paths = self._scan(os.path.join(d, 'wait'))
        self.pick_paths = self._scan(os.path.join(d, 'pick'))
        self.after_click_paths = self._scan(os.path.join(d, 'after_click'))
        self.move_paths = self._scan(os.path.join(d, 'move'))
        self.sit_paths = self._scan(os.path.join(d, 'sit'))
        logger.debug(f'加载动画: wait={len(self.wait_paths)}, click={len(self.click_paths)}, move={len(self.move_paths)}')
        if not self.wait_paths and self.click_paths:
            self.wait_paths = self.click_paths
        if not self.pick_paths:
            self.pick_paths = self.wait_paths or self.click_paths
        self._cd_active = False
        self._sit_mode = False
        self._transition_phase = 0
        self.current_paths = self.wait_paths
        self.current_idx = 0
        self._hit_rect = self._calc_union_hit_rect()
        # 预加载新角色的基础动画帧（后台异步，不阻塞 UI）
        # 合并为一次调用，避免第二次调用取消第一次的后台任务
        self._preload_paths(self.wait_paths + [p for p in self.click_paths if p not in self.wait_paths])
        if self.current_paths:
            self._show(self.current_paths[0])
        self.save_action_state()  # 切换角色后重置并持久化状态

    def update_settings(self, always_on_top):
        if always_on_top == self.always_on_top:
            return
        self.always_on_top = always_on_top

        # 保存当前位置和尺寸
        pos = self.pos()
        size = self.size()

        # 重建窗口标志
        flags = Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint if always_on_top \
            else Qt.FramelessWindowHint | Qt.Tool
        # hide → setWindowFlags → show 是 Qt 文档推荐的强制窗口属性变更流程
        self.hide()
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WA_TranslucentBackground, True)

        # 恢复位置、尺寸并显示
        self.resize(size)
        self.move(pos)
        self.show()
        self.activateWindow()

    # ── 鼠标 ────────────────────────────────
    def _calc_hit_rect(self, paths):
        """扫描图片列表，找出所有非透明像素的包围矩形（优化版）"""
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

        width, height = img.width(), img.height()
        min_x, min_y = width, height
        max_x, max_y = -1, -1

        # PyQt5 兼容方式：使用 bits() 并转换为 bytes
        # 注意：PyQt5 的 constBits() 返回 sip.voidptr，需要特殊处理
        ptr = img.bits()
        ptr.setsize(height * img.bytesPerLine())
        data = bytes(ptr.asstring())
        bytes_per_line = img.bytesPerLine()

        # 快速扫描：先找到上下边界，再找左右边界
        # 从上往下扫描找 min_y
        for y in range(height):
            row_start = y * bytes_per_line
            for x in range(width):
                # ARGB32 格式：每个像素 4 字节，alpha 在第 3 字节（索引 3）
                alpha = data[row_start + x * 4 + 3]
                if alpha > 0:
                    min_y = y
                    break
            if min_y < height:
                break

        # 从下往上扫描找 max_y
        for y in range(height - 1, -1, -1):
            row_start = y * bytes_per_line
            for x in range(width):
                alpha = data[row_start + x * 4 + 3]
                if alpha > 0:
                    max_y = y
                    break
            if max_y >= 0:
                break

        if max_y < 0:
            return None

        # 从左往右扫描找 min_x
        for x in range(width):
            for y in range(min_y, max_y + 1):
                row_start = y * bytes_per_line
                alpha = data[row_start + x * 4 + 3]
                if alpha > 0:
                    min_x = x
                    break
            if min_x < width:
                break

        # 从右往左扫描找 max_x
        for x in range(width - 1, -1, -1):
            for y in range(min_y, max_y + 1):
                row_start = y * bytes_per_line
                alpha = data[row_start + x * 4 + 3]
                if alpha > 0:
                    max_x = x
                    break
            if max_x >= 0:
                break

        # 加上居中偏移，映射到画布（widget）坐标
        ox = (self.PET_W - width) // 2
        oy = (self.PET_H - height) // 2
        return QRect(min_x + ox, min_y + oy,
                     max_x - min_x + 1, max_y - min_y + 1)

    def _calc_union_hit_rect(self):
        """综合 wait/click/pick 所有动画首帧，取并集包围矩形"""
        rects = []
        for paths in (self.wait_paths, self.click_paths, self.pick_paths, self.after_click_paths, self.move_paths, self.sit_paths):
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
        # 右键切换坐下模式（带过渡效果）
        if event.button() == Qt.RightButton:
            if self._hit_rect is None or not self._hit_rect.contains(event.pos()):
                return
            if self.sit_paths and self._transition_phase == 0:
                self._sit_transition_from_paths = list(self.current_paths)
                self._transition_from_idx = self.current_idx
                self._transition_idx = 0
                self._sit_switch_time = time.time()  # 记录切换时间
                self._mode_switch_time = self._sit_switch_time  # 记录模式切换时间
                if not self._sit_mode:
                    # 进入坐下模式：淡入sit
                    self._preload_paths(self.sit_paths)  # 预加载 sit 帧
                    self._transition_phase = 4
                    self._sit_mode = True
                    self._moving = False
                    self.save_action_state()  # 立即持久化
                    logger.debug('进入坐下模式')
                else:
                    # 退出坐下模式：淡出sit到wait
                    self._transition_phase = 5
                    self.save_action_state()  # 立即持久化
                    logger.debug('退出坐下模式')
                self.trim_hot_frames()  # 坐下模式切换，清理非当前动画帧
            return

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
            # 节流：最多 16ms 移动一次（60Hz），避免淹没窗口管理器
            now = time.monotonic()
            if now - self._last_move_time >= 0.016:
                self._last_move_time = now
                self.move(self.pos() + d)
                self._press = event.globalPos()

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        if self._hit_rect is None or not self._hit_rect.contains(event.pos()):
            return
        was_moving = self._moving
        if getattr(self, '_drag', False):
            self._moving = False  # 拖拽取消散步
            if self.wait_paths:
                self.current_paths = self.wait_paths
                self.current_idx = 0
                self._show(self.current_paths[0])
            self._snap_to_edge()  # 吸附边缘
        else:
            if not was_moving:    # 散步中忽略单击
                self._try_click()
        self._save_pos()
        for a in ('_press', '_drag'):
            if hasattr(self, a):
                delattr(self, a)

    def _try_click(self):
        now = time.time()
        if now < self._cd_timer or not self.click_paths:
            return
        self._preload_paths(self.click_paths)  # 确保 click 帧在内存中
        self._cd_timer = now + self.CLICK_CD
        self._mode_switch_time = now  # 记录模式切换时间
        # 保存当前 idle 帧作为淡入源
        self._transition_from_paths = list(self.current_paths)
        self._transition_from_idx = self.current_idx
        self._transition_phase = 3    # 淡入 click
        self._transition_idx = 0
        self.timer.setInterval(self.CLICK_INTERVAL)
        self.save_action_state()  # 立即持久化 click 状态
        self.trim_hot_frames()     # 点击模式切换，清理非当前动画帧
        logger.debug(f'点击动画触发，冷却 {self.CLICK_CD}s')

    def _save_pos(self):
        """标记位置需要保存（防抖：1 秒内无新操作才真正写入）"""
        self._pos_dirty = True
        self._save_timer.start()

    def _do_save(self):
        """真正执行保存（由定时器触发，合并位置 + 动作状态）"""
        try:
            cfg = load()
            changed = False
            if self._pos_dirty:
                cfg['pos_x'] = self.x()
                cfg['pos_y'] = self.y()
                self._pos_dirty = False
                changed = True
            if self._action_dirty:
                action_type = self._get_action_type()
                if cfg.get('action_type') != action_type:
                    cfg['action_type'] = action_type
                    changed = True
                self._action_dirty = False
            if changed:
                save(cfg)
        except Exception as e:
            logger.error(f'延迟保存失败: {e}', exc_info=True)

    def _get_action_type(self) -> str:
        """获取当前动作类型（用于持久化）"""
        if self._transition_phase == 5:
            # Phase 5: 正在退出 sit 模式 → 视为 wait
            return 'wait'
        if self._sit_mode or self._transition_phase == 4:
            return 'sit'
        if self._cd_active or self._transition_phase in (1, 2, 3):
            return 'click'
        return 'wait'

    def save_action_state(self):
        """标记动作状态需要保存（防抖，与位置保存合并）"""
        self._action_dirty = True
        self._save_timer.start()

    def restore_action_state(self, action_type: str):
        """恢复上次的动作状态（启动时调用）"""
        if action_type == 'sit':
            if self.sit_paths:
                self._sit_mode = True
                self.current_paths = self.sit_paths
                self.current_idx = 0
                self._show(self.current_paths[0])
                logger.info('恢复坐下模式')
            else:
                logger.warning('无法恢复坐下模式：无 sit 动画资源')
        elif action_type == 'click':
            if self.click_paths:
                self._try_click()
                logger.info('恢复点击模式')
            else:
                logger.warning('无法恢复点击模式：无 click 动画资源')
        else:
            logger.debug('动作状态为 wait，无需恢复')

    def _snap_to_edge(self):
        """吸附到最近的屏幕边缘（含任务栏）"""
        screen = QApplication.primaryScreen()
        a = screen.availableGeometry()
        snap = 40  # 吸附阈值（像素）
        x, y = self.x(), self.y()

        # X 轴吸附
        if abs(x - a.left()) < snap:
            x = a.left()
        elif abs(x - (a.right() - self.PET_W)) < snap:
            x = a.right() - self.PET_W

        # Y 轴吸附
        if abs(y - a.top()) < snap:
            y = a.top()
        elif abs(y - (a.bottom() - self.PET_H)) < snap:
            y = a.bottom() - self.PET_H

        self.move(x, y)

    def _auto_snap(self):
        """全局自动吸附：仅在wait状态且空闲时靠近边缘自动吸附"""
        # 只在wait状态时吸附
        if self.current_paths != self.wait_paths:
            return
        if self._moving or self._transition_phase != 0 or self._sit_mode:
            return
        if getattr(self, '_drag', False):  # 拖动中不吸附
            return
        screen = QApplication.primaryScreen()
        a = screen.availableGeometry()
        snap = 30  # 自动吸附阈值（像素）
        x, y = self.x(), self.y()
        moved = False

        # X 轴自动吸附
        if abs(x - a.left()) < snap and x != a.left():
            x = a.left()
            moved = True
        elif abs(x - (a.right() - self.PET_W)) < snap and x != a.right() - self.PET_W:
            x = a.right() - self.PET_W
            moved = True

        # Y 轴自动吸附（底部任务栏）
        if abs(y - a.top()) < snap and y != a.top():
            y = a.top()
            moved = True
        elif abs(y - (a.bottom() - self.PET_H)) < snap and y != a.bottom() - self.PET_H:
            y = a.bottom() - self.PET_H
            moved = True

        if moved:
            self.move(x, y)
            self._save_pos()

    def closeEvent(self, event):
        """窗口关闭时立即保存位置 + 动作状态（合并为单次写入）"""
        # 停止防抖定时器，立即执行一次保存
        self._save_timer.stop()
        try:
            cfg = load()
            cfg['pos_x'] = self.x()
            cfg['pos_y'] = self.y()
            cfg['action_type'] = self._get_action_type()
            save(cfg)
            logger.info(f'程序关闭，已保存状态: pos=({self.x()},{self.y()}), action={cfg["action_type"]}')
        except Exception as e:
            logger.error(f'关闭时保存状态失败: {e}', exc_info=True)
        super().closeEvent(event)


# ─── 应用入口 ─────────────────────────────
def _strip_qt_audio_plugins():
    """删除 Qt 捆绑的音频/媒体插件，防止 WASAPI 独占模式抢占系统音频

    仅删除 _MEIPASS（PyInstaller 临时解压目录）中的副本，
    不影响 venv 或系统安装的原始文件。
    我们的 app 不使用任何音频/媒体功能，删除这些插件零功能损失。
    """
    import shutil
    meipass = getattr(sys, '_MEIPASS', None)
    logger.info(f'_strip: frozen={getattr(sys, "frozen", False)}, '
                f'_MEIPASS={meipass}')
    if not meipass:
        return
    # 尝试所有可能的 Qt 插件路径（PyQt5 不同版本路径不同）
    for rel in ('PyQt5/Qt5/plugins', 'PyQt5/Qt/plugins', 'PyQt5/plugins'):
        plugins = os.path.join(meipass, rel)
        if not os.path.isdir(plugins):
            continue
        logger.info(f'_strip: 找到插件目录 {plugins}')
        for d in ('audio', 'mediaservice', 'texttospeech', 'webview'):
            target = os.path.join(plugins, d)
            if os.path.isdir(target):
                try:
                    shutil.rmtree(target)
                    logger.info(f'_strip: 已移除音频插件 {d}/')
                except OSError as e:
                    logger.warning(f'_strip: 移除插件失败 {d}: {e}')


def main():
    # ── 修复打包后中文编码乱码 ────────────────────────────────
    # PyInstaller 打包后 sys.stdout/stderr 默认使用系统编码（GBK），
    # 导致所有中文输出乱码。强制使用 UTF-8。
    if getattr(sys, 'frozen', False):
        os.environ.setdefault('PYTHONIOENCODING', 'utf-8')
        try:
            import io
            if sys.stdout and sys.stdout.encoding.lower() != 'utf-8':
                sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
            if sys.stderr and sys.stderr.encoding.lower() != 'utf-8':
                sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
        except Exception:
            pass

    try:
        logger.info('程序启动中...')
        _sync_auto_start()
        cfg = load()
        logger.info(f'配置加载成功: character={cfg.get("character")}, always_on_top={cfg.get("always_on_top")}')

        # 强制 OpenGL 后端（必须在 QApplication 构造前设置）
        # angle = ANGLE (DirectX 后备)，避免原生 OpenGL 驱动干扰音频子系统
        os.environ['QT_OPENGL'] = os.environ.get('QT_OPENGL', 'angle')

        # ── 音频隔离 ─────────────────────────────────────────
        # Qt5 在 QApplication 创建时通过平台插件初始化音频子系统，
        # 下列环境变量阻止其加载音频/媒体相关组件
        os.environ.setdefault('QT_NO_AUDIO', '1')            # 禁用 Qt 音频后端
        os.environ['QT_MULTIMEDIA_PREFERRED_PLUGINS'] = ''   # 不加载任何媒体插件
        os.environ['QT_WEBENGINE_DISABLE_AUDIO'] = '1'       # 禁用 WebEngine 音频

        # 打包模式：删除 _MEIPASS 中残留的音频插件 DLL
        if getattr(sys, 'frozen', False):
            _strip_qt_audio_plugins()

        # Windows: 通知系统本进程不处理通信音频，防止自动降低其他应用音量
        if sys.platform == 'win32':
            try:
                import ctypes
                ctypes.windll.user32.SystemParametersInfoW(
                    0x0058, 0, None, 0)  # SPI_SETBEEP=False
            except Exception:
                pass

        app = QApplication(sys.argv)
        logger.info(f'音频隔离: QT_OPENGL={os.environ.get("QT_OPENGL")}, '
                    f'QT_NO_AUDIO={os.environ.get("QT_NO_AUDIO")}, '
                    f'QT_MULTIMEDIA={os.environ.get("QT_MULTIMEDIA_PREFERRED_PLUGINS")!r}')
        app.setQuitOnLastWindowClosed(False)

        # 降低进程优先级，避免与游戏争抢 CPU
        if sys.platform == 'win32':
            try:
                import ctypes
                handle = ctypes.windll.kernel32.GetCurrentProcess()
                ctypes.windll.kernel32.SetPriorityClass(handle, 0x00004000)  # BELOW_NORMAL
                logger.info('进程优先级已设为 BELOW_NORMAL')
            except Exception as e:
                logger.warning(f'设置进程优先级失败: {e}')

        logo = os.path.join(CONFIG_DIR, 'logo.ico')
        if os.path.isfile(logo):
            app.setWindowIcon(QIcon(logo))

        pet = DesktopPet(cfg.get('character', 'an94'),
                         cfg.get('always_on_top', True),
                         cfg.get('pos_x'), cfg.get('pos_y'))
        pet.show()
        logger.info('桌面宠物窗口已显示')

        # 恢复上次的动作状态
        action_type = cfg.get('action_type', 'wait')
        logger.info(f'上次动作状态: {action_type}')
        if action_type != 'wait':
            pet.restore_action_state(action_type)

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

        # 添加性能统计菜单项
        a_perf = QAction("📊  性能统计", menu)
        a_perf.triggered.connect(lambda: logger.log_performance())
        menu.addAction(a_perf)

        menu.addSeparator()
        a2 = QAction("⏻  退出", menu)
        a2.triggered.connect(lambda: (logger.info('用户退出程序'), pet.save_action_state(), tray.hide(), pet.close(), app.quit()))
        menu.addAction(a2)
        tray.setContextMenu(menu)
        tray.activated.connect(
            lambda r: open_settings(pet, tray) if r == QSystemTrayIcon.DoubleClick else None)
        tray.show()
        logger.info('系统托盘图标已显示，程序就绪')

        # 每 5 分钟记录一次性能统计
        perf_timer = QTimer()
        perf_timer.timeout.connect(lambda: logger.log_performance())
        perf_timer.start(5 * 60 * 1000)  # 5 分钟

        app.exec_()
    except Exception as e:
        logger.critical(f'程序启动失败: {e}', exc_info=True)
        raise


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
