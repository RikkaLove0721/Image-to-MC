"""室内大厅合成: 从室内透视照提取颜色与尺度, 按空间文法重建一个可进入的厅.

照片是一点透视的室内场景, 不是建筑外立面. 立面文法(勒脚/窗阵/檐口/山花)
在这里完全是错的东西, 所以单独一条路线:

    外壳(地/墙/顶) -> 水池 -> 柱廊 -> 端墙壁画 -> 藻井吊顶 -> 灯具

照片只用来回答两件事:
  1. 配色: 吊顶/墙/柱/池岸/池水/壁画各自是什么颜色
  2. 尺度: 水池占厅宽的多少 (横向蓝色跨度是可测的, 比数柱子可靠)

其余比例(长宽比/层高/开间数)用室内大厅的常见值, 允许命令行覆盖.

坐标系: X 东(厅宽) Y 上 Z 南(厅深). 地板在 y=pool_depth, 池底在 y=0,
这样水池是真正挖下去的, 不是一个蓝色平面.
"""

from __future__ import annotations

import numpy as np

from . import color, palette

# ---- 空间文法常量: 改这些就是在改建筑风格 ----
LENGTH_RATIO = 2.35     # 厅深 / 厅宽
HEIGHT_RATIO = 0.42     # 净高 / 厅宽
DECK_RATIO = 0.15       # 侧池岸宽 / 厅宽 (被照片实测值覆盖)
END_DECK_RATIO = 0.09   # 端池岸宽 / 厅深
POOL_DEPTH = 3          # 池深 (格)
CELL = 5                # 藻井格边长
WIN_W = 3               # 侧窗宽
PIER_W = 2              # 窗间墙垛宽
BAYS = 4                # 每侧柱数

# 室内专用候选材质: 纯色平整, 不掺石砖/圆石这类纹理花脸的方块.
HALL_BLOCKS = [
    (155, 0),   # 石英块
    (251, 0),   # 白色混凝土
    (251, 8),   # 淡灰色混凝土
    (82, 0),    # 黏土块
    (251, 3),   # 淡蓝色混凝土
    (251, 9),   # 青色混凝土
    (251, 11),  # 蓝色混凝土
    (251, 6),   # 粉红色混凝土
    (159, 0),   # 白色陶瓦
    (35, 0),    # 白色羊毛
    (35, 9),    # 青色羊毛
]

# 中性材质阶梯, 明度由高到低. 吊顶/柱/墙/梁从这里按档取,
# 而不是各自去全库找最近色 —— 那样只会选出四五种"差不多的灰",
# 出来的厅像毛坯房.
NEUTRAL_ORDER = [(35, 0), (155, 0), (251, 0), (82, 0), (251, 8)]


# ---------------------------------------------------------------- 特征提取

def _lab_of(rgb: np.ndarray, mask: np.ndarray, q: float = 50) -> np.ndarray | None:
    """取掩码内亮度第 q 百分位的颜色, 返回 Lab.

    用分位数而不是简单中值: 同一面墙上, 灯带附近和阴影角落能差 20 个 L,
    中值会被两头拽着走. 想要"这面墙本身是什么颜色", 取一个明确的亮度切片.
    """
    if mask.sum() < 12:
        return None
    px = rgb[mask]
    lum = 0.2126 * px[:, 0] + 0.7152 * px[:, 1] + 0.0722 * px[:, 2]
    target = np.percentile(lum, q)
    pick = px[np.abs(lum - target) <= 0.035]
    if pick.shape[0] < 6:
        pick = px
    return color.rgb_to_lab(np.median(pick, axis=0).reshape(1, 1, 3))[0, 0]


def _rect(h: int, w: int, x0: float, x1: float, y0: float, y1: float) -> np.ndarray:
    m = np.zeros((h, w), dtype=bool)
    m[int(y0 * h) : max(int(y1 * h), int(y0 * h) + 1),
      int(x0 * w) : max(int(x1 * w), int(x0 * w) + 1)] = True
    return m


