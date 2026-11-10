"""场景分割：先把画面切成"建筑 / 天空 / 植被 / 地面"，再谈用什么方块。

为什么必须要有这一层：

    逐格看纹理能回答"这块像不像树叶"，回答不了"这块是不是建筑的一部分"。
    而建筑立面上恰好全是"一格多色 + 细竖条"的结构 —— 砖墙加规则窗阵在
    纹理上和栅栏、书架几乎同构。实测一张学校照片，逐格分类会把整面墙
    认成 411 格栅栏加 126 格书架，模型直接毁掉。

    正确的顺序是反过来：**先确认某块区域不属于建筑**（在建筑轮廓之外、
    在墙脚以下、或者整片是绿色植物），确认了非建筑，才用装饰方块去替代。
    这样窗框永远不会变成栅栏，因为它压根就在建筑里面。

分割依据（都是区域级的，不看单格纹理）：

    天空   `geometry.sky_mask` 给出，原图分辨率判完再降采样
    植被   绿色区域 → 连通域。再按**形状**分两类：
             树冠/绿篱：团块，不横贯整幅画面   -> 用树叶堆叠
             草坪/地面：宽横条贴底，横贯画面   -> 当地面，不装饰
    建筑   去掉天空和植被之后剩下的最大连通块
    栅栏区 建筑墙脚以下的一条带，以及建筑左右两侧的空白 —— 栏杆、围栏、
           花坛边这类东西只可能出现在这里，不可能出现在立面上
"""

from __future__ import annotations

import numpy as np

from .semantic import luma, saturation, warmth


# --------------------------------------------------------------------------
# 基础算子（与 decor.py 同款，避免循环依赖所以各自持有一份）
# --------------------------------------------------------------------------

def _box_mean(a: np.ndarray, r: int) -> np.ndarray:
    if r <= 0:
        return a
    h, w = a.shape
    p = np.pad(a, r, mode="edge")
    s = np.zeros((p.shape[0] + 1, p.shape[1] + 1), dtype=np.float64)
    s[1:, 1:] = p.cumsum(0).cumsum(1)
    k = 2 * r + 1
    return (s[k:, k:] - s[:-k, k:] - s[k:, :-k] + s[:-k, :-k]) / (k * k)


def _down_frac(mask: np.ndarray, hw: tuple[int, int]) -> np.ndarray:
    from PIL import Image

    h, w = hw
    img = Image.fromarray((mask.astype(np.float64) * 255).astype(np.uint8))
    return np.asarray(img.resize((w, h), Image.BOX), dtype=np.float64) / 255.0


def _down_mean(a: np.ndarray, hw: tuple[int, int]) -> np.ndarray:
    from PIL import Image

    h, w = hw
    img = Image.fromarray((np.clip(a, 0, 1) * 255).astype(np.uint8))
    return np.asarray(img.resize((w, h), Image.BOX), dtype=np.float64) / 255.0


def _local_std(a: np.ndarray, r: int) -> np.ndarray:
    m = _box_mean(a, r)
    m2 = _box_mean(a * a, r)
    return np.sqrt(np.maximum(m2 - m * m, 0.0))


def _cell_rgb(hi: np.ndarray, hw: tuple[int, int]) -> np.ndarray:
    from PIL import Image

    h, w = hw
    img = Image.fromarray((np.clip(hi, 0, 1) * 255).astype(np.uint8))
    return np.asarray(img.resize((w, h), Image.BOX), dtype=np.float64) / 255.0


def components(mask: np.ndarray) -> list[list[tuple[int, int]]]:
    """4 邻接连通域。方块网格规模（几十×几十），直接 DFS 足够。"""
    h, w = mask.shape
    seen = np.zeros_like(mask, dtype=bool)
    out: list[list[tuple[int, int]]] = []
    ys, xs = np.nonzero(mask)
    for y0, x0 in zip(ys, xs):
        if seen[y0, x0]:
            continue
        stack = [(int(y0), int(x0))]
        seen[y0, x0] = True
        cells: list[tuple[int, int]] = []
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


