"""观赏性细节层（detail=4）：社区做法的小件堆叠与色差补丁。

一栋楼"像不像"，往往不取决于整体配色，而取决于**不均质**：
窗下挂着一台外机、墙角落水管一路到底、屋顶水箱旁边冒两根排气管、
墙面有几块补过的旧砖。军舰也一样 —— 甲板上永远堆着设备箱、天线、
救生筒，而且**从来不对称**。

所以这一层只做两件事：
1. ``patch``：把已有方块换成另一种（不新增体量）——水线、烟熏、窗下污渍、
   甲板分块。颜色是从当前方块推出来的，不另起一套配色；
2. ``props``：在空位上垒 1~2 格的小堆叠 —— 设备箱、天线、救生筒、
   空调外机、花箱、屋顶水箱。

两者都由 seed 决定（同 seed + 同蓝图 = 同模型），并且只在一侧、不等距，
刻意破掉对称。位置全部由几何推断：顶面、外露面、窗下第一行，
不需要 AI 给坐标 —— 它给坐标一向给不准。
"""

from __future__ import annotations

import random
from typing import TYPE_CHECKING

import numpy as np

from . import palette

if TYPE_CHECKING:  # 避免和 spec.py 循环 import
    from .spec import Spec


# --------------------------------------------------------------------------
# 基础工具
# --------------------------------------------------------------------------
def _blk(name):
    """名字 -> Block。"concrete:7" / "iron_bars" 两种写法都收。"""
    s = str(name or "").strip()
    if not s:
        return None
    a, b = (s.split(":", 1) + ["0"])[:2] if ":" in s else (s, "0")
    try:
        return palette.find(a, int(b))
    except Exception:
        return None


def _solid_mask(vox: np.ndarray) -> np.ndarray:
    return vox >= 0


def _tops(vox: np.ndarray) -> np.ndarray:
    """每一列 (x,z) 最高的实心格 y；空列为 -1。"""
    solid = vox >= 0
    ny = vox.shape[1]
    has = solid.any(axis=1)
    off = np.argmax(solid[:, ::-1, :], axis=1)
    return np.where(has, ny - 1 - off, -1)


def _exposed(solid: np.ndarray) -> np.ndarray:
    """六邻里至少有一个是空的格（外表面）。

    边界外的空格按"实心"算：模型边缘不是外立面，不该被当成可挂小件的墙面。
    """
    e = np.pad(~solid, 1, constant_values=False)
    return (
        e[2:, 1:-1, 1:-1] | e[:-2, 1:-1, 1:-1]
        | e[1:-1, 2:, 1:-1] | e[1:-1, :-2, 1:-1]
        | e[1:-1, 1:-1, 2:] | e[1:-1, 1:-1, :-2]
    )


def _mode(vals: np.ndarray) -> int:
    if vals.size == 0:
        return -1
    bc = np.bincount(vals.astype(int))
    return int(bc.argmax()) if bc.size else -1


def _seed(spec: "Spec") -> int:
    if spec.seed:
        return int(spec.seed) & 0x7FFFFFFF
    h = 20261110
    for v in spec.volumes:
        h = (h * 131 + v.x * 7 + v.y * 13 + v.z * 17 + v.w * 19 + v.h * 23 + v.d * 29) & 0x7FFFFFFF
    return h


# --------------------------------------------------------------------------
# 材质替换
# --------------------------------------------------------------------------
_VAR_CACHE: dict = {}

# 方块"类目"：材料替换只在同类里挑。不限制的话，白墙压暗会匹配到黏土块
# （它只是 RGB 接近，实际偏蓝），一整面白墙就变成蓝灰色迷彩 —— 实测过。
_CLASS = {
    "stone": {
        "stone", "stonebrick", "cobblestone", "mossy_cobblestone", "andesite", "diorite",
        "granite", "sandstone", "quartz_block", "prismarine", "nether_brick", "end_brick",
        "purpur_block", "hardened_clay", "stained_hardened_clay", "clay", "brick_block",
        "gravel", "obsidian", "cobblestone_wall", "red_nether_brick", "netherrack",
    },
    "concrete": {"concrete"},
    "wool": {"wool", "carpet"},
    "wood": {"planks", "log", "log2", "bookshelf", "crafting_table", "furnace", "noteblock"},
    "metal": {"iron_block", "gold_block", "diamond_block", "emerald_block", "iron_bars"},
}


