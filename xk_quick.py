# -*- coding: utf-8 -*-
"""南京大学选课抢课脚本（内存驻留+防并发冲突版）

修复点：
针对“有时正常有时空指针”的问题，增加了微秒级随机抖动，
避免同一瞬间击穿服务器后端的处理逻辑。
"""

import base64
import json
import os
import random
import time
from typing import Any, Dict, List, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
import urllib3
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad

from serverchan import send_serverchan_notification

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ================= 配置区 =================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONF_FILE = os.path.join(BASE_DIR, "xk.conf")
COURSE_FILE = os.path.join(BASE_DIR, "course.conf")
SESSION_FILE = os.path.join(BASE_DIR, "session_cache.json")

AES_KEY = "wHm1xj3afURghi0c"
TARGET_URL = "https://xk.nju.edu.cn/xsxkapp/sys/xsxkapp/elective/volunteer.do"


def _load_json(path: str) -> Any:
    if not os.path.exists(path):
        raise FileNotFoundError(f"文件未找到: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_config() -> Dict[str, Any]:
    return _load_json(CONF_FILE)


def load_session_cache() -> Tuple[Dict[str, str], str]:
    if not os.path.exists(SESSION_FILE):
        raise FileNotFoundError(f"找不到 {SESSION_FILE}")
    data = _load_json(SESSION_FILE)
    cookies = data.get("cookies", {})
    token = data.get("token", "")
    if not token:
        raise ValueError("session_cache.json 中缺少 token")
    return cookies, token


def load_course_conf() -> Tuple[str, List[Tuple[str, str, str, str]]]:
    raw = _load_json(COURSE_FILE)
    elective_batch_code = str(raw.get("electiveBatchCode") or "").strip()
    raw_courses = raw.get("courses", [])
    courses = []
    for item in raw_courses:
        if isinstance(item, (list, tuple)) and len(item) in (3, 4):
            remark = str(item[3]).strip() if len(item) >= 4 and item[3] else ""
            courses.append((str(item[0]), str(item[1]), str(item[2]), remark))
    return elective_batch_code, courses


def encrypt_add_param(payload_dict: Dict[str, Any]) -> str:
    json_str = json.dumps(payload_dict, separators=(",", ":"))
    timestamp = int(time.time() * 1000)
    text_to_encrypt = f"{json_str}?timestrap={timestamp}"
    key_bytes = AES_KEY.encode("utf-8")
    cipher = AES.new(key_bytes, AES.MODE_ECB)
    padded_data = pad(text_to_encrypt.encode("utf-8"), AES.block_size)
    return base64.b64encode(cipher.encrypt(padded_data)).decode("utf-8")


def build_headers(token: str) -> Dict[str, str]:
    return {
        "Host": "xk.nju.edu.cn",
        "Connection": "keep-alive",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36",
        "token": token,
        "Referer": f"https://xk.nju.edu.cn/xsxkapp/sys/xsxkapp/*default/grablessons.do?token={token}",
    }


def do_select_one_task(
        student_code: str,
        elective_batch_code: str,
        course: Tuple[str, str, str, str],
        session_cookies: Dict[str, str],
        headers: Dict[str, str],
        proxies: Dict[str, str] | None,
) -> Dict[str, Any]:
    # === 关键修改：随机微小抖动 ===
    # 避免所有请求在同一毫秒击中服务器，导致后端 Race Condition 报 NullPointer
    time.sleep(random.uniform(0.01, 0.1))

    class_id, kind, ctype = course[0], course[1], course[2]
    payload = {
        "data": {
            "operationType": "1",
            "studentCode": student_code,
            "electiveBatchCode": elective_batch_code,
            "teachingClassId": class_id,
            "courseKind": kind,
            "teachingClassType": ctype,
        }
    }

    try:
        form_data = {"addParam": encrypt_add_param(payload)}
        r = requests.post(
            TARGET_URL,
            cookies=session_cookies,
            headers=headers,
            data=form_data,
            proxies=proxies,
            verify=False,
            timeout=15,
        )
        r.encoding = "utf-8"
        try:
            return {"success": True, "json": r.json(), "course": course}
        except:
            return {"success": True, "json": None, "course": course, "raw": r.text}
    except Exception as e:
        return {"success": False, "error": str(e), "course": course}


def main():
    try:
        config = load_config()
        student_code = str(config.get("USER") or "").strip()
        proxy_url = (config.get("PROXY") or "").strip() or None

        # 清除 V2RayN 等工具注入的系统代理环境变量，防止流量被劫持
        for var in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
            os.environ.pop(var, None)

        if proxy_url:
            # socks5 -> socks5h 使 DNS 也走代理解析
            if proxy_url.startswith("socks5://"):
                proxy_url = proxy_url.replace("socks5://", "socks5h://", 1)
            print(f">>> 启用代理: {proxy_url}")
        proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None

        elective_batch_code, courses_to_run = load_course_conf()
        print(f">>> 启动成功：内存加载 {len(courses_to_run)} 门课程")
    except Exception as e:
        print(f"❌ 初始化失败: {e}")
        return

    round_no = 0
    while True:
        if not courses_to_run:
            print(">>> ✅ 全部完成，退出。")
            break
        session_cookies, token = load_session_cache()
        headers = build_headers(token)
        round_no += 1
        print(f"\n===== 第 {round_no} 轮 ({len(courses_to_run)} 门) =====")

        succeeded_courses = []
        tasks = []

        # 线程数不要超过课程数，也不要过大导致服务器封禁
        with ThreadPoolExecutor(max_workers=min(len(courses_to_run), 15)) as executor:
            for course in courses_to_run:
                tasks.append(executor.submit(
                    do_select_one_task,
                    student_code,
                    elective_batch_code,
                    course,
                    session_cookies,
                    headers,
                    proxies
                ))

            for future in as_completed(tasks):
                res = future.result()
                course = res["course"]
                cid = course[0]

                if not res["success"]:
                    print(f"    [网络错误] {cid}: {res.get('error')}")
                    continue

                res_json = res.get("json")
                msg = ""
                if isinstance(res_json, dict):
                    msg = str(res_json.get("msg", ""))

                # 判断逻辑
                if msg in ("null", "None", "") and isinstance(res_json, dict):
                    now_str = time.strftime("%H:%M:%S")
                    print(f"    🎉 [抢到了!] {cid} @ {now_str}")
                    remark = course[3] if len(course) >= 4 else ""
                    desp = f"ID: {cid}\n{now_str}"
                    if remark:
                        desp += f"\n备注: {remark}"
                    send_serverchan_notification(f"选课成功: {cid}", desp)
                    succeeded_courses.append(course)

                elif "NullPointer" in msg:
                    # 针对 NPE，视为“服务器繁忙”，不报错，只是默默跳过
                    print(f"    [服务器繁忙] {cid} (NPE重试)")

                elif "超过课容量" in msg:
                    print(f"    [满员] {cid}")

                else:
                    print(f"    [其他] {cid}: {msg}")

        if succeeded_courses:
            courses_to_run = [c for c in courses_to_run if c not in succeeded_courses]
            print(f"    >>> 本轮抢到 {len(succeeded_courses)} 门")

        time.sleep(random.uniform(0.1, 0.8))


if __name__ == "__main__":
    main()