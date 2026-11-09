"""特征提取 -> 理想立面合成（v4 核心）。

v3 之前是"照着像素画"：降采样后的照片直接配色、抹噪、镜像。
问题在于照片是抓拍的 —— 顶部弧形女儿墙、旗杆、空调外机、侧翼错层，
这些"真实世界的杂乱"会原样进入模型，顶部永远参差不齐。

v4 换思路：**照片只用来提取特征，模型由立面文法重新合成**。

    提取：层数 F、层高 fh、窗距 pitch、窗宽 bw/bh、
          墙色 / 腰线色 / 墙裙色（CIEDE2000 映射到 1.12 方块）
    合成：左右严格对称的立面 ——
          勒脚 -> 分层窗阵（全部楼层共用同一列网格）
          -> 每层腰线 -> 顶部双层檐口 -> 平屋顶 + 居中山花

这样出来的模型必然：顶部平整、窗阵对齐、左右对称 ——
也就是 Minecraft 建筑该有的样子。照片里的杂乱被留在照片里。
"""

from __future__ import annotations

import numpy as np
from PIL import Image

from . import color, palette, tidy

# 区域编码：0=墙 1=腰线/檐口 2=勒脚 3=空（该处不放方块）
VOID = 3


# ---------------------------------------------------------------- 特征提取

def _cluster_rows(cy: np.ndarray, tol: float) -> list[list[int]]:
    """按中心 y 聚类成楼层行。"""
    order = list(np.argsort(cy))
    rows: list[list[int]] = []
    for i in order:
        if rows and cy[i] - cy[rows[-1][-1]] <= tol:
            rows[-1].append(int(i))
        else:
            rows.append([int(i)])
    return rows


def _window_rows(boxes):
    """把窗框连通域聚成楼层行，返回 [(行中心 y, 该行窗框列表)]。"""
    if not boxes:
        return []
    cy = np.array([(b[1] + b[3]) / 2.0 for b in boxes])
    bh = np.median([b[3] - b[1] + 1 for b in boxes])
    rows = _cluster_rows(cy, max(1.5, float(bh) * 0.8))
    out = []
    for r in rows:
        r_boxes = [boxes[i] for i in r]
        out.append((float(np.median(cy[r])), r_boxes))
    return out


def extract_features(
    rgb: np.ndarray,
    win: np.ndarray | None,
    sky: np.ndarray | None = None,
) -> dict:
    """从降采样网格里提取立面特征。

    rgb: (H,W,3) 降采样后的照片（1 像素 = 1 方块）
    win: (H,W) 窗掩码（与 rgb 同分辨率）
    """
    h, w = rgb.shape[:2]
    boxes = tidy._windows(win) if win is not None and win.any() else []
    rows = _window_rows(boxes)
    valid = np.ones((h, w), dtype=bool)
    if sky is not None:
        valid &= ~sky

    feat = {
        "floors": 0, "floor_h": 6, "pitch": 7,
        "bw": 3, "bh": 3,
        "wall": None, "trim": None, "plinth": None,
        "windows_raw": len(boxes),
    }

    # ---- 层数 / 层高 / 窗距 / 窗尺寸 ----
    if len(rows) >= 2:
        feat["floors"] = len(rows)
        centers = sorted(c for c, _ in rows)
        gaps = np.diff(centers)
        feat["floor_h"] = int(np.clip(round(float(np.median(gaps))), 5, 8))
        best = max(rows, key=lambda p: len(p[1]))
        xs = sorted(b[0] for b in best[1])
        if len(xs) >= 3:
            feat["pitch"] = int(np.clip(round(float(np.median(np.diff(xs)))), 5, 8))
    else:
        feat["floors"] = 4
        feat["floor_h"] = 6
        feat["pitch"] = 7

    bh_med = float(np.median([b[3] - b[1] + 1 for b in boxes])) if boxes else 3.0
    bw_med = float(np.median([b[2] - b[0] + 1 for b in boxes])) if boxes else 3.0
    feat["bh"] = int(np.clip(round(bh_med), 2, 4))
    feat["bw"] = int(np.clip(round(bw_med), 3, 5))

    # ---- 三个区域色（Lab 中值）----
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
    warm = (r - b) / (r + b + 1e-6)
    sat = rgb.max(axis=-1) - rgb.min(axis=-1)

    # 墙色：暖色像素的中值（红砖、米黄墙都适用）
    wall_m = valid & (warm > 0.10) & (lum > 0.15)
    if wall_m.sum() < 30:
        wall_m = valid & (lum > 0.20)
    # 腰线 / 檐口：亮而中性的像素（白窗框、白瓷砖带）
    trim_m = valid & (lum > 0.62) & (sat < 0.14)
    # 墙裙：底部 12% 行
    y0 = int(h * 0.88)
    plinth_lab = None
    if y0 < h - 2:
        sub = rgb[y0:, :]
        m = valid[y0:, :]
        if m.sum() >= 20:
            plinth_lab = color.rgb_to_lab(
                np.median(sub[m], axis=0).reshape(1, 1, 3)
            )[0, 0]

    def _med(mask, q=50):
        if mask.sum() < 10:
            return None
        px = rgb[mask]
        lum_m = 0.2126 * px[:, 0] + 0.7152 * px[:, 1] + 0.0722 * px[:, 2]
        pick = px[np.abs(lum_m - np.percentile(lum_m, q)) <= 0.02]
        if pick.shape[0] < 5:
            pick = px
        return color.rgb_to_lab(np.median(pick, axis=0).reshape(1, 1, 3))[0, 0]

    # 墙色取"最暖的那批"像素的中值：窗框、浅色饰带 warm 低，
    # 混进来会把砖墙冲淡成"脏粉"。按 warm 排序取前 50%。
    if wall_m.sum() >= 20:
        wm = warm[wall_m]
        thr = np.percentile(wm, 50)
        core_m = wall_m & (warm >= thr)
        feat["wall"] = _med(core_m)
    else:
        feat["wall"] = _med(wall_m)
    # 腰线/窗框：暗的那一半多半是被阴影压住的窗洞，取亮 85 分位
    feat["trim"] = _med(trim_m, q=85)
    feat["plinth"] = plinth_lab
    return feat


