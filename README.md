# NJU 选课助手

南京大学本科生选课系统自动抢课捡漏工具，支持自动登录、验证码识别、多课程循环抢课、Server 酱通知。

## 效果图

![登录](https://www.zcec.top/usr/uploads/2026/01/2448730740.png)

![选课](https://www.zcec.top/usr/uploads/2026/01/1251060327.png)

## 功能特性

- **自动登录**：自动完成统一身份认证登录 + 点选验证码识别
- **本地验证码识别**：基于 ddddocr 的离线 OCR，无需第三方打码平台，提供 4 档精度可选
- **循环抢课**：自动轮询选课接口，抢到后自动移除并通过 Server 酱推送通知
- **Session 缓存**：登录态本地缓存复用，减少重复登录
- **并发模式**：`xk_quick.py` 支持多线程并发提交，适合开放选课瞬间抢课
- **课程查询**：终端内按关键字搜索课程，翻页浏览，快速获取课程 ID
- **辅助工具**：选课批次获取、Cookie 手动导入、Payload 解密

## 项目结构

```
├── xk.conf               # 主配置文件（账号、代理、验证码精度等）
├── course.conf           # 课程配置（选课批次 + 课程列表）
├── xk.py                 # 主抢课脚本（循环模式）
├── xk_quick.py           # 快速抢课脚本（并发模式）
├── xklogin.py            # 登录流程（验证码获取→识别→提交）
├── login.py              # Session 管理（缓存/验证/刷新）
├── captcha.py            # 验证码识别统一入口（从 xk.conf 读取精度级别）
├── captcha_fast.py       # 验证码识别 - 极速版（~1.0s，28 次 OCR）
├── captcha_balanced.py   # 验证码识别 - 均衡版（~1.3s，48 次 OCR）
├── captcha_accurate.py   # 验证码识别 - 精准版（~1.6s，72 次 OCR）
├── captcha_max.py        # 验证码识别 - 最高精度版（~8s，640 次 OCR + 轮廓匹配）
├── des_encrypt.py        # DES 密码加密（移植自前端 JS）
├── serverchan.py         # Server 酱推送通知
└── tools/
    ├── get_batch_code.py   # 获取选课批次代码
    ├── query_course.py     # 课程查询工具（按关键字搜索）
    ├── input_cookie.py     # 手动导入浏览器 Cookie
    └── course_decrypt.py   # AES Payload 解密工具
```

## 环境要求

- Python 3.8 ~ 3.12
- Windows / Linux / macOS
- 校内网络或 [EasyConnect VPN](https://github.com/lyc8503/NJUConnect)

## 安装

```bash
# 1. 克隆项目
git clone https://github.com/GreenTeodoro839/NJU-Auto-xk.git
cd NJU-Auto-xk

# 2. 创建虚拟环境
python -m venv .venv

# Windows
.venv\Scripts\activate
# Linux/macOS
source .venv/bin/activate

# 3. 安装依赖
pip install ddddocr pillow numpy requests pycryptodome serverchan-sdk pysocks
```

或使用 requirements.txt：

```bash
pip install -r requirements.txt
```

> **依赖说明**
> | 包名 | 用途 |
> |------|------|
> | `ddddocr` | 本地验证码 OCR 识别 |
> | `pillow` | 图像处理 |
> | `numpy` | 矩阵计算（匹配算法） |
> | `opencv-python` | 轮廓匹配（仅 `captcha_max.py` 需要） |
> | `requests` | HTTP 请求 |
> | `pycryptodome` | AES/DES 加密 |
> | `serverchan-sdk` | Server 酱推送（可选，不装不影响运行） |
> | `pysocks` | SOCKS5 代理支持（可选） |

## 配置

### xk.conf

主配置文件，JSON 格式：

```json
{
    "USER": "你的学号",
    "PWD": "你的密码",
    "PWD_ENCRYPT": "",
    "MAX_RETRIES": "20",
    "CAPTCHA_LEVEL": "balanced",
    "SCT_KEY": "你的Server酱SendKey",
    "SCT_OPTIONS": {
        "tags": "选课脚本"
    },
    "PROXY": "socks5://127.0.0.1:1080"
}
```

| 字段 | 必填 | 说明 |
|------|------|------|
| `USER` | ✅ | 学号（统一认证用户名） |
| `PWD` | ✅ | 密码明文（脚本自动加密后提交，推荐填这个） |
| `PWD_ENCRYPT` | ❌ | 密码加密文本（和明文二选一，获取方式见下方） |
| `MAX_RETRIES` | ❌ | 登录最大重试次数，默认 `3` |
| `CAPTCHA_LEVEL` | ❌ | 验证码识别精度，默认 `balanced`（见下方说明） |
| `SCT_KEY` | ❌ | Server 酱 SendKey，不填则不推送 |
| `SCT_OPTIONS` | ❌ | Server 酱附加选项 |
| `PROXY` | ❌ | 代理地址，支持 `socks5://` 和 `http://` |

> **获取加密密码**（如果不想填明文）：在选课平台按 F12 打开开发者工具，选 Network，登录后找到登录请求，复制 `loginPwd` 字段的值填入 `PWD_ENCRYPT`。
>
> ![获取密码](https://www.zcec.top/usr/uploads/2026/01/3740882447.png)

#### 验证码精度级别（CAPTCHA_LEVEL）

| 级别 | OCR 次数/张 | 速度 | 说明 |
|------|-------------|------|------|
| `fast` | 28 | ~1.0s | 极速，适合超时严格的场景 |
| `balanced` | 48 | ~1.3s | **推荐**，速度与准确率兼顾 |
| `accurate` | 72 | ~1.6s | 精准，牺牲一点速度换更高准确率 |
| `max` | 640 | ~8.0s | 最高精度，离线批量处理用 |

### course.conf

课程配置文件，JSON 格式：

```json
{
    "electiveBatchCode": "选课批次代码",
    "courses": [
        ["教学班ID", "课程类别", "教学班类型", "备注"],
        ["2025202621800143001", "1", "ZY", "高等数学"]
    ]
}
```

| 字段 | 说明 |
|------|------|
| `electiveBatchCode` | 选课批次代码，通过 `tools/get_batch_code.py` 获取 |
| `courses` | 课程列表，每项为 `[teachingClassId, courseKind, teachingClassType, 备注]`，第 4 项备注可选 |

> **如何获取课程参数？**
>
> **方法一**：使用课程查询工具搜索课程名，直接获取并添加课程：
>
> ```bash
> python tools/query_course.py
> ```
>
> **方法二**：在浏览器选课页面按 F12，Network 面板中找到 `volunteer.do` 请求，复制 Payload 中的 addParam，用解密工具查看：
> ```bash
> python tools/course_decrypt.py
> ```
> ![请求](https://www.zcec.top/usr/uploads/2026/01/3464656477.png)
> ![解密](https://www.zcec.top/usr/uploads/2026/01/949162351.png)

#### 关于 teachingClassID 和 courseKind

`teachingClassID` 的构成规律：`学期代码` + `课程号` + `班级序号`

例如 `2025202621800143001` = `20252026-2`（2025-2026 学年第二学期） + `18001430`（课程号） + `01`（第 1 个班）

`courseKind`（jxblx）对照表：

| 类别 | teachingClassType | courseKind |
|------|-------------------|------------|
| 专业 | ZY | 1 |
| 体育 | TY | 2 |
| 科学之光 | GG06 | 3 |
| 公选课 | GG01 | 4 |
| 美育 | MY | 5 |
| 导学/研讨/通识 | GG02 | 6, 7 |
| 悦读 | YD | 8 |
| 跨专业 | KZY | 12 |
| 大学数学 | TX01 | 13 |
| 大学英语 | TX02 | 14 |
| 思政军事类 | TX03 | 15 |
| 计算机 | TX04 | 16 |

## 使用方法

### 1. 获取选课批次代码

```bash
python tools/get_batch_code.py
```

运行后自动将 `electiveBatchCode` 写入 `course.conf`。

### 2. 查询课程 ID

```bash
python tools/query_course.py
```

输入课程名或教师名搜索，支持翻页（`u`/`d`），输入编号查看课程 ID，然后按提示直接写入或手动填写 `course.conf`。

### 3. 运行抢课（循环模式，捡漏专用）

```bash
python xk.py
```

脚本会自动登录 → 循环请求选课接口 → 抢到后推送通知并从 `course.conf` 移除 → 直到全部抢完或手动终止。

### 4. 运行抢课（并发模式）

```bash
python xk_quick.py
```

适合选课系统刚开放时使用，多线程并发提交提高成功率。

### 5. 手动导入 Session（备用）

如果自动登录遇到困难，可以手动从浏览器复制 Cookie 和 Token：

```bash
python tools/input_cookie.py
```

## 注意事项

- 需要在校内网络或通过 VPN/代理连接校园网
- `electiveBatchCode` 会随选课批次变化，每次选课前需重新获取
- 脚本检测到登录失效（"非法请求"）时会自动重新登录
- 抢到课后会通过 Server 酱推送通知（需配置 `SCT_KEY`）
- 每轮抢课之间有随机间隔（30~90s），避免频繁请求

## 辅助工具

| 工具 | 说明 |
|------|------|
| `tools/get_batch_code.py` | 连接选课系统获取当前可用的选课批次代码 |
| `tools/query_course.py` | 按关键字搜索课程，查看课程 ID 等信息 |
| `tools/input_cookie.py` | 从浏览器手动复制 Cookie/Token 写入 `session_cache.json` |
| `tools/course_decrypt.py` | 解密选课请求的 AES 加密 Payload，用于调试 |

## 免责声明

本项目仅供学习交流使用。使用本工具产生的一切后果由使用者自行承担，开发者不对因使用本工具导致的任何问题负责。请遵守学校相关规定，合理使用。
