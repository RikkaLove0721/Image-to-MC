"""生成一张合成测试照片（房子 + 天空 + 地面），用于验证流水线。

真实项目里直接把手机照片丢进 input/ 即可，这个脚本只是为了让全链路
在没有素材时也能自检。
"""

from __future__ import annotations


from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw


def make_house(w: int = 640, h: int = 480, seed: int = 7) -> Image.Image:
    rng = np.random.default_rng(seed)
    img = Image.new("RGB", (w, h), (255, 255, 255))
    d = ImageDraw.Draw(img)

    # 天空：上蓝下白
    sky_h = int(h * 0.42)
    for y in range(sky_h):
        t = y / max(sky_h - 1, 1)
        col = (
            int(120 + 120 * t),
            int(165 + 80 * t),
            int(210 + 45 * t),
        )
        d.line([(0, y), (w, y)], fill=col)

    # 草地
    for y in range(sky_h, h):
        t = (y - sky_h) / max(h - sky_h, 1)
        col = (int(92 - 22 * t), int(118 - 20 * t), int(66 - 12 * t))
        d.line([(0, y), (w, y)], fill=col)

    # 房屋主体
    bx0, bx1 = int(w * 0.17), int(w * 0.83)
    by0, by1 = int(h * 0.36), int(h * 0.80)
    d.rectangle([bx0, by0, bx1, by1], fill=(214, 200, 172))

    # 屋檐阴影（让立面有层次）
    d.rectangle([bx0, by0, bx1, by0 + 10], fill=(176, 162, 136))

    # 屋顶
    roof_c = (w // 2, int(h * 0.13))
    d.polygon([(bx0 - 24, by0), roof_c, (bx1 + 24, by0)], fill=(140, 62, 48))
    d.polygon([(bx0 - 24, by0), roof_c, (w // 2, by0)], fill=(158, 74, 56))

    # 烟囱
    cx0 = int(w * 0.68)
    d.rectangle([cx0, int(h * 0.16), cx0 + 26, by0 - 6], fill=(120, 86, 74))

    # 门
    dx0, dx1 = int(w * 0.45), int(w * 0.55)
    d.rectangle([dx0, int(h * 0.60), dx1, by1], fill=(88, 58, 34))
    d.rectangle([dx0 + 6, int(h * 0.62), dx1 - 6, by1 - 6], fill=(74, 47, 27))

    # 窗（白框 + 深色玻璃）
    win_w, win_h = int(w * 0.12), int(h * 0.16)
    for wx, wy in [
        (int(w * 0.22), int(h * 0.45)),
        (int(w * 0.66), int(h * 0.45)),
        (int(w * 0.22), int(h * 0.66)),
        (int(w * 0.66), int(h * 0.66)),
    ]:
        d.rectangle([wx, wy, wx + win_w, wy + win_h], fill=(238, 234, 226))
        d.rectangle([wx + 5, wy + 5, wx + win_w - 5, wy + win_h - 5], fill=(52, 68, 86))
        d.rectangle(
            [wx + 5, wy + win_h // 2 - 2, wx + win_w - 5, wy + win_h // 2 + 2],
            fill=(238, 234, 226),
        )
        d.rectangle(
            [wx + win_w // 2 - 2, wy + 5, wx + win_w // 2 + 2, wy + win_h - 5],
            fill=(238, 234, 226),
        )

    # 地面小路
    d.polygon(
        [(int(w * 0.36), h), (int(w * 0.63), h), (int(w * 0.56), by1), (int(w * 0.44), by1)],
        fill=(150, 143, 128),
    )

    arr = np.asarray(img, dtype=np.float64)

    # 加一点颗粒感，避免纯色块
    arr += rng.normal(0, 5.0, arr.shape)
    # 轻微暗角
    yy, xx = np.mgrid[0:h, 0:w]
    r = np.sqrt(((xx - w / 2) / (w / 2)) ** 2 + ((yy - h / 2) / (h / 2)) ** 2)
    arr -= 26 * np.clip(r - 0.55, 0, 1)[..., None]

    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))


def make_hills(w: int = 640, h: int = 420, seed: int = 3) -> Image.Image:
    """合成一张起伏山峦，用于测试 terrain 模式。"""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float64)

    nb = np.arange(w, dtype=np.float64) / w * 6.0
    prof = (
        0.55 * np.sin(nb * 1.7 + 0.4)
        + 0.30 * np.sin(nb * 3.9 + 1.1)
        + 0.16 * np.sin(nb * 8.3 + 2.2)
    )
    prof = (prof - prof.min()) / (prof.max() - prof.min())

    t = (yy / h)[..., None]
    sky_top = np.array([118.0, 168.0, 218.0])
    sky_bot = np.array([228.0, 238.0, 248.0])
    img = sky_top + (sky_bot - sky_top) * t

    layers = [
        (0.34, 0.17, (126, 140, 150)),
        (0.50, 0.19, (98, 118, 104)),
        (0.66, 0.21, (74, 96, 66)),
    ]
    for bias, amp, tone in layers:
        line = h * (bias + amp * (1.0 - prof))          # (w,)
        m = yy > line[None, :]                          # (h,w)
        drop = np.clip((yy - line[None, :]) / (h * 0.55), 0.0, 1.0)
        shade = np.array(tone, dtype=np.float64)[None, None, :] * (1.0 - 0.28 * drop)[..., None]
        img = np.where(m[..., None], shade, img)

    img += rng.normal(0, 4.0, img.shape)
    return Image.fromarray(np.clip(img, 0, 255).astype(np.uint8))


if __name__ == "__main__":
    root = Path(__file__).resolve().parent
    (root / "input").mkdir(exist_ok=True)
    make_house().save(root / "input" / "house.png")
    make_hills().save(root / "input" / "hills.png")
    print("已生成", root / "input" / "house.png")
    print("已生成", root / "input" / "hills.png")
