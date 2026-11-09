"""Minecraft Java 1.12.2 方块调色板。

1.12 属于"扁平化之前"的版本，方块由 **数字 ID + metadata** 唯一确定，
例如 羊毛 = id 35，红色羊毛 = 35:14。1.13 之后改成的字符串 ID 体系在这里无效。

本表只收录 1.12.2 实际存在、且六面纹理基本一致（可安全用作体素）的方块。
刻意排除：
  - 1.13+ 新增方块（深板岩、方解石、凝灰岩、玄武岩、黑石、铜块、樱花木……）
  - 逐面纹理差异大的方块（草方块、原木、干草块——方块朝向无法在体素里逐面指定）
  - 透明方块（玻璃、冰）——会在模型上开洞

颜色为 16x16 纹理的平均色（近似值）。换材质包或换版本后应重新采样。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .color import rgb_to_lab


@dataclass(frozen=True)
class Block:
    id: int
    meta: int
    name_en: str
    name_zh: str
    hex_color: str
    rough: bool = False   # 纹理是否"花"（石砖、圆石一类）
    semantic: bool = False  # 只允许语义规则使用（玻璃等透明方块），不参与颜色搜索

    @property
    def key(self) -> str:
        return f"minecraft:{self.name_en}"

    @property
    def old_id(self) -> str:
        return f"{self.id}:{self.meta}"

    @property
    def rgb(self) -> tuple[int, int, int]:
        h = self.hex_color.lstrip("#")
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)

    @property
    def label(self) -> str:
        return f"{self.name_zh} ({self.old_id})"


def _hex(h: str) -> str:
    return h if h.startswith("#") else "#" + h


def _rows():
    """(id, meta, 英文名, 中文名, 颜色, 是否粗糙)"""
    r = []

    # ---- 石质 ----
    r += [
        (1, 0, "stone", "石头", "#7E7E7E", False),
        (1, 1, "granite", "花岗岩", "#956756", False),
        (1, 2, "polished_granite", "磨制花岗岩", "#9A6B5B", False),
        (1, 3, "diorite", "闪长岩", "#C5C5C4", False),
        (1, 4, "polished_diorite", "磨制闪长岩", "#CDCDCD", False),
        (1, 5, "andesite", "安山岩", "#888B87", False),
        (4, 0, "cobblestone", "圆石", "#7A7A7A", True),
        (98, 0, "stonebrick", "石砖", "#7D7D7C", True),
        (98, 3, "stonebrick", "錾制石砖", "#777777", True),
        (45, 0, "brick_block", "砖块", "#96604E", True),
        (24, 0, "sandstone", "砂岩", "#D9CFA0", False),
        (24, 2, "sandstone", "平滑砂岩", "#D6CBA3", False),
        (12, 0, "sand", "沙子", "#DBCFA3", True),
        (12, 1, "sand", "红沙", "#B75A29", True),
        (155, 0, "quartz_block", "石英块", "#ECE9E2", False),
        (42, 0, "iron_block", "铁块", "#D9D9D9", False),
        (41, 0, "gold_block", "金块", "#F8DC5C", False),
        (82, 0, "clay", "黏土块", "#A0A6B3", False),
        (13, 0, "gravel", "砂砾", "#837E7E", True),
        (49, 0, "obsidian", "黑曜石", "#14121E", False),
        (87, 0, "netherrack", "下界岩", "#6B2020", True),
        (112, 0, "nether_brick", "下界砖块", "#2D1519", True),
        (215, 0, "red_nether_brick", "红色下界砖块", "#4A1A1C", True),
        (168, 0, "prismarine", "海晶石", "#63A89C", True),
        (168, 2, "prismarine", "暗海晶石", "#33534B", True),
        (216, 0, "bone_block", "骨块", "#E4E0CB", False),
        (201, 0, "purpur_block", "紫珀块", "#A97DA9", False),
        (121, 0, "end_brick", "末地石", "#DBDEA0", False),
        (3, 0, "dirt", "泥土", "#866043", False),
        (3, 2, "dirt", "灰化土", "#6B4A28", False),
        (172, 0, "hardened_clay", "陶瓦", "#985E43", False),
    ]

    # ---- 语义方块：不打散色搜索，只由"窗户/水/灯"等规则显式指定 ----
    # 室内场景（泳池大厅）还需要水、深色玻璃与发光方块，这些同样不能进入
    # 颜色搜索：水是流体、灯是光源，让 k-means 去命中它们只会得到一堆噪点。
    r += [
        (20, 0, "glass", "玻璃", "#C7DCE8", False, True),
        (95, 3, "stained_glass", "淡蓝色玻璃", "#86A8C4", False, True),
        (95, 9, "stained_glass", "青色玻璃", "#4D8A99", False, True),
        (95, 11, "stained_glass", "蓝色玻璃", "#2E4A8E", False, True),
        (95, 15, "stained_glass", "黑色玻璃", "#26282B", False, True),
        (20, 1, "thin_glass", "玻璃板", "#C7DCE8", False, True),
        (9, 0, "water", "水", "#3F76E4", False, True),
        (169, 0, "sea_lantern", "海晶灯", "#A8CEC7", False, True),
        (89, 0, "glowstone", "萤石", "#FFDE8C", False, True),
    ]

    # ---- 木板 ----
    r += [
        (5, 0, "planks", "橡木木板", "#B8945F", False),
        (5, 1, "planks", "云杉木板", "#735129", False),
        (5, 2, "planks", "桦木木板", "#D7C185", False),
        (5, 3, "planks", "丛林木木板", "#A07350", False),
        (5, 4, "planks", "金合欢木板", "#B05D2E", False),
        (5, 5, "planks", "深色橡木木板", "#3E2912", False),
    ]

    # ---- 混凝土 251:0-15（最平整、最适合建筑外立面）----
    concrete = [
        ("white", "白色", "#CFD5D6"),
        ("orange", "橙色", "#E06101"),
        ("magenta", "品红色", "#A9309F"),
        ("light_blue", "淡蓝色", "#2389C6"),
        ("yellow", "黄色", "#F0AF15"),
        ("lime", "黄绿色", "#5EA918"),
        ("pink", "粉红色", "#D5658E"),
        ("gray", "灰色", "#36393D"),
        ("silver", "淡灰色", "#7D7D73"),
        ("cyan", "青色", "#157788"),
        ("purple", "紫色", "#64209C"),
        ("blue", "蓝色", "#2C2E8E"),
        ("brown", "棕色", "#603C20"),
        ("green", "绿色", "#495B24"),
        ("red", "红色", "#8E2121"),
        ("black", "黑色", "#080A0F"),
    ]
    for meta, (en, zh, hx) in enumerate(concrete):
        r.append((251, meta, "concrete", f"{zh}混凝土", hx, False))

    # ---- 羊毛 35:0-15（颜色更亮，适合屋顶与强调色）----
    wool = [
        ("white", "白色", "#E9ECEC"),
        ("orange", "橙色", "#F07613"),
        ("magenta", "品红色", "#BD44B3"),
        ("light_blue", "淡蓝色", "#3AAFD9"),
        ("yellow", "黄色", "#F8C627"),
        ("lime", "黄绿色", "#70B919"),
        ("pink", "粉红色", "#ED8DAC"),
        ("gray", "灰色", "#3E4447"),
        ("silver", "淡灰色", "#8E8E86"),
        ("cyan", "青色", "#158991"),
        ("purple", "紫色", "#792AAC"),
        ("blue", "蓝色", "#35399D"),
        ("brown", "棕色", "#724728"),
        ("green", "绿色", "#546D1B"),
        ("red", "红色", "#A12722"),
        ("black", "黑色", "#141519"),
    ]
    for meta, (en, zh, hx) in enumerate(wool):
        r.append((35, meta, "wool", f"{zh}羊毛", hx, False))

    # ---- 染色陶瓦 159:0-15（低饱和度，建筑外墙很好用）----
    clay = [
        ("white", "白色", "#D1B1A1"),
        ("orange", "橙色", "#A15325"),
        ("magenta", "品红色", "#95576C"),
        ("light_blue", "淡蓝色", "#706C8A"),
        ("yellow", "黄色", "#BA8523"),
        ("lime", "黄绿色", "#677534"),
        ("pink", "粉红色", "#A24D61"),
        ("gray", "灰色", "#392A23"),
        ("silver", "淡灰色", "#876B62"),
        ("cyan", "青色", "#575B5E"),
        ("purple", "紫色", "#764656"),
        ("blue", "蓝色", "#4A3B5B"),
        ("brown", "棕色", "#4D3323"),
        ("green", "绿色", "#4C532A"),
        ("red", "红色", "#8F3D2E"),
        ("black", "黑色", "#251610"),
    ]
    for meta, (en, zh, hx) in enumerate(clay):
        r.append((159, meta, "stained_hardened_clay", f"{zh}陶瓦", hx, False))

    return r


BLOCKS: list[Block] = [Block(*row) for row in _rows()]
_BY_ID = {(b.id, b.meta): b for b in BLOCKS}


def all_blocks(include_semantic: bool = False) -> list[Block]:
    """可参与颜色匹配的方块。玻璃默认排除，由语义层显式指定。"""
    if include_semantic:
        return BLOCKS
    return [b for b in BLOCKS if not b.semantic]


def find(name_en: str, meta: int = 0) -> Block:
    return _BY_ID[(int(next(b.id for b in BLOCKS if b.name_en == name_en)), meta)]


def get(block_id: int, meta: int = 0) -> Block:
    return _BY_ID[(block_id, meta)]


def lab_matrix(blocks: list[Block] | None = None) -> np.ndarray:
    """返回 (B, 3) 的 Lab 矩阵。默认与 all_blocks() 严格同序，别混用。"""
    blocks = blocks if blocks is not None else all_blocks()
    rgb = np.array([b.rgb for b in blocks], dtype=np.float64) / 255.0
    return rgb_to_lab(rgb)


def rough_flags(blocks: list[Block] | None = None) -> np.ndarray:
    blocks = blocks if blocks is not None else all_blocks()
    return np.array([b.rough for b in blocks], dtype=bool)


if __name__ == "__main__":
    print(f"1.12.2 调色板：{len(BLOCKS)} 种方块")
    labs = lab_matrix()
    print("Lab 范围 L:", labs[:, 0].min().round(1), "-", labs[:, 0].max().round(1))
    for b in BLOCKS[:4] + BLOCKS[-3:]:
        print(f"  {b.label:24s} {b.hex_color}  {b.key}")

def dedupe(threshold: float = 5.0, blocks: list[Block] | None = None) -> list[Block]:
    """把颜色几乎相同的方块合并，只留一个代表。

    Minecraft 里"安山岩 #888B87"和"淡灰色羊毛 #8E8E86"几乎同色，纹理也只是
    一个粗一点一个细一点。如果两个都留在候选库里，一面灰墙就会被拆成两种
    方块 —— 看着就"花"。所以先按 CIEDE2000 把 ≤ threshold 的合并掉。

    保留规则：同一色簇里优先留**纹理平整**（rough=False）的那个，纯色墙面
    用羊毛/混凝土比用圆石更贴近"建筑"观感。
    """
    from .color import delta_e_00

    src = blocks if blocks is not None else all_blocks()
    labs = lab_matrix(src)

    # 同色时保留谁，直接决定观感。优先留"建筑常用、纹理平整"的材料：
    # 混凝土/羊毛是一整块纯色，最像 Minecraft 建筑；陶瓦次之；
    # 花岗岩、安山岩这类带颗粒的石头，用在墙面上一眼就"脏"。
    prio = {
        "concrete": 0,
        "wool": 1,
        "stained_hardened_clay": 2,
        "hardened_clay": 2,
        "quartz_block": 3,
        "sandstone": 4,
        "planks": 5,
        "stone": 6,
        "cobblestone": 7,
    }

    def rank(i: int) -> tuple:
        b = src[i]
        return (b.rough, prio.get(b.name_en, 9), len(b.name_zh))

    order = sorted(range(len(src)), key=rank)
    keep: list[int] = []
    for i in order:
        if not keep:
            keep.append(i)
            continue
        d = delta_e_00(labs[keep], labs[i])
        if float(np.min(d)) >= threshold:
            keep.append(i)
    keep.sort()
    return [src[i] for i in keep]
