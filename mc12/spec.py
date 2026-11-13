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

import math
import random
from contextlib import contextmanager
from dataclasses import dataclass, field

import numpy as np

from . import ornament, palette
from .knowledge import is_modern_type
from .palette import Block

FACES = ("north", "south", "east", "west")

# 单边最大格数。再宽就不是一栋建筑，是一段城墙了 —— 生存模式里也没人铺得完。
MAX_SPAN = 132
# 每个体块每层的可用高度区间。低于 3 进不去，高于 6 空旷得像仓库。
FLOOR_H = (3, 6)


# --------------------------------------------------------------------------
# 平面轮廓（非矩形）
# --------------------------------------------------------------------------
# 真实建筑很少是方盒子：六边形的实验楼、八边形的塔楼、一头宽的楔形展厅，
# 平面轮廓本身就是设计的一部分。只给 w/d 两个数，做出来永远是长方形。
#
# 做法：给体块一个**平面多边形**，把"这一格在不在轮廓里"抽成一个判断
# （Volume.contains）。体块外壳、楼板、女儿墙、檐口、勒脚、开窗的立面定位
# 全都改成走这个判断，于是"六边形"不需要在别处再写一遍特例。
#
# 多边形用**局部坐标**（0..w / 0..d），与体块的 x/z 无关：
# 斜置（lean/skew）是逐层的位移，轮廓是跟着走的，两者天然叠在一起。

SHAPES = {
    "": "",            # 矩形（默认）
    "rect": "",
    "hex": "hex",           # 六边形：前后两条长边 + 左右两端收尖
    "hexagon": "hex",
    "六边形": "hex",
    "hex_front": "hex_front",   # 六边形：前面窄、后面宽（正面两角大倒角）
    "hexfront": "hex_front",
    "oct": "oct",           # 八边形：四角等切
    "octagon": "oct",
    "八边形": "oct",
    "wedge": "wedge",       # 楔形：正面收成一条窄边
    "楔形": "wedge",
    "round": "round",       # 圆/椭圆：塔楼、旋转楼梯间
    "circle": "round",
    "ellipse": "round",
    "圆形": "round",
    "l": "l",               # L 形平面：切掉一个角
    "lshape": "l",
    "l形": "l",
    "poly": "poly",         # 自己给顶点
}

# 轮廓缓存：键是 (w, d, shape, chamfer, poly)，值是格子集合；
# False 表示"整块都是"（矩形），省得每次重算。
_PLAN_CACHE: dict[tuple, frozenset | bool] = {}


