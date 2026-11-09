"""把 2D 颜色网格挤出成体素网格，并做空心壳、分层统计。

坐标约定（严格对齐 Minecraft Java 右手系）：
    X+ = 东（照片横向向右）
    Y+ = 上（照片纵向向上，即图像行号翻转）
    Z+ = 南（向观察者挤出，照片的"正面"在最大 Z 处）

模型原点 O 自动取包围盒最小角，即 (0,0,0) 恒为模型最西北下角。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .palette import Block


@dataclass
class Model:
    vox: np.ndarray  # (X, Y, Z) int16，-1 = 空
    blocks: list[Block]
    origin: tuple[int, int, int] = (0, 0, 0)
    meta: dict = field(default_factory=dict)

    @property
    def size(self) -> tuple[int, int, int]:
        return tuple(int(v) for v in self.vox.shape)  # type: ignore[return-value]

    @property
    def total(self) -> int:
        return int((self.vox >= 0).sum())

    def counts(self) -> list[tuple[Block, int]]:
        n = len(self.blocks)
        c = np.bincount(self.vox[self.vox >= 0].ravel(), minlength=n)
        pairs = [(self.blocks[i], int(c[i])) for i in range(n) if c[i] > 0]
        pairs.sort(key=lambda p: -p[1])
        return pairs

    def layer_counts(self) -> list[int]:
        """按世界 Y 统计每层方块数，下标 0 = 最底层。"""
        present = self.vox >= 0
        return [int(v) for v in present.sum(axis=(0, 2))]

    def layer_blocks(self, y: int) -> np.ndarray:
        """取第 y 层（世界 Y 坐标）。"""
        return self.vox[:, y, :]


def build(
    grid: np.ndarray,
    thickness: np.ndarray,
    windows: np.ndarray | None = None,
    window_inset: int = 1,
) -> np.ndarray:
    """(H,W) 方块索引网格 + (H,W) 厚度 -> (X,Y,Z) 体素数组。

    windows: 窗洞掩码，True 的格子只保留最前面一块（玻璃），往后掏空，
    并把玻璃往里缩 inset 格，形成"窗洞内凹"的效果。
    """
    h, w = grid.shape
    t_max = int(thickness.max())
    if t_max < 1:
        t_max = 1

    # 翻转到世界坐标：grid 是 (H_img, W_img)，转置后是 (W, H_img)，
    # 再把图像行号反序，使图像顶部 = 世界 Y 最大处。
    lab_w = np.ascontiguousarray(grid.T[:, ::-1])       # (W, H)
    th_w = np.ascontiguousarray(thickness.T[:, ::-1])   # (W, H)

    z = np.arange(t_max)[None, None, :]
    filled = z < th_w[:, :, None]
    val = np.maximum(lab_w, 0)[:, :, None]

    vox = np.where(filled & (lab_w[:, :, None] >= 0), val, -1).astype(np.int16)

    if windows is not None and windows.any():
        win_w = np.ascontiguousarray(windows.T[:, ::-1])       # 同 build 的坐标变换
        front = np.clip(th_w - 1, 0, None)
        inset = max(0, min(int(window_inset), 1))
        plane = np.clip(front - inset, 0, None)
        keep = win_w[:, :, None] & (z == plane[:, :, None])
        vox = np.where(keep, val, vox)                        # 玻璃：只留最前一块
        vox = np.where(win_w[:, :, None] & ~keep, -1, vox)    # 其余深度掏空
    return vox


def hollow_shell(vox: np.ndarray) -> np.ndarray:
    """只保留表面壳层：四周（含模型外）有任意空气邻居的方块才留下。

    这是控制方块总数的关键一步，实心山体通常能砍掉 60%~80% 的方块。
    """
    m = vox >= 0
    if not m.any():
        return vox

    empty = np.pad(~m, 1, mode="constant", constant_values=True)
    near = np.zeros_like(m)
    near |= empty[0:-2, 1:-1, 1:-1]
    near |= empty[2:, 1:-1, 1:-1]
    near |= empty[1:-1, 0:-2, 1:-1]
    near |= empty[1:-1, 2:, 1:-1]
    near |= empty[1:-1, 1:-1, 0:-2]
    near |= empty[1:-1, 1:-1, 2:]

    return np.where(m & near, vox, -1).astype(np.int16)


def trim(vox: np.ndarray) -> tuple[np.ndarray, tuple[int, int, int]]:
    """裁到非空包围盒，返回 (新数组, 原坐标系里的偏移)。

    这样模型原点才是真正的"最小角"，空图层也不会虚占层号。
    """
    m = vox >= 0
    if not m.any():
        return vox, (0, 0, 0)

    xs = np.nonzero(m.any(axis=(1, 2)))[0]
    ys = np.nonzero(m.any(axis=(0, 2)))[0]
    zs = np.nonzero(m.any(axis=(0, 1)))[0]
    x0, x1 = int(xs[0]), int(xs[-1]) + 1
    y0, y1 = int(ys[0]), int(ys[-1]) + 1
    z0, z1 = int(zs[0]), int(zs[-1]) + 1
    return np.ascontiguousarray(vox[x0:x1, y0:y1, z0:z1]), (x0, y0, z0)


def describe(model: Model, limit: int | None = None) -> str:
    c = model.counts()
    lines = []
    for i, (b, n) in enumerate(c[: limit or len(c)]):
        w = sum(2 if ord(c) > 127 else 1 for c in b.label)
        lines.append(f"  {i + 1:2d}. {b.label}{' ' * max(0, 30 - w)} {n:>7,d}  块")
    return "\n".join(lines)
