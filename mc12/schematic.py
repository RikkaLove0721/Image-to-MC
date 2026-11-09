"""导出 Minecraft 1.12 可读的经典 .schematic（MCEdit / WorldEdit 格式）。

1.12 没有 Sponge 的 .schem 格式，最通用的就是这种「数字 ID + Data」的
经典 schematic：
    Width / Height / Length : short
    Materials               : "Alpha"
    Blocks                  : byte[W*H*L]   —— 方块数字 ID
    Data                    : byte[W*H*L]   —— metadata
索引顺序：(y * Length + z) * Width + x
文件本体是 gzip 压缩的无名 NBT。
"""

from __future__ import annotations

import gzip
import struct
from pathlib import Path

import numpy as np

from .palette import Block


def _s(v: int) -> bytes:
    return struct.pack(">h", int(v))


def _i(v: int) -> bytes:
    return struct.pack(">i", int(v))


def _name(s: str) -> bytes:
    b = s.encode("utf-8")
    return struct.pack(">H", len(b)) + b


def _t_short(name: str, v: int) -> bytes:
    return b"\x02" + _name(name) + _s(v)


def _t_int(name: str, v: int) -> bytes:
    return b"\x03" + _name(name) + _i(v)


def _t_string(name: str, s: str) -> bytes:
    return b"\x08" + _name(name) + _name(s)


def _t_byte_array(name: str, data: bytes) -> bytes:
    return b"\x07" + _name(name) + _i(len(data)) + data


def _t_empty_list(name: str, elem_type: int = 10) -> bytes:
    return b"\x09" + _name(name) + bytes([elem_type]) + _i(0)


def write_schematic(path: str | Path, vox: np.ndarray, blocks: list[Block]) -> dict:
    """vox: (X, Y, Z) int16，-1 为空；blocks: palette 索引对应的方块表。"""
    path = Path(path)
    x_len, y_len, z_len = vox.shape

    ids = np.zeros(len(blocks), dtype=np.uint8)
    metas = np.zeros(len(blocks), dtype=np.uint8)
    for i, b in enumerate(blocks):
        if b.id > 255:
            raise ValueError(f"方块 {b.label} 的 ID 超出单字节范围，1.12 需要 AddBlocks 支持")
        ids[i] = b.id
        metas[i] = b.meta

    # (X,Y,Z) -> (Y,Z,X)，C 序展平后下标正好是 (y*Z + z)*X + x
    idx = vox.transpose(1, 2, 0).ravel()
    valid = idx >= 0
    safe = np.where(valid, idx, 0)
    block_bytes = np.where(valid, ids[safe], 0).astype(np.uint8).tobytes()
    data_bytes = np.where(valid, metas[safe], 0).astype(np.uint8).tobytes()

    body = b""
    body += _t_short("Width", x_len)
    body += _t_short("Height", y_len)
    body += _t_short("Length", z_len)
    body += _t_string("Materials", "Alpha")
    body += _t_byte_array("Blocks", block_bytes)
    body += _t_byte_array("Data", data_bytes)
    body += _t_empty_list("Entities")
    body += _t_empty_list("TileEntities")
    body += _t_int("WEOriginX", 0)
    body += _t_int("WEOriginY", 0)
    body += _t_int("WEOriginZ", 0)
    body += _t_int("WEOffsetX", 0)
    body += _t_int("WEOffsetY", 0)
    body += _t_int("WEOffsetZ", 0)

    raw = b"\x0a" + _name("") + body + b"\x00"

    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wb") as f:
        f.write(raw)

    return {
        "path": str(path),
        "size": [int(x_len), int(y_len), int(z_len)],
        "bytes": path.stat().st_size,
    }


def read_schematic(path: str | Path) -> dict:
    """极简回读，用于自检导出的文件是否合法（不依赖第三方 NBT 库）。"""
    with gzip.open(path, "rb") as f:
        raw = f.read()

    pos = 0
    assert raw[pos] == 0x0A, "根 tag 不是 compound"
    pos += 1
    nlen = struct.unpack_from(">H", raw, pos)[0]
    pos += 2 + nlen

    out: dict = {}
    while True:
        tid = raw[pos]
        pos += 1
        if tid == 0:
            break
        nlen = struct.unpack_from(">H", raw, pos)[0]
        pos += 2
        name = raw[pos : pos + nlen].decode("utf-8")
        pos += nlen

        if tid == 0x02:
            out[name] = struct.unpack_from(">h", raw, pos)[0]
            pos += 2
        elif tid == 0x03:
            out[name] = struct.unpack_from(">i", raw, pos)[0]
            pos += 4
        elif tid == 0x07:
            ln = struct.unpack_from(">i", raw, pos)[0]
            pos += 4
            out[name] = raw[pos : pos + ln]
            pos += ln
        elif tid == 0x08:
            ln = struct.unpack_from(">H", raw, pos)[0]
            pos += 2
            out[name] = raw[pos : pos + ln].decode("utf-8")
            pos += ln
        elif tid == 0x09:
            et = raw[pos]
            ln = struct.unpack_from(">i", raw, pos + 1)[0]
            pos += 5
            out[name] = ("list", et, ln)
            if et == 10 and ln > 0:
                raise NotImplementedError("回读暂不支持非空 compound 列表")
        else:
            raise NotImplementedError(f"回读暂不支持 tag 类型 {tid}")
    return out


def verify(path: str | Path, vox: np.ndarray, blocks: list[Block]) -> bool:
    d = read_schematic(path)
    x_len, y_len, z_len = vox.shape
    ok = (
        d.get("Width") == x_len
        and d.get("Height") == y_len
        and d.get("Length") == z_len
        and d.get("Materials") == "Alpha"
        and len(d.get("Blocks", b"")) == x_len * y_len * z_len
        and len(d.get("Data", b"")) == x_len * y_len * z_len
    )
    if not ok:
        return False

    idx = vox.transpose(1, 2, 0).ravel()
    valid = idx >= 0
    expect_id = np.array([b.id for b in blocks], dtype=np.uint8)[np.clip(idx, 0, len(blocks) - 1)]
    got_id = np.frombuffer(d["Blocks"], dtype=np.uint8)
    return bool(np.array_equal(got_id[valid], expect_id[valid]))
