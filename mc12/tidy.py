"""规整化：把照片的噪点、阴影、歪斜，整理成 Minecraft 建筑的样子。

Minecraft 里的建筑有一个共同点：**同一种材料就是一个纯色面**，不掺渐变、
不长斑点，窗户是整齐的矩形阵列。照片恰恰相反 —— 一面砖墙上有光照梯度、
有空调外机的阴影、有窗框的杂色。直接按像素配方块，出的模型就像"打了个
马赛克的老照片"，而不是"建筑"。

所以这一步做四件事：

1. `flatten_illumination`：抹掉大尺度明暗差异（阴影、逆光、渐变），
   让同一面墙落回同一个颜色。
2. `unify_window_sizes`：所有窗统一成同一个尺寸（众数），
   并且在同一排里等间距重排 —— 这是"规整"最直观的来源。
3. `merge_regions`：把面积过小的色块并进邻域主导色，
   去掉"一格深、一格浅"的椒盐噪点。
4. `symmetrize`：可选，左右镜像取共识。真实建筑多半左右对称，
   照片上的不对称往往是透视和光照造成的假象。
"""

from __future__ import annotations

import numpy as np


def luma(rgb: np.ndarray) -> np.ndarray:
    return 0.2126 * rgb[..., 0] + 0.7152 * rgb[..., 1] + 0.0722 * rgb[..., 2]


def _box(a: np.ndarray, r: int) -> np.ndarray:
    if r <= 0:
        return a
    p = np.pad(a, r, mode="edge")
    s = np.zeros((p.shape[0] + 1, p.shape[1] + 1))
    s[1:, 1:] = p.cumsum(0).cumsum(1)
    k = 2 * r + 1
    return (s[k:, k:] - s[:-k, k:] - s[k:, :-k] + s[:-k, :-k]) / (k * k)


# ------------------------------------------------------------------ 光照


def flatten_illumination(
    rgb: np.ndarray,
    radius_frac: float = 0.10,
    lo: float = 0.78,
    hi: float = 1.55,
    passes: int = 4,
) -> tuple[np.ndarray, np.ndarray]:
    """用低频亮度做乘性补偿，抹掉阴影和渐变。

    亮度的大尺度起伏 = 光照，小尺度起伏 = 纹理。把大尺度那一层除掉，
    剩下就是"材料本来的颜色"。乘性补偿保持色相不变，砖还是砖，不会变色。

    返回 (归一化图像, gain 图)。gain 的范围被夹住，避免把窗洞、门洞
    这类本来就该暗的地方强行提亮成白墙。
    """
    lum = luma(rgb)
    r = max(2, int(round(min(rgb.shape[:2]) * radius_frac * 0.35)))
    low = lum
    for _ in range(passes):
        low = _box(low, r)
    low = np.maximum(low, 1e-3)
    gain = np.clip(float(np.median(low)) / low, lo, hi)
    return np.clip(rgb * gain[..., None], 0.0, 1.0), gain


# ------------------------------------------------------------------ 区域


def _components(mask: np.ndarray) -> list[np.ndarray]:
    """4 邻接连通域，返回每个域的坐标数组 (N,2)。"""
    h, w = mask.shape
    seen = np.zeros_like(mask, dtype=bool)
    out = []
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
            for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                ny, nx = y + dy, x + dx
                if 0 <= ny < h and 0 <= nx < w and mask[ny, nx] and not seen[ny, nx]:
                    seen[ny, nx] = True
                    stack.append((ny, nx))
        out.append(np.array(pts, dtype=np.int32))
    return out


def merge_regions(grid: np.ndarray, min_area: int = 3, rounds: int = 2) -> np.ndarray:
    """把面积小于 min_area 的色块，并进它周围最多的那种颜色。

    grid 是 (H,W) 的方块下标，-1 表示不放方块。
    只动小碎块，大色块一律不碰 —— 这样窗户、门窗这些细节还在，
    而砖墙上的斑点和阴影碎片会被吃掉。
    """
    out = grid.copy()
    h, w = out.shape
    for _ in range(rounds):
        changed = False
        for val in np.unique(out):
            if val < 0:
                continue
            m = out == val
            for pts in _components(m):
                if len(pts) >= min_area:
                    continue
                cy, cx = int(pts[:, 0].mean()), int(pts[:, 1].mean())
                # 取这个小块外圈的邻域颜色
                y0, y1 = max(0, cy - 2), min(h, cy + 3)
                x0, x1 = max(0, cx - 2), min(w, cx + 3)
                nb = out[y0:y1, x0:x1].ravel()
                nb = nb[(nb >= 0) & (nb != val)]
                if nb.size == 0:
                    continue
                cnt = np.bincount(nb)
                pick = int(np.argmax(cnt))
                out[pts[:, 0], pts[:, 1]] = pick
                changed = True
        if not changed:
            break
    return out


