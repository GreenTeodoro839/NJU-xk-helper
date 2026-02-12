# -*- coding: utf-8 -*-
"""
课程查询工具
- 连接选课系统，按关键字搜索课程
- 支持翻页浏览、选择课程查看 ID
"""

import json
import os
import sys
import urllib.parse

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ================= 路径 =================
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

import login  # noqa: E402

CONF_FILE = os.path.join(BASE_DIR, "xk.conf")
COURSE_FILE = os.path.join(BASE_DIR, "course.conf")
QUERY_URL = "https://xk.nju.edu.cn/xsxkapp/sys/xsxkapp/elective/queryCourse.do"

DAY_MAP = {"1": "周一", "2": "周二", "3": "周三", "4": "周四", "5": "周五", "6": "周六", "7": "周日"}
PAGE_SIZE = 10

# jxblx → (courseKind, teachingClassType, 类别名称)
# ⚠️ 此对照表根据经验整理，可能不完全准确
JXBLX_MAP = {
    "1":  ("1",   "ZY",   "专业"),
    "2":  ("2",   "TY",   "体育"),
    "3":  ("3",   "GG06", "科学之光"),
    "4":  ("4",   "GG01", "公选课"),
    "5":  ("5",   "MY",   "美育"),
    "6":  ("6,7", "GG02", "导学/研讨/通识"),  # 响应只显示6, 实际请求需要6,7
    "7":  ("6,7", "GG02", "导学/研讨/通识"),  # 同上
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
    """根据 jxblx 查表得到 (courseKind, teachingClassType, 类别名)"""
    jxblx = str(jxblx).strip()
    if jxblx in JXBLX_MAP:
        return JXBLX_MAP[jxblx]
    return (jxblx, "??", "未知类别")


def _load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _load_config():
    """加载 xk.conf 和 course.conf 中需要的字段"""
    conf = _load_json(CONF_FILE)
    student_code = str(conf.get("USER", "")).strip()
    proxy_url = (conf.get("PROXY") or "").strip() or None
    if not student_code:
        print("❌ xk.conf 中缺少 USER（学号）")
        sys.exit(1)

    course_conf = _load_json(COURSE_FILE)
    batch_code = str(course_conf.get("electiveBatchCode", "")).strip()
    if not batch_code:
        print("❌ course.conf 中缺少 electiveBatchCode，请先运行 tools/get_batch_code.py")
        sys.exit(1)

    return student_code, batch_code, proxy_url


def _build_proxies(proxy_url):
    if not proxy_url:
        return None
    if proxy_url.startswith("socks5://"):
        proxy_url = proxy_url.replace("socks5://", "socks5h://", 1)
    return {"http": proxy_url, "https": proxy_url}


def _build_headers(token):
    return {
        "Host": "xk.nju.edu.cn",
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
    }


def _format_time(course):
    """从 teachingPlace 字段提取时间信息"""
    place = course.get("teachingPlace") or ""
    if place:
        return place
    # fallback: 从 teachingTimeList 拼接
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
    """
    查询课程，返回 (课程列表, 是否为最后一页)。
    课程列表为空 + 最后一页 = 无更多结果。
    返回 None 表示需要重新登录。
    """
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

    form_data = {"querySetting": json.dumps(query_setting, ensure_ascii=False)}
    headers = _build_headers(token)

    try:
        r = requests.post(
            QUERY_URL,
            cookies=cookies,
            headers=headers,
            data=form_data,
            proxies=proxies,
            verify=False,
            timeout=10,
        )
        r.encoding = "utf-8"
    except Exception as e:
        print(f"❌ 网络请求失败: {e}")
        return None

    # 空响应 / 非法请求 → 需要重新登录
    text = r.text.strip()
    if not text:
        return None

    try:
        data = r.json()
    except Exception:
        # 非 JSON（可能是登录页 HTML）
        if "非法请求" in text or "<html" in text.lower():
            return None
        print(f"❌ 响应解析失败: {text[:200]}")
        return None

    if isinstance(data, dict) and "非法请求" in str(data.get("msg", "")):
        return None

    data_list = data.get("dataList") if isinstance(data, dict) else None
    if data_list is None:
        # response-empty 的情况：响应为空对象或无 dataList
        return ([], True)

    if not data_list:
        return ([], True)

    is_last = len(data_list) < PAGE_SIZE
    return (data_list, is_last)


def display_page(courses, page_number, is_last, keyword):
    """在终端展示一页课程"""
    total_hint = "最后一页" if is_last else f"下一页: d"
    print(f"\n{'='*70}")
    print(f"  搜索: \"{keyword}\"  |  第 {page_number + 1} 页  |  {total_hint}")
    print(f"{'='*70}")

    if not courses:
        print("  （无结果）")
        return

    for i, c in enumerate(courses, 1):
        cnum = c.get("courseNumber", "?")
        name = c.get("courseName", "?")
        teacher = c.get("teacherName", "?")
        time_str = _format_time(c)
        campus = c.get("campusName", "?")
        dept = c.get("departmentName", "?")
        credit = c.get("credit", "?")
        jxblx = c.get("jxblx", "?")
        _, ctype_code, ctype_name = _lookup_kind_type(jxblx)

        print(f"\n  [{i:>2}] {name}  ({credit}学分, {ctype_name})")
        print(f"       课程号: {cnum}  |  教师: {teacher}")
        print(f"       时间: {time_str}")
        print(f"       校区: {campus}  |  学院: {dept}")

    print(f"\n{'─'*70}")
    cmds = []
    if courses:
        cmds.append("编号=选课")
    if page_number > 0:
        cmds.append("u=上一页")
    if not is_last:
        cmds.append("d=下一页")
    cmds.append("r=重新搜索")
    cmds.append("q=退出")
    print(f"  {' | '.join(cmds)}")


def clear_screen():
    os.system("cls" if os.name == "nt" else "clear")


def _add_to_course_conf(class_id, kind, ctype, remark):
    """将课程追加到 course.conf"""
    try:
        data = _load_json(COURSE_FILE)
    except Exception:
        data = {"electiveBatchCode": "", "courses": []}

    courses = data.get("courses", [])
    # 检查是否已存在
    for c in courses:
        if c[0] == class_id:
            print(f"  ⚠️ 该课程已在 course.conf 中，跳过")
            return False

    courses.append([class_id, kind, ctype, remark])
    data["courses"] = courses

    # 手动拼接 JSON，保证每个课程占一行，方便用户查看
    batch = json.dumps(data.get("electiveBatchCode", ""), ensure_ascii=False)
    lines = []
    lines.append("{")
    lines.append(f'  "electiveBatchCode": {batch},')
    lines.append('  "courses": [')
    for i, c in enumerate(courses):
        row = json.dumps(c, ensure_ascii=False)
        comma = "," if i < len(courses) - 1 else ""
        lines.append(f"    {row}{comma}")
    lines.append("  ]")
    lines.append("}")

    tmp = COURSE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    os.replace(tmp, COURSE_FILE)
    return True


def _show_course_detail(course):
    """显示课程详情，询问是否加入 course.conf"""
    class_id = course.get("teachingClassID", "?")
    name = course.get("courseName", "?")
    teacher = course.get("teacherName", "?")
    time_str = _format_time(course)
    campus = course.get("campusName", "?")
    dept = course.get("departmentName", "?")
    credit = course.get("credit", "?")
    cnum = course.get("courseNumber", "?")
    jxblx = course.get("jxblx", "?")

    kind, ctype, ctype_name = _lookup_kind_type(jxblx)

    print(f"\n{'─'*70}")
    print(f"  课程名称:  {name}")
    print(f"  课程号:    {cnum}")
    print(f"  教师:      {teacher}")
    print(f"  时间:      {time_str}")
    print(f"  校区:      {campus}")
    print(f"  学院:      {dept}")
    print(f"  学分:      {credit}")
    print(f"{'─'*70}")
    print(f"  teachingClassID:   {class_id}")
    print(f"  courseKind:         {kind}  ({ctype_name})")
    print(f"  teachingClassType: {ctype}")
    print(f"{'─'*70}")
    print(f"  ⚠️ 注意: courseKind 和 teachingClassType 由对照表推断，可能不准确！")
    if jxblx in ("6", "7"):
        print(f"  ⚠️ 该课程 jxblx={jxblx}，实际请求 courseKind 应为 \"6,7\"（已自动处理）")

    if ctype == "??":
        print(f"  ❌ 未找到 jxblx={jxblx} 对应的类别，无法自动添加，请手动配置")
        input("\n  按回车返回...")
        return

    print()
    confirm = input("  输入 y 添加到 course.conf，其他键返回: ").strip().lower()
    if confirm == "y":
        # 生成可读备注: 课程名/教师/时间简述
        time_short = time_str.replace(";", ",") if len(time_str) <= 40 else time_str[:37] + "..."
        remark = f"{name}/{teacher}/{time_short}"

        if _add_to_course_conf(class_id, kind, ctype, remark):
            print(f"  ✅ 已添加到 course.conf")
        input("\n  按回车继续...")


def main():
    student_code, batch_code, proxy_url = _load_config()
    proxies = _build_proxies(proxy_url)

    # 清除系统代理环境变量
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
        os.environ.pop(var, None)

    # 获取登录凭证
    print(">>> 正在获取登录凭证...")
    cookies, token = login.get_session()
    if not (cookies and token):
        print("❌ 登录失败，无法继续")
        sys.exit(1)
    print(">>> 登录成功\n")

    keyword = ""
    page_number = 0
    cached_pages = {}  # page_number -> (courses, is_last)

    while True:
        # 获取搜索关键字
        if not keyword:
            clear_screen()
            keyword = input("请输入搜索关键字（课程名/教师名）: ").strip()
            if not keyword:
                continue
            page_number = 0
            cached_pages.clear()

        # 查询当前页（优先用缓存）
        if page_number in cached_pages:
            courses, is_last = cached_pages[page_number]
        else:
            print(f"\n>>> 正在查询第 {page_number + 1} 页...")
            result = query_courses(keyword, page_number, student_code, batch_code, cookies, token, proxies)

            if result is None:
                # 需要重新登录
                print(">>> Session 失效，正在重新登录...")
                cookies, token = login.get_session(force_refresh=True)
                if not (cookies and token):
                    print("❌ 重新登录失败")
                    sys.exit(1)
                # 重试一次
                result = query_courses(keyword, page_number, student_code, batch_code, cookies, token, proxies)
                if result is None:
                    print("❌ 查询仍然失败，请检查网络或配置")
                    sys.exit(1)

            courses, is_last = result
            cached_pages[page_number] = (courses, is_last)

        # 展示
        clear_screen()
        display_page(courses, page_number, is_last, keyword)

        # 用户交互
        cmd = input("\n>>> ").strip().lower()

        if cmd == "q":
            print("再见！")
            break
        elif cmd == "r":
            keyword = ""
            continue
        elif cmd == "u":
            if page_number > 0:
                page_number -= 1
            else:
                print("已经是第一页了")
                input("按回车继续...")
            continue
        elif cmd == "d":
            if not is_last:
                page_number += 1
            else:
                print("已经是最后一页了")
                input("按回车继续...")
            continue
        elif cmd.isdigit():
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
