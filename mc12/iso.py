"""等轴测离线渲染：把体素模型画成 PNG，便于不开浏览器也能检查三维形态。

画家算法：相机在 (+x,+y,+z) 方向无穷远，按 x+y+z 从小到大绘制，
正好是"由远及近"。每个方块画顶面 / 东面 / 南面三块，各自不同亮度。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from .palette import Block


def render(
    path: str | Path,
    vox: np.ndarray,
    blocks: list[Block],
    tile: float = 5.0,
    bg: tuple[int, int, int] = (247, 246, 242),
    y_range: tuple[int, int] | None = None,
    margin: int = 16,
) -> dict:
    xs, ys, zs = np.nonzero(vox >= 0)
    keep = np.ones(xs.shape, dtype=bool)
    if y_range is not None:
        keep = (ys >= y_range[0]) & (ys <= y_range[1])
    xs, ys, zs = xs[keep], ys[keep], zs[keep]

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if xs.size == 0:
        Image.new("RGB", (64, 64), bg).save(path)
        return {"path": str(path), "blocks": 0, "pixels": [64, 64]}

    vals = vox[xs, ys, zs]
    lut = np.array([(235, 235, 235)] + [b.rgb for b in blocks], dtype=np.int16)
    cols = lut[vals + 1]

    def proj(x, y, z):
        return (x - z) * tile, (x + z) * tile * 0.5 - y * tile

    # 包围盒：把 8 个角点都投影一遍
    cx = np.concatenate([xs, xs + 1, xs, xs + 1, xs, xs + 1, xs, xs + 1])
    cy = np.concatenate([ys, ys, ys + 1, ys + 1, ys, ys, ys + 1, ys + 1])
    cz = np.concatenate([zs, zs, zs, zs, zs + 1, zs + 1, zs + 1, zs + 1])
    sx, sy = proj(cx, cy, cz)
    x0, x1 = float(sx.min()), float(sx.max())
    y0, y1 = float(sy.min()), float(sy.max())
    # 留白按画面尺寸的 5% 算：固定 16px 在 1000px 的大图上等于没有，
    # 建筑会顶到画布边缘，看着像被裁掉了一角。
    pad = max(margin, int(round(0.05 * max(x1 - x0, y1 - y0))))
    min_sx, max_sx = x0 - pad, x1 + pad
    min_sy, max_sy = y0 - pad, y1 + pad

    w = int(max(max_sx - min_sx, 8)) + 1
    h = int(max(max_sy - min_sy, 8)) + 1
    img = Image.new("RGB", (w, h), bg)
    d = ImageDraw.Draw(img)
    ox, oy = -min_sx, -min_sy

    def poly(points, color):
        d.polygon([(p[0] + ox, p[1] + oy) for p in points], fill=color)

    def shade(c, k):
        return tuple(int(min(255, max(0, v * k))) for v in c)

    order = np.lexsort((ys, zs, xs, xs + ys + zs))
    for idx in order:
        x, y, z = int(xs[idx]), int(ys[idx]), int(zs[idx])
        c = tuple(int(v) for v in cols[idx])

        top = [proj(x, y + 1, z), proj(x + 1, y + 1, z), proj(x + 1, y + 1, z + 1), proj(x, y + 1, z + 1)]
        east = [proj(x + 1, y, z), proj(x + 1, y, z + 1), proj(x + 1, y + 1, z + 1), proj(x + 1, y + 1, z)]
        south = [proj(x, y, z + 1), proj(x + 1, y, z + 1), proj(x + 1, y + 1, z + 1), proj(x, y + 1, z + 1)]

        # 朝向观察者的南面（z+）就是照片立面本身，压太暗会让整张图像发霉；
        # 三面亮度比参考 Minecraft 本体：顶面最亮，正面次之，侧面最暗。
        poly(top, shade(c, 1.00))
        poly(east, shade(c, 0.76))
        poly(south, shade(c, 0.90))

    img.save(path)
    return {"path": str(path), "blocks": int(xs.size), "pixels": [w, h]}
