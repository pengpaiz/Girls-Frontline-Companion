"""日志系统 — 文件日志 + 轮转 + 错误追踪 + 性能监控"""

import logging
import os
import sys
import time
from logging.handlers import RotatingFileHandler
from typing import Optional


def _get_log_dir() -> str:
    """获取日志目录"""
    if getattr(sys, 'frozen', False):
        # 打包后：日志在 exe 同级目录
        base_dir = os.path.dirname(sys.executable)
    else:
        # 开发时：日志在 config 目录
        base_dir = os.path.join(
            os.getenv('APPDATA', os.path.expanduser('~')),
            'AuraPet'
        )
    os.makedirs(base_dir, exist_ok=True)
    return base_dir


class PerformanceMonitor:
    """性能监控器"""
    def __init__(self):
        self._frame_times = []
        self._cache_hits = 0
        self._cache_misses = 0
        self._last_time = time.perf_counter()
        self._frame_count = 0

    def record_frame(self):
        """记录一帧的时间"""
        now = time.perf_counter()
        frame_time = now - self._last_time
        self._last_time = now
        self._frame_times.append(frame_time)
        self._frame_count += 1

        # 只保留最近 60 帧的数据
        if len(self._frame_times) > 60:
            self._frame_times.pop(0)

    def record_cache_hit(self):
        """记录缓存命中"""
        self._cache_hits += 1

    def record_cache_miss(self):
        """记录缓存未命中"""
        self._cache_misses += 1

    def get_stats(self) -> dict:
        """获取性能统计数据"""
        if not self._frame_times:
            return {
                'avg_fps': 0,
                'min_frame_ms': 0,
                'max_frame_ms': 0,
                'cache_hit_rate': 0,
                'total_frames': self._frame_count,
            }

        avg_frame_time = sum(self._frame_times) / len(self._frame_times)
        avg_fps = 1.0 / avg_frame_time if avg_frame_time > 0 else 0

        total_cache = self._cache_hits + self._cache_misses
        cache_hit_rate = (self._cache_hits / total_cache * 100) if total_cache > 0 else 0

        return {
            'avg_fps': round(avg_fps, 2),
            'min_frame_ms': round(min(self._frame_times) * 1000, 2),
            'max_frame_ms': round(max(self._frame_times) * 1000, 2),
            'avg_frame_ms': round(avg_frame_time * 1000, 2),
            'cache_hit_rate': round(cache_hit_rate, 1),
            'cache_hits': self._cache_hits,
            'cache_misses': self._cache_misses,
            'total_frames': self._frame_count,
        }

    def reset(self):
        """重置统计数据"""
        self._frame_times.clear()
        self._cache_hits = 0
        self._cache_misses = 0
        self._frame_count = 0
        self._last_time = time.perf_counter()


# 全局性能监控器实例
perf_monitor = PerformanceMonitor()


class Logger:
    """日志管理器"""
    _instance: Optional['Logger'] = None
    _initialized = False

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if Logger._initialized:
            return

        self._setup_logger()
        Logger._initialized = True

    def _setup_logger(self):
        """配置日志系统"""
        log_dir = _get_log_dir()
        log_file = os.path.join(log_dir, 'AuraPet.log')

        # 创建 logger
        self.logger = logging.getLogger('AuraPet')
        self.logger.setLevel(logging.DEBUG)

        # 避免重复添加 handler
        if self.logger.handlers:
            return

        # 文件 handler（轮转：10MB，保留 3 个备份）
        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=10 * 1024 * 1024,  # 10 MB
            backupCount=3,
            encoding='utf-8'
        )
        file_handler.setLevel(logging.DEBUG)

        # 控制台 handler（开发模式）
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)

        # 格式化器
        file_formatter = logging.Formatter(
            '%(asctime)s | %(levelname)-8s | %(name)s | %(funcName)s:%(lineno)d | %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        console_formatter = logging.Formatter(
            '%(asctime)s | %(levelname)-8s | %(message)s',
            datefmt='%H:%M:%S'
        )

        file_handler.setFormatter(file_formatter)
        console_handler.setFormatter(console_formatter)

        self.logger.addHandler(file_handler)
        self.logger.addHandler(console_handler)

        # 记录启动信息
        self.logger.info('=' * 60)
        self.logger.info('AuraPet 启动')
        self.logger.info(f'Python: {sys.version}')
        self.logger.info(f'平台: {sys.platform}')
        self.logger.info(f'日志文件: {log_file}')
        self.logger.info('=' * 60)

    def debug(self, msg: str, *args, **kwargs):
        """记录调试信息"""
        self.logger.debug(msg, *args, **kwargs)

    def info(self, msg: str, *args, **kwargs):
        """记录正常操作信息"""
        self.logger.info(msg, *args, **kwargs)

    def warning(self, msg: str, *args, **kwargs):
        """记录警告信息"""
        self.logger.warning(msg, *args, **kwargs)

    def error(self, msg: str, *args, exc_info=True, **kwargs):
        """记录错误信息（包含异常堆栈）"""
        self.logger.error(msg, *args, exc_info=exc_info, **kwargs)

    def critical(self, msg: str, *args, exc_info=True, **kwargs):
        """记录严重错误"""
        self.logger.critical(msg, *args, exc_info=exc_info, **kwargs)

    def log_performance(self):
        """记录性能统计"""
        stats = perf_monitor.get_stats()
        self.logger.info(
            f'性能统计 | FPS: {stats["avg_fps"]} | '
            f'帧时间: {stats["min_frame_ms"]}-{stats["max_frame_ms"]}ms '
            f'(avg {stats["avg_frame_ms"]}ms) | '
            f'缓存命中率: {stats["cache_hit_rate"]}% '
            f'({stats["cache_hits"]}/{stats["cache_hits"] + stats["cache_misses"]}) | '
            f'总帧数: {stats["total_frames"]}'
        )

    def get_log_file(self) -> str:
        """获取日志文件路径"""
        log_dir = _get_log_dir()
        return os.path.join(log_dir, 'AuraPet.log')


# 全局 logger 实例
logger = Logger()