def fill_holes(mask: np.ndarray) -> np.ndarray:
    """填掉被围住的空洞，不扩张外轮廓。"""
    from collections import deque

    h, w = mask.shape
    bg = ~mask
    seen = np.zeros_like(bg)
    dq: deque[tuple[int, int]] = deque()
    for x in range(w):
        for y in (0, h - 1):
            if bg[y, x] and not seen[y, x]:
                seen[y, x] = True
                dq.append((y, x))
    for y in range(h):
        for x in (0, w - 1):
            if bg[y, x] and not seen[y, x]:
                seen[y, x] = True
                dq.append((y, x))
    while dq:
        y, x = dq.popleft()
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            ny, nx = y + dy, x + dx
            if 0 <= ny < h and 0 <= nx < w and bg[ny, nx] and not seen[ny, nx]:
                seen[ny, nx] = True
                dq.append((ny, nx))
    return mask | (bg & ~seen)


# --------------------------------------------------------------------------
# 植被：团块 vs 贴地长条
# --------------------------------------------------------------------------

def vegetation_regions(
    hi: np.ndarray,
    hw: tuple[int, int],
    green_thr: float = 0.045,
    cell_thr: float = 0.40,
    min_cells: int = 3,
    lawn_span: float = 0.80,
    lawn_aspect: float = 2.6,
    lawn_area: float = 0.05,
    flat_thr: float = 0.020,
    sat_max: float = 0.45,
) -> dict:
    """绿色区域按形状分成"树冠/绿篱"与"草坪/地面"，并排除绿漆墙。

    这里刻意**不用纹理做主判据**：草坪和树冠的纹理都可能是平滑的，
    区别在形状 —— 草坪是一条贴着画面底边、顶边平直、面积够大的块；
    树冠是一团，通常不碰到画面底边，顶边也参差。

    纹理只在最后做一次**否定性检查**：纯平涂的绿墙（几乎没有亮度起伏）
    是建筑材料，不是植物，退回建筑去。真实叶冠总有明暗，压不到 0.02 以下。
    """
    h, w = hw
    g = hi[..., 1]
    green_hi = (g - np.maximum(hi[..., 0], hi[..., 2])) > green_thr
    green = _down_frac(green_hi, hw) > cell_thr

    # 细节图上的局部亮度起伏，只用来判"是不是纯平涂"
    flat_map = _down_mean(_local_std(luma(hi), 1), hw)
    cell = _cell_rgb(hi, hw)

    canopy = np.zeros((h, w), bool)
    lawn = np.zeros((h, w), bool)
    paint = np.zeros((h, w), bool)
    groups: list[list[tuple[int, int]]] = []
    dropped = 0

    for cells in components(green):
        if len(cells) < min_cells:
            dropped += 1
            continue
        ys = [c[0] for c in cells]
        xs = [c[1] for c in cells]
        y0, y1 = min(ys), max(ys) + 1
        x0, x1 = min(xs), max(xs) + 1
        bw, bh = x1 - x0, y1 - y0
        sub = np.zeros((bh, bw), bool)
        for y, x in cells:
            sub[y - y0, x - x0] = True
        sub = fill_holes(sub)

        if float(flat_map[y0:y1, x0:x1][sub].mean()) < flat_thr:
            paint[y0:y1, x0:x1] |= sub  # 平涂绿墙面，属于建筑
            continue
        # 荧光绿、纯色块：篮球架、绿幕布、喷漆标识，不是植物
        if float(saturation(cell[y0:y1, x0:x1][sub]).mean()) > sat_max:
            paint[y0:y1, x0:x1] |= sub
            continue

        spans = bw >= lawn_span * w
        strip = bh <= max(2, 0.35 * h) and bw >= lawn_aspect * max(bh, 1)
        # 草坪：贴着画面底边、面积够大、顶边平直
        bottom = y1 >= h - 1
        top = np.array([sub[:, i].argmax() if sub[:, i].any() else bh for i in range(bw)])
        flat_top = float(np.std(top)) < 0.12 * max(bh, 1)
        big = len(cells) >= lawn_area * h * w
        if (spans and strip) or (bottom and big and flat_top):
            lawn[y0:y1, x0:x1] |= sub  # 草坪 / 地面：不装饰
        else:
            canopy[y0:y1, x0:x1] |= sub
            groups.append(cells)

    return {
        "canopy": canopy,
        "lawn": lawn,
        "paint": paint,
        "regions": groups,
        "info": {
            "canopy_cells": int(canopy.sum()),
            "lawn_cells": int(lawn.sum()),
            "paint_cells": int(paint.sum()),
            "canopy_regions": len(groups),
            "dropped": dropped,
        },
    }


