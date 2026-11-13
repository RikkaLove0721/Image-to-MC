"""模型体检：把"像不像"翻译成 AI 能读的数字。

为什么需要这一层：

    照片 → 蓝图 → 方块 这条链只有一次机会。AI 写完蓝图就锁死，本地构建器
    只会照章办事，谁也不知道结果到底像不像 —— 于是"大宫殿做出来是一栋大房子"
    这种错误要等到人肉看图才发现，而且下一轮还是同一套盲猜。

    体检层把结果和照片重新对一遍，输出**可执行**的偏差：比例差多少、
    屋顶体量够不够、窗铺得够不够密、颜色偏了几档、哪些构件缺。
    AI 拿到这几行数字，就能只改该改的地方，而不是重写整份蓝图。

    这就是"AI 跑蓝图"：它不吐方块，它读指标、改蓝图、再验一遍。

打分只用于排序和止损，不追求绝对准确 —— 真正有用的是 issues 里的
具体条目和 patch 里的具体数字。
"""

from __future__ import annotations

import numpy as np

from .knowledge import is_modern_type
from .spec import Spec


def _extent(spec: Spec) -> tuple[int, int, int]:
    """蓝图真实体量（含屋顶高度，不含 size 预留的外扩边）。

    屋顶必须算进总高：否则 60×8+5 格孟莎的"宽高比"会被算成 7.5，
    而实际交付的模型是 61×13 —— 体检报告和用户看到的东西必须是同一个数。
    """
    if not spec.volumes:
        return (1, 1, 1)
    w = max(v.x1 for v in spec.volumes) + 1
    d = max(v.z1 for v in spec.volumes) + 1
    h = max(v.y1 for v in spec.volumes) + 1
    roof_kind = str((spec.roof or {}).get("type", "flat"))
    roof_h = int((spec.roof or {}).get("height", 0) or 0)
    if roof_kind != "flat" and roof_h > 0:
        h += roof_h
    return (w, h, d)


def _exposed(vox: np.ndarray, mask: np.ndarray) -> int:
    """mask 里有多少格是露天的（六邻有空格）。"""
    if not mask.any():
        return 0
    solid = np.pad(vox >= 0, 1, constant_values=False)
    inner = solid.copy()
    for ax in (0, 1, 2):
        for sh in (1, -1):
            inner &= np.roll(solid, sh, axis=ax)
    # 去掉 padding 带来的假"实心"
    inner = inner[1:-1, 1:-1, 1:-1]
    return int((mask & ~inner).sum())


def _ship_dims(vox: np.ndarray) -> dict:
    """量一艘已经建出来的船：船长、船宽、船艏收窄比、最高点。

    舰艏用**水线附近（最下 3 层）的宽度**来量，不用整个模型的包围盒：
    上层建筑、桅杆会让上部的"宽度"失真，而船头是不是尖的，
    看的是船体本身。
    """
    mask = vox >= 0
    if not mask.any():
        return {"length": 0, "beam": 0, "tip": 0.0, "top": 0}
    zs = np.nonzero(mask.any(axis=(0, 1)))[0]
    xs = np.nonzero(mask.any(axis=(1, 2)))[0]
    z0, z1 = int(zs[0]), int(zs[-1])
    length = z1 - z0 + 1
    beam = int(xs[-1]) - int(xs[0]) + 1
    hull = mask[:, : min(3, mask.shape[1]), :]

    def hull_beam_at(z: int) -> int:
        sl = hull[:, :, z].any(axis=1)
        idx = np.nonzero(sl)[0]
        return 0 if idx.size == 0 else int(idx[-1] - idx[0] + 1)

    widest = max((hull_beam_at(z) for z in range(z0, z1 + 1)), default=0)
    # 船头取最后 6% 处（端头一格常常就是 1 格宽的尖）
    tip = hull_beam_at(max(z0, z1 - max(1, int(length * 0.06))))
    ratio = tip / widest if widest else 1.0
    return {"length": length, "beam": beam, "tip": round(ratio, 2), "widest": widest}