# ------------------------------------------------------------------ 窗户


def _windows(win: np.ndarray) -> list[tuple[int, int, int, int]]:
    out = []
    for pts in _components(win):
        ys, xs = pts[:, 0], pts[:, 1]
        out.append((int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())))
    return out


def gridify_windows(
    win: np.ndarray,
    pier: int = 2,
    max_rows_gap: float = 0.7,
    even_rows: bool = True,
    row_cv: float = 0.65,
    min_per_row: int = 2,
) -> tuple[np.ndarray, dict]:
    """按楼层分行，把窗户重画成规整的阵列。

    为什么不能直接用检测结果：
        在 1 像素 = 1 方块的分辨率上，一扇窗只有 2~3 格宽，检测非常不稳定 ——
        两扇相邻的窗会被连成一块 8 格宽的"大窗"，而背光的那扇又只漏出 1 格。
        直接照抄，模型上的窗户就一大一小、参差不齐。

    做法：
        1. 按 y 把窗户分行（同一层楼）；
        2. 行内按 x 排序，间距过大的地方断开，说明那是另一段立面（比如左翼）；
        3. 每一段用"窗宽 + pier 格墙垛"作为节奏，从段首铺到段尾。

    这样出来的窗户必然同宽、等距、成行 —— 也就是 Minecraft 建筑的样子。
    pier 是两扇窗之间的墙垛宽度，默认 2 格。
    """
    boxes = _windows(win)
    info = {"before": len(boxes), "rows": 0, "segs": 0, "windows": 0, "bw": 0}
    # 只有几扇窗时，"推断网格规律"纯属瞎猜 —— 照原样保留反而更准
    if len(boxes) < 5:
        info["skipped"] = True
        return win, info

    h, w = win.shape
    out = np.zeros_like(win)

    # 窗宽别太小：Minecraft 建筑里 40 格宽的楼，窗一般 3 格宽。
    # 检测出来的 2 格是因为透视把靠边的窗压窄了，照抄会变成一排小点。
    w_img = win.shape[1]
    bw_med = float(np.median([b[2] - b[0] + 1 for b in boxes]))
    bw = int(np.clip(max(3.0, bw_med), 2, max(3, round(w_img * 0.10))))
    bh = int(np.clip(np.median([b[3] - b[1] + 1 for b in boxes]), 2, 4))
    pitch = bw + max(1, pier)
    info["bw"] = bw

    cy = np.array([(b[1] + b[3]) / 2.0 for b in boxes])
    row_tol = max(1.0, float(np.median([b[3] - b[1] + 1 for b in boxes])) * max_rows_gap)

    order = list(np.argsort(cy))
    rows: list[list[int]] = []
    for i in order:
        if rows and cy[i] - cy[rows[-1][-1]] <= row_tol:
            rows[-1].append(int(i))
        else:
            rows.append([int(i)])

    # 只保留"成排"的楼层：一排里只有一扇孤窗，多半是屋顶装饰或误检
    rows = [r for r in rows if len(r) >= min_per_row] or rows

    row_y = [
        float(np.median([(boxes[i][1] + boxes[i][3]) / 2.0 for i in r])) for r in rows
    ]
    if even_rows and len(rows) >= 3:
        gaps = np.diff(np.sort(row_y))
        cv = float(np.std(gaps) / max(np.mean(gaps), 1e-6))
        if cv <= row_cv:
            # 楼层高度本来就该一致；检测出来的 2/4/2/2/4/6/4 是投影误差
            row_y = list(np.linspace(min(row_y), max(row_y), len(rows)))
            info["even_rows"] = True
        else:
            info["even_rows"] = False

    # 全局列网格：所有楼层共用同一条"窗柱"网格，上下的窗才会垂直对齐。
    # 各层各自等距，虽然每层自己整齐，但层与层错开半格，看着还是乱。
    mods = [int(b[0]) % pitch for b in boxes]
    offset = int(np.bincount(mods, minlength=pitch).argmax())
    info["offset"] = offset

    for row, yf in zip(rows, row_y):
        info["rows"] += 1
        idx = sorted(row, key=lambda i: boxes[i][0])
        y = int(round(yf))

        # 间距过大就断开：左翼和主体属于不同立面，不能连成一条
        segs: list[list[int]] = [[idx[0]]]
        for a, b in zip(idx, idx[1:]):
            if boxes[b][0] - boxes[a][2] > 2 * pitch:
                segs.append([b])
            else:
                segs[-1].append(b)

        # 但整行的窗跨度已经过半，说明这就是一整面连续立面（检测漏掉的窗
        # 不该在模型上留一个空档），直接合并成一条铺满。
        row_span = boxes[idx[-1]][2] - boxes[idx[0]][0]
        if row_span > 0.5 * w:
            segs = [idx]

        for seg in segs:
            info["segs"] += 1
            x0 = int(boxes[seg[0]][0])
            x1 = int(boxes[seg[-1]][2])
            y0 = int(np.clip(y - bh // 2, 0, max(0, h - bh)))
            k0 = int(np.ceil((x0 - offset) / pitch))
            k1 = int(np.floor((x1 - bw + 1 - offset) / pitch))
            for k in range(k0, k1 + 1):
                xa = offset + k * pitch
                if xa < 0 or xa + bw > w:
                    continue
                out[y0 : y0 + bh, xa : xa + bw] = True
                info["windows"] += 1

    return out, info


# ------------------------------------------------------------------ 对称


def measure_symmetry(grid: np.ndarray) -> float:
    """左右镜像一致的比例，用来判断"值不值得对齐对称轴"。"""
    if grid.size == 0:
        return 0.0
    mirror = grid[:, ::-1]
    both = (grid >= 0) & (mirror >= 0)
    if both.sum() == 0:
        return 0.0
    return float(((grid == mirror) & both).sum()) / float(both.sum())


def mirror_half(grid: np.ndarray, axis: int | None = None) -> np.ndarray:
    """把左半边（含对称轴）镜像到右半边，强行做出左右对称的模型。

    真实建筑大多左右对称，照片里之所以看着歪，多半是透视和光照造成的
    假象。但这不是默认行为 —— 校舍、民居这类本身不对称的建筑会被改坏，
    所以要显式开关。
    """
    h, w = grid.shape
    ax = w // 2 if axis is None else int(np.clip(axis, 0, w - 1))
    out = grid.copy()
    for x in range(ax, w):
        sx = 2 * ax - x
        if 0 <= sx < w:
            out[:, x] = grid[:, sx]
    _ = h
    return out

def fill_holes(rgb: np.ndarray, holes: np.ndarray) -> np.ndarray:
    """把洞（窗户、门）按同一行左右最近的墙色线性填平。

    为什么必须做：降到 1 像素 = 1 方块后，一格里往往同时有砖墙和窗框，
    平均出来是一种"灰褐色"。这颜色在任何一个 Minecraft 方块里都找不到
    对应，于是整面砖墙被配成了安山岩 —— 模型就脏了。
    先把窗填成墙色，砖墙才能保持纯砖色；窗最后由语义层整块盖玻璃。
    """
    out = rgb.copy()
    h, w = holes.shape
    for y in range(h):
        row = holes[y]
        if not row.any():
            continue
        xs = np.nonzero(row)[0]
        known = np.nonzero(~row)[0]
        if known.size < 2:
            continue
        for c in range(3):
            out[y, xs, c] = np.interp(xs, known, rgb[y, known, c])
    return out

def line_smooth(
    grid: np.ndarray,
    win: np.ndarray | None = None,
    kh: int = 3,
    kv: int = 1,
) -> np.ndarray:
    """沿行/列做众数滤波，把"条纹状建筑"上的随机跳变压平。

    为什么不用普通 3x3 滤波：建筑立面是**各向异性**的 —— 腰线、窗台、
    楼层线都是横着走的，竖方向的变化才是真边界。所以水平方向可以大胆
    抹（窗口 3~5 格），竖直方向必须轻手（窗口 1~2 格）。
    普通方形核会把楼层线一起抹掉，模型就糊成一团。

    win 为 True 的格子（窗户）不参与也不被修改。
    """
    k = int(grid.max()) + 1
    if k <= 1:
        return grid
    oh = np.stack([(grid == i) for i in range(k)]).astype(np.float64)
    if win is not None:
        oh[:, win] = 0.0

    def box1(a: np.ndarray, n: int, axis: int) -> np.ndarray:
        if n <= 1:
            return a
        r = n // 2
        pad = [(0, 0)] * a.ndim
        pad[axis] = (r, n - 1 - r)
        p = np.pad(a, pad, mode="constant")
        acc = np.zeros_like(a)
        for i in range(n):
            sl = [slice(None)] * a.ndim
            sl[axis] = slice(i, i + a.shape[axis])
            acc += p[tuple(sl)]
        return acc

    acc = box1(oh, kh, axis=2)
    acc = box1(acc, kv, axis=1)
    out = np.argmax(acc, axis=0).astype(np.int32)
    out = np.where(grid < 0, -1, out)
    if win is not None:
        out[win] = grid[win]
    return out
