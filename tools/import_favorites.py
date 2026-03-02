# -*- coding: utf-8 -*-
"""从选课平台「收藏」列表直接导入课程到 course.conf。

用法：
  python tools/import_favorites.py          # 交互式选择导入
  python tools/import_favorites.py --all    # 一键导入全部收藏
"""

import json
import os
import sys

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# 将项目根目录加入 sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from lib.common import (
    XK_CONF_FILE,
    COURSE_CONF_FILE,
    load_json,
    build_headers,
    build_proxies,
    clear_env_proxies,
)
from lib.session_manager import acquire_session

BASE_URL = "https://xk.nju.edu.cn/xsxkapp/sys/xsxkapp"
QUERY_FAV_URL = f"{BASE_URL}/elective/queryfavorite.do"
STUDENT_URL = f"{BASE_URL}/student"


# ===================== 动态参数映射 =====================

def fetch_type_to_kind_map(student_code, cookies, token, proxies):
    """从学生信息接口获取 teachingClassType → courseKind 映射。

    queryfavorite.do 返回的收藏中 courseKind 为 None，
    但 teachingClassType 有值，需要通过此映射反推 courseKind。
    """
    url = f"{STUDENT_URL}/{student_code}.do"
    headers = build_headers(token)
    try:
        r = requests.post(url, cookies=cookies, headers=headers,
                          proxies=proxies, verify=False, timeout=10)
        r.encoding = "utf-8"
        data = r.json()
    except Exception as e:
        print(f"❌ 获取学生信息失败: {e}")
        return None

    batch_list = data.get("data", {}).get("electiveBatchList", [])
    if not batch_list:
        print("❌ 学生信息中未找到选课批次（electiveBatchList）")
        return None

    type_to_kind = {}  # teachingClassType → (courseKind, 类别名)
    for batch in batch_list:
        for m in batch.get("limitMenuList", []):
            course_kind = m.get("courseKind")
            menu_code = m.get("menuCode")
            menu_name = m.get("menuName") or m.get("engMenuName") or "?"
            if not course_kind or course_kind == "-" or not menu_code:
                continue
            if menu_code not in type_to_kind:
                type_to_kind[menu_code] = (course_kind, menu_name)

    if not type_to_kind:
        print("❌ 未能从 limitMenuList 构建映射")
        return None

    return type_to_kind


# ===================== 配置加载 =====================

def _load_config():
    conf = load_json(XK_CONF_FILE)
    student_code = str(conf.get("USER", "")).strip()
    proxy_url = (conf.get("PROXY") or "").strip() or None
    if not student_code:
        print("❌ xk.conf 中缺少 USER（学号）")
        sys.exit(1)

    course_conf = load_json(COURSE_CONF_FILE)
    batch_code = str(course_conf.get("electiveBatchCode", "")).strip()
    if not batch_code:
        print("❌ course.conf 中缺少 electiveBatchCode，请先运行 tools/get_batch_code.py")
        sys.exit(1)

    return student_code, batch_code, proxy_url


# ===================== 查询收藏 =====================

def fetch_all_favorites(student_code, batch_code, cookies, token, proxies):
    """通过 queryfavorite.do 拉取用户收藏课程列表。"""
    headers = build_headers(token)

    query_setting = json.dumps({
        "data": {
            "studentCode": student_code,
            "electiveBatchCode": batch_code,
        },
        "pageSize": "999",
        "pageNumber": "0",
        "order": "",
    }, ensure_ascii=False)

    try:
        r = requests.post(
            QUERY_FAV_URL,
            cookies=cookies,
            headers=headers,
            data={"querySetting": query_setting},
            proxies=proxies,
            verify=False,
            timeout=10,
        )
        r.encoding = "utf-8"
    except Exception as e:
        print(f"❌ 网络请求失败: {e}")
        return None

    text = r.text.strip()
    if not text:
        return None

    try:
        data = r.json()
    except Exception:
        if "非法请求" in text or "<html" in text.lower():
            return None
        print(f"❌ 响应解析失败: {text[:200]}")
        return None

    if isinstance(data, dict) and "非法请求" in str(data.get("msg", "")):
        return None

    data_list = data.get("dataList")
    if data_list is None:
        return []

    return data_list


# ===================== 写入 course.conf =====================

def _add_courses_to_conf(new_courses):
    """批量添加课程到 course.conf，返回 (成功数, 跳过数)。"""
    try:
        data = load_json(COURSE_CONF_FILE)
    except Exception:
        data = {"electiveBatchCode": "", "courses": []}

    existing = data.get("courses", [])
    existing_ids = {c[0] for c in existing}

    added = 0
    skipped = 0
    for course_entry in new_courses:
        class_id = course_entry[0]
        if class_id in existing_ids:
            skipped += 1
            continue
        existing.append(course_entry)
        existing_ids.add(class_id)
        added += 1

    data["courses"] = existing

    # 写入文件（保持与其他工具一致的格式）
    batch = json.dumps(data.get("electiveBatchCode", ""), ensure_ascii=False)
    lines = ["{", f'  "electiveBatchCode": {batch},', '  "courses": [']
    for i, c in enumerate(existing):
        row = json.dumps(c, ensure_ascii=False)
        comma = "," if i < len(existing) - 1 else ""
        lines.append(f"    {row}{comma}")
    lines.extend(["  ]", "}"])

    tmp = COURSE_CONF_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    os.replace(tmp, COURSE_CONF_FILE)

    return added, skipped


# ===================== 主逻辑 =====================

