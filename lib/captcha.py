"""
Pure ONNX captcha solver.
Exports one function:
  solve_captcha_from_base64(img_gif_b64_body) -> [(x, y) * 4] or None
"""

from __future__ import annotations

import base64
import io
import json
import os
import threading
from itertools import permutations
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import onnxruntime as ort
from PIL import Image

Point = Tuple[int, int]
TITLE_RANGES = [(118, 138), (140, 160), (162, 182), (185, 205)]

# 模型路径：相对于项目根目录的 models/ 文件夹
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UPPER_ONNX_PATH = os.path.join(_PROJECT_ROOT, "models", "upper_model.onnx")
TITLE_ONNX_PATH = os.path.join(_PROJECT_ROOT, "models", "title_model.onnx")
ALLOW_LOWER_TAIL = True

_LOCK = threading.Lock()
_SOLVER = None
_INIT_ERROR = None


def _exists(path: str) -> bool:
    return bool(path) and Path(path).exists()


def _softmax(x: np.ndarray) -> np.ndarray:
    x = x - np.max(x)
    ex = np.exp(x)
    return ex / max(float(ex.sum()), 1e-9)


def _extract_upper_mask_image(img: Image.Image, pad: int = 6) -> Image.Image:
    arr = np.array(img.convert("RGB"), dtype=np.uint8)
    r = arr[..., 0].astype(np.float32)
    g = arr[..., 1].astype(np.float32)
    b = arr[..., 2].astype(np.float32)

    maxc = np.maximum(np.maximum(r, g), b)
    minc = np.minimum(np.minimum(r, g), b)
    sat = (maxc - minc) / (maxc + 1e-6)
    val = maxc / 255.0

    light_bg = (arr[..., 0] > 165) & (arr[..., 1] > 205) & (arr[..., 2] > 225)
    mask = (sat > 0.16) & (val > 0.10) & (~light_bg)
    if int(mask.sum()) < 6:
        mask = (sat > 0.10) & (val > 0.08)

    ys, xs = np.where(mask)
    if len(xs) >= 6:
        x1, x2 = int(xs.min()), int(xs.max()) + 1
        y1, y2 = int(ys.min()), int(ys.max()) + 1
        tight = mask[y1:y2, x1:x2].astype(np.uint8)
    else:
        tight = mask.astype(np.uint8)

    h, w = tight.shape
    side = max(h, w) + pad * 2
    canvas = np.zeros((side, side), dtype=np.uint8)
    oy = (side - h) // 2
    ox = (side - w) // 2
    canvas[oy : oy + h, ox : ox + w] = tight * 255
    return Image.fromarray(canvas, mode="L").convert("RGB")


def _preprocess_for_model(img: Image.Image, input_size: int, mode: str) -> np.ndarray:
    if mode == "upper_mask_v1":
        img = _extract_upper_mask_image(img)
    img = img.resize((input_size, input_size), Image.Resampling.BILINEAR).convert("RGB")
    arr = np.asarray(img, dtype=np.float32) / 255.0
    arr = (arr - 0.5) / 0.5
    arr = np.transpose(arr, (2, 0, 1))[None, ...]
    return arr.astype(np.float32)


def _color_mask(arr: np.ndarray, sat_thr: float, val_thr: float) -> np.ndarray:
    r = arr[..., 0].astype(np.float32)
    g = arr[..., 1].astype(np.float32)
    b = arr[..., 2].astype(np.float32)
    maxc = np.maximum(np.maximum(r, g), b)
    minc = np.minimum(np.minimum(r, g), b)
    sat = (maxc - minc) / (maxc + 1e-6)
    val = maxc / 255.0
    light_bg = (arr[..., 0] > 160) & (arr[..., 1] > 200) & (arr[..., 2] > 220)
    return (sat > sat_thr) & (val > val_thr) & (~light_bg)


