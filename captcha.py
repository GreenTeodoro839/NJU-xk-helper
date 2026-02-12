"""
captcha.py — 统一入口，从 xk.conf 读取 CAPTCHA_LEVEL 自动选择版本

CAPTCHA_LEVEL 可选值：
    fast     — 极速版  ~1.0s  28次OCR
    balanced — 均衡版  ~1.3s  48次OCR（默认）
    accurate — 精准版  ~1.6s  72次OCR
    max      — 最高版  ~8.0s  640次OCR + 轮廓匹配

用法:
    from captcha import solve_captcha_from_base64
"""

import json
import os

_LEVEL_MAP = {
    "fast": "captcha_fast",
    "balanced": "captcha_balanced",
    "accurate": "captcha_accurate",
    "max": "captcha_max",
}


def _load_level():
    conf_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "xk.conf")
    try:
        with open(conf_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        return cfg.get("CAPTCHA_LEVEL", "balanced").strip().lower()
    except Exception:
        return "balanced"


_level = _load_level()
_module_name = _LEVEL_MAP.get(_level, "captcha_balanced")

import importlib
_module = importlib.import_module(_module_name)
solve_captcha_from_base64 = _module.solve_captcha_from_base64