# -*- coding: utf-8 -*-
"""课程查询工具 - 连接选课系统按关键字搜索课程，支持翻页和添加到 course.conf。"""

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

QUERY_URL = "https://xk.nju.edu.cn/xsxkapp/sys/xsxkapp/elective/queryCourse.do"

DAY_MAP = {"1": "周一", "2": "周二", "3": "周三", "4": "周四", "5": "周五", "6": "周六", "7": "周日"}
PAGE_SIZE = 10

# jxblx → (courseKind, teachingClassType, 类别名称)
JXBLX_MAP = {
    "1":  ("1",   "ZY",   "专业"),
    "2":  ("2",   "TY",   "体育"),
    "3":  ("3",   "GG06", "科学之光"),
    "4":  ("4",   "GG01", "公选课"),
    "5":  ("5",   "MY",   "美育"),
    "6":  ("6,7", "GG02", "导学/研讨/通识"),
    "7":  ("6,7", "GG02", "导学/研讨/通识"),
    "8":  ("8",   "YD",   "悦读"),
    "9":  ("9",   "GG03", "交流生语言课"),
    "10": ("10",  "GG04", "国际化课程"),
    "12": ("12",  "KZY",  "跨专业"),
    "13": ("13",  "TX01", "大学数学"),
    "14": ("14",  "TX02", "大学英语"),
    "15": ("15",  "TX03", "思政军事类"),
    "16": ("16",  "TX04", "计算机"),
    "20": ("20",  "GG05", "其他国际化课程"),
}


def _lookup_kind_type(jxblx):
    jxblx = str(jxblx).strip()
    return JXBLX_MAP.get(jxblx, (jxblx, "??", "未知类别"))


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


def _format_time(course):
    place = course.get("teachingPlace") or ""
    if place:
        return place
    time_list = course.get("teachingTimeList") or []
    parts = []
    for t in time_list:
        day = DAY_MAP.get(str(t.get("dayOfWeek", "")), "?")
        begin = t.get("beginSection", "?")
        end = t.get("endSection", "?")
        week = t.get("weekName", "")
        parts.append(f"{day} {begin}-{end}节 {week}")
    return "; ".join(parts) if parts else "未知"


