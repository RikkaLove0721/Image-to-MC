"""画发布包用的图标: 等轴测立方体 + 坐在上面的 Q 版小C。

立方体的三个顶点坐标与颜色是软件的识别符,不要改 —— 界面里的方块图形、
悬浮形象脚下的方块,用的都是这三个色值。人物是外部位图(xiaoc_cut.png,
白底已抠成透明),没有素材时退化成纯立方体,不影响打包。
"""

from __future__ import annotations

from pathlib import Path

TOP = [(128, 26), (232, 84), (128, 142), (24, 84)]
LEFT = [(24, 84), (128, 142), (128, 238), (24, 180)]
RIGHT = [(232, 84), (232, 180), (128, 238), (128, 142)]

SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]

# 人物贴图: 与脚本同目录,跟着 pack/ 一起走
FIGURE = Path(__file__).with_name("xiaoc_cut.png")


def make_icon(target: Path) -> Path:
    from PIL import Image, ImageDraw

    target.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.polygon(TOP, fill="#7FA6E8")
    draw.polygon(LEFT, fill="#2F6FDE")
    draw.polygon(RIGHT, fill="#1F4FA8")

    if FIGURE.exists():
        fig = Image.open(FIGURE).convert("RGBA")
        # 人高占画布 51%:再大人头会顶出画布,再小 48px 下认不出是谁
        fh = int(256 * 0.51)
        fw = max(1, int(fig.width * fh / fig.height))
        fig = fig.resize((fw, fh), Image.LANCZOS)
        # 关键是把**屁股**对齐到顶面,不是把图片居中:
        # 抠图里臀部大约在人物高度的 62%(裙摆最宽那一行),
        # 让它落在顶面菱形右侧的 (170, 88)。
        px = 158 - fw // 2
        py = 88 - int(fh * 0.62)
        img.alpha_composite(fig, (px, py))

    img.save(target, sizes=SIZES)
    return target
