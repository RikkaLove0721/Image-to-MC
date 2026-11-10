"""蓝图 spec：用一份紧凑的 JSON 描述建筑，本地构建成体素。

为什么要有这一层：

    照片直接转体素，能拿到的只有"某一列像素是什么颜色"，拿不到"这是主楼、
    那是侧翼、这排窗开在二层"。所以稍复杂的建筑一律变成火柴盒。
    而让人或 AI 直接吐体素更不现实 —— 一栋 40x20x16 的建筑就是一万多个
    方块，token 根本放不下。

    折中办法是把"设计意图"压缩成几十行的 spec：
    几个体块、几排窗、屋顶形式、材质分工，剩下的由本模块展开成方块。
    AI 只需要写 spec（几百 token），构建全在本地，这就是省 token 的关键。

坐标（与 Minecraft 一致）：X 向东、Y 向上、Z 向南。
spec 里所有尺寸都是格数；`volumes` 的 x/y/z 是体块的西北下角。

spec 形状（字段都可选，除了 volumes）：

    {
      "name": "villa",
      "palette": {"wall": "sandstone", "trim": "concrete:7",
                  "roof": "hardened_clay", "glass": "stained_glass:11",
                  "accent": "dark_oak_planks", "floor": "stonebrick"},
      "volumes": [
        {"x":0,"y":0,"z":0,"w":26,"h":18,"d":14,"mat":"wall","floors":3},
        {"x":26,"y":0,"z":4,"w":12,"h":12,"d":10,"mat":"wall","floors":2}
      ],
      "windows": [
        {"face":"south","volume":0,"u":3,"v":3,"w":4,"h":5,
         "cols":4,"rows":3,"gap_u":2,"gap_v":3,"mat":"glass","frame":1}
      ],
      "roof": {"type":"flat","parapet":1,"mat":"roof"},
      "details": [{"type":"balcony","face":"south","volume":0,"u":6,"v":12,"w":8,"mat":"accent"},
                  {"type":"chimney","volume":0,"u":20,"w":2}]
    }
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

import numpy as np

from . import ornament, palette
from .palette import Block

FACES = ("north", "south", "east", "west")

# 单边最大格数。再宽就不是一栋建筑，是一段城墙了 —— 生存模式里也没人铺得完。
MAX_SPAN = 88
# 每个体块每层的可用高度区间。低于 3 进不去，高于 6 空旷得像仓库。
FLOOR_H = (3, 6)


# --------------------------------------------------------------------------
# 材质
# --------------------------------------------------------------------------

# 常见的"组合名"写法（AI 会这么写），映射到 1.12.2 的 name_en + meta
ALIASES = {
    "oak_planks": ("planks", 0),
    "spruce_planks": ("planks", 1),
    "birch_planks": ("planks", 2),
    "jungle_planks": ("planks", 3),
    "acacia_planks": ("planks", 4),
    "dark_oak_planks": ("planks", 5),
    "white_concrete": ("concrete", 0),
    "light_gray_concrete": ("concrete", 8),
    "gray_concrete": ("concrete", 7),
    "black_concrete": ("concrete", 15),
    "glass_pane": ("stained_glass_pane", 0),
    "pane": ("stained_glass_pane", 0),
    "glass_block": ("glass", 0),
    # 石英三兄弟：柱子用竖纹，收边用平滑，重点面用錾制
    "quartz": ("quartz_block", 0),
    "quartz_pillar": ("quartz_pillar", 0),
    "quartz_column": ("quartz_pillar", 0),
    "chiseled_quartz_block": ("chiseled_quartz_block", 0),
    "smooth_quartz": ("smooth_quartz", 0),
    "grass_block": ("grass", 0),
    "grass": ("grass", 0),
    "tall_grass": ("tallgrass", 1),
    "fern": ("tallgrass", 2),
    "dandelion": ("yellow_flower", 0),
    "poppy": ("red_flower", 0),
    "sealantern": ("sea_lantern", 0),
    "glowstone_block": ("glowstone", 0),
    "lamp": ("redstone_lamp", 0),
    "white_wool": ("wool", 0),
    "terracotta": ("hardened_clay", 0),
}


def pane_of(blk: Block) -> Block | None:
    """玻璃块 → 对应的玻璃板；不是玻璃就返回 None。"""
    if blk.name_en == "stained_glass_pane":
        return blk
    if blk.name_en == "glass":
        return palette.find("stained_glass_pane", 0)
    if blk.name_en == "stained_glass":
        return palette.find("stained_glass_pane", blk.meta)
    return None


def glass_skin(glass: Block, item: dict, default_pane: bool = True) -> Block:
    """窗户/幕墙默认用**玻璃板**，不是整块玻璃。

    现实里绝大多数玻璃是几毫米的薄片，只有采光顶、玻璃盒建筑、
    防弹/水族馆那种厚重玻璃才是一整块。MC 社区做窗也是用板：
    板占 1/16 厚、会自动连成一整片，贴在墙上就是"窗"；
    用整块玻璃会把洞口填死，从外面看是一堵浅蓝色的墙。

    `item` 里写 `"thick": true`（或 `"glass": "block"`）才用整块玻璃 ——
    那是玻璃天花板 / 幕墙盒 / 防护玻璃的写法。
    """
    if not default_pane or not isinstance(glass, Block):
        return glass
    thick = item.get("thick")
    if isinstance(thick, str):
        thick = thick.strip().lower() in ("1", "true", "yes", "on")
    if thick:
        return glass
    if str(item.get("glass") or "").strip().lower() in ("block", "full", "solid"):
        return glass
    p = pane_of(glass)
    return p or glass


def parse_material(spec_value, default: str = "stone") -> Block:
    """接受 "名称"、"名称:meta"、"id:meta" 三种写法。"""
    if isinstance(spec_value, Block):
        return spec_value
    s = str(spec_value or default).strip()
    if s in ALIASES:
        name, meta = ALIASES[s]
        try:
            return palette.find(name, meta)
        except (StopIteration, KeyError):
            pass
    if ":" in s:
        a, b = s.split(":", 1)
        try:
            if a.isdigit():
                return palette.get(int(a), int(b))
            # 名称 + meta
            meta = int(b)
            same = [blk for blk in palette.BLOCKS if blk.name_en == a]
            for blk in same:
                if blk.meta == meta:
                    return blk
            if same:
                # 这个 meta 没收录（比如以前只收了 4 种彩色玻璃）：
                # 取同种里 meta 最接近的，不要掉进最后的"石头"兜底 ——
                # 那会让整栋楼的窗户变成实心石头，而且完全没有提示。
                return min(same, key=lambda x: abs(x.meta - meta))
        except (ValueError, KeyError):
            pass
    for blk in palette.BLOCKS:
        if blk.name_en == s:
            return blk
    # 中文名
    for blk in palette.BLOCKS:
        if blk.name_zh == s:
            return blk
    return palette.get(1, 0)  # 兜底：石头


# --------------------------------------------------------------------------
# spec 结构
# --------------------------------------------------------------------------

@dataclass
class Volume:
    x: int
    y: int
    z: int
    w: int
    h: int
    d: int
    mat: Block
    floors: int = 0
    floor_mat: Block | None = None
    hollow: bool = True
    # ---- 楔形收窄（舰艏 / 机鼻 / 任何"一头尖"的形体） ----
    # 建筑是立方体的堆叠，舰船不是：船体从舰艏往后逐渐放宽，这一点靠
    # "多写几个越来越小的方盒子"也能近似，但会得到一段明显的楼梯，
    # 而且每段都要占一个 volume id，窗阵、细节全对不上。
    # 这里给体块本身一个线性收窄：沿 taper_axis 走，越靠近 taper_end，
    # 横截面从满值收到 taper_to 格。taper_to=1 就是尖头。
    taper_axis: str = ""       # "" | "x" | "z"
    taper_end: str = "max"     # 收在 min 端还是 max 端
    taper_to: int = 1          # 末端横截面尺寸（格）
    taper_len: int = 0         # 收窄段的长度，0 = 整个体块

    @property
    def x1(self) -> int:
        return self.x + self.w - 1

    @property
    def y1(self) -> int:
        return self.y + self.h - 1

    @property
    def z1(self) -> int:
        return self.z + self.d - 1

    # ---- 楔形几何 ----
    def along_range(self) -> tuple[int, int]:
        """收窄轴的起止坐标（哪个轴长在 taper_axis 上）。"""
        if self.taper_axis == "z":
            return self.z, self.z1
        return self.x, self.x1

    def cross_range(self, along: int) -> tuple[int, int]:
        """沿收窄轴走到 along 时，横截面占据的区间。

        没有 taper 就是满截面；有 taper 就按到"尖端"的距离线性收窄，
        并且**保持居中** —— 舰艏、机鼻、船头都是左右对称的，
        偏到一边会立刻被看出是斜切。
        """
        if self.taper_axis == "z":
            c0, c1 = self.x, self.x1
        elif self.taper_axis == "x":
            c0, c1 = self.z, self.z1
        else:
            return self.x, self.x1

        full = c1 - c0 + 1
        lo, hi = self.along_range()
        length = self.taper_len if self.taper_len > 0 else (hi - lo + 1)
        length = max(1, min(length, hi - lo + 1))
        tip = hi if self.taper_end != "min" else lo
        dist = abs(tip - along)
        if dist >= length:
            return c0, c1
        t = 1.0 - dist / float(length)          # 0 = 还没开始收，1 = 尖端
        want = full + (max(1, self.taper_to) - full) * t
        cross = max(1, int(round(want)))
        cross = min(cross, full)
        mid = (c0 + c1) / 2.0
        a = int(round(mid - (cross - 1) / 2.0))
        b = a + cross - 1
        if a < c0:
            a, b = c0, c0 + cross - 1
        if b > c1:
            a, b = c1 - cross + 1, c1
        return a, b


@dataclass
class Spec:
    name: str = "model"
    palette: dict = field(default_factory=dict)
    # 主体类型：building（默认）/ ship / vehicle。
    # 舰船不是建筑：没有勒脚、没有檐口、没有楼层腰线，屋顶也是甲板。
    # 构建端按它决定"要不要补建筑构件"，体检端按它换一套量测标准。
    subject: str = "building"
    # 细节等级 1~4：尺寸预设越大，等级越高，构建完会额外补腰线、角柱等。
    # 4 = 观赏性细节：在最高精度之上再铺一层"人味"（小件堆叠 + 材质色差），
    # 见 mc12/ornament.py。等级 4 会自动打开 ornament。
    detail: int = 1
    ornament: bool = False
    # 小件配置（当前由主体类型自动决定放什么，这里只记录 AI 的偏好）
    ornaments: dict = field(default_factory=dict)
    # 材质替换表：[{"region":"below_windows","mat":"-0.14","cover":0.35}]
    patches: list[dict] = field(default_factory=list)
    # 照片里的高宽比（宽 ÷ 高），只作参考
    aspect: float = 0.0
    # 目标总宽 / 总高（米数 × 比例尺）。给 width 就等比缩放到该宽度
    target_width: int = 0
    target_height: int = 0
    # 复现用的种子：同样的 seed + 同样的蓝图 = 同样的模型
    seed: int = 0
    volumes: list[Volume] = field(default_factory=list)
    windows: list[dict] = field(default_factory=list)
    roof: dict = field(default_factory=dict)
    details: list[dict] = field(default_factory=list)
    meta: dict = field(default_factory=dict)

    @property
    def size(self) -> tuple[int, int, int]:
        if not self.volumes:
            return (1, 1, 1)
        w = max(v.x1 for v in self.volumes) + 1
        h = max(v.y1 for v in self.volumes) + 1
        d = max(v.z1 for v in self.volumes) + 1
        # 外扩一圈：阳台、雨棚、烟囱会探出体块外，尺寸不够会被直接丢掉
        # Y 方向还要留出屋顶：孟莎 / 四坡顶有 3~8 格高，按 4 格留会削顶。
        roof_h = int((self.roof or {}).get("height", 0) or 0)
        kind = str((self.roof or {}).get("type", "flat"))
        if kind in ("hip", "gable"):
            # 坡顶的高度会被"收到闭合"抬上去（见 roof()），预留也跟着抬，
            # 否则屋脊最上面两格会被尺寸裁掉，看着又变成平顶。
            need = max(
                ((min(v.w, v.d) if kind == "hip" else v.d) + 1) // 2 for v in self.volumes
            )
            roof_h = min(10, max(roof_h, need))
        # 旗杆是竖在甲板上的细杆，会高出体块一大截；不预留就会被裁成半截
        pole = max(
            (int(d.get("h", 8)) for d in self.details if str(d.get("type")) == "flagpole"),
            default=0,
        )
        if pole:
            roof_h = max(roof_h, pole + 1)
        # 观赏性细节里的天线/桅杆会从甲板（体块顶面）再往上竖 4~5 格，
        # 不预留就会被尺寸裁掉，只在旗杆旁边留一根断头柱子。
        if self.ornament:
            roof_h = max(roof_h, 6)
        return (w + 2, h + max(4, roof_h + 2), d + 2)


def _resolve(value, mats: dict, default: str) -> Block:
    """mat 可以写角色名（wall/trim/roof/glass/accent/floor），也可以写具体方块。

    没写 mat 时用 default 这个角色，别让它掉进 parse_material 的兜底石头里。
    """
    if isinstance(value, Block):
        return value
    if not value:
        value = default
    if isinstance(value, str) and value in mats:
        return mats[value]
    return parse_material(value, default)


def _sat(rgb: tuple[int, int, int]) -> float:
    mx, mn = max(rgb), min(rgb)
    return 0.0 if mx == 0 else (mx - mn) / mx


def _hue(rgb: tuple[int, int, int]) -> float | None:
    """色相角（0~360）。接近中性色时返回 None —— 灰的色相没有意义。"""
    r, g, b = (c / 255.0 for c in rgb)
    mx, mn = max(r, g, b), min(r, g, b)
    # 阈值要低：青色陶瓦 #575B5E 的饱和度只有 0.027，按 0.03 判会被当成
    # 中性色放过去，于是"白墙配青屋顶"完全拦不住（踩过）
    if mx - mn < 0.012:
        return None
    if mx == r:
        h = 60 * (((g - b) / (mx - mn)) % 6)
    elif mx == g:
        h = 60 * ((b - r) / (mx - mn) + 2)
    else:
        h = 60 * ((r - g) / (mx - mn) + 4)
    return h % 360


def _hue_gap(a: float | None, b: float | None) -> float:
    """两个色相的最小夹角；任一方是中性色就不算冲突。"""
    if a is None or b is None:
        return 0.0
    d = abs(a - b) % 360
    return min(d, 360 - d)


def _luma(rgb: tuple[int, int, int]) -> float:
    return (0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]) / 255.0


def _soften(mats: dict) -> dict:
    """线脚 / 屋顶不能比墙暗太多。

    AI 为了"对比明显"会挑一个很深的色：别墅那栋选了青色陶瓦 #575B5E，
    明度只有白墙的 38%。而线脚（勒脚 + 腰线 + 檐口 + 柱）占了立面三成以上，
    于是整栋白楼被染成青灰 —— 照片里的白几乎看不见了。

    真实建筑的石材线脚和墙面是同色系，只差一到两档。这里按明度设下限：
    线脚不低于墙的 58%，屋顶不低于 42%；超了就换成"墙色压暗到该明度"
    对应的方块，色相跟着墙走，不会突然冒出一块青色。
    """
    wall = mats.get("wall")
    if wall is None:
        return mats
    wl = _luma(wall.rgb)
    if wl < 0.05:
        return mats
    # 用全量可配色库，不用 dedupe：dedupe(8) 会把中性灰合并成一档，
    # 想给白墙配"浅灰线脚"时只剩一个中灰可选（实测屋顶被挤到青色陶瓦）
    cand = palette.all_blocks()
    out = dict(mats)
    wh = _hue(wall.rgb)
    for role, floor in (("trim", 0.72), ("roof", 0.45)):
        blk = mats.get(role)
        if blk is None:
            continue
        # 彩色屋顶是设计意图（绿琉璃瓦、铜皮、红瓦），不能按"压暗"处理 ——
        # 中式那栋就是靠绿琉璃瓦认出来的，把它压成灰色等于毁掉风格。
        if role == "roof" and _sat(blk.rgb) > 0.20:
            continue
        if blk is wall and role != "roof":
            continue  # 线脚与墙同色，只是看不出来，不必强行换
        too_dark = blk is not wall and _luma(blk.rgb) < wl * floor
        # 色相也要跟着墙：白墙配青陶瓦，明度差达标了照样把整栋楼染青
        off_hue = _hue_gap(_hue(blk.rgb), wh) > 60
        if not (too_dark or off_hue or (blk is wall and role == "roof")):
            continue
        target = np.clip(np.array(wall.rgb, dtype=np.float64) / 255.0 * floor, 0, 1)
        from .color import delta_e_00, rgb_to_lab

        ref = np.array([b.rgb for b in cand], dtype=np.float64) / 255.0
        d = delta_e_00(rgb_to_lab(ref), rgb_to_lab(target[None, :]))
        # 排序 = 色差 + 色相偏差惩罚。只按 ΔE 排的话，深灰候选全是冷色
        # （青陶瓦、青混凝土），一栋白楼的屋顶会被换成青色 —— 色相跑偏必须罚。
        picked = None
        for j in np.argsort(d):
            b = cand[int(j)]
            if b is wall:
                continue
            if _hue_gap(_hue(b.rgb), wh) <= 70:
                picked = b
                break
        if picked is None:
            score = d + np.array([_hue_gap(_hue(b.rgb), wh) for b in cand]) * 0.6
            for j in np.argsort(score):
                b = cand[int(j)]
                if b is not wall:
                    picked = b
                    break
        if picked is not None and picked is not out.get("trim"):
            out[role] = picked

    # 窗户必须看得出来。白色玻璃配白色石英墙 = 整面墙像没开窗
    # （AI 写 stained_glass:0 时就这样，窗户全糊进墙里）。
    g = out.get("glass")
    if g is not None and g.id in (20, 95) and abs(_luma(g.rgb) - wl) < 0.12:
        out["glass"] = palette.get(95, 11)  # 蓝色玻璃
    return out


def loads(raw: dict) -> Spec:
    """把 dict 解析成 Spec（宽容：缺字段就给默认值）。"""
    pal = raw.get("palette") or {}
    defaults = {
        "wall": "sandstone",
        "trim": "concrete:7",
        "roof": "hardened_clay",
        "glass": "stained_glass:11",
        "accent": "dark_oak_planks",
        "floor": "stonebrick",
    }
    mats = {k: parse_material(pal.get(k, v)) for k, v in defaults.items()}
    mats = _soften(mats)

    # ---- 主体类型（先定，缩放和构建都要用）----
    # 舰船/载具不是建筑。以前所有蓝图都走建筑流程：补勒脚、补檐口、
    # 按层高卡高度、按开间数开窗 —— 一艘军舰被这套规则改造成"长条写字楼"。
    subject_raw = str(
        raw.get("subject") or (raw.get("building") or {}).get("subject") or ""
    ).strip().lower()
    if (
        subject_raw in ("ship", "vessel", "warship", "boat")
        or "舰" in subject_raw
        or "船" in subject_raw
    ):
        subject = "ship"
    elif subject_raw in ("vehicle", "car", "plane", "aircraft", "train"):
        subject = "vehicle"
    else:
        subject = "building"

    vols: list[Volume] = []
    for item in raw.get("volumes") or []:
        mat = _resolve(item.get("mat"), mats, "wall")
        tp = item.get("taper") or {}
        taper_axis = str(tp.get("axis", "")).lower() if isinstance(tp, dict) else ""
        if taper_axis not in ("x", "z"):
            taper_axis = ""
        vols.append(
            Volume(
                x=int(item.get("x", 0)),
                y=int(item.get("y", 0)),
                z=int(item.get("z", 0)),
                w=max(1, int(item.get("w", 8))),
                h=max(1, int(item.get("h", 6))),
                d=max(1, int(item.get("d", 8))),
                mat=mat,
                floors=int(item.get("floors", 0)),
                floor_mat=mats["floor"],
                hollow=bool(item.get("hollow", True)),
                taper_axis=taper_axis,
                taper_end=str(tp.get("end", "max")) if taper_axis else "max",
                taper_to=max(1, int(tp.get("to", 1) or 1)) if taper_axis else 1,
                taper_len=max(0, int(tp.get("len", 0) or 0)) if taper_axis else 0,
            )
        )
    if not vols:
        vols = [Volume(0, 0, 0, 12, 8, 10, mats["wall"], floors=2, floor_mat=mats["floor"])]

    windows = [dict(w) for w in (raw.get("windows") or [])]
    details = [dict(d) for d in (raw.get("details") or [])]
    aspect = float(raw.get("aspect") or 0)

    # ---- 尺寸归一：**只允许一个系数**，X / Y / Z 一起走 ----
    # 旧版把 target_width 和 target_height 当成两个独立指标，等于对模型做
    # 非等比变形。一张大宫殿照片（蓝图自身体量 72×12、比例 6.0）被要求
    # "宽 32、高 24"，于是一边压缩 0.44、一边拉伸 2.0，最终输出 32×24 的
    # 方盒子 —— 这就是"大宫殿做出来还是一栋大房子"的直接原因。
    # 现在宽度和高度共用一个 k，蓝图写成什么比例，模型就是什么比例；
    # 高度只受"层高 3~6 格"这条**结构**约束，不再由照片估算的米数拉伸。
    target_w = int(raw.get("target_width") or 0)
    target_h = int(raw.get("target_height") or 0)
    if vols:
        span_x = max(v.x1 for v in vols) + 1 - min(v.x for v in vols)
        span_z = max(v.z1 for v in vols) + 1 - min(v.z for v in vols)
        span_h = max(v.y1 for v in vols) + 1 - min(v.y for v in vols)
        # 舰船的主轴是船长（Z），不是船宽（X）。按船宽缩放会让
        # "80 格的船要 60 格宽"直接放大四倍，船长冲出上限。
        span = span_z if subject == "ship" else span_x
        # 请求宽度只用来**放大**（或小幅收窄），不拿它把一栋大楼压小。
        # AI 的"米数"是它照着体块反推的，经常把大建筑估小一半；而它写的
        # volumes 反而更接近它自己想要的体量。以前无脑按 target_width 缩放，
        # 一栋 51 格宽的宫殿被按 33 格重建，用户看到的就是"越来越小的大房子"。
        want = span
        if target_w > 0 and target_w >= 0.8 * span:
            want = target_w
        k = want / max(span, 1)
        # 高度反向约束：等比放大后如果比 target_height 高太多，说明照片里
        # 这栋楼没这么高，把 k 压回来（宽度一起缩，比例不变）
        if target_h > 0 and span_h > 0 and span_h * k > target_h * 1.6:
            k = target_h * 1.6 / span_h
        # 缩放幅度有边界：缩太小没细节，放太大一眼假
        k = min(2.0, max(0.55, k))
        if span * k > MAX_SPAN:
            k = MAX_SPAN / span
        if abs(k - 1.0) > 0.04:
            x0 = min(v.x for v in vols)
            y0 = min(v.y for v in vols)
            z0 = min(v.z for v in vols)
            for v in vols:
                v.x = x0 + int(round((v.x - x0) * k))
                v.y = y0 + int(round((v.y - y0) * k))
                v.z = z0 + int(round((v.z - z0) * k))
                v.w = max(3, int(round(v.w * k)))
                v.h = max(3, int(round(v.h * k)))
                v.d = max(3, int(round(v.d * k)))
                if v.taper_axis:
                    # 收窄段和末端截面是几何的一部分，跟着一起缩，
                    # 不然放大后舰艏会变成"前面突然少一截"的断头。
                    v.taper_len = max(0, int(round(v.taper_len * k)))
                    v.taper_to = max(1, int(round(v.taper_to * k)))
            # 窗本身的尺寸不缩：2 格宽的窗缩成 1 格就只剩一块玻璃了。
            # 只缩窗阵的**位置**与节距，列数按新墙宽重算。
            for w in windows:
                w["u"] = max(0, int(round(int(w.get("u", 0)) * k)))
                w["v"] = max(0, int(round(int(w.get("v", 0)) * k)))
                w["gap_u"] = max(1, int(round(int(w.get("gap_u", 2)) * k)))
                w["gap_v"] = max(1, int(round(int(w.get("gap_v", 2)) * k)))
                w["cols"] = max(1, min(16, int(round(int(w.get("cols", 1)) * k))))
            for d in details:
                for key in ("u", "v", "depth"):
                    if key in d:
                        d[key] = max(0, int(round(int(d[key]) * k)))
                if "w" in d:
                    d["w"] = max(2, int(round(int(d["w"]) * k)))
                # 竖向高度跟着一起缩。以前只缩 v 不缩 h：一块 8 格高的幕墙
                # 放进 0.6 倍缩小的楼里，还是 8 格 —— 直接顶穿屋顶，
                # 上层的招牌带、女儿墙全被它盖掉。
                # 旗杆例外：桅杆高度是"旗要挂多高"，不是比例，缩了就变成短杆。
                if "h" in d and str(d.get("type")) != "flagpole":
                    d["h"] = max(1, int(round(int(d["h"]) * k)))

    # ---- 结构约束：层高必须落在 3~6 格 ----
    # 这是"能不能住"的问题，不是审美问题：3 格以下玩家进不去，6 格以上
    # 一层楼空的像仓库。只动 Y，不动 X/Z，所以横向比例不受影响。
    #
    # 但**立面程序优先于层高**。商业立面写的是一条竖向序列：
    # 勒脚 2 + 通高玻璃 8 + 檐口带 1 + 招牌带 2 + 上层玻璃 2 = 15 格，
    # 而 AI 常常同时写 floors=2（上限 12 格）。按 2 层砍到 12，
    # 砍掉的正好是"上层玻璃"整层 —— 层次没了一半，看着像加厚的平房。
    # 这不是房子矮，是 floors 写少了：先把层数补够，再按层高约束收。
    need: dict[int, int] = {}
    for d in details:
        if d.get("v") is None:
            continue
        idx = int(d.get("volume") or 0)
        top = int(d["v"]) + max(1, int(d.get("h") or 1))
        need[idx] = max(need.get(idx, 0), top)
    for i, v in enumerate(vols):
        floors = int(v.floors or 0)
        if floors <= 0:
            continue
        nd = max(need.get(i, 0), v.h)
        # eff 只用来放宽"层高上限"，**不改 floors 本身**：
        # floors 决定楼板铺在哪几层，改了会把楼板铺到 5/10/15 的怪位置。
        eff = max(floors, (nd + FLOOR_H[1] - 1) // FLOOR_H[1])
        lo, hi = floors * FLOOR_H[0], eff * FLOOR_H[1]
        if v.h < lo:
            v.h = lo
        elif v.h > hi:
            v.h = hi

    # 坐标可能带负值（AI 常把主楼放在 x=0、两侧翼放成负数）。
    # 体素数组从 0 开始，不归一化的话左翼会被裁掉，楼层板还会越界崩掉。
    if vols:
        x0 = min(v.x for v in vols)
        y0 = min(v.y for v in vols)
        z0 = min(v.z for v in vols)
        if x0 or y0 or z0:
            for v in vols:
                v.x -= x0
                v.y -= y0
                v.z -= z0

    # 尺寸以**归一之后**的真实体量为准回报，别把请求值当成结果值
    real_w = (max(v.x1 for v in vols) + 1) if vols else 0
    real_h = (max(v.y1 for v in vols) + 1) if vols else 0

    # ---- 主体类型 ----
    # 上面已经解析过了，这里只保留说明：舰船/载具走另一套收敛规则。

    # ---- 细节等级 / 观赏性细节 ----
    # 等级 4 不是"比 3 多补几条线脚"，而是另一层：小件堆叠 + 材质色差
    # （见 mc12/ornament.py）。它要求"最高精度"打底 —— 选了观赏性却只给
    # 等级 2，等于在毛坯墙上挂装饰，所以这里直接把等级抬到 4。
    detail_level = max(1, min(4, int(raw.get("detail") or 1)))
    orn_raw = raw.get("ornament")
    ornament = detail_level >= 4
    if isinstance(orn_raw, bool):
        ornament = ornament or orn_raw
    elif isinstance(orn_raw, str) and orn_raw.strip().lower() in ("1", "true", "yes", "on"):
        ornament = True
    if ornament:
        detail_level = max(detail_level, 4)

    return Spec(
        name=str(raw.get("name") or "model"),
        palette=mats,
        subject=subject,
        detail=detail_level,
        ornament=bool(ornament),
        ornaments=dict(raw.get("ornaments") or {}) if isinstance(raw.get("ornaments"), dict) else {},
        patches=[dict(x) for x in (raw.get("patches") or []) if isinstance(x, dict)][:6],
        aspect=aspect,
        target_width=real_w,
        target_height=real_h,
        seed=int(raw.get("seed") or 0) or random.SystemRandom().randint(1, 99999),
        volumes=vols,
        windows=windows,
        roof=dict(raw.get("roof") or {"type": "flat", "parapet": 1}),
        details=details,
        meta={
            "source": "spec",
            # 体检要用：不同类型的建筑，"够不够大"的标准完全不同
            "building_type": str((raw.get("building") or {}).get("type") or ""),
            "subject": subject,
        },
    )


# --------------------------------------------------------------------------
# 构建
# --------------------------------------------------------------------------

# 招牌上的图形：MC 里没有字体，社区做法是用少量方块"拼"出一个符号 ——
# 一个 M 只需要十几块，但它是"这是麦当劳/这是商铺"最强的识别信号，
# 比任何材质细节都管用。# 为图形，. 为底色。
GLYPHS: dict[str, list[str]] = {
    "m": ["#...#", "##.##", "#.#.#", "#...#", "#...#"],
    "arch": [".#.#.", "#####", "#####", "#...#"],
    "star": ["..#..", "..#..", "#####", "..#..", "..#.."],
    "ring": [".###.", "#...#", "#...#", "#...#", ".###."],
    "bars": ["#####", ".....", "#####", ".....", "#####"],
    "solid": ["#####", "#####", "#####", "#####", "#####"],
}


class _Builder:
    def __init__(self, spec: Spec):
        self.spec = spec
        self.blocks: list[Block] = []
        self.vox = np.full(spec.size, -1, dtype=np.int16)
        for blk in spec.palette.values():
            self._idx(blk)

    def _idx(self, blk: Block) -> int:
        if blk not in self.blocks:
            self.blocks.append(blk)
        return self.blocks.index(blk)

    def put(self, x: int, y: int, z: int, blk: Block) -> None:
        if 0 <= x < self.vox.shape[0] and 0 <= y < self.vox.shape[1] and 0 <= z < self.vox.shape[2]:
            self.vox[x, y, z] = self._idx(blk)

    def carve(self, x: int, y: int, z: int) -> None:
        if 0 <= x < self.vox.shape[0] and 0 <= y < self.vox.shape[1] and 0 <= z < self.vox.shape[2]:
            self.vox[x, y, z] = -1

    # ---- 体块 ----
    def volume(self, v: Volume) -> None:
        if v.taper_axis:
            self._volume_taper(v)
            return
        for x in range(v.x, v.x + v.w):
            for y in range(v.y, v.y + v.h):
                for z in range(v.z, v.z + v.d):
                    edge = (
                        x in (v.x, v.x1)
                        or z in (v.z, v.z1)
                        or y in (v.y, v.y1)
                    )
                    if edge or not v.hollow:
                        self.put(x, y, z, v.mat)
        self._floors(v)

    def _volume_taper(self, v: Volume) -> None:
        """带收窄的体块：一片一片地铺，每片的横截面由 Volume.cross_range 给。

        和普通体块的区别只有"这一片的边界在哪"，所以空心判定照旧
        （片内四边 + 顶底才是壳），楼梯感也自然出现在侧面 —— 那正是
        MC 里表达斜面的唯一手段。
        """
        lo, hi = v.along_range()
        for a in range(lo, hi + 1):
            c0, c1 = v.cross_range(a)
            for y in range(v.y, v.y + v.h):
                for c in range(c0, c1 + 1):
                    edge = (
                        c in (c0, c1)
                        or y in (v.y, v.y1)
                        or a in (lo, hi)
                    )
                    if not edge and v.hollow:
                        continue
                    if v.taper_axis == "z":
                        self.put(c, y, a, v.mat)
                    else:
                        self.put(a, y, c, v.mat)
        self._floors(v)

    def _floors(self, v: Volume) -> None:
        if v.floors and v.floor_mat is not None:
            step = max(2, v.h // max(1, v.floors))
            for k in range(1, v.floors + 1):
                y = v.y + k * step
                if y >= v.y1:
                    break
                for x in range(v.x, v.x + v.w):
                    for z in range(v.z, v.z + v.d):
                        if self.vox[x, y, z] < 0:
                            self.put(x, y, z, v.floor_mat)

    # ---- 窗阵 ----
    def window_band(self, item: dict) -> int:
        vi = int(item.get("volume", 0))
        if not (0 <= vi < len(self.spec.volumes)):
            return 0
        v = self.spec.volumes[vi]
        face = str(item.get("face", "south"))
        if face not in FACES:
            face = "south"
        glass = glass_skin(_resolve(item.get("mat"), self.spec.palette, "glass"), item)
        frame = int(item.get("frame", 0) or 0)
        bw = max(1, int(item.get("w", 2)))
        bh = max(1, int(item.get("h", 2)))
        cols = max(1, int(item.get("cols", 1)))
        rows = max(1, int(item.get("rows", 1)))
        gx = max(1, int(item.get("gap_u", 2)))
        gy = max(2, int(item.get("gap_v", 3)))
        u0 = int(item.get("u", 2))
        v0 = int(item.get("v", 2))
        # 窗阵要装得下：AI 常按"看着差不多"给列数，26 格宽的墙排 5 扇 4 格窗
        # 会溢出，最后一扇直接被丢掉。这里按墙长把列数收一收，再校正起点。
        span = v.w if face in ("south", "north") else v.d
        fit = max(1, (span - u0 + gx) // (bw + gx))
        cols = min(cols, fit)
        used = cols * bw + (cols - 1) * gx
        if u0 + used > span:
            u0 = max(0, span - used)
        count = 0
        for r in range(rows):
            for c in range(cols):
                u = u0 + c * (bw + gx)
                y = v0 + r * (bh + gy)
                count += self._punch(v, face, u, y, bw, bh, glass, frame,
                                     arch=bool(item.get("arch")))
        return count

    def _punch(self, v: Volume, face: str, u: int, y0: int, bw: int, bh: int,
               glass: Block, frame: int, arch: bool = False) -> int:
        """在立面 face 上开一个洞并填玻璃。u 是沿墙面横向的偏移。

        窗框做在**洞口外侧**：先把整块洞口挖开填玻璃，再在四周补一圈线脚。
        早版是把边框画在洞口内部，3×2 的小窗只剩 0 格玻璃，整扇窗变成一块石头。
        """
        n = 0
        for dy in range(bh):
            for du in range(bw):
                x, z, ok = self._face_pos(v, face, u + du, y0 + dy)
                if not ok:
                    continue
                self.carve(x, y0 + dy, z)
                self.put(x, y0 + dy, z, glass)
                n += 1
        trim = self.spec.palette["trim"]
        if arch and bh >= 3 and bw >= 3:
            # 拱窗：顶行收两角，远看就是拱顶（MC 做不出真圆弧，这是最省的成本）
            for du in (0, bw - 1):
                x, z, ok = self._face_pos(v, face, u + du, y0 + bh - 1)
                if ok:
                    self.put(x, y0 + bh - 1, z, trim)
                    n += 1
        if not frame:
            return n
        # 窗框只做两侧 + 窗台一行。
        # 早版四面都包一圈，层高 4 格时"窗 2 + 上下框 + 下排窗"会把每一行都占掉，
        # 整面墙变成灰色网格，砖墙一点都看不见。
        for dy in range(bh):
            for du in (-1, bw):
                x, z, ok = self._face_pos(v, face, u + du, y0 + dy)
                if ok:
                    self.put(x, y0 + dy, z, trim)
                    n += 1
        for du in range(bw):
            x, z, ok = self._face_pos(v, face, u + du, y0 - 1)
            if ok:
                self.put(x, y0 - 1, z, trim)
                n += 1
        return n

    # ---- 屋顶 ----

    @staticmethod
    def _mansard_inset(w: int, d: int, height: int, level: int) -> int:
        """孟莎屋面上第 level 层相对墙体内缩多少格。

        剖面是**两段式**，这就是孟莎屋顶和普通四坡顶的区别：
        下面 40% 的高度几乎垂直（每层只退 1 格，像一段立起来的墙），
        上面开始放缓（每层退 2 格），顶部留一块平屋面。
        早版用一条幂曲线，前两层的取整结果都是 0，屋顶看起来就是
        "一块平板压在墙上" —— 用户看到的就是"还是个大盒子"。
        """
        if height <= 0:
            return 0
        lim = max(1, int(min(w, d) * 0.34))
        steep = max(1, int(round(height * 0.45)))
        inset = 1.0  # 第一层先退 1 格：屋檐收进去，墙和屋顶才分得开
        for k in range(2, level + 1):
            inset += 1.0 if k <= steep else 1.8
        return int(min(lim, round(inset)))

    def roof(self) -> int:
        cfg = self.spec.roof or {}
        kind = str(cfg.get("type", "flat"))
        mat = _resolve(cfg.get("mat"), self.spec.palette, "roof")
        added = 0
        if kind == "flat":
            # 舰船的顶面就是甲板，不是女儿墙围出来的平屋面：
            # 默认不压那一圈墙，否则整艘船顶着一圈"屋顶护栏"，立刻变建筑。
            default_parapet = 0 if self.spec.subject != "building" else 1
            n = max(0, int(cfg.get("parapet", default_parapet) or 0))
            # 先把顶面换成屋面材料：体块是空心的，顶面原来是墙色，
            # 远看就是一块"粉色大平台"，跟屋顶完全没有关系。
            for v in self.spec.volumes:
                for x in range(v.x, v.x1 + 1):
                    for z in range(v.z, v.z1 + 1):
                        if self.vox[x, v.y1, z] >= 0:
                            # 顶层的幕墙/天窗不换屋面材料。整层统一铺屋面板
                            # 会把"上层玻璃"那一条也变成灰墙 —— 从正面看，
                            # 一栋通高玻璃的店面上半截突然变实了。
                            prev = int(self.vox[x, v.y1, z])
                            if "glass" in self.blocks[prev].name_en:
                                continue
                            self.put(x, v.y1, z, mat)
                            added += 1
            if n == 0:
                return added
            for v in self.spec.volumes:
                for k in range(1, n + 1):
                    y = v.y1 + k
                    for x in range(v.x, v.x + v.w):
                        for z in range(v.z, v.z + v.d):
                            if x in (v.x, v.x1) or z in (v.z, v.z1):
                                self.put(x, y, z, mat)
                                added += 1
            return added

        if kind == "mansard":
            # 法式孟莎屋顶：宫殿、府邸、老式公寓的标配。
            # 平顶把这类建筑做成一栋办公楼，这是"看起来还是大房子"的第二个原因。
            h = max(2, min(8, int(cfg.get("height", 4))))
            for v in self.spec.volumes:
                for k in range(1, h + 1):
                    inset = self._mansard_inset(v.w, v.d, h, k)
                    zz0, zz1 = v.z + inset, v.z1 - inset
                    xx0, xx1 = v.x + inset, v.x1 - inset
                    y = v.y1 + k
                    if zz1 < zz0 or xx1 < xx0:
                        break
                    # 只铺外圈，保持空心；最后一层铺满，把屋顶封住
                    solid = k == h
                    for x in range(xx0, xx1 + 1):
                        for z in range(zz0, zz1 + 1):
                            if solid or x in (xx0, xx1) or z in (zz0, zz1):
                                self.put(x, y, z, mat)
                                added += 1
            return added

        # gable / hip：逐层收
        h = max(1, int(cfg.get("height", 3)))
        for v in self.spec.volumes:
            # 坡顶必须**收到闭合**。只按 AI 写的 height 收（常见 4 格），
            # 在 14 格进深上会留下 8×6 的平台 —— 视觉模型一眼就报"这是平顶加厚檐口"，
            # 那是实情。所以收的层数取"够闭合"和"AI 写的"里的大者，上限 10。
            if kind == "hip":
                short = min(v.w, v.d)
                need = (short + 1) // 2
            else:
                need = (v.d + 1) // 2
            hh = min(10, max(h, need))
            # hip 沿短边收满、长边按同比例收，才能收出一条屋脊，
            # 而不是把一排长楼收成一个金字塔尖。
            if kind == "hip":
                kx = (v.w / 2.0) / max(1.0, short / 2.0)
                kz = (v.d / 2.0) / max(1.0, short / 2.0)
            else:
                kx = kz = 1.0
            for k in range(1, hh + 1):
                zz0, zz1 = v.z + int(round(k * kz)), v.z1 - int(round(k * kz))
                xx0, xx1 = v.x + int(round(k * kx)), v.x1 - int(round(k * kx))
                y = v.y1 + k
                if zz1 < zz0 or xx1 < xx0:
                    zc = (v.z + v.z1) // 2
                    for x in range(xx0, xx1 + 1):
                        self.put(x, y, zc, mat)
                        added += 1
                    break
                for x in range(xx0, xx1 + 1):
                    for z in range(zz0, zz1 + 1):
                        self.put(x, y, z, mat)
                        added += 1
        return added

    # ---- 立面定位 ----

    def _face_pos(self, v: Volume, face: str, u: int, y: int) -> tuple[int, int, bool]:
        """把 (face, 沿墙偏移 u, 高度 y) 换成世界坐标 (x, z, 是否在立面内)。"""
        if face in ("south", "north"):
            x = v.x + u
            z = v.z1 if face == "south" else v.z
            ok = v.x <= x <= v.x1 and v.y <= y <= v.y1
        else:
            z = v.z + u
            x = v.x1 if face == "east" else v.x
            ok = v.z <= z <= v.z1 and v.y <= y <= v.y1
        return x, z, ok

    @staticmethod
    def _shift(face: str, out: int) -> tuple[int, int]:
        """立面朝外的方向（往街上）偏移 out 格的 (dx, dz)。"""
        if face == "south":
            return 0, out
        if face == "north":
            return 0, -out
        if face == "east":
            return out, 0
        return -out, 0

    def _column_at(self, v: Volume, face: str, uu: int, out: int, y0: int,
                   height: int, mat: Block) -> int:
        """一根贴墙的柱子：柱础 + 柱身 + 沿立面加宽的柱头。"""
        dx, dz = self._shift(face, out)
        added = 0
        for dy in range(1, height + 1):
            x, z, ok = self._face_pos(v, face, uu, y0 + dy)
            if ok:
                self.put(x + dx, y0 + dy, z + dz, mat)
                added += 1
        x, z, ok = self._face_pos(v, face, uu, y0)
        if ok:
            self.put(x + dx, y0, z + dz, mat)
            added += 1
        top = y0 + height
        for du in (-1, 0, 1):
            x, z, ok = self._face_pos(v, face, uu + du, top)
            if ok:
                self.put(x + dx, top, z + dz, mat)
                added += 1
        return added

    # ---- 细节 ----

    # 结构性的先做，开口类后做，免得勒脚/檐口把门窗盖掉
    DETAIL_ORDER = {
        "plinth": 0,
        "cornice": 1,
        "column": 2,
        "arcade": 3,
        "portico": 4,
        "pediment": 5,
        "curtain_wall": 6,
        "door": 7,
        "steps": 8,
        "balcony": 9,
        "canopy": 10,
        # 现代商业立面的三件套：水平饰带 → 竖向格栅 → 花池。
        # 它们要压在幕墙/门之上，所以排在后面。
        "band": 11,
        "louver": 12,
        "planter": 13,
        # 灯具排在花池之后、老虎窗之前：它贴在立面/檐下，
        # 不能盖掉招牌柱，也不该被女儿墙埋掉
        "lamp": 13.5,
        # 老虎窗必须最后：它要按已经建好的孟莎坡面找位置
        "dormer": 14,
        "chimney": 15,
        # 栏杆和旗杆是"站在甲板上的东西"，必须最后放，
        # 否则会被后建的甲板/女儿墙埋掉
        "railing": 16,
        "flagpole": 17,
        # 招牌永远是最后一笔：它画在别的构件表面，不能被任何东西盖掉
        "signbox": 18,
    }

    def detail(self, item: dict) -> int:
        t = str(item.get("type", ""))
        vi = int(item.get("volume", 0))
        if not (0 <= vi < len(self.spec.volumes)):
            return 0
        v = self.spec.volumes[vi]
        mat = _resolve(item.get("mat"), self.spec.palette, "accent")
        added = 0
        if t == "column":
            # 贴墙柱廊：古典立面全靠它，缺了就是办公楼
            mat = _resolve(item.get("mat"), self.spec.palette, "trim")
            face = str(item.get("face", "south"))
            u = int(item.get("u", 2))
            count = max(1, int(item.get("count", 2)))
            spacing = max(2, int(item.get("spacing", 6)))
            out = max(1, int(item.get("out", 1)))
            height = int(item.get("h", 0)) or max(1, v.h - 1)
            for c in range(count):
                added += self._column_at(v, face, u + c * spacing, out, v.y, height, mat)
        elif t == "portico":
            # 门廊 = 柱子 + 顶板 + 台阶，是"有入口"和"有气派"的分界
            mat = _resolve(item.get("mat"), self.spec.palette, "trim")
            face = str(item.get("face", "south"))
            u = int(item.get("u", 2))
            pw = max(4, int(item.get("w", 6)))
            ph = max(3, int(item.get("h", v.h // 2)))
            depth = max(1, int(item.get("depth", 2)))
            col_h = ph - 1
            count = 2 if pw < 8 else 3
            step = max(2, (pw - 1) // max(1, count - 1)) if count > 1 else 0
            for c in range(count):
                added += self._column_at(v, face, u + c * step, depth, v.y, col_h, mat)
            # 顶板：从墙面往外铺 depth 格，宽度盖住整排柱
            for du in range(pw):
                for k in range(0, depth + 1):
                    x, z, ok = self._face_pos(v, face, u + du, v.y + ph)
                    if not ok:
                        continue
                    sx, sz = self._shift(face, k)
                    self.put(x + sx, v.y + ph, z + sz, mat)
                    added += 1
            # 台阶：门廊前铺 2 格
            for k in range(1, depth + 1):
                for du in range(-1, pw + 1):
                    x, z, ok = self._face_pos(v, face, u + du, v.y)
                    if ok:
                        sx, sz = self._shift(face, k)
                        self.put(x + sx, v.y, z + sz, mat)
                        added += 1
        elif t == "arcade":
            # 底层拱廊：柱 + 拱券。古典宫殿、学院、政府楼的首层几乎都是这个，
            # 缺了它底层就只剩一排小方窗，气派全无。
            mat = _resolve(item.get("mat"), self.spec.palette, "trim")
            glass = glass_skin(_resolve(item.get("glass"), self.spec.palette, "glass"), item)
            face = str(item.get("face", "south"))
            u = int(item.get("u", 2))
            aw = max(6, int(item.get("w", max(6, v.w - 4))))
            ah = max(3, int(item.get("h", 4)))
            bays = max(2, int(item.get("count", max(2, aw // 6))))
            pitch = aw / float(bays)
            for b in range(bays):
                x0 = u + int(round(b * pitch))
                ow = max(1, int(round(pitch)) - 2)
                # 柱：每个开间的起点立一根，一直到拱顶标高
                for dy in range(ah + 1):
                    x, z, ok = self._face_pos(v, face, x0, v.y + dy)
                    if ok:
                        self.put(x, v.y + dy, z, mat)
                        added += 1
                # 拱洞：填玻璃（做成透空会灌光，玩家也进不去）
                for dy in range(ah):
                    for du in range(1, ow + 1):
                        x, z, ok = self._face_pos(v, face, x0 + du, v.y + dy)
                        if not ok:
                            continue
                        self.carve(x, v.y + dy, z)
                        self.put(x, v.y + dy, z, glass)
                        added += 1
                # 拱肩：拱顶两角填回石材，远看就是圆拱
                for du in (1, ow):
                    x, z, ok = self._face_pos(v, face, x0 + du, v.y + ah - 1)
                    if ok:
                        self.put(x, v.y + ah - 1, z, mat)
                        added += 1
        elif t == "pediment":
            # 三角山花：压在门廊 / 柱廊顶上，是"这是古典建筑"的最短表达。
            # 阶梯三角，MC 做不出真斜边，三级就够读出来了。
            mat = _resolve(item.get("mat"), self.spec.palette, "trim")
            face = str(item.get("face", "south"))
            pw = max(4, int(item.get("w", 8)))
            ph = max(2, int(item.get("h", 3)))
            u = int(item.get("u", 2))
            y0 = v.y + int(item.get("v", v.h))
            mid = (pw - 1) // 2
            for k in range(ph):
                span = max(0, mid - k)
                for du in range(-span, span + 1):
                    x, z, ok = self._face_pos(v, face, u + mid + du, y0 + k)
                    if ok:
                        self.put(x, y0 + k, z, mat)
                        added += 1
        elif t == "dormer":
            # 老虎窗：从孟莎坡面上凸出来的小窗盒子。
            # 没有它，孟莎屋顶就是一整块灰板，远看还是平顶 ——
            # 这是"像不像法式宫殿"的最后一个门槛。
            # 做法：贴坡面立一个 dw×2 的盒子，外面一层玻璃，上面压一道石檐。
            face = str(item.get("face", "south"))
            if face not in FACES:
                face = "south"
            u = int(item.get("u", 4))
            h_roof = int(self.spec.roof.get("height", 4))
            # 默认坐在坡面中段：太靠下会被檐口挡住，太靠上就爬到屋脊了
            base = int(item.get("v", 0)) or max(2, min(h_roof - 1, (h_roof + 1) // 2))
            base = max(1, min(h_roof, base))
            # 坡面还没开始收（inset=0）的那几层其实还是墙，窗放上去就变成
            # 檐口上的一排白块。往上抬到坡真正开始的地方。
            while base < h_roof and self._mansard_inset(v.w, v.d, h_roof, base) < 1:
                base += 1
            dw = max(1, int(item.get("w", 2)))
            dh = 2
            h_roof = int(self.spec.roof.get("height", 4))
            glass = glass_skin(_resolve(item.get("mat"), self.spec.palette, "glass"), item)
            frame = _resolve(item.get("frame_mat"), self.spec.palette, "trim")
            y0 = v.y1 + base
            if face in ("south", "north"):
                zs = v.z1 - self._mansard_inset(v.w, v.d, h_roof, base)
                out = 1 if face == "south" else -1
                for du in range(dw):
                    for dy in range(dh):
                        x = v.x + u + du
                        # 窗面与坡面齐平：从外面看是"坡上开的一个洞"，
                        # 这才是老虎窗；凸出来的盒子远看像屋顶上的杂物。
                        self.carve(x, y0 + dy, zs)
                        self.put(x, y0 + dy, zs, glass)
                        added += 1
                # 窗套：上面压一道往外探 1 格的石檐，下面一道窗台，
                # 影子把"这是一个凸出来的小窗"读出来
                for du in range(-1, dw + 1):
                    self.put(v.x + u + du, y0 + dh, zs + out, frame)
                    self.put(v.x + u + du, y0 - 1, zs + out, frame)
                    added += 2
            else:
                xs = v.x1 - self._mansard_inset(v.w, v.d, h_roof, base)
                out = 1 if face == "east" else -1
                for du in range(dw):
                    for dy in range(dh):
                        z = v.z + u + du
                        self.carve(xs, y0 + dy, z)
                        self.put(xs, y0 + dy, z, glass)
                        added += 1
                for du in range(-1, dw + 1):
                    self.put(xs + out, y0 + dh, v.z + u + du, frame)
                    self.put(xs + out, y0 - 1, v.z + u + du, frame)
                    added += 2
        elif t == "plinth":
            # 勒脚：贴着地面的那一圈换成深色，楼才不会像浮在空地上。
            # 写了 volume 就只做那个体块 —— 退台的三楼是坐在屋顶上的，
            # 给它也刷一圈勒脚，等于在楼顶画了一道黑腰线。
            hh = max(1, int(item.get("h", 1)))
            target = item.get("volume")
            vlist = (
                [self.spec.volumes[int(target)]]
                if target is not None and 0 <= int(target) < len(self.spec.volumes)
                else self.spec.volumes
            )
            for vv in vlist:
                for k in range(hh):
                    y = vv.y + k
                    for x in range(vv.x, vv.x1 + 1):
                        for z in range(vv.z, vv.z1 + 1):
                            if x in (vv.x, vv.x1) or z in (vv.z, vv.z1):
                                if self.vox[x, y, z] >= 0 and self._exposed(x, y, z):
                                    self.put(x, y, z, mat)
                                    added += 1
        elif t == "cornice":
            # 檐口：墙顶一圈往外挑 1 格，平顶建筑全靠它收头
            out = max(0, int(item.get("out", 1)))
            for vv in self.spec.volumes:
                y = vv.y1
                for x in range(vv.x - out, vv.x1 + out + 1):
                    for z in range(vv.z - out, vv.z1 + out + 1):
                        if vv.x < x < vv.x1 and vv.z < z < vv.z1:
                            continue  # 只留一圈
                        if 0 <= x < self.vox.shape[0] and 0 <= z < self.vox.shape[2]:
                            # 挑出去落在别的体块墙上时跳过，接缝处不该有檐口
                            if self.vox[x, y, z] >= 0 and not self._exposed(x, y, z):
                                continue
                            # 顶到屋顶的幕墙不能被檐口盖掉。商业立面常把
                            # "上层玻璃"写在最上面 2 格，而檐口正好落在同一行：
                            # 一盖，整层玻璃消失，模型就变成"勒脚+招牌+灰墙"。
                            prev = int(self.vox[x, y, z])
                            if prev >= 0 and "glass" in self.blocks[prev].name_en:
                                continue
                            self.put(x, y, z, mat)
                            added += 1
        elif t == "door":
            face = str(item.get("face", "south"))
            u = int(item.get("u", 2))
            dw = max(1, int(item.get("w", 2)))
            dh = max(2, int(item.get("h", 3)))
            y0 = v.y + int(item.get("v", 0))
            frame_mat = _resolve(item.get("frame_mat"), self.spec.palette, "trim")
            for dy in range(dh):
                for du in range(dw):
                    x, z, ok = self._face_pos(v, face, u + du, y0 + dy)
                    if ok:
                        self.carve(x, y0 + dy, z)
                        self.put(x, y0 + dy, z, mat)
                        added += 1
            # 门框：两侧各一列，顶上一行
            for dy in range(dh + 1):
                for du in (-1, dw):
                    x, z, ok = self._face_pos(v, face, u + du, y0 + dy)
                    if ok:
                        self.put(x, y0 + dy, z, frame_mat)
                        added += 1
            for du in range(-1, dw + 1):
                x, z, ok = self._face_pos(v, face, u + du, y0 + dh)
                if ok:
                    self.put(x, y0 + dh, z, frame_mat)
                    added += 1
            # 门前台阶：向外铺 step 格，宽度比门宽两格
            step = max(0, int(item.get("steps", 0)))
            for k in range(1, step + 1):
                for du in range(-1, dw + 1):
                    if face == "south":
                        x, z = v.x + u + du, v.z1 + k
                    elif face == "north":
                        x, z = v.x + u + du, v.z - k
                    elif face == "east":
                        z, x = v.z + u + du, v.x1 + k
                    else:
                        z, x = v.z + u + du, v.x - k
                    self.put(x, y0, z, frame_mat)
                    added += 1
        elif t == "curtain_wall":
            # 玻璃幕墙：整片开窗 + 竖梃，别墅的临街面靠它出效果
            face = str(item.get("face", "south"))
            u = int(item.get("u", 1))
            y0 = v.y + int(item.get("v", 0))
            cw = max(2, int(item.get("w", 6)))
            ch = max(2, int(item.get("h", 6)))
            mullion = max(2, int(item.get("mullion", 3)))
            # 横梁：商铺/办公的幕墙一定有横向分格，只画竖梃会像一块蓝玻璃招牌
            transom = int(item.get("transom", 0))
            glass = glass_skin(_resolve(item.get("mat"), self.spec.palette, "glass"), item)
            frame_mat = _resolve(item.get("frame_mat"), self.spec.palette, "trim")
            for dy in range(ch):
                for du in range(cw):
                    x, z, ok = self._face_pos(v, face, u + du, y0 + dy)
                    if not ok:
                        continue
                    self.carve(x, y0 + dy, z)
                    vertical = du % mullion == 0 or du == cw - 1
                    # 横梁只在够高的幕墙上画。2 格高的玻璃上下各一道框，
                    # 一整条玻璃带就只剩框色 —— 上层玻璃最容易这样被吃掉。
                    horizontal = (dy == 0 and ch >= 4) or (ch >= 7 and dy == ch - 1) or (
                        transom >= 2 and dy % transom == 0
                    )
                    self.put(x, y0 + dy, z, frame_mat if (vertical or horizontal) else glass)
                    added += 1
        elif t == "railing":
            # 甲板/屋顶边缘的栏杆。这是"这是船不是楼"最廉价的一条线索：
            # 军舰的上层建筑平台、直升机甲板、舰桥两侧全是栏杆，
            # 少了它，甲板边缘就是一圈光秃秃的方块，像楼顶。
            #
            # 用**邻居是否为空**来判定边缘，而不是按 bbox 四边算 ——
            # 楔形舰艏每片的宽度都不一样，bbox 判法会在船头铺出一圈
            # 悬空的栏杆。
            rail = _resolve(item.get("mat"), self.spec.palette, "iron_bars")
            face = str(item.get("face", "all"))
            y = v.y1 + 1
            nx, ny, nz = self.vox.shape

            def solid(xx: int, yy: int, zz: int) -> bool:
                return (
                    0 <= xx < nx and 0 <= yy < ny and 0 <= zz < nz
                    and int(self.vox[xx, yy, zz]) >= 0
                )

            for x in range(v.x, v.x1 + 1):
                for z in range(v.z, v.z1 + 1):
                    if not solid(x, v.y1, z):
                        continue
                    open_s = not solid(x, v.y1, z + 1)
                    open_n = not solid(x, v.y1, z - 1)
                    open_e = not solid(x + 1, v.y1, z)
                    open_w = not solid(x - 1, v.y1, z)
                    if not (open_s or open_n or open_e or open_w):
                        continue  # 内部格，不是边缘
                    if face != "all":
                        if face == "south" and not open_s:
                            continue
                        if face == "north" and not open_n:
                            continue
                        if face == "east" and not open_e:
                            continue
                        if face == "west" and not open_w:
                            continue
                    self.put(x, y, z, rail)
                    added += 1
        elif t == "flagpole":
            # 旗杆：桅顶或舰尾的一根细杆 + 一面旗。
            # 杆用铁栏杆（十字细杆），旗用羊毛一整块 —— 1/16 高的地毯
            # 贴在杆上根本看不出来是一面旗。
            pole = _resolve(item.get("mat"), self.spec.palette, "iron_bars")
            flag = _resolve(item.get("flag"), self.spec.palette, "wool:14")
            ph = max(2, int(item.get("h", 8)))
            u = int(item.get("u", v.w // 2))
            w = int(item.get("v", v.d // 2))
            x0, z0 = v.x + u, v.z + w
            base = v.y1 + 1
            for dy in range(ph):
                self.put(x0, base + dy, z0, pole)
                added += 1
            # 旗面挂在杆顶下方 1/3 处，朝 side 方向铺 4 格宽、3 格高
            side = str(item.get("side", "east"))
            fw = max(2, int(item.get("fw", 4)))
            fh = max(2, int(item.get("fh", 3)))
            top = base + ph - 1
            for dy in range(fh):
                for du in range(1, fw + 1):
                    if side in ("east", "west"):
                        fx = x0 + du if side == "east" else x0 - du
                        fz = z0
                    else:
                        fz = z0 + du if side == "south" else z0 - du
                        fx = x0
                    self.put(fx, top - dy, fz, flag)
                    added += 1
        elif t == "balcony":
            # 阳台板默认也是石材：深色木板在空中会变成一条突兀的横杠
            mat = _resolve(item.get("mat"), self.spec.palette, "trim")
            face = str(item.get("face", "south"))
            u = int(item.get("u", 2))
            y = int(item.get("v", v.h - 4))
            bw = max(2, int(item.get("w", 6)))
            depth = max(1, int(item.get("depth", 1)))
            # 栏杆默认用铁栏杆（101）：深灰、细，等轴测预览里也不会变成一条土黄木条
            rail_mat = parse_material(item.get("rail") or "iron_bars", "iron_bars")
            y_abs = v.y + y
            for du in range(bw):
                for dd in range(depth):
                    if face == "south":
                        x, z = v.x + u + du, v.z1 + 1 + dd
                    elif face == "north":
                        x, z = v.x + u + du, v.z - 1 - dd
                    else:
                        z, x = v.z + u + du, v.x1 + 1 + dd
                    self.put(x, y_abs, z, mat)
                    added += 1
            # 栏杆：前沿 + 两侧围成 U 形。只做前沿的话，等轴测图上就是
            # 一根悬空的深色横条，看不出是阳台。
            for du in range(bw):
                if face in ("south", "north"):
                    x = v.x + u + du
                    z = v.z1 + depth if face == "south" else v.z - depth
                else:
                    z = v.z + u + du
                    x = v.x1 + depth
                self.put(x, y_abs + 1, z, rail_mat)
                added += 1
            for dd in range(1, depth + 1):
                for du in (0, bw - 1):
                    if face in ("south", "north"):
                        x, z = v.x + u + du, v.z1 + dd if face == "south" else v.z - dd
                    else:
                        z, x = v.z + u + du, v.x1 + dd
                    self.put(x, y_abs + 1, z, rail_mat)
                    added += 1
            # 阳台后面必须能出去：正中开一扇玻璃门，否则阳台就是贴在墙上的一块板
            glass = _resolve(item.get("door_mat"), self.spec.palette, "glass")
            door_w = min(3, bw - 2)
            if door_w >= 2:
                du0 = u + (bw - door_w) // 2
                for dy in range(1, 3):
                    for du in range(door_w):
                        x, z, ok = self._face_pos(v, face, du0 + du, y_abs + dy)
                        if ok:
                            self.carve(x, y_abs + dy, z)
                            self.put(x, y_abs + dy, z, glass)
                            added += 1
        elif t == "canopy":
            u = int(item.get("u", 0))
            y = int(item.get("v", 3))
            bw = max(2, int(item.get("w", 4)))
            for du in range(bw):
                self.put(v.x + u + du, v.y + y, v.z1 + 1, mat)
                added += 1
        elif t == "chimney":
            u = int(item.get("u", 0))
            w = max(1, int(item.get("w", 2)))
            for du in range(w):
                for dz in range(w):
                    for y in range(v.y1 + 1, min(v.y1 + 4, self.vox.shape[1])):
                        self.put(v.x + u + du, y, v.z + 2 + dz, mat)
                        added += 1
        elif t == "band":
            # 水平饰带：现代商业立面的"分层"几乎全靠它。
            # 照片里那条木纹铝板带、上面那道白色檐口、檐下的灯带，
            # 都是同一种东西 —— 一条通长的水平板，把立面切成几段。
            face = str(item.get("face", "south"))
            u = int(item.get("u", 0))
            bw = int(item.get("w", 0)) or (v.w if face in ("south", "north") else v.d)
            y0 = v.y + int(item.get("v", 0))
            bh = max(1, int(item.get("h", 1)))
            out = max(0, min(3, int(item.get("out", 1))))
            dx, dz = self._shift(face, 1)
            for du in range(max(1, bw)):
                for dy in range(bh):
                    x, z, ok = self._face_pos(v, face, u + du, y0 + dy)
                    if not ok:
                        continue
                    if out == 0:
                        # 贴面：只替换原来那格，不外挑（用来做"换料的腰线"）
                        if 0 <= x < self.vox.shape[0] and self.vox[x, y0 + dy, z] >= 0:
                            self.put(x, y0 + dy, z, mat)
                            added += 1
                        continue
                    for k in range(1, out + 1):
                        self.put(x + dx * k, y0 + dy, z + dz * k, mat)
                        added += 1
        elif t == "louver":
            # 竖向格栅 / 木百叶：店面转角、设备遮挡、幕墙分隔都用它。
            # 一排竖条在等轴测里是"疏密"，一块实心板是"死面"，
            # 这是现代立面看起来轻盈的关键。
            face = str(item.get("face", "south"))
            u = int(item.get("u", 0))
            lw = max(1, int(item.get("w", 4)))
            y0 = v.y + int(item.get("v", 0))
            lh = max(2, int(item.get("h", 8)))
            step = max(1, int(item.get("step", 2)))
            out = max(0, min(2, int(item.get("out", 1))))
            dx, dz = self._shift(face, out) if out else (0, 0)
            for du in range(lw):
                if du % step:
                    continue  # 留缝：格栅必须能看见后面的东西
                for dy in range(lh):
                    x, z, ok = self._face_pos(v, face, u + du, y0 + dy)
                    if not ok:
                        continue
                    self.put(x + dx, y0 + dy, z + dz, mat)
                    added += 1
        elif t == "planter":
            # 花池：铺装与立面之间的过渡。没有它，店面像直接插在地上；
            # 有了它，一张照片里"门口有一排绿化"这件事才立得住。
            face = str(item.get("face", "south"))
            u = int(item.get("u", 0))
            pw = max(2, int(item.get("w", 4)))
            pd = max(1, min(3, int(item.get("d", 2))))
            y0 = v.y + int(item.get("v", 0))
            ph = max(1, min(3, int(item.get("h", 1))))
            plant = _resolve(item.get("plant"), self.spec.palette, "leaves:0")
            dx, dz = self._shift(face, 1)
            for du in range(pw):
                for dd in range(pd):
                    x, z, ok = self._face_pos(v, face, u + du, y0)
                    if not ok:
                        continue
                    wx, wz = x + dx * (dd + 1), z + dz * (dd + 1)
                    for k in range(ph):
                        self.put(wx, y0 + k, wz, mat)
                        added += 1
                    # 顶上种东西：边缘种满、中间随机留空，别长成一坨方块
                    if plant is not None and (du % 2 == 0 or dd == pd - 1):
                        self.put(wx, y0 + ph, wz, plant)
                        added += 1
        elif t == "lamp":
            # 灯具：照片里那种成片的灯箱、灯带、大吊灯，用**光源方块**表达。
            # 不写这个的话，AI 只能用"白色混凝土"画一块白板 —— 白天看着像，
            # 进游戏里晚上不亮，一眼就是贴图。
            face = str(item.get("face", "south"))
            u = int(item.get("u", 0))
            y0 = v.y + int(item.get("v", 0))
            lw = max(1, int(item.get("w", 3)))
            lh = max(1, int(item.get("h", 1)))
            out = max(0, min(2, int(item.get("out", 1))))
            lm = _resolve(item.get("mat"), self.spec.palette, "sea_lantern")
            dx, dz = self._shift(face, 1)
            for du in range(lw):
                for dy in range(lh):
                    x, z, ok = self._face_pos(v, face, u + du, y0 + dy)
                    if not ok:
                        continue
                    if out == 0:
                        # 齐平：直接换掉墙块，做成"嵌进立面的灯带"
                        self.put(x, y0 + dy, z, lm)
                        added += 1
                    else:
                        for k in range(1, out + 1):
                            self.put(x + dx * k, y0 + dy, z + dz * k, lm)
                            added += 1
        elif t == "signbox":
            # 招牌箱：一块贴在立面上的独立板（红底 + 图形），或者一整根
            # 竖向广告柱。这是商业建筑最强的识别信号 —— 材质做得再准，
            # 没有招牌也只是一栋玻璃盒子。
            face = str(item.get("face", "south"))
            u = int(item.get("u", 0))
            y0 = v.y + int(item.get("v", 0))
            sw = max(2, int(item.get("w", 3)))
            sh = max(2, int(item.get("h", 6)))
            out = max(1, min(3, int(item.get("out", 1))))
            top = _resolve(item.get("cap"), self.spec.palette, "trim")
            dx, dz = self._shift(face, 1)
            for du in range(sw):
                for dy in range(sh):
                    x, z, ok = self._face_pos(v, face, u + du, y0 + dy)
                    if not ok:
                        continue
                    for k in range(1, out + 1):
                        self.put(x + dx * k, y0 + dy, z + dz * k, mat)
                        added += 1
                # 顶上一块压顶，招牌箱才不像贴上去的色块
                if out >= 1:
                    x, z, ok = self._face_pos(v, face, u + du, y0 + sh)
                    if ok:
                        for k in range(1, out + 1):
                            self.put(x + dx * k, y0 + sh, z + dz * k, top)
                            added += 1
            glyph = GLYPHS.get(str(item.get("logo", "")).strip().lower())
            if glyph:
                logo_mat = _resolve(item.get("logo_mat"), self.spec.palette, "concrete:4")
                rows = len(glyph)
                cols = max(len(r) for r in glyph)
                # 图形按整数倍放大，塞进招牌内框。**塞不下就不画** ——
                # 早版是先算倍数再硬画，4 格宽的招牌被 5 格宽的 M 顶出去，
                # 黄点糊到旁边的玻璃上，比不画还难看。
                # 同时限制**占招牌高度的一半**：真实招牌的 logo 只占中间一段，
                # 铺满整根柱子会变成一堵黄色图案（照片里 M 只占柱子四成高）。
                s = min(sw // cols, (sh - 2) // rows, max(1, int(sh * 0.55) // rows))
                if s >= 1:
                    gw, gh = cols * s, rows * s
                    gu = u + max(0, (sw - gw) // 2)
                    gv0 = int(item.get("logo_v", -1))
                    gv = y0 + gv0 if gv0 >= 0 else y0 + max(1, (sh - gh) // 2)
                    # 夹回招牌内框：AI 常把 logo_v 写到招牌顶（h=10 却写 logo_v=9），
                    # 5 格高的 M 只剩最下面两行露在外面，看起来是两个黄点。
                    gv = max(y0, min(gv, y0 + sh - gh))
                    for ry, line in enumerate(glyph):
                        for rx, ch in enumerate(line):
                            if ch != "#":
                                continue
                            for sy in range(s):
                                for sx in range(s):
                                    du_ = gu + rx * s + sx - u
                                    dy_ = gv + (rows - 1 - ry) * s + sy - y0
                                    if not (0 <= du_ < sw and 0 <= dy_ < sh):
                                        continue
                                    x, z, ok = self._face_pos(v, face, u + du_, y0 + dy_)
                                    if ok:
                                        self.put(x + dx * out, y0 + dy_, z + dz * out, logo_mat)
                                        added += 1
        return added

    # ---- 细节等级（尺寸越大越细） ----

    def _exposed(self, x: int, y: int, z: int) -> bool:
        """这一格是不是真的露在外面（六邻里有空格）。

        线脚只该画在外立面上：三个体块贴在一起时，勒脚/腰线会沿着
        体块之间的接缝重复画一遍，那些格子在建筑内部，玩家永远看不见，
        却实实在在吃掉了配色比例 —— 别墅那栋的青色线脚有一半是这么来的。
        """
        nx, ny, nz = self.vox.shape
        for dx, dy, dz in ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1)):
            ax, ay, az = x + dx, y + dy, z + dz
            if not (0 <= ax < nx and 0 <= ay < ny and 0 <= az < nz):
                return True
            if self.vox[ax, ay, az] < 0:
                return True
        return False

    def _is_block(self, x: int, y: int, z: int, blk: Block) -> bool:
        if not (
            0 <= x < self.vox.shape[0]
            and 0 <= y < self.vox.shape[1]
            and 0 <= z < self.vox.shape[2]
        ):
            return False
        try:
            idx = self.blocks.index(blk)
        except ValueError:
            return False
        return int(self.vox[x, y, z]) == idx

    def _trim_ratio(self, trim: Block) -> float:
        """线脚占全部方块的比例。"""
        total = int((self.vox >= 0).sum())
        if total <= 0:
            return 0.0
        return int((self.vox == self._idx(trim)).sum()) / total

    def refine(self) -> int:
        """按 detail 等级补细节：勒脚/檐口兜底、每层腰线、四角壁柱。

        带**面积预算**：线脚是"画在墙上的线"，不是墙本身。真实建筑的
        勒脚+腰线+檐口占立面 15%~20%，而这里第一版做到 46% —— 一栋白墙楼
        被线脚盖成了青灰色，照片的白几乎看不见了。所以每一步加完都要
        回头看占比，超了就先砍角柱、再砍腰线。勒脚和檐口是结构必需，保留。
        """
        if self.spec.detail <= 1:
            return 0
        if self.spec.subject != "building":
            # 勒脚/檐口/腰线/角柱是**建筑**的收头方式。舰船上它们只会变成
            # 船体侧面一圈莫名其妙的灰带和甲板边一圈矮墙 —— 这正是
            # "驱逐舰做出来像写字楼"的最后一道保险。
            return 0
        trim = self.spec.palette["trim"]
        wall = self.spec.palette["wall"]
        if trim is wall:  # 线脚和墙同色，加了也看不出来
            return 0

        budget = 0.22
        kinds = {str(d.get("type", "")) for d in self.spec.details}
        added = 0
        # 1) 勒脚 / 檐口兜底：结构必需，不加的话楼像直接切在地面上
        if "plinth" not in kinds:
            added += self.detail({"type": "plinth", "mat": "trim", "h": 1})
        if "cornice" not in kinds:
            added += self.detail({"type": "cornice", "mat": "trim", "out": 1})
        if self._trim_ratio(trim) >= budget:
            return added

        # 2) 腰线：每层楼板高度压一道，只压外露的格
        for v in self.spec.volumes:
            if v.floors < 2:
                continue
            step = max(2, v.h // v.floors)
            for k in range(1, v.floors):
                if self._trim_ratio(trim) >= budget:
                    return added
                y = v.y + k * step
                if y >= v.y1:
                    break
                for x in range(v.x, v.x1 + 1):
                    for z in range(v.z, v.z1 + 1):
                        if x not in (v.x, v.x1) and z not in (v.z, v.z1):
                            continue
                        if not self._is_block(x, y, z, wall):
                            continue
                        if not self._exposed(x, y, z):
                            continue
                        self.put(x, y, z, trim)
                        added += 1

        # 3) 角柱：只给最宽的那个体块加，而且宽 < 10 不加。
        #    每个体块都加四角竖壁柱的话，三个体块 × 4 角 × 18 格高就是
        #    两百多块，白墙被竖着切碎，远看就是一栋青色的楼。
        if self.spec.detail >= 3 and self.spec.volumes:
            main = max(self.spec.volumes, key=lambda v: v.w * v.d)
            if main.w >= 10 and self._trim_ratio(trim) < budget:
                for x in (main.x, main.x1):
                    for z in (main.z, main.z1):
                        for y in range(main.y, main.y1 + 1):
                            if not self._is_block(x, y, z, wall):
                                continue
                            if not self._exposed(x, y, z):
                                continue
                            self.put(x, y, z, trim)
                            added += 1
        return added

    def run(self) -> tuple[np.ndarray, list[Block], dict]:
        for v in self.spec.volumes:
            self.volume(v)
        n_win = sum(self.window_band(w) for w in self.spec.windows)
        n_roof = self.roof()
        n_det = 0
        for item in sorted(
            self.spec.details, key=lambda x: self.DETAIL_ORDER.get(str(x.get("type")), 9)
        ):
            n_det += self.detail(item)
        n_det += self.refine()
        # 观赏性细节最后上：它要看到全部体块、窗、屋顶、线脚才放得准。
        # 放在 refine 之后还有一个原因 —— 线脚/腰线是"结构色"，
        # 补丁要能把它们一起做旧，否则墙角永远是刚刷的。
        orn = ornament.apply(self)
        info = {
            "volumes": len(self.spec.volumes),
            "window_cells": n_win,
            "roof_blocks": n_roof,
            "detail_blocks": n_det,
            "ornament_patch": int(orn.get("patch", 0)),
            "ornament_prop": int(orn.get("prop", 0)),
        }
        return self.vox, self.blocks, info


def build(spec: Spec) -> tuple[np.ndarray, list[Block], dict]:
    """spec -> (vox (X,Y,Z), 方块表, 统计)。"""
    return _Builder(spec).run()
