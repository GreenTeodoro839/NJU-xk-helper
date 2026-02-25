"""手动导入浏览器 Cookie/Token 到 session_cache.json。"""

import json
import os
import sys
import time

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from lib.common import SESSION_CACHE_FILE

USEFUL_KEYS = {"_WEU", "JSESSIONID", "route"}


def get_input():
    cookie = input("请输入浏览器中的cookie: ").strip()
    token = input("请输入token: ").strip()
    return cookie, token


def parse_cookie(cookie_str):
    cookie_dict = {}
    for item in cookie_str.split(';'):
        item = item.strip()
        if '=' in item:
            k, v = item.split('=', 1)
            k = k.strip()
            if k in USEFUL_KEYS:
                cookie_dict[k] = v.strip()
    return cookie_dict


def write_session_cache(cookies, token):
    session = {
        "cookies": cookies,
        "token": token,
        "timestamp": time.time(),
    }
    with open(SESSION_CACHE_FILE, 'w', encoding='utf-8') as f:
        json.dump(session, f, ensure_ascii=False)
    return SESSION_CACHE_FILE


if __name__ == "__main__":
    cookie_raw, token = get_input()
    cookies = parse_cookie(cookie_raw)

    missing = USEFUL_KEYS - cookies.keys()
    if missing:
        print(f"⚠️ cookie 中缺少以下字段: {', '.join(missing)}")

    path = write_session_cache(cookies, token)
    print(f"✅ {path} 已更新，包含 cookie: {list(cookies.keys())}")
