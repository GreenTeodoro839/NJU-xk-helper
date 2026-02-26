"""
Pure ONNX captcha solver (v2 — color-isolated models + Hungarian matching).

Exports one function:
  solve_captcha_from_base64(img_gif_b64_body) -> [(x, y) * 4] or None

Changes vs v1:
  - Color-isolated upper char cropping (preserves stroke color/texture)
  - ImageNet normalization (matches new pretrained-backbone models)
  - Corrected title crop positions (centers at 127/150/173/196, y=101-117)
  - Hungarian matching (optimal, replaces brute-force permutations)
  - Cleaner segmentation with color-distance filtering
"""

from __future__ import annotations

import base64
import io
import json
import os
import threading
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import onnxruntime as ort
from PIL import Image

Point = Tuple[int, int]

# Bottom title char crop positions (fixed layout)
# "依次点击【char1 char2 char3 char4】"
TITLE_X_CENTERS = [127, 150, 173, 196]
TITLE_Y_TOP = 101
TITLE_Y_BOTTOM = 117
TITLE_HALF_X = 10

# Upper area boundary
UPPER_HEIGHT = 100

# Model paths (relative to project root / models)
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UPPER_ONNX_PATH = os.path.join(_PROJECT_ROOT, "models", "upper_model.onnx")
TITLE_ONNX_PATH = os.path.join(_PROJECT_ROOT, "models", "title_model.onnx")

_LOCK = threading.Lock()
_SOLVER = None
_INIT_ERROR = None

# ---------------------------------------------------------------------------
# Normalization presets
# ---------------------------------------------------------------------------

_NORM_PRESETS = {
    "imagenet": {
        "mean": np.array([0.485, 0.456, 0.406], dtype=np.float32),
        "std":  np.array([0.229, 0.224, 0.225], dtype=np.float32),
    },
    "half": {
        "mean": np.array([0.5, 0.5, 0.5], dtype=np.float32),
        "std":  np.array([0.5, 0.5, 0.5], dtype=np.float32),
    },
}


def _softmax(x: np.ndarray) -> np.ndarray:
    x = x - np.max(x)
    ex = np.exp(x)
    return ex / max(float(ex.sum()), 1e-9)


# ---------------------------------------------------------------------------
# Upper char segmentation: color-based isolation
# ---------------------------------------------------------------------------

def _fg_mask(arr: np.ndarray, sat_thr: float = 0.15) -> np.ndarray:
    """Foreground mask based on saturation (colored chars on light bg)."""
    r = arr[..., 0].astype(np.float32)
    g = arr[..., 1].astype(np.float32)
    b = arr[..., 2].astype(np.float32)
    maxc = np.maximum(np.maximum(r, g), b)
    minc = np.minimum(np.minimum(r, g), b)
    sat = (maxc - minc) / (maxc + 1e-6)
    light_bg = (arr[..., 0] > 165) & (arr[..., 1] > 205) & (arr[..., 2] > 225)
    return (sat > sat_thr) & (~light_bg)


def _connected_components(mask: np.ndarray, min_area: int = 25) -> List[dict]:
    """Simple flood-fill connected components (no scipy dependency)."""
    h, w = mask.shape
    visited = np.zeros((h, w), dtype=np.uint8)
    regions = []

    for y in range(h):
        for x in range(w):
            if not mask[y, x] or visited[y, x]:
                continue
            stack = [(x, y)]
            visited[y, x] = 1
            pixels = []

            while stack:
                cx, cy = stack.pop()
                pixels.append((cx, cy))
                for ny in range(max(0, cy - 1), min(h, cy + 2)):
                    for nx in range(max(0, cx - 1), min(w, cx + 2)):
                        if not visited[ny, nx] and mask[ny, nx]:
                            visited[ny, nx] = 1
                            stack.append((nx, ny))

            if len(pixels) < min_area:
                continue

            xs = [p[0] for p in pixels]
            ys = [p[1] for p in pixels]
            bw = max(xs) - min(xs) + 1
            bh = max(ys) - min(ys) + 1
            if bw < 6 or bh < 6:
                continue
            if bw / max(bh, 1) > 5 or bh / max(bw, 1) > 5:
                continue

            regions.append({
                "center": (int(np.mean(xs)), int(np.mean(ys))),
                "bbox": (min(xs), min(ys), max(xs) + 1, max(ys) + 1),
                "area": len(pixels),
            })

    regions.sort(key=lambda r: -r["area"])
    return regions


