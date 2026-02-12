"""
captcha_fast.py — 极速版
- OCR调用: 7次/区域，共28次
- 速度: ~1s/张
- 适用: 高性能要求、对准确率容忍度高的场景

用法:
    from captcha_fast import solve_captcha_from_base64
    result = solve_captcha_from_base64(base64_str)
    # result: [(x1,y1), (x2,y2), (x3,y3), (x4,y4)] 或 None
"""

import base64
import io
import numpy as np
from PIL import Image
import ddddocr
from itertools import permutations
from collections import Counter

# OCR engines
_det = ddddocr.DdddOcr(det=True, show_ad=False)
_ocr_beta = ddddocr.DdddOcr(beta=True, show_ad=False)
_ocr_normal = ddddocr.DdddOcr(show_ad=False)

_TITLE_CHAR_RANGES = [(118, 138), (140, 160), (162, 182), (185, 205)]


def _img_to_bytes(img):
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _is_abnormal(img):
    px = img.getpixel((218, 110))
    if isinstance(px, int):
        return px < 50
    r, g, b = px
    if r > 200 and g > 200 and b > 200:
        return False
    if r < 50 and g < 50 and b < 50:
        return True
    return False


def _ocr_title_chars(img):
    chars = []
    for x1, x2 in _TITLE_CHAR_RANGES:
        crop = img.crop((x1, 100, x2, 120))
        chars.append(_ocr_beta.classification(_img_to_bytes(crop)))
    return chars


def _detect_click_regions(img):
    click_area = img.crop((0, 0, 250, 100))
    bboxes = _det.detection(_img_to_bytes(click_area))
    return bboxes, click_area


def _get_votes(click_area, bbox):
    """7次OCR/区域: 3种padding × (原图+64x64) + 1次normal"""
    x1, y1, x2, y2 = bbox
    votes = Counter()
    for pad in [3, 5, 8]:
        px1, py1 = max(0, x1 - pad), max(0, y1 - pad)
        px2, py2 = min(250, x2 + pad), min(100, y2 + pad)
        ci = click_area.crop((px1, py1, px2, py2))
        r = _ocr_beta.classification(_img_to_bytes(ci))
        if len(r) == 1:
            votes[r] += 1
        big = ci.resize((64, 64), Image.LANCZOS)
        r = _ocr_beta.classification(_img_to_bytes(big))
        if len(r) == 1:
            votes[r] += 1
    ci5 = click_area.crop((max(0, x1 - 5), max(0, y1 - 5), min(250, x2 + 5), min(100, y2 + 5)))
    r = _ocr_normal.classification(_img_to_bytes(ci5))
    if len(r) == 1:
        votes[r] += 1
    return votes


def _match_chars(title_chars, bboxes, click_area):
    n = min(4, len(bboxes))
    all_votes = [_get_votes(click_area, b) for b in bboxes[:n]]
    score_matrix = np.zeros((n, n))
    for i in range(n):
        for j in range(n):
            score_matrix[i][j] = all_votes[j].get(title_chars[i], 0)
    best_score, best_perm = -999, tuple(range(n))
    for perm in permutations(range(n)):
        total = sum(score_matrix[i][perm[i]] for i in range(n))
        if total > best_score:
            best_score, best_perm = total, perm
    matched = []
    for i in range(n):
        j = best_perm[i]
        b = bboxes[j]
        matched.append(((b[0] + b[2]) // 2, (b[1] + b[3]) // 2))
    return matched


def solve_captcha_from_base64(base64_str):
    """
    传入base64图片，返回4个点[(x1,y1),...]
    异常码或识别失败时返回 None
    """
    try:
        img_bytes = base64.b64decode(base64_str)
        img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
        if _is_abnormal(img):
            return None
        title_chars = _ocr_title_chars(img)
        bboxes, click_area = _detect_click_regions(img)
        if len(bboxes) < 4:
            return None
        matched = _match_chars(title_chars, bboxes, click_area)
        if len(matched) != 4:
            return None
        return matched
    except Exception:
        return None