def water_mask(rgb: np.ndarray) -> np.ndarray:
    """水池掩码: 必须是"明显的蓝", 而且要落在画面下半部.

    早期版本用 b > r*1.18, 结果把整面偏冷的灰墙也算成水 —— 这种渲染图的
    阴影本身就是冷色, 阈值一松就全图命中. 现在要求 b 压过 r 三分之一以上,
    并只认画面下 45%.
    """
    h = rgb.shape[0]
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
    m = (b > r * 1.35) & (b > g * 1.06) & (lum > 0.30)
    m[: int(h * 0.55), :] = False
    return m


def pool_span(rgb: np.ndarray) -> tuple[float, float] | None:
    """水池在画面里的横向占比 (0~1). 返回 (左边界, 右边界).

    水面是整张图里最大的一块强冷区, 横向跨度可以直接量, 比数柱子稳得多.
    透视只影响纵深方向的长度, 近端宽度基本等于真实占比.
    """
    m = water_mask(rgb)
    if m.mean() < 0.015:
        return None
    cols = m.sum(axis=0)
    thr = max(3, cols.max() * 0.30)
    idx = np.nonzero(cols >= thr)[0]
    if idx.size < 6:
        return None
    return float(idx[0] / rgb.shape[1]), float(idx[-1] / rgb.shape[1])


def extract_features(rgb: np.ndarray) -> dict:
    """从室内照提取配色与尺度. rgb 为 0~1 浮点, 建议用原图分辨率."""
    h, w = rgb.shape[:2]
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
    sat = rgb.max(-1) - rgb.min(-1)

    # 曝光归一化: 这类效果图整体压得很暗(中位亮度只有 0.5), 直接采样会把
    # 一面白墙读成中灰, 映射出来整栋建筑发霉. 把亮部抬到 0.78 再取色.
    p90 = float(np.percentile(lum, 90))
    gain = float(np.clip(0.78 / max(p90, 1e-3), 1.0, 2.1))
    # 同时补一点饱和度: 小图压缩把壁画、蓝窗都糊成了灰.
    rgb = color.boost_saturation(np.clip(rgb * gain, 0.0, 1.0), 1.55)
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
    sat = rgb.max(-1) - rgb.min(-1)

    # 吊顶: 画面上部中央, 避开中央灯槽
    m_ceil = _rect(h, w, 0.26, 0.74, 0.05, 0.30) & (lum > 0.45)
    feat: dict = {"ceiling": _lab_of(rgb, m_ceil, q=62), "gain": gain}

    # 墙: 左侧墙内侧. 只采一侧, 两侧灯位不同, 混采会把颜色拉花
    m_wall = _rect(h, w, 0.01, 0.10, 0.38, 0.62) & (sat < 0.26)
    feat["wall"] = _lab_of(rgb, m_wall, q=58)

    # 池岸: 泳池左前方的浅色窄条
    m_deck = _rect(h, w, 0.07, 0.30, 0.74, 0.83) & (sat < 0.22) & (lum > 0.45)
    feat["deck"] = _lab_of(rgb, m_deck, q=65)

    # 池底/池壁瓷砖: 水体里偏暗的那批(水面反光不算)
    mw = water_mask(rgb)
    feat["tile"] = _lab_of(rgb, mw, q=40) if mw.any() else None

    # 壁画: 端墙中央 (照片里 y 约 0.55~0.78), 取偏青绿的一半
    m_mural = _rect(h, w, 0.40, 0.59, 0.50, 0.79) & (lum > 0.28)
    if m_mural.sum() >= 20:
        green = (g - r)[m_mural]
        feat["mural"] = _lab_of(
            rgb, m_mural & ((g - r) >= np.percentile(green, 45)), q=55
        )
    else:
        feat["mural"] = None

    # 壁画里的点缀色: 同区域内暖色最强的那批 (照片上是粉/橙)
    if m_mural.sum() >= 20:
        warm = (r - b)[m_mural]
        core = m_mural & ((r - b) >= np.percentile(warm, 88))
        feat["mural_accent"] = _lab_of(rgb, core, q=50)
    else:
        feat["mural_accent"] = None

    # 条窗玻璃: 侧墙上部的深蓝窄条
    m_glass = _rect(h, w, 0.02, 0.18, 0.36, 0.48) & (b > r * 1.12) & (lum < 0.62)
    feat["glass"] = _lab_of(rgb, m_glass, q=50)

    span = pool_span(rgb)
    feat["pool_span"] = span
    # 相机就架在池子上方, 近端水面几乎顶满画面宽度, 量出来的池岸永远偏窄.
    # 照片只是参考, MC 里池岸至少要 4 格才走得动, 所以夹到 12%~20%.
    feat["deck_ratio"] = (
        float(np.clip((1.0 - (span[1] - span[0])) / 2.0, 0.12, 0.20))
        if span
        else DECK_RATIO
    )
    feat["size"] = (w, h)
    return feat


