"""MC 工程图的 MCP 服务（stdio JSON-RPC 2.0，无第三方依赖）。

作用：
    把"分析照片 / 拿到建筑常识 / 校验蓝图 / 构建模型"做成工具，
    外部的 AI 客户端（WorkBuddy、Claude、Cursor…）直接调用即可。

为什么省 token：
    AI 不需要读图，也不需要吐上万个方块。它拿到的是照片分析出的几十行
    brief，写的是几十行 spec；像素处理和体素展开全在本地做。

启动（在 mcp 客户端里配置）：
    {"command": "python", "args": ["D:/.../mcp_server.py"]}
或者用打包后的后端：
    {"command": "D:/.../core/mc12-core.exe", "args": ["--mcp"]}
"""

from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mc12 import analyze, knowledge, palette, spec as spec_mod, voxel  # noqa: E402

SERVER_INFO = {"name": "mc12-studio", "version": "0.15.0"}
PROTOCOL = "2024-11-05"


# --------------------------------------------------------------------------
# 工具
# --------------------------------------------------------------------------

def tool_photo_brief(args: dict) -> dict:
    path = str(args.get("path") or "")
    if not path:
        raise ValueError("需要 path：照片路径")
    width = int(args.get("width") or 40)
    brief = analyze.analyze(path, width)
    return {"brief": brief}


def tool_rules(args: dict) -> dict:
    """设计常识 + 可选方块清单。AI 写 spec 前先看一眼，能少来回几轮。

    subject=ship 时给舰船那套（尖艏/甲板/上层建筑/栏杆旗杆），
    建筑和船是两套语法，给错了 AI 会把船写成一栋长条写字楼。
    """
    q = str(args.get("query") or "").strip().lower()
    blocks = []
    for b in palette.all_blocks():
        name = f"{b.name_en}:{b.meta}"
        if q and q not in name and q not in b.name_zh:
            continue
        blocks.append({"id": name, "zh": b.name_zh, "hex": b.hex_color})
        if len(blocks) >= 60:
            break
    return {
        "rules": knowledge.rules_text(
            str(args.get("subject") or ""), ornament=bool(args.get("ornament"))
        ),
        "blocks": blocks,
    }


def tool_validate(args: dict) -> dict:
    raw = args.get("spec")
    if isinstance(raw, str):
        raw = json.loads(raw)
    if not isinstance(raw, dict):
        raise ValueError("需要 spec 对象")
    return knowledge.check_spec(raw)


def tool_build(args: dict) -> dict:
    raw = args.get("spec")
    if isinstance(raw, str):
        raw = json.loads(raw)
    if not isinstance(raw, dict):
        raise ValueError("需要 spec 对象")
    check = knowledge.check_spec(raw)
    if check["errors"] and not args.get("force"):
        return {"ok": False, "errors": check["errors"], "warnings": check["warnings"]}

    out_dir = Path(str(args.get("out") or (ROOT / "output" / "mcp")))
    out_dir.mkdir(parents=True, exist_ok=True)
    name = str(args.get("name") or raw.get("name") or "model")

    sp = spec_mod.loads(raw)
    vox, blocks, sinfo = spec_mod.build(sp)
    vox, _off = voxel.trim(vox)
    model = voxel.Model(vox=vox, blocks=blocks, meta={"source": "mcp", "mode": "spec"})

    from mc12 import iso, schematic, viewer

    sch = schematic.write_schematic(out_dir / f"{name}.schematic", vox, blocks)
    ok = schematic.verify(out_dir / f"{name}.schematic", vox, blocks)
    iso.render(out_dir / f"{name}_iso.png", vox, blocks, tile=8)
    viewer.write_viewer(out_dir / f"{name}_view.html", vox, blocks, name=name)
    counts = [
        {"id": f"{b.name_en}:{b.meta}", "zh": b.name_zh, "count": n}
        for b, n in model.counts()
    ]
    return {
        "ok": bool(ok),
        "files": {
            "schematic": str(out_dir / f"{name}.schematic"),
            "iso": str(out_dir / f"{name}_iso.png"),
            "viewer": str(out_dir / f"{name}_view.html"),
        },
        "size": list(model.size),
        "total": model.total,
        "kinds": len(counts),
        "blocks": counts[:20],
        "build": sinfo,
        "warnings": check["warnings"],
    }