def _class(blk) -> str:
    n = blk.name_en
    for key, names in _CLASS.items():
        if n in names:
            return key
    if "concrete" in n:
        return "concrete"
    if "wool" in n or "carpet" in n:
        return "wool"
    return "other"


def _variant(blk, dl: float):
    """把方块换成"同色系、明度偏移 dl"的另一种方块。

    社区做法里的"色差"不是随便换个颜色，而是同一材料的深浅两档
    （淡灰混凝土 / 灰混凝土、石砖 / 苔石砖）。所以：
    1. 只在**同类目**里找（石材不会变成羊毛）；
    2. 明度方向必须对（要暗就必须真的暗一档）；
    3. 色相偏太多要罚 —— 白墙的深色变体不该是偏蓝的黏土块。
    """
    key = (blk.id, blk.meta, round(dl, 2))
    if key in _VAR_CACHE:
        return _VAR_CACHE[key]
    from .color import delta_e_00, rgb_to_lab

    base = np.array(blk.rgb, dtype=np.float64)
    target = np.clip(base * (1.0 + dl), 0, 255)
    cls = _class(blk)
    bl = float(base.sum())
    tl = rgb_to_lab(target[None, :] / 255.0)

    def _rank(pool):
        if not pool:
            return [], np.array([])
        ref = np.array([b.rgb for b in pool], dtype=np.float64) / 255.0
        return pool, delta_e_00(rgb_to_lab(ref), tl)

    # 明度方向先筛：要"暗一档"就必须真的暗，否则会出现"旧化后变亮"。
    # 必须**严格**不比原方块亮/暗 —— 把原方块留在池子里当"最近邻"，
    # 白混凝土的"窗下挂污"就会选中它自己，等于什么也没做。
    def _direct(pool):
        if dl > 0:
            tighter = [b for b in pool if float(sum(b.rgb)) > bl + 1]
        elif dl < 0:
            tighter = [b for b in pool if float(sum(b.rgb)) < bl - 1]
        else:
            tighter = [b for b in pool if b is not blk]
        return tighter or pool

    same = [b for b in palette.all_blocks() if _class(b) == cls and b.id not in (20, 95)]
    picked, d = _rank(_direct(same))
    # 同类里没有合适色阶（白色混凝土只有"白/浅灰/灰/黑"四档）时，
    # 放宽到石材 + 混凝土这一族 —— 砌体之间互换没人看得出问题，
    # 但绝不能放宽到羊毛/木板（那是"贴了一块布"）。
    if picked and float(np.min(d)) > 9.0:
        union = [b for b in palette.all_blocks() if _class(b) in ("stone", "concrete") and b.id not in (20, 95)]
        p2, d2 = _rank(_direct(union))
        if len(d2) and float(np.min(d2)) < float(np.min(d)):
            picked, d = p2, d2
    if not picked:
        return blk
    out = picked[int(np.argmin(d))]
    _VAR_CACHE[key] = out
    return out


def _blobs(mask: np.ndarray, rng: random.Random, want: int, kmin: int = 1, kmax: int = 3):
    """在 mask 里长出一小簇一小簇的格子。

    逐格随机替换会得到椒盐噪点，看着像贴图坏了；MC 里的旧化、补丁都是
    连成片的小块，所以这里从一个随机种子出发做随机游走。
    """
    avail = mask.copy()
    cells: list[tuple[int, int, int]] = []
    if not avail.any():
        return cells
    idxs = np.argwhere(avail)
    order = list(range(len(idxs)))
    rng.shuffle(order)
    cursor = 0
    nx, ny, nz = avail.shape
    while len(cells) < want and cursor < len(order):
        x, y, z = (int(v) for v in idxs[order[cursor]])
        cursor += 1
        if not avail[x, y, z]:
            continue
        k = rng.randint(kmin, kmax)
        frontier = [(x, y, z)]
        got = 0
        while frontier and got < k:
            cx, cy, cz = frontier.pop(rng.randrange(len(frontier)))
            if not (0 <= cx < nx and 0 <= cy < ny and 0 <= cz < nz):
                continue
            if not avail[cx, cy, cz]:
                continue
            avail[cx, cy, cz] = False
            cells.append((cx, cy, cz))
            got += 1
            for dx, dy, dz in ((1, 0, 0), (-1, 0, 0), (0, 0, 1), (0, 0, -1), (0, 1, 0)):
                if rng.random() < 0.75:
                    frontier.append((cx + dx, cy + dy, cz + dz))
    return cells


