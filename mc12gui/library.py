"""方块仓库的检索, 分类与使用统计.

仓库就是 `mc12.palette` 里的 1.12.2 方块表. 本模块不改表, 只负责把表
整理成界面能直接用的视图: 分类, 关键字过滤, 以及在已有产物里的用量.
"""

from __future__ import annotations

import json
from pathlib import Path

from mc12.palette import BLOCKS, Block

CATEGORIES = ["全部", "石质", "混凝土", "羊毛", "陶瓦", "木质", "语义"]

CATEGORY_ORDER = {name: i for i, name in enumerate(CATEGORIES)}

ROLE_TEXT = {
    "语义": "只由规则注入, 不参与颜色搜索",
    "石质": "建筑基座与外墙面",
    "混凝土": "最平整, 适合大面积外墙",
    "羊毛": "颜色最亮, 适合屋顶与强调",
    "陶瓦": "低饱和, 适合仿古外墙",
    "木质": "梁柱与内饰",
}


def identity(block: Block) -> tuple[int, int]:
    return block.id, block.meta


def category(block: Block) -> str:
    if block.semantic:
        return "语义"
    if block.name_en == "concrete":
        return "混凝土"
    if block.name_en == "wool":
        return "羊毛"
    if block.name_en in ("stained_hardened_clay", "hardened_clay"):
        return "陶瓦"
    if block.name_en == "planks":
        return "木质"
    return "石质"


def texture_text(block: Block) -> str:
    return "粗糙" if block.rough else "平整"


def select(text: str = "", cat: str = "全部", only_paintable: bool = False) -> list[Block]:
    """按关键字与分类过滤, 关键字同时匹配中文名, 英文名与 id."""
    key = text.strip().lower()
    out: list[Block] = []
    for b in BLOCKS:
        if only_paintable and b.semantic:
            continue
        if cat != "全部" and category(b) != cat:
            continue
        if key:
            hay = f"{b.name_zh} {b.name_en} {b.old_id}".lower()
            if key not in hay:
                continue
        out.append(b)
    return out


def usage(out_dir: Path) -> dict[tuple[int, int], tuple[int, int]]:
    """统计每个方块在 output 目录里的总用量与出现模型数.

    返回 (id, meta) -> (总块数, 模型数).
    """
    stats: dict[tuple[int, int], tuple[int, int]] = {}
    if not out_dir.is_dir():
        return stats
    for man in out_dir.glob("*_manifest.json"):
        try:
            data = json.loads(man.read_text(encoding="utf-8"))
        except Exception:
            continue
        for row in data.get("blocks") or []:
            try:
                key = int(row["id"]), int(row.get("meta", 0))
                count = int(row.get("count", 0))
            except (KeyError, TypeError, ValueError):
                continue
            total, models = stats.get(key, (0, 0))
            stats[key] = (total + count, models + 1)
    return stats


def summary(blocks: list[Block]) -> dict[str, int]:
    counts = {name: 0 for name in CATEGORIES}
    counts["全部"] = len(blocks)
    for b in blocks:
        counts[category(b)] = counts.get(category(b), 0) + 1
    return counts
