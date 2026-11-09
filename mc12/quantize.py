"""调色板精简、逐像素分配与邻域去噪。

核心目标是"尽量少的方块种类"：
  1. 贪心集合覆盖：每次挑一个能"新覆盖最多像素"的方块，直到用满 K 种；
  2. 逐像素分配给已选方块中色差最小的那个；
  3. 邻域投票：把孤立单块并进周围多数色，消除棋盘状斑点。
"""

from __future__ import annotations

import numpy as np

from .color import delta_e_00, rgb_to_lab
from .palette import Block, all_blocks, dedupe, lab_matrix, rough_flags


def cost_matrix(pix_lab: np.ndarray, blocks: list[Block], rough_penalty: float = 1.0) -> np.ndarray:
    """(P, B) 的匹配代价矩阵。粗糙纹理方块加一点惩罚，避免在纯色墙面上乱入圆石。"""
    blab = lab_matrix(blocks)
    cost = delta_e_00(pix_lab[:, None, :], blab[None, :, :])
    if rough_penalty:
        cost = cost + rough_penalty * rough_flags(blocks)[None, :]
    return cost


def select_palette(
    cost: np.ndarray,
    mask: np.ndarray,
    k: int,
    tol: float = 6.0,
    blocks: list[Block] | None = None,
    min_gain_ratio: float = 0.015,
) -> list[int]:
    """贪心选方块：每次挑"能让总色差下降最多"的那一个。

    用相对改善量判停（新增方块带来的收益低于总误差的 1.5% 就收手），
    这样方块种类会自然收敛到 K 以内，而不会为了凑数塞进颜色几乎
    重复的方块 —— 这正是"尽量精简方块种类"想要的。
    """
    n_pix, n_blk = cost.shape
    if not mask.any():
        raise ValueError("没有有效像素")

    chosen: list[int] = []
    available = np.ones(n_blk, dtype=bool)
    best = np.full(n_pix, 1e6, dtype=np.float64)

    for _ in range(min(k, n_blk)):
        before = float(np.where(mask, best, 0.0).sum())
        cand = np.minimum(best[:, None], cost)                # (P, B)
        gain = np.where(mask[:, None], best[:, None] - cand, 0.0).sum(axis=0)
        gain[~available] = -1.0

        pick = int(np.argmax(gain))
        if gain[pick] <= 0:
            break
        if chosen and gain[pick] < min_gain_ratio * before:
            break

        chosen.append(pick)
        available[pick] = False
        best = np.minimum(best, cost[:, pick])

    # 去掉颜色几乎重复的方块（ΔE00 < 4.0 视为同一种）
    if len(chosen) > 1 and blocks is not None:
        blab = lab_matrix(blocks)
        dedup: list[int] = []
        for c in chosen:
            if dedup and float(np.min(delta_e_00(blab[dedup], blab[c]))) < 4.0:
                continue
            dedup.append(c)
        chosen = dedup

    return chosen


def assign(cost: np.ndarray, chosen: list[int]) -> np.ndarray:
    """把每个像素分配给已选方块中代价最小的一个，返回值是 chosen 的下标。"""
    sub = cost[:, chosen]
    return np.argmin(sub, axis=1).astype(np.int32)


def _box3_sum(a: np.ndarray) -> np.ndarray:
    p = np.pad(np.asarray(a, dtype=np.float64), 1, mode="edge")
    s = np.zeros((p.shape[0] + 1, p.shape[1] + 1), dtype=np.float64)
    s[1:, 1:] = p.cumsum(0).cumsum(1)
    return s[3:, 3:] - s[:-3, 3:] - s[3:, :-3] + s[:-3, :-3]


def despeckle(idx2d: np.ndarray, k: int, passes: int = 1) -> np.ndarray:
    """只清除"孤立单块"的去噪。

    普通众数滤波会把 1 格宽的窗框、栏杆直接抹掉；这里只在某个方块在
    自身 3x3 邻域内**完全独一份**时才把它并入周围多数色，因此线条和
    边缘都能保住。
    """
    out = idx2d.astype(np.int32).copy()
    if k <= 1:
        return out
    rows = np.arange(out.shape[0])[:, None]
    cols = np.arange(out.shape[1])[None, :]

    for _ in range(passes):
        counts = np.stack([_box3_sum((out == i).astype(np.float64)) for i in range(k)])
        center = counts[out, rows, cols]
        mode = np.argmax(counts, axis=0).astype(np.int32)
        out = np.where(center < 1.5, mode, out).astype(np.int32)
    return out