# --------------------------------------------------------------------------
# 建筑与"栅栏区"
# --------------------------------------------------------------------------

def building_and_zone(
    hi: np.ndarray,
    hw: tuple[int, int],
    sky: np.ndarray | None,
    canopy: np.ndarray,
    floor_y: int | None = None,
    wide_row: float = 0.50,
) -> dict:
    """建筑 = 去掉天空和植被之后的最大连通块；栅栏区 = 墙脚以下 + 两侧空白。

    关键点是 zone 把建筑立面排除在外：窗框、窗棂、空调支架都在墙里面，
    不在 zone 里，所以永远没机会被认成栅栏。
    """
    h, w = hw
    cell = _cell_rgb(hi, hw)
    lum = luma(cell)

    solid = lum > 0.10
    if sky is not None:
        # sky 通常是细节图分辨率的，先投到方块网格
        sky_cell = sky if sky.shape == (h, w) else _down_frac(sky, hw) > 0.35
        solid &= ~sky_cell
    solid &= ~canopy

    # 闭运算把窗户、腰线这些洞补上，建筑才是一整块
    solid = _box_mean(solid.astype(np.float64), 1) > 0.45
    solid = fill_holes(solid)

    building = np.zeros((h, w), bool)
    best = 0
    for cells in components(solid):
        if len(cells) > best:
            best = len(cells)
            building[:] = False
            for y, x in cells:
                building[y, x] = True
    if best == 0:
        return {
            "building": np.zeros((h, w), bool),
            "zone": np.zeros((h, w), bool),
            "floor_y": h - 1,
            "bbox": None,
            "info": {"building_cells": 0, "floor_y": h - 1, "bbox": None},
        }

    ys = np.nonzero(building.any(axis=1))[0]
    xs = np.nonzero(building.any(axis=0))[0]
    y_top, y_bot = int(ys[0]), int(ys[-1])
    x0, x1 = int(xs[0]), int(xs[-1])

    # 墙脚：建筑里最后一次"够宽"的行。再往下就是台阶、铺地、马路。
    # 调用方给了 floor_y（比如按"暖色墙行"算出来的）就以调用方为准，
    # 建筑掩码在台阶、停着的车上会一路吃到画面底部。
    if floor_y is None:
        width = x1 - x0 + 1
        floor_y = y_top
        for y in range(y_bot, y_top - 1, -1):
            if building[y, x0 : x1 + 1].sum() >= wide_row * width:
                floor_y = y
                break
    floor_y = int(max(y_top, min(floor_y, h - 1)))

    zone = np.zeros((h, w), bool)
    zone[floor_y:, :] = True
    zone[:, :x0] = True
    zone[:, x1 + 1 :] = True
    # 只把**立面上半部分**排除在外。建筑掩码一路吃到台阶、广场、停着的车，
    # 若按整块排除，墙脚那条带也会被吃光，栅栏就永远检测不到了。
    facade = building.copy()
    facade[floor_y:, :] = False
    zone &= ~facade  # 窗框、窗棂、空调支架都在立面里，进不了栅栏区

    return {
        "building": building,
        "zone": zone,
        "floor_y": floor_y,
        "bbox": (x0, y_top, x1, y_bot),
        "info": {
            "building_cells": int(building.sum()),
            "floor_y": floor_y,
            "bbox": [x0, y_top, x1, y_bot],
        },
    }


def segment(
    hi: np.ndarray,
    hw: tuple[int, int],
    sky: np.ndarray | None = None,
    floor_y: int | None = None,
) -> dict:
    """一次分割出植被、草坪、建筑与栅栏区。"""
    veg = vegetation_regions(hi, hw)
    bz = building_and_zone(hi, hw, sky, veg["canopy"], floor_y=floor_y)
    return {
        "canopy": veg["canopy"],
        "lawn": veg["lawn"],
        "paint": veg["paint"],
        "building": bz["building"],
        "zone": bz["zone"],
        "floor_y": bz["floor_y"],
        "regions": veg["regions"],
        "info": {**veg["info"], **bz["info"]},
    }
