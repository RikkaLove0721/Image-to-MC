"""照片分析：把一张建筑照压成一份几十行的 brief，喂给 AI 或 MCP。

为什么不让 AI 直接看图：

    图片进上下文就是几百上千 token，而且 AI 从像素里估"这栋楼几层、窗距
    多少"并不比本地算得准。本地把可量化的部分算好（体块分段、窗阵节奏、
    主色），AI 只负责"设计决策"，token 立刻降一个量级。

brief 里所有数值都是方块格数，AI 可以直接拿去写 spec。
"""

from __future__ import annotations

import numpy as np

from . import geometry, massing, palette, scene, semantic
from .color import rgb_to_lab
from .quantize import kmeans_lab


def _nearest_block(rgb_mean: np.ndarray, blocks: list[palette.Block]) -> palette.Block:
    from .color import delta_e_00

    ref = np.array([b.rgb for b in blocks], dtype=np.float64) / 255.0
    d = delta_e_00(rgb_to_lab(ref), rgb_to_lab(rgb_mean[None, :]))
    return blocks[int(np.argmin(d))]


def _window_stats(win: np.ndarray) -> dict:
    """从窗掩码里读节奏：排数、每排几扇、窗宽、横向节距、竖向节距。"""
    h, w = win.shape
    rows: list[int] = []
    for y in range(h):
        if win[y].sum() > 0:
            rows.append(y)
    bands: list[list[int]] = []
    for y in rows:
        if bands and y - bands[-1][-1] <= 1:
            bands[-1].append(y)
        else:
            bands.append([y])
    widths: list[int] = []
    cols_max = 0
    for b in bands:
        band = win[b[0] : b[-1] + 1].any(axis=0)
        segs = []
        x = 0
        while x < w:
            if band[x]:
                x0 = x
                while x < w and band[x]:
                    x += 1
                segs.append((x0, x))
            else:
                x += 1
        widths += [b1 - b0 for b0, b1 in segs]
        cols_max = max(cols_max, len(segs))
    if not widths:
        return {"rows": 0, "cols": 0, "bw": 0, "bh": 0, "pitch_u": 0, "pitch_v": 0}
    bh = int(np.median([len(b) for b in bands])) if bands else 0
    pv = int(np.median(np.diff([b[0] for b in bands]))) if len(bands) > 1 else 0
    return {
        "rows": len(bands),
        "cols": cols_max,
        "bw": int(np.median(widths)),
        "bh": int(bh),
        "pitch_u": 0,
        "pitch_v": pv,
    }


def _outline(cell: np.ndarray) -> dict:
    """从亮度估计"人工建筑"的立面轮廓，给出宽度、高度、宽高比。

    为什么不用分割掩码的包围盒：

        斜俯视照片里，后排的高翼、前景的露台、草坪上的白车都会把包围盒撑大。
        一张长横的宫殿照片会被算成 1.33 的宽高比（实测），于是构建端老老实实
        把模型拉成一个方盒子 —— 比例是"像不像"的第一要素，这一步错全盘皆错。

    这里改成看**列**：每列从上往下第一个"亮"的位置就是这列的顶。
    取所有列的顶线中位数当主立面檐口，脚下取底边中位数当地面线。
    后排的高翼只占少数几列，被中位数直接排除，不会把楼"变高"。

    只依赖亮度，不依赖颜色 —— 很多建筑照片其实是近灰度的（测试这张的
    平均彩度只有 5%），任何按"绿=树、蓝=天"写的规则在这种图上全部失效。
    """
    h, w = cell.shape[:2]
    lum = 0.2126 * cell[..., 0] + 0.7152 * cell[..., 1] + 0.0722 * cell[..., 2]
    thr = float(np.clip(np.percentile(lum, 60), 0.30, 0.55))
    mask = scene.fill_holes(lum > thr)
    comps = sorted(scene.components(mask), key=len, reverse=True)
    if not comps:
        return {"width": w, "height": h, "aspect": round(w / max(h, 1), 2), "confident": False}
    body = np.zeros_like(mask)
    for y, x in comps[0]:
        body[y, x] = True

    cols, tops, bots = [], [], []
    for x in range(w):
        ys = np.nonzero(body[:, x])[0]
        if len(ys) >= 3:
            cols.append(x)
            tops.append(int(ys[0]))
            bots.append(int(ys[-1]))
    if not cols:
        return {"width": w, "height": h, "aspect": round(w / max(h, 1), 2), "confident": False}

    ground = int(np.median(bots))
    roof = int(np.median(tops))
    fh = max(1, ground - roof + 1)
    # 只保留"高度接近主立面"的列：露台、独立小楼、前景的车都会被排除
    kept = [x for x, t in zip(cols, tops) if 0.55 * fh <= (ground - t) <= 1.6 * fh]
    if len(kept) < max(4, 0.25 * len(cols)):
        kept = cols
    fw = max(1, max(kept) - min(kept) + 1)
    return {
        "width": int(fw),
        "height": int(fh),
        "aspect": round(fw / fh, 2),
        # 列数太少或者高宽比异常时标记不可信，调用方只当参考
        "confident": len(kept) >= 6 and 0.6 <= fw / fh <= 12,
        "threshold": round(thr, 2),
    }


