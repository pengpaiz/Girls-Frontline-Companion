"""配置管理模块 — JSON 持久化"""

import json
import os
import sys
from threading import Lock


def _get_base_dir():
    """程序根目录（data/、logo.ico 等资源的父目录）"""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    else:
        return os.path.dirname(os.path.abspath(__file__))


CONFIG_DIR = _get_base_dir()                          # 资源目录
CONFIG_PATH = os.path.join(                           # 用户配置 → %APPDATA%
    os.getenv('APPDATA', os.path.expanduser('~')),
    'AuraPet', 'config.json')

_lock = Lock()

DEFAULTS = {
    'auto_start': False,
    'character': 'ar15',
    'always_on_top': True,
    'pos_x': None,
    'pos_y': None,
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
        os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
        with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
            json.dump(delta, f, indent=2, ensure_ascii=False)