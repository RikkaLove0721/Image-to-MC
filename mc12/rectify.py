"""立面透视矫正：把斜着拍的照片拉成正视立面。

为什么必须先做这一步：
    手机/相机拍建筑，几乎不可能是"正对、水平、无俯仰"。照片里的建筑是
    一个梯形：竖边向上收束、横边向一侧收束。直接按图做方块化，模型就会
    一头大一头小 —— 窗户右密左疏、屋顶歪斜，看起来"不对称"。这不是
    方块匹配的问题，是几何根本没摆正。

做法（不用神经网络，也不用 OpenCV）：
    1. 边缘检测 + 方向分解，找出一批"竖边"和"横边"线段；
    2. 每组线段做齐次最小二乘，求出各自的消失点（平行线在照片里的交点）；
    3. 把两个消失点同时送到无穷远 —— 得到射影矫正矩阵 H；
    4. 反向单应变换重采样，建筑横线就变水平、竖线就变竖直。

为什么用消失点而不是"框四个角"：
    建筑本身有高低起伏（左翼矮、主体高），上边界根本不是一条直线，
    拟合四角必然错。消失点只看**方向**，不假设建筑轮廓，所以对不规则
    建筑同样成立。
"""

from __future__ import annotations

import numpy as np


# ---------------------------------------------------------------- 基础工具


def luma(rgb: np.ndarray) -> np.ndarray:
    return 0.2126 * rgb[..., 0] + 0.7152 * rgb[..., 1] + 0.0722 * rgb[..., 2]


def _sobel(l: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """中心差分 + 3x3 平滑，比标准 Sobel 更抗噪。"""
    p = np.pad(l, 1, mode="edge")
    gx = 0.5 * (p[1:-1, 2:] - p[1:-1, :-2])
    gy = 0.5 * (p[2:, 1:-1] - p[:-2, 1:-1])
    return _box3(gx), _box3(gy)


def _box3(a: np.ndarray) -> np.ndarray:
    p = np.pad(a, 1, mode="edge")
    s = np.zeros((p.shape[0] + 1, p.shape[1] + 1))
    s[1:, 1:] = p.cumsum(0).cumsum(1)
    return (s[3:, 3:] - s[:-3, 3:] - s[3:, :-3] + s[:-3, :-3]) / 9.0


def _components(mask: np.ndarray, limit: int = 6000) -> list[np.ndarray]:
    """8 邻接连通域，返回每个域的 (N,2) 坐标数组。超过 limit 个像素的
    域直接丢弃（那是天空/地面这种大片区域，不可能是线段）。"""
    h, w = mask.shape
    seen = np.zeros_like(mask, dtype=bool)
    out: list[np.ndarray] = []
    ys, xs = np.nonzero(mask)
    for y0, x0 in zip(ys, xs):
        if seen[y0, x0]:
            continue
        stack = [(int(y0), int(x0))]
        seen[y0, x0] = True
        pts = []
        while stack:
            y, x = stack.pop()
            pts.append((y, x))
            if len(pts) > limit:
                break
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    if dy == 0 and dx == 0:
                        continue
                    ny, nx = y + dy, x + dx
                    if 0 <= ny < h and 0 <= nx < w and mask[ny, nx] and not seen[ny, nx]:
                        seen[ny, nx] = True
                        stack.append((ny, nx))
        if len(pts) <= limit:
            out.append(np.array(pts, dtype=np.float64))
    return out


# ---------------------------------------------------------------- 线段与消失点


def _fit_line(pts: np.ndarray) -> tuple[float, float, float] | None:
    """对点集拟合一般式直线 A*x + B*y + C = 0，两步剔除离群点。"""
    if len(pts) < 8:
        return None
    x, y = pts[:, 1], pts[:, 0]
    keep = np.ones(len(x), dtype=bool)
    line = None
    for _ in range(3):
        if keep.sum() < 8:
            return None
        c = np.stack([x[keep], y[keep], np.ones(keep.sum())], axis=1)
        # 最小二乘解 (A,B,C)，约束 |(A,B)|=1
        _, _, vt = np.linalg.svd(c, full_matrices=False)
        a, b, cc = vt[-1]
        n = np.hypot(a, b)
        if n < 1e-9:
            return None
        a, b, cc = a / n, b / n, cc / n
        d = np.abs(a * x + b * y + cc)
        med = np.median(d)
        scale = max(med * 2.5, 0.6)
        keep = d < scale
        line = (a, b, cc)
    return line


def _segments(
    edge: np.ndarray,
    vertical: bool,
    min_len: float,
    max_thick: float,
) -> list[tuple[float, float, float, float]]:
    """从方向边缘图里抽出直线段，返回 [(a,b,c,weight)]。"""
    h, w = edge.shape
    out = []
    for pts in _components(edge):
        ys, xs = pts[:, 0], pts[:, 1]
        bh = ys.max() - ys.min() + 1
        bw = xs.max() - xs.min() + 1
        length = bh if vertical else bw
        thick = bw if vertical else bh
        if length < min_len or thick > max_thick:
            continue
        # 填充率：真正的直线段在自身包围盒里应该很"满"
        if len(pts) / max(bh * bw, 1) < 0.35:
            continue
        ln = _fit_line(pts)
        if ln is None:
            continue
        # 方向校验：一般式 A*x+B*y+C=0 的 (A,B) 是法向量。
        # 竖线的法向量朝 x（|A| 大），横线的法向量朝 y（|B| 大）。
        a, b, c = ln
        if vertical and abs(a) < 2 * abs(b):
            continue
        if (not vertical) and abs(b) < 2 * abs(a):
            continue
        out.append((a, b, c, length))
    return out


def _vanish(lines: list[tuple[float, float, float, float]]) -> np.ndarray | None:
    """齐次最小二乘求消失点：所有直线都过同一点。"""
    if len(lines) < 3:
        return None
    m = np.array([[l[0], l[1], l[2]] for l in lines], dtype=np.float64)
    weight = np.array([min(l[3], 400.0) for l in lines])[:, None]
    m = m * weight
    _, s, vt = np.linalg.svd(m, full_matrices=False)
    # 条件数太差说明这些线根本不共点，别硬算
    if s[1] < 1e-9:
        return None
    v = vt[-1]
    if abs(v[2]) < 1e-9:
        return None
    return v[:2] / v[2]


def _homography(vp_v: np.ndarray, vp_h: np.ndarray) -> np.ndarray:
    """把两个消失点送到无穷远的射影变换。

    单应矩阵第三行 h3 与消失点同在一条线上：h3 · (vx, vy, 1) = 0。
    两个消失点给出两条约束，h3 就是它们的叉积。
    """
    v1 = np.array([vp_v[0], vp_v[1], 1.0])
    v2 = np.array([vp_h[0], vp_h[1], 1.0])
    h3 = np.cross(v1, v2)
    n = np.linalg.norm(h3)
    if n < 1e-12:
        return np.eye(3)
    h3 = h3 / n
    return np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], h3])