def _runs(mask: np.ndarray, rng: random.Random, want: int, amin: int = 3, amax: int = 8, axis: int = -1):
    """沿一条轴连成短条，而不是长成一团。

    船体钢板、墙面雨痕、甲板分块都是**横向的条**：同样的覆盖率，
    用斑点长出来像数码迷彩，用条长出来才像"这块板子换过"。
    axis=-1 表示每条随机挑 X 或 Z；axis=1 是竖条（雨水顺墙往下淌的痕迹）。
    """
    avail = mask.copy()
    cells: list[tuple[int, int, int]] = []
    if not avail.any():
        return cells
    nx, ny, nz = avail.shape
    idxs = np.argwhere(avail)
    order = list(range(len(idxs)))
    rng.shuffle(order)
    for i in order:
        if len(cells) >= want:
            break
        x, y, z = (int(v) for v in idxs[i])
        if not avail[x, y, z]:
            continue
        n = rng.randint(amin, amax)
        ax = axis if axis >= 0 else rng.choice((0, 2))
        step = 1 if ax == 1 else rng.choice((1, -1))
        cx, cy, cz = x, y, z
        got = 0
        for _ in range(n):
            if not (0 <= cx < nx and 0 <= cy < ny and 0 <= cz < nz):
                break
            if not avail[cx, cy, cz]:
                break
            avail[cx, cy, cz] = False
            cells.append((cx, cy, cz))
            got += 1
            if ax == 0:
                cx += step
            elif ax == 1:
                cy += step
            else:
                cz += step
        if 0 < got < amin:  # 太短就退回去当没放，避免又变回斑点
            for c in cells[-got:]:
                avail[c] = True
            del cells[-got:]
    return cells


# 这些区域是"大面"：补丁要长成横向的条（钢板、雨痕、分块），
# 长成斑点就成了数码迷彩 —— 同样覆盖率，观感完全不同。
PLATE_REGIONS = {
    "hull_side": "plate",
    "hull_lower": "plate",
    "superstructure": "plate",
    "walls": "plate",
    "deck": "plate",
    "roof": "plate",
}


def _default_patches(spec: "Spec") -> list[dict]:
    """AI 没写 patches 时的兜底：只做"必然存在"的那几处色差。"""
    if spec.subject == "ship":
        return [
            # 水线：这不是"做旧"，是真实舰船的防污漆带 —— 必须是**连续**一条，
            # 打成斑块就变成数码迷彩了（第一版就是这样，一眼假）。
            {"region": "hull_lower", "mat": "-0.28", "cover": 1.0},
            {"region": "deck", "mat": "-0.08", "cover": 0.14},
            {"region": "superstructure", "mat": "+0.06", "cover": 0.10},
        ]
    return [
        {"region": "base", "mat": "-0.22", "cover": 0.30},             # 墙脚返潮
        {"region": "below_windows", "mat": "-0.14", "cover": 0.35},    # 窗下挂污
        {"region": "roof", "mat": "-0.12", "cover": 0.22},
    ]