def kmeans_lab(
    lab: np.ndarray,
    k: int,
    l_weight: float = 0.5,
    iters: int = 30,
    seed: int = 0,
) -> tuple[np.ndarray, np.ndarray]:
    """在 Lab 空间做 k-means，返回 (簇心, 每点所属簇)。

    L 通道降权是关键：照片上一面砖墙，受光处亮、背光处暗，L 能差出 20；
    但它们**是同一种材料**。把 L 的权重压到 0.5，聚类就不会把一面墙拆成
    "亮砖""暗砖"两种方块 —— 这正是"Minecraft 建筑一面墙只有一个颜色"。
    """
    n = lab.shape[0]
    k = max(1, min(int(k), n))
    w = np.array([l_weight, 1.0, 1.0], dtype=np.float64)
    v = lab * w
    rng = np.random.default_rng(seed)

    centers = [v[rng.integers(n)].copy()]
    d2 = ((v - centers[0]) ** 2).sum(1)
    for _ in range(k - 1):
        tot = float(d2.sum())
        if tot <= 1e-12:
            centers.append(v[rng.integers(n)].copy())
            continue
        idx = int(rng.choice(n, p=d2 / tot))
        centers.append(v[idx].copy())
        d2 = np.minimum(d2, ((v - v[idx]) ** 2).sum(1))
    c = np.array(centers)

    assign = np.zeros(n, dtype=np.int32)
    for _ in range(iters):
        d = ((v[:, None, :] - c[None, :, :]) ** 2).sum(-1)
        assign = d.argmin(1).astype(np.int32)
        new_c = c.copy()
        for j in range(k):
            m = assign == j
            if m.any():
                new_c[j] = v[m].mean(0)
            else:
                far = int(d.min(1).argmax())
                new_c[j] = v[far]
        if np.allclose(new_c, c, atol=1e-7):
            c = new_c
            break
        c = new_c

    d = ((v[:, None, :] - c[None, :, :]) ** 2).sum(-1)
    assign = d.argmin(1).astype(np.int32)
    return c / w, assign


def quantize(
    rgb_grid: np.ndarray,
    valid: np.ndarray,
    k: int = 12,
    tol: float = 6.0,
    smooth_passes: int = 1,
    rough_penalty: float = 1.0,
    l_weight: float = 0.5,
    dedupe_thr: float = 5.0,
) -> tuple[np.ndarray, list[Block], dict]:
    """把 (H,W,3) 颜色网格压成 (H,W) 方块索引网格（-1 = 不放方块）。

    两步走，而不是"逐像素找最近方块"：
      1. **聚类**：把整面墙的所有像素归成同一个"主色"，一张图通常只剩
         4~8 个主色；
      2. **映射**：每个主色找一个最接近的方块。

    这样一面砖墙必然是一种方块，不会出现"这块深一点、那块浅一点"的花斑。
    """
    h, w = rgb_grid.shape[:2]
    # 候选库先去重：颜色几乎一样的方块只留一个代表，
    # 否则一面墙会被拆成"安山岩 + 淡灰羊毛"两种，看着就花。
    blocks = dedupe(dedupe_thr)

    flat_rgb = rgb_grid.reshape(-1, 3)
    flat_lab = rgb_to_lab(flat_rgb)
    valid_flat = valid.reshape(-1)
    if not valid_flat.any():
        raise ValueError("没有任何有效像素（可能天空检测把整张图都吃掉了）")

    pix = flat_lab[valid_flat]
    centers, assign = kmeans_lab(pix, k, l_weight=l_weight)

    # 每个簇的"代表色"用**色度加权**，而不是简单平均。
    # 一面砖墙的像素里，受光的砖饱和度高、背光的砖发灰；简单平均会得到
    # 一个灰褐色，映射到"安山岩"——模型就脏了。按色度加权，饱和的像素
    # 分量更重，代表色就会回到砖红本色。
    rep = centers.copy()
    for j in range(len(centers)):
        m = assign == j
        if not m.any():
            continue
        px = pix[m]
        chroma = np.hypot(px[:, 1], px[:, 2])
        wgt = chroma + 0.15
        rep[j] = (px * wgt[:, None]).sum(0) / wgt.sum()

    # 每个簇映射到最近的方块
    blab = lab_matrix(blocks)
    rf = rough_flags(blocks)
    cost = delta_e_00(rep[:, None, :], blab[None, :, :]) + rough_penalty * rf[None, :]
    pick = cost.argmin(1)

    # 不同簇可能撞到同一个方块 —— 合并
    clusters = sorted(set(pick.tolist()))
    remap = {c: i for i, c in enumerate(clusters)}
    sub_blocks = [blocks[c] for c in clusters]

    grid = np.full(h * w, -1, dtype=np.int32)
    idx = np.array([remap[int(v)] for v in pick], dtype=np.int32)
    grid[valid_flat] = idx[assign]
    grid = grid.reshape(h, w)

    if smooth_passes > 0:
        grid = despeckle(grid, len(sub_blocks), passes=smooth_passes)

    used = np.unique(grid[valid])
    back = np.full(len(sub_blocks), -1, dtype=np.int32)
    for new_i, old_i in enumerate(used):
        back[old_i] = new_i
    safe = np.clip(grid, 0, len(sub_blocks) - 1)
    grid_final = np.where(valid, back[safe], -1).astype(np.int32)

    used_blocks = [sub_blocks[i] for i in used]
    info = {
        "requested_k": k,
        "selected": len(sub_blocks),
        "used": len(used_blocks),
        "tol": tol,
        "dropped_unused": [b.label for i, b in enumerate(sub_blocks) if i not in set(used.tolist())],
        "cluster_lab": [[round(float(v), 1) for v in c] for c in rep],
    }
    return grid_final, used_blocks, info