# ---------------------------------------------------------------- 材质

def pick_materials(feat: dict, dedupe: float = 10.0) -> dict:
    """把各区域特征映射成方块.

    中性面(吊顶/柱/墙/池岸/梁)走"明度阶梯": 照片只决定墙站在第几档,
    其余各面按档位上下取. 室内场景的自由匹配没有好处 —— 全库里有几十种
    浅灰, 每个面各挑一种, 最后就是一屋子差不多的灰, 完全分不出层次.
    彩色面(瓷砖/壁画/点缀)才按色相单独匹配.
    """
    lattice = [palette.get(i, m) for i, m in NEUTRAL_ORDER]
    labs_n = palette.lab_matrix(lattice)
    wall_lab = feat["wall"] if feat["wall"] is not None else np.array([64.0, 0, 0])

    # 墙落在阶梯的第几档: 取 L 最接近的一档, 但至少留一档在上面给柱和吊顶
    lw = float(wall_lab[0])
    i_wall = int(np.argmin(np.abs(labs_n[:, 0] - lw)))
    i_wall = min(i_wall, len(lattice) - 3) if len(lattice) >= 3 else i_wall
    i_wall = max(1, i_wall)

    # 色彩倾向: 照片墙偏暖就换成暖白陶瓦, 偏冷就用冷灰黏土
    wall_b = lattice[i_wall]
    if float(wall_lab[1]) > 6.0:
        wall_b = palette.get(159, 0)      # 白色陶瓦, 暖
    column_b = lattice[max(0, i_wall - 1)]
    ceiling_b = lattice[max(0, i_wall - 2)]
    beam_b = lattice[min(len(lattice) - 1, i_wall + 1)]

    cand = [palette.get(i, m) for i, m in HALL_BLOCKS]
    labs = palette.lab_matrix(cand)

    def pick(lab, bump: float = 0.0) -> palette.Block:
        if lab is None:
            return cand[0]
        target = np.array(lab, dtype=np.float64).copy()
        target[0] = float(np.clip(target[0] + bump, 4.0, 97.0))
        return cand[int(np.argmin(color.delta_e_00(labs, target)))]

    tile_b = pick(feat["tile"], bump=0.0)

    # 壁画: 小图压缩把青绿糊成了灰, 采样彩度不足时不能退回池底色 ——
    # 那样壁画和池底同色, 白墙上一块大补丁. 照片上壁画是青绿马赛克.
    mlab = feat.get("mural")
    chroma = 0.0 if mlab is None else float(np.hypot(mlab[1], mlab[2]))
    if chroma < 13.0:
        mural_b = palette.get(251, 9)
        accent_b = palette.get(251, 6)
    else:
        mural_b = pick(mlab, bump=0.0)
        alab = feat.get("mural_accent")
        achroma = 0.0 if alab is None else float(np.hypot(alab[1], alab[2]))
        accent_b = palette.get(251, 6) if achroma < 10.0 else pick(alab, bump=0.0)

    mats = {
        "ceiling": ceiling_b,
        "wall": wall_b,
        "column": column_b,
        "deck": column_b,
        "tile": tile_b,
        "mural": mural_b,
        "mural_accent": accent_b,
        "beam": beam_b,
    }
    # 固定材质: 这些不该由颜色搜索决定
    mats["water"] = palette.get(9, 0)
    mats["glass"] = palette.get(95, 11)
    mats["lamp"] = palette.get(169, 0)
    mats["light"] = palette.get(155, 0)      # 壁画高光/白框
    mats["chair"] = palette.get(35, 15)      # 池岸躺椅: 深色, 提供尺度参照
    return mats


