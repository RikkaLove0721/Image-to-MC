"""把已有模型的 _view.html 换成最新查看器模板，模型本体一格不改。

为什么需要它：`_view.html` 是**构建时**生成的，模板烘在里面。查看器加了新功能
（比如方块查询），老模型的页面还是旧的 —— 用户打开 s02 找不到按钮，就是这个原因。

做法不是重建模型（那会把玻璃换成玻璃板、方块数也变），而是把老页面里内联的
体素 JSON 原样抠出来，重新套进新模板。模型数据一个字节都没动。

用法：
  python tools/upgrade_viewer.py            # 仓库 projects/
  python tools/upgrade_viewer.py --root dist/MC工程图/projects
  python tools/upgrade_viewer.py --check    # 只统计，不写
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mc12.viewer import TEMPLATE_VERSION, render_html, upgrade_view_file  # noqa: E402

# 老页面里的体素数据：<script id="model" type="application/json">…</script>
_MODEL_RE = re.compile(
    r'<script id="model" type="application/json">(.*?)</script>', re.S
)


def upgrade(path: Path, check: bool) -> str:
    """真正的升级逻辑在 mc12.viewer.upgrade_view_file，这里只是批量入口。"""
    if check:
        html = path.read_text(encoding="utf-8")
        if f'name="viewer-template" content="{TEMPLATE_VERSION}"' in html:
            return "ok:已是最新"
        return "updated" if _MODEL_RE.search(html) else "skip:没有内联模型数据"
    r = upgrade_view_file(path)
    return "ok:已是最新" if r == "ok" else r


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(ROOT / "projects"))
    ap.add_argument("--check", action="store_true", help="只统计，不写文件")
    args = ap.parse_args()

    base = Path(args.root)
    if not base.is_dir():
        raise SystemExit(f"目录不存在：{base}")

    stat: dict[str, int] = {}
    for f in sorted(base.glob("*/output/*_view.html")):
        r = upgrade(f, args.check)
        key = r.split(":")[0]
        stat[key] = stat.get(key, 0) + 1
        if key in ("updated", "renamed"):
            print(f"  {r:9s} {f.relative_to(base)}")
    print("汇总:", ", ".join(f"{k}={v}" for k, v in sorted(stat.items())) or "没有找到 _view.html")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
