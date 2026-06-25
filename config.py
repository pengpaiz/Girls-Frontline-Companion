"""配置管理模块 — JSON 持久化，线程安全"""

import json
import os
import sys
from threading import Lock


def _get_base_dir():
    """获取程序根目录（兼容开发 / PyInstaller）"""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    else:
        return os.path.dirname(os.path.abspath(__file__))


CONFIG_DIR = _get_base_dir()
CONFIG_PATH = os.path.join(CONFIG_DIR, 'config.json')
_lock = Lock()

DEFAULTS = {
    'auto_start': False,           # 开机自启
    'character': 'an94_images',    # 当前角色（data 下的文件夹名）
    'always_on_top': True,         # 置顶
}


def load():
    """加载配置，缺失键用默认值补全"""
    with _lock:
        cfg = dict(DEFAULTS)
        try:
            if os.path.isfile(CONFIG_PATH):
                with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                cfg.update(data)
        except (json.JSONDecodeError, OSError):
            pass
        return cfg


def save(cfg: dict):
    """保存配置（只存与默认值不同的键）"""
    with _lock:
        delta = {k: v for k, v in cfg.items() if v != DEFAULTS.get(k)}
        with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
            json.dump(delta, f, indent=2, ensure_ascii=False)