def tool_critique(args: dict) -> dict:
    """构建 + 体检：把"像不像"变成分数、问题清单和可执行的修改建议。

    这是让 AI 能**自己跑闭环**的那一环：
        photo_brief -> 写 spec -> critique -> 改 spec -> critique ...
    每轮只回传几十行数字，不用重发整份蓝图，也不用看图。
    """
    from mc12 import analyze as _analyze, critique as _critique

    raw = args.get("spec")
    if isinstance(raw, str):
        raw = json.loads(raw)
    if not isinstance(raw, dict):
        raise ValueError("需要 spec 对象")
    photo = str(args.get("photo") or "")
    if not photo:
        raise ValueError("需要 photo：照片路径")

    sp = spec_mod.loads(raw)
    vox, blocks, _info = spec_mod.build(sp)
    vox, _off = voxel.trim(vox)
    brief = _analyze.analyze(photo, int(args.get("width") or 40))
    out = _critique.critique(brief, sp, vox, blocks)
    out["check"] = knowledge.check_spec(raw)
    return out


TOOLS = [
    {
        "name": "photo_brief",
        "description": "分析一张建筑照片，返回紧凑的建筑信息（体块分段、窗阵节奏、主色、场景占比）。AI 只需要读这份 brief，不用看图。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "照片绝对路径"},
                "width": {"type": "integer", "description": "目标网格宽度，默认 40"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "mc_rules",
        "description": "返回 MC 设计常识与可选方块清单。写 spec 前调用一次，能少走弯路。"
        "subject=ship 时返回舰船那套（尖艏/甲板/上层建筑/栏杆旗杆）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "按名字过滤方块，可空"},
                "subject": {
                    "type": "string",
                    "description": "building（默认）/ ship —— 舰船与建筑是两套语法",
                },
                "ornament": {
                    "type": "boolean",
                    "description": "true 时附带「观赏性细节」（detail=4）那一节：小件堆叠 + 材质色差",
                },
            },
        },
    },
    {
        "name": "validate_spec",
        "description": "检查一份蓝图 spec 是否符合 MC 建筑常识，返回 errors（必须改）与 warnings（建议改）。",
        "inputSchema": {
            "type": "object",
            "properties": {"spec": {"type": "object", "description": "蓝图 spec"}},
            "required": ["spec"],
        },
    },
    {
        "name": "build_model",
        "description": "按蓝图 spec 构建模型，输出 .schematic / 等轴测图 / 三维查看器。构建全在本地，AI 不需要输出方块。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "spec": {"type": "object", "description": "蓝图 spec"},
                "out": {"type": "string", "description": "输出目录，默认 output/mcp"},
                "name": {"type": "string", "description": "模型名"},
                "force": {"type": "boolean", "description": "有硬错误也强行构建"},
            },
            "required": ["spec"],
        },
    },
    {
        "name": "critique_model",
        "description": (
            "构建蓝图并和照片对比体检，返回 0~100 分、具体问题清单（比例/屋顶/体量/窗/配色/构件）"
            "以及可直接套用的 patches。**建议至少跑两轮**：写 spec → critique → 只改 patches 指出的地方 → 再 critique。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "spec": {"type": "object", "description": "蓝图 spec"},
                "photo": {"type": "string", "description": "照片绝对路径（与写 spec 时用同一张）"},
                "width": {"type": "integer", "description": "分析网格宽度，默认 40"},
            },
            "required": ["spec", "photo"],
        },
    },
]

HANDLERS = {
    "photo_brief": tool_photo_brief,
    "mc_rules": tool_rules,
    "validate_spec": tool_validate,
    "build_model": tool_build,
    "critique_model": tool_critique,
}


# --------------------------------------------------------------------------
# JSON-RPC over stdio
# --------------------------------------------------------------------------

def _reply(msg_id, result=None, error=None) -> dict:
    out = {"jsonrpc": "2.0", "id": msg_id}
    if error is not None:
        out["error"] = error
    else:
        out["result"] = result
    return out


def handle(msg: dict) -> dict | None:
    method = msg.get("method")
    mid = msg.get("id")
    params = msg.get("params") or {}

    if method == "initialize":
        return _reply(
            mid,
            {
                "protocolVersion": PROTOCOL,
                "capabilities": {"tools": {}},
                "serverInfo": SERVER_INFO,
            },
        )
    if method in ("notifications/initialized", "initialized"):
        return None
    if method == "ping":
        return _reply(mid, {})
    if method == "tools/list":
        return _reply(mid, {"tools": TOOLS})
    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        fn = HANDLERS.get(name)
        if fn is None:
            return _reply(mid, error={"code": -32601, "message": f"未知工具 {name}"})
        try:
            result = fn(args)
            return _reply(
                mid,
                {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}]},
            )
        except Exception as exc:  # noqa: BLE001
            return _reply(
                mid,
                {
                    "content": [
                        {
                            "type": "text",
                            "text": f"出错：{exc}\n{traceback.format_exc(limit=3)}",
                        }
                    ],
                    "isError": True,
                },
            )
    return _reply(mid, error={"code": -32601, "message": f"未知方法 {method}"})


def serve() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        out = handle(msg)
        if out is not None:
            sys.stdout.write(json.dumps(out, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    serve()
