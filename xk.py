import base64
import json
import os
import random
import time
from typing import Any, Dict, List, Tuple

import login
import requests
import urllib3
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad

from serverchan import send_serverchan_notification

# 禁用安全警告
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ================= 配置区 =================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONF_FILE = os.path.join(BASE_DIR, "xk.conf")
COURSE_FILE = os.path.join(BASE_DIR, "course.conf")

# AES加密密钥 (从浏览器调试中获取的)
AES_KEY = "wHm1xj3afURghi0c"

# 选课批次代码 (ElectiveBatchCode)
# ⚠️ 注意：该代码会过期/变化，请在浏览器 Network -> Payload 中获取最新的
# 需求：不再在脚本内提供默认值；必须在 course.conf 中填写，否则直接报错退出。

# 抢课接口 URL
TARGET_URL = "https://xk.nju.edu.cn/xsxkapp/sys/xsxkapp/elective/volunteer.do"


def _load_json(path: str) -> Any:
    if not os.path.exists(path):
        raise FileNotFoundError(f"文件未找到: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_json_atomic(path: str, data: Any) -> None:
    """原子写入，避免写一半崩溃导致文件损坏。"""
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)


def load_config() -> Dict[str, Any]:
    """加载 xk.conf（JSON）。"""
    return _load_json(CONF_FILE)


def load_course_conf() -> Tuple[str, List[Tuple[str, str, str, str]]]:
    """加载 course.conf（新格式，不兼容老格式）。

    course.conf 必须是 JSON 对象：
    {
      "electiveBatchCode": "...",
      "courses": [
        ["teachingClassId", "courseKind", "teachingClassType", "备注"],
        ...
      ]
    }

    第 4 项为备注（可选，可为空字符串）。
    要求：electiveBatchCode 必填；未填直接报错退出（不提供默认值）。
    """
    raw = _load_json(COURSE_FILE)
    if not isinstance(raw, dict):
        raise ValueError("course.conf 必须是 JSON 对象，包含 electiveBatchCode 和 courses")

    elective_batch_code = str(raw.get("electiveBatchCode") or "").strip()
    if not elective_batch_code:
        raise ValueError("course.conf 缺少 electiveBatchCode（选课批次），请填写后再运行")

    raw_courses = raw.get("courses")
    if not isinstance(raw_courses, list):
        raise ValueError("course.conf 缺少 courses 数组")

    courses: List[Tuple[str, str, str, str]] = []
    for i, item in enumerate(raw_courses):
        if not (isinstance(item, (list, tuple)) and len(item) in (3, 4)):
            raise ValueError(
                f"course.conf courses 第 {i+1} 项必须是 [teachingClassId, courseKind, teachingClassType, 备注(可选)]，实际: {item}"
            )
        class_id, kind, ctype = item[0], item[1], item[2]
        remark = str(item[3]).strip() if len(item) >= 4 and item[3] else ""
        if not (class_id and kind and ctype):
            raise ValueError(f"course.conf courses 第 {i+1} 项缺少字段: {item}")
        courses.append((str(class_id), str(kind), str(ctype), remark))

    return elective_batch_code, courses


def save_course_conf(elective_batch_code: str, courses: List[Tuple[str, str, str, str]]) -> None:
    """保存 course.conf（新格式）。"""
    data = {
        "electiveBatchCode": str(elective_batch_code).strip(),
        "courses": [[c[0], c[1], c[2], c[3]] for c in courses],
    }
    _save_json_atomic(COURSE_FILE, data)


def load_courses() -> List[Tuple[str, str, str, str]]:
    """仅返回课程列表（用于兼容脚本内部旧调用）。"""
    _, courses = load_course_conf()
    return courses


def remove_course_from_file(course: Tuple[str, str, str, str]) -> bool:
    """从 course.conf 删除某门课。"""
    try:
        elective_batch_code, courses = load_course_conf()
    except Exception as e:
        print(f"!!! 删除课程失败：读取 course.conf 异常: {e}")
        return False

    before = len(courses)
    courses = [c for c in courses if c != course]
    after = len(courses)

    if after == before:
        return False

    try:
        save_course_conf(elective_batch_code, courses)
        return True
    except Exception as e:
        print(f"!!! 删除课程失败：写入 course.conf 异常: {e}")
        return False


def encrypt_add_param(payload_dict: Dict[str, Any]) -> str:
    """AES 加密 addParam。

    逻辑：JSON序列化(去空格) -> 拼接错误拼写的 timestrap -> AES-ECB 加密 -> Base64
    """
    json_str = json.dumps(payload_dict, separators=(",", ":"))
    timestamp = int(time.time() * 1000)
    text_to_encrypt = f"{json_str}?timestrap={timestamp}"

    key_bytes = AES_KEY.encode("utf-8")
    cipher = AES.new(key_bytes, AES.MODE_ECB)

    padded_data = pad(text_to_encrypt.encode("utf-8"), AES.block_size)
    encrypted_bytes = cipher.encrypt(padded_data)
    encrypted_b64 = base64.b64encode(encrypted_bytes).decode("utf-8")
    return encrypted_b64


def build_headers(token: str) -> Dict[str, str]:
    return {
        "Host": "xk.nju.edu.cn",
        "Connection": "keep-alive",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "X-Requested-With": "XMLHttpRequest",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36"
        ),
        "token": token,
        "Origin": "https://xk.nju.edu.cn",
        "Referer": f"https://xk.nju.edu.cn/xsxkapp/sys/xsxkapp/*default/grablessons.do?token={token}",
        "Accept-Encoding": "gzip, deflate, br, zstd",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "sec-ch-ua": '"Not(A:Brand";v="8", "Chromium";v="144", "Google Chrome";v="144"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
    }



