"""图像读取、方块级降采样，以及三种"厚度先验"。

关键点：**先把照片降采样到 1 像素 = 1 方块**，之后所有颜色匹配才是同一尺度
上的比较。这一步做错，后面无论怎么调都会系统性偏色。

厚度先验决定模型的第三维（Z 向挤出量），三种模式共用同一个输出格式：
    thickness[y_img][x] = 该位置沿 Z 向的方块数，0 表示不放方块。
"""

from __future__ import annotations

import numpy as np
from PIL import Image, ImageFilter, ImageOps


def load_image(path: str) -> np.ndarray:
    """读取照片为 float RGB（0~1），自动摆正 EXIF 方向。

    注意：这里不做降采样 —— 透视矫正、光照归一化、窗洞检测都必须在
    **原始分辨率**上做，降到 1 像素 = 1 方块之后再矫正，等于拿方块去
    插值方块，越补越糊。
    """
    img = Image.open(path)
    img = ImageOps.exif_transpose(img).convert("RGB")
    return np.asarray(img, dtype=np.float64) / 255.0


def downsample(rgb: np.ndarray, width: int) -> np.ndarray:
    """面积平均降到"1 像素 = 1 方块"。"""
    h0, w0 = rgb.shape[:2]
    h = max(1, int(round(width * h0 / w0)))
    arr = np.clip(rgb, 0, 1) * 255
    small = Image.fromarray(arr.astype(np.uint8)).resize((width, h), Image.BOX)
    return np.asarray(small, dtype=np.float64) / 255.0


def trim_border(rgb: np.ndarray, tol: float = 0.06) -> tuple[np.ndarray, tuple[int, int, int, int]]:
    """裁掉照片四周的黑边/白边（截图、扫描件常见）。

    只从外往里扫，一旦遇到"不是边色"的行/列就停，不会误伤画面主体。
    """
    h, w = rgb.shape[:2]
    if h < 8 or w < 8:
        return rgb, (0, 0, w, h)
    ref = np.median(
        np.concatenate([rgb[0], rgb[-1], rgb[:, 0], rgb[:, -1]]), axis=0
    )
    near = np.all(np.abs(rgb - ref[None, None, :]) < tol, axis=-1)

    top = 0
    while top < h - 4 and near[top].mean() > 0.90:
        top += 1
    bottom = h - 1
    while bottom > 4 and near[bottom].mean() > 0.90:
        bottom -= 1
    left = 0
    while left < w - 4 and near[:, left].mean() > 0.90:
        left += 1
    right = w - 1
    while right > 4 and near[:, right].mean() > 0.90:
        right -= 1
    if (top, left, right, bottom) == (0, 0, w - 1, h - 1):
        return rgb, (0, 0, w, h)
    return rgb[top : bottom + 1, left : right + 1], (left, top, right + 1, bottom + 1)


def median_filter(rgb: np.ndarray, size: int = 9) -> np.ndarray:
    """中值滤波。用来在降采样前抹掉窗框、砖缝这类"少数派"细线。"""
    if size < 3:
        return rgb
    arr = (np.clip(rgb, 0, 1) * 255).astype(np.uint8)
    out = Image.fromarray(arr).filter(ImageFilter.MedianFilter(int(size) | 1))
    return np.asarray(out, dtype=np.float64) / 255.0


def load_blocks(path: str, width: int) -> tuple[np.ndarray, tuple[int, int]]:
    """读取照片并直接降采样到"方块分辨率"（保留旧接口）。"""
    rgb = load_image(path)
    return downsample(rgb, width), (rgb.shape[1], rgb.shape[0])


def _luma(rgb: np.ndarray) -> np.ndarray:
    return 0.2126 * rgb[..., 0] + 0.7152 * rgb[..., 1] + 0.0722 * rgb[..., 2]


def _box3(a: np.ndarray) -> np.ndarray:
    """3x3 均值（积分图实现）。"""
    p = np.pad(np.asarray(a, dtype=np.float64), 1, mode="edge")
    s = np.zeros((p.shape[0] + 1, p.shape[1] + 1), dtype=np.float64)
    s[1:, 1:] = p.cumsum(0).cumsum(1)
    return (s[3:, 3:] - s[:-3, 3:] - s[3:, :-3] + s[:-3, :-3]) / 9.0