def _col_tops(mask: np.ndarray) -> np.ndarray:
    """每列最上面那个建筑像素的行号（没有就是 -1）。"""
    tops = np.full(mask.shape[1], -1, dtype=np.int32)
    for x in range(mask.shape[1]):
        col = np.where(mask[:, x])[0]
        if col.size:
            tops[x] = int(col.min())
    return tops


def _rhythm(lum: np.ndarray, y0: int, y1: int) -> dict:
    """立面的"开间节奏"：相邻两根竖梃/窗框之间的宽度（像素）。

    商业立面（玻璃幕墙、柱廊、连排窗）真正的结构单位是**开间**，不是像素。
    有了 pitch 才能回答"照片左边被裁掉了几个开间"，否则补全只能靠猜。
    做法：取立面带的逐列亮度 → 一阶差分得到"竖线信号" → 自相关找重复周期。
    """
    if y1 - y0 < 4 or lum.shape[1] < 16:
        return {"pitch": 0, "strength": 0.0, "lines": 0}
    band = lum[y0:max(y0 + 4, y1)]
    prof = band.mean(axis=0)
    sig = np.abs(np.diff(prof))
    sig = sig - sig.mean()
    if float(np.abs(sig).sum()) < 1e-6:
        return {"pitch": 0, "strength": 0.0, "lines": 0}
    ac = np.correlate(sig, sig, mode="full")[len(sig) - 1:]
    ac = ac / max(float(ac[0]), 1e-9)
    w = lum.shape[1]
    lo, hi = max(3, int(w * 0.02)), min(len(ac) - 1, max(6, int(w * 0.30)))
    if hi <= lo:
        return {"pitch": 0, "strength": 0.0, "lines": 0}
    seg = ac[lo:hi]
    k = int(np.argmax(seg)) + lo
    strength = float(ac[k])
    # 峰不够高 = 没有稳定节奏（平墙、自由立面），不要硬报一个数
    if strength < 0.25:
        return {"pitch": 0, "strength": round(strength, 2), "lines": 0}
    thr = float(sig.mean() + 1.2 * sig.std())
    return {
        "pitch": int(k),
        "strength": round(strength, 2),
        "lines": int(np.count_nonzero(sig > thr)),
    }


def _symmetry(lum: np.ndarray, x0: int, x1: int, y0: int, y1: int) -> dict:
    """估立面的左右对称轴（0~1 的相对位置）。

    商业立面几乎都对称：入口在轴线上，招牌偏一侧。照片被裁掉一边时，
    知道轴线在哪就能算出"整栋应该多宽"。用逐列亮度轮廓比，不用像素比 ——
    前者不受宽度、轻微透视和局部反光影响。
    """
    if x1 - x0 < 12 or y1 - y0 < 4:
        return {"axis": 0.5, "score": 0.0, "confident": False}
    prof = lum[y0:y1, x0:x1].mean(axis=0)
    prof = prof - prof.mean()
    n = prof.size
    best, best_d, base = 0.5, 1e18, []
    # 半窗太短一定"更像"（两条玻璃带互相都差不多），所以限制半窗至少 30%：
    # 否则轴线会永远落在边缘，补全量被算成 1.6 倍
    lo_c, hi_c = int(n * 0.30), int(n * 0.70)
    for c in range(lo_c, hi_c):
        half = min(c, n - 1 - c)
        if half < n * 0.28:
            continue
        left = prof[c - half: c][::-1]
        right = prof[c + 1: c + 1 + half]
        m = min(left.size, right.size)
        d = float(np.abs(left[:m] - right[:m]).mean())
        base.append(d)
        if d < best_d:
            best_d, best = d, (c + 0.5) / n
    if not base:
        return {"axis": 0.5, "score": 0.0, "confident": False}
    med = float(np.median(base))
    score = 1.0 - best_d / max(med, 1e-6)
    return {
        "axis": round(float(best), 3),
        "score": round(max(0.0, min(1.0, score)), 2),
        "confident": bool(score > 0.25),
    }


