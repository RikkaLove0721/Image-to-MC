"""体块组合自检：画 L 形、T 形、坡屋顶、斜拍四个场景，验证分段与缩进。

用法：
    python tools/check_massing.py [--width 40] [--save]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mc12 import massing  # noqa: E402


def _scene(kind: str, w: int = 480, h: int = 360):
    """返回 (有效掩码, 真值 [x0,x1] 列区间, 说明)。掩码 = 建筑像素。"""
    img = np.zeros((h, w), dtype=bool)
    yy, xx = np.mgrid[0:h, 0:w]
    ground = yy > h * 0.86
    gt: list[list[float]] = []

    if kind == "L":  # 主楼在左，右侧接一段更矮的侧翼
        img |= (xx > w * 0.05) & (xx < w * 0.60) & (yy > h * 0.12) & ~ground
        img |= (xx >= w * 0.60) & (xx < w * 0.90) & (yy > h * 0.45) & ~ground
        gt = [[0.60, 0.90]]
    elif kind == "T":  # 中间主楼，两翼更矮
        img |= (xx > w * 0.05) & (xx < w * 0.25) & (yy > h * 0.45) & ~ground
        img |= (xx >= w * 0.25) & (xx < w * 0.75) & (yy > h * 0.12) & ~ground
        img |= (xx >= w * 0.75) & (xx < w * 0.95) & (yy > h * 0.45) & ~ground
        gt = [[0.05, 0.25], [0.75, 0.95]]
    elif kind == "gable":  # 一栋带三角屋顶的房子：不该被切成体块
        body = (xx > w * 0.10) & (xx < w * 0.90) & (yy > h * 0.45) & ~ground
        roof = (yy > h * 0.10) & (yy <= h * 0.45)
        span = np.abs(xx - w * 0.5) < (1 - (yy - h * 0.10) / (h * 0.35)) * w * 0.40
        img |= (body | (roof & span)) & ~ground
        gt = []
    elif kind == "tilt":  # 单一体块，但整条屋顶线是斜的（透视）
        top = h * 0.10 + (xx / w) * h * 0.18
        img |= (yy > top) & (xx > w * 0.05) & (xx < w * 0.95) & ~ground
        gt = []
    else:
        raise ValueError(kind)
    return img, gt


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--width", type=int, default=40)
    ap.add_argument("--save", action="store_true")
    args = ap.parse_args()

    cases = [
        ("L", "L 形：右侧一段矮翼"),
        ("T", "T 形：两侧都是矮翼"),
        ("gable", "坡屋顶：不该分段"),
        ("tilt", "斜拍单一体块：不该分段"),
    ]
    ok = True
    for kind, desc in cases:
        mask, gt = _scene(kind)
        h, w = mask.shape
        ww = args.width
        hh = int(round(args.width * h / w))
        small = np.zeros((hh, ww), dtype=bool)
        # 面积平均降到方块网格
        for y in range(hh):
            for x in range(ww):
                y0, y1 = int(y * h / hh), int((y + 1) * h / hh)
                x0, x1 = int(x * w / ww), int((x + 1) * w / ww)
                small[y, x] = mask[y0:y1, x0:x1].mean() > 0.5

        res = massing.detect(small, None, depth=8)
        spans = [[p["x0"], p["x1"]] for p in res["panels"]]
        want = [[int(round(a * ww)), int(round(b * ww))] for a, b in gt]

        flat = 0.0
        if spans or want:
            inter = 0
            union = 0
            for s in spans:
                for t in want:
                    inter += max(0, min(s[1], t[1]) - max(s[0], t[0]))
            union = sum(s[1] - s[0] for s in spans) + sum(t[1] - t[0] for t in want) - inter
            flat = inter / union if union else 0.0

        good = (len(spans) == len(want)) and (not want or flat >= 0.6)
        ok = ok and good
        print(
            f"  {'OK ' if good else 'BAD'} {desc:14s} 分块 {len(spans)} 段（真值 {len(want)}）"
            f" 范围 {spans} 真值 {want}  重叠 {flat:.2f}"
        )

        if args.save:
            from PIL import Image

            vis = np.zeros((hh, ww, 3), dtype=np.uint8)
            vis[small] = (120, 120, 120)
            for p in res["panels"]:
                vis[p["y_top"] :, p["x0"] : p["x1"]] = (200, 90, 50)
            out = Path(__file__).resolve().parents[1] / "temp" / "massing_check"
            out.mkdir(parents=True, exist_ok=True)
            Image.fromarray(vis).resize((ww * 10, hh * 10), Image.NEAREST).save(
                out / f"{kind}.png"
            )

    print("结论:", "通过" if ok else "有待调整")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