def _apply(h: np.ndarray, x: np.ndarray, y: np.ndarray):
    den = h[2, 0] * x + h[2, 1] * y + h[2, 2]
    den = np.where(np.abs(den) < 1e-12, 1e-12, den)
    u = (h[0, 0] * x + h[0, 1] * y + h[0, 2]) / den
    v = (h[1, 0] * x + h[1, 1] * y + h[1, 2]) / den
    return u, v


# ---------------------------------------------------------------- 建筑主体


def building_mask(rgb: np.ndarray) -> np.ndarray:
    """砖红/暖色建筑主体。天空、铺地、树都不暖，天然被排除。"""
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    lm = luma(rgb)
    warm = (r - b) / (r + b + 1e-6)
    m = (warm > 0.14) & (lm > 0.10) & (lm < 0.82)
    # 3x3 闭运算，把窗户、腰线这些"洞"补上，得到一整块
    m = _box3(m.astype(np.float64)) > 0.45
    m = _box3(m.astype(np.float64)) > 0.55
    # 去掉零星小域
    out = np.zeros_like(m)
    for pts in _components(m, limit=10**9):
        if len(pts) > 0.004 * m.size:
            out[pts[:, 0].astype(int), pts[:, 1].astype(int)] = True
    return out


# ---------------------------------------------------------------- 主流程


