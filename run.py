"""照片 -> Minecraft 1.12 方块模型。

用法：
    python run.py -i 照片.jpg                  # 默认：建筑立面，预设 m（约 40 格宽）
    python run.py -i 照片.jpg --preset s        # 32 格宽，正好"大型房子"的尺度
    python run.py -i 照片.jpg --preset l        # 56 格宽，窗格更规整
    python run.py -i 山景.jpg --mode terrain   # 地貌
    python run.py -i 照片.jpg --sat 1.0         # 颜色完全忠实原图（默认 1.5 会提饱和）

尺寸预设（宽 × 方块种类上限）：
    xs  24 格  5 种   小房子 / 岗亭
    s   32 格  6 种   单体楼（推荐住宅尺度）
    m   40 格  8 种   大型建筑 / 校舍（默认）
    l   56 格 10 种   地标级，窗格更整齐
    xl  80 格 12 种   高精度，方块数很多
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

from mc12 import (
    color,
    geometry,
    hall,
    iso,
    multiview,
    palette,
    quantize,
    rectify,
    schematic,
    semantic,
    synth,
    tidy,
    viewer,
    voxel,
)

# 尺寸预设：Minecraft 里一栋"大型建筑"大约 30x30x20，
# 再大就只是数字变好看，游戏里根本放不下也用不上。
PRESETS = {
    "xs": (24, 5, "小房子 / 岗亭，约 24 格宽"),
    "s": (32, 6, "单体楼，约 32 格宽（推荐住宅尺度）"),
    "m": (40, 8, "大型建筑 / 校舍，约 40 格宽"),
    "l": (56, 9, "地标级，约 56 格宽"),
    "xl": (80, 10, "高精度，方块数很多"),
}


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="把一张照片转成 Minecraft 1.12 方块模型（.schematic）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("-i", "--input", required=True, help="输入照片路径（正视图）")
    p.add_argument(
        "--view",
        action="append",
        metavar="角色=路径",
        help="补充视角, 可重复: side=侧面.jpg / top=俯视.jpg / free=自由.jpg",
    )
    p.add_argument("-o", "--out", default="output", help="输出目录，默认 output")
    p.add_argument("--name", default=None, help="模型名，默认取文件名")
    p.add_argument(
        "--preset",
        default="m",
        choices=list(PRESETS),
        help="尺寸预设：" + " / ".join(f"{k}={v[2]}" for k, v in PRESETS.items()),
    )
    p.add_argument("--width", type=int, default=None, help="直接指定宽度（覆盖预设）")
    p.add_argument(
        "--mode",
        default="building",
        choices=["building", "synth", "hall", "flat", "relief", "terrain"],
        help="building=按照片重建（默认）/ synth=提取特征后重新合成对称立面 / "
             "hall=室内大厅（泳池/展厅，按空间文法重建）/ flat=平板 / relief=浮雕 / terrain=地形",
    )
    p.add_argument("--k", type=int, default=None, help="方块种类上限，默认由预设决定")
    p.add_argument("--strength", type=float, default=4.0, help="relief 模式的起伏强度，默认 4")
    p.add_argument("--smooth", type=int, default=1, help="去孤立点滤波遍数，0 关闭，默认 1")
    p.add_argument(
        "--depth",
        type=int,
        default=0,
        help="building 模式=墙体厚度（默认 3）；synth 模式=建筑进深；hall 模式=池深（默认 3）",
    )
    p.add_argument("--length", type=int, default=0, help="hall 模式：厅深（0=按宽度自动）")
    p.add_argument("--height", type=int, default=0, help="hall 模式：净高（0=按宽度自动）")
    p.add_argument("--bays", type=int, default=4, help="hall 模式：每侧柱子数量，默认 4")
    p.add_argument("--cell", type=int, default=5, help="hall 模式：藻井格边长，默认 5")
    p.add_argument("--sat", type=float, default=1.5, help="色彩饱和度增益，默认 1.5；设 1.0 为完全忠实原图")
    p.add_argument("--no-glass", action="store_true", help="关闭窗户自动替换玻璃")
    p.add_argument("--save-windows", action="store_true", help="额外输出窗洞检测调试图")
    p.add_argument("--no-sky", action="store_true", help="关闭天空自动剥离")
    p.add_argument("--no-rect", action="store_true", help="关闭透视矫正（默认开启）")
    p.add_argument("--no-flat", action="store_true", help="关闭光照归一化（默认开启）")
    p.add_argument("--median", type=int, default=9, help="降采样前的中值核大小，默认 9；0 关闭")
    p.add_argument("--flat-radius", type=float, default=0.10, help="光照归一化的尺度，默认 0.10")
    p.add_argument(
        "--clean",
        type=int,
        default=2,
        choices=[0, 1, 2],
        help="规整强度：0=不动 / 1=统一窗尺寸+去碎块 / 2=再加等间距重排（默认）",
    )
    p.add_argument("--floors", type=int, default=None, help="synth 模式手动指定层数")
    p.add_argument("--sym", action="store_true", help="强行左右镜像，做出对称模型")
    p.add_argument(
        "--dedupe",
        type=float,
        default=10.0,
        help="颜色几乎相同的方块合并阈值（CIEDE2000），默认 10；调大则颜色更少更统一",
    )
    p.add_argument("--no-hollow", action="store_true", help="保留实心，不做空心壳")
    p.add_argument("--no-viewer", action="store_true", help="不生成 HTML 查看器")
    p.add_argument("--no-iso", action="store_true", help="不生成等轴测预览图")
    return p.parse_args(argv)


def _pad(text: str, width: int) -> str:
    """按显示宽度补空格（中文算 2 列）。"""
    w = sum(2 if ord(c) > 127 else 1 for c in text)
    return text + " " * max(0, width - w)


def preview_png(path: Path, rgb: np.ndarray, grid: np.ndarray, blocks, scale: int = 6):
    """生成对比图：左=降采样后的照片，右=还原出来的方块配色。"""
    h, w = grid.shape
    left = (np.clip(rgb, 0, 1) * 255).astype(np.uint8)
    right = np.full((h, w, 3), 235, dtype=np.uint8)
    for i, b in enumerate(blocks):
        right[grid == i] = np.array(b.rgb, dtype=np.uint8)

    gap = 8
    canvas = np.full((h, w * 2 + gap, 3), 245, dtype=np.uint8)
    canvas[:, :w] = left
    canvas[:, w + gap :] = right

    img = Image.fromarray(canvas).resize(
        ((w * 2 + gap) * scale, h * scale), Image.NEAREST
    )
    img.save(path)


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


def run_hall(args, src: Path, name: str, out_dir: Path, width: int, desc: str, balance=None) -> int:
    """hall 模式：室内空间重建.

    与另外几条路线完全分开 —— 那些都在还原一个"面", 这条在造一个"房间".
    照片只提供配色与水池尺度, 其余按空间文法生成.
    """
    print(f"[1/5] 读取室内照（预设 {args.preset}：{desc}）...")
    raw = geometry.load_image(str(src))
    raw, border = geometry.trim_border(raw)
    raw = multiview.apply_balance(raw, balance)
    print(f"      原图 {raw.shape[1]}×{raw.shape[0]}（去边框 {border}）")

    print("[2/5] 提取空间特征（配色 / 水池尺度）...")
    feat = hall.extract_features(raw)
    span = feat.get("pool_span")
    dr_pct = round(feat["deck_ratio"] * 100)
    if span is None:
        scale_note = f"未测到水面，池岸按 {dr_pct}% 厅宽"
    elif (span[1] - span[0]) > 0.90:
        scale_note = f"相机架在池面上，水面顶满画面，池岸按 {dr_pct}% 厅宽"
    else:
        scale_note = (
            f"水池横向占比 {round((span[1] - span[0]) * 100)}%，"
            f"池岸 {dr_pct}% 厅宽"
        )
    print(f"      曝光补偿 x{feat['gain']:.2f}；{scale_note}")
    mats = hall.pick_materials(feat)
    print("      材质：" + " / ".join(f"{k}:{v.name_zh}" for k, v in mats.items()))

    print("[3/5] 按空间文法重建大厅 ...")
    try:
        vox, blocks, hinfo = hall.build(
            feat,
            mats,
            width,
            length=args.length or 0,
            height=args.height or 0,
            bays=args.bays,
            pool_depth=args.depth or hall.POOL_DEPTH,
            cell=args.cell,
        )
    except ValueError as exc:
        print(f"      {exc}", file=sys.stderr)
        return 2
    solid_total = int((vox >= 0).sum())
    vox, trim_off = voxel.trim(vox)
    y_top_local = hinfo["y_top"] - trim_off[1]
    print(
        f"      厅 {hinfo['W']}×{hinfo['H']}×{hinfo['L']}（宽×高×深），"
        f"水池 {hinfo['pool'][0]}×{hinfo['pool'][1]} 深 {hinfo['pool'][2]}，"
        f"{hinfo['bays']} 对联排柱，入口在 Z=0 端"
    )

    model = voxel.Model(vox=vox, blocks=blocks, meta={"source": str(src), "mode": "hall"})
    model.meta["trim_offset"] = list(trim_off)
    model.meta["hall"] = {k: hinfo[k] for k in ("W", "L", "H", "pool", "bays", "mural")}

    if not args.no_iso:
        print("[4/5] 渲染外观与剖切 ...")
        ir = iso.render(out_dir / f"{name}_iso.png", vox, blocks, tile=4)
        print(f"      {ir['path']}  外观 {ir['pixels'][0]}×{ir['pixels'][1]}")
        # 剖切：切掉屋顶与右半边，露出泳池、柱廊与端墙壁画
        cut = vox.copy()
        cut[:, y_top_local:, :] = -1
        cut[cut.shape[0] // 2 :, :, :] = -1
        cr = iso.render(out_dir / f"{name}_cut.png", cut, blocks, tile=6)
        print(f"      {cr['path']}  剖切 {cr['pixels'][0]}×{cr['pixels'][1]}")
    else:
        print("[4/5] 跳过渲染")

    print("[5/5] 导出 ...")
    sch = schematic.write_schematic(out_dir / f"{name}.schematic", vox, blocks)
    ok = schematic.verify(out_dir / f"{name}.schematic", vox, blocks)
    print(f"      {sch['path']}  {sch['bytes'] / 1024:.1f} KB  校验: {'通过' if ok else '失败'}")

    if not args.no_viewer:
        vw = viewer.write_viewer(out_dir / f"{name}_view.html", vox, blocks, name)
        print(f"      {vw['path']}  {vw['bytes'] / 1024:.1f} KB")

    (out_dir / f"{name}_layers.txt").write_text(
        layer_map_txt(model, blocks), encoding="utf-8"
    )

    counts = model.counts()
    total = model.total
    lines = [
        f"模型：{name}",
        f"尺寸：{model.size[0]} × {model.size[1]} × {model.size[2]} (X × Y × Z)",
        f"来源：{src}  模式：hall  预设：{args.preset}（{desc}）",
        f"厅宽 {hinfo['W']} · 厅深 {hinfo['L']} · 净高 {hinfo['H']} · 联排柱 {hinfo['bays']} 对",
        f"水池 {hinfo['pool'][0]} × {hinfo['pool'][1]}，深 {hinfo['pool'][2]} 格（水面比池岸低一格）",
        f"端墙壁画 {hinfo['mural'][0]} × {hinfo['mural'][1]}，藻井格边长 {args.cell}",
        f"总方块数：{total:,}",
        f"方块种类：{len(counts)}",
        "",
        "方块数量清单：",
    ]
    for i, (b, n) in enumerate(counts, 1):
        lines.append(
            f"  {i:2d}. {_pad(b.name_zh, 14)} id:{_pad(b.old_id, 8)} {b.hex_color} {n:>7,d} 块"
        )
    lines.append("")
    lines.append("各层方块数（下标 0 = 池底）：")
    lc = model.layer_counts()
    lines.append("  " + ", ".join(f"y{y}:{n}" for y, n in enumerate(lc) if n))
    report = "\n".join(lines)
    (out_dir / f"{name}_blocks.txt").write_text(report, encoding="utf-8")

    manifest = {
        "name": name,
        "source": str(src),
        "mode": "hall",
        "version": "1.12.2",
        "size": list(model.size),
        "origin": list(model.origin),
        "total": total,
        "solid_total": solid_total,
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
        "layer_counts": {str(y): n for y, n in enumerate(model.layer_counts()) if n},
        "hall": {
            "width": hinfo["W"],
            "length": hinfo["L"],
            "height": hinfo["H"],
            "bays": hinfo["bays"],
            "pool": hinfo["pool"],
            "mural": hinfo["mural"],
            "deck_ratio": round(float(feat["deck_ratio"]), 4),
            "pool_span": [round(v, 4) for v in span] if span else None,
            "gain": round(float(feat["gain"]), 3),
        },
        "params": {"preset": args.preset, "width": width, "cell": args.cell},
    }
    (out_dir / f"{name}_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print()
    print(report.split("\n\n")[0])
    print()
    print("方块数量清单：")
    print(voxel.describe(model))
    print()
    print(f"总方块数 {total:,}（房间为壳体，内部中空可进入）")
    print(f"输出目录：{out_dir.resolve()}")
    return 0


def parse_views(args, src: Path) -> dict[str, str]:
    """把 -i 与多个 --view 收成 {角色: 路径}。认不出的角色直接忽略。"""
    paths = {"front": str(src)}
    for spec in args.view or []:
        if "=" not in spec:
            continue
        role, path = spec.split("=", 1)
        role = role.strip().lower()
        path = path.strip().strip('"')
        if role in multiview.ROLES and Path(path).exists():
            paths[role] = path
        else:
            print(f"      忽略视角 {spec}（角色不认识或文件不存在）")
    return paths


def _force_utf8() -> None:
    """冻结成 exe 后 Windows 会按活动代码页输出, 网页那头是按 UTF-8 解的, 这里统一掉。"""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass


def main(argv=None) -> int:
    _force_utf8()
    args = parse_args(argv)
    src = Path(args.input)
    if not src.exists():
        print(f"找不到输入文件：{src}", file=sys.stderr)
        return 2

    name = args.name or src.stem
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    # ---- 多视角：正视图走原有流程, 其余视角只提供度量 ----
    view_paths = parse_views(args, src)
    views: dict = {}
    mv_wb = np.ones(3)
    if len(view_paths) > 1:
        views = multiview.load_views(view_paths)
        roles = " / ".join(multiview.ROLE_LABELS[r] for r in multiview.ROLES if r in views)
        print(f"[视角] 载入 {len(views)} 张: {roles}")
        if "free" in views:
            mv_wb = multiview.white_balance_ref(views["free"])
            spread = float(np.max(np.abs(mv_wb - 1.0)))
            if spread >= 0.03:
                print(
                    "      自由视角白平衡：R x{:.2f}  G x{:.2f}  B x{:.2f}".format(*mv_wb)
                )
            else:
                print("      自由视角白平衡：色偏很小，不做校正")
        if args.mode != "synth":
            print("      注: 多视角融合只用于 synth（立面合成）路线, 本条路线只用正视图")
    elif len(view_paths) == 1 and args.mode == "synth":
        print("[视角] 只有正视图, 进深与平面轮廓按单图估算")

    # 预设决定宽度与方块种类上限，--width 可显式覆盖
    width, k_lim, desc = PRESETS[args.preset]
    if args.width:
        width = args.width
    if args.k is None:
        args.k = k_lim
    args.width = width

    # 室内场景走独立路线：它不是"一个面"，而是一个房间，立面文法完全不适用
    if args.mode == "hall":
        return run_hall(args, src, name, out_dir, width, desc, mv_wb)

    # ---- 1. 读图 -> 去边框 -> 透视矫正 -> 光照归一化 ----
    print(f"[1/8] 读取照片（预设 {args.preset}：{desc}）...")
    raw = geometry.load_image(str(src))
    raw, border = geometry.trim_border(raw)
    print(f"      原图 {raw.shape[1]}×{raw.shape[0]}（去边框 {border}）")

    if not args.no_rect:
        raw, rinfo = rectify.rectify(raw)
        if rinfo["ok"]:
            print(
                f"      透视矫正：竖线 {rinfo['v_segs']} / 横线 {rinfo['h_segs']}，"
                f"消失点 {rinfo['vp_v']} {rinfo['vp_h']} -> {rinfo['size'][0]}×{rinfo['size'][1]}"
            )
        else:
            print(
                f"      透视矫正：跳过（{rinfo.get('note', '直线不足')}；"
                f"竖线 {rinfo['v_segs']} 横线 {rinfo['h_segs']}）"
            )
    else:
        rinfo = {"ok": False, "note": "手动关闭"}

    raw = multiview.apply_balance(raw, mv_wb)

    if not args.no_flat:
        raw, gain = tidy.flatten_illumination(raw, radius_frac=args.flat_radius)
        print(f"      光照归一化：补偿 x{gain.min():.2f}~x{gain.max():.2f}")

    if args.median and args.median >= 3:
        # 中值而不是均值：一格 20x20 像素里，砖墙是"多数派"，窗框、砖缝、
        # 空调支架是"少数派"。中值直接把少数派丢掉，墙面才是一个颜色。
        raw = geometry.median_filter(raw, args.median)
        print(f"      中值滤波 {args.median}px：抹掉窗框、砖缝这类细线")

    # 天空必须在**原图分辨率**上判：低分辨率下一格 = 十几像素，
    # 边缘 1 像素的黑框会把整格染深，那一列就永远剥不掉，最后立成一根柱子。
    sky_hi = geometry.sky_mask(raw) if not args.no_sky else None

    # ---- 2. 降采样到 1 像素 = 1 方块 ----
    width = min(width, raw.shape[1])
    rgb = geometry.downsample(raw, width)
    h, w = rgb.shape[:2]
    orig = (raw.shape[1], raw.shape[0])
    print(f"[2/8] 降采样到方块网格 {w}×{h}（约 {w * h:,} 格）")
    rgb_c = color.boost_saturation(rgb, args.sat) if args.sat != 1.0 else rgb

    # 天空（低分辨率）
    sky_lo = None
    if sky_hi is not None and sky_hi.any():
        sky_lo = (
            geometry.downsample(
                np.repeat(sky_hi[..., None].astype(np.float64), 3, axis=2), w
            )[..., 0]
            > 0.5
        )

    # ================================================================
    # v4 路线：synth 模式 —— 照片只提供特征，立面由文法重新合成
    # ================================================================
    if args.mode == "synth":
        print("[3/8] 提取立面特征（层数/窗距/墙色/腰线色）...")
        hi = geometry.downsample(raw, max(width * 2, 64))
        hi_sky = geometry.sky_mask(hi) if not args.no_sky else None
        win_hi, _winfo = semantic.window_grid(
            hi,
            (h, w),
            hi_sky,
            min_w=max(1, round(w * 0.035)),
            max_w=max(3, round(w * 0.16)),
            max_h=max(1, round(h * 0.09)),
        )
        feat = synth.extract_features(rgb_c, win_hi, sky_lo)
        if args.floors:
            feat["floors"] = args.floors
        print(
            f"      {feat['floors']} 层 · 层高 {feat['floor_h']} · 窗距 {feat['pitch']}"
            f" · 窗 {feat['bw']}×{feat['bh']} · 检出窗行特征 {feat['windows_raw']} 格"
        )

        print("[4/8] 按立面文法合成对称立面 ...")
        regions, win_s, sinfo = synth.synthesize(feat, width)
        H = sinfo["H"]
        print(
            f"      立面 {sinfo['W']}×{H}，{sinfo['floors']} 层，"
            f"{sinfo['cols']} 列窗（窗距 {sinfo['pitch']}），山花宽 {sinfo['pediment_w']}"
        )

        # ---- 多视角融合：进深、屋顶、侧墙与平面轮廓 ----
        fusion = None
        if len(views) > 1:
            fusion = multiview.fuse(views, sinfo["W"], H)
            print(
                f"      多视角融合：进深 {fusion['depth']} 格（{fusion['depth_source']}）"
                f"，屋顶 {fusion['roof_type']}，平面覆盖率 "
                f"{fusion['footprint_ratio'] * 100:.0f}%"
            )
            for note in fusion["notes"]:
                print(f"      {note}")

        # 区域色 -> 方块（与 quantize 相同的 dedupe 候选库）
        cand = palette.dedupe(args.dedupe)
        labs = palette.lab_matrix(cand)

        def lab2block(lab):
            d = color.delta_e_00(labs, lab)
            return cand[int(np.argmin(d))]

        wall_b = lab2block(feat["wall"])
        trim_b = lab2block(feat["trim"] if feat["trim"] is not None else feat["wall"])
        # 勒脚：颜色离墙太近（ΔE<12）就退回墙色，避免多出一种"似是而非"的方块
        plinth_b = wall_b
        if feat["plinth"] is not None:
            cand_plinth = lab2block(feat["plinth"])
            d_wall_plinth = color.delta_e_00(
                palette.lab_matrix([wall_b]), feat["plinth"]
            )[0]
            if cand_plinth.key != wall_b.key and d_wall_plinth >= 12:
                plinth_b = cand_plinth
        glass = palette.get(20, 0)
        # 屋顶用勒脚同一档石材，没有勒脚就与墙同色
        roof_b = plinth_b
        mats = {
            "wall": wall_b, "trim": trim_b, "plinth": plinth_b,
            "roof": roof_b, "glass": glass,
        }
        if fusion is not None:
            side_lab = fusion.get("side_wall")
            roof_lab = fusion.get("roof")
            if side_lab is not None:
                mats["side"] = lab2block(side_lab)
            if roof_lab is not None:
                mats["roof"] = lab2block(roof_lab)
            print(
                f"      视角材质：侧墙 {mats['side'].name_zh} / "
                f"屋顶 {mats['roof'].name_zh}"
            )
        print(
            f"      配色：墙 {wall_b.name_zh} / 腰线 {trim_b.name_zh} / "
            f"勒脚 {plinth_b.name_zh} / 玻璃"
        )

        print("[7/8] 挤出面宽、进深、屋顶，成三维体量 ...")
        depth_arg = args.depth or (fusion["depth"] if fusion else 0)
        v, blocks = synth.build_volume(
            regions,
            win_s,
            sinfo,
            mats,
            depth=depth_arg,
            footprint=fusion["footprint"] if fusion else None,
        )
        solid_total = int((v >= 0).sum())
        v, trim_off = voxel.trim(v)
        glass_n = int((v == blocks.index(glass)).sum()) if glass in blocks else 0
        winfo = {"windows": sinfo["cols"] * sinfo["floors"] + 1, "cells": glass_n}
        print(f"      体量 {v.shape[0]}×{v.shape[1]}×{v.shape[2]}（宽×高×深），"
              f"内部中空，未跑削壳")

        model = voxel.Model(
            vox=v, blocks=blocks, meta={"source": str(src), "mode": "synth"}
        )
        model.meta["trim_offset"] = list(trim_off)
        model.meta["grid"] = [w, H]
        model.meta["original"] = list(orig)
        model.meta["features"] = {
            "floors": sinfo["floors"], "floor_h": sinfo["floor_h"],
            "pitch": sinfo["pitch"], "cols": sinfo["cols"],
            "depth": int(v.shape[2]),
        }

        # 预览图：合成立面本身（不是照片），用来核对文法是否落地
        prev_blocks = list(dict.fromkeys([wall_b, trim_b, plinth_b, glass]))
        prev_idx = {b.key: i for i, b in enumerate(prev_blocks)}
        grid = np.full((H, w), -1, dtype=np.int32)
        for r, blk in ((0, wall_b), (1, trim_b), (2, plinth_b)):
            grid[regions == r] = prev_idx[blk.key]
        grid[win_s] = prev_idx[glass.key]

        rgb_for_preview = np.zeros((H, w, 3), dtype=np.float64)
        for r, c in {
            0: np.array(wall_b.rgb) / 255.0,
            1: np.array(trim_b.rgb) / 255.0,
            2: np.array(plinth_b.rgb) / 255.0,
        }.items():
            rgb_for_preview[regions == r] = c
        rgb_for_preview[win_s] = np.array(glass.rgb) / 255.0

        print("[8/8] 导出 ...")
        sch = schematic.write_schematic(out_dir / f"{name}.schematic", v, blocks)
        ok = schematic.verify(out_dir / f"{name}.schematic", v, blocks)
        print(f"      {sch['path']}  {sch['bytes'] / 1024:.1f} KB  校验: {'通过' if ok else '失败'}")

        preview_png(out_dir / f"{name}_preview.png", rgb_for_preview, grid, prev_blocks)

        if not args.no_iso:
            ir = iso.render(out_dir / f"{name}_iso.png", v, blocks, tile=8)
            print(f"      {ir['path']}  {ir['blocks']:,} 块等轴测预览")

        if not args.no_viewer:
            vw = viewer.write_viewer(out_dir / f"{name}_view.html", v, blocks, name)
            print(f"      {vw['path']}  {vw['bytes'] / 1024:.1f} KB")

        # 临时 model 只为本函数内报告服务
        counts = model.counts()
        total = model.total
        lc = model.layer_counts()
        lines = [
            f"模型：{name}",
            f"尺寸：{model.size[0]} × {model.size[1]} × {model.size[2]} (X × Y × Z)",
            f"来源：{src}  模式：synth（特征提取+立面合成）",
            f"特征：{sinfo['floors']} 层 · 层高 {sinfo['floor_h']} · "
            f"窗距 {sinfo['pitch']} · {sinfo['cols']} 列",
            f"窗户：{winfo['windows']} 扇（含正门）/ {glass_n} 格玻璃",
            f"总方块数：{total:,}",
            f"方块种类：{len(counts)}",
            "",
            "方块数量清单：",
        ]
        for i, (b, n) in enumerate(counts, 1):
            lines.append(f"  {i:2d}. {_pad(b.name_zh, 14)} id:{_pad(b.old_id, 8)} {b.hex_color} {n:>7,d} 块")
        lines.append("")
        lines.append("各层方块数（下标 0 = 最底层）：")
        lines.append("  " + ", ".join(f"y{y}:{n}" for y, n in enumerate(lc) if n))
        report = "\n".join(lines)
        (out_dir / f"{name}_blocks.txt").write_text(report, encoding="utf-8")

        manifest = {
            "name": name,
            "source": str(src),
            "mode": "synth",
            "version": "1.12.2",
            "size": list(model.size),
            "origin": list(model.origin),
            "total": total,
            "solid_total": solid_total,
            "kinds": len(counts),
            "blocks": [
                {
                    "id": b.id, "meta": b.meta, "name_en": b.name_en,
                    "name_zh": b.name_zh, "hex": b.hex_color, "count": n,
                }
                for b, n in counts
            ],
            "layer_counts": {str(y): n for y, n in enumerate(lc) if n},
            "features": model.meta["features"],
            "views": (
                {
                    "roles": fusion["views"],
                    "depth_source": fusion["depth_source"],
                    "roof_type": fusion["roof_type"],
                    "footprint_ratio": fusion["footprint_ratio"],
                    "notes": fusion["notes"],
                    "metrics": fusion["metrics"],
                }
                if fusion
                else {"roles": ["front"], "depth_source": "guess", "notes": []}
            ),
            "params": {
                "preset": args.preset, "width": args.width, "depth": int(v.shape[2]),
                "sat": args.sat,
                "windows": winfo["windows"], "glass_cells": glass_n,
                "views": len(views),
            },
        }
        (out_dir / f"{name}_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        print()
        print(report)
        print()
        print(f"总方块数 {total:,}（墙体为壳体，内部中空可进入）")
        print(f"输出目录：{out_dir.resolve()}")
        return 0

    # ================================================================
    # v3 路线：building / flat / relief / terrain（按照片逐格重建）
    # ================================================================
    # 树、绿篱、绿色栏杆不是建筑，留着会变成一块突兀的深绿
    veg_frac = 0.0
    if not args.no_sky:
        r_, g_, b_ = rgb[..., 0], rgb[..., 1], rgb[..., 2]
        veg = (g_ > r_ * 1.06) & (g_ > b_ * 1.06) & (g_ > 0.15)
        veg_frac = float(veg.mean())
        if 0.001 < veg_frac < 0.25:
            print(f"      排除植被 {veg_frac * 100:.0f}%（树、绿篱、绿色栏杆）")

    print(f"[3/8] 构建厚度先验（mode={args.mode}）...")
    if args.mode == "building":
        th = geometry.thickness_flat(rgb, base=args.depth or 3, sky=not args.no_sky)
    elif args.mode == "flat":
        th = geometry.thickness_flat(rgb, base=1, sky=not args.no_sky)
    elif args.mode == "relief":
        th = geometry.build_thickness(
            rgb, "relief", strength=args.strength, sky=not args.no_sky
        )
    else:
        th = geometry.build_thickness(rgb, "terrain", sky=not args.no_sky)
    if veg_frac > 0.001 and veg_frac < 0.25:
        th[veg] = 0

    # 照片四周的黑色边框会变成一根突兀的灰柱子，直接判为"不放方块"
    lum_g = 0.2126 * rgb[..., 0] + 0.7152 * rgb[..., 1] + 0.0722 * rgb[..., 2]
    sat_g = rgb.max(axis=-1) - rgb.min(axis=-1)
    dark = (lum_g < 0.14) & (sat_g < 0.10)
    if dark.any():
        th[dark] = 0

    # 裁掉底部不是墙的行（台阶、广场、马路）——
    # 它们最后会变成一整块灰底座，把楼压得又矮又脏。
    warm = (rgb[..., 0] - rgb[..., 2]) / (rgb[..., 0] + rgb[..., 2] + 1e-6)
    row_wall = (warm > 0.12).mean(axis=1)
    wall_rows = np.nonzero(row_wall > 0.25)[0]
    if wall_rows.size and wall_rows[-1] < h - 1:
        th[wall_rows[-1] + 1 :, :] = 0
        print(f"      裁掉底部 {h - 1 - wall_rows[-1]} 行非墙体（台阶/广场）")
    if sky_hi is not None and sky_hi.any():
        sky_lo = (
            geometry.downsample(
                np.repeat(sky_hi[..., None].astype(np.float64), 3, axis=2), w
            )[..., 0]
            > 0.5
        )
        th[sky_lo] = 0
    print(f"      纵深 {int(th.max())} 块，有效格 {int((th > 0).sum())} / {w * h}")

    print(f"[4/8] 识别窗户 ...")
    win = None
    winfo = {"windows": 0, "cells": 0}
    if not args.no_glass:
        hi = geometry.downsample(raw, max(width * 2, 64))
        hi = color.boost_saturation(hi, args.sat) if args.sat != 1.0 else hi
        hi_skip = geometry.sky_mask(hi) if not args.no_sky else None
        win, winfo = semantic.window_grid(
            hi,
            (h, w),
            hi_skip,
            min_w=max(1, round(w * 0.035)),
            max_w=max(3, round(w * 0.16)),
            max_h=max(1, round(h * 0.09)),
        )
        print(f"      找到 {winfo['windows']} 扇窗，共 {winfo['cells']} 格玻璃")
        if args.save_windows:
            dbg = semantic.debug_overlay(rgb_c, [(0, 0, 0, 0)])
            from PIL import Image as _I

            tmp = (np.clip(rgb_c, 0, 1) * 255).astype(np.uint8).copy()
            tmp[win] = (0, 230, 230)
            _I.fromarray(np.repeat(np.repeat(tmp, 12, 0), 12, 1)).save(
                out_dir / f"{name}_windows.png"
            )
            _ = dbg

    # ---- 5. 规整化：窗统一尺寸 / 等间距重排 ----
    print(f"[5/8] 规整化（clean={args.clean}）...")
    sym_before = 0.0
    if win is not None and win.any() and args.clean >= 1:
        win, uinfo = tidy.gridify_windows(win)
        if uinfo.get("skipped"):
            print(f"      窗：只检测到 {uinfo['before']} 扇，保持原样不做阵列重画")
        else:
            print(
                f"      窗：{uinfo['rows']} 排 × {uinfo['segs']} 段，重画 {uinfo['windows']} 扇"
                f"（检测到 {uinfo['before']} 扇，窗宽 {uinfo['bw']} 格）"
            )
        winfo["windows"] = uinfo["before"]
        winfo["cells"] = int(win.sum())

    # 窗洞先填成墙色，否则一格里的"砖+窗框"会混成灰褐色，
    # 整面砖墙会被配成灰色石头。
    rgb_q = rgb_c
    if win is not None and win.any():
        rgb_q = tidy.fill_holes(rgb_c, win)

    # ---- 6. 精简调色板 ----
    print(f"[6/8] 精简调色板（上限 {args.k} 种）...")
    valid = th > 0
    grid, blocks, info = quantize.quantize(
        rgb_q, valid, k=args.k, smooth_passes=args.smooth, dedupe_thr=args.dedupe
    )
    print(f"      选中 {info['selected']} 种，实际使用 {info['used']} 种")

    if args.clean >= 1:
        before = int((grid >= 0).sum())
        grid = tidy.merge_regions(grid, min_area=3, rounds=2)
        n = int((grid >= 0).sum())
        print(f"      碎块合并：{before:,} -> {n:,} 格")

    if args.clean >= 1:
        # 建筑是横条纹结构：水平方向大胆抹，竖直方向轻手。
        kh = 5 + args.clean * 2
        grid = tidy.line_smooth(grid, win, kh=kh, kv=args.clean)
        print(f"      行列平滑：水平 {kh} 格 / 竖直 {args.clean} 格")
    sym_before = tidy.measure_symmetry(grid)
    print(f"      左右对称度 {sym_before * 100:.0f}%")

    glass_n = 0
    if win is not None and win.any():
        glass = palette.get(20, 0)
        if glass not in blocks:
            blocks.append(glass)
        grid = grid.copy()
        grid[win] = blocks.index(glass)
        glass_n = int(win.sum())

    if args.sym:
        grid = tidy.mirror_half(grid)
        if win is not None:
            win = tidy.mirror_half(win.astype(np.int8)).astype(bool)
        print(f"      左右镜像已应用（对称度 {sym_before * 100:.0f}% 时强行拉平）")

    print(f"[7/8] 体素化并提取表面壳层 ...")
    v = voxel.build(grid, th, win, window_inset=0)
    solid_total = int((v >= 0).sum())
    if not args.no_hollow:
        v = voxel.hollow_shell(v)
    v, trim_off = voxel.trim(v)

    model = voxel.Model(vox=v, blocks=blocks, meta={"source": str(src), "mode": args.mode})
    model.meta["trim_offset"] = list(trim_off)
    model.meta["grid"] = [w, h]
    model.meta["original"] = list(orig)

    print(f"[8/8] 导出 ...")
    sch = schematic.write_schematic(out_dir / f"{name}.schematic", v, blocks)
    ok = schematic.verify(out_dir / f"{name}.schematic", v, blocks)
    print(f"      {sch['path']}  {sch['bytes'] / 1024:.1f} KB  校验: {'通过' if ok else '失败'}")

    preview_png(out_dir / f"{name}_preview.png", rgb, grid, blocks)

    if not args.no_iso:
        ir = iso.render(out_dir / f"{name}_iso.png", v, blocks, tile=8)
        print(f"      {ir['path']}  {ir['blocks']:,} 块等轴测预览")

    if not args.no_viewer:
        vw = viewer.write_viewer(out_dir / f"{name}_view.html", v, blocks, name)
        print(f"      {vw['path']}  {vw['bytes'] / 1024:.1f} KB")

    (out_dir / f"{name}_layers.txt").write_text(layer_map_txt(model, blocks), encoding="utf-8")

    counts = model.counts()
    total = model.total
    lines = [
        f"模型：{name}",
        f"尺寸：{model.size[0]} × {model.size[1]} × {model.size[2]} (X × Y × Z)",
        f"来源：{src}  模式：{args.mode}  预设：{args.preset}（{desc}）",
        f"窗户：{winfo['windows']} 扇 / {glass_n} 格玻璃",
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
    (out_dir / f"{name}_blocks.txt").write_text(report, encoding="utf-8")

    manifest = {
        "name": name,
        "source": str(src),
        "mode": args.mode,
        "version": "1.12.2",
        "size": list(model.size),
        "origin": list(model.origin),
        "total": total,
        "solid_total": solid_total,
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
        "layer_counts": {str(y): n for y, n in enumerate(model.layer_counts()) if n},
        "params": {
            "preset": args.preset,
            "width": args.width,
            "k": args.k,
            "smooth": args.smooth,
            "depth": args.depth or 3,
            "sat": args.sat,
            "windows": winfo["windows"],
            "glass_cells": glass_n,
        },
    }
    (out_dir / f"{name}_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print()
    print(report.split("\n\n")[0])
    print()
    print("方块数量清单：")
    print(voxel.describe(model))
    print()
    print(f"总方块数 {total:,}（实心 {solid_total:,}，空心节省 {100 * (1 - total / max(solid_total, 1)):.0f}%）")
    print(f"输出目录：{out_dir.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
