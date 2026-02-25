"""
Server 酱推送通知。依赖可选：没装 serverchan-sdk 不影响主程序。
"""

import json
import os

try:
    from serverchan_sdk import sc_send
except ImportError:
    sc_send = None

_CONF_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", "xk.conf"
)


def _safe_load_config():
    try:
        with open(_CONF_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def send_serverchan_notification(title: str, desp: str = "") -> bool:
    """通知是附加功能：任何错误都吞掉，不抛异常。"""
    if not sc_send:
        return False

    config = _safe_load_config()
    sendkey = (config.get("SCT_KEY") or "").strip()
    if not sendkey:
        return False

    try:
        sc_send(sendkey, title, desp, options=config.get("SCT_OPTIONS"))
        return True
    except Exception as e:
        print(f"[serverchan] send failed: {e}")
        return False