def rectify(
    rgb: np.ndarray,
    min_line_frac: float = 0.06,
) -> tuple[np.ndarray, dict]:
    """矫正一张 (H,W,3) float 图像，返回 (矫正后图像, 诊断信息)。

    检测不可靠时**原样返回**，并置 info["ok"]=False —— 宁可不动，也不能
    把一张好照片旋成一团。
    """
    h, w = rgb.shape[:2]
    gx, gy = _sobel(luma(rgb))
    mag = np.hypot(gx, gy)
    thr = max(float(np.percentile(mag, 88)), 0.012)

    vmask = (mag > thr) & (np.abs(gx) > 1.6 * np.abs(gy))
    hmask = (mag > thr) & (np.abs(gy) > 1.6 * np.abs(gx))

    min_len = min_line_frac * h
    vlines = _segments(vmask, True, min_len, max_thick=max(3, 0.012 * w))
    hlines = _segments(hmask, False, min_len * 1.2, max_thick=max(3, 0.012 * h))

    vp_v = _vanish(vlines)
    vp_h = _vanish(hlines)
    info = {
        "ok": False,
        "v_segs": len(vlines),
        "h_segs": len(hlines),
        "vp_v": None if vp_v is None else [round(float(x), 1) for x in vp_v],
        "vp_h": None if vp_h is None else [round(float(x), 1) for x in vp_h],
    }
    if vp_v is None or vp_h is None:
        return rgb, info

    # 消失点不该离图像太近（否则说明这些线基本是平行的，没有透视）
    # 也不该离谱地远（那是拟合噪声）
    for vp in (vp_v, vp_h):
        d = np.hypot(vp[0] - w / 2, vp[1] - h / 2)
        if d < 0.6 * w:
            info["note"] = "消失点太近，透视不明显，跳过"
            return rgb, info
        if d > 60 * max(w, h):
            info["note"] = "消失点离谱，判定拟合失败，跳过"
            return rgb, info

    hm = _homography(vp_v, vp_h)

    # 用建筑主体的四角变换后定输出范围，避免整幅图被拉爆
    bm = building_mask(rgb)
    if bm.sum() < 0.02 * bm.size:
        return rgb, info
    ys, xs = np.nonzero(bm)
    x0, x1, y0, y1 = xs.min(), xs.max(), ys.min(), ys.max()
    corners = np.array(
        [[x0, y0], [x1, y0], [x0, y1], [x1, y1]], dtype=np.float64
    )
    u, v = _apply(hm, corners[:, 0], corners[:, 1])
    if not np.all(np.isfinite(u)) or not np.all(np.isfinite(v)):
        return rgb, info
    ou0, ou1 = float(u.min()), float(u.max())
    ov0, ov1 = float(v.min()), float(v.max())
    ow, oh = ou1 - ou0, ov1 - ov0
    if ow < 0.2 * w or oh < 0.2 * h or ow > 12 * w or oh > 12 * h:
        return rgb, info
    # 单应输出到"原图尺度"，避免分辨率先被放大再缩回来的二次模糊
    scale = min(1.0, max(0.25, w / ow))
    out_w = max(16, int(round(ow * scale)))
    out_h = max(16, int(round(oh * scale)))

    # 反向映射：输出像素 -> 输入像素
    inv = np.linalg.inv(hm)
    uu, vv = np.meshgrid(np.arange(out_w), np.arange(out_h))
    uu = ou0 + (uu + 0.5) / scale
    vv = ov0 + (vv + 0.5) / scale
    sx, sy = _apply(inv, uu.ravel(), vv.ravel())
    sx = sx.reshape(out_h, out_w)
    sy = sy.reshape(out_h, out_w)

    valid = (sx >= 0) & (sx <= w - 1) & (sy >= 0) & (sy <= h - 1)
    out = np.zeros((out_h, out_w, 3), dtype=np.float64)
    for c in range(3):
        out[..., c] = _bilinear(rgb[..., c], sx, sy, valid)

    info.update(
        {
            "ok": True,
            "size": [int(out_w), int(out_h)],
            "scale": round(float(scale), 3),
            "homography": [[round(float(v), 6) for v in row] for row in hm],
        }
    )
    return out, info


def _bilinear(ch: np.ndarray, x: np.ndarray, y: np.ndarray, valid: np.ndarray) -> np.ndarray:
    h, w = ch.shape
    x = np.clip(x, 0, w - 1.001)
    y = np.clip(y, 0, h - 1.001)
    x0 = np.floor(x).astype(int)
    y0 = np.floor(y).astype(int)
    fx = x - x0
    fy = y - y0
    x1 = np.minimum(x0 + 1, w - 1)
    y1 = np.minimum(y0 + 1, h - 1)
    v = (
        ch[y0, x0] * (1 - fx) * (1 - fy)
        + ch[y0, x1] * fx * (1 - fy)
        + ch[y1, x0] * (1 - fx) * fy
        + ch[y1, x1] * fx * fy
    )
    return np.where(valid, v, 0.0)

def crop_to_building(
    rgb: np.ndarray,
    margin: float = 0.03,
) -> tuple[np.ndarray, tuple[int, int, int, int] | None]:
    """把画面裁到建筑主体，去掉天空、马路、车辆、树。

    为什么要裁：这些"配景"最后都会变成一整块灰色底座，把建筑压在下面，
    模型看着又脏又矮。用户要的是建筑，不是街景。
    """
    m = building_mask(rgb)
    if m.sum() < 0.01 * m.size:
        return rgb, None
    ys, xs = np.nonzero(m)
    h, w = rgb.shape[:2]
    mx = int(round(w * margin))
    my = int(round(h * margin))
    x0 = max(0, int(xs.min()) - mx)
    x1 = min(w, int(xs.max()) + mx + 1)
    y0 = max(0, int(ys.min()) - my)
    y1 = min(h, int(ys.max()) + my + 1)
    if (x1 - x0) < 0.25 * w or (y1 - y0) < 0.25 * h:
        return rgb, None
    return rgb[y0:y1, x0:x1], (x0, y0, x1, y1)
