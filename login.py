import os
import json
import time
import requests
import urllib3
import xklogin  # 确保 xklogin.py 在同级目录
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ================= 配置与常量 =================
CONF_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "xk.conf")
SESSION_FILE = "session_cache.json"
LOCK_FILE= "login.lock"

# Session 本地缓存时间 (秒)，超过这个时间强制联网检查
# 建议设为 1800 (30分钟)，因为通常 Session 有效期较短
CACHE_TTL = 1800


def load_config():
    """加载配置文件"""
    if not os.path.exists(CONF_FILE):
        raise FileNotFoundError(f"配置文件 {CONF_FILE} 未找到，请在同目录下创建。")
    with open(CONF_FILE, 'r', encoding='utf-8') as f:
        return json.load(f)


def _is_session_active(cookies, token, student_id, proxy=None):
    """
    【核心验证逻辑】
    通过请求学生个人信息接口来验证 Session 和 Token 是否依然有效
    """
    # 构造固定的验证 URL
    url = f"https://xk.nju.edu.cn/xsxkapp/sys/xsxkapp/student/{student_id}.do"

    print(f">>> 正在验证登录状态: {url} ...")

    # 构造请求头，根据抓包只需要 token 和 UA 即可
    headers = {
        "token": token,
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36",
        "X-Requested-With": "XMLHttpRequest"
    }

    # 【新增】构造 requests 库需要的 proxies 字典
    proxies = None
    if proxy:
        proxies = {
            "http": proxy,
            "https": proxy
        }
        # print(f">>> 验证请求使用代理: {proxy}")

    try:
        # 发送 POST 请求，【修改】增加 proxies 参数
        res = requests.post(url, cookies=cookies, headers=headers, timeout=5, verify=False, proxies=proxies)

        if res.status_code == 200:
            try:
                res_json = res.json()
                # 根据你提供的 response.txt，成功标志是 msg 为 "查询学生基础信息成功"
                if res_json.get("msg") == "查询学生基础信息成功":
                    print(">>> ✅ 登录状态有效")
                    return True
                else:
                    print(f">>> ❌ 验证失败，业务返回: {res_json.get('msg')}")
            except json.JSONDecodeError:
                print(">>> ❌ 验证失败，返回内容不是 JSON")
        else:
            print(f">>> ❌ 验证失败，HTTP状态码: {res.status_code}")

    except Exception as e:
        print(f">>> ⚠️ 验证请求异常: {e}")

    return False


def get_session(force_refresh=False):
    """
    【外部接口】获取可用的 Session 和 Token
    1. 优先读取缓存
    2. 检查缓存是否过期或无效 (调用 _is_session_active)
    3. 如果无效，加锁并调用 xklogin 重新登录
    """
    # 加载配置
    try:
        config = load_config()
        student_id = config["USER"]
        # 【新增】读取代理配置
        proxy_setting = config.get("PROXY")
    except Exception as e:
        print(f"❌ 配置文件错误: {e}")
        return None, None

    # --- 1. 尝试读取并验证缓存 ---
    if not force_refresh and os.path.exists(SESSION_FILE):
        try:
            with open(SESSION_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)

            # 检查是否太旧
            if time.time() - data.get("timestamp", 0) < CACHE_TTL:
                # 缓存时间虽然没过期，但在返回前做一次最终的联网“活体检测”
                # 【修改】传入 proxy_setting
                if _is_session_active(data["cookies"], data["token"], student_id, proxy=proxy_setting):
                    return data["cookies"], data["token"]
                else:
                    print(">>> 缓存校验未通过，准备重登...")
            else:
                print(">>> 缓存时间已超时，准备重登...")
        except Exception:
            print(">>> 缓存文件读取出错，准备重登...")

    # --- 2. 缓存不可用，进入加锁登录流程 ---

    # 简单的文件锁逻辑：如果锁存在，等待
    wait_count = 0
    while os.path.exists(LOCK_FILE):
        # 锁超时保护：如果锁文件存在超过 180 秒，认为是死锁，强制删除
        if time.time() - os.path.getmtime(LOCK_FILE) > 180:
            print(">>> ⚠️ 检测到死锁，强制重置...")
            os.remove(LOCK_FILE)
            break

        print(f">>> 等待其他进程登录中... ({wait_count}s)")
        time.sleep(1)
        wait_count += 1

        # 等待期间如果别人登好了，直接用
        if os.path.exists(SESSION_FILE) and wait_count % 2 == 0:
            # 递归调用自己去读缓存
            return get_session(force_refresh=False)

    # 创建锁
    with open(LOCK_FILE, 'w') as f:
        f.write("LOCKED")

    try:
        print(">>> 🔄 调用 xklogin 执行登录...")

        # 调用 xklogin.py 的 login 函数（参数均从 xk.conf 读取）
        cookies, token = xklogin.login()
        if cookies and token:
            # 登录成功，写入缓存
            with open(SESSION_FILE, 'w', encoding='utf-8') as f:
                json.dump({
                    "cookies": cookies,
                    "token": token,
                    "timestamp": time.time()
                }, f)
            print(">>> ✅ 新 Session 已保存")
            return cookies, token
        else:
            raise Exception("登录失败，未获取到凭证")

    except Exception as e:
        print(f"❌ 登录过程发生错误: {e}")
        return None, None

    finally:
        # 释放锁
        if os.path.exists(LOCK_FILE):
            os.remove(LOCK_FILE)


if __name__ == "__main__":
    # 测试代码
    print(">>> 开始测试 login.py ...")
    c, t = get_session()
    if c and t:
        print(f"\n>>> 测试成功！")
        print(f"Token: {t[:10]}...")
        print(f"Cookie JSESSIONID: {c.get('JSESSIONID')}")
    else:
        print("\n>>> 测试失败。")