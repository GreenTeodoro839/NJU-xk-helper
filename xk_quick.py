"""南京大学选课助手 —— 并发抢课模式

多线程并发提交选课请求，适合开放选课瞬间抢课。
需要先通过 xk.py 或 tools/input_cookie.py 生成 session_cache.json。

速率控制策略：
  - 全局令牌桶限制约 2~3 req/s（单次请求间隔 ≥0.35s）
  - 并发数限制为 3，利用 I/O 重叠加速而非暴力并发
  - 检测到 QoS（NPE/网络错误）时指数退避，最长 15s
  - 轮间间隔 2~4s，触发 QoS 后自动拉长
"""

import json
import os
import random
import sys
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Tuple

import requests
import urllib3

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lib.common import (
    SESSION_CACHE_FILE,
    load_xk_config,
    load_course_conf,
    load_json,
    encrypt_add_param,
    build_headers,
    build_proxies,
    clear_env_proxies,
    poll_process_result,
)
from lib.serverchan import send_serverchan_notification

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

TARGET_URL = "https://xk.nju.edu.cn/xsxkapp/sys/xsxkapp/elective/volunteer.do"

# ===== 速率控制 =====
MAX_WORKERS = 3            # 并发线程数（利用I/O重叠，不暴力并发）
MIN_INTERVAL = 0.35        # 全局最小请求间隔(秒)，约 2.8 req/s
BASE_ROUND_DELAY = (2, 4)  # 轮间随机延迟(秒)
QOS_BACKOFF_BASE = 3.0     # QoS 退避基础(秒)
QOS_BACKOFF_MAX = 15.0     # QoS 退避上限(秒)


class _RateLimiter:
    """简易令牌桶：保证全局请求间隔 ≥ min_interval 秒。"""

    def __init__(self, min_interval: float):
        self._min_interval = min_interval
        self._lock = threading.Lock()
        self._last_time = 0.0

    def acquire(self):
        with self._lock:
            now = time.monotonic()
            wait = self._last_time + self._min_interval - now
            if wait > 0:
                time.sleep(wait)
            self._last_time = time.monotonic()


_rate_limiter = _RateLimiter(MIN_INTERVAL)


def _is_session_expired(res_json: Dict[str, Any] | None) -> bool:
    """与前端 bh_utils.js / grablessons.min.js 保持一致的登录失效检测。

    前端两种判定方式:
    1. resp.loginURL 存在且非空  (bh_utils.js doAjax)
    2. resp.code == "302"         (grablessons.min.js)
    """
    if not isinstance(res_json, dict):
        return False
    login_url = res_json.get("loginURL")
    if login_url:
        return True
    if str(res_json.get("code", "")) == "302":
        return True
    return False


def _load_session_cache() -> Tuple[Dict[str, str], str]:
    data = load_json(SESSION_CACHE_FILE)
    token = data.get("token", "")
    if not token:
        raise ValueError("session_cache.json 中缺少 token")
    return data.get("cookies", {}), token


def _try_int(val):
    """纯数字字符串转 int，与浏览器前端 JSON 类型保持一致。"""
    try:
        return int(val)
    except (ValueError, TypeError):
        return val


def _do_select_one_task(
    student_code: str,
    elective_batch_code: str,
    course: Tuple[str, str, str, str],
    session_cookies: Dict[str, str],
    headers: Dict[str, str],
    proxies: Dict[str, str] | None,
) -> Dict[str, Any]:
    """单个线程执行的选课任务。"""
    # 通过全局令牌桶控速
    _rate_limiter.acquire()

    payload = {
        "data": {
            "operationType": "1",
            "studentCode": student_code,
            "electiveBatchCode": elective_batch_code,
            "teachingClassId": course[0],
            "courseKind": _try_int(course[1]),
            "teachingClassType": course[2],
        }
    }

    try:
        r = requests.post(
            TARGET_URL,
            cookies=session_cookies,
            headers=headers,
            data={
                "addParam": encrypt_add_param(payload),
                "studentCode": student_code,
            },
            proxies=proxies,
            verify=False,
            timeout=15,
        )
        r.encoding = "utf-8"
        try:
            return {"success": True, "json": r.json(), "course": course}
        except Exception:
            return {"success": True, "json": None, "course": course, "raw": r.text}
    except Exception as e:
        return {"success": False, "error": str(e), "course": course}


