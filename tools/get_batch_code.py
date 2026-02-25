"""获取选课批次代码，写入 config/course.conf。"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import requests

INDEX_URL = "https://xk.nju.edu.cn/xsxkapp/sys/xsxkapp/*default/index.do"
BATCH_URL = "https://xk.nju.edu.cn/xsxkapp/sys/xsxkapp/elective/batch.do"

LINE2_RE = re.compile(
    r'^(?P<prefix>\s*"electiveBatchCode"\s*:\s*")(?P<val>[^"]*)(?P<suffix>"\s*,?\s*)$'
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
XK_CONF = PROJECT_ROOT / "config" / "xk.conf"
COURSE_CONF = PROJECT_ROOT / "config" / "course.conf"


def load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        print(f"[!] 找不到文件：{path}")
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"[!] JSON 解析失败：{path} -> {e}")
        sys.exit(1)


def build_session(proxy: str | None) -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Connection": "keep-alive",
    })
    if proxy:
        s.proxies.update({"http": proxy, "https": proxy})
    return s


def fetch_batches(sess: requests.Session) -> list[dict]:
    r1 = sess.get(INDEX_URL, timeout=20)
    r1.raise_for_status()

    r2 = sess.post(BATCH_URL, data={}, headers={"Referer": INDEX_URL}, timeout=20)
    r2.raise_for_status()

    try:
        payload = r2.json()
    except Exception:
        print("[!] batch.do 返回不是 JSON：")
        print(r2.text[:1200])
        sys.exit(1)

    data_list = payload.get("dataList")
    if not isinstance(data_list, list):
        print("[!] 响应缺少 dataList：")
        print(json.dumps(payload, ensure_ascii=False, indent=2)[:2000])
        sys.exit(1)

    batches = []
    for item in data_list:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        code = str(item.get("code", "")).strip()
        if name and code:
            batches.append({"name": name, "code": code})

    if not batches:
        print("[!] dataList 里没有可用批次")
        sys.exit(1)

    return batches


def choose_batch(batches: list[dict]) -> dict:
    print("\n可用批次：")
    for i, b in enumerate(batches, start=1):
        print(f"{i}.{b['name']}")

    while True:
        raw = input(f"\n请输入序号(1-{len(batches)}): ").strip()
        try:
            n = int(raw)
        except ValueError:
            print("输入不正确，请重试。")
            continue
        if 1 <= n <= len(batches):
            return batches[n - 1]
        print("序号超出范围，请重试。")


def write_course_conf_line2(course_conf_path: Path, new_code: str) -> None:
    try:
        text = course_conf_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        print(f"[!] 找不到文件：{course_conf_path}")
        sys.exit(1)

    lines = text.splitlines(keepends=True)
    if len(lines) < 2:
        print("[!] course.conf 行数不足 2 行")
        sys.exit(1)

    line2 = lines[1]
    m = LINE2_RE.match(line2.rstrip("\r\n"))
    if not m:
        print("[!] course.conf 第二行格式不符合预期")
        print("    期望类似：  \"electiveBatchCode\": \"xxxx\",")
        print("    实际第二行：" + line2.rstrip("\r\n"))
        sys.exit(1)

    eol = "\r\n" if line2.endswith("\r\n") else ("\n" if line2.endswith("\n") else "")
    lines[1] = f"{m.group('prefix')}{new_code}{m.group('suffix')}" + eol
    course_conf_path.write_text("".join(lines), encoding="utf-8")


def main() -> None:
    xk_conf = load_json(XK_CONF)
    proxy = xk_conf.get("PROXY")
    if proxy is not None:
        proxy = str(proxy).strip() or None
    sess = build_session(proxy)

    try:
        batches = fetch_batches(sess)
    except requests.RequestException as e:
        print(f"[!] 网络请求失败：{e}")
        sys.exit(1)

    chosen = choose_batch(batches)
    write_course_conf_line2(COURSE_CONF, chosen["code"])
    print(f"\n已写入 course.conf：electiveBatchCode = {chosen['code']}")


if __name__ == "__main__":
    main()