# ---------------------------------------------------------------- 合成

def _mural_pattern(w: int, h: int, seed: int = 7) -> np.ndarray:
    """壁画图案: 0=底 1=亮 2=点缀.

    照片上是一幅水草/水波的抽象马赛克, 左右对称. 这里用确定性规则生成,
    不复制原图像素: 竖向水草 + 上升气泡 + 底部深水 + 顶部亮带.
    """
    rng = np.random.default_rng(seed)
    pat = np.zeros((h, w), dtype=np.int8)
    cx = (w - 1) / 2.0

    # 竖向水草: 从底部往上, 高度不一, 左右镜像
    n_stems = max(3, w // 7)
    for k in range(n_stems):
        sx = int(round((k + 0.5) * w / n_stems))
        if sx >= w:
            continue
        top = int(h * (0.22 + 0.05 * (k % 3)))
        stem_h = h - top
        if stem_h <= 0:
            continue
        pat[top:, sx] = 1
        if sx + 1 < w and (k + seed) % 2 == 0:
            pat[top + stem_h // 4 :, sx + 1] = 1
        if sx > 0 and (k + seed) % 3 == 0:
            pat[top + stem_h // 3 :, sx - 1] = 1

    # 上升气泡: 稀疏亮点
    n_bub = max(6, (w * h) // 26)
    for _ in range(n_bub):
        bx = int(rng.integers(0, w))
        by = int(rng.integers(h // 4, h))
        pat[by, bx] = 1

    # 底部两条深色水带, 顶部一条亮带
    pat[: max(1, h // 12), :] = 1
    pat[0, :] = 2

    # 点缀色: 成对的暖色小块, 散在中段, 太少就完全看不出来
    for _ in range(max(6, (w * h) // 22)):
        bx = int(rng.integers(0, max(1, w - 1)))
        by = int(rng.integers(int(h * 0.30), int(h * 0.85) + 1))
        pat[by, bx] = 2
        pat[by, min(w - 1, bx + 1)] = 2

    # 强制左右对称
    pat[:, w // 2 :] = pat[:, : w - w // 2][:, ::-1]
    return pat


def build(
    feat: dict,
    mats: dict,
    width: int,
    *,
    length: int = 0,
    height: int = 0,
    bays: int = BAYS,
    pool_depth: int = POOL_DEPTH,
    cell: int = CELL,
    deck_ratio: float = 0.0,
) -> tuple[np.ndarray, list, dict]:
    """重建一个可进入的室内大厅.

    返回 (vox, blocks, info). vox 为 (X, Y, Z) int16, -1 为空.
    """
    W = int(width)
    L = int(length) if length else max(W + 14, int(round(W * LENGTH_RATIO)))
    Hh = int(height) if height else max(8, int(round(W * HEIGHT_RATIO)))
    pd = max(1, int(pool_depth))
    y_floor = pd
    y_top = y_floor + Hh          # 天花平面

    order: list = []
    index: dict[tuple[int, int], int] = {}

    def bid(block) -> int:
        k = (block.id, block.meta)
        if k not in index:
            index[k] = len(order)
            order.append(block)
        return index[k]

    m_ceil = bid(mats["ceiling"])
    m_wall = bid(mats["wall"])
    m_col = bid(mats["column"])
    m_deck = bid(mats["deck"])
    m_tile = bid(mats["tile"])
    m_beam = bid(mats["beam"])
    m_water = bid(mats["water"])
    m_glass = bid(mats["glass"])
    m_lamp = bid(mats["lamp"])
    m_mural = bid(mats["mural"])
    m_acc = bid(mats["mural_accent"])
    m_light = bid(mats["light"])

    vox = np.full((W, y_top + 1, L), -1, dtype=np.int16)

    # ---------------- 水池范围 ----------------
    dr = deck_ratio if deck_ratio > 0 else float(feat.get("deck_ratio", DECK_RATIO))
    side = max(4, int(round(W * dr)))
    px0, px1 = side, W - 1 - side
    end = max(3, int(round(L * END_DECK_RATIO * 0.55)))
    pz0, pz1 = end, L - 1 - end
    if px1 - px0 < 5 or pz1 - pz0 < 7:
        raise ValueError(f"厅太小, 放不下水池 (厅 {W}x{L})")

    # ---------------- 地板与水池 ----------------
    for z in range(L):
        for x in range(W):
            vox[x, y_floor, z] = m_deck

    # 池底 + 池壁 (挖下去, 用瓷砖)
    for z in range(pz0 - 1, pz1 + 2):
        for x in range(px0 - 1, px1 + 2):
            if not (0 <= x < W and 0 <= z < L):
                continue
            inside = (px0 <= x <= px1) and (pz0 <= z <= pz1)
            vox[x, 0, z] = m_tile
            for y in range(1, y_floor):
                if inside:
                    vox[x, y, z] = m_water
                else:
                    vox[x, y, z] = m_tile
    # 水面比池岸低一格: 池区把地板那层挖空, 边缘就能看见池壁,
    # 读起来才是"挖下去的水池"而不是"一块蓝地砖".
    for z in range(pz0, pz1 + 1):
        for x in range(px0, px1 + 1):
            vox[x, y_floor, z] = -1

    # ---------------- 外墙 ----------------
    for y in range(y_floor, y_top):
        for z in range(L):
            vox[0, y, z] = m_wall
            vox[W - 1, y, z] = m_wall
        for x in range(W):
            vox[x, y, 0] = m_wall
            vox[x, y, L - 1] = m_wall

    # 入口端留门洞 (3 宽 4 高), 不然进不去
    cx = W // 2
    for y in range(y_floor, min(y_floor + 4, y_top)):
        for x in range(max(1, cx - 1), min(W - 1, cx + 1) + 1):
            vox[x, y, 0] = -1

    # ---------------- 柱廊 ----------------
    # 柱位均匀分布在两端之间; 每根柱占 Z 方向 2 格, 从墙面向内凸 1 格.
    z0, z1 = 3, L - 4
    n_col = max(2, int(bays))
    cols = sorted(
        {int(round(v)) for v in np.linspace(z0, z1, n_col)}
    )
    for cz in cols:
        for dz in range(2):
            z = cz + dz
            if not (0 <= z < L):
                continue
            for y in range(y_floor, y_top):
                vox[1, y, z] = m_col
                vox[W - 2, y, z] = m_col
        # 柱头: 顶上两行沿 Z 各外扩一格, 用梁色, 读出来是"柱帽"
        for dz in (-1, 0, 1, 2):
            z = cz + dz
            if 0 <= z < L:
                vox[1, y_top - 1, z] = m_beam
                vox[1, y_top - 2, z] = m_beam if dz in (-1, 2) else m_col
                vox[W - 2, y_top - 1, z] = m_beam
                vox[W - 2, y_top - 2, z] = vox[1, y_top - 2, z]

    # ---------------- 侧墙开窗 ----------------
    # 柱间是开间; 每个开间里排若干小窗, 上部再加一条通长条窗.
    zones: list[tuple[int, int]] = []
    prev = 2
    for cz in cols:
        if cz - 1 > prev:
            zones.append((prev, cz - 1))
        prev = cz + 2
    if L - 3 > prev:
        zones.append((prev, L - 3))

    y_low0 = y_floor + 1
    y_band = y_top - 7                       # 横向灯带 (照片里柱上有一条通长光带)
    y_low1 = y_band - 1
    y_hi0 = y_band + 1
    y_hi1 = y_band + 2                        # 上部窄条窗

    # 通长灯带: 先铺满, 柱子会盖在它前面
    for y in range(y_band, y_band + 1):
        for z in range(2, L - 2):
            vox[0, y, z] = m_lamp
            vox[W - 1, y, z] = m_lamp

    for a, b in zones:
        span = b - a + 1
        if span < 4:
            continue
        # 下部落地玻璃: 每个开间排 1~2 整扇, 扇间只留一根竖框.
        # 照片上是连续玻璃幕墙, 不是一排小窗, 所以扇宽跟着开间走.
        if y_low1 >= y_low0:
            k = 1 if span < 10 else 2
            gap = 1
            fw = max(2, (span - gap * (k + 1)) // k)
            wz = a + gap
            for _ in range(k):
                for y in range(y_low0, y_low1 + 1):
                    for z in range(wz, min(wz + fw, b + 1)):
                        vox[0, y, z] = m_glass
                        vox[W - 1, y, z] = m_glass
                wz += fw + gap
        # 上部窄条窗: 贯通整个开间
        for y in range(y_hi0, y_hi1 + 1):
            for z in range(a, b + 1):
                vox[0, y, z] = m_glass
                vox[W - 1, y, z] = m_glass

    # ---------------- 端墙壁画 ----------------
    mw = max(5, int(round(W * 0.36)))
    mh = max(4, int(round(Hh * 0.62)))
    mx0 = cx - mw // 2
    my0 = y_floor + max(2, int(round(Hh * 0.34)))
    pat = _mural_pattern(mw, mh)
    for j in range(mh):
        for i in range(mw):
            x = mx0 + i
            y = my0 + (mh - 1 - j)          # 图案自下而上画
            if not (0 <= x < W and 0 <= y < y_top - 1):
                continue
            p = int(pat[j, i])
            vox[x, y, L - 1] = (m_mural, m_light, m_acc)[p]
    # 壁画四周白框
    for x in range(max(0, mx0 - 1), min(W, mx0 + mw + 1)):
        for y in (my0 - 1, my0 + mh):
            if 0 <= y < y_top:
                vox[x, y, L - 1] = m_light
    for y in range(max(0, my0 - 1), min(y_top, my0 + mh + 1)):
        for x in (mx0 - 1, mx0 + mw):
            if 0 <= x < W:
                vox[x, y, L - 1] = m_light

    # 壁画两侧的小方灯窗
    for sx in (mx0 - max(4, mw // 2), mx0 + mw + max(3, mw // 2)):
        for y in range(my0 + mh // 2, my0 + mh // 2 + 2):
            for x in range(sx, sx + 2):
                if 0 <= x < W and 0 <= y < y_top:
                    vox[x, y, L - 1] = m_glass

    # ---------------- 藻井吊顶 ----------------
    for z in range(L):
        for x in range(W):
            rim = x in (0, W - 1) or z in (0, L - 1)
            edge = (x % cell == 0) or (z % cell == 0)
            if rim:
                vox[x, y_top, z] = m_wall
            else:
                vox[x, y_top, z] = m_beam if edge else m_ceil
    # 中央纵向灯槽
    for z in range(1, L - 1):
        for x in (cx - 1, cx):
            if 0 <= x < W:
                vox[x, y_top, z] = m_lamp
    # 沿柱列的筒灯
    for cz in cols:
        for x in (max(2, W // 4), min(W - 3, W - W // 4)):
            vox[x, y_top, cz] = m_lamp

    # ---------------- 池岸躺椅 ----------------
    # 池岸是 5~8 格宽的条带, 什么都不放会显得像没做完. 两侧各摆几张躺椅,
    # 深色 2x1, 既提供尺度参照, 也把"泳池"这个功能说清楚.
    m_chair = bid(mats["chair"])
    chairs = cols[1:-1] if len(cols) > 2 else cols
    for side_x in (2, W - 4):
        if px0 <= side_x <= px1:      # 池岸收得太窄, 没地方摆
            continue
        for cz in chairs:
            for dz in range(3):
                z = cz + dz
                if not (0 <= z < L - 1):
                    continue
                for dx in range(2):
                    vox[side_x + dx, y_floor + 1, z] = m_chair

    info = {
        "W": W, "L": L, "H": Hh,
        "pool": [px1 - px0 + 1, pz1 - pz0 + 1, pd],
        "bays": len(cols),
        "mural": [mw, mh],
        "y_floor": y_floor,
        "y_top": y_top,
    }
    return vox, order, info