def main():
    try:
        config = load_xk_config()
        student_code = str(config.get("USER") or "").strip()
        proxy_url = (config.get("PROXY") or "").strip() or None

        clear_env_proxies()
        proxies = build_proxies(proxy_url)
        if proxies:
            print(f">>> 启用代理: {proxy_url}")

        elective_batch_code, courses_to_run = load_course_conf()
        print(f">>> 启动成功：内存加载 {len(courses_to_run)} 门课程")
        print(f">>> 速率控制: {MAX_WORKERS} 并发, 最小间隔 {MIN_INTERVAL}s (~{1/MIN_INTERVAL:.1f} req/s)")
    except Exception as e:
        print(f"❌ 初始化失败: {e}")
        return

    round_no = 0
    qos_hit_count = 0  # 连续 QoS 触发次数，用于指数退避

    while courses_to_run:
        session_cookies, token = _load_session_cache()
        headers = build_headers(token)
        round_no += 1
        print(f"\n===== 第 {round_no} 轮 ({len(courses_to_run)} 门) =====")

        succeeded = []
        round_qos = False      # 本轮是否检测到 QoS
        session_expired = False # 本轮是否检测到登录失效

        with ThreadPoolExecutor(max_workers=min(len(courses_to_run), MAX_WORKERS)) as executor:
            futures = [
                executor.submit(
                    _do_select_one_task,
                    student_code, elective_batch_code, course,
                    session_cookies, headers, proxies,
                )
                for course in courses_to_run
            ]

            for future in as_completed(futures):
                res = future.result()
                course = res["course"]
                cid = course[0]

                if not res["success"]:
                    print(f"    [网络错误] {cid}: {res.get('error')}")
                    round_qos = True
                    continue

                res_json = res.get("json")

                # 非 JSON 响应
                if res_json is None:
                    raw = res.get("raw", "")
                    print(f"    [非JSON响应] {cid}: {str(raw)[:200]}...")
                    round_qos = True
                    continue

                # 登录失效检测（与前端 loginURL / code=302 逻辑一致）
                if _is_session_expired(res_json):
                    print(f"    [会话过期] {cid}: 检测到 loginURL/302")
                    session_expired = True
                    continue

                code = str(res_json.get("code", ""))
                msg = str(res_json.get("msg", ""))

                if code == "1":
                    # volunteer.do 返回 code="1" 只表示请求已入队
                    # 需要轮询 studentstatus.do 获取真正结果
                    print(f"    ⏳ [{cid}] 请求已提交，轮询处理结果...")
                    poll = poll_process_result(
                        student_code=student_code,
                        teaching_class_id=cid,
                        session_cookies=session_cookies,
                        headers=headers,
                        proxies=proxies,
                    )
                    poll_code = str(poll.get("code", ""))
                    poll_msg = poll.get("msg", "")

                    if poll_code == "1":
                        now_str = time.strftime("%H:%M:%S")
                        print(f"    🎉 [抢到了!] {cid} @ {now_str}")
                        if poll_msg:
                            print(f"       服务器消息: {poll_msg}")
                        class_id, kind, ctype, remark = course
                        desp = (f"teachingClassId: {class_id}\ncourseKind: {kind}\n"
                                f"teachingClassType: {ctype}\ntime: {now_str}")
                        if remark:
                            desp += f"\n备注: {remark}"
                        send_serverchan_notification(f"选课成功: {cid}", desp)
                        succeeded.append(course)
                    elif poll_code == "-1":
                        print(f"    ❌ [选课失败] {cid}: {poll_msg}")
                    elif poll_code == "timeout":
                        print(f"    ⚠️ [轮询超时] {cid}: {poll_msg}")
                    else:
                        print(f"    ⚠️ [轮询未知状态] {cid}: code={poll_code}, msg={poll_msg}")

                else:
                    # 非 code=1 的情况，打印完整返回方便调试
                    if "NullPointer" in msg:
                        print(f"    [服务器繁忙/QoS] {cid} (NPE)")
                        round_qos = True
                    else:
                        print(f"    >>> [{cid}] 返回: {res_json}")

        if succeeded:
            courses_to_run = [c for c in courses_to_run if c not in succeeded]
            print(f"    >>> 本轮抢到 {len(succeeded)} 门")

        # 登录失效：重新加载 session_cache 后立即重试，不等待
        if session_expired:
            print("    ⚠️ 本轮检测到会话过期，重新加载 session_cache.json...")
            try:
                session_cookies, token = _load_session_cache()
                headers = build_headers(token)
                print(f"    >>> 凭证已刷新，Token: {str(token)[:10]}...")
            except Exception as e:
                print(f"    ❌ 重新加载凭证失败: {e}")
            time.sleep(random.uniform(0.5, 1.5))
            continue

        # 自适应轮间延迟
        if round_qos:
            qos_hit_count += 1
            backoff = min(QOS_BACKOFF_BASE * (2 ** (qos_hit_count - 1)), QOS_BACKOFF_MAX)
            jitter = random.uniform(0, backoff * 0.3)
            delay = backoff + jitter
            print(f"    ⚠️ 检测到 QoS/繁忙，退避 {delay:.1f}s (第 {qos_hit_count} 次)")
        else:
            qos_hit_count = max(0, qos_hit_count - 1)  # 成功一轮，逐步恢复
            delay = random.uniform(*BASE_ROUND_DELAY)

        time.sleep(delay)

    print(">>> ✅ 全部完成，退出。")


if __name__ == "__main__":
    main()
