"""体块组合：把立面按"屋顶线台阶"切成主楼与侧翼，给出各块的进深偏移。

为什么用屋顶线而不是垂直缝：
    立面内部全是窗框、砖缝、腰线，垂直方向的"颜色突变列"比比皆是，
    按缝切会把一栋楼切成十几块。屋顶线（每列最上面一行建筑像素）
    则很干净：透视会让它整体缓慢倾斜，但真正的侧翼会在某处**突然掉
    下去一大截**，两者的尺度完全不同。

判据：
    1. 逐列求建筑顶行 `tops[x]`（天空以下、非纯黑的第一行）；
    2. 先拟合一条直线扣掉整体倾斜 —— 斜着拍会让屋顶线一路倾斜，
       扣掉之后真正的侧翼才以"相对趋势突然下沉"的形式露出来；
    3. 顶行比趋势低出 `step` 格以上、边界在 1~2 列内陡降、内部屋顶平直的
       连续列 = 一个侧翼；
    4. 侧翼一律按**缩进去**处理。轮廓上出现台阶且台阶上方是天空，
       这个低块只可能在后面：要是它在前面，上面就该看见背后的高楼。
       所以这里没有"探出来"的分支，也就不需要再猜方向。

只改厚度，不改坐标：一块缩进去的侧翼就是"这一段列少挤几格"，
体素化之后侧面自然出现台阶面，平面自然成 L 形或 T 形。
"""

from __future__ import annotations

import numpy as np


def column_tops(valid: np.ndarray, sky: np.ndarray | None = None) -> np.ndarray:
    """每列建筑顶行。整列没有建筑时记为 h（等于"顶在画面之外"）。"""
    h, w = valid.shape
    m = valid.copy()
    if sky is not None:
        m &= ~sky
    tops = np.full(w, h, dtype=np.int32)
    for x in range(w):
        col = np.nonzero(m[:, x])[0]
        if col.size:
            tops[x] = int(col[0])
    return tops


def _detrend(tops: np.ndarray) -> np.ndarray:
    """扣掉整体倾斜，只留下"相对趋势的塌陷"。

    斜着拍会让屋顶线一路倾斜（学校那张从第 11 行斜到第 1 行），
    直接比高低会把整条斜线当成台阶。

    但不能拿全部列去拟合一条直线：台阶本身会把直线拽歪，残差就变成
    斜坡，侧翼的后半段会被判成"正常"。所以迭代几轮，每轮只保留
    **在当前趋势之上**的列重新拟合 —— 收敛到最高那条屋顶线的斜率。
    """
    w = tops.size
    x = np.arange(w, dtype=np.float64)
    ok = tops < tops.max()
    if ok.sum() < 8:
        return tops.astype(np.float64) - float(np.median(tops))

    sel = ok.copy()
    fit = np.full(w, float(np.median(tops[ok])))
    for _ in range(4):
        if sel.sum() < 4:
            break
        A = np.vstack([x[sel], np.ones(int(sel.sum()))]).T
        coef, *_ = np.linalg.lstsq(A, tops[sel].astype(np.float64), rcond=None)
        fit = coef[0] * x + coef[1]
        resid = tops.astype(np.float64) - fit
        # 只留"没塌下去"的列（残差 ≤ 1 格），下一轮拟合主楼那条线
        sel = ok & (resid <= 1.0)
    return tops.astype(np.float64) - fit


def split_panels(
    tops: np.ndarray,
    sky: np.ndarray | None,
    valid: np.ndarray,
    step: float = 2.0,
    min_frac: float = 0.12,
) -> list[dict]:
    """按屋顶线台阶切出侧翼块。返回值只包含"侧翼"，主楼是剩下的部分。

    每块给：x0/x1（右开）、y_top（该块顶行）、protrude（是否探出来）。
    """
    h, w = valid.shape
    resid = _detrend(tops)
    low = (resid >= step) & (tops < h)

    panels: list[dict] = []
    x = 0
    while x < w:
        if not low[x]:
            x += 1
            continue
        x0 = x
        while x < w and low[x]:
            x += 1
        x1 = x
        if x1 - x0 < max(2, int(round(min_frac * w))):
            continue  # 太窄，是烟囱、天线、阴影，不是体块
        # 台阶要"陡"：真正的体块交界在 1~2 列之内就掉下去，
        # 而坡屋顶是每列掉半格，靠这一条把屋顶斜坡挡掉。
        left = tops[x0 - 1] if x0 > 0 else None
        right = tops[x1] if x1 < w else None
        jump = 0
        if left is not None:
            jump = max(jump, abs(int(tops[x0]) - int(left)))
        if right is not None:
            jump = max(jump, abs(int(tops[x1 - 1]) - int(right)))
        if jump < 3:
            continue
        # 侧翼的屋顶是平的；坡屋顶内部一路在斜
        slope = float(np.polyfit(np.arange(x1 - x0), tops[x0:x1].astype(float), 1)[0])
        if abs(slope) > 0.4:
            continue
        panel_tops = tops[x0:x1]
        y_top = int(np.median(panel_tops))
        panels.append({"x0": int(x0), "x1": int(x1), "y_top": y_top})
    return panels


def apply(th: np.ndarray, panels: list[dict], offset: int) -> np.ndarray:
    """按体块调整厚度：侧翼范围内、屋顶线以下的部分减薄。

    屋顶线以上不动 —— 那几行是天空，本来就是 0；真要还有墙，那是主楼
    伸过来的部分，不该跟着侧翼一起减。
    """
    if not panels or offset <= 0:
        return th
    out = th.copy()
    for p in panels:
        x0, x1, y0 = p["x0"], p["x1"], p["y_top"]
        seg = out[y0:, x0:x1]
        out[y0:, x0:x1] = np.where(seg > 0, np.maximum(1, seg - offset), seg)
    return out


def detect(
    valid: np.ndarray,
    sky: np.ndarray | None,
    depth: int,
    wing_frac: float = 0.30,
    step: float = 2.0,
    min_frac: float = 0.12,
) -> dict:
    """返回体块分段与进深偏移，供 `apply` 使用。"""
    h, w = valid.shape
    tops = column_tops(valid, sky)
    panels = split_panels(tops, sky, valid, step=step, min_frac=min_frac)
    offset = max(1, int(round(depth * wing_frac))) if panels else 0
    return {
        "panels": panels,
        "offset": offset,
        "tops": tops,
        "info": {
            "panels": len(panels),
            "offset": offset,
            "spans": [[p["x0"], p["x1"], p["y_top"]] for p in panels],
        },
    }