def main():
    import_all = "--all" in sys.argv

    student_code, batch_code, proxy_url = _load_config()
    proxies = build_proxies(proxy_url)
    clear_env_proxies()

    # 1. 登录
    print(">>> 正在获取登录凭证...")
    cookies, token = acquire_session()
    if not (cookies and token):
        print("❌ 登录失败，无法继续")
        sys.exit(1)
    print(">>> 登录成功\n")

    # 2. 获取 teachingClassType → courseKind 映射
    print(">>> 正在获取课程参数映射...")
    type_to_kind = fetch_type_to_kind_map(student_code, cookies, token, proxies)
    if type_to_kind is None:
        print("❌ 无法获取课程参数映射，退出")
        sys.exit(1)
    print(f">>> 成功获取 {len(type_to_kind)} 条类别映射\n")

    # 3. 拉取收藏列表
    print(">>> 正在拉取收藏列表...")
    all_favs = fetch_all_favorites(student_code, batch_code, cookies, token, proxies)

    if all_favs is None:
        # Session 可能失效，尝试重登
        print(">>> Session 可能失效，尝试重新登录...")
        cookies, token = acquire_session(force_refresh=True)
        if not (cookies and token):
            print("❌ 重新登录失败")
            sys.exit(1)
        all_favs = fetch_all_favorites(student_code, batch_code, cookies, token, proxies)
        if all_favs is None:
            print("❌ 拉取收藏列表失败")
            sys.exit(1)

    if not all_favs:
        print("收藏列表为空，没有需要导入的课程。")
        return

    # 4. 读取已有课程 ID，用于标记和自动跳过
    try:
        existing_data = load_json(COURSE_CONF_FILE)
        existing_ids = {c[0] for c in existing_data.get("courses", [])}
    except Exception:
        existing_ids = set()

    # 5. 展示收藏列表
    print(f"\n{'='*70}")
    print(f"  收藏列表（共 {len(all_favs)} 门）")
    print(f"{'='*70}")

    prepared = []  # [(index, class_id, kind, ctype, remark, display_name)]
    already_count = 0
    invalid_count = 0
    for i, c in enumerate(all_favs, 1):
        name = c.get("courseName", "?")
        teacher = c.get("teacherName", "?")
        place = c.get("teachingPlace", "?")
        credit = c.get("credit", "?")
        class_id = c.get("teachingClassID", "?")
        ctype = c.get("teachingClassType", "")

        # 从 teachingClassType 反推 courseKind
        if ctype and ctype in type_to_kind:
            kind, ctype_name = type_to_kind[ctype]
        else:
            kind = "??"
            ctype_name = "未知类别"

        # 生成备注
        place_short = place[:37] + "..." if len(place) > 40 else place
        remark = f"{name}/{teacher}/{place_short}"

        # 判断状态
        is_existing = class_id in existing_ids
        if is_existing:
            tag = " ✔ 已在配置中"
            already_count += 1
        elif kind == "??":
            tag = " ⚠️ 无法映射类别"
            invalid_count += 1
        else:
            tag = ""

        print(f"\n  [{i:>2}] {name}  ({credit}学分, {ctype_name}){tag}")
        print(f"       教师: {teacher}  |  时间: {place}")
        print(f"       ID: {class_id}  |  courseKind: {kind}  |  type: {ctype}")

        prepared.append((i, class_id, kind, ctype, remark, name))

    # 过滤掉已有课程和无法映射的课程
    valid = [
        (idx, cid, k, ct, rm, nm)
        for idx, cid, k, ct, rm, nm in prepared
        if k != "??" and cid not in existing_ids
    ]

    print(f"\n{'─'*70}")
    if already_count > 0:
        print(f"  ✔ {already_count} 门课程已在配置中，自动跳过")
    if invalid_count > 0:
        print(f"  ⚠️ {invalid_count} 门课程无法映射类别，自动跳过")

    if not valid:
        print("  没有可导入的课程。")
        return

    # 6. 选择导入
    if import_all:
        selected = valid
        print(f"\n  --all 模式：将导入全部 {len(selected)} 门课程")
    else:
        print(f"\n  可导入 {len(valid)} 门课程")
        print(f"  输入方式：")
        print(f"    a     - 导入全部")
        print(f"    1,3,5 - 导入指定编号（逗号分隔）")
        print(f"    1-5   - 导入指定范围")
        print(f"    q     - 退出")

        cmd = input("\n>>> 请选择: ").strip().lower()

        if cmd == "q":
            print("已退出。")
            return
        elif cmd == "a":
            selected = valid
        elif "-" in cmd and cmd.replace("-", "").isdigit():
            parts = cmd.split("-", 1)
            try:
                lo, hi = int(parts[0]), int(parts[1])
                selected = [v for v in valid if lo <= v[0] <= hi]
            except ValueError:
                print("❌ 输入格式有误")
                return
        elif "," in cmd or cmd.isdigit():
            try:
                indices = {int(x.strip()) for x in cmd.split(",") if x.strip()}
                selected = [v for v in valid if v[0] in indices]
            except ValueError:
                print("❌ 输入格式有误")
                return
        else:
            print("❌ 无效输入")
            return

    if not selected:
        print("没有选择任何课程。")
        return

    # 7. 写入
    entries = [[cid, k, ct, rm] for _, cid, k, ct, rm, _ in selected]
    added, skipped = _add_courses_to_conf(entries)

    print(f"\n{'='*70}")
    print(f"  ✅ 导入完成！新增 {added} 门")
    print(f"{'='*70}")

    if added > 0:
        print(f"\n  已添加的课程：")
        for _, cid, k, ct, rm, nm in selected:
            print(f"    ✅ {nm}")

    print(f"\n  配置文件: {COURSE_CONF_FILE}")


if __name__ == "__main__":
    main()