def _edge_info(lum: np.ndarray, mask: np.ndarray) -> dict:
    """哪条边上的建筑被裁掉了，以及补全后应有宽度。

    判"被裁"不能只看掩码有没有碰到画面边 —— 背景里还有别的楼、还有门前地面，
    它们也会碰到边。真正的判据是**连续性**：如果贴边那一小条和它内侧一小条
    的**顶线高度**几乎一样、颜色也几乎一样，那它就是同一堵墙被画框切断了；
    如果顶线差好几格，那是后面的另一栋楼，不算裁切。
    """
    h, w = mask.shape
    rows = np.where(mask.mean(axis=1) > 0.30)[0]
    if rows.size < 3:
        return {"cut": [], "bays_visible": 0, "bays_full": 0, "ratio": 1.0, "pitch": 0}
    scale = lum.shape[1] / max(w, 1)
    r0, r1 = int(rows.min()), int(rows.max())
    # 立面主体只取上 80%：最下面那几行是门前地面，颜色和墙不一样
    y1 = r0 + max(3, int((r1 - r0) * 0.8))
    band = mask[r0:y1]
    tops = _col_tops(band)
    rgb_lum = lum[int(r0 * scale): int(y1 * scale)]

    def strip(x: slice) -> tuple[float, float]:
        t = tops[x]
        t = t[t >= 0]
        if t.size == 0:
            return -1.0, -1.0
        return float(np.median(t)), float(band[:, x].mean())

    cut: list[str] = []
    for side, edge_sl, in_sl in (
        ("left", slice(0, 2), slice(2, 6)),
        ("right", slice(w - 2, w), slice(w - 6, w - 2)),
    ):
        t_e, c_e = strip(edge_sl)
        t_i, c_i = strip(in_sl)
        if t_e < 0 or c_e < 0.45:
            continue                      # 边上几乎没东西，不算裁切
        if t_i >= 0 and abs(t_e - t_i) <= 1 and abs(c_e - c_i) <= 0.22:
            cut.append(side)

    cols = np.where(band.mean(axis=0) > 0.15)[0]
    x0 = int(cols.min()) if cols.size else 0
    x1 = int(cols.max()) if cols.size else w - 1
    span = max(1, x1 - x0 + 1)
    rh = _rhythm(rgb_lum, 0, rgb_lum.shape[0])
    pitch_cell = int(round(int(rh.get("pitch") or 0) / max(scale, 1e-6)))
    if pitch_cell < 3:
        pitch_cell = 0
    sym = _symmetry(lum, int(x0 * scale), int((x1 + 1) * scale),
                    int(r0 * scale), int(y1 * scale))
    symmetric = bool(sym["confident"] and sym["score"] > 0.32)
    # 补全量：对称的建筑，完整宽度 = 2 × 较长的半边（轴线在可见段里的位置）；
    # 判不出对称就按经验补 —— 一侧被裁补 1/4，两侧各裁补一半。
    ratio = 1.0
    if cut:
        if symmetric:
            half = max(sym["axis"], 1.0 - sym["axis"])
            ratio = round(min(1.8, max(1.0, 2.0 * half)), 2)
        else:
            ratio = 1.5 if len(cut) == 2 else 1.25
    bays_vis = int(round(span / pitch_cell)) if pitch_cell else 0
    bays_full = int(round(bays_vis * ratio)) if bays_vis else 0
    return {
        "cut": cut,
        "bays_visible": bays_vis,
        "bays_full": bays_full,
        "ratio": ratio,
        "pitch": pitch_cell,
        "rhythm_strength": float(rh.get("strength") or 0),
        "symmetry_axis": sym["axis"],
        "symmetric": symmetric,
    }


