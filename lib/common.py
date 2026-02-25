"""
共享工具模块：配置加载、AES 加密、请求头构建、代理设置等。
"""

import base64
import json
import os
import time
from typing import Any, Dict, List, Tuple

from Crypto.Cipher import AES
from Crypto.Util.Padding import pad

# ================= 路径常量 =================
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONF_DIR = os.path.join(BASE_DIR, "config")
XK_CONF_FILE = os.path.join(CONF_DIR, "xk.conf")
COURSE_CONF_FILE = os.path.join(CONF_DIR, "course.conf")
SESSION_CACHE_FILE = os.path.join(CONF_DIR, "session_cache.json")
LOCK_FILE = os.path.join(CONF_DIR, "login.lock")

# AES 加密密钥（浏览器调试获取）
AES_KEY = "wHm1xj3afURghi0c"


# ================= JSON 读写 =================

def load_json(path: str) -> Any:
    if not os.path.exists(path):
        raise FileNotFoundError(f"文件未找到: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json_atomic(path: str, data: Any) -> None:
    """原子写入，避免写一半崩溃导致文件损坏。"""
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)


# ================= 配置加载 =================

def load_xk_config() -> Dict[str, Any]:
    """加载 xk.conf（JSON）。"""
    return load_json(XK_CONF_FILE)


def load_course_conf() -> Tuple[str, List[Tuple[str, str, str, str]]]:
    """加载 course.conf。

    返回 (electiveBatchCode, [(teachingClassId, courseKind, teachingClassType, 备注), ...])
    """
    raw = load_json(COURSE_CONF_FILE)
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
                f"course.conf courses 第 {i+1} 项格式错误: {item}"
            )
        class_id, kind, ctype = str(item[0]), str(item[1]), str(item[2])
        remark = str(item[3]).strip() if len(item) >= 4 and item[3] else ""
        if not (class_id and kind and ctype):
            raise ValueError(f"course.conf courses 第 {i+1} 项缺少字段: {item}")
        courses.append((class_id, kind, ctype, remark))

    return elective_batch_code, courses


def save_course_conf(elective_batch_code: str, courses: List[Tuple[str, str, str, str]]) -> None:
    """保存 course.conf。"""
    data = {
        "electiveBatchCode": str(elective_batch_code).strip(),
        "courses": [[c[0], c[1], c[2], c[3]] for c in courses],
    }
    save_json_atomic(COURSE_CONF_FILE, data)


def remove_course_from_conf(course: Tuple[str, str, str, str]) -> bool:
    """从 course.conf 删除某门课。"""
    try:
        elective_batch_code, courses = load_course_conf()
    except Exception as e:
        print(f"!!! 删除课程失败：读取 course.conf 异常: {e}")
        return False

    before = len(courses)
    courses = [c for c in courses if c != course]
    if len(courses) == before:
        return False

    try:
        save_course_conf(elective_batch_code, courses)
        return True
    except Exception as e:
        print(f"!!! 删除课程失败：写入 course.conf 异常: {e}")
        return False


# ================= AES 加密 =================

def encrypt_add_param(payload_dict: Dict[str, Any]) -> str:
    """AES 加密 addParam。"""
    json_str = json.dumps(payload_dict, separators=(",", ":"))
    timestamp = int(time.time() * 1000)
    text_to_encrypt = f"{json_str}?timestrap={timestamp}"

    key_bytes = AES_KEY.encode("utf-8")
    cipher = AES.new(key_bytes, AES.MODE_ECB)
    padded_data = pad(text_to_encrypt.encode("utf-8"), AES.block_size)
    return base64.b64encode(cipher.encrypt(padded_data)).decode("utf-8")


# ================= 请求头 / 代理 =================

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
    }


def build_proxies(proxy_url: str | None) -> Dict[str, str] | None:
    """构建 requests 需要的 proxies 字典，自动处理 socks5→socks5h。"""
    if not proxy_url or not proxy_url.strip():
        return None
    proxy_url = proxy_url.strip()
    if proxy_url.startswith("socks5://"):
        proxy_url = proxy_url.replace("socks5://", "socks5h://", 1)
    return {"http": proxy_url, "https": proxy_url}


def clear_env_proxies():
    """清除系统代理环境变量，防止 V2RayN 等工具注入的代理劫持流量。"""
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
        os.environ.pop(var, None)