def is_illegal_request(res_json: Dict[str, Any] | None, raw_text: str) -> bool:
    if res_json and str(res_json.get("msg", "")).find("非法请求") != -1:
        return True
    if "非法请求" in (raw_text or ""):
        return True
    return False


def do_select_one(
    *,
    student_code: str,
    elective_batch_code: str,
    course: Tuple[str, str, str, str],
    session_cookies: Dict[str, str],
    headers: Dict[str, str],
    proxies: Dict[str, str] | None,
) -> Tuple[Dict[str, Any] | None, str]:
    """对单门课发起一次 volunteer.do 请求。返回 (json_or_none, raw_text)。"""
    class_id, kind, ctype = course[0], course[1], course[2]

    payload_source = {
        "data": {
            "operationType": "1",
            "studentCode": student_code,
            "electiveBatchCode": elective_batch_code,
            "teachingClassId": class_id,
            "courseKind": kind,
            "teachingClassType": ctype,
        }
    }

    encrypted_param = encrypt_add_param(payload_source)
    form_data = {"addParam": encrypted_param}

    r = requests.post(
        TARGET_URL,
        cookies=session_cookies,
        headers=headers,
        data=form_data,
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
    # 0. 加载 xk.conf
    try:
        config = load_config()
        student_code = str(config.get("USER") or "").strip()
        proxy_url = (config.get("PROXY") or "").strip() or None

        if not student_code:
            print("❌ xk.conf 中缺少 USER (学号/统一认证用户名)")
            return

    except Exception as e:
        print(f"❌ 读取 xk.conf 失败: {e}")
        return

    # 1. 代理设置
    # 清除 V2RayN 等工具注入的系统代理环境变量，防止流量被劫持
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
        os.environ.pop(var, None)

    proxies = None
    if proxy_url:
        # socks5 -> socks5h 使 DNS 也走代理解析
        if proxy_url.startswith("socks5://"):
            proxy_url = proxy_url.replace("socks5://", "socks5h://", 1)
        proxies = {"http": proxy_url, "https": proxy_url}
        print(f">>> 启用代理: {proxy_url}")

    # 1.5 预检查 course.conf（避免还没开始就先登录）
    try:
        _elective_batch_code, _ = load_course_conf()
    except Exception as e:
        print(f"❌ 读取 course.conf 失败: {e}")
        return

    # 2. 获取 Session
    print(">>> 正在获取登录凭证...")
    session_cookies, token = login.get_session()
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
            print(f"\n[{idx}/{len(courses)}] 班级ID={class_id}, courseKind={kind}, teachingClassType={ctype}{remark_str}")

            # --- 第一次请求 ---
            try:
                res_json, raw = do_select_one(
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

            # --- 登录失效：非法请求 -> 重新 get_session 并重试一次 ---
            if is_illegal_request(res_json, raw):
                print("    ⚠️ 检测到‘非法请求’，重新获取登录凭证并重试...")
                session_cookies, token = login.get_session()  # 需求：很简单一句话
                if not (session_cookies and token):
                    print("    ❌ 重新获取登录凭证失败，跳过本次，下一轮再试")
                    time.sleep(random.uniform(1, 3))
                    continue
                headers = build_headers(token)

                try:
                    res_json, raw = do_select_one(
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

            # --- 解析结果 ---
            msg = None
            if isinstance(res_json, dict):
                msg = res_json.get("msg")

            if msg == "该课程超过课容量":
                print("    >>> 该课程超过课容量，继续运行...")

            elif msg in (None, "", "null"):
                # 需求：msg 为空视为选到
                now_str = time.strftime("%Y-%m-%d %H:%M:%S")
                print(f"    ✅ 选课成功（msg为空）: {class_id} ({ctype}) @ {now_str}")

                desp = (
                    f"teachingClassId: {class_id}\n"
                    f"courseKind: {kind}\n"
                    f"teachingClassType: {ctype}\n"
                    f"time: {now_str}"
                )
                if remark:
                    desp += f"\n备注: {remark}"
                send_serverchan_notification("✅ 选课成功", desp)

                removed = remove_course_from_file(course)
                if removed:
                    print("    >>> 已从 course.conf 删除该课程，后续不再请求")
                else:
                    print("    !!! 选课成功但未能从 course.conf 删除（可能文件已被改动/不存在该条目）")

                # 如果删完了，立刻退出
                try:
                    _, left_courses = load_course_conf()
                except Exception as e:
                    print(f"❌ 读取 course.conf 失败: {e}")
                    return

                if not left_courses:
                    print(">>> 所有课程已完成，退出。")
                    return

            else:
                # 其它情况：打印结果摘要
                if res_json is not None:
                    print(f"    >>> 返回: {res_json}")
                else:
                    print(f"    >>> 返回(非JSON): {str(raw)[:200]}...")

            # 每门课请求间隔 1~3 秒
            time.sleep(random.uniform(1, 3))

        # 每轮结束间隔 30~120 秒
        try:
            _, left_courses = load_course_conf()
        except Exception as e:
            print(f"❌ 读取 course.conf 失败: {e}")
            return

        if not left_courses:
            print(">>> 所有课程已完成，退出。")
            return

        sleep_s = random.uniform(30, 90)
        print(f"\n>>> 本轮结束，休息 {sleep_s:.1f}s 后进入下一轮...")
        time.sleep(sleep_s)


if __name__ == "__main__":
    main()