def _critique_ship(brief: dict, spec: Spec, vox: np.ndarray, blocks) -> dict:
    """舰船体检：只看"像不像船"，完全不套建筑的开间/层高/勒脚那套。

    为什么必须分开：建筑体检的逻辑是"要有屋顶、要有勒脚、窗要铺满立面"，
    这三条对船全是错的 —— 船没有屋顶、没有勒脚、立面几乎不开窗。
    拿建筑标准给船打分，AI 会认认真真地把船改造成一栋楼。
    """
    issues: list[dict] = []
    patches: list[dict] = []
    score = 100.0

    W, H, D = _extent(spec)
    dims = _ship_dims(vox)
    L = dims["length"] or D
    beam = dims["beam"] or W

    def add(key: str, level: str, msg: str, fix: str, cost: float, patch: dict | None = None):
        nonlocal score
        issues.append({"key": key, "level": level, "issue": msg, "fix": fix})
        if patch:
            patches.append(patch)
        score -= cost

    # ---- 1. 长宽比：船的第一识别特征 ----
    ratio = L / max(beam, 1)
    if ratio < 4.5:
        add(
            "ship.proportion",
            "high",
            f"船长 {L} / 船宽 {beam} = {ratio:.1f}：太粗短，像驳船或浮台",
            "把主船体的 d（船长）拉长到船宽的 6~10 倍；不要靠加宽甲板来补",
            26,
            {"op": "ship_elongate", "want_ratio": 8},
        )
    elif ratio > 14:
        add(
            "ship.proportion",
            "mid",
            f"长宽比 {ratio:.1f}：细长得像一根杆子",
            "加宽船体（w 提到船长的 1/9 左右）",
            6,
        )

    # ---- 2. 尖艏：这张图的核心特征 ----
    if ratio >= 4.5 and dims["tip"] > 0.5:
        add(
            "ship.bow",
            "high",
            f"舰艏没有收尖（艏端宽度是船体的 {dims['tip']:.0%}）：现在是一艘平头船",
            '给主船体加 taper：{"axis":"z","end":"max","to":2,"len":船长的 1/4~1/3}；'
            "甲板要带同样的 taper，否则船头会露出一截没有甲板的船体",
            30,
            {"op": "ship_taper_bow", "to": 2},
        )
    elif dims["tip"] <= 0.3:
        pass  # 合格
    else:
        add(
            "ship.bow",
            "mid",
            f"舰艏收得不够（艏端宽度 {dims['tip']:.0%}）：看得出收窄，但还不够尖",
            "taper.len 加长到船长的 1/3，taper.to 收到 1~2",
            8,
        )

    # ---- 3. 朝向：长轴必须在 Z 上，否则正面看到的是船舷 ----
    if W > D * 0.9:
        add(
            "ship.axis",
            "high",
            f"模型是 {W} 宽 × {D} 深：船长写到了 X 轴上，正面视图看到的是船舷",
            "把船长写进 d（Z 方向），舰艏放在 z 最大端，这样正面才是船头迎面",
            24,
        )

    # ---- 4. 上层建筑：有没有"舰桥"这个高度层次 ----
    tops = sorted({v.y1 for v in spec.volumes}, reverse=True)
    if len(tops) < 3:
        add(
            "ship.superstructure",
            "mid",
            f"只有 {len(tops)} 个高度层次：船是平的，没有舰桥/桅杆的高差",
            "上层建筑做 3~4 段，每段比下一段窄 1~2 格、高 3~5 格，桅杆再往上 6~10 格",
            14,
        )
    else:
        span = tops[0] - min(v.y for v in spec.volumes)
        if span > 0 and (tops[0] - tops[1]) < 3:
            add(
                "ship.mast",
                "mid",
                "最高层比舰桥只高不到 3 格：缺一根立得起来的桅杆/雷达",
                "在舰桥顶加一个 w/d 1~2、h 6~10 的细体块当桅杆",
                8,
            )

    # ---- 5. 栏杆 / 旗杆：非整方块，看实际建出来的数量 ----
    rails = sum(
        int((vox == i).sum())
        for i, b in enumerate(blocks)
        if b.id in (85, 101, 113, 188, 189, 190, 191, 192, 139)
    )
    kinds = {str(d.get("type")) for d in spec.details}
    if rails < max(8, L // 4):
        add(
            "ship.railing",
            "high",
            f"甲板上只有 {rails} 格栏杆：光秃秃的甲板边缘会被读成楼顶",
            '给甲板和上层建筑各加 {"type":"railing","mat":"iron_bars","face":"all"}',
            14,
            {"op": "add_railing"},
        )
    if "flagpole" not in kinds:
        add(
            "ship.flag",
            "mid",
            "没有旗杆：舰尾或桅顶立一根，识别度很高",
            '加 {"type":"flagpole","volume":甲板体块,"h":10,"side":"east","flag":"wool:14"}',
            7,
            {"op": "add_flagpole"},
        )

    # ---- 6. 建筑构件混进来了没有 ----
    bad = kinds & {"plinth", "cornice", "door", "balcony", "arcade", "portico", "pediment", "dormer"}
    if bad:
        add(
            "ship.building_parts",
            "high",
            f"蓝图里有建筑构件（{'、'.join(sorted(bad))}）：装在船上会变回写字楼",
            "删掉这些细节，改用 railing / flagpole；屋顶写 flat 且 parapet=0",
            12,
        )

    # ---- 7. 窗：军舰立面几乎不开窗 ----
    # 玻璃板（102）也算玻璃：0.31 之后窗和幕墙默认都转成板，
    # 只认 20/95 的话舰体上的玻璃条一格都量不到。
    glass_idx = {i for i, b in enumerate(blocks) if "glass" in b.name_en}
    gmask = np.isin(vox, list(glass_idx)) if glass_idx else np.zeros_like(vox, dtype=bool)
    win_cells = int(gmask.sum())
    solid = int((vox >= 0).sum())
    share = win_cells / max(1, solid)
    # 船体（最下面几层）上开窗是最常见的错：照片里那些"亮点"其实是设备反光，
    # 不是舷窗。军舰侧面基本是整片钢板。
    hull_glass = int(gmask[:, : min(6, vox.shape[1]), :].sum())
    if hull_glass > 0:
        add(
            "ship.hull_windows",
            "mid",
            f"船体侧面（最下 6 层）开了 {hull_glass} 格窗：军舰船身是钢板，没有舷窗",
            "删掉主船体上的 windows，玻璃只留在舰桥/上层建筑",
            9,
        )
    if share > 0.10:
        add(
            "ship.windows",
            "mid",
            f"玻璃占 {share:.0%}：军舰外立面几乎全是钢板，这个开窗量像商场",
            "只在舰桥和上层建筑留 1~2 条横向窗带，删掉船体上的窗",
            10,
        )

    # ---- 8. 配色：和照片的主色调别差太远 ----
    pal = brief.get("palette") or {}
    want_wall = str(pal.get("wall") or "")
    have_wall = spec.palette.get("wall")
    if want_wall and have_wall is not None:
        from .color import delta_e_00, rgb_to_lab
        from .spec import parse_material

        try:
            target = parse_material(want_wall)
            d = float(
                delta_e_00(
                    rgb_to_lab(np.array([target.rgb], dtype=np.float64) / 255.0),
                    rgb_to_lab(np.array([have_wall.rgb], dtype=np.float64) / 255.0),
                )[0]
            )
        except Exception:
            d = 0.0
        if d > 14:
            add(
                "ship.palette",
                "mid",
                f"船体色和照片差了 ΔE {d:.0f}：军舰是低饱和的灰，别用暖色",
                f"palette.wall 换成 {want_wall}（浅灰石英 / 淡灰混凝土）",
                8,
                {"op": "set", "path": "palette.wall", "to": want_wall},
            )

    # ---- 9. 观赏性细节：甲板有没有"东西" ----
    if getattr(spec, "detail", 1) >= 4:
        used = {int(v) for v in np.unique(vox) if int(v) >= 0}
        if len(used) < 7:
            add(
                "ornament.variety",
                "mid",
                f"观赏性细节开着，但整艘船只用了 {len(used)} 种方块：甲板设备与色差都没生效",
                '蓝图里写 patches（水线 hull_lower 用 "-0.28"/0.9，甲板 deck 用 "-0.08"/0.14）',
                6,
            )

    score = max(0, min(100, int(round(score))))
    return {
        "score": score,
        "size": [W, H, D],
        "aspect": round(L / max(beam, 1), 2),   # 舰船报长宽比，不报宽高比
        "photo_aspect": float((brief.get("building") or {}).get("aspect") or 0),
        "roof": {"type": "flat", "height": 0, "share": 0},
        "volumes": len(spec.volumes),
        "window_density": round(share, 3),
        "ship": {"length": L, "beam": beam, "bow": dims["tip"], "railing": rails},
        "issues": issues,
        "patches": patches,
        "seed": spec.seed,
    }


def _facade_layers(vox: np.ndarray, blocks, min_share: float = 0.12) -> list[dict]:
    """量正立面的"层次"：从下到上一共几种材质条带。

    用户说的"层次细节"是可以量的：一张好照片的立面，从勒脚到檐口通常有
    4~6 条不同材质的水平带（深色基座 → 玻璃 → 白檐口 → 木饰带 → 招牌 → 上层）。
    一条都没有、整面墙一个颜色，就是"平板一块"。
    做法：每一行取**最前面那个方块**的众数，再按材质变化切段。
    """
    mask = vox >= 0
    if not mask.any():
        return []
    nx, ny, nz = vox.shape
    front = np.full((nx, ny), -1, dtype=np.int32)
    for y in range(ny):
        for x in range(nx):
            col = np.nonzero(mask[x, y])[0]
            if col.size:
                front[x, y] = int(vox[x, y, col[-1]])
    rows: list[int] = []
    for y in range(ny):
        vals = front[:, y]
        vals = vals[vals >= 0]
        if vals.size < max(3, int(nx * min_share)):
            rows.append(-1)
            continue
        uq, cnt = np.unique(vals, return_counts=True)
        rows.append(int(uq[np.argmax(cnt)]))
    segs: list[dict] = []
    for y, blk in enumerate(rows):
        if blk < 0:
            continue
        if segs and segs[-1]["blk"] == blk:
            segs[-1]["h"] += 1
            continue
        segs.append({"y": y, "h": 1, "blk": blk})

    # 1 格高的段多半是窗框、横梁、腰线，不是"一层"。按颜色就近并进邻居 ——
    # 否则一整片幕墙会被数成 6 层（玻璃 / 框 / 玻璃 / 框…），
    # 层数就成了噪声，反而掩盖了"这块立面到底分了几段"。
    def _dist(a: int, b: int) -> float:
        ra, rb = blocks[a].rgb, blocks[b].rgb
        return sum((ra[i] - rb[i]) ** 2 for i in range(3)) ** 0.5

    for _ in range(3):
        i = 0
        while i < len(segs):
            s = segs[i]
            if s["h"] > 1 or len(segs) < 2:
                i += 1
                continue
            cand = []
            if i > 0:
                cand.append((_dist(s["blk"], segs[i - 1]["blk"]), i - 1))
            if i + 1 < len(segs):
                cand.append((_dist(s["blk"], segs[i + 1]["blk"]), i + 1))
            if not cand:
                i += 1
                continue
            d, j = min(cand)
            if d > 70:  # 颜色差得远：这是真的一层，不是框
                i += 1
                continue
            segs[j]["h"] += s["h"]
            segs.pop(i)
    return [
        {
            "y": s["y"],
            "h": s["h"],
            "name": blocks[s["blk"]].name_zh if 0 <= s["blk"] < len(blocks) else "?",
        }
        for s in segs
    ]


def _glass_share(vox: np.ndarray, blocks) -> float:
    """玻璃占了立面**高度的**几成 —— 商业立面要 0.45~0.75。

    为什么按"行"算而不是按"格"算：竖梃、横梁、招牌柱都长在玻璃带上，
    按格算会被这些骨架稀释掉一半（一栋玻璃占七成的店面，按格算只有 35%），
    量出来永远是"玻璃太少"，逼着 AI 把幕墙改成实墙。
    一行里有 1/4 以上是玻璃，这一行就算"玻璃层"，这样量的是**玻璃占几层楼高**。
    """
    idx = [i for i, b in enumerate(blocks) if "glass" in b.name_en]
    if not idx:
        return 0.0
    mask = vox >= 0
    nx, ny, nz = vox.shape
    front = np.full((nx, ny), -1, dtype=np.int32)
    for y in range(ny):
        for x in range(nx):
            col = np.nonzero(mask[x, y])[0]
            if col.size:
                front[x, y] = int(vox[x, y, col[-1]])
    rows = [y for y in range(ny) if int((front[:, y] >= 0).sum()) >= max(3, nx // 4)]
    if not rows:
        return 0.0
    glass_rows = 0
    for y in rows:
        vals = front[:, y]
        vals = vals[vals >= 0]
        if vals.size and float(np.isin(vals, idx).mean()) >= 0.25:
            glass_rows += 1
    return glass_rows / len(rows)


def _photo_cropped(brief: dict) -> bool:
    """照片的左右/上下被画框切掉了吗？

    切掉之后照片的宽高比只是"画面片段"的比例，不是整栋建筑的比例：
    被切掉一侧的店面照会算出 5.8 这种极端值，拿它要求模型就永远不达标
    （真实建筑是 2.4，已经是对的，却被判"做成了竖高的方楼"）。
    """
    cut = (brief.get("completion") or {}).get("cut") or []
    return bool(cut)


def critique(brief: dict, spec: Spec, vox: np.ndarray, blocks) -> dict:
    """对比"照片 brief"与"已构建的模型"，返回分数、问题与修改建议。"""
    if spec.subject != "building":
        return _critique_ship(brief, spec, vox, blocks)
    issues: list[dict] = []
    patches: list[dict] = []
    score = 100.0

    W, H, D = _extent(spec)
    aspect = W / max(H, 1)
    photo_aspect = float((brief.get("building") or {}).get("aspect") or 0)

    def add(key: str, level: str, msg: str, fix: str, cost: float, patch: dict | None = None):
        nonlocal score
        issues.append({"key": key, "level": level, "issue": msg, "fix": fix})
        if patch:
            patches.append(patch)
        score -= cost

    # ---- 1. 比例：第一眼抓人的东西 ----
    # 照片的宽高比是从**斜俯视**照片上量的，竖向被透视压短，所以它系统性
    # 偏"扁"：真实的宫殿比照片里还长。因此只抓"做成了竖高的方楼"这一种错，
    # 往扁的方向放得很宽 —— 否则长横体量反而会被误判。
    if photo_aspect > 0 and not _photo_cropped(brief):
        ratio = aspect / photo_aspect
        if ratio < 0.58:
            add(
                "aspect",
                "high",
                f"模型宽高比 {aspect:.2f}，照片 {photo_aspect:.2f}：做成了竖高的方楼，"
                f"照片里是横着的建筑",
                "减少层数或加大横向体块，宽度要对得上照片；不要靠拉高层高来凑高度",
                22,
                {"op": "aspect", "want": round(photo_aspect, 2), "have": round(aspect, 2)},
            )
        elif ratio > 3.6:
            add(
                "aspect",
                "low",
                f"模型宽高比 {aspect:.2f}，照片 {photo_aspect:.2f}：压得偏扁",
                "加一层或把屋顶做高（孟莎 4~6 格）",
                6,
                {"op": "aspect", "want": round(photo_aspect, 2), "have": round(aspect, 2)},
            )

    # ---- 1.6 现代商业立面：层次 / 玻璃 / 招牌 ----
    # 店面是**一栋长条**，不是"主体 + 两翼"的建筑群；拿住宅那套去要求它拆体块，
    # 会把一家麦当劳拆成三个山墙小楼。所以商业立面单独一套量法。
    btype = str(spec.meta.get("building_type") or "")
    kinds = {str(d.get("type", "")) for d in (spec.details or [])}
    is_shop = any(
        k in btype for k in ("店", "商业", "餐厅", "商铺", "展厅", "快餐", "超市", "银行", "营业厅")
    )
    # 现代建筑是第三套语汇（悬挑、错位、幕墙、百叶）。不认这一支，
    # 体检会拿"要有屋顶、要有拱廊"的住宅标准去逼它，越改越像府邸 —— 实测过。
    is_modern = (not is_shop) and is_modern_type(btype)
    layers = _facade_layers(vox, blocks)
    if is_shop:
        if len(layers) < 3:
            add(
                "shop.layers",
                "high",
                f"正立面只有 {len(layers)} 层材质：一块平板，层次立不起来",
                "按横三段补：深色勒脚 → 通高玻璃（curtain_wall）→ 白檐口带（band）→ "
                "木纹招牌带（band）→ 招牌（signbox）",
                16,
            )
        share = _glass_share(vox, blocks)
        if share < 0.35:
            add(
                "shop.glass",
                "high",
                f"玻璃只占立面高度的 {share:.0%}：商业店面应该是 45%~75%",
                "把正面的 curtain_wall 加高（h 取立面的 45%~60%），铺满整个宽度"
                "（w = 体块宽 − 2），mullion 3~4、transom 3~4",
                12,
            )
        if "signbox" not in kinds:
            add(
                "shop.sign",
                "high",
                "没有招牌：这是商业建筑最强的识别信号，缺了就像办公室",
                '加 {"type":"signbox","face":"south","u":…,"v":0,"w":5,"h":…,"out":1,'
                '"logo":"m","logo_mat":"concrete:4"}',
                12,
            )
        else:
            signs = [d for d in (spec.details or []) if str(d.get("type")) == "signbox"]
            tall = [d for d in signs if int(d.get("h", 0) or 0) >= 6]
            if not tall:
                add(
                    "shop.sign_height",
                    "high",
                    "招牌都是矮的（h<6）：贴在墙上的一条横带，不是照片里那根从地面通上去的招牌柱",
                    '把竖向招牌柱写成 {"type":"signbox","u":宽的20%,"v":0,"w":5,"h":立面的 70%,'
                    '"out":1,"mat":"concrete:14","logo":"m","logo_mat":"concrete:4","logo_v":…}',
                    14,
                    {"op": "add_detail", "to": {"type": "signbox", "face": "south", "volume": 0,
                                                "u": max(2, round(W * 0.18)), "v": 0, "w": 5,
                                                "h": max(8, round(H * 0.7)), "out": 1,
                                                "mat": "concrete:14", "logo": "m",
                                                "logo_mat": "concrete:4"}},
                )
        if str((spec.roof or {}).get("type", "flat")) != "flat":
            add(
                "shop.roof",
                "mid",
                f"屋顶是 {spec.roof.get('type')}：商业立面是平的，坡顶会立刻变回住宅",
                '改回 {"type":"flat","parapet":1}',
                10,
            )
        mixed = {"balcony", "dormer", "arcade", "column", "pediment", "portico"} & kinds
        if mixed:
            add(
                "shop.borrowed",
                "mid",
                f"混进了住宅/古典构件（{'、'.join(sorted(mixed))}），店面用不上",
                "删掉这些细节，改用 band / louver / planter / signbox",
                8,
            )

    # ---- 1.7 现代建筑：错位 / 悬挑 / 幕墙 / 百叶 ----
    # 这一支的病变和住宅正好相反：住宅怕"太平"，现代怕"太整齐"。
    # 三个盒子对齐码齐、屋顶压一个孟莎，就是"白石府邸"那一次的翻版。
    if is_modern:
        if str((spec.roof or {}).get("type", "flat")) != "flat":
            add(
                "modern.roof",
                "high",
                f"屋顶是 {spec.roof.get('type')}：现代建筑的屋面是平的，坡顶一出现就变回府邸",
                '改回 {"type":"flat","parapet":1~2}，屋面设备用 1~2 格小体块压',
                14,
                {"op": "set", "path": "roof", "to": {"type": "flat", "parapet": 1}},
            )
        classical = {"arcade", "portico", "pediment", "dormer", "column"} & kinds
        if classical:
            add(
                "modern.classical",
                "high",
                f"混进了古典构件（{'、'.join(sorted(classical))}）：这套语汇一进来，"
                f"整栋就按府邸读了",
                "删掉它们，改用 curtain_wall（通高玻璃）+ louver（竖百叶）+ cornice（薄檐口）",
                10,
            )
        share_g = _glass_share(vox, blocks)
        if share_g < 0.30:
            add(
                "modern.glass",
                "high",
                f"玻璃只占立面高度的 {share_g:.0%}：这类建筑的正面主体就是玻璃，"
                f"低于 40% 会读成砌体楼",
                "curtain_wall 铺满面宽（w = 体块宽 − 2）、跨整层（h 4~9），"
                "mullion 3~5、transom 3~4；窗列只留给侧立面",
                12,
            )
        if "curtain_wall" not in kinds and "louver" not in kinds:
            add(
                "modern.skin",
                "mid",
                "既没有幕墙也没有竖百叶：立面只剩一块板，现代感全靠这两样",
                "先写一条 curtain_wall（h 取层高的 2~3 倍），再在同一条 u 上盖 louver（step 2、out 1）",
                9,
            )
        # 附属体量（角塔/凸窗/侧翼）是"结构数量"长出来的，
        # 不该按主体的错位/梯田规则去罚
        vols = [v for v in spec.volumes if not getattr(v, "aux", False)]
        if len(vols) >= 2:
            # 平面完全重合 = 一根更高的方柱，不是"错位堆叠"
            same_xy = all(
                v.x == vols[0].x and v.z == vols[0].z and v.w == vols[0].w and v.d == vols[0].d
                for v in vols[1:]
            )
            if same_xy:
                add(
                    "modern.offset",
                    "high",
                    f"{len(vols)} 个体块在平面上完全重合：这就是一根更高的方柱，没有错位",
                    "上层在 x 或 z 上偏移 2~6 格（w/d 跟着收或放），"
                    "至少一处探出下层 2~4 格做成悬挑",
                    12,
                )
            # 并排 vs 上下叠：现代建筑的错位是"叠"，不是"排"
            side_by_side = True
            for i in range(len(vols)):
                for j in range(i + 1, len(vols)):
                    a, b = vols[i], vols[j]
                    ox = min(a.x1, b.x1) - max(a.x, b.x)
                    oz = min(a.z1, b.z1) - max(a.z, b.z)
                    if ox > 0 and oz > 0:
                        side_by_side = False
            if side_by_side:
                add(
                    "modern.stacked",
                    "high",
                    f"{len(vols)} 个体块在平面上互不重叠：并排的三栋房子，不是错位堆叠",
                    "把体块改成上下叠：下层顶面 = 上层 y，平面互相重叠一半以上，"
                    "靠 x/z 偏 2~6 格做出错位",
                    12,
                )
            # 层层缩小 = 梯田/生日蛋糕：真正的现代建筑是"错位"，不是"收分"
            shrink = 0
            for a in vols:
                below = [b for b in vols if b is not a and b.y1 <= a.y]
                if not below:
                    continue
                base = max(below, key=lambda b: b.w * b.d)
                # 屋面上的设备间、楼梯间只有几格，天然"比下层小很多"，
                # 把它们算进梯田，任何带屋顶小件的蓝图都会被误罚。
                if a.w * a.d < 0.25 * base.w * base.d:
                    continue
                if base.w - a.w > 4 or base.d - a.d > 4:
                    shrink += 1
            # 照片里常见"两个小盒子坐在一个大基座上"，那不是梯田 ——
            # 梯田是**一层层**往回收，至少要连着收三次才读得出。
            if shrink >= 3:
                add(
                    "modern.terrace",
                    "mid",
                    f"有 {shrink} 个体块比下层窄了 4 格以上：挤成了梯田（生日蛋糕），"
                    f"下半层的屋面会露出一大片",
                    "让相邻两层的 w/d 相差不超过 4 格，靠 x/z 错位错开；"
                    "屋面露出来的那部分应该是「悬挑下方」，不是「上一层退回去」",
                    9,
                )
            # 悬挑：某个体块相对它下面的体块探出去了
            cant = False
            for a in vols:
                for b in vols:
                    if a is b or b.y1 > a.y:
                        continue  # b 必须在 a 之下
                    if a.x < b.x or a.x1 > b.x1 or a.z < b.z or a.z1 > b.z1:
                        cant = True
            if not cant and not same_xy:
                add(
                    "modern.cantilever",
                    "mid",
                    "没有任何体块探出下层：整栋是竖直堆叠，缺「浮起来」的那一层",
                    "把上层体块的 w/d 放大 2~4 格，或者 z 往前挪 2~4 格（向南探出），"
                    "探出部分的下方要留空",
                    8,
                )
            # 错位要"看得出来"。两张 25×20 的盒子只错 1 格，等轴测里就是
            # 一整块 —— 用户看到的仍是"方盒子"。这里量的是**上层同高体块之间**
            # 的平面位移，2 格以下等于没错。
            for i in range(len(vols)):
                for j in range(i + 1, len(vols)):
                    a1, b1 = vols[i], vols[j]
                    if a1.y != b1.y:
                        continue
                    ox = min(a1.x1, b1.x1) - max(a1.x, b1.x)
                    oz = min(a1.z1, b1.z1) - max(a1.z, b1.z)
                    if ox <= 0 or oz <= 0:
                        continue  # 不重叠的是"并排"，上面已经罚过
                    dx = abs(a1.x - b1.x)
                    dz = abs(a1.z - b1.z)
                    # 两张盒子可以在 x 上隔很远（左右并排），但只要 z 一样，
                    # 等轴测里它们就在同一条"带"上，读成一整片而不是两段。
                    # 真正拉开层次靠的是**进深错位**，这一条不能省。
                    if dz < 3 and dx < 3:
                        why = f"x 差 {dx} 格、z 差 {dz} 格"
                    elif dz < 3:
                        why = f"进深只差 {dz} 格（x 隔了 {dx} 格）"
                    else:
                        why = ""
                    if why:
                        add(
                            "modern.stagger",
                            "mid",
                            f"第 {i + 1}、{j + 1} 两个盒子几乎没有进深错位（{why}）："
                            f"等轴测里会连成一整块，错位等于没做",
                            f"把其中一个在 z 上拉开 4~6 格（或 x 上 5~8 格），"
                            f"并让它比另一个高 2~4 格 —— 高低差 + 平面位移才是「错位堆叠」",
                            10,
                            {"op": "stagger", "a": i, "b": j, "dz": 5, "dy": 3},
                        )
                        break
                else:
                    continue
                break
            # 斜置 / 多边形平面：不强制，但现代建筑最要紧的"不规则"手段就这几个参数。
            # cost 0 = 只提示不扣分，别把正交的楼逼成歪的。
            has_poly = any(getattr(v, "shape", "") or getattr(v, "poly", []) or getattr(v, "chamfer", 0)
                           for v in vols)
            if not any(
                getattr(v, "lean_x", 0) or getattr(v, "lean_z", 0)
                or getattr(v, "skew_x", 0) or getattr(v, "skew_z", 0)
                or getattr(v, "tilted", False)
                for v in vols
            ):
                add(
                    "modern.slant",
                    "low",
                    "所有体块都是正交盒子：照片里上层若与底层不平行，现在只能做成方块",
                    "给上层某个体块加 \"lean\": {\"x\": 4~8}（墙往外斜）、"
                    "\"skew\": {\"z\": 4~10}（平面斜切），"
                    "或者 \"tilt\": {\"axis\": \"x\", \"bottom\": 8, \"top\": 8}（整层斜带，"
                    "楼板斜着走 —— 照片里\"二层像一条斜带\"就是用这个）",
                    0,
                )
            if not has_poly:
                add(
                    "modern.plan",
                    "low",
                    "所有体块的平面都是矩形：六边形楼层、楔形展厅、L 形转角楼做不出来",
                    "需要时给 volume 写 \"shape\": \"hex\"（六边形）/ \"hex_front\"（前窄后宽）"
                    "/ \"oct\"（八边形）/ \"wedge\"（楔形）/ \"round\"（圆塔）/ \"l\"（L 形），"
                    "或者用 \"poly\": [[x,z],…] 自己给顶点；轮廓边缘会自动变成斜墙",
                    0,
                )
            if not any(v.y > 0 for v in vols):
                add(
                    "modern.base",
                    "mid",
                    "所有体块都从地面起（y 全是 0）：没有架空层，也没有底层内收",
                    "把首层玻璃退进 2~3 格，上层体块的 y 抬到首层顶面，"
                    "整栋才有「底虚上实」的关系",
                    6,
                )

    # ---- 2. 体量：有没有主次 ----
    # 大型公共建筑先看"够不够大"：33 格宽的宫殿无论细节多好都读不出来，
    # 开间排不下、拱廊只有三四个、老虎窗两三个就没了。
    LARGE = ("宫殿", "庄园", "府邸", "城堡", "教堂", "大殿", "学校", "教学楼",
             "图书馆", "车站", "博物馆", "市政", "剧院", "医院")
    if any(k in btype for k in LARGE) and W < 48:
        add(
            "scale",
            "high",
            f"你自己判定这是「{btype}」，但只做了 {W} 格宽：这类建筑开间多，"
            f"48 格以下排不下拱廊和足够的窗，细节会被压没",
            f"把体块整体放大到 56~72 格宽（比例保持不变，别只加层数）",
            20,
            {"op": "scale_width", "to": 60, "keep_ratio": True},
        )
    if len(spec.volumes) == 1 and W >= 24 and not is_shop:
        add(
            "massing",
            "high",
            f"{W} 格宽只有一个长方体：这是「大房子」，不是「建筑群」",
            "现代建筑拆成**上下错位的几个盒子**（y 一层层叠、平面各偏 2~6 格）；"
            "其他类型拆成中央主体 + 两侧翼（主体 45%~60% 宽，翼低 2~4 格并后退）"
            if is_modern
            else "拆成中央主体 + 两侧翼（主体占 45%~60% 宽，翼低 2~4 格并后退）",
            14,
            {"op": "split_volumes", "main": round(W * 0.5)},
        )
    elif len(spec.volumes) >= 2:
        widest = max(v.w for v in spec.volumes)
        share = widest / W
        if share < 0.35:
            add(
                "massing",
                "mid",
                f"最宽体块只占 {share:.0%}：缺少主体，看着像一排平房",
                "把中央主体加宽到总宽的 45%~60%",
                8,
            )
        # 有多个体块但高度全一样 = 没有主次，远看还是一个盒子。
        # 三段式的重点不在"分成三块"，而在中间那块更高、更往前。
        heights = {v.h for v in spec.volumes}
        if len(heights) == 1 and len(spec.volumes) >= 2:
            add(
                "massing",
                "mid",
                f"{len(spec.volumes)} 个体块一样高（{spec.volumes[0].h} 格）：没有主次，远看还是一个盒子",
                "主体加高 2~4 格（或把两翼降低 2~4 格并后退），中间那块要压得住两端",
                8,
            )

    # ---- 3. 屋顶：平顶是"办公楼"的最大来源 ----
    roof_kind = str((spec.roof or {}).get("type", "flat"))
    roof_h = int((spec.roof or {}).get("height", 0) or 0)
    if roof_kind == "flat" and W >= 32 and not is_shop and not is_modern:
        big = photo_aspect >= 2.6
        add(
            "roof",
            "high" if big else "mid",
            f"{W} 格宽的建筑用了平顶：这类建筑的屋顶通常占立面 1/3 以上，"
            f"平顶会把整栋楼读成办公楼",
            'roof.type 改 "mansard"（法式）或 "hip"（四坡），height 取 4~6，'
            "再沿正面加 4~8 个 dormer",
            16 if big else 9,
            {"op": "set", "path": "roof", "to": {"type": "mansard", "height": 5, "mat": "roof"}},
        )
    if roof_kind == "mansard":
        want = max(3, int(round(H * 0.22)))
        if roof_h < want:
            add(
                "roof",
                "mid",
                f"孟莎屋顶只有 {roof_h} 格，占立面 {roof_h / max(H, 1):.0%}：太扁，像压了个盖",
                f"roof.height 提到 {want} 格（约占立面 22%）",
                8,
                {"op": "set", "path": "roof.height", "to": want},
            )
        dormers = sum(1 for d in spec.details if str(d.get("type")) == "dormer")
        if dormers < 3 and W >= 32:
            add(
                "roof",
                "mid",
                f"孟莎屋面上只有 {dormers} 个老虎窗：整块灰板，屋顶读不出来",
                "沿正面每 6~8 格加一个 dormer（w=2）",
                8,
                {"op": "add_dormers", "count": max(4, W // 8)},
            )

    # ---- 4. 层高：堆层数是最常见的"变高"办法，也是最错的 ----
    # 店面例外：商业首层是**通高**的（通高玻璃 + 挑空），8~9 格是常态，
    # 照片里那家麦当劳一楼就是两层高。拿住宅的 6.5 格去卡它，等于逼 AI
    # 把一个通高店面拆成两个矮夹层。
    for i, v in enumerate(spec.volumes):
        if not v.floors:
            continue
        fh = v.h / v.floors
        # 店面和现代建筑的首层是通高的（通高玻璃 + 挑空），7~9 格是常态；
        # 拿住宅的 6.5 去卡它，等于逼 AI 把一层大堂拆成两个夹层。
        tall = 9.0 if (is_shop or is_modern) else 6.5
        if fh > tall:
            add(
                "floors",
                "high",
                f"体块 {i} 层高 {fh:.1f} 格（{v.h}÷{v.floors}）：照片里没有这么高的一层",
                "层高按 4~5 格；多出来的高度应该给屋顶，不是给楼层",
                12,
                {"op": "set_floor_h", "volume": i, "to": 4},
            )
        elif fh < 3:
            add(
                "floors",
                "high",
                f"体块 {i} 层高 {fh:.1f} 格：玩家进不去",
                "层高至少 3 格，建议 4",
                12,
                {"op": "set_floor_h", "volume": i, "to": 4},
            )

    # ---- 5. 窗：铺满度 ----
    # 玻璃板（102）也算玻璃：0.31 之后窗与幕墙默认都转成板，只认 20/95
    # 的话"开窗密度"永远量成 0%，AI 会以为立面是实墙，一轮轮往上加窗。
    glass_idx = {i for i, b in enumerate(blocks) if "glass" in b.name_en}
    if glass_idx:
        gmask = np.isin(vox, list(glass_idx))
    else:
        gmask = np.zeros_like(vox, dtype=bool)

    # 正面开间数：最长体的南立面上有几段连续的玻璃。
    # 这比"总玻璃占比"准得多 —— 侧立面、幕墙会把总占比拉高，掩盖正面太稀。
    if spec.volumes:
        main = max(spec.volumes, key=lambda v: v.w)
        run_cols = set()
        for x in range(main.x, main.x1 + 1):
            if gmask[x, main.y : main.y1 + 1, main.z1].any():
                run_cols.add(x)
        bays = 0
        prev = None
        for x in sorted(run_cols):
            if prev is None or x - prev > 1:
                bays += 1
            prev = x
        want_bays = max(2, round(main.w / 6))
        if main.w >= 24 and bays < 0.7 * want_bays and not is_shop and not is_modern:
            add(
                "windows.rhythm",
                "mid",
                f"正面 {main.w} 格宽只排了 {bays} 组窗：按每 4~6 格一组算，应该有 {want_bays} 组左右，"
                f"两头和中间会留下大片实墙",
                f"把正面窗列的 cols 提高到 {want_bays}，并让 u/gap_u 按墙宽铺满",
                10,
                {"op": "set_window_cols", "volume": spec.volumes.index(main), "to": want_bays},
            )

    wall_idx = {i for i, b in enumerate(blocks) if b is spec.palette.get("wall")}
    wmask = np.isin(vox, list(wall_idx)) if wall_idx else (vox >= 0)
    wall_open = _exposed(vox, wmask)
    win_open = int(gmask.sum())
    density = win_open / max(1, wall_open)
    # 商业立面本来就没有"墙垛"：一整片幕墙，玻璃占比越高越对。
    # 拿住宅的"玻璃太多"去罚它，会逼着 AI 在店面中间砌墙。
    if density < 0.05 and not is_shop:
        add(
            "windows",
            "mid",
            f"开窗密度只有 {density:.0%}：立面几乎是实墙",
            "窗列按 (墙宽 − 2×边距 + 间距) ÷ (窗宽 + 间距) 铺满，覆盖 55%~90%",
            10,
        )
    elif density > 0.55 and not is_shop and not is_modern:
        add(
            "windows",
            "low",
            f"开窗密度 {density:.0%}：玻璃太多，墙几乎没有了",
            "减少窗列或缩小窗宽，保留墙垛",
            4,
        )

    # ---- 6. 必备构件 ----
    kinds = {str(d.get("type")) for d in spec.details}
    # 现代建筑的收头是"玻璃落地 + 女儿墙"，没有勒脚也没有檐口 ——
    # 这两条是住宅/古典的语法。逼它加，等于在玻璃盒子底下糊一圈石头。
    modern_skin = is_modern
    for need, msg, fix, cost in (
        ("door", "没有入口门", "加 door（w 2~4、h 3、steps 2）", 8),
        *([] if modern_skin else [("plinth", "没有勒脚", "加 plinth（h 1~2），把楼和地面分开", 4)]),
        # 商业立面的收头是"檐口带（band）"，不是古典 cornice
        *([] if (is_shop or modern_skin) else [
            ("cornice", "没有檐口", "加 cornice（out 1），墙顶才不会是一道切口", 4)
        ]),
    ):
        if need not in kinds:
            add(f"detail.{need}", "high", msg, fix, cost)

    # 大型建筑缺拱廊 / 柱廊（店面不适用：那是古典语汇）
    if W >= 36 and not is_shop and not is_modern and not ({"arcade", "portico", "column"} & kinds):
        add(
            "detail.classical",
            "mid",
            "大体量建筑没有拱廊 / 柱廊 / 门廊：立面是平的，像厂房",
            "首层加 arcade（count = 开间数），中央加 portico + pediment",
            10,
            {"op": "add_detail", "to": {"type": "arcade", "face": "south", "volume": 0,
                                        "u": 2, "w": max(8, W - 6), "h": 4,
                                        "count": max(3, W // 7)}},
        )

    # ---- 7. 配色：模型的主色有没有跑偏 ----
    pal = brief.get("palette") or {}
    want_wall = str(pal.get("wall") or "")
    have_wall = spec.palette.get("wall")
    if want_wall and have_wall is not None:
        from .color import delta_e_00, rgb_to_lab
        from .spec import parse_material

        try:
            target = parse_material(want_wall)
            d = float(
                delta_e_00(
                    rgb_to_lab(np.array([target.rgb], dtype=np.float64) / 255.0),
                    rgb_to_lab(np.array([have_wall.rgb], dtype=np.float64) / 255.0),
                )[0]
            )
        except Exception:
            d = 0.0
        if d > 12:
            add(
                "palette",
                "mid",
                f"墙色和照片差了 ΔE {d:.0f}：远看会是另一种颜色",
                f"把 palette.wall 换成 {want_wall}",
                10,
                {"op": "set", "path": "palette.wall", "to": want_wall},
            )

    # ---- 8. 观赏性细节（detail=4）：只看"不均质"，不看构件数量 ----
    # 这一档的问题不是"缺什么构件"，而是"什么都均匀"：整面墙一个色、
    # 屋顶干净得像效果图。用方块种类数当代理指标足够了 ——
    # 小件和色差一旦生效，种类数必然跳上去。
    if getattr(spec, "detail", 1) >= 4:
        used = {int(v) for v in np.unique(vox) if int(v) >= 0}
        if len(used) < 7:
            add(
                "ornament.variety",
                "mid",
                f"观赏性细节开着，但整栋只用了 {len(used)} 种方块：小件和色差都没生效",
                "在蓝图里写 2~4 条 patches（材质替换），并确认 ornament 为 true",
                6,
            )

    score = max(0, min(100, int(round(score))))
    return {
        "score": score,
        "size": [W, H, D],
        "aspect": round(aspect, 2),
        "photo_aspect": photo_aspect,
        "roof": {"type": roof_kind, "height": roof_h, "share": round(roof_h / max(H, 1), 2)},
        "volumes": len(spec.volumes),
        "modern": is_modern,
        "window_density": round(density, 3),
        "facade_layers": layers,
        "glass_share": round(_glass_share(vox, blocks), 2) if (is_shop or is_modern) else None,
        "issues": issues,
        # 机器可执行的修改建议：AI 可以照抄，也可以自己换一种改法
        "patches": patches,
        "seed": spec.seed,
    }
