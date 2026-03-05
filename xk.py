"""南京大学选课助手 —— 循环抢课模式

自动登录 → 循环请求选课接口 → 抢到后推送通知并移除 → 直到全部完成。
"""

import json
import os
import random
import sys
import time
from typing import Any, Dict, Tuple

import requests
import urllib3

# 将项目根目录加入 sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lib.common import (
    load_xk_config,
    load_course_conf,
    remove_course_from_conf,
    encrypt_add_param,
    build_headers,
    build_proxies,
    clear_env_proxies,
    poll_process_result,
)
from lib.session_manager import acquire_session
from lib.serverchan import send_serverchan_notification

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

TARGET_URL = "https://xk.nju.edu.cn/xsxkapp/sys/xsxkapp/elective/volunteer.do"


def _is_illegal_request(res_json: Dict[str, Any] | None, raw_text: str) -> bool:
    if res_json and "非法请求" in str(res_json.get("msg", "")):
        return True
    return "非法请求" in (raw_text or "")


def _try_int(val):
    """纯数字字符串转 int，与浏览器前端 JSON 类型保持一致。"""
    try:
        return int(val)
    except (ValueError, TypeError):
        return val


def _do_select_one(
    *,
    student_code: str,
    elective_batch_code: str,
    course: Tuple[str, str, str, str],
    session_cookies: Dict[str, str],
    headers: Dict[str, str],
    proxies: Dict[str, str] | None,
) -> Tuple[Dict[str, Any] | None, str]:
    """对单门课发起一次选课请求。返回 (json_or_none, raw_text)。"""
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
        timeout=10,
    )
    r.encoding = "utf-8"
    try:
        return r.json(), r.text
    except Exception:
        return None, r.text


def main() -> None:
    # 0. 加载配置
    try:
        config = load_xk_config()
        student_code = str(config.get("USER") or "").strip()
        if not student_code:
            print("❌ xk.conf 中缺少 USER (学号)")
            return
        proxy_url = (config.get("PROXY") or "").strip() or None
    except Exception as e:
        print(f"❌ 读取 xk.conf 失败: {e}")
        return

    # 1. 代理设置
    clear_env_proxies()
    proxies = build_proxies(proxy_url)
    if proxies:
        print(f">>> 启用代理: {proxy_url}")

    # 1.5 预检查 course.conf
    try:
        load_course_conf()
    except Exception as e:
        print(f"❌ 读取 course.conf 失败: {e}")
        return

    # 2. 获取 Session
    print(">>> 正在获取登录凭证...")
    session_cookies, token = acquire_session()
    if not (session_cookies and token):
        print(">>> 登录失败或 Session 无效，无法继续。")
        return

    headers = build_headers(token)
    print(f">>> 凭证获取成功，Token: {str(token)[:10]}...")

    # 3. 循环抢课
    round_no = 0
    while True:
        try:
            elective_batch_code, courses = load_course_conf()
        except Exception as e:
            print(f"❌ 读取 course.conf 失败: {e}")
            return

        if not courses:
            print(">>> course.conf 已无课程（可能都抢到了），退出。")
            return

        round_no += 1
        print(f"\n========== 第 {round_no} 轮，共 {len(courses)} 门课程 ==========")

        for idx, course in enumerate(list(courses), 1):
            class_id, kind, ctype, remark = course
            remark_str = f", 备注={remark}" if remark else ""
            print(f"\n[{idx}/{len(courses)}] 班级ID={class_id}, courseKind={kind}, "
                  f"teachingClassType={ctype}{remark_str}")

            # 发起请求
            try:
                res_json, raw = _do_select_one(
                    student_code=student_code,
                    elective_batch_code=elective_batch_code,
                    course=course,
                    session_cookies=session_cookies,
                    headers=headers,
                    proxies=proxies,
                )
            except Exception as e:
                print(f"    ❌ 请求发生网络错误: {e}")
                time.sleep(random.uniform(1, 3))
                continue

            # 登录失效检测与重试
            if _is_illegal_request(res_json, raw):
                print("    ⚠️ 检测到'非法请求'，重新获取登录凭证...")
                session_cookies, token = acquire_session()
                if not (session_cookies and token):
                    print("    ❌ 重新获取登录凭证失败，跳过本次")
                    time.sleep(random.uniform(1, 3))
                    continue
                headers = build_headers(token)

                try:
                    res_json, raw = _do_select_one(
                        student_code=student_code,
                        elective_batch_code=elective_batch_code,
                        course=course,
                        session_cookies=session_cookies,
                        headers=headers,
                        proxies=proxies,
                    )
                except Exception as e:
                    print(f"    ❌ 重试请求发生网络错误: {e}")
                    time.sleep(random.uniform(1, 3))
                    continue

            # 解析结果 —— 两步判断，与前端 initProcessInterval 逻辑一致
            msg = res_json.get("msg") if isinstance(res_json, dict) else None
            code = res_json.get("code") if isinstance(res_json, dict) else None

            if str(code) == "1":
                # volunteer.do 返回 code="1" 只表示请求已入队
                # 需要轮询 studentstatus.do 获取真正结果
                print(f"    ⏳ 请求已提交，轮询处理结果...")
                poll = poll_process_result(
                    student_code=student_code,
                    teaching_class_id=class_id,
                    session_cookies=session_cookies,
                    headers=headers,
                    proxies=proxies,
                )
                poll_code = str(poll.get("code", ""))
                poll_msg = poll.get("msg", "")

                if poll_code == "1":
                    now_str = time.strftime("%Y-%m-%d %H:%M:%S")
                    print(f"    ✅ 选课成功: {class_id} ({ctype}) @ {now_str}")
                    if poll_msg:
                        print(f"       服务器消息: {poll_msg}")

                    desp = (f"teachingClassId: {class_id}\ncourseKind: {kind}\n"
                            f"teachingClassType: {ctype}\ntime: {now_str}")
                    if remark:
                        desp += f"\n备注: {remark}"
                    send_serverchan_notification("✅ 选课成功", desp)

                    if remove_course_from_conf(course):
                        print("    >>> 已从 course.conf 删除该课程")
                    else:
                        print("    !!! 选课成功但未能从 course.conf 删除")

                    # 检查是否全部完成
                    try:
                        _, left = load_course_conf()
                        if not left:
                            print(">>> 所有课程已完成，退出。")
                            return
                    except Exception:
                        return

                elif poll_code == "-1":
                    print(f"    ❌ 选课失败: {poll_msg}")
                elif poll_code == "timeout":
                    print(f"    ⚠️ 轮询超时，未能确认结果: {poll_msg}")
                else:
                    print(f"    ⚠️ 轮询返回未知状态: code={poll_code}, msg={poll_msg}")

            elif str(code) == "302":
                print("    ⚠️ 会话已过期(302)，将在下一轮重新获取凭证")
                session_cookies, token = acquire_session()
                if session_cookies and token:
                    headers = build_headers(token)

            else:
                if res_json is not None:
                    print(f"    >>> 返回: {res_json}")
                else:
                    print(f"    >>> 返回(非JSON): {str(raw)[:200]}...")

            time.sleep(random.uniform(0.5, 1.2))

        # 每轮结束检查
        try:
            _, left = load_course_conf()
            if not left:
                print(">>> 所有课程已完成，退出。")
                return
        except Exception:
            return

        sleep_s = random.uniform(3, 8)
        print(f"\n>>> 本轮结束，休息 {sleep_s:.1f}s 后进入下一轮...")
        time.sleep(sleep_s)


if __name__ == "__main__":
    main()
