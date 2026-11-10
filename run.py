"""照片/蓝图 -> Minecraft 1.12 方块模型。

从 0.17.0 起**只保留蓝图路线**：
    照片 -> analyze（brief） -> AI 写 spec -> 本文件按 spec 展开成方块

    python run.py --spec 蓝图.json -o output --name 我的楼
    python run.py --brief -i 照片.jpg --width 40     # 只分析，给 AI 用
    python run.py --rules                            # 打印建筑规则
    python run.py --mcp                              # 以 MCP 服务运行

照片直转体素的旧路线（synth / building / hall / relief / flat / terrain）已在
0.17.0 删除：它拿不到"这是主楼、那是侧翼"，别墅会变成一个颜色被绿植带跑的方盒子。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from mc12 import (
    iso,
    knowledge,
    schematic,
    spec as spec_mod,
    viewer,
    voxel,
)


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="按蓝图把建筑展开成 Minecraft 1.12 方块模型（.schematic）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--spec",
        help="蓝图 spec（JSON）。构建入口，AI / MCP / 手写方案都走这条",
    )
    p.add_argument("-i", "--input", help="照片路径，配合 --brief 使用")
    p.add_argument(
        "--brief",
        action="store_true",
        help="只做照片分析，打印建筑 brief（JSON）后退出，供 AI / MCP 使用",
    )
    p.add_argument(
        "--rules",
        nargs="?",
        const="building",
        default=None,
        help="打印 MC 设计常识（给 AI 提示词用），然后退出。"
        "可选 building（默认）/ ship —— 舰船是另一套语法",
    )
    p.add_argument(
        "--upgrade-view",
        metavar="HTML",
        help="把某个 _view.html 就地换成最新查看器模板（复用页面里内联的体素数据，模型不动）",
    )
    p.add_argument(
        "--craft",
        type=int,
        default=1,
        help="游戏建筑技巧性 0/1/2（低/中/高）。越高越优先套用 MC 社区建筑法则，"
        "越允许偏离照片的逐像素还原",
    )
    p.add_argument(
        "--ornament",
        action="store_true",
        help="规则里附带「观赏性细节」（detail=4）那一节",
    )
    p.add_argument(
        "--critique",
        action="store_true",
        help="体检：按蓝图构建一遍，和照片 brief 对比，打印分数与问题（JSON）后退出",
    )
    p.add_argument(
        "--critique-iso",
        default=None,
        help="配合 --critique：把这次构建的等轴测图写到这个路径，交给视觉模型复核",
    )
    p.add_argument(
        "--compare",
        nargs=2,
        metavar=("PHOTO", "RENDER"),
        help="把原图和模型渲染拼成一张对比图（给视觉模型看），配合 --img-out",
    )
    p.add_argument("--img-out", default=None, help="--compare 的输出路径（JPEG）")
    p.add_argument(
        "--seed",
        type=int,
        default=None,
        help="复现用的种子，写进蓝图与清单；同一个种子 + 同一份蓝图 = 同一个模型",
    )
    p.add_argument(
        "--mcp",
        action="store_true",
        help="以 MCP 服务模式运行（stdio JSON-RPC），供 AI 客户端调用",
    )
    p.add_argument("-o", "--out", default="output", help="输出目录，默认 output")
    p.add_argument("--name", default=None, help="模型名，默认用蓝图里的 name")
    p.add_argument(
        "--width",
        type=int,
        default=None,
        help="--brief 时按多少格宽分析，默认 40",
    )
    return p.parse_args(argv)


def _pad(text: str, width: int) -> str:
    """按显示宽度补空格（中文算 2 列）。"""
    w = sum(2 if ord(c) > 127 else 1 for c in text)
    return text + " " * max(0, width - w)


def layer_map_txt(model: voxel.Model, blocks) -> str:
    """逐层平面图（每格一个字符），用于确认"分层"确实生效。"""
    sx, sy, sz = model.size
    chars = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
    freqs = [b for b, _ in model.counts()][: len(chars)]
    sym = {b: chars[i] for i, b in enumerate(freqs)}

    out = []
    for y in range(sy - 1, -1, -1):
        layer = model.vox[:, y, :]
        n = int((layer >= 0).sum())
        out.append(f"\n=== 第 {y} 层（世界 Y={y}）· {n} 块 ===")
        for z in range(sz):
            row = []
            for x in range(sx):
                v = layer[x, z]
                row.append("." if v < 0 else sym.get(blocks[v], "?"))
            out.append("".join(row))
    out.append("\n\n图例： . = 空")
    for b, ch in sym.items():
        out.append(f"  {ch} = {b.label}")
    return "\n".join(out)


def _force_utf8() -> None:
    """冻结成 exe 后 Windows 会按活动代码页输出, 网页那头是按 UTF-8 解的, 这里统一掉。"""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass


def run_spec(path: str, out_dir: Path, name: str | None, seed: int | None = None) -> int:
    """按蓝图 spec 构建模型（不走照片）。"""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if seed is not None:
        raw["seed"] = int(seed)
    check = knowledge.check_spec(raw)
    print(f"[1/5] 读取蓝图 {path}")
    for e in check["errors"]:
        print(f"      ✗ {e}")
    for w in check["warnings"]:
        print(f"      ! {w}")
    if not check["ok"]:
        print("蓝图有硬错误，先改再构建")
        return 2

    spec = spec_mod.loads(raw)
    vox, blocks, sinfo = spec_mod.build(spec)
    vox, trim_off = voxel.trim(vox)
    out_dir.mkdir(parents=True, exist_ok=True)
    nm = name or spec.name or "spec"
    print(
        f"[2/5] 构建：{sinfo['volumes']} 个体块 / {sinfo['window_cells']} 格窗 / "
        f"屋顶 {sinfo['roof_blocks']} 块 / 细节 {sinfo['detail_blocks']} 块"
        + (
            f" / 观赏性细节 {sinfo['ornament_patch'] + sinfo['ornament_prop']} 块"
            if sinfo.get("ornament_patch") or sinfo.get("ornament_prop")
            else ""
        )
    )

    model = voxel.Model(vox=vox, blocks=blocks, meta={"source": path, "mode": "spec"})
    model.meta["trim_offset"] = list(trim_off)
    counts = model.counts()
    total = model.total
    print(f"[3/5] 尺寸 {model.size[0]}x{model.size[1]}x{model.size[2]}，总方块 {total:,}")
    sch = schematic.write_schematic(out_dir / f"{nm}.schematic", vox, blocks)
    ok = schematic.verify(out_dir / f"{nm}.schematic", vox, blocks)
    print(f"      {sch['path']}  {sch['bytes'] / 1024:.1f} KB  校验: {'通过' if ok else '失败'}")
    ir = iso.render(out_dir / f"{nm}_iso.png", vox, blocks, tile=8)
    print(f"      {ir['path']}  {ir['blocks']:,} 块等轴测预览")
    vw = viewer.write_viewer(out_dir / f"{nm}_view.html", vox, blocks, nm)
    print(f"      {vw['path']}  {vw['bytes'] / 1024:.1f} KB")

    # ---- 文本清单：和照片路线保持同一套文件，前端仓库才能给出同样的下载 ----
    raw["seed"] = spec.seed
    (out_dir / f"{nm}_spec.json").write_text(
        json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / f"{nm}_layers.txt").write_text(layer_map_txt(model, blocks), encoding="utf-8")
    lines = [
        f"模型：{nm}",
        f"尺寸：{model.size[0]} × {model.size[1]} × {model.size[2]} (X × Y × Z)",
        f"来源：{path}  模式：蓝图（AI 方案）",
        f"体块：{sinfo['volumes']} 个 / 窗：{sinfo['window_cells']} 格 / 屋顶：{sinfo['roof_blocks']} 块",
        f"总方块数：{total:,}",
        f"方块种类：{len(counts)}",
        "",
        "方块数量清单：",
    ]
    for i, (b, n) in enumerate(counts, 1):
        lines.append(f"  {i:2d}. {_pad(b.name_zh, 14)} id:{_pad(b.old_id, 8)} {b.hex_color} {n:>7,d} 块")
    lines.append("")
    lines.append("各层方块数（下标 0 = 最底层）：")
    lc = model.layer_counts()
    lines.append("  " + ", ".join(f"y{y}:{n}" for y, n in enumerate(lc) if n))
    report = "\n".join(lines)
    (out_dir / f"{nm}_blocks.txt").write_text(report, encoding="utf-8")

    print("[4/5] 写入清单 ...")
    manifest = {
        "name": nm,
        "source": str(path),
        "mode": "spec",
        "version": "1.12.2",
        "size": list(model.size),
        "origin": list(model.origin),
        "total": total,
        "solid_total": total,
        "kinds": len(counts),
        "blocks": [
            {
                "id": b.id,
                "meta": b.meta,
                "name_en": b.name_en,
                "name_zh": b.name_zh,
                "hex": b.hex_color,
                "count": n,
            }
            for b, n in counts
        ],
        "layer_counts": {str(y): n for y, n in enumerate(lc) if n},
        "params": {
            "volumes": sinfo["volumes"],
            "window_cells": sinfo["window_cells"],
            "roof_blocks": sinfo["roof_blocks"],
            "detail_blocks": sinfo["detail_blocks"],
            "ornament_patch": sinfo.get("ornament_patch", 0),
            "ornament_prop": sinfo.get("ornament_prop", 0),
            "detail": spec.detail,
            "ornament": bool(getattr(spec, "ornament", False)),
            "roof": (raw.get("roof") or {}).get("type", "flat"),
            "palette": raw.get("palette") or {},
            "seed": spec.seed,
        },
        # 蓝图原样存进清单：仓库页要能回看"这次 AI 到底给了什么方案"
        "spec": raw,
    }
    (out_dir / f"{nm}_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print("[5/5] 清单：")
    print(voxel.describe(model))
    return 0 if ok else 1


def run_critique(spec_path: str, photo: str, width: int = 40, iso_out: str | None = None) -> int:
    """体检：构建一遍，和照片的 brief 对比，打印分数与问题。

    这是"AI 跑蓝图"的反馈端：AI 拿到这几行数字就知道下一轮该改哪里，
    不必重写整份蓝图，也不必让人肉看图。

    给了 iso_out 就顺手渲染一张等轴测图 —— 视觉模型要靠它做"看一眼"的复核。
    """
    from mc12 import analyze as _analyze, critique as _critique

    raw = json.loads(Path(spec_path).read_text(encoding="utf-8"))
    spec = spec_mod.loads(raw)
    vox, blocks, _info = spec_mod.build(spec)
    vox, _off = voxel.trim(vox)
    brief = _analyze.analyze(photo, width)
    result = _critique.critique(brief, spec, vox, blocks)
    result["check"] = knowledge.check_spec(raw)
    if iso_out:
        r = iso.render(Path(iso_out), vox, blocks, tile=8)
        result["iso"] = r["path"]
    print(json.dumps(result, ensure_ascii=False))
    return 0


def run_compare(photo: str, render: str, out: str) -> int:
    from mc12.compare import make_compare

    info = make_compare(photo, render, out)
    print(json.dumps(info, ensure_ascii=False))
    return 0


def main(argv=None) -> int:
    _force_utf8()
    args = parse_args(argv)

    if args.mcp:
        import mcp_server

        mcp_server.serve()
        return 0
    if args.upgrade_view:
        print(viewer.upgrade_view_file(Path(args.upgrade_view)))
        return 0

    if args.rules:
        print(
            knowledge.rules_text(
                args.rules, ornament=bool(args.ornament), craft=int(args.craft or 0)
            )
        )
        return 0
    if args.critique:
        if not args.spec or not args.input:
            print("--critique 需要 --spec 蓝图 和 -i 照片", file=sys.stderr)
            return 2
        return run_critique(args.spec, args.input, args.width or 40, args.critique_iso)
    if args.compare:
        if not args.img_out:
            print("--compare 需要 --img-out 输出路径", file=sys.stderr)
            return 2
        return run_compare(args.compare[0], args.compare[1], args.img_out)
    if args.brief:
        if not args.input:
            print("--brief 需要 -i 照片", file=sys.stderr)
            return 2
        from mc12 import analyze as _analyze

        print(json.dumps(_analyze.analyze(args.input, args.width or 40), ensure_ascii=False))
        return 0
    if args.spec:
        return run_spec(args.spec, Path(args.out or "output"), args.name, args.seed)

    print("用法：--spec 蓝图 / --brief -i 照片 / --rules / --mcp", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