def _region(region: str, ctx: dict) -> np.ndarray | None:
    """命名区域 -> 候选格 mask。

    用名字而不是坐标：AI 给数字坐标十次有六次越界，给"窗下/水线/屋顶"
    这种名字反而是稳的，具体几何由这里算。
    """
    solid, exp, tops = ctx["solid"], ctx["exp"], ctx["tops"]
    shape = solid.shape
    region = str(region or "").lower()
    if region in ("hull_lower", "hull_side", "deck", "superstructure") and ctx["subject"] != "ship":
        return None
    if region == "deck":
        if ctx["subject"] == "ship":
            m = np.zeros(shape, dtype=bool)
            xs, zs = np.nonzero(ctx["free_deck"])
            m[xs, max(0, ctx["deck_y"]), zs] = True
            return m & solid
        return ctx["topmask"] & solid
    if region == "hull_lower":
        m = exp & solid & (ctx["yidx"] <= ctx["y0"] + int(ctx["band_low"]))
        return m & (ctx["yidx"] <= ctx["deck_y"])
    if region == "hull_side":
        m = exp & solid & (ctx["yidx"] <= ctx["deck_y"]) & (ctx["yidx"] > ctx["y0"] + int(ctx["band_low"]))
        return m
    if region == "superstructure":
        return exp & solid & (ctx["yidx"] > ctx["deck_y"])
    if region == "base":
        return exp & solid & (ctx["yidx"] <= ctx["y0"] + 1)
    if region == "roof":
        return ctx["topmask"] & solid & (ctx["yidx"] >= ctx["y1"] - 2)
    if region == "below_windows":
        return ctx["below_windows"]
    if region == "walls":
        return exp & solid & ~ctx["topmask"]
    if region == "corners":
        m = np.zeros(shape, dtype=bool)
        x0, x1, z0, z1 = ctx["bbox"]
        for x in (x0, x1):
            m[x, :, z0] = True
            m[x, :, z1] = True
        for z in (z0, z1):
            m[x0, :, z] = True
            m[x1, :, z] = True
        return m & exp & solid & ~ctx["topmask"]
    return None


def _patch(builder, spec: "Spec", rng: random.Random) -> int:
    vox = builder.vox
    solid = _solid_mask(vox)
    if not solid.any():
        return 0
    exp = _exposed(solid)
    tops = _tops(vox)
    ny = vox.shape[1]

    # 玻璃：不许被补丁盖掉（窗子变墙是照片还原度最大的损失）
    glass = np.zeros(vox.shape, dtype=bool)
    for i, b in enumerate(builder.blocks):
        if "glass" in b.name_en:
            glass |= vox == i

    yidx = np.broadcast_to(np.arange(ny, dtype=np.int16)[None, :, None], vox.shape)
    solid_y = np.argwhere(solid)[:, 1]
    y0, y1 = int(solid_y.min()), int(solid_y.max())
    deck_y = _mode(tops[tops >= 0])
    topmask = np.zeros(vox.shape, dtype=bool)
    valid = tops >= 0
    xs, zs = np.nonzero(valid)
    topmask[xs, tops[xs, zs], zs] = True
    free_deck = (tops == deck_y) & (deck_y >= 0)

    # 窗下第一行：按玻璃列的底端来定，斜屋顶、幕墙都适用
    below = np.zeros(vox.shape, dtype=bool)
    if glass.any():
        bottom: dict[tuple[int, int], int] = {}
        for x, y, z in np.argwhere(glass):
            k = (int(x), int(z))
            if k not in bottom or y < bottom[k]:
                bottom[k] = int(y)
        for (x, z), yb in bottom.items():
            for dy in (1, 2):
                y = yb - dy
                if 0 <= y < ny and solid[x, y, z] and exp[x, y, z] and not glass[x, y, z]:
                    below[x, y, z] = True

    bbox = (
        int(np.nonzero(solid.any(axis=(1, 2)))[0].min()),
        int(np.nonzero(solid.any(axis=(1, 2)))[0].max()),
        int(np.nonzero(solid.any(axis=(0, 1)))[0].min()),
        int(np.nonzero(solid.any(axis=(0, 1)))[0].max()),
    )
    ctx = {
        "solid": solid, "exp": exp, "tops": tops, "topmask": topmask,
        "deck_y": deck_y, "free_deck": free_deck, "below_windows": below,
        "y0": y0, "y1": y1, "yidx": yidx, "bbox": bbox,
        "subject": spec.subject,
        "band_low": 1,
    }

    items = [dict(x) for x in (spec.patches or []) if isinstance(x, dict)]
    if not items:
        items = _default_patches(spec)

    n = 0
    for it in items[:6]:
        region_hint = str(it.get("region") or "").lower()
        m = _region(region_hint, ctx)
        if m is None:
            continue
        m = m & ~glass
        if not m.any():
            continue
        cover = max(0.02, min(1.0, float(it.get("cover", 0.30) or 0.30)))
        want = max(1, int(round(cover * int(m.sum()))))
        mat_name = str(it.get("mat") or "")
        # "-0.30" / "+0.07" 是相对当前方块的明度偏移，AI 可以直接写"暗一档"
        relative = mat_name.startswith(("+", "-"))
        fixed = None if relative else _blk(mat_name)
        if not relative and fixed is None:
            continue
        dl = 0.0
        if relative:
            try:
                dl = max(-0.6, min(0.5, float(mat_name)))
            except ValueError:
                continue
        if cover >= 0.9:
            # 连续带（水线、檐口阴影）：整片换，不能留洞
            cells = [tuple(int(v) for v in c) for c in np.argwhere(m)]
        elif region_hint == "below_windows":
            # 窗下的污渍是**竖着**淌下来的，不是一块块斑点
            cells = _runs(m, rng, want, amin=2, amax=5, axis=1)
        elif str(it.get("shape") or PLATE_REGIONS.get(region_hint, "blob")) == "plate":
            cells = _runs(m, rng, want)
        else:
            cells = _blobs(m, rng, want)
        for x, y, z in cells:
            cur = int(vox[x, y, z])
            if cur < 0 or cur >= len(builder.blocks):
                continue
            blk = _variant(builder.blocks[cur], dl) if relative else fixed
            if blk is None or blk is builder.blocks[cur]:
                continue
            builder.put(x, y, z, blk)
            n += 1
    return n