# ---------------------------------------------------------------- 立面合成

def synthesize(feat: dict, width: int) -> tuple[np.ndarray, np.ndarray, dict]:
    """按立面文法合成一张"理想化"的左右对称立面。

    返回 (regions, win, info)：
        regions: (H,W) int8，0=墙 1=腰线/檐口 2=勒脚
        win:     (H,W) bool，玻璃格（含正门）
    """
    W = int(width)
    F = max(2, int(feat["floors"]))
    fh = int(feat["floor_h"])
    pitch = int(feat["pitch"])
    bw = int(feat["bw"])
    bh = int(feat["bh"])

    # 高度上限：MC 里楼太高显得假，宽高比控制在 0.85 以内
    max_h = max(24, int(W * 0.85))
    overhead = 3 + 3          # 双层檐口 + 女儿墙 + 山花三级
    fh = int(np.clip(fh, 5, max(5, (max_h - 2 - overhead) // F)))
    bh = int(np.clip(bh, 2, fh - 3))
    bw = int(np.clip(bw, 3, max(3, pitch - 2)))
    pitch = max(pitch, bw + 2)

    axis = W // 2

    # ---- 窗列网格：全楼共用，左右对称 ----
    margin = 2
    n_max = (W - 2 * margin - bw) // pitch + 1
    n_cols = max(3, n_max if n_max % 2 == 1 else n_max - 1)
    half = (n_cols - 1) / 2.0
    centers = [round(axis + (k - half) * pitch) for k in range(n_cols)]
    centers = [
        c for c in centers
        if margin <= c - bw // 2 and c + (bw - bw // 2) <= W - margin
    ]

    # ---- 画布：勒脚 + F 层 + 檐口 + 女儿墙 + 山花两级 ----
    plinth_h = 1
    cornice_h = 2
    H = plinth_h + F * fh + cornice_h + 1 + 3
    regions = np.zeros((H, W), dtype=np.int8)   # 0=墙 1=腰线/檐口 2=勒脚
    win = np.zeros((H, W), dtype=bool)

    for f in range(F):
        y_top = plinth_h + f * fh
        y_bot = y_top + fh - 1
        wy0, wy1 = y_top + 1, min(y_top + bh, y_bot - 1)
        for c in centers:
            x0 = c - bw // 2
            if x0 < margin or x0 + bw > W - margin:
                continue
            win[wy0:wy1 + 1, x0:x0 + bw] = True
        regions[y_bot, :] = 1                 # 每层腰线

    regions[0:plinth_h, :] = 2                # 勒脚
    y_eave = plinth_h + F * fh
    regions[y_eave:y_eave + cornice_h, :] = 1 # 双层檐口
    y_para = y_eave + cornice_h               # 女儿墙行（墙色，平屋顶收边）
    y_ped1 = y_para + 1                       # 山花第一级
    y_ped2 = y_para + 2                       # 山花第二级
    y_ped3 = y_para + 3                       # 山花第三级（顶）

    # 山花：三级踏步。屋顶以上除山花外都是空的（VOID），
    # 否则画布默认 0=墙，女儿墙之上会糊成一条整宽的墙带，看不出凸起。
    # 每级左右各 1 格描檐口色勾边，顶级整体做压顶。
    pw = max(7, min(round(W * 0.26) // 2 * 2 + 1, W - 6))
    tiers = [pw, max(3, pw - 4), max(3, pw - 8)]
    for row, tw in zip((y_ped1, y_ped2, y_ped3), tiers):
        regions[row, :] = VOID
        x0 = axis - tw // 2
        regions[row, x0:x0 + tw] = 0
    # 顶级做压顶：整级用檐口色，读起来是"收头"而不是一坨墙
    tw = tiers[2]
    x0 = axis - tw // 2
    regions[y_ped3, x0:x0 + tw] = 1

    # ---- 正门：底层中央，3 格宽，占满整层高（含该层腰线）----
    dw = 3
    gx0 = axis - dw // 2
    gy0 = plinth_h
    gy1 = plinth_h + fh - 1
    win[gy0:gy1 + 1, gx0:gx0 + dw] = True
    # 门两侧墙垛压成腰线色，做出"门框"
    if gx0 - 1 >= 0:
        regions[gy0:gy1 + 1, gx0 - 1] = 1
    if gx0 + dw < W:
        regions[gy0:gy1 + 1, gx0 + dw] = 1

    info = {
        "W": W, "H": H, "floors": F, "floor_h": fh,
        "pitch": pitch, "bw": bw, "bh": bh,
        "cols": len(centers), "axis": axis,
        "pediment_w": pw,
        "h_eave": y_eave, "h_para": y_para,
        "h_ped1": y_ped1, "h_ped2": y_ped2, "h_ped3": y_ped3,
        "plinth_h": plinth_h,
    }

    # 行序约定：合成时 y=0 是"地面"（自下而上画），但全链路（run.py /
    # voxel.build）用的是图像行序 —— y=0 是图像顶部。翻转一次统一约定。
    regions = regions[::-1].copy()
    win = win[::-1].copy()
    return regions, win, info


# ---------------------------------------------------------------- 三维体量

def _fit_footprint(fp: np.ndarray | None, width: int, depth: int) -> np.ndarray:
    """把俯视图轮廓对齐到 (depth, width) 的方块网格。"""
    if fp is None:
        return np.ones((depth, width), dtype=bool)
    a = np.asarray(fp, dtype=bool)
    if a.shape != (depth, width):
        img = Image.fromarray((a * 255).astype(np.uint8))
        small = img.resize((width, max(1, depth)), Image.BOX)
        a = np.asarray(small, dtype=np.uint8) > 127
    if not a.any():
        return np.ones((depth, width), dtype=bool)
    # 正立面总得有一排落地，否则窗户贴不上去
    if not a[-1].any():
        a[-1] = a[-2] if depth > 1 else True
    return a


def build_volume(
    regions: np.ndarray,
    win: np.ndarray,
    info: dict,
    mats: dict,
    depth: int = 0,
    recess: int = 1,
    footprint: np.ndarray | None = None,
) -> tuple[np.ndarray, list]:
    """把合成立面挤出成一栋**有进深**的建筑，而不是一块 3 格厚的板。

    mats 至少包含 wall / trim / plinth / roof / glass 五种 Block，可选 side。
    depth 为进深（格），0 表示按宽度自动取 0.42 倍。
    footprint 为 (depth, W) 的布尔平面轮廓，行 0 = 背面、最后一行 = 正面；
    传 None 就是矩形体量。多视角模式下它来自俯视图。

    结构：
        正立面      z = depth-1，用 regions / win 的材质与窗洞
        背墙与侧墙   素墙色（侧墙可用侧视图的材质）
        屋顶甲板    h_eave 层铺满整个轮廓
        女儿墙      屋顶以上沿轮廓一圈，只留 1 格厚
        山花        只在正立面，立在女儿墙之上
        窗          前脸掏空，玻璃内缩 recess 格形成窗洞

    内部全空，直接就是空心壳，不需要再跑 hollow_shell。
    """
    H, W = regions.shape
    if depth <= 0:
        depth = max(7, int(round(W * 0.42)))
    depth = int(depth)

    m_wall = mats["wall"]
    m_trim = mats["trim"]
    m_plinth = mats["plinth"]
    m_roof = mats.get("roof", m_plinth)
    m_glass = mats["glass"]
    m_side = mats.get("side") or m_wall

    inside = _fit_footprint(footprint, W, depth)

    # 用 (id, meta) 作键而不是 Block.key: key 只含名称,
    # 白色混凝土 251:0 与淡灰色混凝土 251:8 会撞成同一个键。
    order: list = []
    index: dict[tuple[int, int], int] = {}

    def bid(block) -> int:
        k = (block.id, block.meta)
        if k not in index:
            index[k] = len(order)
            order.append(block)
        return index[k]

    ids = {
        "wall": bid(m_wall),
        "trim": bid(m_trim),
        "plinth": bid(m_plinth),
        "roof": bid(m_roof),
        "glass": bid(m_glass),
        "side": bid(m_side),
    }

    vox = np.full((W, H, depth), -1, dtype=np.int16)

    h_eave = int(info["h_eave"])
    h_para = int(info["h_para"])

    # 轮廓边缘：四个方向里有任意一个邻居在外，就是外墙
    pad = np.zeros((depth + 2, W + 2), dtype=bool)
    pad[1:-1, 1:-1] = inside
    ring = inside & ~(
        pad[0:-2, 1:-1] & pad[2:, 1:-1] & pad[1:-1, 0:-2] & pad[1:-1, 2:]
    )
    glass_z = int(np.clip(depth - 1 - max(0, recess), 0, depth - 1))

    # 立面是图像行序，这里统一换成"自地面向上"的 y 再写体素
    def facade(y_ground: int, x: int) -> int:
        y_img = H - 1 - y_ground
        r = int(regions[y_img, x])
        if r == VOID:
            return -1
        return {0: ids["wall"], 1: ids["trim"], 2: ids["plinth"]}[r]

    def outer(x: int, z: int) -> int:
        """外墙材质：左右两面用侧视图的色，前后用正立面的墙色。"""
        if x == 0 or x == W - 1:
            return ids["side"]
        return ids["wall"]

    for y in range(H):
        above_roof = y > h_eave
        y_img = H - 1 - y
        for z in range(depth):
            front = z == depth - 1
            near_front = z >= depth - 2
            for x in range(W):
                if not inside[z, x]:
                    continue
                is_win = bool(win[y_img, x]) and not above_roof

                if above_roof:
                    # 屋顶以上：正立面走女儿墙与山花，其余沿轮廓围两行女儿墙
                    if front:
                        v_f = facade(y, x)
                        if v_f >= 0:
                            vox[x, y, z] = v_f
                    elif y > h_para and near_front:
                        # 山花向后加厚到 2 格，避免变成一片薄纸
                        v_f = facade(y, x)
                        if v_f >= 0:
                            vox[x, y, z] = v_f
                    elif y <= h_para + 1 and ring[z, x]:
                        vox[x, y, z] = outer(x, z)
                    continue

                if is_win and front:
                    # 窗洞：前脸留空，玻璃内缩 recess 格
                    if z == glass_z:
                        vox[x, y, z] = ids["glass"]
                    continue
                if is_win and z == glass_z and inside[glass_z, x]:
                    vox[x, y, z] = ids["glass"]
                elif front:
                    vox[x, y, z] = facade(y, x)
                elif y == h_eave:
                    # 屋顶甲板：铺满整个轮廓
                    vox[x, y, z] = ids["roof"]
                elif ring[z, x]:
                    vox[x, y, z] = outer(x, z)
    # 前后空白层清理交给 voxel.trim
    return vox, order