def _merge_nearby_regions(regions: List[dict], dist_thresh: int = 20) -> List[dict]:
    """
    Merge regions whose centers are within dist_thresh pixels.
    Fixes split characters (e.g. 传 splitting into top+bottom halves).
    """
    if len(regions) <= 1:
        return regions

    merged = [dict(r) for r in regions]
    changed = True
    while changed:
        changed = False
        for i in range(len(merged)):
            for j in range(i + 1, len(merged)):
                ci = merged[i]["center"]
                cj = merged[j]["center"]
                dist = ((ci[0] - cj[0]) ** 2 + (ci[1] - cj[1]) ** 2) ** 0.5
                if dist < dist_thresh:
                    # Merge j into i
                    a_i, a_j = merged[i]["area"], merged[j]["area"]
                    total = a_i + a_j
                    # Weighted center
                    new_cx = int((ci[0] * a_i + cj[0] * a_j) / total)
                    new_cy = int((ci[1] * a_i + cj[1] * a_j) / total)
                    # Merged bbox
                    bi, bj = merged[i]["bbox"], merged[j]["bbox"]
                    new_bbox = (
                        min(bi[0], bj[0]), min(bi[1], bj[1]),
                        max(bi[2], bj[2]), max(bi[3], bj[3]),
                    )
                    merged[i] = {
                        "center": (new_cx, new_cy),
                        "bbox": new_bbox,
                        "area": total,
                    }
                    merged.pop(j)
                    changed = True
                    break
            if changed:
                break

    merged.sort(key=lambda r: -r["area"])
    return merged


def _segment_upper(img: Image.Image) -> List[dict]:
    """
    Find 4 character regions in upper area.
    Returns list of dicts with 'center', 'bbox', 'area'.
    """
    arr = np.array(img.convert("RGB"), dtype=np.uint8)[:UPPER_HEIGHT, :]

    # Try progressively relaxed thresholds
    for sat_thr, min_area in [(0.18, 25), (0.14, 15), (0.10, 8), (0.06, 8)]:
        mask = _fg_mask(arr, sat_thr=sat_thr)
        regions = _connected_components(mask, min_area=min_area)
        regions = _merge_nearby_regions(regions, dist_thresh=20)
        if len(regions) >= 4:
            return regions[:8]

    return regions[:8] if regions else []