def analyze(path: str, width: int = 40) -> dict:
    """返回 brief（纯 JSON 可序列化）。"""
    raw = geometry.load_image(path)
    raw, _border = geometry.trim_border(raw)
    raw = geometry.median_filter(raw, 9)
    rgb = geometry.downsample(raw, min(width, raw.shape[1]))
    h, w = rgb.shape[:2]
    detail = geometry.downsample(raw, min(max(w * 4, 160), raw.shape[1]))
    sky_hi = geometry.sky_mask(detail)
    sky_lo = geometry.sky_mask(rgb)

    sc = scene.segment(detail, (h, w), sky_hi)
    building = sc["building"]
    if building.sum() < 0.1 * h * w:
        building = ~sky_lo  # 分割失败就退回"非天空"

    # 体块分段
    tops = massing.column_tops(building, None)
    panels = massing.split_panels(tops, None, building, step=2.0, min_frac=0.12)
    m_info = []
    for p in panels:
        m_info.append(
            {
                "x": p["x0"],
                "w": p["x1"] - p["x0"],
                "top_row": p["y_top"],
                "note": "低一截的侧翼",
            }
        )

    # 主色：取"建筑区域里最亮的那批暖色像素"的中值。
    # 不用聚类 —— 别墅那张图背景全是草坪和树，聚类出来的最大簇是树叶；
    # 而墙面的受光面永远是同一种暖色，取高亮分位的中值最稳。
    lum = 0.2126 * rgb[..., 0] + 0.7152 * rgb[..., 1] + 0.0722 * rgb[..., 2]
    warm = (rgb[..., 0] - rgb[..., 2]) / (rgb[..., 0] + rgb[..., 2] + 1e-6)
    green = rgb[..., 1] > np.maximum(rgb[..., 0], rgb[..., 2]) + 0.03
    region = building & ~sky_lo
    if region.sum() < 40:
        region = ~sky_lo

    blocks = palette.dedupe(10.0)

    lit = region & (warm > 0.0) & ~green
    if lit.sum() < 25:
        lit = region
    # 取 80 分位而不是 55：55 分位落在"整片立面的平均亮度"上，而 40 格宽的
    # 采样格会把暗窗、阴影、路面一起平均进去 —— 米黄色石灰岩量出来是 (157,148,143)
    # 这种中灰，最近色就成了 concrete:8（灰蓝混凝土），整栋楼变成灰盒子。
    # 受光面在 80 分位附近，白墙才会被判成 quartz。
    hi_lum = np.percentile(lum[lit], 80)
    wall_px = rgb[lit & (lum >= hi_lum)]
    if wall_px.shape[0] < 10:
        wall_px = rgb[lit]
    wall_rgb_ref = np.median(wall_px, axis=0)
    wall = _nearest_block(wall_rgb_ref, blocks)

    # 白墙就用"真白"，别用白色陶瓦。
    # 照片里的白墙在夕照下是 (200,190,180) 这种微暖白，最近色算出来是
    # 白色陶瓦 #D1B1A1 —— 那是偏粉的米色砖纹，整栋楼看上去是浅棕色，
    # 跟"白色石材"的印象差很远。明度高、饱和度低的时候直接给石英块。
    _wl = 0.2126 * wall_rgb_ref[0] + 0.7152 * wall_rgb_ref[1] + 0.0722 * wall_rgb_ref[2]
    _ws = float(wall_rgb_ref.max() - wall_rgb_ref.min()) / max(float(wall_rgb_ref.max()), 1e-6)
    if _wl > 0.65 and _ws < 0.22:
        wall = palette.find("quartz_block", 0)

    # 线脚与屋顶：直接用墙色压暗，不另外采样。
    # 照片里最暗的像素多半是树影、窗洞、车底 —— 采样出来的"线脚"会变成
    # 奇怪的青绿色（实测别墅那张就取到了暗海晶石）。同色压暗永远不会跑偏。
    wall_rgb = np.median(wall_px, axis=0)
    # 线脚 = 墙色压一档，不是"另选一个深色"。
    # 0.45 太狠：白墙会落到中灰，而线脚占立面三成以上，整栋楼就成了灰楼
    # （用户反馈"原图白色居多，模型却青/灰居多"）。石材线脚只比墙面暗 15~25%。
    trim = _nearest_block(np.clip(wall_rgb * 0.74, 0, 1), blocks)
    roof = _nearest_block(np.clip(wall_rgb * 0.42, 0, 1), blocks)
    if trim is wall:
        trim = palette.get(251, 7)
    # 屋顶要再暗一档：和线脚同色的话，远看屋顶和檐口糊成一条
    if roof is wall or roof is trim:
        roof = palette.get(159, 15)

    pal = {
        "wall": f"{wall.name_en}:{wall.meta}",
        "trim": f"{trim.name_en}:{trim.meta}",
        "roof": f"{roof.name_en}:{roof.meta}",
        "glass": "stained_glass:11",
        "accent": "dark_oak_planks",
    }

    # 窗阵
    hi = geometry.downsample(raw, max(w * 2, 64))
    win, winfo = semantic.window_grid(hi, (h, w), geometry.sky_mask(hi))
    wstats = _window_stats(win) if win.any() else {"rows": 0, "cols": 0, "bw": 0, "bh": 0}
    if win.any():
        mean = rgb[win].mean(axis=0)
        glass = _nearest_block(mean, [b for b in palette.BLOCKS if b.semantic and b.id in (20, 95)])
        pal["glass"] = f"{glass.name_en}:{glass.meta}"

    # 古典立面（栏杆/线脚/柱式）很容易让窗检测失效，标一个可信度让 AI 自己排
    detected = wstats.get("cols", 0) >= 2 and wstats.get("rows", 0) >= 1
    wstats["detected"] = bool(detected)
    # 层数没有别的来源：窗带行数往往少算一层（底层是门/玻璃），+1 兜一下
    rows = int(wstats.get("rows", 0))
    wstats["floors_hint"] = max(2, min(8, (rows + 1) if rows else 3))

    notes: list[str] = []
    if not detected:
        notes.append("窗阵检测不可信：按层数与墙宽自行排窗（每 5~6 格一扇）")
    if sc["info"].get("canopy_cells", 0) > 0.06 * h * w:
        notes.append("画面里植被占比高，墙色按建筑像素取，不含绿植")
    if winfo.get("windows", 0) == 0:
        notes.append("没检测到窗，spec 里需要自己排窗")
    if not panels:
        notes.append("屋顶线没有台阶，按单一体块处理；需要多体块时自行拆分")

    bw_cells = int(np.count_nonzero(building.any(axis=0)))
    bh_cells = int(np.count_nonzero(building.any(axis=1)))
    # 高宽比是"像不像"的第一要素：竖向的楼被铺宽就会变成一排平房。
    # 用逐列轮廓算，不用分割掩码的包围盒（后者会被后排高翼和前景露台撑大）。
    outline = _outline(rgb)
    aspect = outline["aspect"]

    # ---- 裁切与补全 ----
    # 用户常有这种照片：店面在画面里只露出一半，左边还在画面外。
    # 模型照抄可见部分，得到的永远只是"半栋楼"。所以先量两件事：
    # 哪条边被裁了、立面自己的开间节奏是多少，据此估出完整宽度比。
    # 节奏与对称要在**高分辨率亮度**上算：40 格宽时一根竖梃还不到 2 格，
    # 采样不足会让自相关直接归零。
    body = building & ~sky_lo
    if body.sum() < 40:
        body = building
    lum_hi = 0.299 * detail[..., 0] + 0.587 * detail[..., 1] + 0.114 * detail[..., 2]
    edge = _edge_info(lum_hi, body)
    if edge["cut"]:
        notes.append(
            "照片的"
            + "、".join("左" if c == "left" else "右" for c in edge["cut"])
            + "边缘建筑被裁掉了：模型要**补全成完整一栋**，不要只建可见的这一段"
        )

    return {
        "image": {"w": int(raw.shape[1]), "h": int(raw.shape[0]), "grid": [w, h]},
        "building": {
            "width": outline["width"],
            "height": outline["height"],
            "aspect": aspect,
            "aspect_confident": bool(outline["confident"]),
            "bbox_width": bw_cells,
            "bbox_height": bh_cells,
            "depth_hint": int(min(24, max(4, round(outline["width"] * 0.8)))),
        },
        "massing": {"panels": m_info, "count": len(m_info) + 1},
        "completion": edge,
        "windows": wstats,
        "palette": pal or {"wall": "sandstone", "trim": "concrete:7"},
        "scene": {
            "canopy": round(float(sc["canopy"].mean()), 2),
            "lawn": round(float(sc["lawn"].mean()), 2),
            "sky": round(float(sky_lo.mean()), 2),
        },
        "notes": notes,
    }
