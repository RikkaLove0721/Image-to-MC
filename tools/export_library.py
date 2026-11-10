"""把 1.12.2 方块表导出成界面用的 JSON。

界面不该自己去维护一份方块清单 —— 那样 palette.py 一改，仓库页就过期。
这里直接读 Python 的表，生成 web/server/data/library.json。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mc12 import palette  # noqa: E402

CATEGORY = {
    "concrete": "混凝土",
    "wool": "羊毛",
    "stained_hardened_clay": "陶瓦",
    "hardened_clay": "陶瓦",
    "planks": "木材",
    "glass": "玻璃与光",
    "stained_glass": "玻璃与光",
    "stained_glass_pane": "玻璃与光",
    "water": "玻璃与光",
    "sea_lantern": "玻璃与光",
    "glowstone": "玻璃与光",
    # 装饰件：树叶、栅栏、书架、原木这些只由规则注入的方块
    "leaves": "装饰",
    "fence": "装饰",
    "spruce_fence": "装饰",
    "birch_fence": "装饰",
    "jungle_fence": "装饰",
    "acacia_fence": "装饰",
    "dark_oak_fence": "装饰",
    "nether_brick_fence": "装饰",
    "iron_bars": "装饰",
    "log": "装饰",
    "log2": "装饰",
    "bookshelf": "装饰",
    "crafting_table": "装饰",
    "jukebox": "装饰",
    "noteblock": "装饰",
    "furnace": "装饰",
    "enchanting_table": "装饰",
    "vine": "装饰",
    "hay_block": "装饰",
    "pumpkin": "装饰",
    "lit_pumpkin": "装饰",
    "melon_block": "装饰",
    "sponge": "装饰",
    "tnt": "装饰",
    "slime": "装饰",
    "redstone_lamp": "玻璃与光",
    "end_rod": "装饰",
    "emerald_block": "装饰",
    "diamond_block": "装饰",
    "cobblestone_wall": "装饰",
    "carpet": "装饰",
    # 绿化带：草方块顶面是草、侧面是土，正好当花坛
    "grass": "绿化",
    "tallgrass": "绿化",
    "yellow_flower": "绿化",
    "red_flower": "绿化",
}
ORDER = ["石材", "混凝土", "羊毛", "陶瓦", "木材", "玻璃与光", "绿化", "装饰", "其他"]


def category_of(name_en: str) -> str:
    if name_en in CATEGORY:
        return CATEGORY[name_en]
    for key in ("stone", "brick", "sand", "quartz", "iron", "gold", "clay",
                "gravel", "obsidian", "nether", "prismarine", "bone",
                "purpur", "end", "dirt", "cobble"):
        if key in name_en:
            return "石材"
    return "其他"


def main() -> int:
    rows = []
    for b in palette.all_blocks(include_semantic=True):
        r, g, bl = b.rgb
        rows.append(
            {
                "id": b.id,
                "meta": b.meta,
                "oldId": f"{b.id}:{b.meta}",
                "nameEn": b.name_en,
                "nameZh": b.name_zh,
                "hex": b.hex_color,
                "rgb": [r, g, bl],
                "rough": b.rough,
                "semantic": b.semantic,
                "category": category_of(b.name_en),
            }
        )
    out = {
        "version": "1.12.2",
        "categories": ORDER,
        "total": len(rows),
        "blocks": rows,
    }
    target = Path(__file__).resolve().parents[1] / "web" / "server" / "data" / "library.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{target}  {len(rows)} 种方块")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
