import json
import os

# 依赖是可选的：没装就不推送，但不影响主程序
try:
    from serverchan_sdk import sc_send
except ImportError:
    sc_send = None

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONF_FILE = os.path.join(BASE_DIR, "xk.conf")


def _safe_load_config():
    """读取配置失败就返回空 dict，不影响主流程。"""
    try:
        with open(CONF_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError:
        return {}
    except Exception:
        return {}


def send_serverchan_notification(title: str, desp: str = "") -> bool:
    """
    通知是附加功能：任何错误都吞掉。
    返回值：True=看起来发送成功；False=未发送/发送失败（但不抛异常）
    """
    if not sc_send:
        return False

    config = _safe_load_config()
    sendkey = (config.get("SCT_KEY") or "").strip()
    if not sendkey:
        return False

    options = config.get("SCT_OPTIONS")  # 没有就 None

    try:
        sc_send(sendkey, title, desp, options=options)
        return True
    except Exception as e:
        # 不影响主流程：只打印
        print(f"[serverchan] send failed: {e}")
        return False
