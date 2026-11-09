"""扫描 output 目录, 把一个模型散落的产物聚成一条记录.

run.py 每跑一次会写 6 到 8 个同名前缀的文件, 界面需要的是"一个模型"
而不是"一堆文件", 所以以 manifest 为索引把文件归拢起来.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

MODE_NAMES = {
    "synth": "立面合成",
    "building": "按照片重建",
    "hall": "室内大厅",
    "flat": "平板",
    "relief": "浮雕",
    "terrain": "地貌",
}

# 后缀与文件角色的对应关系, 顺序即界面上的按钮顺序
ARTIFACTS = [
    ("schematic", ".schematic"),
    ("iso", "_iso.png"),
    ("cut", "_cut.png"),
    ("preview", "_preview.png"),
    ("view", "_view.html"),
    ("blocks", "_blocks.txt"),
    ("layers", "_layers.txt"),
    ("manifest", "_manifest.json"),
]

IMAGE_KINDS = ("iso", "cut", "preview")


@dataclass
class ModelInfo:
    name: str
    mode: str
    size: tuple[int, int, int]
    total: int
    kinds: int
    source: str
    blocks: list[dict] = field(default_factory=list)
    files: dict[str, Path] = field(default_factory=dict)
    mtime: float = 0.0
    params: dict = field(default_factory=dict)

    @property
    def mode_text(self) -> str:
        return MODE_NAMES.get(self.mode, self.mode)

    @property
    def size_text(self) -> str:
        return " x ".join(str(v) for v in self.size)

    @property
    def source_name(self) -> str:
        return Path(self.source).name if self.source else "未记录"


def _read_manifest(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def scan(out_dir: Path) -> list[ModelInfo]:
    """按修改时间倒序返回 output 目录里的模型."""
    if not out_dir.is_dir():
        return []
    models: list[ModelInfo] = []
    for man in out_dir.glob("*_manifest.json"):
        data = _read_manifest(man)
        if not data:
            continue
        name = str(data.get("name") or man.name[: -len("_manifest.json")])
        size = data.get("size") or [0, 0, 0]
        info = ModelInfo(
            name=name,
            mode=str(data.get("mode") or ""),
            size=(int(size[0]), int(size[1]), int(size[2])),
            total=int(data.get("total") or 0),
            kinds=int(data.get("kinds") or len(data.get("blocks") or [])),
            source=str(data.get("source") or ""),
            blocks=list(data.get("blocks") or []),
            params=dict(data.get("params") or {}),
            mtime=man.stat().st_mtime,
        )
        for key, suffix in ARTIFACTS:
            p = out_dir / f"{name}{suffix}"
            if p.exists():
                info.files[key] = p
        models.append(info)
    models.sort(key=lambda m: m.mtime, reverse=True)
    return models


def images_of(model: ModelInfo) -> list[tuple[str, Path]]:
    """返回该模型可显示的图片, 按展示优先级排序, 键为图片种类."""
    return [(k, model.files[k]) for k in IMAGE_KINDS if k in model.files]
