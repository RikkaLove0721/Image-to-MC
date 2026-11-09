"""语义层：把"窗户"从像素里认出来，整块替换成玻璃。

为什么需要这一步：
    照片里一扇窗由窗框、玻璃反光、窗帘、空调外机拼成几十个像素，降到
    1 像素 = 1 方块后变成一片灰白噪点。而真实建筑建模时，一面窗就是
    1 块玻璃。与其逐像素瞎猜，不如先认出"窗洞"这个结构，整块填玻璃。

判别依据（不是靠亮度，而是靠**色温**）：
    砖墙/涂料墙是暖色（R 明显大于 B），窗框玻璃是中性或偏冷（R≈B）。
    归一化色温 (R-B)/(R+B) 对光照强弱不敏感 —— 阴影里的砖墙依然比
    玻璃暖得多，所以阴面、逆光也能分对。

流程：
    1. 色温 + 亮度阈值 -> 中性掩码；
    2. 行投影 -> "楼层带"（一排窗会形成一条横向的高占比带）；
    3. 带内列投影 -> "窗洞"（连续若干列都够中性就是一面窗）；
    4. 尺寸/长宽比过滤掉白色横腰线、竖向壁柱、天空、地面。
"""

from __future__ import annotations

import numpy as np


def luma(rgb: np.ndarray) -> np.ndarray:
    return 0.2126 * rgb[..., 0] + 0.7152 * rgb[..., 1] + 0.0722 * rgb[..., 2]


def saturation(rgb: np.ndarray) -> np.ndarray:
    return rgb.max(axis=-1) - rgb.min(axis=-1)


def warmth(rgb: np.ndarray) -> np.ndarray:
    """归一化色温：>0.25 是砖/土/木，≈0 是灰白/玻璃。"""
    r, b = rgb[..., 0], rgb[..., 2]
    return (r - b) / (r + b + 1e-6)


def _box_mean(a: np.ndarray, r: int = 1) -> np.ndarray:
    if r <= 0:
        return a
    h, w = a.shape
    p = np.pad(a, r, mode="edge")
    s = np.zeros((p.shape[0] + 1, p.shape[1] + 1), dtype=np.float64)
    s[1:, 1:] = p.cumsum(0).cumsum(1)
    k = 2 * r + 1
    return (s[k:, k:] - s[:-k, k:] - s[k:, :-k] + s[:-k, :-k]) / (k * k)


def _runs(flags: np.ndarray, min_len: int = 1) -> list[tuple[int, int]]:
    """一维布尔数组 -> [start, end] 闭区间段。"""
    out: list[tuple[int, int]] = []
    i, n = 0, len(flags)
    while i < n:
        if flags[i]:
            j = i
            while j + 1 < n and flags[j + 1]:
                j += 1
            if j - i + 1 >= min_len:
                out.append((i, j))
            i = j + 1
        else:
            i += 1
    return out


def neutral_mask(
    rgb: np.ndarray,
    warm_thr: float = 0.16,
    lum_lo: float = 0.30,
    lum_hi: float = 0.98,
    blur: int = 1,
) -> np.ndarray:
    """亮而中性的像素 = 窗框 + 玻璃。"""
    w = _box_mean(warmth(rgb), blur)
    l = _box_mean(luma(rgb), blur)
    return (w < warm_thr) & (l > lum_lo) & (l < lum_hi)


