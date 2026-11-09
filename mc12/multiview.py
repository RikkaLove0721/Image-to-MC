"""多视角融合（v5）。

一张照片只能看到建筑的一个面：正立面告诉你层数、窗阵、配色，却对
进深一无所知 —— 之前只能按宽度乘 0.42 猜一个，房子是"薄片"还是
"盒子"全凭运气。

多视角要解决的就是这件事：

    正视图  立面文法（层数 / 窗距 / 墙色 / 腰线 / 勒脚）
    侧视图  进深与檐口高度 —— 同一个"高"，正视图横着量出宽度，
            侧视图横着量出进深，两者之比就是进深比
    俯视图  平面轮廓（L 形 / 凹口）与屋顶材质 —— 直接映射成方块网格
    自由视角  整体曝光与色偏，用来把另外几张的颜色拉到同一基准

四张图不要求同一台相机、同一个天气，只要求是同一栋楼。凡是算不准的
都退回默认值并在 notes 里写明，宁可保守也不要瞎猜。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

from . import color, geometry

ROLES = ("front", "side", "top", "free")
ROLE_LABELS = {
    "front": "正视图",
    "side": "侧视图",
    "top": "俯视图",
    "free": "自由视角",
}
ROLE_HINTS = {
    "front": "正对建筑正面，能看清窗户排列（必填）",
    "side": "从侧面拍，用来量进深",
    "top": "俯拍或平面图，用来定轮廓与屋顶",
    "free": "任意角度的照片，用来校准整体颜色",
}


# ---------------------------------------------------------------- 基础工具


def _luma(rgb: np.ndarray) -> np.ndarray:
    return 0.2126 * rgb[..., 0] + 0.7152 * rgb[..., 1] + 0.0722 * rgb[..., 2]


def _sat(rgb: np.ndarray) -> np.ndarray:
    return rgb.max(axis=-1) - rgb.min(axis=-1)


def _box3(a: np.ndarray) -> np.ndarray:
    p = np.pad(np.asarray(a, dtype=np.float64), 1, mode="edge")
    s = np.zeros((p.shape[0] + 1, p.shape[1] + 1), dtype=np.float64)
    s[1:, 1:] = p.cumsum(0).cumsum(1)
    return (s[3:, 3:] - s[:-3, 3:] - s[3:, :-3] + s[:-3, :-3]) / 9.0


def _close(mask: np.ndarray) -> np.ndarray:
    """3x3 闭运算，把窗户、栏杆打出来的小洞补上。"""
    m = _box3(mask.astype(np.float64)) > 0.42
    return _box3(m.astype(np.float64)) > 0.52


def load_view(path: str | Path, max_side: int = 1200) -> np.ndarray:
    """读一张视角图，去掉四周黑边，并把长边压到 1200 以内。

    侧视与俯视只需要比例，不需要原始像素。压一下能省掉几百 MB 内存，
    也避免四张 4K 照片同时驻留。
    """
    rgb = geometry.load_image(str(path))
    # 这里不能去黑边: 俯视图的边框就是地面, 裁掉之后背景基准会变成屋顶,
    # 整个轮廓会被判反。边框由后面的四角取色自然消化。
    h, w = rgb.shape[:2]
    if max(h, w) > max_side:
        scale = max_side / max(h, w)
        img = Image.fromarray((np.clip(rgb, 0, 1) * 255).astype(np.uint8))
        img = img.resize((max(8, int(w * scale)), max(8, int(h * scale))), Image.LANCZOS)
        rgb = np.asarray(img, dtype=np.float64) / 255.0
    return rgb


def load_views(paths: dict[str, str]) -> dict[str, np.ndarray]:
    """按角色读取，目录不存在或文件损坏的视角直接跳过。"""
    out: dict[str, np.ndarray] = {}
    for role in ROLES:
        p = paths.get(role)
        if not p:
            continue
        if not Path(p).exists():
            continue
        try:
            out[role] = load_view(p)
        except Exception:
            continue
    return out


def _corners(rgb: np.ndarray, frac: float = 0.06) -> np.ndarray:
    """四角背景色的中值。天空、地面、纯色底图都会被它代表。"""
    h, w = rgb.shape[:2]
    by = max(2, int(round(h * frac)))
    bx = max(2, int(round(w * frac)))
    patches = [
        rgb[:by, :bx], rgb[:by, -bx:], rgb[-by:, :bx], rgb[-by:, -bx:],
    ]
    return np.median(np.concatenate([p.reshape(-1, 3) for p in patches]), axis=0)


def _bbox(mask: np.ndarray, cover: float = 0.12) -> tuple[int, int, int, int] | None:
    """按行列覆盖率取主体包围盒，零星噪点不会把框撑大。"""
    if mask.sum() < 0.01 * mask.size:
        return None
    rows = mask.mean(axis=1)
    cols = mask.mean(axis=0)
    rt = max(float(rows.max()) * cover, 0.02)
    ct = max(float(cols.max()) * cover, 0.02)
    ys = np.nonzero(rows >= rt)[0]
    xs = np.nonzero(cols >= ct)[0]
    if ys.size < 2 or xs.size < 2:
        return None
    return int(xs[0]), int(ys[0]), int(xs[-1]) + 1, int(ys[-1]) + 1


def subject_mask(rgb: np.ndarray) -> np.ndarray:
    """从背景里抠出建筑主体。

    用 Lab 欧氏距离而不是 RGB 逐通道差：草地和灰屋顶在 RGB 里只差 0.1，
    换算到 Lab 能拉开到 14 以上，阈值才站得住。只做这一件事，再加闭运算
    补窗洞 —— 边缘发丝级的误差对进深比影响很小，不值得引入分割模型。
    """
    lab = color.rgb_to_lab(rgb)
    bg = color.rgb_to_lab(_corners(rgb).reshape(1, 1, 3))[0, 0]
    dist = np.linalg.norm(lab - bg, axis=-1)
    m = (dist > 11.0) & ~geometry.sky_mask(rgb)
    return _close(m)


def _lab_of(rgb: np.ndarray, mask: np.ndarray, q: float = 50) -> np.ndarray | None:
    if mask.sum() < 20:
        return None
    px = rgb[mask]
    lum = _luma(px.reshape(1, -1, 3)).ravel()
    pick = px[np.abs(lum - np.percentile(lum, q)) <= 0.04]
    if pick.shape[0] < 8:
        pick = px
    return color.rgb_to_lab(np.median(pick, axis=0).reshape(1, 1, 3))[0, 0]


def _resize_mask(mask: np.ndarray, width: int, height: int) -> np.ndarray:
    """把掩码降到方块网格。用面积平均，边缘按 45% 覆盖率取整。"""
    img = Image.fromarray((np.clip(mask, 0, 1) * 255).astype(np.uint8))
    small = img.resize((max(1, width), max(1, height)), Image.BOX)
    return np.asarray(small, dtype=np.uint8) > 110


# ---------------------------------------------------------------- 单视角度量


def side_metrics(rgb: np.ndarray) -> dict | None:
    """侧视图：进深比、屋顶走势、侧墙与檐口颜色。"""
    mask = subject_mask(rgb)
    box = _bbox(mask)
    if box is None:
        return None
    x0, y0, x1, y1 = box
    bw, bh = x1 - x0, y1 - y0
    if bw < 8 or bh < 8:
        return None
    crop = rgb[y0:y1, x0:x1]
    cm = mask[y0:y1, x0:x1]

    # 屋顶走势：每列最上面那个主体像素的 y，左右各取 1/3 比高低。
    # 相机没摆正也会造成倾斜，所以只有倾斜量超过 18% 楼高才判为坡屋顶。
    top_y = np.full(bw, np.nan)
    for x in range(bw):
        col = np.nonzero(cm[:, x])[0]
        if col.size and col.size > 0.35 * bh:
            top_y[x] = col[0]
    third = max(3, bw // 3)
    left = np.nanmedian(top_y[:third])
    right = np.nanmedian(top_y[-third:])
    slope = 0.0
    if np.isfinite(left) and np.isfinite(right):
        slope = float((right - left) / max(1, bw - third))
    roof_type = "pitched" if abs(slope) > 0.18 else "flat"

    # 颜色：上沿 12% 当屋顶/檐口，中段 45%~75% 当侧墙
    band = max(1, int(bh * 0.07))
    roof_m = cm.copy()
    roof_m[band:, :] = False
    wall_m = cm.copy()
    wall_m[: int(bh * 0.45), :] = False
    wall_m[int(bh * 0.75) :, :] = False

    return {
        "kind": "side",
        "size": (int(crop.shape[1]), int(crop.shape[0])),
        "aspect": float(bw / bh),
        "roof_type": roof_type,
        "slope": round(slope, 3),
        "roof": _lab_of(crop, roof_m, 72),
        "wall": _lab_of(crop, wall_m, 55),
    }


def top_metrics(rgb: np.ndarray) -> dict | None:
    """俯视图：平面轮廓、屋顶材质、平面形状。

    约定俯视图是"上北下南"：图像上边是背面，下边是正面。若与实际相反，
    模型会前后翻个个儿，但轮廓与尺寸仍然成立。
    """
    mask = subject_mask(rgb)
    box = _bbox(mask, cover=0.10)
    if box is None:
        return None
    x0, y0, x1, y1 = box
    bw, bh = x1 - x0, y1 - y0
    if bw < 10 or bh < 10:
        return None
    crop = rgb[y0:y1, x0:x1]
    cm = mask[y0:y1, x0:x1]
    fill = float(cm.mean())
    if fill < 0.55:
        # 分割失败（背景与屋顶同色）时不要拿半张图去当轮廓
        return {
            "kind": "top",
            "aspect": float(bw / bh),
            "fill": round(fill, 3),
            "footprint": None,
            "roof": None,
            "usable": False,
        }

    roof = _lab_of(crop, cm, 60)
    bright = _lab_of(crop, cm, 88)
    # 平面中挖空的部分：天井、内院、采光井
    holes = 1.0 - fill
    return {
        "kind": "top",
        "aspect": float(bw / bh),
        "fill": round(fill, 3),
        "holes": round(float(holes), 3),
        "footprint": cm,
        "roof": roof,
        "accent": bright,
        "usable": True,
    }


def white_balance_ref(rgb: np.ndarray) -> np.ndarray:
    """从最亮的一批像素估一个中性参考，给出三通道白平衡增益。

    自由视角的作用不是"把图调亮"，而是把不同天气、不同灯光下的色偏拉回
    同一个基准。取 92 分位以上的像素（白墙、天空、浅色石材）当"应该接近
    中性"的样本，算出每通道增益后限幅到 ±18% —— 再多就是在改建筑的颜色了。
    """
    lum = _luma(rgb)
    thr = float(np.percentile(lum, 92))
    px = rgb[lum >= thr]
    if px.shape[0] < 30:
        return np.ones(3)
    ref = np.median(px, axis=0)
    gains = float(ref.mean()) / np.maximum(ref, 1e-3)
    return np.clip(gains, 0.85, 1.18)


def apply_balance(rgb: np.ndarray, gains: np.ndarray) -> np.ndarray:
    """按白平衡增益校正整张图。"""
    if float(np.max(np.abs(gains - 1.0))) < 0.03:
        return rgb
    return np.clip(rgb * gains.reshape(1, 1, 3), 0, 1)


def free_metrics(rgb: np.ndarray) -> dict:
    """自由视角：报告整体亮度、色偏与白平衡增益，供其它视角对齐颜色。"""
    lum = _luma(rgb)
    p50 = float(np.percentile(lum, 50))
    rgb_mean = rgb.reshape(-1, 3).mean(axis=0)
    wb = white_balance_ref(rgb)
    return {
        "kind": "free",
        "luma": round(p50, 3),
        "balance": [round(float(v), 3) for v in rgb_mean],
        "wb": [round(float(v), 3) for v in wb],
    }


# ---------------------------------------------------------------- 融合


def fuse(
    views: dict[str, np.ndarray],
    width: int,
    height_blocks: int,
) -> dict:
    """把各视角的度量合成一组建模参数。

    width / height_blocks: 正视图降采样后的方块网格尺寸，用来把"比例"
    换算成"格数"。进深不直接取侧视图的像素宽度 —— 侧视图离得近拍得大、
    离得远拍得小，只有宽高比是尺度无关的。
    """
    side = side_metrics(views["side"]) if "side" in views else None
    top = top_metrics(views["top"]) if "top" in views else None
    free = free_metrics(views["free"]) if "free" in views else None

    notes: list[str] = []
    depth_votes: list[tuple[float, float]] = []

    if side:
        depth_votes.append((side["aspect"] * height_blocks, 0.65))
        if side["roof_type"] == "pitched":
            notes.append(f"侧视屋脊倾斜 {abs(side['slope']) * 100:.0f}%，判为坡屋顶")
    else:
        if "side" in views:
            notes.append("侧视图无法识别建筑主体，进深退回按宽度估算")

    if top and top.get("usable") and top.get("footprint") is not None:
        # 俯视图里横向是正面宽度，纵向是进深
        depth_votes.append((width * (1.0 / max(top["aspect"], 1e-3)), 0.35))
        if top["holes"] > 0.10:
            notes.append(f"俯视平面有 {top['holes'] * 100:.0f}% 挖空，按凹形轮廓建模")
    elif "top" in views:
        notes.append("俯视图轮廓不清晰，忽略平面轮廓")

    if depth_votes:
        total_w = sum(w for _, w in depth_votes)
        depth = sum(v * w for v, w in depth_votes) / total_w
        lo = max(6, int(round(width * 0.28)))
        hi = max(lo + 2, int(round(width * 1.6)))
        depth_i = int(np.clip(round(depth), lo, hi))
        if len(depth_votes) == 2:
            a, b = depth_votes[0][0], depth_votes[1][0]
            if abs(a - b) / max(a, b, 1) > 0.45:
                notes.append("侧视与俯视的进深估计差得较多，以侧视为主")
        depth_src = "side+top" if len(depth_votes) == 2 else ("side" if side else "top")
    else:
        depth_i = max(7, int(round(width * 0.42)))
        depth_src = "guess"
        notes.append("没有可用的侧视或俯视图，进深按宽度 0.42 倍估算")

    footprint = None
    if top and top.get("usable") and top.get("footprint") is not None:
        fp = _resize_mask(top["footprint"], width, depth_i)
        # 正面那一排必须落地，否则立面没地方贴
        if not fp[-1].any():
            fp[-1] = True
        if not fp.any():
            fp = None
        elif float(fp.mean()) < 0.62:
            notes.append("平面轮廓过于零碎，改用矩形体量")
            fp = None
        footprint = fp
    footprint_ratio = float(footprint.mean()) if footprint is not None else 1.0

    return {
        "views": [r for r in ROLES if r in views],
        "depth": depth_i,
        "depth_source": depth_src,
        "height_blocks": int(height_blocks),
        "footprint": footprint,
        "footprint_ratio": round(footprint_ratio, 3),
        "roof_type": side["roof_type"] if side else "flat",
        "side_wall": side["wall"] if side else None,
        "side_roof": side["roof"] if side else None,
        # 屋顶材质只信俯视图：侧视最上面那一条往往拍到的是檐口或女儿墙，
        # 拿它当屋顶会把整个屋面刷成墙色。没有俯视就退回"勒脚同色"。
        "roof": top["roof"] if top is not None and top.get("usable") else None,
        "roof_accent": top["accent"] if top and top.get("usable") else None,
        "wb": free["wb"] if free else [1.0, 1.0, 1.0],
        "notes": notes,
        "metrics": {"side": _strip(side), "top": _strip(top), "free": free},
    }


def _strip(m: dict | None) -> dict | None:
    """去掉大数组，只留可序列化的度量值。"""
    if m is None:
        return None
    out = {}
    for k, v in m.items():
        if k in ("footprint", "kind"):
            continue
        if hasattr(v, "tolist"):
            v = v.tolist()
        out[k] = v
    return out



