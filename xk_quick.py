"""南京大学选课助手 —— 并发抢课模式

多线程并发提交选课请求，适合开放选课瞬间抢课。
需要先通过 xk.py 或 tools/input_cookie.py 生成 session_cache.json。
"""

import json
import os
import random
import sys
import time
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
    # 微小抖动避免同一瞬间击穿后端
    time.sleep(random.uniform(0.01, 0.1))

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
    except Exception as e:
        print(f"❌ 初始化失败: {e}")
        return

    round_no = 0
    while courses_to_run:
        session_cookies, token = _load_session_cache()
        headers = build_headers(token)
        round_no += 1
        print(f"\n===== 第 {round_no} 轮 ({len(courses_to_run)} 门) =====")

        succeeded = []

        with ThreadPoolExecutor(max_workers=min(len(courses_to_run), 15)) as executor:
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
                    continue

                res_json = res.get("json")
                code = str(res_json.get("code", "")) if isinstance(res_json, dict) else ""
                msg = str(res_json.get("msg", "")) if isinstance(res_json, dict) else ""

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
                        remark = course[3] if len(course) >= 4 else ""
                        desp = f"ID: {cid}\n{now_str}"
                        if remark:
                            desp += f"\n备注: {remark}"
                        send_serverchan_notification(f"选课成功: {cid}", desp)
                        succeeded.append(course)
                    elif poll_code == "-1":
                        print(f"    [选课失败] {cid}: {poll_msg}")
                    elif poll_code == "timeout":
                        print(f"    [轮询超时] {cid}: {poll_msg}")
                    else:
                        print(f"    [未知状态] {cid}: code={poll_code}, msg={poll_msg}")

                elif code == "302":
                    print(f"    [会话过期] {cid}")

                elif "NullPointer" in msg:
                    print(f"    [服务器繁忙] {cid} (NPE重试)")

                else:
                    print(f"    [拒绝] {cid}: {msg}")

        if succeeded:
            courses_to_run = [c for c in courses_to_run if c not in succeeded]
            print(f"    >>> 本轮抢到 {len(succeeded)} 门")

        time.sleep(random.uniform(0.1, 0.8))

    print(">>> ✅ 全部完成，退出。")


if __name__ == "__main__":
    main()