# --------------------------------------------------------------------------
# 小件堆叠
# --------------------------------------------------------------------------
def _fill(builder, x: int, y: int, z: int, w: int, h: int, d: int, blk) -> int:
    n = 0
    for i in range(max(1, w)):
        for j in range(max(1, h)):
            for k in range(max(1, d)):
                builder.put(x + i, y + j, z + k, blk)
                n += 1
    return n


SHIP_EQUIP = ("concrete:7", "concrete:8", "iron_block", "stonebrick", "stained_hardened_clay:9")
SHIP_LIGHT = ("glowstone", "sea_lantern")


def _ship_props(builder, spec: "Spec", rng: random.Random, tops: np.ndarray, budget: int) -> int:
    """甲板小件：设备箱成组、天线、救生筒、格栅。

    舰船甲板是"空"的，所以小件最容易出效果；但也最容易假 ——
    一旦左右镜像排列，立刻像玩具。这里整组只挑一侧、间距随机。
    """
    deck_y = _mode(tops[tops >= 0])
    if deck_y < 0:
        return 0
    free = (tops == deck_y)
    xs, zs = np.nonzero(free)
    cells = list(zip(xs.tolist(), zs.tolist()))
    if not cells:
        return 0
    rng.shuffle(cells)
    bars = _blk("iron_bars")
    glass_light = [b for b in (_blk(n) for n in SHIP_LIGHT) if b]
    n = 0
    for _ in range(len(cells)):
        if n >= budget or not cells:
            break
        x, z = cells.pop()
        w = rng.choice((1, 1, 2, 2, 3))
        d = rng.choice((1, 1, 2))
        foot = [(x + i, z + j) for i in range(w) for j in range(d)]
        if not all(
            0 <= i < free.shape[0] and 0 <= j < free.shape[1] and free[i, j] for i, j in foot
        ):
            continue
        # 上面已经被占了（栏杆、上建、前一批小件）
        if any(builder.vox[i, deck_y + 1, j] >= 0 for i, j in foot if i < builder.vox.shape[0] and j < builder.vox.shape[2]):
            continue
        r = rng.random()
        if r < 0.15 and bars:
            # 天线 / 桅杆：细杆 + 顶端红灯
            h = rng.randint(2, 4)
            n += _fill(builder, x, deck_y + 1, z, 1, 1, 1, _blk("iron_block"))
            n += _fill(builder, x, deck_y + 2, z, 1, h - 1, 1, bars)
            if rng.random() < 0.5:
                n += _fill(builder, x, deck_y + 1 + h, z, 1, 1, 1, _blk("wool:14"))
        elif r < 0.30:
            # 救生筒：白色/橙色圆柱，竖着靠栏杆
            n += _fill(builder, x, deck_y + 1, z, 1, 2, 1, _blk(rng.choice(("concrete:0", "wool:0", "concrete:1"))))
        elif r < 0.42 and glass_light:
            # 甲板灯：海晶灯一块，嵌在设备箱边上
            n += _fill(builder, x, deck_y + 1, z, 1, 1, 1, rng.choice(glass_light))
        elif r < 0.72:
            # 设备箱：2 格高，成组出现（同材质，才不像随机噪点）
            mat = _blk(rng.choice(SHIP_EQUIP))
            n += _fill(builder, x, deck_y + 1, z, w, 2, d, mat)
        else:
            # 矮台 / 格栅：1 格高，压扁的体块
            mat = _blk(rng.choice(SHIP_EQUIP))
            n += _fill(builder, x, deck_y + 1, z, w, 1, d, mat)
    return n


