import json
import os
import time

# 只保留这几个对选课系统有用的 cookie key
USEFUL_KEYS = {"_WEU", "JSESSIONID", "route"}


def get_input():
    cookie = input("请输入浏览器中的cookie: ").strip()
    token = input("请输入token: ").strip()
    return cookie, token


def parse_cookie(cookie_str):
    """解析 cookie 字符串，只保留有用的字段"""
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
    """按 login.py 的格式写入 session_cache.json"""
    session = {
        "cookies": cookies,
        "token": token,
        "timestamp": time.time()
    }
    cache_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'session_cache.json')
    with open(cache_path, 'w', encoding='utf-8') as f:
        json.dump(session, f, ensure_ascii=False)
    return cache_path


if __name__ == "__main__":
    cookie_raw, token = get_input()
    cookies = parse_cookie(cookie_raw)

    missing = USEFUL_KEYS - cookies.keys()
    if missing:
        print(f"⚠️ cookie 中缺少以下字段: {', '.join(missing)}")

    write_session_cache(cookies, token)
    print(f"✅ session_cache.json 已更新，包含 cookie: {list(cookies.keys())}")