def sky_mask(rgb: np.ndarray) -> np.ndarray:
    """从每列顶部向下剥离天空，返回 True=天空。

    只用像素统计，不需要模型。对户外照片有效，对室内/纯墙面会自动放弃。
    """
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    lum = _luma(rgb)
    sat = rgb.max(axis=-1) - rgb.min(axis=-1)

    cand = (
        ((b > r + 0.02) & (lum > 0.45))
        | ((lum > 0.70) & (sat < 0.10))
        | (lum > 0.92)
    )
    # 照片最上几行常常是黑色边框。若不处理，这一列会"一开始就不是天空"，
    # 整列都不被剥离，最后在模型左侧立起一根十几格高的灰柱子。
    top = min(3, rgb.shape[0])
    cand[:top] |= (lum[:top] < 0.22)
    # 沿列做前缀与：从顶部连续为真的部分才算天空
    contiguous = np.cumprod(cand.astype(np.uint8), axis=0).astype(bool)

    frac = float(contiguous.mean())
    if frac < 0.03 or frac > 0.75:
        return np.zeros_like(contiguous)
    return contiguous


def thickness_flat(rgb: np.ndarray, base: int = 1, sky: bool = True) -> np.ndarray:
    """平板：整块 base 个方块厚，相当于把照片做成一面"像素墙"。最稳，不会出错。"""
    h, w = rgb.shape[:2]
    t = np.full((h, w), max(1, int(base)), dtype=np.int32)
    if sky:
        t[sky_mask(rgb)] = 0
    return t


def thickness_relief(
    rgb: np.ndarray,
    base: int = 1,
    strength: float = 3.0,
    max_t: int = 6,
    sky: bool = True,
) -> np.ndarray:
    """浮雕：亮处凸出、暗处凹陷，形成被"雕刻"出来的浅浮雕。

    深度由亮度驱动（类似压印/黑白浮雕），先做 3x3 平滑抑制噪点，
    再用 5%/95% 分位拉伸，避免个别极值把整体压平。
    """
    lum = _box3(_luma(rgb))
    lo, hi = np.percentile(lum, 5), np.percentile(lum, 95)
    span = max(hi - lo, 1e-6)
    norm = np.clip((lum - lo) / span, 0.0, 1.0)

    t = base + np.round(strength * norm)
    t = np.clip(t, 1, max_t).astype(np.int32)
    if sky:
        t[sky_mask(rgb)] = 0
    return t


def _sky_by_gradient(rgb: np.ndarray) -> np.ndarray:
    """备用天际线：每列取亮度变化最剧烈的位置当作天空/地面分界。"""
    lum = _luma(rgb)
    h, w = lum.shape
    if h < 2:
        return np.zeros((h, w), dtype=bool)
    d = np.abs(np.diff(lum, axis=0))
    idx = np.argmax(d, axis=0)
    return np.arange(h)[:, None] <= idx[None, :]


def _horizon_rows(mask: np.ndarray, h: int, w: int) -> np.ndarray:
    """每列第一个"非天空"行号，找不到则记 h。"""
    out = np.full(w, h, dtype=np.int32)
    for x in range(w):
        col = np.nonzero(~mask[:, x])[0]
        if col.size:
            out[x] = col[0]
    return out


def thickness_terrain(
    rgb: np.ndarray,
    max_t: int = 10,
    min_depth: int = 3,
    sky: bool = True,
) -> np.ndarray:
    """地形：按天际线把主体轮廓整体挤出，形成山峦体块。

    适合风景/山体照片。对建筑照片会退化成一堵墙，属预期行为。
    天际线取"颜色判据"与"梯度判据"中**较高**的那条：远处的淡色山体
    容易被颜色判据误判成天空，梯度判据能兜住。
    """
    h, w = rgb.shape[:2]
    grad_row = _horizon_rows(_sky_by_gradient(rgb), h, w)
    first_ground = grad_row
    if sky:
        color_row = _horizon_rows(sky_mask(rgb), h, w)
        first_ground = np.minimum(color_row, grad_row)

    # 单张照片只能给出轮廓，纵深只能"编"。让所有列用**同一个厚度**，
    # 得到干净的轮廓挤出体；若按高度给不同厚度，相邻列会互相跳变，
    # 表面全是竖条纹（试过，很难看）。
    ys = np.arange(h)[:, None]
    t = np.where(ys >= first_ground[None, :], max(1, int(max_t)), 0).astype(np.int32)
    _ = min_depth
    return t


MODES = {
    "flat": thickness_flat,
    "relief": thickness_relief,
    "terrain": thickness_terrain,
}


def build_thickness(rgb: np.ndarray, mode: str, **kwargs) -> np.ndarray:
    if mode not in MODES:
        raise ValueError(f"未知模式 {mode}，可选：{list(MODES)}")
    t = MODES[mode](rgb, **kwargs)
    if t.max() < 1:
        t = np.ones_like(t)
    return t