def _building_props(builder, spec: "Spec", rng: random.Random, tops: np.ndarray, budget: int) -> int:
    """建筑外挂件：屋顶水箱 / 排气管 / 窗下外机 / 花箱 / 壁灯。"""
    n = 0
    solid = builder.vox >= 0
    y1 = int(np.argwhere(solid)[:, 1].max()) if solid.any() else 0

    # ---- 屋顶：只放在平的地方 ----
    # 判"平"不能要求四邻同高：女儿墙走道、孟莎顶的台地往往只有 1~2 格宽，
    # 四邻全平的条件会把它们全排掉，屋顶就永远是空的（实测）。
    t = tops
    same = (
        (t == np.roll(t, 1, 0)).astype(np.int8)
        + (t == np.roll(t, -1, 0)).astype(np.int8)
        + (t == np.roll(t, 1, 1)).astype(np.int8)
        + (t == np.roll(t, -1, 1)).astype(np.int8)
    )
    flat = (t >= 0) & (same >= 2)
    xs, zs = np.nonzero(flat)
    cells = list(zip(xs.tolist(), zs.tolist()))
    rng.shuffle(cells)
    tank = _blk("concrete:7")
    vent = _blk("iron_block")
    bars = _blk("iron_bars")
    roof_budget = int(budget * 0.45)
    # 顶面平处就那么多，不设上限会在平台中央堆成一堆箱子；
    # 每 40 格平顶放 1 件，最多 30 件。
    # 顶面越大越要克制：一整片屋顶上撒 30 个箱子，远看像散落的积木。
    # 每 60 格平顶放 1 件，最多 12 件 —— 商业建筑的屋顶本来就干净。
    roof_cap = max(3, min(12, int(flat.sum() // 60)))
    rn = 0
    placed = 0
    for x, z in cells:
        if rn >= roof_budget or placed >= roof_cap:
            break
        y = int(t[x, z])
        # 只认"上层平台"：别墅的露台、侧翼屋顶也该有东西，所以留 12 格余量；
        # 再低就是窗台/雨棚了，把水箱放上去会比没有还假。
        if y < y1 - 12:
            continue
        if y + 1 >= builder.vox.shape[1] or builder.vox[x, y + 1, z] >= 0:
            continue
        # 相邻格已经有小件就跳过：两件紧贴着会连成一大坨，
        # 屋顶设备真正好看的地方是"散得开、间距不等"。
        busy = False
        for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (-1, -1)):
            xx, zz = x + dx, z + dz
            if 0 <= xx < t.shape[0] and 0 <= zz < t.shape[1]:
                yy = int(t[xx, zz])
                if 0 <= yy + 1 < builder.vox.shape[1] and builder.vox[xx, yy + 1, zz] >= 0:
                    busy = True
                    break
        if busy:
            continue
        r = rng.random()
        add = 0
        if r < 0.22 and tank:
            # 2×2 的水箱要确认四格都平，不然会有半截悬空
            if all(
                0 <= x + i < t.shape[0] and 0 <= z + j < t.shape[1] and flat[x + i, z + j] and t[x + i, z + j] == y
                for i in (0, 1) for j in (0, 1)
            ):
                add += _fill(builder, x, y + 1, z, 2, 2, 2, tank)
                if bars and rng.random() < 0.6:
                    add += _fill(builder, x, y + 3, z, 1, 1, 1, bars)
        elif r < 0.60 and vent:
            h = rng.randint(1, 2)
            add += _fill(builder, x, y + 1, z, 1, h, 1, vent)
            if bars and rng.random() < 0.6:
                add += _fill(builder, x, y + 1 + h, z, 1, 1, 1, bars)
        else:
            # 屋面设备箱 / 楼梯间小体块
            add += _fill(builder, x, y + 1, z, 1, 1, 1, _blk("stonebrick") or tank)
        if add:
            rn += add
            placed += 1
    n += rn

    # ---- 墙面：窗下外机 / 花箱 / 窗上壁灯 ----
    # 先找玻璃列的底端，再按"体块边界"判朝外方向 —— 空心体块里面也是空的，
    # 只看"旁边有没有空气"会把外机挂到室内去。
    #
    # 但**幕墙店面没有"窗"**：整个立面就是一整片玻璃。它没有窗台，
    # 挂一排空调外机和花箱只会把漂亮的玻璃墙戳得千疮百孔。
    # 判据用蓝图：没写 windows 就是幕墙/整片玻璃，跳过这一类。
    if not spec.windows:
        return n
    glass_idx = [i for i, b in enumerate(builder.blocks) if "glass" in b.name_en]
    if not glass_idx:
        return n
    glass = np.isin(builder.vox, glass_idx)
    bottom: dict[tuple[int, int], int] = {}
    for x, y, z in np.argwhere(glass):
        k = (int(x), int(z))
        if k not in bottom or y < bottom[k]:
            bottom[k] = int(y)

    def outward(x: int, y: int, z: int) -> tuple[int, int] | None:
        for v in spec.volumes:
            if v.x <= x <= v.x1 and v.z <= z <= v.z1 and v.y <= y <= v.y1:
                if x == v.x:
                    return -1, 0
                if x == v.x1:
                    return 1, 0
                if z == v.z:
                    return 0, -1
                if z == v.z1:
                    return 0, 1
        return None

    ac_mat = _blk("iron_block")
    dirt = _blk("dirt")
    leaf = _blk("leaves")
    lamp = _blk("glowstone")
    keys = list(bottom.items())
    rng.shuffle(keys)
    for (x, z), yb in keys:
        if n >= budget:
            break
        if rng.random() > 0.42:
            continue
        d = outward(x, yb, z)
        if d is None:
            continue
        ox, oz = d
        px, pz = x + ox, z + oz
        if not (0 <= px < builder.vox.shape[0] and 0 <= pz < builder.vox.shape[2]):
            continue
        if 0 <= yb - 1 and builder.vox[px, yb - 1, pz] >= 0:
            continue  # 那个位置已经有东西（檐口、线脚），别叠上去
        r = rng.random()
        if r < 0.45 and ac_mat:
            # 空调外机：窗下 1 行，横向 2 格，只外挑 1 格。
            # 一格铁块一格深灰混凝土 —— 单色方块在立面上像块补丁，双色才像机器。
            n += _fill(builder, px, yb - 1, pz, 1, 1, 1, ac_mat)
            px2, pz2 = px, pz
            if ox:
                px2, pz2 = px, pz + rng.choice((-1, 1))
            else:
                px2, pz2 = px + rng.choice((-1, 1)), pz
            n += _fill(builder, px2, yb - 1, pz2, 1, 1, 1, _blk("concrete:7") or ac_mat)
        elif r < 0.70 and dirt and leaf:
            # 花箱：泥土一行 + 上面一株树叶，宽度随机（不对称）
            n += _fill(builder, px, yb - 1, pz, 1, 1, 1, dirt)
            n += _fill(builder, px, yb, pz, 1, 1, 1, leaf)
        elif lamp:
            # 壁灯：**嵌进墙里**（替换窗顶那一格墙），不要外挂一个黄方块 ——
            # 一块 1 米见方的发光体浮在立面上，比没有灯还假。
            ty = yb + 1 + rng.choice((0, 1))
            if 0 <= ty < builder.vox.shape[1] and builder.vox[x, ty, z] >= 0 and not glass[x, ty, z]:
                builder.put(x, ty, z, lamp)
                n += 1
    return n


# --------------------------------------------------------------------------
def apply(builder) -> dict:
    """入口：把观赏性细节铺到已经建好的体素上。"""
    spec = builder.spec
    if not getattr(spec, "ornament", False):
        return {"patch": 0, "prop": 0}
    vox = builder.vox
    if not (vox >= 0).any():
        return {"patch": 0, "prop": 0}
    rng = random.Random(_seed(spec))
    n_patch = _patch(builder, spec, rng)
    tops = _tops(vox)
    budget = max(14, min(420, int(int((vox >= 0).sum()) * 0.022)))
    n_prop = _ship_props(builder, spec, rng, tops, budget) if spec.subject == "ship" else _building_props(builder, spec, rng, tops, budget)
    return {"patch": n_patch, "prop": n_prop}