def _crop_upper_char_color_isolated(
    arr: np.ndarray,
    cx: int, cy: int,
    search_half: int = 40,
    color_thresh: float = 80.0,
    pad: int = 4,
) -> Image.Image:
    """
    Crop a single upper character using color isolation.
    Keeps only pixels whose color is similar to the center pixel's color.
    """
    H, W = min(UPPER_HEIGHT, arr.shape[0]), arr.shape[1]
    arr_f = arr[:H].astype(np.float32)

    fg = _fg_mask(arr[:H])

    # Sample dominant color from center
    sr = 4
    sy1, sy2 = max(0, cy - sr), min(H, cy + sr)
    sx1, sx2 = max(0, cx - sr), min(W, cx + sr)
    center_fg = fg[sy1:sy2, sx1:sx2]

    if center_fg.sum() < 3:
        # Fallback: relax
        fg = _fg_mask(arr[:H], sat_thr=0.08)
        center_fg = fg[sy1:sy2, sx1:sx2]

    if center_fg.sum() >= 3:
        center_color = arr_f[sy1:sy2, sx1:sx2][center_fg].mean(axis=0)
    else:
        center_color = arr_f[cy, cx]

    # Search window
    ax1 = max(0, cx - search_half)
    ay1 = max(0, cy - search_half)
    ax2 = min(W, cx + search_half)
    ay2 = min(H, cy + search_half)

    local = arr_f[ay1:ay2, ax1:ax2]
    local_fg = fg[ay1:ay2, ax1:ax2]

    color_dist = np.sqrt(((local - center_color) ** 2).sum(axis=-1))
    char_mask = local_fg & (color_dist < color_thresh)

    if char_mask.sum() < 10:
        # Fallback: use all fg in tighter window
        h2 = 25
        by1 = max(0, cy - h2 - ay1)
        bx1 = max(0, cx - h2 - ax1)
        by2 = min(local.shape[0], cy + h2 - ay1)
        bx2 = min(local.shape[1], cx + h2 - ax1)
        char_mask = np.zeros_like(char_mask)
        char_mask[by1:by2, bx1:bx2] = local_fg[by1:by2, bx1:bx2]

    # Build isolated image
    isolated = np.full_like(local, 220.0)
    isolated[char_mask] = local[char_mask]

    # Tight crop + pad to square
    ys, xs = np.where(char_mask)
    if len(xs) < 5:
        # Emergency fallback: naive crop
        half = 25
        crop = arr[max(0, cy-half):min(H, cy+half), max(0, cx-half):min(W, cx+half)]
        h, w = crop.shape[:2]
        side = max(h, w)
        canvas = np.full((side, side, 3), 220, dtype=np.uint8)
        canvas[(side-h)//2:(side-h)//2+h, (side-w)//2:(side-w)//2+w] = crop
        return Image.fromarray(canvas)

    tx1 = max(0, int(xs.min()) - pad)
    ty1 = max(0, int(ys.min()) - pad)
    tx2 = min(isolated.shape[1], int(xs.max()) + 1 + pad)
    ty2 = min(isolated.shape[0], int(ys.max()) + 1 + pad)
    tight = isolated[ty1:ty2, tx1:tx2]

    h, w = tight.shape[:2]
    side = max(h, w)
    canvas = np.full((side, side, 3), 220.0, dtype=np.float32)
    canvas[(side-h)//2:(side-h)//2+h, (side-w)//2:(side-w)//2+w] = tight

    return Image.fromarray(canvas.astype(np.uint8))


# ---------------------------------------------------------------------------
# Title (bottom) char cropping
# ---------------------------------------------------------------------------

def _crop_title_chars(img: Image.Image) -> List[Image.Image]:
    """Crop 4 title characters from fixed bottom positions."""
    crops = []
    for tx in TITLE_X_CENTERS:
        x1, x2 = tx - TITLE_HALF_X, tx + TITLE_HALF_X
        crop = img.crop((x1, TITLE_Y_TOP, x2, TITLE_Y_BOTTOM))
        w, h = crop.size
        side = max(w, h)
        canvas = Image.new("RGB", (side, side), (0, 0, 0))
        canvas.paste(crop, ((side - w) // 2, (side - h) // 2))
        crops.append(canvas)
    return crops


# ---------------------------------------------------------------------------
# Preprocessing for ONNX model
# ---------------------------------------------------------------------------

def _preprocess(img: Image.Image, input_size: int, norm: str) -> np.ndarray:
    """Resize + normalize → [1, 3, H, W] float32 for ONNX."""
    img = img.resize((input_size, input_size), Image.LANCZOS).convert("RGB")
    arr = np.asarray(img, dtype=np.float32) / 255.0  # [H, W, 3] in [0, 1]

    preset = _NORM_PRESETS.get(norm, _NORM_PRESETS["imagenet"])
    arr = (arr - preset["mean"]) / preset["std"]

    return arr.transpose(2, 0, 1)[None, ...].astype(np.float32)  # [1, 3, H, W]


# ---------------------------------------------------------------------------
# Hungarian matching (no scipy dependency)
# ---------------------------------------------------------------------------

def _hungarian_4x4(cost: np.ndarray) -> List[Tuple[int, int]]:
    """
    Optimal assignment for a 4×N cost matrix (N >= 4).
    For small N, brute-force over permutations is fast enough.
    """
    from itertools import permutations
    n = cost.shape[1]
    best_cost = float("inf")
    best_perm = None
    for perm in permutations(range(n), 4):
        c = sum(cost[i, perm[i]] for i in range(4))
        if c < best_cost:
            best_cost = c
            best_perm = perm
    if best_perm is None:
        return [(i, i) for i in range(4)]
    return [(i, best_perm[i]) for i in range(4)]


# ---------------------------------------------------------------------------
# ONNX model wrapper
# ---------------------------------------------------------------------------

class _OnnxCharModel:
    def __init__(self, onnx_path: str):
        self.session = ort.InferenceSession(
            onnx_path, providers=["CPUExecutionProvider"]
        )
        self.input_name = self.session.get_inputs()[0].name

        meta = self.session.get_modelmeta().custom_metadata_map or {}
        self.input_size = int(meta.get("input_size", "80"))
        self.normalize = str(meta.get("normalize", "imagenet"))

        idx_json = meta.get("idx_to_cls_json")
        if not idx_json:
            raise RuntimeError(f"ONNX missing 'idx_to_cls_json': {onnx_path}")
        self.idx_to_cls = {int(k): v for k, v in json.loads(idx_json).items()}
        self.num_classes = len(self.idx_to_cls)

    def predict_probs(self, img: Image.Image) -> np.ndarray:
        """Return softmax probabilities [num_classes]."""
        x = _preprocess(img, self.input_size, self.normalize)
        logits = self.session.run(None, {self.input_name: x})[0][0]
        return _softmax(logits.astype(np.float64))

    def predict_topk(self, img: Image.Image, k: int = 5) -> List[Tuple[str, float]]:
        probs = self.predict_probs(img)
        k = min(k, len(probs))
        idx = np.argsort(-probs)[:k]
        return [(self.idx_to_cls[int(i)], float(probs[i])) for i in idx]


# ---------------------------------------------------------------------------
# Solver
# ---------------------------------------------------------------------------

class _OnnxCaptchaSolver:
    def __init__(self, upper_path: str, title_path: Optional[str]):
        self.upper = _OnnxCharModel(upper_path)
        self.title = _OnnxCharModel(title_path) if title_path else self.upper

    def solve(self, img: Image.Image) -> Optional[List[Point]]:
        # 1. Segment upper area → find 4 char regions
        regions = _segment_upper(img)
        if len(regions) < 4:
            return None

        arr = np.array(img.convert("RGB"), dtype=np.uint8)

        # 2. Color-isolate each upper char → get crops and probs
        upper_crops = []
        upper_centers = []
        for r in regions[:min(len(regions), 8)]:
            cx, cy = r["center"]
            crop = _crop_upper_char_color_isolated(arr, cx, cy)
            upper_crops.append(crop)
            upper_centers.append((cx, cy))

        upper_probs = [self.upper.predict_probs(c) for c in upper_crops]

        # 3. Crop title chars → classify
        title_crops = _crop_title_chars(img)
        title_top1 = []
        for tc in title_crops:
            preds = self.title.predict_topk(tc, k=1)
            if not preds:
                return None
            title_top1.append(preds[0][0])  # top-1 char

        # 4. Build cost matrix and do Hungarian matching
        #    cost[t][r] = -log P(upper_r == title_char_t)
        n_upper = len(upper_crops)
        cost = np.full((4, n_upper), 100.0, dtype=np.float64)

        for ti, target_char in enumerate(title_top1):
            # Find target class index in upper model
            target_idx = None
            for idx, cls in self.upper.idx_to_cls.items():
                if cls == target_char:
                    target_idx = idx
                    break
            if target_idx is None:
                continue
            for ri in range(n_upper):
                p = max(upper_probs[ri][target_idx], 1e-10)
                cost[ti, ri] = -np.log(p)

        # 5. Optimal matching
        matches = _hungarian_4x4(cost)

        # 6. Return click positions in title order
        result = []
        for ti, ri in matches:
            result.append(upper_centers[ri])

        return result

    def solve_from_base64(self, b64_body: str) -> Optional[List[Point]]:
        raw = base64.b64decode(b64_body)
        img = Image.open(io.BytesIO(raw)).convert("RGB")
        return self.solve(img)


# ---------------------------------------------------------------------------
# Module-level API
# ---------------------------------------------------------------------------

def _build_solver():
    if not Path(UPPER_ONNX_PATH).exists():
        raise RuntimeError(f"ONNX not found: {UPPER_ONNX_PATH}")
    title_path = TITLE_ONNX_PATH if Path(TITLE_ONNX_PATH).exists() else None
    return _OnnxCaptchaSolver(UPPER_ONNX_PATH, title_path)


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
    """Main entry point: base64 image → list of 4 (x, y) click coords, or None."""
    try:
        solver = _get_solver()
        out = solver.solve_from_base64(img_gif_b64_body)
        if not out or len(out) != 4:
            return None
        return [(int(x), int(y)) for x, y in out]
    except Exception as e:
        print(f"[captcha] solve failed: {e}")
        return None
