"""画发布包用的图标: 一个等轴测立方体, 与 exe 里的界面风格一致."""

from __future__ import annotations

from pathlib import Path

TOP = [(128, 26), (232, 84), (128, 142), (24, 84)]
LEFT = [(24, 84), (128, 142), (128, 238), (24, 180)]
RIGHT = [(232, 84), (232, 180), (128, 238), (128, 142)]

SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]


def make_icon(target: Path) -> Path:
    from PIL import Image, ImageDraw

    target.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.polygon(TOP, fill="#7FA6E8")
    draw.polygon(LEFT, fill="#2F6FDE")
    draw.polygon(RIGHT, fill="#1F4FA8")
    img.save(target, sizes=SIZES)
    return target
