"""
captcha_max.py — 最高精度版
- OCR调用: 160次/区域，共640次 + 轮廓相似度辅助
- 速度: ~8s/张
- 新增: 5种padding × 4种预处理 × 4种尺寸 × 2引擎 + cv2轮廓匹配
- 适用: 离线批量处理、不限时间的场景

用法:
    from captcha_max import solve_captcha_from_base64
    result = solve_captcha_from_base64(base64_str)
    # result: [(x1,y1), (x2,y2), (x3,y3), (x4,y4)] 或 None
"""

import base64
import io
import numpy as np
import cv2
from PIL import Image, ImageOps, ImageEnhance
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
    chars, imgs = [], []
    for x1, x2 in _TITLE_CHAR_RANGES:
        crop = img.crop((x1, 100, x2, 120))
        imgs.append(crop)
        chars.append(_ocr_beta.classification(_img_to_bytes(crop)))
    return chars, imgs


def _detect_click_regions(img):
    click_area = img.crop((0, 0, 250, 100))
    bboxes = _det.detection(_img_to_bytes(click_area))
    return bboxes, click_area


def _get_votes(click_area, bbox):
    """
    160次OCR/区域:
    5种padding × 4种预处理(原图/灰度/反色/高对比) × 4种尺寸(原/64/48/32) × 2引擎
    """
    x1, y1, x2, y2 = bbox
    votes = Counter()
    for pad in [0, 3, 5, 8, 12]:
        px1, py1 = max(0, x1 - pad), max(0, y1 - pad)
        px2, py2 = min(250, x2 + pad), min(100, y2 + pad)
        ci = click_area.crop((px1, py1, px2, py2))
        variants = [
            ci,
            ci.convert("L"),
            ImageOps.invert(ci.convert("RGB")),
            ImageEnhance.Contrast(ci).enhance(2.0),
        ]
        for v in variants:
            for sz in [None, (64, 64), (48, 48), (32, 32)]:
                im = v if sz is None else v.resize(sz, Image.LANCZOS)
                for ocr_engine in [_ocr_beta, _ocr_normal]:
                    try:
                        r = ocr_engine.classification(_img_to_bytes(im))
                        if len(r) == 1:
                            votes[r] += 1
                    except Exception:
                        pass
    return votes


def _contour_similarity(title_img, click_area, bbox):
    """cv2.matchShapes 轮廓形状相似度"""
    t_gray = np.array(title_img.convert("L"))
    _, t_bin = cv2.threshold(t_gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    x1, y1, x2, y2 = bbox
    pad = 5
    c_gray = np.array(click_area.crop((
        max(0, x1 - pad), max(0, y1 - pad),
        min(250, x2 + pad), min(100, y2 + pad)
    )).convert("L"))
    _, c_bin = cv2.threshold(c_gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    _, c_bin_inv = cv2.threshold(c_gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

    t_contours, _ = cv2.findContours(t_bin, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not t_contours:
        return 0.0
    t_cnt = max(t_contours, key=cv2.contourArea)

    best_dist = 999.0
    for cb in [c_bin, c_bin_inv]:
        c_contours, _ = cv2.findContours(cb, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not c_contours:
            continue
        c_cnt = max(c_contours, key=cv2.contourArea)
        dist = cv2.matchShapes(t_cnt, c_cnt, cv2.CONTOURS_MATCH_I2, 0)
        best_dist = min(best_dist, dist)

    return max(0.0, 2.0 - best_dist)


def _match_chars(title_chars, title_imgs, bboxes, click_area):
    n = min(4, len(bboxes))
    all_votes = [_get_votes(click_area, b) for b in bboxes[:n]]
    score_matrix = np.zeros((n, n))
    for i in range(n):
        for j in range(n):
            vote_count = all_votes[j].get(title_chars[i], 0)
            ocr_score = vote_count * 0.1
            shape_score = _contour_similarity(title_imgs[i], click_area, bboxes[j])
            score_matrix[i][j] = ocr_score + shape_score * 0.5
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
        title_chars, title_imgs = _ocr_title_chars(img)
        bboxes, click_area = _detect_click_regions(img)
        if len(bboxes) < 4:
            return None
        matched = _match_chars(title_chars, title_imgs, bboxes, click_area)
        if len(matched) != 4:
            return None
        return matched
    except Exception:
        return None