def _connected_components(mask: np.ndarray, min_area: int, max_area: int, top_h: int) -> List[dict]:
    h, w = mask.shape
    visited = np.zeros((h, w), dtype=np.uint8)
    regions = []
    pad = 4

    for y in range(h):
        for x in range(w):
            if not mask[y, x] or visited[y, x]:
                continue
            stack = [(x, y)]
            visited[y, x] = 1
            area = 0
            minx = maxx = x
            miny = maxy = y
            sumx = sumy = 0

            while stack:
                cx, cy = stack.pop()
                area += 1
                sumx += cx
                sumy += cy
                if cx < minx: minx = cx
                if cx > maxx: maxx = cx
                if cy < miny: miny = cy
                if cy > maxy: maxy = cy

                for ny in range(max(0, cy - 1), min(h - 1, cy + 1) + 1):
                    for nx in range(max(0, cx - 1), min(w - 1, cx + 1) + 1):
                        if not visited[ny, nx] and mask[ny, nx]:
                            visited[ny, nx] = 1
                            stack.append((nx, ny))

            bw = maxx - minx + 1
            bh = maxy - miny + 1
            if area < min_area or area > max_area:
                continue
            if bw < 8 or bh < 8:
                continue
            if (bw / max(bh, 1)) > 4.0 or (bh / max(bw, 1)) > 4.0:
                continue

            cx = int(round(sumx / max(area, 1)))
            cy = int(round(sumy / max(area, 1)))
            regions.append({
                "bbox": (max(0, minx - pad), max(0, miny - pad),
                         min(w, maxx + 1 + pad), min(top_h, maxy + 1 + pad)),
                "center": (cx, cy),
            })
    regions.sort(key=lambda r: r["center"][0])
    return regions


def _merge_dedup(base: List[dict], extra: List[dict]) -> List[dict]:
    merged = list(base)
    for er in extra:
        if not any(abs(er["center"][0] - br["center"][0]) < 15
                   and abs(er["center"][1] - br["center"][1]) < 15
                   for br in merged):
            merged.append(er)
    merged.sort(key=lambda r: r["center"][0])
    return merged


def _segment(img: Image.Image, allow_lower_tail: bool) -> List[dict]:
    top_h = 100
    arr = np.array(img.convert("RGB"), dtype=np.uint8)
    top = arr[:top_h, :250, :]

    regions: List[dict] = []
    for sat_thr, val_thr, min_area, max_area in [
        (0.20, 0.12, 25, 700),
        (0.16, 0.10, 12, 1400),
        (0.12, 0.08, 8, 2200),
    ]:
        mask = _color_mask(top, sat_thr=sat_thr, val_thr=val_thr)
        regs = _connected_components(mask, min_area=min_area, max_area=max_area, top_h=top_h)
        regions = _merge_dedup(regions, regs)
        if len(regions) >= 4:
            return regions[:8]

    if allow_lower_tail:
        top_h2 = min(112, arr.shape[0])
        top2 = arr[:top_h2, :250, :]
        mask2 = _color_mask(top2, sat_thr=0.12, val_thr=0.08)
        regs2 = _connected_components(mask2, min_area=8, max_area=2600, top_h=top_h2)
        regions = _merge_dedup(regions, regs2)

    return regions[:8]


class _OnnxCharModel:
    def __init__(self, onnx_path: str):
        self.session = ort.InferenceSession(onnx_path, providers=["CPUExecutionProvider"])
        self.input_name = self.session.get_inputs()[0].name

        meta = self.session.get_modelmeta().custom_metadata_map or {}
        try:
            self.input_size = int(meta.get("input_size", "48"))
        except Exception:
            self.input_size = 48
        self.preprocess = str(meta.get("preprocess", "none"))

        idx_to_cls_json = meta.get("idx_to_cls_json")
        if not idx_to_cls_json:
            raise RuntimeError(f"ONNX missing metadata key 'idx_to_cls_json': {onnx_path}")
        self.idx_to_cls = {int(k): v for k, v in json.loads(idx_to_cls_json).items()}

    def predict_topk(self, img: Image.Image, topk: int) -> List[Tuple[str, float]]:
        x = _preprocess_for_model(img, self.input_size, self.preprocess)
        logits = self.session.run(None, {self.input_name: x})[0][0]
        probs = _softmax(logits.astype(np.float64))
        k = max(1, min(int(topk), probs.shape[0]))
        idx = np.argsort(-probs)[:k]
        return [(self.idx_to_cls[int(i)], float(probs[int(i)])) for i in idx]


