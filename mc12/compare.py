"""把"原图"和"模型渲染"拼成一张对比图，喂给视觉模型。

为什么要拼成一张：

    现在的流程里，AI 只拿到数字（宽高比、层高、开窗密度……），它能算出
    "比例对了"，但算不出"这个孟莎屋顶看起来像一块压扁的板"。这类判断只有
    真的看一眼才知道。

    而"看两张图"比"看一张拼图"贵一倍 token，还容易让模型在两张图之间
    跳来跳去。并排放好、标上"原图 / 模型"，一次读图就能回答"差在哪"。

    导出 JPEG 而不是 PNG：视觉模型的输入上限通常是 1024~2048，JPEG 小一个
    数量级，base64 之后还能塞得进请求体。
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# Windows 上必有中文字体；打包到别人机器上找不到就退回无标注，不让它崩
_FONTS = (
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/msyhbd.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "C:/Windows/Fonts/simsun.ttc",
)


def _font(size: int):
    for path in _FONTS:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return None


def _fit(img: Image.Image, h: int) -> Image.Image:
    w = max(1, int(round(img.width * h / max(1, img.height))))
    return img.resize((w, h), Image.LANCZOS)


def make_compare(
    photo: str,
    render: str,
    out: str,
    max_side: int = 1024,
    label_a: str = "原图",
    label_b: str = "模型",
) -> dict:
    """左原图 / 右模型，并排成一张 JPEG。返回输出信息。"""
    a = Image.open(photo).convert("RGB")
    b = Image.open(render).convert("RGB")

    pad, gap, top = 16, 14, 34
    # 先按单图高度排版，再整体缩到 max_side 以内
    h = 400
    a, b = _fit(a, h), _fit(b, h)
    W = pad * 2 + a.width + gap + b.width
    H = top + h + pad
    canvas = Image.new("RGB", (W, H), (246, 248, 251))
    canvas.paste(a, (pad, top))
    canvas.paste(b, (pad + a.width + gap, top))

    font = _font(16)
    if font is not None:
        d = ImageDraw.Draw(canvas)
        d.text((pad, 9), label_a, fill=(40, 50, 70), font=font)
        d.text((pad + a.width + gap, 9), label_b, fill=(20, 110, 70), font=font)

    # 整体缩放进 max_side，别给视觉模型塞一张几千像素的大图
    k = min(1.0, max_side / float(max(canvas.size)))
    if k < 1.0:
        canvas = canvas.resize(
            (max(1, int(canvas.width * k)), max(1, int(canvas.height * k))), Image.LANCZOS
        )

    out_path = Path(out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out_path, "JPEG", quality=82, optimize=True)
    return {
        "path": str(out_path),
        "size": list(canvas.size),
        "bytes": out_path.stat().st_size,
    }
