from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import requests

INDEX_URL = "https://xk.nju.edu.cn/xsxkapp/sys/xsxkapp/*default/index.do"
BATCH_URL = "https://xk.nju.edu.cn/xsxkapp/sys/xsxkapp/elective/batch.do"

# 只允许修改第二行这一种格式（可容忍空格、末尾逗号、不同引号/缩进）：
#   "electiveBatchCode": "xxxx",
LINE2_RE = re.compile(
    r'^(?P<prefix>\s*"electiveBatchCode"\s*:\s*")(?P<val>[^"]*)(?P<suffix>"\s*,?\s*)$'
)


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
    s.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Connection": "keep-alive",
        }
    )
    if proxy:
        s.proxies.update({"http": proxy, "https": proxy})
    return s


def fetch_batches(sess: requests.Session) -> list[dict]:
    # 2. 先访问 index.do 建立/保存 session cookie
    r1 = sess.get(INDEX_URL, timeout=20)
    r1.raise_for_status()

    # 3. 带着 session POST batch.do
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
        print("[!] 响应缺少 dataList，完整响应(截断)：")
        print(json.dumps(payload, ensure_ascii=False, indent=2)[:2000])
        sys.exit(1)

    batches: list[dict] = []
    for item in data_list:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        code = item.get("code")
        if name is None or code is None:
            continue
        name_s = str(name).strip()
        code_s = str(code).strip()
        if name_s and code_s:
            batches.append({"name": name_s, "code": code_s})

    if not batches:
        print("[!] dataList 里没有可用批次（缺 name/code）")
        sys.exit(1)

    return batches


def choose_batch(batches: list[dict]) -> dict:
    # 4. 从上往下打印所有存在的批次
    print("\n可用批次：")
    for i, b in enumerate(batches, start=1):
        print(f"{i}.{b['name']}")

    # 5. 输入数字校验
    while True:
        raw = input(f"\n请输入序号(1-{len(batches)}): ").strip()
        try:
            n = int(raw)
        except ValueError:
            print("输入不正确：不是数字，请重试。")
            continue
        if 1 <= n <= len(batches):
            return batches[n - 1]
        print("输入不正确：序号超出范围，请重试。")


def write_course_conf_line2(course_conf_path: Path, new_code: str) -> None:
    # 6. 固定修改 course.conf 第二行，否则报错
    try:
        text = course_conf_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        print(f"[!] 找不到文件：{course_conf_path}")
        sys.exit(1)

    # 保留原始换行符（\n / \r\n）
    lines = text.splitlines(keepends=True)
    if len(lines) < 2:
        print("[!] course.conf 行数不足 2 行，无法修改第二行")
        sys.exit(1)

    line2 = lines[1]
    # 去掉行尾换行再匹配，匹配后再把原始换行加回去
    m = LINE2_RE.match(line2.rstrip("\r\n"))
    if not m:
        print("[!] course.conf 第二行格式不符合预期，拒绝修改。")
        print("    期望类似：  \"electiveBatchCode\": \"xxxx\",")
        print("    实际第二行：" + line2.rstrip("\r\n"))
        sys.exit(1)

    # 保留原始行尾换行符
    eol = ""
    if line2.endswith("\r\n"):
        eol = "\r\n"
    elif line2.endswith("\n"):
        eol = "\n"

    new_line2 = f"{m.group('prefix')}{new_code}{m.group('suffix')}" + eol
    lines[1] = new_line2

    course_conf_path.write_text("".join(lines), encoding="utf-8")


def main() -> None:
    xk_conf_path = Path(__file__).parent.parent / "xk.conf"
    course_conf_path = Path(__file__).parent.parent / "course.conf"


    xk_conf = load_json(xk_conf_path)
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

    write_course_conf_line2(course_conf_path, chosen["code"])
    print(f"\n已写入 course.conf 第二行：electiveBatchCode = {chosen['code']}")


if __name__ == "__main__":
    main()