def _shape_poly(w: int, d: int, shape: str, chamfer: int):
    """按预设名字给出多边形顶点（连续坐标，单位格，0..w / 0..d）。"""
    w = max(1, w)
    d = max(1, d)
    if not shape:
        if chamfer <= 0:
            return None
        c = min(chamfer, (min(w, d) - 1) // 2)
        if c <= 0:
            return None
        return [(c, 0), (w - c, 0), (w, c), (w, d - c), (w - c, d), (c, d), (0, d - c), (0, c)]
    if shape == "hex":
        # 左右两端各收成一个 45° 的尖：前后保留两条长边，正面仍然能开窗。
        # 收角必须接近 45°，否则"六边形"会读成"一面斜墙"—— 体素里斜边的
        # 每格退让量决定了它看起来是切角还是抹角。
        c = max(1.0, min(d / 2.0, w * 0.40))
        return [(c, 0), (w - c, 0), (w, d / 2.0), (w - c, d), (c, d), (0, d / 2.0)]
    if shape == "hex_front":
        # 前面窄、后面宽：正面两个角按 45° 切掉，这是最常见的六边形平面
        c = max(1.0, min(w * 0.32, d * 0.55))
        return [(0, 0), (w, 0), (w, d - c), (w - c, d), (c, d), (0, d - c)]
    if shape == "oct":
        c = max(1, int(round(min(w, d) * 0.30)))
        c = min(c, (min(w, d) - 1) // 2)
        return [(c, 0), (w - c, 0), (w, c), (w, d - c), (w - c, d), (c, d), (0, d - c), (0, c)]
    if shape == "wedge":
        return [(0, 0), (w, 0), (w, d * 0.38), (w / 2.0, d), (0, d * 0.38)]
    if shape == "round":
        # 用 24 边形逼近圆/椭圆：1 格尺度上已经读不出棱角，
        # 但仍然是整数格，不会像真圆那样在斜边处漏光。
        import math as _m

        n = 24
        return [
            (w / 2.0 + (w / 2.0) * _m.cos(2 * _m.pi * i / n), d / 2.0 + (d / 2.0) * _m.sin(2 * _m.pi * i / n))
            for i in range(n)
        ]
    if shape == "l":
        # L 形平面：切掉东北角一块。学校、办公楼、转角楼最常见的轮廓。
        cw = max(2.0, round(w * 0.45))
        cd = max(2.0, round(d * 0.45))
        return [(0, 0), (w - cw, 0), (w - cw, cd), (w, cd), (w, d), (0, d)]
    return None


def plan_cells(w: int, d: int, shape: str, chamfer: int, poly) -> frozenset | None:
    """轮廓覆盖的格子集合（局部坐标）；返回 None 表示整块矩形。

    用格心做点内判定（射线法）。45° 的斜边在体素里必然是一段楼梯，
    这正是 MC 表达斜面的唯一手段，不需要额外平滑。
    """
    key = (w, d, shape, chamfer, tuple(tuple(p) for p in (poly or ())))
    hit = _PLAN_CACHE.get(key)
    if hit is not None:
        return None if hit is False else hit
    pts = None
    if poly and len(poly) >= 3:
        pts = [(float(a), float(b)) for a, b in poly]
    elif shape:
        pts = _shape_poly(w, d, shape, chamfer)
    elif chamfer > 0:
        pts = _shape_poly(w, d, "", chamfer)
    if not pts:
        _PLAN_CACHE[key] = False
        return None
    cells = set()
    n = len(pts)
    for i in range(w):
        cx = i + 0.5
        for j in range(d):
            cy = j + 0.5
            inside = False
            for k in range(n):
                x1, y1 = pts[k]
                x2, y2 = pts[(k + 1) % n]
                if (y1 > cy) != (y2 > cy):
                    xin = x1 + (cy - y1) * (x2 - x1) / (y2 - y1)
                    if cx < xin:
                        inside = not inside
            if inside:
                cells.add((i, j))
    # 顶点写错（自交、退化）时可能只剩一条缝：宁可退回矩形，
    # 也不要交出一具"骨架"—— 那比方盒子更不像建筑。
    if len(cells) < 0.32 * w * d:
        _PLAN_CACHE[key] = False
        return None
    out = frozenset(cells)
    _PLAN_CACHE[key] = out
    return out


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
    # ---- 斜置（剪切）：现代建筑最要紧的一条 ----
    # 扎哈、蓝天组、MVRDV 那类"斜着摞的盒子"根本不是正交体块：
    # 上层比下层歪出去几格，立面与地面不垂直。只靠三个正交长方体拼，
    # 无论怎么错位都是"方块堆"，正是用户说的"和原图不像"。
    # lean_x / lean_z = **顶层相对底层**的总平移（格，可为负），
    # 中间按高度线性插值，于是整个体块是一块斜着的平行六面体。
    # 体素里表现为每层错一格 —— 那正是 MC 表达斜面的唯一手段。
    lean_x: int = 0
    lean_z: int = 0
    # ---- 平面剪切：让立面与坐标轴不平行 ----
    # 照片里那种"斜着摆的长条盒子"，俯视图是平行四边形，不是矩形。
    # skew_z = 沿 Z 每走完整个进深，X 平移多少格（顶层视角看是斜的）；
    # skew_x = 沿 X 每走完整个开间，Z 平移多少格。
    # 和 lean 的区别：lean 是竖着的斜（剖面是平行四边形），
    # skew 是躺着的斜（平面是平行四边形）。真实的斜置建筑两种都有。
    skew_x: int = 0
    skew_z: int = 0
    # ---- 平面轮廓：不一定是矩形 ----
    # shape 取 hex / hex_front / oct / wedge / poly；chamfer 是四角切角格数；
    # poly 是自己给的顶点（局部坐标 0..w / 0..d）。三者互斥优先级：
    # poly > shape > chamfer。
    # 六边形的实验楼、八边形的塔楼、楔形展厅，靠 w/d 是写不出来的 ——
    # 只给两个数，无论怎么调都是一个长方形。
    shape: str = ""
    poly: list = field(default_factory=list)
    chamfer: int = 0
    # ---- 竖向坡度：整块"斜着走" ----
    # lean 是"墙斜、每层仍水平"，skew 是"平面斜摆"，两者都不改变一件事：
    # 体块的**底面还是水平的**。照片里那种"二层像一条左低右高的斜带"
    # 恰恰是底面本身斜了 —— 楼板斜着往上跑，一层的顶面在两端差出七八格。
    # 只靠 lean 永远做不出来，所以补这一组：
    #   tilt_bottom：沿 tilt_axis 走完全长，底面抬起多少格（可负 = 往下沉）
    #   tilt_top   ：顶面同理
    # 两者相等 = 整块斜板（斜带）；只给 bottom = 斜底悬挑（盒子底下削一刀）；
    # 只给 top = 斜屋面。height 记的是"近端"的高度，远端会被坡度量撑高。
    tilt_axis: str = ""        # "" | "x" | "z"
    tilt_bottom: int = 0
    tilt_top: int = 0
    # 附属体量：由"结构数量"自动长出来的角塔/凸窗/侧翼/设备房。
    # 它们不是主体，体检时不该按"错位/梯田"去罚，材质也统一跟主体走。
    aux: bool = False

    def plan(self) -> frozenset | None:
        """平面轮廓覆盖的局部格子集合；None = 整块矩形。"""
        if not (self.shape or self.chamfer or self.poly):
            return None
        return plan_cells(self.w, self.d, self.shape, self.chamfer, self.poly)

    def contains(self, x: int, z: int) -> bool:
        """世界坐标 (x,z) 这一格在不在体块的平面轮廓里。"""
        i, j = x - self.x, z - self.z
        if i < 0 or j < 0 or i >= self.w or j >= self.d:
            return False
        cells = self.plan()
        return True if cells is None else (i, j) in cells

    def edge_at(self, x: int, z: int) -> bool:
        """这一格是不是轮廓边缘（四邻有一个在轮廓外）。"""
        return (
            not self.contains(x - 1, z)
            or not self.contains(x + 1, z)
            or not self.contains(x, z - 1)
            or not self.contains(x, z + 1)
        )

    @property
    def area(self) -> int:
        cells = self.plan()
        return self.w * self.d if cells is None else len(cells)

    @property
    def slanted(self) -> bool:
        return bool(
            self.lean_x or self.lean_z or self.skew_x or self.skew_z or self.tilted
        )

    @property
    def tilted(self) -> bool:
        return bool(self.tilt_axis in ("x", "z") and (self.tilt_bottom or self.tilt_top))

    def _tilt_at(self, x: int, z: int, amount: int) -> int:
        """某一列相对近端的抬升量。"""
        if not amount:
            return 0
        n = self.w if self.tilt_axis == "x" else self.d
        i = (x - self.x) if self.tilt_axis == "x" else (z - self.z)
        if n <= 1:
            return 0
        t = max(0.0, min(1.0, i / float(n - 1)))
        return int(round(amount * t))

    def y_span(self, x: int, z: int) -> tuple[int, int]:
        """这一列体素从哪一层到哪一层（含两端）。

        没坡度时就是 (y, y1)；有坡度时底面与顶面各按自己的斜率走，
        整块就是一块斜着的板 —— 楼板、女儿墙、百叶顶端全都靠这里对齐。
        """
        if not self.tilted:
            return self.y, self.y1
        y0 = self.y + self._tilt_at(x, z, self.tilt_bottom)
        y1 = self.y + self.h - 1 + self._tilt_at(x, z, self.tilt_top)
        return (y0, y1) if y1 >= y0 else (y0, y0)

    def ypad(self) -> tuple[int, int]:
        """坡度让包围盒上下溢出的量 (下, 上)。"""
        if not self.tilted:
            return 0, 0
        lo = min(0, self.tilt_bottom, self.tilt_top)
        hi = max(0, self.tilt_bottom, self.tilt_top)
        return lo, hi

    def shift_at(self, x: int, y: int, z: int) -> tuple[int, int]:
        """(x,y,z) 这一格相对体块基准的平移量：竖直倾斜 + 平面剪切。"""
        if not self.slanted:
            return 0, 0
        dx = dz = 0
        if self.h > 1 and (self.lean_x or self.lean_z):
            t = max(0.0, min(1.0, (y - self.y) / float(self.h - 1)))
            dx += int(round(self.lean_x * t))
            dz += int(round(self.lean_z * t))
        if self.skew_z and self.d > 1:
            t = max(0.0, min(1.0, (z - self.z) / float(self.d - 1)))
            dx += int(round(self.skew_z * t))
        if self.skew_x and self.w > 1:
            t = max(0.0, min(1.0, (x - self.x) / float(self.w - 1)))
            dz += int(round(self.skew_x * t))
        return dx, dz

    def pad(self) -> tuple[int, int, int, int]:
        """斜置后包围盒的溢出量：(左, 前, 右, 后)，供尺寸与归一化使用。

        保守取满值：skew_z 只有在体块最远端才偏移这么多格，
        但少留这一截，顶上那一排就会被裁掉，看着像被削平的盒子。
        """
        left = min(0, self.lean_x) + min(0, self.skew_z)
        front = min(0, self.lean_z) + min(0, self.skew_x)
        right = max(0, self.lean_x) + max(0, self.skew_z)
        back = max(0, self.lean_z) + max(0, self.skew_x)
        return left, front, right, back

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


def _stairs_bleed(details: list, vols: list) -> tuple[int, int, int, int]:
    """构件探出体块包围盒的最大格数 (西, 北, 东, 南)。

    别的构件最多挑出 1~3 格（檐口、阳台、雨棚），包围盒统一留 2 格就够了；
    两类构件是"天生站在墙外"的：
      - 大台阶：顺着立面往外铺好几格、还要折返几段；
      - 半圆柱廊：弧顶离墙 r + 柱宽，两侧还要各张开 r 格。
    `size()` 和 `loads()` 的归一化都要按它加余量，否则最外面那几段
    会被体素数组裁掉 —— 用户看到的是"楼梯/柱廊被切掉一角"。
    """
    west = north = east = south = 0
    for d in details or []:
        if not isinstance(d, dict):
            continue
        t = str(d.get("type", ""))
        try:
            vi = int(d.get("volume", 0) or 0)
        except (TypeError, ValueError):
            continue
        if not (0 <= vi < len(vols)):
            continue
        v = vols[vi]
        face = str(d.get("face", "south"))
        if t in ("bow_portico", "portico_bow"):
            r = max(3, int(d.get("r", 10) or 10)) + max(1, int(d.get("col_w", 2) or 2))
            depth = side = r
        elif t in ("grand_stairs", "staircase"):
            width = max(2, int(d.get("w", 5) or 5))
            out0 = max(1, int(d.get("out", 1) or 1))
            turns = max(0, min(6, int(d.get("turns", 1) or 0)))
            depth = out0 + (turns + 1) * (width + 1)
            side = 0
        else:
            continue
        if face == "south":
            south = max(south, depth)
            east = max(east, side)
            west = max(west, side)
        elif face == "north":
            north = max(north, depth)
            east = max(east, side)
            west = max(west, side)
        elif face == "east":
            east = max(east, depth)
            north = max(north, side)
            south = max(south, side)
        else:
            west = max(west, depth)
            north = max(north, side)
            south = max(south, side)
    return west, north, east, south


def _porch_zones(details: list, vols: list) -> list[tuple[int, int, int, int]]:
    """入口区：门廊 / 大门前面那一片的世界坐标矩形 (x0, z0, x1, z1)。

    为什么需要它：`_grow_volumes` 会按"结构数量"在南面自动长出一排凸出体
    和一个门厅（都是实心盒子），它们正好落在柱廊前面，把柱廊整个埋掉 ——
    蓝图里明明写了 portico，用户看到的还是一面墙。所以在长附属体量之前，
    先把入口占的位置圈出来，让增生绕开。
    """
    zones: list[tuple[int, int, int, int]] = []
    for d in details or []:
        if not isinstance(d, dict):
            continue
        t = str(d.get("type", ""))
        try:
            vi = int(d.get("volume", 0) or 0)
        except (TypeError, ValueError):
            continue
        if not (0 <= vi < len(vols)):
            continue
        v = vols[vi]
        face = str(d.get("face", "south"))
        reach = 0
        if t in ("portico", "portico_bow", "bow_portico"):
            reach = max(2, int(d.get("depth", 3) or 3))
            if t != "portico":
                reach = max(reach, int(d.get("r", 10) or 10))
        elif t in ("grand_stairs", "staircase"):
            width = max(2, int(d.get("w", 5) or 5))
            turns = max(0, min(6, int(d.get("turns", 1) or 0)))
            reach = max(width, turns * (width + 1))
        elif t == "door":
            reach = 0  # 门本身不占墙外，旁边的凸出体可以留
        if reach <= 0:
            continue
        # 沿墙方向的范围：门廊按宽度/半径算，取宽一点，宁可少长一两个盒子
        if t in ("bow_portico", "portico_bow"):
            half = int(d.get("r", 10) or 10) + 1
            u0, u1 = int(d.get("u", v.w // 2)) - half, int(d.get("u", v.w // 2)) + half
        else:
            u0 = int(d.get("u", 0) or 0) - 1
            u1 = u0 + max(4, int(d.get("w", 6) or 6)) + 2
        if face in ("south", "north"):
            x0, x1 = v.x + u0, v.x + u1
            z0, z1 = (v.z1, v.z1 + reach) if face == "south" else (v.z - reach, v.z)
        else:
            z0, z1 = v.z + u0, v.z + u1
            x0, x1 = (v.x1, v.x1 + reach) if face == "east" else (v.x - reach, v.x)
        zones.append((x0, z0, x1, z1))
    return zones


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
    # 比例尺：1 米 = 多少格。层高上限跟着它走 —— 一张"精细模型"（8 格/米）
    # 里一层楼本来就有 20 多格，还按 6 格卡，等于禁止做大比例，细节也就没了。
    scale: float = 2.0
    # 结构数量等级 1~5：不是"细节多少"，是建筑物由几个部分组成
    complexity: int = 1
    # 室内设计：每层随机隔房间（留门洞）+ 层间架楼梯（上层楼板开楼梯井）。
    # 这是构建端的一次成形，不进 AI 提示词 —— AI 只需要写好外壳。
    interior: bool = False
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
        w = max(v.x1 + v.pad()[2] for v in self.volumes) + 1
        # 斜层把顶面/底面推出体块基准高度，包围盒要跟着长高，
        # 否则斜带的最高那几列会被数组裁掉，远看像被削平了。
        h = max(v.y1 + max(0, v.ypad()[1]) for v in self.volumes) + 1
        d = max(v.z1 + v.pad()[3] for v in self.volumes) + 1
        # 大台阶天生站在墙外：宽度朝外铺、还要折返好几段。不算进来，
        # 最外面那几段会被体素数组裁掉 —— 表现是"楼梯爬到一半断掉"。
        _bw, _bn, _be, _bs = _stairs_bleed(self.details, self.volumes)
        w += _be
        d += _bs
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
        # 尖顶也是"立"出来的：塔顶再往上 sh 格，不预留会只剩半截锥
        spire = max(
            (
                int(d.get("h", 0) or 0) or 24
                for d in self.details
                if str(d.get("type")) == "spire"
            ),
            default=0,
        )
        if spire:
            roof_h = max(roof_h, spire + 2)
        # 穹顶同理：它从体块顶面往上 h 格，size 不预留就只留下最下面三圈
        # （实测泰姬陵的穹顶被裁成一层"同心方环"，看着像屋面装饰）。
        dome = max(
            (
                int(d.get("h", 0) or 0) or 12
                for d in self.details
                if str(d.get("type")) == "dome"
            ),
            default=0,
        )
        if dome:
            roof_h = max(roof_h, dome + 3)
        # 观赏性细节里的天线/桅杆会从甲板（体块顶面）再往上竖 4~5 格，
        # 不预留就会被尺寸裁掉，只在旗杆旁边留一根断头柱子。
        if self.ornament:
            roof_h = max(roof_h, 6)
        return (w + 2, h + max(4, roof_h + 2), d + 2)


def _grow_volumes(vols: list[Volume], mats: dict, level: int, seed: int,
                  avoid: list[tuple[int, int, int, int]] | None = None) -> None:
    """按"结构数量"等级，在主体上长出一圈附属体量。

    为什么要有这一层：
      照片里的建筑有几十个组成部分（角塔、凸窗、楼梯间、侧翼、设备房、烟囱），
      而 AI 写蓝图时只给 3~5 个体块 —— 它写不了三十个，坐标也一定算歪。
      所以"结构数量"不是让 AI 多写，而是由构建器按规则**长**出来：
      主体骨架仍由 AI 定（比例、错位、屋顶），附属体量交给程序。

    等级（level）：
      1 简易：只有主体（等于以前的行为）
      2 基本：前两角各一座角塔
      3 标准：四角角塔 + 南面一排凸窗
      4 精细：+ 侧翼 + 楼梯间 + 屋顶设备
      5 复杂：+ 更密的凸窗 + 烟囱 + 双倍设备（体块总数可到 25~30）

    所有附属体量都是**实心、贴着主体**的，所以不会悬空；
    材质统一取主体的墙色与 trim，避免一栋楼被撒成彩色积木。
    """
    if level <= 1:
        return
    mains = [v for v in vols if not v.aux]
    if not mains:
        return
    # 圆形 / 八边形 / 多边形平面不长附属体量：
    # 角塔是"矩形四角"的概念，套到斗兽场的椭圆环上就是四根莫名方柱立在场地里
    # （实测 t02 的黄色柱子）。这类建筑的结构感应该来自环形分段，不是角塔。
    if any(v.plan() is not None for v in mains):
        return
    y0 = min(v.y for v in mains)
    # 用**全部主体的联合包围盒**当基准，而不是"面积最大的那一块"。
    # 用单块做基准时，角塔会落在中殿四角、被两翼夹住，加 30 个体块
    # 轮廓也看不出变化（第一版就是这样白做的）。
    bx0 = min(v.x for v in mains)
    bx1 = max(v.x1 for v in mains)
    bz0 = min(v.z for v in mains)
    bz1 = max(v.z1 for v in mains)
    bh0 = max(v.y1 for v in mains) - y0 + 1

    class _Box:
        x = bx0
        x1 = bx1
        z = bz0
        z1 = bz1
        y = y0
        w = bx1 - bx0 + 1
        d = bz1 - bz0 + 1
        h = bh0
        mat = mains[0].mat
        area = w * d

        @property
        def y1(self):
            return y0 + bh0 - 1

    base = _Box()  # type: ignore[assignment]
    wall = base.mat
    trim = mats.get("trim") or wall
    rnd = random.Random(seed or 1)
    add: list[Volume] = []

    def push(x: int, y: int, z: int, w: int, h: int, d: int, mat: Block | None = None) -> None:
        if w < 1 or h < 2 or d < 1:
            return
        # 入口区（门廊/大门前面那一片）不许再长东西。
        # 门厅、南面凸出体都是实心盒子，正好盖在柱廊上 —— 白宫那版
        # 明明写了 portico，用户看到的还是一面墙，就是这个原因。
        if avoid:
            for (ax0, az0, ax1, az1) in avoid:
                if x <= ax1 and x + w - 1 >= ax0 and z <= az1 and z + d - 1 >= az0:
                    return
        add.append(
            Volume(
                x=int(x), y=max(0, int(y)), z=int(z),
                w=int(w), h=int(h), d=int(d),
                mat=mat or wall, hollow=False, aux=True,
            )
        )

    # ---- 角塔：建筑的"结构感"八成来自四个角有没有东西 ----
    # 角塔要**高过主体**：和主体齐平或更矮，等轴测下就被主体吞掉了，
    # 结构数量加上去也看不出轮廓变化 —— 这是第一版最失败的地方。
    t = max(4, min(9, base.w // 7))
    th = max(6, int(base.h * 0.95))
    corners = [
        (base.x, base.z),
        (base.x1 - t + 1, base.z),
        (base.x, base.z1 - t + 1),
        (base.x1 - t + 1, base.z1 - t + 1),
    ]
    picks = corners if level >= 3 else corners[:2]
    for i, (cx, cz) in enumerate(picks):
        hh = th + (i % 2) * max(3, base.h // 4)
        push(cx, base.y, cz, t, hh, t, trim)

    # ---- 侧翼：左右各接一段更矮的体量，主体立刻从"一个盒子"变成"一组" ----
    if level >= 4:
        fw = max(5, int(base.w * 0.24))
        fd = max(5, int(base.d * 0.7))
        fh = max(5, int(base.h * 0.5))
        zc = base.z + (base.d - fd) // 2
        # 侧翼是唯一会往外扩张的结构，加之前先看总宽会不会超上限；
        # 超了就只做一侧，别把模型撑成一条横板。
        room = MAX_SPAN - base.w
        if room >= fw * 2:
            push(base.x1 + 1, base.y, zc, fw, fh, fd, trim if level >= 5 else wall)
            push(base.x - fw, base.y, zc, fw, fh, fd, trim if level >= 5 else wall)
        elif room >= fw:
            push(base.x1 + 1, base.y, zc, fw, fh, fd, trim if level >= 5 else wall)

    # ---- 南面凸出体：按开间等距排，是"几十个结构"里数量最多的一类 ----
    if level >= 3:
        span = 5 if level >= 5 else 8
        cnt = max(1, (base.w - 2) // span)
        for i in range(cnt):
            u = base.x + 2 + i * span
            push(u, base.y, base.z1 + 1, 4, max(6, base.h // 3), 3, wall)

    # ---- 门厅：南面中央整块凸出来，入口那一组立刻有"体量" ----
    if level >= 4:
        hw = max(6, int(base.w * 0.34))
        hd = max(4, min(8, base.d // 4))
        hh2 = max(6, int(base.h * 0.55))
        push(base.x + (base.w - hw) // 2, base.y, base.z1 + 1, hw, hh2, hd, trim)

    # ---- 楼梯间：北面凸出一个高一点的体量，屋顶轮廓就活了 ----
    if level >= 4:
        push(base.x + max(1, base.w // 2 - 3), base.y, base.z - 4, 6, base.h + 2, 4, trim)

    # ---- 屋顶设备：通风井 / 电梯机房 / 水箱 ----
    if level >= 4:
        for v in sorted(mains, key=lambda v: -v.area)[:3]:
            for _ in range(2 if level >= 5 else 1):
                w = rnd.randint(2, 4)
                d = rnd.randint(2, 4)
                x = rnd.randint(v.x + 1, max(v.x + 1, v.x1 - w))
                z = rnd.randint(v.z + 1, max(v.z + 1, v.z1 - d))
                push(x, v.y1 + 1, z, w, rnd.randint(2, 4), d, trim)

    # ---- 烟囱 ----
    if level >= 5:
        for _ in range(2):
            v = base
            x = rnd.randint(v.x + 2, max(v.x + 2, v.x1 - 2))
            z = rnd.randint(v.z + 2, max(v.z + 2, v.z1 - 2))
            push(x, v.y1 + 1, z, 2, rnd.randint(3, 6), 2, trim)

    vols.extend(add)


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


def _soften(mats: dict, keep_roof: bool = False) -> dict:
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
        # 现代建筑的屋面是"白色屋面平台"：墙和屋顶同色是设计意图，
        # 按老规则压暗一档，模型顶上就变成一大片深灰，iso 图里像盖了块铁皮。
        if role == "roof" and keep_roof:
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
    pal = {
        k: v for k, v in (raw.get("palette") or {}).items() if v is not None
    }
    defaults = {
        "wall": "sandstone",
        "trim": "concrete:7",
        "roof": "hardened_clay",
        "glass": "stained_glass:11",
        "accent": "dark_oak_planks",
        "floor": "stonebrick",
    }
    mats = {k: parse_material(pal.get(k, v)) for k, v in defaults.items()}
    btype_raw = str((raw.get("building") or {}).get("type") or "")
    mats = _soften(mats, keep_roof=is_modern_type(btype_raw))

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
    def _clean(d) -> dict:
        """去掉值为 null 的键。

        被 max_tokens 截断的蓝图经过"补括号"修复后，常常留下
        `"h": null` 这种半截字段；`int(None)` 会直接抛异常，
        用户看到的就是"生成失败"。删掉键 → 取默认值，比报错有用。
        """
        return {k: v for k, v in (d or {}).items() if v is not None} if isinstance(d, dict) else {}

    for item in raw.get("volumes") or []:
        if not isinstance(item, dict):
            continue
        item = _clean(item)
        mat = _resolve(item.get("mat"), mats, "wall")
        tp = item.get("taper") or {}
        taper_axis = str(tp.get("axis", "")).lower() if isinstance(tp, dict) else ""
        if taper_axis not in ("x", "z"):
            taper_axis = ""
        # 斜置：允许 "lean": {"x": 6, "z": -2} 或平铺的 lean_x / lean_z。
        # 上限 ±12 格 —— 再大就不是倾斜体块，是整栋楼要倒了。
        ln = item.get("lean") or {}
        lean_x = lean_z = 0
        if isinstance(ln, dict):
            lean_x = int(ln.get("x", 0) or 0)
            lean_z = int(ln.get("z", 0) or 0)
        lean_x = max(-12, min(12, lean_x or int(item.get("lean_x", 0) or 0)))
        lean_z = max(-12, min(12, lean_z or int(item.get("lean_z", 0) or 0)))
        # 平面剪切：单位是"走完整个进深 / 开间，横移多少格"
        sk = item.get("skew") or {}
        skew_x = skew_z = 0
        if isinstance(sk, dict):
            skew_x = int(sk.get("x", 0) or 0)
            skew_z = int(sk.get("z", 0) or 0)
        skew_x = max(-12, min(12, skew_x or int(item.get("skew_x", 0) or 0)))
        skew_z = max(-12, min(12, skew_z or int(item.get("skew_z", 0) or 0)))
        # 竖向坡度：允许 "tilt": {"axis":"x", "bottom":8, "top":8}，
        # 也允许只写 "tilt": {"x": 8}（= 底面顶面同斜率，整块斜板）。
        tl = item.get("tilt") or {}
        tilt_axis = ""
        tilt_bottom = tilt_top = 0
        if isinstance(tl, dict):
            tilt_axis = str(tl.get("axis", "")).lower()
            try:
                tilt_bottom = int(tl.get("bottom", 0) or 0)
                tilt_top = int(tl.get("top", 0) or 0)
            except (TypeError, ValueError):
                tilt_bottom = tilt_top = 0
            if not (tilt_bottom or tilt_top):
                # 简写：{"x": 8} → 沿 x 整块斜 8 格
                for ax in ("x", "z"):
                    try:
                        val = int(tl.get(ax, 0) or 0)
                    except (TypeError, ValueError):
                        val = 0
                    if val:
                        tilt_axis, tilt_bottom, tilt_top = ax, val, val
                        break
        if tilt_axis not in ("x", "z"):
            tilt_axis = str(item.get("tilt_axis", "")).lower()
            if tilt_axis not in ("x", "z"):
                tilt_axis = ""
        # 平铺写法：tilt_x / tilt_z 直接给整块斜率
        if not tilt_axis:
            for ax in ("x", "z"):
                try:
                    val = int(item.get(f"tilt_{ax}", 0) or 0)
                except (TypeError, ValueError):
                    val = 0
                if val:
                    tilt_axis, tilt_bottom, tilt_top = ax, val, val
                    break
        try:
            tilt_bottom = int(item.get("tilt_bottom", tilt_bottom) or 0)
            tilt_top = int(item.get("tilt_top", tilt_top) or 0)
        except (TypeError, ValueError):
            pass
        # 上限 ±16：一条 40 格长的带子斜 16 格已经是 22°，再多就是墙要倒
        tilt_bottom = max(-16, min(16, tilt_bottom))
        tilt_top = max(-16, min(16, tilt_top))
        if not tilt_axis:
            tilt_bottom = tilt_top = 0
        # 平面轮廓：shape 认名字（含中文），poly 认顶点，chamfer 认切角格数。
        # 给了 poly 就按顶点算尺寸 —— AI 常常只写顶点、忘了 w/d。
        shape = SHAPES.get(str(item.get("shape") or item.get("plan_shape") or "").strip().lower(), "")
        poly: list[list[float]] = []
        raw_poly = item.get("poly")
        if isinstance(raw_poly, list) and len(raw_poly) >= 3:
            for p in raw_poly[:16]:
                try:
                    if isinstance(p, (list, tuple)) and len(p) >= 2:
                        poly.append([float(p[0]), float(p[1])])
                    elif isinstance(p, dict):
                        poly.append([float(p.get("x", 0)), float(p.get("z", 0))])
                except (TypeError, ValueError):
                    poly = []
                    break
        if poly:
            shape = "poly"
        if shape == "poly" and not poly:
            shape = ""
        try:
            chamfer = int(item.get("chamfer", 0) or 0)
        except (TypeError, ValueError):
            chamfer = 0
        chamfer = max(0, min(12, chamfer))
        # 尺寸：有 poly 就用顶点范围兜底，多边形永远落在 w×d 的框里
        def _dim(key: str, default: int, idx: int) -> int:
            try:
                val = int(item.get(key, 0) or 0)
            except (TypeError, ValueError):
                val = 0
            if val <= 0 and poly:
                val = int(round(max(p[idx] for p in poly)))
            return max(1, val or default)
        w_v = _dim("w", 8, 0)
        d_v = _dim("d", 8, 1)
        if poly:
            # 顶点可能从非零开始：平移到 0 点，再核对是否超出 w/d
            px0 = min(p[0] for p in poly)
            pz0 = min(p[1] for p in poly)
            if abs(px0) > 1e-6 or abs(pz0) > 1e-6:
                poly = [[round(p[0] - px0, 2), round(p[1] - pz0, 2)] for p in poly]
            w_v = max(w_v, int(round(max(p[0] for p in poly))))
            d_v = max(d_v, int(round(max(p[1] for p in poly))))
        vols.append(
            Volume(
                x=int(item.get("x", 0)),
                y=int(item.get("y", 0)),
                z=int(item.get("z", 0)),
                w=w_v,
                h=max(1, int(item.get("h", 6))),
                d=d_v,
                mat=mat,
                floors=int(item.get("floors", 0)),
                floor_mat=mats["floor"],
                hollow=bool(item.get("hollow", True)),
                taper_axis=taper_axis,
                taper_end=str(tp.get("end", "max")) if taper_axis else "max",
                taper_to=max(1, int(tp.get("to", 1) or 1)) if taper_axis else 1,
                taper_len=max(0, int(tp.get("len", 0) or 0)) if taper_axis else 0,
                lean_x=lean_x,
                lean_z=lean_z,
                skew_x=skew_x,
                skew_z=skew_z,
                shape=shape,
                poly=poly,
                chamfer=chamfer,
                tilt_axis=tilt_axis,
                tilt_bottom=tilt_bottom,
                tilt_top=tilt_top,
            )
        )
    # 坡度不能吃掉体块自己的厚度：h=8 的盒子斜底 12 格，远端只剩负厚度，
    # 那一列会退化成 1 格厚的刀片（y_span 里被迫夹成 (y0,y0)）。
    for v in vols:
        if not v.tilted:
            continue
        lim = max(1, v.h - 2)
        v.tilt_bottom = max(-v.h, min(lim, v.tilt_bottom))
        v.tilt_top = max(-lim, min(lim, v.tilt_top))

    if not vols:
        vols = [Volume(0, 0, 0, 12, 8, 10, mats["wall"], floors=2, floor_mat=mats["floor"])]

    windows = [_clean(w) for w in (raw.get("windows") or []) if isinstance(w, dict)]
    details = [_clean(d) for d in (raw.get("details") or []) if isinstance(d, dict)]
    aspect = float(raw.get("aspect") or 0)

    # ---- 尺寸归一：**只允许一个系数**，X / Y / Z 一起走 ----
    # 旧版把 target_width 和 target_height 当成两个独立指标，等于对模型做
    # 非等比变形。一张大宫殿照片（蓝图自身体量 72×12、比例 6.0）被要求
    # "宽 32、高 24"，于是一边压缩 0.44、一边拉伸 2.0，最终输出 32×24 的
    # 方盒子 —— 这就是"大宫殿做出来还是一栋大房子"的直接原因。
    # 现在宽度和高度共用一个 k，蓝图写成什么比例，模型就是什么比例；
    # 高度只受"层高 3~6 格"这条**结构**约束，不再由照片估算的米数拉伸。
    target_w = int(raw.get("target_width") or 0)
    try:
        scale_v = float(raw.get("scale") or 2.0)
    except (TypeError, ValueError):
        scale_v = 2.0
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
        # 上限跟着分辨率走：以前硬卡 2 倍，一栋 53 格的教堂最多放大到 106 格，
        # 想再细也没有格子可用了。现在允许一路放到 MAX_SPAN。
        k = min(3.4, max(0.55, k))
        if span * k > MAX_SPAN:
            k = MAX_SPAN / span
        # 建造高度上限：MC 1.12 的世界高度是 256。上海中心 632 米、8 格/米
        # 本该 5000 格，实测被 X 方向 132 限制到 783 格 —— 仍然装不进世界，
        # 方块数也爆到 108 万。这里按 250 格再压一次 k，宽度跟着缩，比例不变。
        if span_h > 0 and span_h * k > 235:
            k = 235.0 / span_h
        # 层高是唯一的硬约束，但它必须**整体降 k**，不能单独压 h。
        # 旧做法是按 floors×6 把每个体块的 h 掐回去：40 格宽、24 高的蓝图
        # 被拉成 80 宽之后，h 从 48 掐到 18 —— 得到 80×18 的一条平房，
        # 照片 1.8 的宽高比变成 4.5，这就是"模型和照片差很远"的头号原因。
        # 现在谁超标就整体缩：宽高比永远是蓝图写的那个。
        # 现代建筑的层高本来就更松（首层通高、无吊顶），上限给到 7 格；
        # 否则一栋 3 层的玻璃楼会被压在 18 格高，宽高比一缩再缩。
        # 层高上限 = max(6, 比例尺 × 3.3)：8 格/米时一层约 26 格，
        # 6 格是"1 格 ≈ 0.5 米"那种常规比例的常识，不能拿来卡精细模型。
        floor_cap = max(FLOOR_H[1], int(round(scale_v * 3.3)))
        cap = floor_cap + (1 if is_modern_type(btype_raw) else 0)
        if k > 1.0:
            for i, v in enumerate(vols):
                fl = int(v.floors or 0)
                if fl <= 0:
                    continue
                prog = max(
                    [
                        int(d["v"]) + max(1, int(d.get("h") or 1))
                        for d in details
                        if d.get("v") is not None and int(d.get("volume") or 0) == i
                    ]
                    or [0]
                )
                eff = max(fl, (max(prog, v.h) + cap - 1) // cap)
                lim = eff * cap
                if v.h * k > lim:
                    k = max(0.55, lim / max(1, v.h))
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
                # 倾斜量是格数，也跟着缩放：3 格高的小体块不该斜 8 格
                v.lean_x = int(round(v.lean_x * k))
                v.lean_z = int(round(v.lean_z * k))
                v.skew_x = int(round(v.skew_x * k))
                v.skew_z = int(round(v.skew_z * k))
                v.tilt_bottom = int(round(v.tilt_bottom * k))
                v.tilt_top = int(round(v.tilt_top * k))
                # 轮廓顶点同理：不缩的话，缩小后的体块会留一圈"多边形空边"，
                # 放大后则被 w/d 裁掉一个角。
                if v.poly:
                    v.poly = [
                        [min(v.w, max(0, int(round(p[0] * k)))), min(v.d, max(0, int(round(p[1] * k))))]
                        for p in v.poly
                    ]
                if v.chamfer:
                    v.chamfer = max(1, int(round(v.chamfer * k)))
            # 窗本身的尺寸不缩：2 格宽的窗缩成 1 格就只剩一块玻璃了。
            # 只缩窗阵的**位置**与节距，列数按新墙宽重算。
            for w in windows:
                w["u"] = max(0, int(round(int(w.get("u", 0)) * k)))
                w["v"] = max(0, int(round(int(w.get("v", 0)) * k)))
                w["gap_u"] = max(1, int(round(int(w.get("gap_u", 2)) * k)))
                w["gap_v"] = max(1, int(round(int(w.get("gap_v", 2)) * k)))
                w["cols"] = max(1, min(16, int(round(int(w.get("cols", 1)) * k))))
                # 放大时窗格也要跟着长大：53 格宽里 3×3 的窗，放到 122 宽
                # 还保持 3×3，立面就成了"一片灰墙上点了几个小蓝点"。
                # 缩小则不动 —— 2 格窗缩成 1 格就只剩一块玻璃。
                if k > 1.0:
                    w["w"] = max(1, int(round(int(w.get("w", 2)) * k)))
                    w["h"] = max(1, int(round(int(w.get("h", 2)) * k)))
            for d in details:
                for key in ("u", "v", "depth"):
                    if key in d:
                        d[key] = max(0, int(round(int(d[key]) * k)))
                # 弧形柱廊的半径 / 柱宽也是几何尺寸：不缩的话，一栋放大
                # 2.4 倍的楼配一个 6 格半径的门廊，弧廊会缩成一个小壁龛。
                if str(d.get("type")) in ("bow_portico", "portico_bow"):
                    if "r" in d:
                        d["r"] = max(3, int(round(int(d["r"]) * k)))
                    if "col_w" in d:
                        d["col_w"] = max(1, min(3, int(round(int(d["col_w"]) * k))))
                if "w" in d:
                    d["w"] = max(2, int(round(int(d["w"]) * k)))
                # 竖向高度跟着一起缩。以前只缩 v 不缩 h：一块 8 格高的幕墙
                # 放进 0.6 倍缩小的楼里，还是 8 格 —— 直接顶穿屋顶，
                # 上层的招牌带、女儿墙全被它盖掉。
                # 旗杆例外：桅杆高度是"旗要挂多高"，不是比例，缩了就变成短杆。
                if "h" in d and str(d.get("type")) != "flagpole":
                    d["h"] = max(1, int(round(int(d["h"]) * k)))

    # ---- 结构数量：按等级在主体上长出一圈附属体量 ----
    # 放在缩放之后：附属尺寸是按主体的最终大小推的，先长再缩会把
    # 角塔缩成两格的小疙瘩。
    try:
        _level = int(raw.get("complexity") or 1)
    except (TypeError, ValueError):
        _level = 1
    _grow_volumes(
        vols, mats, max(1, min(5, _level)), int(raw.get("seed") or 1),
        avoid=_porch_zones(details, vols),
    )

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
        # 斜置体块的顶层可能歪到负数那边，包围盒要把 lean 算进去，
        # 否则"往左倒"的那一层会被裁平，看着像被削了一刀。
        # 再留 2 格余量：檐口、勒脚、阳台、百叶都是**往墙外挑**的，
        # 墙贴到 x=0 时，西/北两侧挑出去的方块会被数组边界直接吞掉 ——
        # 表现是"房子东边有檐口、西边光秃秃"，而六个面明明写的是同一份蓝图。
        x0 = min(v.x + v.pad()[0] for v in vols) - 2
        y0 = min(v.y for v in vols)
        z0 = min(v.z + v.pad()[1] for v in vols) - 2
        # 北/西面的楼梯比檐口挑得远得多，余量要按它给
        _bw, _bn, _be, _bs = _stairs_bleed(details, vols)
        x0 = min(x0, min(v.x for v in vols) - _bw - 1)
        z0 = min(z0, min(v.z for v in vols) - _bn - 1)
        if x0 or y0 or z0:
            for v in vols:
                v.x -= x0
                v.y -= y0
                v.z -= z0

    # 尺寸以**归一之后**的真实体量为准回报，别把请求值当成结果值
    real_w = (max(v.x1 + v.pad()[2] for v in vols) + 1) if vols else 0
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

    # ---- 室内设计 ----
    # 只认显式开关（true / 1 / yes / on）。默认关：大多数人是拿模型去
    # 当摆件/远景，室内方块会白白多出几千块，还会把方块数预算吃掉。
    interior_raw = raw.get("interior")
    if isinstance(interior_raw, bool):
        interior = interior_raw
    elif isinstance(interior_raw, str):
        interior = interior_raw.strip().lower() in ("1", "true", "yes", "on")
    else:
        interior = bool(int(interior_raw or 0)) if isinstance(interior_raw, (int, float)) else False

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
        scale=scale_v,
        complexity=max(1, min(5, _level)),
        interior=interior,
        target_height=real_h,
        seed=int(raw.get("seed") or 0) or random.SystemRandom().randint(1, 99999),
        volumes=vols,
        windows=windows,
        roof=_clean(raw.get("roof")) or {"type": "flat", "parapet": 1},
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
        # 当前正在画的体块。斜置体块（lean）不能只斜外壳 —— 窗、幕墙、
        # 勒脚、女儿墙都在同一个斜面上，逐函数改坐标一定会漏。
        # 这里把"按高度平移"收在 put/carve/get 三个口子上：
        # 画谁的构件就先把 ctx 设成谁，剩下的照旧写正交坐标。
        self.ctx: Volume | None = None
        self.stair_log: list = []
        self.link_log: list = []
        for blk in spec.palette.values():
            self._idx(blk)

    def _off(self, x: int, y: int, z: int) -> tuple[int, int]:
        if self.ctx is None:
            return 0, 0
        return self.ctx.shift_at(x, y, z)

    @contextmanager
    def using(self, v: Volume):
        """把绘制上下文切到某个体块：之后所有 put/carve/get 都带它的斜率。"""
        prev, self.ctx = self.ctx, v
        try:
            yield
        finally:
            self.ctx = prev

    def _idx(self, blk: Block) -> int:
        if blk not in self.blocks:
            self.blocks.append(blk)
        return self.blocks.index(blk)

    def put(self, x: int, y: int, z: int, blk: Block) -> None:
        dx, dz = self._off(x, y, z)
        x += dx
        z += dz
        if 0 <= x < self.vox.shape[0] and 0 <= y < self.vox.shape[1] and 0 <= z < self.vox.shape[2]:
            self.vox[x, y, z] = self._idx(blk)

    def carve(self, x: int, y: int, z: int) -> None:
        dx, dz = self._off(x, y, z)
        x += dx
        z += dz
        if 0 <= x < self.vox.shape[0] and 0 <= y < self.vox.shape[1] and 0 <= z < self.vox.shape[2]:
            self.vox[x, y, z] = -1

    def get(self, x: int, y: int, z: int) -> int:
        """按"未斜置"的坐标读体素，自动补上 ctx 的偏移。"""
        dx, dz = self._off(x, y, z)
        x += dx
        z += dz
        if 0 <= x < self.vox.shape[0] and 0 <= y < self.vox.shape[1] and 0 <= z < self.vox.shape[2]:
            return int(self.vox[x, y, z])
        return -1

    # ---- 体块 ----
    def volume(self, v: Volume) -> None:
        with self.using(v):
            if v.taper_axis:
                self._volume_taper(v)
                return
            cells = v.plan()
            for x in range(v.x, v.x + v.w):
                for z in range(v.z, v.z + v.d):
                    if cells is not None and (x - v.x, z - v.z) not in cells:
                        continue
                    # 轮廓边缘 = 平面四邻有一个在轮廓外。矩形下等价于"贴 bbox 边"，
                    # 六边形/楔形下就是它真正的斜边。
                    rim = v.edge_at(x, z)
                    # 竖向坡度：每一列自己算顶底，整块就会斜着走
                    y0, y1 = v.y_span(x, z)
                    for y in range(y0, y1 + 1):
                        edge = rim or y in (y0, y1)
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
            cells = v.plan()
            for k in range(1, v.floors + 1):
                dy = k * step
                if dy >= v.h:
                    break
                for x in range(v.x, v.x + v.w):
                    for z in range(v.z, v.z + v.d):
                        if cells is not None and (x - v.x, z - v.z) not in cells:
                            continue
                        # 斜层里的楼板也是斜的：按列自己的底往上数，
                        # 否则一层斜带里会出现几条水平的楼板，看着像货架。
                        y0, y1 = v.y_span(x, z)
                        y = y0 + dy
                        if y >= y1:
                            continue
                        if self.get(x, y, z) < 0:
                            self.put(x, y, z, v.floor_mat)

    # ---- 窗阵 ----
    def _flat_run(self, v: Volume, face: str) -> tuple[int, int]:
        """立面 face 上最长的一段"平直墙"：(起点 u, 长度)。

        矩形体块就是整条边；六边形/八边形/圆塔的立面被斜边切成好几段，
        窗阵必须落在平的那一段上 —— 否则一半窗会顺着斜边开到侧墙去，
        正面看起来像被谁斜着切掉一块玻璃。
        """
        if v.plan() is None:
            return 0, (v.w if face in ("south", "north") else v.d)
        cells = v.plan() or frozenset()
        if face in ("south", "north"):
            arr: list[int | None] = [None] * v.w
            for (i, j) in cells:
                if arr[i] is None:
                    arr[i] = j
                else:
                    arr[i] = max(arr[i], j) if face == "south" else min(arr[i], j)
        else:
            arr = [None] * v.d
            for (i, j) in cells:
                if arr[j] is None:
                    arr[j] = i
                else:
                    arr[j] = max(arr[j], i) if face == "east" else min(arr[j], i)
        best_s, best_l = 0, 0
        cur_s, cur_l = 0, 0
        prev: object = object()
        for k, val in enumerate(arr):
            if val is None:
                if cur_l > best_l:
                    best_s, best_l = cur_s, cur_l
                cur_l = 0
                prev = object()
                continue
            if cur_l and val == prev:
                cur_l += 1
            else:
                if cur_l > best_l:
                    best_s, best_l = cur_s, cur_l
                cur_s, cur_l = k, 1
            prev = val
        if cur_l > best_l:
            best_s, best_l = cur_s, cur_l
        return best_s, best_l

    def _fit_run(self, v: Volume, face: str, u: int, w: int) -> tuple[int, int]:
        """把一段立面构件（幕墙/饰带/百叶/招牌）收进"平直墙"范围。

        多边形平面上模型常按 `w = 体块宽 − 2` 写幕墙，可六边形的正面只有
        0.4w 宽，多出来的部分会顺着 45° 斜边一路铺到侧墙上去 —— 远看像
        立面被斜切了一刀。这里统一裁到平直段，并居中。
        """
        if v.plan() is None or w <= 0:
            return u, w
        fs, fl = self._flat_run(v, face)
        if fl <= 0:
            return u, w
        if w > fl:
            w = fl
        # 原来偏在哪一侧就贴哪一侧；越过平直段就居中
        off = u - fs
        if off < 0 or off + w > fl:
            off = max(0, (fl - w) // 2)
        return fs + off, w

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
        # 多边形平面下"墙长"只算平直的那一段（六边形的斜边不算），
        # 否则窗会顺着 45° 边一路排到侧墙上。
        fstart, flen = self._flat_run(v, face)
        span = flen if flen else (v.w if face in ("south", "north") else v.d)
        # 单扇大窗（cols=1、w 写到 26 格）在多边形正面上会一路铺到 45° 斜边，
        # 玻璃随斜边逐格往后退，看起来就是一排白色小柱子把玻璃切成碎片。
        # 先把单扇窗的宽度压进平直墙段。
        bw = max(1, min(bw, span))
        fit = max(1, (span + gx) // (bw + gx))
        cols = min(cols, fit)
        used = cols * bw + (cols - 1) * gx
        if v.plan() is not None:
            # 平直段居中排：AI 给的 u 是按整个 bbox 算的，在多边形上没有意义
            u0 = fstart + max(0, (span - used) // 2)
        elif u0 + used > span:
            u0 = max(0, span - used)
        count = 0
        with self.using(v):
            for r in range(rows):
                for c in range(cols):
                    u = u0 + c * (bw + gx)
                    # **必须加 v.y**：`_face_pos` 拿的是世界高度，而蓝图里的
                    # v 是相对体块底面的。旧写法只在"所有体块都贴地 y=0"时
                    # 才碰巧对；一旦有体块坐在台基上（布达拉宫：白宫在 y=28），
                    # 整片窗阵会掉进体块下面，只有最下面几行"碰巧"落在体块
                    # 高度区间里 —— 表现是"白墙上只有脚边一排小窗"。
                    y = v.y + v0 + r * (bh + gy)
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

    def _roof_poly(self, kind: str, vols: list[Volume], cfg: dict, mat: Block) -> int:
        """多边形平面上的坡顶 / 孟莎顶：按轮廓逐层内缩（腐蚀）。

        收分半径由轮廓自己决定：每上一层，把"四邻不全在轮廓里"的那一圈丢掉。
        收到只剩一两格就铺平封口 —— 六边形的坡顶最后收成一个尖，
        楔形收成一条脊，都比 bbox 收分自然。
        """
        added = 0
        h = max(2, min(8, int(cfg.get("height", 4)))) if kind == "mansard" else max(1, int(cfg.get("height", 3)))
        for v in vols:
            cells = v.plan()
            if not cells:
                continue
            with self.using(v):
                cur = set(cells)
                limit = min(12, max(v.w, v.d))
                for k in range(1, limit + 1):
                    nxt = {
                        c
                        for c in cur
                        if all((c[0] + dx, c[1] + dz) in cur for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)))
                    }
                    if not nxt:
                        break
                    cur = nxt
                    y = v.y1 + k
                    # 孟莎顶只收固定层数；坡顶要一直收到闭合，否则顶面留一块平台
                    if kind == "mansard" and k > h:
                        break
                    if len(cur) <= 2:
                        for (i, j) in cur:
                            self.put(v.x + i, y, v.z + j, mat)
                            added += 1
                        break
                    for (i, j) in cur:
                        if any(
                            (i + dx, j + dz) not in cur
                            for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1))
                        ):
                            self.put(v.x + i, y, v.z + j, mat)
                            added += 1
        return added

    def roof(self) -> int:
        cfg = self.spec.roof or {}
        kind = str(cfg.get("type", "flat"))
        mat = _resolve(cfg.get("mat"), self.spec.palette, "roof")
        added = 0
        # 有尖顶的体块不再铺坡顶：坡顶会在塔顶收成一圈阶梯金字塔，
        # 尖顶再从那堆台阶里钻出来，看着像"金字塔上插了根针"。
        # 尖塔的塔身是平的，锥直接从顶面立起来才对。
        spire_vols = [
            self.spec.volumes[int(d.get("volume", 0))]
            for d in self.spec.details
            if str(d.get("type")) == "spire"
            and 0 <= int(d.get("volume", 0)) < len(self.spec.volumes)
        ]

        def skip(v) -> bool:
            return any(v is sv for sv in spire_vols)

        if kind == "flat":
            # 舰船的顶面就是甲板，不是女儿墙围出来的平屋面：
            # 默认不压那一圈墙，否则整艘船顶着一圈"屋顶护栏"，立刻变建筑。
            default_parapet = 0 if self.spec.subject != "building" else 1
            n = max(0, int(cfg.get("parapet", default_parapet) or 0))
            # 先把顶面换成屋面材料：体块是空心的，顶面原来是墙色，
            # 远看就是一块"粉色大平台"，跟屋顶完全没有关系。
            for v in self.spec.volumes:
                cells = v.plan()
                with self.using(v):
                    for x in range(v.x, v.x1 + 1):
                        for z in range(v.z, v.z1 + 1):
                            if cells is not None and (x - v.x, z - v.z) not in cells:
                                continue
                            # 斜层的顶面也是斜的：每列取自己的顶
                            ytop = v.y_span(x, z)[1]
                            cur = self.get(x, ytop, z)
                            if cur < 0:
                                continue
                            # 顶层的幕墙/天窗不换屋面材料。整层统一铺屋面板
                            # 会把"上层玻璃"那一条也变成灰墙 —— 从正面看，
                            # 一栋通高玻璃的店面上半截突然变实了。
                            if "glass" in self.blocks[cur].name_en:
                                continue
                            self.put(x, ytop, z, mat)
                            added += 1
            if n == 0:
                return added
            for v in self.spec.volumes:
                rim = self._rim(v)
                with self.using(v):
                    for (x, z, _dx, _dz) in rim:
                        ytop = v.y_span(x, z)[1]
                        for k in range(1, n + 1):
                            self.put(x, ytop + k, z, mat)
                            added += 1
            return added

        # 多边形平面（六边形/八边形/楔形）上的坡顶：按轮廓逐层腐蚀收分。
        # 用 bbox 收分会在切角处留下一圈悬空的屋面块，从等轴测一眼就看出来。
        poly_vols = [v for v in self.spec.volumes if v.plan() is not None and not skip(v)]
        if poly_vols:
            added += self._roof_poly(kind, poly_vols, cfg, mat)
        rect_vols = [v for v in self.spec.volumes if v.plan() is None and not skip(v)]
        if not rect_vols:
            return added

        if kind == "mansard":
            # 法式孟莎屋顶：宫殿、府邸、老式公寓的标配。
            # 平顶把这类建筑做成一栋办公楼，这是"看起来还是大房子"的第二个原因。
            h = max(2, min(8, int(cfg.get("height", 4))))
            for v in rect_vols:
                with self.using(v):
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
        for v in rect_vols:
            with self.using(v):
                # 坡顶必须**收到闭合**。只按 AI 写的 height 收（常见 4 格），
                # 在 14 格进深上会留下 8×6 的平台 —— 视觉模型一眼就报"这是平顶加厚檐口"，
                # 那是实情。所以收的层数取"够闭合"和"AI 写的"里的大者，上限 10。
                if kind == "hip":
                    short = min(v.w, v.d)
                    need = (short + 1) // 2
                else:
                    need = (v.d + 1) // 2
                hh = min(10, max(h, need))
                # 两个方向**同坡度**收（每层各退 1 格）：短边先闭合，
                # 长边还剩一段 —— 那一段就是庑殿顶的屋脊。
                # 早版按"长边按比例收得更快"写（kx = w/短边），
                # 结果两个方向同时归零，长条屋顶被收成一座金字塔，
                # 故宫那种横长的城门楼就变成了三个方锥。
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

    def _face_col(self, v: Volume, face: str, u: int) -> tuple[int, int] | None:
        """沿墙面偏移 u 定位到"这一列最外面那一格"：返回 (x, z)，不在墙上则 None。

        多边形平面上墙不一定贴在最外一格（六边形的斜边逐格退进来），
        斜层上同一列的高度范围也不一样 —— 所以定位与高度分开：
        先定位列，再由 `y_span` 决定这一列能画到哪一层。
        """
        if face in ("south", "north"):
            x = v.x + u
            if not (v.x <= x <= v.x1):
                return None
            rng = range(v.z1, v.z - 1, -1) if face == "south" else range(v.z, v.z1 + 1)
            for z in rng:
                if v.contains(x, z):
                    return x, z
            return None
        z = v.z + u
        if not (v.z <= z <= v.z1):
            return None
        rng = range(v.x1, v.x - 1, -1) if face == "east" else range(v.x, v.x1 + 1)
        for x in rng:
            if v.contains(x, z):
                return x, z
        return None

    def _face_pos(self, v: Volume, face: str, u: int, y: int) -> tuple[int, int, bool]:
        """把 (face, 沿墙偏移 u, 高度 y) 换成世界坐标 (x, z, 是否在立面内)。

        非矩形平面下这里很关键：沿 u 走过去，墙不一定贴在最外那一格
        （六边形的斜边是逐格退进来的）。所以按"这一列/行的最外一格"定位，
        窗、门、幕墙就会自动沿着斜边走 —— 不需要为多边形再写一套开窗。
        """
        pos = self._face_col(v, face, u)
        if pos is None:
            return v.x, v.z, False
        x, z = pos
        y0, y1 = v.y_span(x, z)
        return x, z, y0 <= y <= y1

    def _rim(self, v: Volume) -> list[tuple[int, int, int, int]]:
        """平面轮廓外沿的每一格，以及它朝外的方向 (x, z, dx, dz)。

        檐口、勒脚、女儿墙都靠它绕一圈。旧写法是"贴 bbox 四条边"，
        遇到六边形会在切角处铺出一圈悬空的方块。
        """
        out: list[tuple[int, int, int, int]] = []
        cells = v.plan()
        if cells is None:
            for x in range(v.x, v.x1 + 1):
                out.append((x, v.z1, 0, 1))
                out.append((x, v.z, 0, -1))
            for z in range(v.z + 1, v.z1):
                out.append((v.x1, z, 1, 0))
                out.append((v.x, z, -1, 0))
            return out
        for (i, j) in cells:
            x, z = v.x + i, v.z + j
            for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                if not v.contains(x + dx, z + dz):
                    out.append((x, z, dx, dz))
        return out

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
        # 半圆柱廊要跑在门之后（门洞在它背后），但早于栏杆/阳台
        "bow_portico": 4.2,
        "portico_bow": 4.2,
        "pediment": 5,
        "curtain_wall": 6,
        "door": 7,
        "steps": 8,
        # 大台阶是"结构"而不是装饰：它从地面垒上来，必须早于栏杆/女儿墙，
        # 否则后者会被这道斜坡挡住或埋掉。
        "grand_stairs": 8.2,
        "staircase": 8.2,
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
        # 立面构件：扶壁在窗之前（窗要挖在墩之间），尖拱窗/玫瑰窗在幕墙之后，
        # 小尖饰最后（它站在屋顶上）
        "buttress": 5.8,
        "lancet": 6.5,
        "rose": 6.6,
        # 穹顶比尖顶更早：它先占住屋面上方的中心，尖顶再往外立
        "dome": 15.2,
        # 尖顶站在塔楼顶面上，必须晚于屋顶、早于栏杆
        "spire": 15.5,
        "pinnacle": 15.6,
        "railing": 16,
        "flagpole": 17,
        # 招牌永远是最后一笔：它画在别的构件表面，不能被任何东西盖掉
        "signbox": 18,
    }

    def detail(self, item: dict) -> int:
        """给构件套上所属体块的斜率，再交给具体实现。

        斜置体块上的门、百叶、幕墙必须跟着斜面走：单独改每个构件的坐标
        一定会漏，所以统一在这一层把 ctx 切过去。
        """
        vi = int(item.get("volume", 0))
        if not (0 <= vi < len(self.spec.volumes)):
            return 0
        with self.using(self.spec.volumes[vi]):
            return self._detail(item)

    def _detail(self, item: dict) -> int:
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
        elif t in ("bow_portico", "portico_bow"):
            # 半圆柱廊：美国白宫南门廊、圆厅别墅、很多新古典府邸的正门。
            #
            # 为什么不能用 portico 凑：直廊是"一排柱子 + 一块平板"，
            # 弧廊是"柱子沿一段圆弧排开、檐部和上面的阳台也跟着弯"。
            # 白宫那张照片里最抓人的就是那圈围过来的柱子和弧形栏杆，
            # 做成直的，整栋楼立刻变成"某公司的办公楼"。
            #
            # 坐标：u 是弧心在立面上的位置，r 是半径（弧顶离墙多远），
            # count 是柱子数，spread 是半张角（默认 80°，接近真实半圆但留一点切口）。
            mat = _resolve(item.get("mat"), self.spec.palette, "trim")
            rail_mat = _resolve(item.get("rail"), self.spec.palette, "trim")
            face = str(item.get("face", "south"))
            u = int(item.get("u", v.w // 2))
            r = max(3, int(item.get("r", 10)))
            n = max(3, min(14, int(item.get("count", 6))))
            ch = max(2, int(item.get("h", 12)))
            cw = max(1, min(4, int(item.get("col_w", 2))))
            spread = float(item.get("spread", 80))
            balcony = bool(item.get("balcony", True))
            pos = self._face_col(v, face, u)
            if pos is None:
                return 0
            cx, cz = pos

            def at(a_deg: float, rad: float) -> tuple[int, int]:
                """极角 → 世界坐标（a=0 正对墙面，± 向两侧张开）。"""
                a = math.radians(a_deg)
                side = int(round(rad * math.sin(a)))
                out = int(round(rad * math.cos(a)))
                if face == "south":
                    return cx + side, cz + out
                if face == "north":
                    return cx - side, cz - out
                if face == "east":
                    return cx + out, cz - side
                return cx - out, cz + side

            half = (cw - 1) // 2

            def pillar(bx: int, bz: int, y0: int, y1: int, blk: Block) -> int:
                k = 0
                for dx in range(cw):
                    for dz in range(cw):
                        for y in range(y0, y1 + 1):
                            self.put(bx + dx - half, y, bz + dz - half, blk)
                            k += 1
                return k

            # 柱：沿圆弧等分。柱心落在弧上，柱身向两侧各铺 cw 格
            for i in range(n):
                a = -spread + 2 * spread * i / (n - 1)
                bx, bz = at(a, r)
                added += pillar(bx, bz, v.y, v.y + ch, mat)

            def ring(rad: int, y0: int, y1: int, blk: Block, every: int = 1,
                     thick: int = 1) -> int:
                """沿半径 rad 的圆弧按**格**铺。

                两个都不能省：
                  - 按角度采样会漏格，檐部变成一排断齿；
                  - 只铺 1 格厚时，45° 那几段相邻块只靠角相接，
                    等轴测里就退化成"一串散落的方块"。`thick` 让它往里
                    再铺一层，弧才连得起来。
                """
                k = 0
                for side in range(-rad, rad + 1, max(1, every)):
                    ang = math.degrees(math.asin(max(-1.0, min(1.0, side / float(rad)))))
                    for d in range(thick):
                        bx, bz = at(ang, rad - d)
                        for y in range(y0, y1 + 1):
                            self.put(bx, y, bz, blk)
                            k += 1
                return k

            # 檐部（额枋）：柱头之上两格高、三格进深，沿弧连成一圈。
            # 2 格进深时弧的两端会露出明显的锯齿（相邻块只差一格的台阶），
            # 3 格才叠成一圈连续的带子。
            added += ring(r, v.y + ch + 1, v.y + ch + 2, mat, thick=3)

            if not balcony:
                return added

            # 顶上的女儿墙：只做沿弧一圈的栏杆，**不铺整块半圆屋面**。
            # 铺了那块，檐部+屋面会糊成一个大白饼，边缘还是体素圆的锯齿 ——
            # 白宫门廊顶上本来就是一圈栏杆，不是屋顶。
            top = v.y + ch + 3
            added += ring(r, top, top + 1, rail_mat, thick=2)
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
                # 悬挑 / 退台的上层体块是坐在下层顶面上的：给它刷一圈勒脚，
                # 等于在半空画一道黑腰线。只有显式点名 volume 时才照做。
                if target is None and vv.y > 0:
                    continue
                rim = self._rim(vv)
                with self.using(vv):
                    for k in range(hh):
                        for (x, z, _dx, _dz) in rim:
                            y = vv.y_span(x, z)[0] + k
                            if self.get(x, y, z) >= 0 and self._exposed(x, y, z):
                                self.put(x, y, z, mat)
                                added += 1
        elif t == "cornice":
            # 檐口：墙顶一圈往外挑 1 格，平顶建筑全靠它收头
            out = max(0, int(item.get("out", 1)))
            for vv in self.spec.volumes:
                rim = self._rim(vv)
                with self.using(vv):
                    for (bx, bz, dx, dz) in rim:
                        # 斜层的檐口要跟着斜顶走，否则会有一半埋进墙里、
                        # 一半浮在空中
                        y = vv.y_span(bx, bz)[1]
                        for k in range(0, out + 1):
                            x, z = bx + dx * k, bz + dz * k
                            if not (0 <= x < self.vox.shape[0] and 0 <= z < self.vox.shape[2]):
                                continue
                            if k > 0:
                                # 挑出去落在别的体块墙上时跳过，接缝处不该有檐口
                                if self.get(x, y, z) >= 0 and not self._exposed(x, y, z):
                                    continue
                            # 顶到屋顶的幕墙不能被檐口盖掉。商业立面常把
                            # "上层玻璃"写在最上面 2 格，而檐口正好落在同一行：
                            # 一盖，整层玻璃消失，模型就变成"勒脚+招牌+灰墙"。
                            prev = self.get(x, y, z)
                            if prev >= 0 and "glass" in self.blocks[prev].name_en:
                                continue
                            self.put(x, y, z, mat)
                            added += 1
        elif t == "door":
            face = str(item.get("face", "south"))
            u = int(item.get("u", 2))
            dw = max(1, int(item.get("w", 2)))
            dh = max(2, int(item.get("h", 3)))
            u, dw = self._fit_run(v, face, u, dw)
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
            # 门前台阶：向外铺 step 格，宽度比门宽两格。
            # 起算点是"这一列的墙面"，不是 bbox 前沿 —— 六边形的正面退在
            # 里面，按 bbox 铺会在门前浮出一排台阶。
            step = max(0, int(item.get("steps", 0)))
            for k in range(1, step + 1):
                for du in range(-1, dw + 1):
                    ax, az, ok0 = self._face_pos(v, face, u + du, y0)
                    if not ok0:
                        continue
                    sx, sz = self._shift(face, k)
                    self.put(ax + sx, y0, az + sz, frame_mat)
                    added += 1
        elif t in ("grand_stairs", "staircase"):
            # 大台阶：贴着立面折返爬升，两侧栏墙跟着台阶走成锯齿。
            #
            # 山地宫堡（布达拉宫）、高台基神庙、山城民居的正面全是这个：
            # 照片里"一圈一圈往上绕的楼梯"，本质是一条折返的坡道加白色栏墙。
            # 缺了它，一栋爬在山上的宫堡就只剩几摞摆在地上的白盒子 ——
            # 这是布达拉宫最容易被认出来的一笔，比金顶还醒目。
            #
            # 坐标口径和别的构件一致：(face, u, y) 是立面坐标，out 是离墙格数。
            # 每一级 1 格高、沿墙面前进 run 格（默认 2）—— 1 格 = 1 级台阶，
            # 这是 1:1 方块分辨率下唯一能不靠贴图表达坡度的办法。
            # run=1（45°）在等轴测图里读成"一道斜墙"，run=2（27°）才像台阶。
            stair_mat = _resolve(item.get("mat"), self.spec.palette, "wall")
            rail_mat = _resolve(item.get("rail"), self.spec.palette, "wall")
            face = str(item.get("face", "south"))
            width = max(2, int(item.get("w", 5)))     # 梯宽（垂直于墙面）
            out0 = max(1, int(item.get("out", 1)))    # 第一段离墙几格
            base = v.y + int(item.get("y0", 0))
            top = v.y + (int(item.get("y1", 0)) or max(1, v.h - 1))
            steps = max(1, top - base)
            turns = max(0, min(6, int(item.get("turns", 1))))
            rail_h = max(0, int(item.get("rail_h", 1)))
            run = max(1, min(4, int(item.get("run", 2))))
            u = int(item.get("u", 0))
            sign = -1 if str(item.get("dir", "up")) in ("down", "left", "west", "north") else 1
            per = max(1, (steps + turns) // (turns + 1))
            done = 0
            for f in range(turns + 1):
                n = min(per, steps - done)
                if n <= 0:
                    break
                # 折返时**往里退**而不是往外挪：第一段在最外侧（低），
                # 第二段贴着墙往上走（高）。反过来摆的话，高的那段站在
                # 前面，把下面整段挡死，等轴测里只看得到一个白方块。
                band = out0 + (turns - f) * (width + 1)
                # 第二段以后**只从本段起点往下垒**，不再一直填到地面。
                # 否则折返段是一堵从地面立起的实墙，把下面那一段完全挡住 ——
                # 等轴测里只剩一个大方块，看不出是楼梯。
                fill_from = v.y if f == 0 else base + done
                for s in range(n):
                    y = base + done + s
                    # 一级踏步要占满 run 格进深：只放一格的话，会得到
                    # 一排互不相连的台阶片，中间留着 1 格宽的沟，
                    # 远看是"墙前插了一排白梳子"。
                    for tt in range(run):
                        uu = u + sign * (s * run + tt)
                        pos = self._face_col(v, face, uu)
                        if pos is None:
                            continue
                        for k in range(width):
                            dx, dz = self._shift(face, band + k)
                            # 台阶是**实心**的：从体块底一直垒到这一级。
                            # 只铺一层踏步会得到一道悬空的斜线。
                            for yy in range(fill_from, y + 1):
                                self.put(pos[0] + dx, yy, pos[1] + dz, stair_mat)
                                added += 1
                        # 栏墙：跟着每一级升一格，于是外沿是一道锯齿轮廓 ——
                        # 照片里布达拉宫那道白边就是这么来的。
                        dx, dz = self._shift(face, band + width)
                        for hh in range(rail_h + 1):
                            self.put(pos[0] + dx, y + hh, pos[1] + dz, rail_mat)
                            added += 1
                done += n
                if done >= steps:
                    break
                # 转折平台：横跨当前梯宽 + 中间一格 + 下一段梯宽，
                # 高一级落在下一段起点上，两段才接得上。
                last_u = u + sign * (n * run - 1)
                landing_y = base + done
                for du in range(1, run + 1):
                    pos = self._face_col(v, face, last_u + sign * du)
                    if pos is None:
                        continue
                    for k in range(width * 2 + 1):
                        dx, dz = self._shift(face, band + k)
                        for yy in range(v.y, landing_y + 1):
                            self.put(pos[0] + dx, yy, pos[1] + dz, stair_mat)
                            added += 1
                    # 平台外沿也补一段栏墙，否则折返处的白边会断掉
                    if rail_h > 0:
                        dx, dz = self._shift(face, band + width * 2 + 1)
                        for hh in range(rail_h + 1):
                            self.put(pos[0] + dx, landing_y + hh, pos[1] + dz, rail_mat)
                            added += 1
                u = last_u + sign * 2
                band += width + 1
                # 折返：下一段是往回走的。忘了这一句，几段会连成一条长坡 ——
                # 看着像墙前堆了一条土堤，完全不是"盘旋而上"。
                sign = -sign
        elif t == "curtain_wall":
            # 玻璃幕墙：整片开窗 + 竖梃，别墅的临街面靠它出效果
            face = str(item.get("face", "south"))
            u = int(item.get("u", 1))
            y0 = v.y + int(item.get("v", 0))
            cw = max(2, int(item.get("w", 6)))
            # 多边形平面：幕墙只在平直墙上铺，斜边交给转角
            u, cw = self._fit_run(v, face, u, cw)
            ch = max(2, int(item.get("h", 6)))
            mullion = max(2, int(item.get("mullion", 3)))
            # 横梁：商铺/办公的幕墙一定有横向分格，只画竖梃会像一块蓝玻璃招牌
            transom = int(item.get("transom", 0))
            glass = glass_skin(_resolve(item.get("mat"), self.spec.palette, "glass"), item)
            frame_mat = _resolve(item.get("frame_mat"), self.spec.palette, "trim")
            for du in range(cw):
                pos = self._face_col(v, face, u + du)
                if pos is None:
                    continue
                x, z = pos
                cy0, cy1 = v.y_span(x, z)
                base = cy0 + int(item.get("v", 0))
                for dy in range(ch):
                    y = base + dy
                    if y > cy1:
                        break
                    self.carve(x, y, z)
                    vertical = du % mullion == 0 or du == cw - 1
                    # 横梁只在够高的幕墙上画。2 格高的玻璃上下各一道框，
                    # 一整条玻璃带就只剩框色 —— 上层玻璃最容易这样被吃掉。
                    horizontal = (dy == 0 and ch >= 4) or (ch >= 7 and dy == ch - 1) or (
                        transom >= 2 and dy % transom == 0
                    )
                    self.put(x, y, z, frame_mat if (vertical or horizontal) else glass)
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
                    and int(self.get(xx, yy, zz)) >= 0
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
            rim: list[tuple[int, int]] = []
            for du in range(bw):
                for dd in range(depth):
                    ax, az, ok = self._face_pos(v, face, u + du, y_abs)
                    if not ok:
                        continue
                    sx, sz = self._shift(face, 1 + dd)
                    self.put(ax + sx, y_abs, az + sz, mat)
                    added += 1
                # 前沿（阳台板最外面那一格），栏杆就立在这上面
                ax, az, ok = self._face_pos(v, face, u + du, y_abs)
                if ok:
                    sx, sz = self._shift(face, depth)
                    rim.append((ax + sx, az + sz))
            # 栏杆：前沿 + 两侧围成 U 形。只做前沿的话，等轴测图上就是
            # 一根悬空的深色横条，看不出是阳台。
            for (rx, rz) in rim:
                self.put(rx, y_abs + 1, rz, rail_mat)
                added += 1
            for dd in range(1, depth + 1):
                for du in (0, bw - 1):
                    ax, az, ok = self._face_pos(v, face, u + du, y_abs)
                    if not ok:
                        continue
                    sx, sz = self._shift(face, dd)
                    self.put(ax + sx, y_abs + 1, az + sz, rail_mat)
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
        elif t == "lancet":
            # 尖拱窗：哥特立面的基本单位。旧的 windows 只能开方洞，
            # 一排方洞铺过去读出来是办公楼；哥特的正立面全靠
            # "窄高 + 顶部收尖 + 深窗洞"这三件事。
            # 写成 cols/gap_u 一次排一串，AI 不用一个个写。
            face = str(item.get("face", "south"))
            u0 = int(item.get("u", 2))
            v0 = int(item.get("v", 2))
            lw = max(1, min(6, int(item.get("w", 2))))
            lh = max(4, min(40, int(item.get("h", 10))))
            cols = max(1, int(item.get("cols", 1)))
            gap = max(1, int(item.get("gap_u", 2)))
            glass = glass_skin(
                _resolve(item.get("mat"), self.spec.palette, "glass"),
                {"thick": True},
            )
            frame = _resolve(item.get("frame_mat"), self.spec.palette, "trim")

            def row_span(dy: int) -> tuple[int, int]:
                """这一行留哪几格：顶部两行收成尖，其余全宽。"""
                if dy < lh - 2:
                    return 0, lw
                shrink = 1 if dy == lh - 2 else max(1, (lw - 1) // 2 + 1)
                lo, hi = shrink, lw - shrink
                if hi <= lo:
                    mid = lw // 2
                    return mid, mid + 1
                return lo, hi

            for c in range(cols):
                u = u0 + c * (lw + gap)
                for dy in range(lh):
                    lo, hi = row_span(dy)
                    # 窗框：尖拱两侧各一列换成石材，窗洞才有"厚度"
                    for du in (lo - 1, hi):
                        if 0 <= du < lw or du in (lo - 1, hi):
                            x, z, ok = self._face_pos(v, face, u + du, v.y + v0 + dy)
                            if ok and self.get(x, v.y + v0 + dy, z) >= 0:
                                self.put(x, v.y + v0 + dy, z, frame)
                                added += 1
                    for du in range(lo, hi):
                        x, z, ok = self._face_pos(v, face, u + du, v.y + v0 + dy)
                        if not ok:
                            continue
                        self.carve(x, v.y + v0 + dy, z)
                        self.put(x, v.y + v0 + dy, z, glass)
                        added += 1
        elif t == "rose":
            # 玫瑰窗：正立面中央那个圆。在体素里用"圆内填彩窗、圆心压石十字"近似，
            # 远看就是一块发亮的圆形花窗 —— 哥特立面的识别点之一。
            face = str(item.get("face", "south"))
            u0 = int(item.get("u", 0))
            v0 = int(item.get("v", 0))
            dia = max(5, min(17, int(item.get("d", 9))))
            glass = glass_skin(
                _resolve(item.get("mat"), self.spec.palette, "glass"),
                {"thick": True},
            )
            frame = _resolve(item.get("frame_mat"), self.spec.palette, "trim")
            r = dia / 2.0
            for dy in range(dia):
                for du in range(dia):
                    fx, fy = du + 0.5 - r, dy + 0.5 - r
                    dist = (fx * fx + fy * fy) ** 0.5
                    if dist > r:
                        continue
                    x, z, ok = self._face_pos(v, face, u0 + du, v.y + v0 + dy)
                    if not ok:
                        continue
                    # 外圈 1 格做石框，内部 4 条辐条也用石
                    spoke = abs(fx) < 0.5 or abs(fy) < 0.5
                    blk = frame if (dist > r - 1.0 or spoke) else glass
                    self.carve(x, v.y + v0 + dy, z)
                    self.put(x, v.y + v0 + dy, z, blk)
                    added += 1
        elif t == "buttress":
            # 扶壁：贴着外墙往外凸一排竖墩，顶部再收一个小尖。
            # 哥特的立面节奏就是"窗 — 墩 — 窗 — 墩"，
            # 只有窗没有墩，墙是一整片平板，再多的窗也像幕墙。
            face = str(item.get("face", "south"))
            u0 = int(item.get("u", 0))
            v0 = int(item.get("v", 0))
            bw = max(1, min(4, int(item.get("w", 1))))
            bh = max(3, min(60, int(item.get("h", 12))))
            cnt = max(1, int(item.get("count", 4)))
            step = max(2, int(item.get("spacing", 6)))
            out = max(1, min(2, int(item.get("out", 1))))
            bmat = _resolve(item.get("mat"), self.spec.palette, "trim")
            dx, dz = self._shift(face, out)
            for c in range(cnt):
                u = u0 + c * step
                for dy in range(bh):
                    # 顶部 3 行逐层收窄，墩头才不是一根平顶柱子
                    shrink = 0
                    if dy >= bh - 3:
                        shrink = bh - dy - 1
                    for du in range(bw):
                        if du < shrink // 2 or du >= bw - shrink // 2:
                            continue
                        x, z, ok = self._face_pos(v, face, u + du, v.y + v0 + dy)
                        if not ok:
                            continue
                        self.put(x + dx, v.y + v0 + dy, z + dz, bmat)
                        added += 1
        elif t == "pinnacle":
            # 小尖饰：墙顶/女儿墙上成排的小尖。城堡、哥特复兴的天际线全靠它，
            # 没有这一排尖刺，屋顶收得再陡也像一座厂房。
            face = str(item.get("face", "south"))
            u0 = int(item.get("u", 0))
            cnt = max(1, int(item.get("count", 6)))
            step = max(2, int(item.get("spacing", 6)))
            ph = max(2, min(12, int(item.get("h", 3))))
            out = max(1, min(2, int(item.get("out", 1))))
            pmat = _resolve(item.get("mat"), self.spec.palette, "trim")
            dx, dz = self._shift(face, out)
            for c in range(cnt):
                u = u0 + c * step
                x, z, ok = self._face_pos(v, face, u, v.y1)
                if not ok:
                    continue
                base = v.y1 + 1
                for dy in range(ph):
                    self.put(x + dx, min(base + dy, self.vox.shape[1] - 1), z + dz, pmat)
                    added += 1
        elif t == "dome":
            # 穹顶：泰姬陵、圣瓦西里、清真寺、政府大楼的"洋葱/半球"。
            # 之前没有这个构件，AI 只能拿一堆 round 体块往上叠，
            # 叠出来是阶梯锥（泰姬陵实测就是这样），一眼不像。
            # style: "onion"（洋葱，中部鼓、两端收）/ "hemi"（半球）
            face = str(item.get("face", "south"))
            dh = int(item.get("h", 0) or 0)
            dr = float(item.get("r", 0) or 0)
            if dh <= 0:
                dh = max(6, int(min(v.w, v.d) * 0.55))
            if dr <= 0:
                dr = min(v.w, v.d) / 2.0 * 0.98
            dh = max(3, min(48, dh))
            style = str(item.get("style", "onion")).lower()
            dmat = _resolve(item.get("mat"), self.spec.palette, "trim")
            cx = (v.x + v.x1) / 2.0
            cz = (v.z + v.z1) / 2.0
            base = v.y1 + 1
            for k in range(dh):
                tk = k / float(max(1, dh - 1))
                if style == "hemi":
                    rr = dr * max(0.0, 1.0 - tk * tk) ** 0.5
                else:
                    rr = dr * max(0.0, math.sin(math.pi * tk)) ** 0.65
                y = base + k
                if y >= self.vox.shape[1]:
                    break
                x0, x1 = int(round(cx - rr)), int(round(cx + rr))
                z0, z1 = int(round(cz - rr)), int(round(cz + rr))
                if x1 - x0 <= 0 and z1 - z0 <= 0:
                    self.put(int(round(cx)), y, int(round(cz)), dmat)
                    added += 1
                    continue
                for x in range(x0, x1 + 1):
                    for z in range(z0, z1 + 1):
                        # 只铺外圈：穹顶是壳，内部看不见，铺满白费方块
                        if k == 0 or x in (x0, x1) or z in (z0, z1):
                            self.put(x, y, z, dmat)
                            added += 1
            # 顶尖：洋葱顶收在一根小立柱上
            top = base + dh
            if top < self.vox.shape[1]:
                self.put(int(round(cx)), top, int(round(cz)), dmat)
                added += 1
                if top + 1 < self.vox.shape[1]:
                    self.put(int(round(cx)), top + 1, int(round(cz)), dmat)
                    added += 1
        elif t == "spire":
            # 尖顶：哥特教堂、城堡塔楼、钟楼的天际线全靠它。
            # gable / hip 只会把方塔一层层收成"阶梯金字塔"（每层退一格），
            # 而真正的尖塔是"塔身上再立一根锥" —— 这两者在等轴测里一眼能分开。
            # 从体块顶面中心起，按锥面逐层收半径，收到 1 格为止。
            sp_mat = _resolve(item.get("mat"), self.spec.palette, "roof")
            sh = int(item.get("h", 0) or 0)
            if sh <= 0:
                sh = max(6, int(min(v.w, v.d) * 1.4))
            sh = max(3, min(60, sh))
            cx = (v.x + v.x1) / 2.0
            cz = (v.z + v.z1) / 2.0
            base = v.y1 + 1
            r0 = max(1.0, min(v.w, v.d) / 2.0)
            # 收分必须**每层退一格**：体素是 1 格一个量子，按 (1-t) 线性收
            # 在 18 层里只退得到 6 格，每三层才退一格，看着仍是阶梯金字塔。
            # 先按半径层数收成一点（陡锥），剩下高度立成 1 格宽的针 ——
            # 这才是哥特尖塔在方块里的样子。
            # 每 **2 层退 1 格**：每层退一格是 45° 的金字塔，哥特尖塔要陡得多
            cone = max(1, min(sh, int(round(r0 * 2))))
            step = (r0 - 0.5) / cone
            for k in range(sh):
                rr = max(0.5, r0 - k * step)
                y = base + k
                if y >= self.vox.shape[1]:
                    break
                x0, x1 = int(round(cx - rr)), int(round(cx + rr))
                z0, z1 = int(round(cz - rr)), int(round(cz + rr))
                if x1 - x0 <= 1 and z1 - z0 <= 1:
                    self.put(int(round(cx)), y, int(round(cz)), sp_mat)
                    added += 1
                    continue
                for x in range(x0, x1 + 1):
                    for z in range(z0, z1 + 1):
                        # 只铺外圈：尖顶是空心的，省方块也省得内部刷不到光
                        if k == 0 or x in (x0, x1) or z in (z0, z1):
                            self.put(x, y, z, sp_mat)
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
            u, bw = self._fit_run(v, face, u, bw)
            out = max(0, min(3, int(item.get("out", 1))))
            dx, dz = self._shift(face, 1)
            for du in range(max(1, bw)):
                pos = self._face_col(v, face, u + du)
                if pos is None:
                    continue
                cx, cz = pos
                # 斜层上的饰带跟着斜面走，不然会横向切进墙里
                cy0 = v.y_span(cx, cz)[0]
                for dy in range(bh):
                    x, z = cx, cz
                    y = cy0 + int(item.get("v", 0)) + dy
                    if out == 0:
                        # 贴面：只替换原来那格，不外挑（用来做"换料的腰线"）
                        if 0 <= x < self.vox.shape[0] and self.get(x, y, z) >= 0:
                            self.put(x, y, z, mat)
                            added += 1
                        continue
                    for k in range(1, out + 1):
                        self.put(x + dx * k, y, z + dz * k, mat)
                        added += 1
        elif t == "louver":
            # 竖向格栅 / 木百叶：店面转角、设备遮挡、幕墙分隔都用它。
            # 一排竖条在等轴测里是"疏密"，一块实心板是"死面"，
            # 这是现代立面看起来轻盈的关键。
            #
            # 材质默认跟着墙走，不要用 accent：现代建筑的竖向遮阳翼片
            # 是白色/银色的，做成深色木格栅会读成"黑色铁栅栏"，
            # 一栋白楼正面糊一排黑条，比不加还糟。
            modern = is_modern_type(str(self.spec.meta.get("building_type") or ""))
            mat = _resolve(item.get("mat"), self.spec.palette, "wall" if modern else "accent")
            face = str(item.get("face", "south"))
            u = int(item.get("u", 0))
            lw = max(1, int(item.get("w", 4)))
            y0 = v.y + int(item.get("v", 0))
            lh = max(2, int(item.get("h", 8)))
            u, lw = self._fit_run(v, face, u, lw)
            step = max(1, int(item.get("step", 2)))
            out = max(0, min(2, int(item.get("out", 1))))
            dx, dz = self._shift(face, out) if out else (0, 0)
            for du in range(lw):
                if du % step:
                    continue  # 留缝：格栅必须能看见后面的东西
                # 按列定位：斜层上每根百叶的起点/终点都不一样，
                # 正是这一格格的高度差把"斜带"画出来。用统一 y0 的话，
                # 斜体的高侧会露出一排只有半截的柱子，低侧则戳出屋面。
                pos = self._face_col(v, face, u + du)
                if pos is None:
                    continue
                x, z = pos
                cy0, cy1 = v.y_span(x, z)
                base = cy0 + int(item.get("v", 0))
                top = min(cy1, base + lh - 1)
                for y in range(base, top + 1):
                    self.put(x + dx, y, z + dz, mat)
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
            # 邻居按**它自己那一层**的斜率取坐标：斜置体块的上面一层
            # 比下面一层歪出去 1 格，偷懒用同层偏移会误判"头顶是空的"。
            ox, oz = self._off(x + dx, y + dy, z + dz)
            ax, ay, az = x + dx + ox, y + dy, z + dz + oz
            if not (0 <= ax < nx and 0 <= ay < ny and 0 <= az < nz):
                return True
            if self.vox[ax, ay, az] < 0:
                return True
        return False

    def _is_block(self, x: int, y: int, z: int, blk: Block) -> bool:
        ox, oz = self._off(x, y, z)
        x += ox
        z += oz
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

    # ---- 室内设计 ----
    # 开关打开后每层楼自动隔房间、层与层之间架楼梯。三件事必须一起做：
    # 房间要有门（否则是死盒子）、楼梯要在上层楼板开洞（否则爬不出去）、
    # 隔墙不能从楼梯井中间穿过去（否则楼梯被砌死）。
    #
    # 楼梯用整方块做「走一格高一格」—— MC 里没有更陡的走法。层高 4 格时
    # 就是 3 级台阶，刚好顶到上层楼板；层高 3 格只有 2 级，也能爬（要跳一下）。
    # 特殊平面（六边形/圆/楔形/收窄体）不做分隔，整块当一间 —— 直角隔墙
    # 塞不进斜轮廓，硬塞会在墙上戳出洞。

    INT_MIN_ROOM = 4      # 一间房的最小内空边长（格）
    INT_DOOR_MAX_H = 3    # 门高上限；房间矮时降到 2，2 格是硬下限

    def _wall_mat(self, rnd: random.Random) -> Block:
        """室内隔墙的材料：优先用线脚色，其次木作色，最后跟墙。

        不能直接用 `wall`：白墙房子里隔墙也是白的，打开屋门看进去
        只有一片白，等于没做室内。挑一个和墙不同色的角色，观感立刻分开。
        """
        pal = self.spec.palette
        wall = pal.get("wall")
        picks = [m for m in (pal.get("trim"), pal.get("accent")) if m is not None and m != wall]
        if not picks:
            picks = [wall or (self.spec.volumes[0].mat if self.spec.volumes else None)]
        picks = [p for p in picks if p is not None]
        return picks[rnd.randrange(len(picks))] if picks else self.spec.volumes[0].mat

    def _levels(self, v: Volume) -> list[tuple[int, int]]:
        """体块里每一层的 (楼板高度, 上层楼板高度)。最后一层的上层是屋面。"""
        step = max(2, v.h // max(1, v.floors)) if (v.floors and v.floor_mat is not None) else 0
        ys = [v.y]
        if step:
            for k in range(1, v.floors + 1):
                y = v.y + k * step
                if y >= v.y1:
                    break
                ys.append(y)
        return [(y, ys[i + 1] if i + 1 < len(ys) else v.y1) for i, y in enumerate(ys)]

    def _pick_split(self, rect, reserved: set, rnd: random.Random):
        """在房间里挑一条能砌墙的分割线：(axis, 位置, 起, 止)。

        两头各留 INT_MIN_ROOM 格，保证切出来的两半都还是一间房；
        整条线不能压到楼梯井上，压到就换一条，换不到就放弃这间 ——
        宁可少隔一间，也不能把楼梯砌死。
        """
        x0, x1, z0, z1 = rect
        need = 2 * self.INT_MIN_ROOM + 1
        axes = []
        if x1 - x0 + 1 >= need:
            axes.append("x")
        if z1 - z0 + 1 >= need:
            axes.append("z")
        if not axes:
            return None
        # 优先切"长的那一边"：总是随机切会把 26×16 的大房间切成
        # 一排 4×16 的走廊。切长边，切出来的房间才接近方正。
        if len(axes) == 1:
            axis = axes[0]
        elif abs((x1 - x0) - (z1 - z0)) <= 1:
            axis = axes[rnd.randrange(2)]
        else:
            axis = "x" if (x1 - x0) > (z1 - z0) else "z"
        if axis == "x":
            lo, hi, u0, u1 = x0, x1, z0, z1
        else:
            lo, hi, u0, u1 = z0, z1, x0, x1
        for _ in range(8):
            s = rnd.randint(lo + self.INT_MIN_ROOM, hi - self.INT_MIN_ROOM)
            if axis == "x":
                hit = any((s, u) in reserved for u in range(u0, u1 + 1))
            else:
                hit = any((u, s) in reserved for u in range(u0, u1 + 1))
            if not hit:
                return (axis, s, u0, u1)
        return None

    def _rooms(self, v: Volume, lo: int, hi: int, rnd: random.Random,
               reserved: set) -> int:
        """把一层楼隔成几间，返回砌了几道墙。

        `lo` 是这层的楼板高度，`hi` 是它头顶那块楼板（或屋面）。房间净空是
        lo+1 ~ hi-1，隔墙从地砖顶砌到天花板，门洞直接在这道墙上挖出来。
        """
        height = hi - lo - 1
        if height < 2:
            return 0
        x0, x1 = v.x + 1, v.x1 - 1
        z0, z1 = v.z + 1, v.z1 - 1
        if x1 - x0 < 2 or z1 - z0 < 2:
            return 0

        walls: list[tuple] = []
        rects = [(x0, x1, z0, z1)]
        area = (x1 - x0 + 1) * (z1 - z0 + 1)
        # 每 40 平方格一间：小屋 2 间、大宫殿 6 间。上限 6 是因为再多
        # 就变成一串小格子，玩家进去像走迷宫，观感反而不如少几间。
        target = max(2, min(6, area // 40))

        for _ in range(24):
            if len(rects) >= target:
                break
            order = sorted(
                range(len(rects)),
                key=lambda i: -((rects[i][1] - rects[i][0] + 1) * (rects[i][3] - rects[i][2] + 1)),
            )
            picked = None
            for idx in order:
                split = self._pick_split(rects[idx], reserved, rnd)
                if split:
                    picked = (idx, split)
                    break
            if picked is None:
                break
            idx, (axis, s, u0, u1) = picked
            rect = rects[idx]
            walls.append((axis, s, u0, u1))
            if axis == "x":
                rects[idx] = (rect[0], s - 1, rect[2], rect[3])
                rects.append((s + 1, rect[1], rect[2], rect[3]))
            else:
                rects[idx] = (rect[0], rect[1], rect[2], s - 1)
                rects.append((rect[0], rect[1], s + 1, rect[3]))

        door_h = min(self.INT_DOOR_MAX_H, height)
        built = 0
        for axis, s, u0, u1 in walls:
            placed: set[tuple[int, int, int]] = set()
            # 一道墙只用一种材料。在块循环里调 _wall_mat 会得到
            # 深棕/浅灰交替的"糖果条纹"墙 —— 第一版就是这样，
            # 房子里像贴了一排马赛克。
            mat = self._wall_mat(rnd)
            for y in range(lo + 1, hi):
                for u in range(u0, u1 + 1):
                    x, z = (s, u) if axis == "x" else (u, s)
                    if (x, z) in reserved:
                        continue
                    if self.get(x, y, z) < 0:
                        self.put(x, y, z, mat)
                        placed.add((x, y, z))
            if not placed:
                continue
            built += 1
            # 门：1 格宽，隔墙够长时给 2 格宽 —— 门洞大小跟着建筑大小走，
            # 但高度不低于 2（1 格高的门只能爬过去）。
            span = u1 - u0 + 1
            door_w = 2 if span >= 14 else 1
            for _ in range(8):
                start = rnd.randint(u0 + 1, max(u0 + 1, u1 - door_w))
                cells = []
                for du in range(door_w):
                    u = start + du
                    x, z = (s, u) if axis == "x" else (u, s)
                    cells.append((x, z))
                if any((x, z) in reserved for x, z in cells):
                    continue
                if any((x, lo + 1, z) not in placed for x, z in cells):
                    continue
                for y in range(lo + 1, lo + 1 + door_h):
                    for x, z in cells:
                        if (x, y, z) in placed:
                            self.carve(x, y, z)
                break
        return built

    def _stair_width(self, v: Volume) -> int:
        """楼梯宽度：建筑越大越宽（1~3 格）。

        一格宽的楼梯在大宅里像一根梯子，两个人对着走都过不去；
        但小屋里给 3 格宽又会把整个房间挤掉。按体块短边定档：
        <14 格 1 宽、14~23 格 2 宽、≥24 格 3 宽。
        """
        span = min(v.w, v.d)
        if span >= 24:
            return 3
        if span >= 14:
            return 2
        return 1

    def _stairs(self, v: Volume, y0: int, y1: int, rnd: random.Random,
                reserved: dict) -> int:
        """在 y0 层与 y1 层之间架一部楼梯，返回台阶数（0 = 放不下）。

        走法就是 MC 里最土也最好用的那种：往前走一格、往上一格，
        每级都是一整块方块。上层楼板在"头会撞到"的那两列开洞，
        于是楼梯井只有 2 格长，不是把整层楼板掀掉。

        梯宽按建筑大小给（见 _stair_width），并且贴着一面内墙铺开 ——
        两格以上的楼梯从外面看进来才有"这儿有楼梯"的体量。
        """
        rise = y1 - y0
        if rise < 3:
            return 0
        n = rise - 1  # 台阶块数：1 格高 1 格远，顶层那格由楼板自己当
        ix0, ix1 = v.x + 1, v.x1 - 1
        iz0, iz1 = v.z + 1, v.z1 - 1
        wid = self._stair_width(v)

        # (dx, dz) 是前进方向，(sx, sz) 是梯段起点，
        # (px, pz) 是加宽方向 —— 一律朝屋里，别长出墙外。
        cands = []
        # 贴着内墙根走，别占房间正中间；四个角都试一遍，
        # 总有一个角没被别的体块/窗框占掉。
        if ix1 - ix0 + 1 >= n + 2:
            cands.append((1, 0, ix0, iz0, 0, 1))
            cands.append((-1, 0, ix1, iz1, 0, -1))
        if iz1 - iz0 + 1 >= n + 2:
            cands.append((0, 1, ix0, iz0, 1, 0))
            cands.append((0, -1, ix1, iz1, -1, 0))
        rnd.shuffle(cands)
        mat = self._wall_mat(rnd)
        for dx, dz, sx, sz, px, pz in cands:
            # 加宽是垂直于梯段的：沿 x 走就占 z 方向，反之亦然。
            # 每种朝向各自算一次上限，留一格走道，别把房间横向堵死。
            wcap = (iz1 - iz0 + 1) if dx else (ix1 - ix0 + 1)
            w = max(1, min(wid, wcap - 1))
            cells = [
                (sx + dx * i + px * k, sz + dz * i + pz * k)
                for i in range(n)
                for k in range(w)
            ]
            ok = True
            for i in range(n):
                h = y0 + 1 + i
                # 台阶之上要有 2 格净空（玩家身高），撞到楼板的那几列
                # 待会儿开洞；这里先确认没有别的实体（邻栋的墙、窗台）。
                for k in range(w):
                    x = sx + dx * i + px * k
                    z = sz + dz * i + pz * k
                    for yy in range(h, min(y1, h + 3)):
                        if self.get(x, yy, z) >= 0:
                            ok = False
                            break
                    if not ok:
                        break
                if not ok:
                    break
            if not ok:
                continue
            for i in range(n):
                h = y0 + 1 + i
                for k in range(w):
                    x = sx + dx * i + px * k
                    z = sz + dz * i + pz * k
                    self.put(x, h, z, mat)
                    if h >= y1 - 2:
                        self.carve(x, y1, z)
            self.stair_log.append((y0, y1, cells))
            # 楼梯井 + 进出口各留一格：隔墙不许压在楼梯上/楼梯口
            band = set(cells)
            for k in range(w):
                band.add((sx - dx + px * k, sz - dz + pz * k))
                band.add((sx + dx * n + px * k, sz + dz * n + pz * k))
            for lv in (y0, y1):
                reserved.setdefault(lv, set()).update(band)
            return n
        return 0

    def interior(self) -> tuple[int, int]:
        """给每个空心体块加室内：隔墙 + 楼梯。返回 (隔墙数, 楼梯数)。"""
        if self.spec.subject != "building":
            return 0, 0
        rnd = random.Random((int(self.spec.seed) or 1) * 7919 + 17)
        walls = stairs = 0
        for v in self.spec.volumes:
            # 实心体块（角塔、凸窗、设备房）没有室内。收窄体、斜置体、
            # 斜楼板的楼板/墙每层都在平移或倾斜，按固定标高隔出来的房间
            # 会和外壳错位 —— 实测楼梯井会被上层楼板盖住，二层变成密室。
            # 这些形体一律当"整块一间"，不做室内。
            if v.aux or not v.hollow or v.taper_axis or v.slanted:
                continue
            if v.plan() is not None:
                continue  # 六边形/圆/楔形：整块当一间，不做分隔
            if v.w < 8 or v.d < 8:
                continue
            levels = self._levels(v)
            if not levels:
                continue
            reserved: dict[int, set] = {}
            with self.using(v):
                for i in range(len(levels) - 1):
                    y0, y1 = levels[i]
                    stairs += 1 if self._stairs(v, y0, y1, rnd, reserved) else 0
                for i, (lo, hi) in enumerate(levels):
                    walls += self._rooms(v, lo, hi, rnd, reserved.get(lo, set()))
        doors = self._link_volumes()
        return walls + doors, stairs

    def _link_volumes(self) -> int:
        """相邻体块之间开一道门，返回开的数量。

        主楼 + 侧翼各是一个独立体块，各画各的壳，贴在一起时中间是
        两层墙 —— 从主楼进不去侧翼，屋里再漂亮也没意义。这里在共用的
        那面墙上开一个 2 格高 1 格宽的门洞，只找"两边都站在地板上、
        头顶两格都是空的"位置，不会凿穿楼板或屋面。
        """
        vols = [
            v for v in self.spec.volumes
            if v.hollow and not v.aux and not v.slanted and not v.taper_axis and v.plan() is None
        ]
        pairs: list[tuple] = []
        for i, a in enumerate(vols):
            for b in vols[i + 1:]:
                # 间隔 0 是贴在一起（两层壳），间隔 1 是中间夹一格空气 ——
                # 两种都算"一栋楼的两个部分"，都要能走通。间隔再大就是
                # 两栋独立的房子了，凿穿它反而错。
                if b.x - a.x1 in (1, 2):
                    pairs.append(("x", a, b))
                elif a.x - b.x1 in (1, 2):
                    pairs.append(("x", b, a))
                elif b.z - a.z1 in (1, 2):
                    pairs.append(("z", a, b))
                elif a.z - b.z1 in (1, 2):
                    pairs.append(("z", b, a))

        made = 0
        for axis, near, far in pairs:
            if axis == "x":
                u0 = max(near.z + 1, far.z + 1)
                u1 = min(near.z1 - 1, far.z1 - 1)
            else:
                u0 = max(near.x + 1, far.x + 1)
                u1 = min(near.x1 - 1, far.x1 - 1)
            if u1 - u0 < 1:
                continue
            y0 = max(near.y, far.y) + 2
            y1 = min(near.y1, far.y1) - 2

            spot = None
            for y in range(y0, y1 + 1):
                for u in range(u0, u1 + 1):
                    if axis == "x":
                        wall = [(near.x1, y, u), (far.x, y, u)]
                        side = [(near.x1 - 1, y, u), (far.x + 1, y, u)]
                    else:
                        wall = [(u, y, near.z1), (u, y, far.z)]
                        side = [(u, y, near.z1 - 1), (u, y, far.z + 1)]
                    # 墙上是实心（两格高），两侧各是一间站得下人的屋子
                    if not all(self.get(x, yy, z) >= 0 for x, _, z in wall for yy in (y, y + 1)):
                        continue
                    ok = True
                    for x, _, z in side:
                        if self.get(x, y, z) >= 0 or self.get(x, y + 1, z) >= 0:
                            ok = False
                            break
                        if self.get(x, y - 1, z) < 0:  # 脚下没地板 = 悬空，另找
                            ok = False
                            break
                    if ok:
                        spot = (u, y)
                        break
                if spot:
                    break
            if not spot:
                self.link_log.append((axis, near.x, far.x, near.z, far.z, None))
                continue
            u, y = spot
            self.link_log.append((axis, near.x, far.x, near.z, far.z, spot))
            for yy in (y, y + 1):
                if axis == "x":
                    self.carve(near.x1, yy, u)
                    self.carve(far.x, yy, u)
                else:
                    self.carve(u, yy, near.z1)
                    self.carve(u, yy, far.z)
            made += 1
        return made

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
        # 室内放在线脚之前：线脚只碰外露的墙，室内隔墙全在屋里，
        # 两边互不干扰；但顺序反过来（先 refine）会让楼梯井开洞时
        # 把刚补好的腰线一起挖掉。
        n_wall = n_stair = 0
        if getattr(self.spec, "interior", False):
            n_wall, n_stair = self.interior()
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
            "interior_walls": n_wall,
            "interior_stairs": n_stair,
        }
        return self.vox, self.blocks, info


def build(spec: Spec) -> tuple[np.ndarray, list[Block], dict]:
    """spec -> (vox (X,Y,Z), 方块表, 统计)。"""
    return _Builder(spec).run()