class _OnnxCaptchaSolver:
    def __init__(self, upper_onnx_path: str, title_onnx_path: Optional[str], allow_lower_tail: bool):
        self.upper = _OnnxCharModel(upper_onnx_path)
        self.title = _OnnxCharModel(title_onnx_path) if title_onnx_path else self.upper
        self.allow_lower_tail = bool(allow_lower_tail)

    @staticmethod
    def _topk_to_dict(preds: Sequence[Tuple[str, float]]) -> Dict[str, float]:
        d: Dict[str, float] = {}
        for ch, p in preds:
            d[ch] = max(d.get(ch, 0.0), float(p))
        return d

    @staticmethod
    def _normalize_topk(preds: Sequence[Tuple[str, float]], power: float = 0.55):
        if not preds:
            return []
        merged: Dict[str, float] = {}
        for ch, p in preds:
            merged[ch] = max(merged.get(ch, 0.0), float(p))
        chars = list(merged.keys())
        vals = np.array([max(1e-9, merged[c]) for c in chars], dtype=np.float64)
        vals = np.power(vals, power)
        vals = vals / max(float(vals.sum()), 1e-9)
        return [(chars[i], float(vals[i])) for i in range(len(chars))]

    def _score_title_region(self, title_candidates, region_preds) -> float:
        t = self._topk_to_dict(title_candidates)
        r = self._topk_to_dict(region_preds)
        if not t or not r:
            return 0.0
        score_soft = sum(tp * r[ch] for ch, tp in t.items() if ch in r)
        top1_char = title_candidates[0][0]
        score_hard = r.get(top1_char, 0.0)
        return float(score_soft + 0.20 * score_hard)

    def _get_title_candidates(self, img: Image.Image, k: int = 4):
        out = []
        for x1, x2 in TITLE_RANGES:
            crop = img.crop((x1, 100, x2, 120))
            preds = self.title.predict_topk(crop, topk=max(2, k))
            out.append(self._normalize_topk(preds, power=0.55))
        return out

    def solve(self, img: Image.Image):
        title_candidates = self._get_title_candidates(img, k=4)
        if len(title_candidates) != 4 or any(len(c) == 0 for c in title_candidates):
            return None

        regions = _segment(img, allow_lower_tail=self.allow_lower_tail)
        if len(regions) < 4:
            return None

        for r in regions:
            r["crop"] = img.crop(r["bbox"])

        region_preds = [self.upper.predict_topk(r["crop"], topk=8) for r in regions]
        n = len(regions)
        score = np.zeros((4, n), dtype=np.float64)
        for ti in range(4):
            for ri in range(n):
                score[ti, ri] = self._score_title_region(title_candidates[ti], region_preds[ri])

        best_s = -1.0
        best_perm = None
        for perm in permutations(range(n), 4):
            s = sum(score[i, perm[i]] for i in range(4))
            if s > best_s:
                best_s = s
                best_perm = perm
        if best_perm is None or best_s <= 0.0:
            return None

        return [regions[best_perm[i]]["center"] for i in range(4)]

    def solve_from_base64(self, b64_body: str):
        raw = base64.b64decode(b64_body)
        img = Image.open(io.BytesIO(raw)).convert("RGB")
        return self.solve(img)


def _build_solver():
    if not _exists(UPPER_ONNX_PATH):
        raise RuntimeError(f"ONNX file missing: {UPPER_ONNX_PATH}")
    title_onnx = TITLE_ONNX_PATH if _exists(TITLE_ONNX_PATH) else None
    return _OnnxCaptchaSolver(
        upper_onnx_path=UPPER_ONNX_PATH,
        title_onnx_path=title_onnx,
        allow_lower_tail=ALLOW_LOWER_TAIL,
    )


def _get_solver():
    global _SOLVER, _INIT_ERROR
    if _SOLVER is not None:
        return _SOLVER
    if _INIT_ERROR is not None:
        raise _INIT_ERROR

    with _LOCK:
        if _SOLVER is not None:
            return _SOLVER
        if _INIT_ERROR is not None:
            raise _INIT_ERROR
        try:
            _SOLVER = _build_solver()
            return _SOLVER
        except Exception as e:
            _INIT_ERROR = e
            raise


def solve_captcha_from_base64(img_gif_b64_body: str) -> Optional[List[Point]]:
    try:
        solver = _get_solver()
        out = solver.solve_from_base64(img_gif_b64_body)
        if not out or len(out) != 4:
            return None
        return [(int(x), int(y)) for x, y in out]
    except Exception as e:
        print(f"[captcha] solve failed: {e}")
        return None