def detect_windows(
    rgb: np.ndarray,
    skip: np.ndarray | None = None,
    warm_thr: float = 0.16,
    lum_lo: float = 0.30,
    row_frac: float = 0.10,
    col_frac: float = 0.45,
    min_w: int = 2,
    max_w_frac: float = 0.34,
    max_h_frac: float = 0.22,
    merge: bool = True,
) -> tuple[np.ndarray, list[tuple[int, int, int, int]]]:
    """返回 (窗洞掩码, 矩形列表)。矩形为图像坐标 (x0, y0, x1, y1)，闭区间。"""
    h, w = rgb.shape[:2]
    cand = neutral_mask(rgb, warm_thr, lum_lo)
    if skip is not None:
        cand &= ~skip
    if merge:
        # 1 像素闭运算：补上窗框与玻璃之间的缝，避免一扇窗碎成几块
        cand = _box_mean(cand.astype(np.float64), 1) > 0.55

    max_w = max(min_w, int(round(max_w_frac * w)))
    max_h = max(1, int(round(max_h_frac * h)))

    boxes: list[tuple[int, int, int, int]] = []
    row_score = cand.mean(axis=1)

    for y0, y1 in _runs(row_score >= row_frac):
        bh = y1 - y0 + 1
        if bh < 1 or bh > max_h:
            continue
        band = cand[y0 : y1 + 1]
        col_score = band.mean(axis=0)
        for x0, x1 in _runs(col_score >= col_frac, min_len=min_w):
            bw = x1 - x0 + 1
            if bw < min_w or bw > max_w:
                continue
            fill = float(band[:, x0 : x1 + 1].mean())
            if fill < 0.55:
                continue
            boxes.append((x0, y0, x1, y1))

    mask = np.zeros((h, w), dtype=bool)
    for x0, y0, x1, y1 in boxes:
        mask[y0 : y1 + 1, x0 : x1 + 1] = True
    return mask, boxes


def snap(boxes: list[tuple[int, int, int, int]], w: int, h: int, gap: int = 2):
    """把矩形的上下沿/左右沿对齐到同一套网格。

    手工建模之所以"像"，很大程度靠这种规整；照片给的边沿总会差一格。
    """
    if not boxes:
        return boxes

    def cluster(vals: list[int]) -> dict[int, int]:
        vals = sorted(set(vals))
        groups: list[list[int]] = []
        for v in vals:
            if groups and v - groups[-1][-1] <= gap:
                groups[-1].append(v)
            else:
                groups.append([v])
        return {v: int(round(float(np.mean(g)))) for g in groups for v in g}

    cy = cluster([b[1] for b in boxes] + [b[3] for b in boxes])
    cx = cluster([b[0] for b in boxes] + [b[2] for b in boxes])
    out = []
    for x0, y0, x1, y1 in boxes:
        nx0 = max(0, min(cx.get(x0, x0), w - 1))
        nx1 = max(0, min(cx.get(x1, x1), w - 1))
        ny0 = max(0, min(cy.get(y0, y0), h - 1))
        ny1 = max(0, min(cy.get(y1, y1), h - 1))
        if nx1 <= nx0:
            nx1 = min(nx0 + 1, w - 1)
        out.append((nx0, ny0, nx1, ny1))
    return out


def debug_overlay(rgb: np.ndarray, boxes, scale: int = 12) -> np.ndarray:
    """把检测到的窗洞描成青框，用于肉眼校验。"""
    img = (np.clip(rgb, 0, 1) * 255).astype(np.uint8).copy()
    for x0, y0, x1, y1 in boxes:
        img[y0, x0 : x1 + 1] = (0, 230, 230)
        img[y1, x0 : x1 + 1] = (0, 230, 230)
        img[y0 : y1 + 1, x0] = (0, 230, 230)
        img[y0 : y1 + 1, x1] = (0, 230, 230)
    return np.repeat(np.repeat(img, scale, axis=0), scale, axis=1)


# --------------------------------------------------------------------------
# 面向"方块网格"的接口：在 2 倍分辨率上判色温，再面积平均降回方块网格，
# 最后按"尺寸像不像一扇窗"过滤。
#
# 直接在 1 像素 = 1 方块的低分辨率上判色温会因为混色而失败，所以先高后低。
# --------------------------------------------------------------------------