def query_courses(keyword, page_number, student_code, batch_code, cookies, token, proxies):
    query_setting = {
        "data": {
            "studentCode": student_code,
            "electiveBatchCode": batch_code,
            "teachingClassType": "QB",
            "queryContent": keyword,
        },
        "pageSize": str(PAGE_SIZE),
        "pageNumber": str(page_number),
        "order": "",
    }

    headers = build_headers(token)
    try:
        r = requests.post(
            QUERY_URL,
            cookies=cookies,
            headers=headers,
            data={"querySetting": json.dumps(query_setting, ensure_ascii=False)},
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

    data_list = data.get("dataList") if isinstance(data, dict) else None
    if data_list is None or not data_list:
        return ([], True)

    return (data_list, len(data_list) < PAGE_SIZE)


def display_page(courses, page_number, is_last, keyword):
    total_hint = "最后一页" if is_last else "下一页: d"
    print(f"\n{'='*70}")
    print(f"  搜索: \"{keyword}\"  |  第 {page_number + 1} 页  |  {total_hint}")
    print(f"{'='*70}")

    if not courses:
        print("  （无结果）")
    else:
        for i, c in enumerate(courses, 1):
            name = c.get("courseName", "?")
            teacher = c.get("teacherName", "?")
            time_str = _format_time(c)
            campus = c.get("campusName", "?")
            credit = c.get("credit", "?")
            _, _, ctype_name = _lookup_kind_type(c.get("jxblx", "?"))

            print(f"\n  [{i:>2}] {name}  ({credit}学分, {ctype_name})")
            print(f"       课程号: {c.get('courseNumber', '?')}  |  教师: {teacher}")
            print(f"       时间: {time_str}")
            print(f"       校区: {campus}  |  学院: {c.get('departmentName', '?')}")

    print(f"\n{'─'*70}")
    cmds = []
    # noinspection: cmds 构建区
    if courses: cmds.append("编号=选课")
    if page_number > 0: cmds.append("u=上一页")
    if not is_last: cmds.append("d=下一页")
    cmds.extend(["r=重新搜索", "q=退出"])
    print(f"  {' | '.join(cmds)}")


def clear_screen():
    os.system("cls" if os.name == "nt" else "clear")


def _add_to_course_conf(class_id, kind, ctype, remark):
    try:
        data = load_json(COURSE_CONF_FILE)
    except Exception:
        data = {"electiveBatchCode": "", "courses": []}

    courses = data.get("courses", [])
    for c in courses:
        if c[0] == class_id:
            print("  ⚠️ 该课程已在 course.conf 中，跳过")
            return False

    courses.append([class_id, kind, ctype, remark])
    data["courses"] = courses

    batch = json.dumps(data.get("electiveBatchCode", ""), ensure_ascii=False)
    lines = ["{", f'  "electiveBatchCode": {batch},', '  "courses": [']
    for i, c in enumerate(courses):
        row = json.dumps(c, ensure_ascii=False)
        comma = "," if i < len(courses) - 1 else ""
        lines.append(f"    {row}{comma}")
    lines.extend(["  ]", "}"])

    tmp = COURSE_CONF_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    os.replace(tmp, COURSE_CONF_FILE)
    return True


def _show_course_detail(course):
    class_id = course.get("teachingClassID", "?")
    name = course.get("courseName", "?")
    teacher = course.get("teacherName", "?")
    time_str = _format_time(course)
    jxblx = course.get("jxblx", "?")
    kind, ctype, ctype_name = _lookup_kind_type(jxblx)

    print(f"\n{'─'*70}")
    print(f"  课程名称:  {name}")
    print(f"  课程号:    {course.get('courseNumber', '?')}")
    print(f"  教师:      {teacher}")
    print(f"  时间:      {time_str}")
    print(f"  校区:      {course.get('campusName', '?')}")
    print(f"  学院:      {course.get('departmentName', '?')}")
    print(f"  学分:      {course.get('credit', '?')}")
    print(f"{'─'*70}")
    print(f"  teachingClassID:   {class_id}")
    print(f"  courseKind:         {kind}  ({ctype_name})")
    print(f"  teachingClassType: {ctype}")
    print(f"{'─'*70}")
    print(f"  ⚠️ 注意: courseKind 和 teachingClassType 由对照表推断，可能不准确！")

    if ctype == "??":
        print(f"  ❌ 未找到 jxblx={jxblx} 对应的类别，无法自动添加")
        input("\n  按回车返回...")
        return

    confirm = input("\n  输入 y 添加到 course.conf，其他键返回: ").strip().lower()
    if confirm == "y":
        time_short = time_str[:37] + "..." if len(time_str) > 40 else time_str.replace(";", ",")
        remark = f"{name}/{teacher}/{time_short}"
        if _add_to_course_conf(class_id, kind, ctype, remark):
            print("  ✅ 已添加到 course.conf")
        input("\n  按回车继续...")


def main():
    student_code, batch_code, proxy_url = _load_config()
    proxies = build_proxies(proxy_url)
    clear_env_proxies()

    print(">>> 正在获取登录凭证...")
    cookies, token = acquire_session()
    if not (cookies and token):
        print("❌ 登录失败，无法继续")
        sys.exit(1)
    print(">>> 登录成功\n")

    keyword = ""
    page_number = 0
    cached_pages = {}

    while True:
        if not keyword:
            clear_screen()
            keyword = input("请输入搜索关键字（课程名/教师名）: ").strip()
            if not keyword:
                continue
            page_number = 0
            cached_pages.clear()

        if page_number in cached_pages:
            courses, is_last = cached_pages[page_number]
        else:
            print(f"\n>>> 正在查询第 {page_number + 1} 页...")
            result = query_courses(keyword, page_number, student_code, batch_code, cookies, token, proxies)

            if result is None:
                print(">>> Session 失效，正在重新登录...")
                cookies, token = acquire_session(force_refresh=True)
                if not (cookies and token):
                    print("❌ 重新登录失败")
                    sys.exit(1)
                result = query_courses(keyword, page_number, student_code, batch_code, cookies, token, proxies)
                if result is None:
                    print("❌ 查询仍然失败")
                    sys.exit(1)

            courses, is_last = result
            cached_pages[page_number] = (courses, is_last)

        clear_screen()
        display_page(courses, page_number, is_last, keyword)

        cmd = input("\n>>> ").strip().lower()

        if cmd == "q":
            print("再见！")
            break
        elif cmd == "r":
            keyword = ""
        elif cmd == "u":
            if page_number > 0:
                page_number -= 1
            else:
                print("已经是第一页了")
                input("按回车继续...")
        elif cmd == "d":
            if not is_last:
                page_number += 1
            else:
                print("已经是最后一页了")
                input("按回车继续...")
        elif cmd.isdigit():
            if not courses:
                print("当前无搜索结果，无法选课")
                input("按回车继续...")
            else:
                idx = int(cmd)
                if 1 <= idx <= len(courses):
                    _show_course_detail(courses[idx - 1])
                else:
                    print(f"无效编号，请输入 1~{len(courses)}")
                    input("按回车继续...")
        else:
            print("无效输入")
            input("按回车继续...")


if __name__ == "__main__":
    main()
