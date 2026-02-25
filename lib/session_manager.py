"""
登录态管理器：负责 Session 的缓存、验证与刷新。

原 login.py，重命名以区分职责：
  - session_manager.py: 管理登录态生命周期（缓存/验证/刷新）
  - authenticator.py: 执行登录流程

对外接口: acquire_session(force_refresh=False) -> (cookies_dict, token) or (None, None)
"""

import json
import os
import time

import requests
import urllib3

from lib.common import (
    SESSION_CACHE_FILE,
    LOCK_FILE,
    load_xk_config,
    build_proxies,
)

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Session 本地缓存时间（秒），超过后强制联网检查
CACHE_TTL = 1800


def _is_session_active(cookies, token, student_id, proxies=None):
    """通过请求学生信息接口验证 Session 是否有效。"""
    url = f"https://xk.nju.edu.cn/xsxkapp/sys/xsxkapp/student/{student_id}.do"
    print(f">>> 正在验证登录状态...")

    headers = {
        "token": token,
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36",
        "X-Requested-With": "XMLHttpRequest",
    }

    try:
        res = requests.post(url, cookies=cookies, headers=headers,
                            timeout=5, verify=False, proxies=proxies)
        if res.status_code == 200:
            res_json = res.json()
            if res_json.get("msg") == "查询学生基础信息成功":
                print(">>> ✅ 登录状态有效")
                return True
            else:
                print(f">>> ❌ 验证失败，业务返回: {res_json.get('msg')}")
        else:
            print(f">>> ❌ 验证失败，HTTP状态码: {res.status_code}")
    except Exception as e:
        print(f">>> ⚠️ 验证请求异常: {e}")

    return False


def acquire_session(force_refresh=False):
    """获取可用的 Session 和 Token。

    1. 优先读取缓存并验证
    2. 缓存无效时加锁并调用 authenticator 重新登录

    Returns:
        (cookies_dict, token) 或 (None, None)
    """
    try:
        config = load_xk_config()
        student_id = config["USER"]
        proxies = build_proxies(config.get("PROXY"))
    except Exception as e:
        print(f"❌ 配置文件错误: {e}")
        return None, None

    # --- 1. 尝试读取并验证缓存 ---
    if not force_refresh and os.path.exists(SESSION_CACHE_FILE):
        try:
            with open(SESSION_CACHE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)

            if time.time() - data.get("timestamp", 0) < CACHE_TTL:
                if _is_session_active(data["cookies"], data["token"], student_id, proxies=proxies):
                    return data["cookies"], data["token"]
                else:
                    print(">>> 缓存校验未通过，准备重登...")
            else:
                print(">>> 缓存时间已超时，准备重登...")
        except Exception:
            print(">>> 缓存文件读取出错，准备重登...")

    # --- 2. 加锁登录 ---
    wait_count = 0
    while os.path.exists(LOCK_FILE):
        # 死锁保护
        if time.time() - os.path.getmtime(LOCK_FILE) > 180:
            print(">>> ⚠️ 检测到死锁，强制重置...")
            os.remove(LOCK_FILE)
            break

        print(f">>> 等待其他进程登录中... ({wait_count}s)")
        time.sleep(1)
        wait_count += 1

        # 等待期间若别人登好了，直接用
        if os.path.exists(SESSION_CACHE_FILE) and wait_count % 2 == 0:
            return acquire_session(force_refresh=False)

    # 创建锁
    with open(LOCK_FILE, "w") as f:
        f.write("LOCKED")

    try:
        print(">>> 🔄 调用认证器执行登录...")

        # 延迟导入避免循环依赖
        from lib.authenticator import perform_login

        cookies, token = perform_login()
        if cookies and token:
            with open(SESSION_CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump({
                    "cookies": cookies,
                    "token": token,
                    "timestamp": time.time(),
                }, f)
            print(">>> ✅ 新 Session 已保存")
            return cookies, token
        else:
            raise Exception("登录失败，未获取到凭证")

    except Exception as e:
        print(f"❌ 登录过程发生错误: {e}")
        return None, None

    finally:
        if os.path.exists(LOCK_FILE):
            os.remove(LOCK_FILE)


if __name__ == "__main__":
    print(">>> 开始测试 session_manager.py ...")
    c, t = acquire_session()
    if c and t:
        print(f"\n>>> 测试成功！Token: {t[:10]}...")
    else:
        print("\n>>> 测试失败。")