def _components(mask: np.ndarray) -> list[list[tuple[int, int]]]:
    """4 邻接连通域，返回每块的格点列表。"""
    h, w = mask.shape
    seen = np.zeros_like(mask, dtype=bool)
    out: list[list[tuple[int, int]]] = []
    ys, xs = np.nonzero(mask)
    for y0, x0 in zip(ys, xs):
        if seen[y0, x0]:
            continue
        stack = [(int(y0), int(x0))]
        seen[y0, x0] = True
        cells = []
        while stack:
            y, x = stack.pop()
            cells.append((y, x))
            for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                ny, nx = y + dy, x + dx
                if 0 <= ny < h and 0 <= nx < w and mask[ny, nx] and not seen[ny, nx]:
                    seen[ny, nx] = True
                    stack.append((ny, nx))
        out.append(cells)
    return out


def _open(mask: np.ndarray, k: int = 2, axis: int = 1) -> np.ndarray:
    """沿指定轴做长度 k 的开运算：去掉撑不满 k 格的短条。"""
    if k <= 1:
        return mask
    m = mask.astype(np.int32)
    if axis == 1:
        pad = np.pad(m, ((0, 0), (k // 2, k - 1 - k // 2)), mode="constant")
        s = sum(pad[:, i : i + m.shape[1]] for i in range(k))
    else:
        pad = np.pad(m, ((k // 2, k - 1 - k // 2), (0, 0)), mode="constant")
        s = sum(pad[i : i + m.shape[0], :] for i in range(k))
    return s >= k


def _keep_window_shaped(
    mask: np.ndarray,
    min_w: int = 2,
    max_w: int = 6,
    min_h: int = 1,
    max_h: int = 3,
    min_fill: float = 0.40,
    fill_bbox: bool = True,
) -> tuple[np.ndarray, int]:
    """只保留"像一扇窗"的连通域：小、方、实心。

    屋顶白边、地面、白色腰线都是一大片连通域，尺寸对不上就被这一步筛掉。
    保留下来的连通域直接**填满外接框**：窗框和窗棂造成的小洞一并抹平，
    正好对应"一扇窗 = 一块玻璃"。
    """
    out = np.zeros_like(mask)
    kept = 0
    for cells in _components(mask):
        ys = [c[0] for c in cells]
        xs = [c[1] for c in cells]
        bw = max(xs) - min(xs) + 1
        bh = max(ys) - min(ys) + 1
        fill = len(cells) / (bw * bh)
        if not (min_w <= bw <= max_w and min_h <= bh <= max_h):
            continue
        if fill < min_fill:
            continue
        if fill_bbox:
            out[min(ys) : max(ys) + 1, min(xs) : max(xs) + 1] = True
        else:
            for y, x in cells:
                out[y, x] = True
        kept += 1
    return out, kept


def window_grid(
    hi_rgb: np.ndarray,
    target_hw: tuple[int, int],
    hi_skip: np.ndarray | None = None,
    warm_thr: float = 0.12,
    cell_thr: float = 0.45,
    min_w: int = 2,
    max_w: int = 6,
    min_h: int = 1,
    max_h: int = 3,
    guard: int = 1,
) -> tuple[np.ndarray, dict]:
    """在 2 倍图上判窗，降采样到 (h, w) 的方块网格。

    返回 (窗掩码 (h,w), 统计信息)。
    """
    from PIL import Image

    h, w = target_hw
    cand = neutral_mask(hi_rgb, warm_thr)
    if hi_skip is not None:
        cand &= ~hi_skip

    img = Image.fromarray((cand * 255).astype(np.uint8)).resize((w, h), Image.BOX)
    frac = np.asarray(img, dtype=np.float64) / 255.0
    mask = frac > cell_thr

    if guard:
        mask[:guard, :] = False
        mask[-guard:, :] = False
        mask[:, :guard] = False
        mask[:, -guard:] = False

    # 低分辨率下（一扇窗只有 1~2 格）不能做长度 2 的开运算，会把窗全削掉
    k_open = 2 if w >= 30 else 1
    mask = _open(mask, k_open, axis=1) & _open(mask, k_open, axis=0)
    mask, n = _keep_window_shaped(mask, min_w, max_w, min_h, max_h)

    return mask, {
        "windows": n,
        "cells": int(mask.sum()),
        "ratio": round(float(mask.sum()) / max(h * w, 1), 3),
    }
