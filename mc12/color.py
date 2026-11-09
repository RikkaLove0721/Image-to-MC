"""色彩空间转换与 CIEDE2000 色差（numpy 向量化）。

只依赖 numpy。用于把照片像素与 Minecraft 方块的平均颜色放到同一个
感知色空间里比较，避免直接比 RGB 造成的系统性偏色。
"""

from __future__ import annotations

import numpy as np


def boost_saturation(
    rgb: np.ndarray,
    factor: float = 1.0,
    protect_neutral: bool = True,
) -> np.ndarray:
    """绕着亮度放大色度：老照片、雾天、手机自动白平衡偏灰的图用得上。

    Minecraft 方块本身就是高饱和的，一张低饱和的照片直接做色差匹配，
    砖墙会被配成灰色羊毛 —— 看着"准"，却完全不像。

    但整体提饱和会把白色腰线、白色柱子一起染成粉色。所以默认按原始
    色度做加权：本来就没色的像素（白墙、灰地）不动，只有砖、木、瓦
    这类本来有色的区域才被加强。
    """
    if abs(factor - 1.0) < 1e-6:
        return rgb
    x = np.clip(np.asarray(rgb, dtype=np.float64), 0.0, 1.0)
    y = 0.2126 * x[..., 0] + 0.7152 * x[..., 1] + 0.0722 * x[..., 2]
    k = np.full_like(x, float(factor))
    if protect_neutral:
        chroma = x.max(axis=-1) - x.min(axis=-1)
        # 色度 0.03 以下视为纯中性（完全不动），0.12 以上全额提饱和
        w = np.clip((chroma - 0.03) / 0.09, 0.0, 1.0)[..., None]
        k = 1.0 + (float(factor) - 1.0) * w
    out = y[..., None] + (x - y[..., None]) * k
    return np.clip(out, 0.0, 1.0)


# D65 白点
_WHITE = np.array([0.95047, 1.00000, 1.08883], dtype=np.float64)


def srgb_to_linear(rgb: np.ndarray) -> np.ndarray:
    """sRGB(0..1) -> 线性光。"""
    rgb = np.asarray(rgb, dtype=np.float64)
    return np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)


def rgb_to_lab(rgb: np.ndarray) -> np.ndarray:
    """sRGB(0..255 或 0..1) -> CIELAB(D65)，返回 [..., 3]。"""
    rgb = np.asarray(rgb, dtype=np.float64)
    if rgb.max() > 1.5:
        rgb = rgb / 255.0
    lin = srgb_to_linear(np.clip(rgb, 0.0, 1.0))

    m = np.array(
        [
            [0.4124564, 0.3575761, 0.1804375],
            [0.2126729, 0.7151522, 0.0721750],
            [0.0193339, 0.1191920, 0.9503041],
        ]
    )
    xyz = lin @ m.T
    xyz = xyz / _WHITE

    eps = 216.0 / 24389.0
    kappa = 24389.0 / 27.0
    f = np.where(xyz > eps, np.cbrt(xyz), (kappa * xyz + 16.0) / 116.0)

    lab = np.empty_like(f)
    lab[..., 0] = 116.0 * f[..., 1] - 16.0
    lab[..., 1] = 500.0 * (f[..., 0] - f[..., 1])
    lab[..., 2] = 200.0 * (f[..., 1] - f[..., 2])
    return lab


def lab_to_rgb(lab: np.ndarray) -> np.ndarray:
    """CIELAB -> sRGB(0..255)，用于反查色板时显示。"""
    lab = np.asarray(lab, dtype=np.float64)
    fy = (lab[..., 0] + 16.0) / 116.0
    fx = fy + lab[..., 1] / 500.0
    fz = fy - lab[..., 2] / 200.0

    eps = 216.0 / 24389.0
    kappa = 24389.0 / 27.0
    x = np.where(fx ** 3 > eps, fx ** 3, (116.0 * fx - 16.0) / kappa)
    y = np.where(lab[..., 0] > kappa * eps, fy ** 3, lab[..., 0] / kappa)
    z = np.where(fz ** 3 > eps, fz ** 3, (116.0 * fz - 16.0) / kappa)

    m = np.array(
        [
            [3.2404542, -1.5371385, -0.4985314],
            [-0.9692660, 1.8760108, 0.0415560],
            [0.0556434, -0.2040259, 1.0572252],
        ]
    )
    xyz = np.stack([x, y, z], axis=-1) * _WHITE
    lin = np.clip(xyz @ m.T, 0.0, None)
    out = np.where(lin <= 0.0031308, 12.92 * lin, 1.055 * np.power(lin, 1 / 2.4) - 0.055)
    return np.clip(out, 0.0, 1.0)


def _wrap_deg(x):
    return np.mod(x, 360.0)


def delta_e_00(lab1: np.ndarray, lab2: np.ndarray) -> np.ndarray:
    """CIEDE2000 色差。

    lab1: (..., 3) 或可广播形状；lab2: (..., 3)。
    典型用法：lab1 形状 (P, 1, 3)，lab2 形状 (1, B, 3) -> 输出 (P, B)。
    """
    lab1 = np.asarray(lab1, dtype=np.float64)
    lab2 = np.asarray(lab2, dtype=np.float64)

    L1, a1, b1 = lab1[..., 0], lab1[..., 1], lab1[..., 2]
    L2, a2, b2 = lab2[..., 0], lab2[..., 1], lab2[..., 2]

    C1 = np.hypot(a1, b1)
    C2 = np.hypot(a2, b2)
    C_bar = (C1 + C2) / 2.0

    C_bar7 = C_bar ** 7
    G = 0.5 * (1.0 - np.sqrt(C_bar7 / (C_bar7 + 25.0 ** 7)))

    a1p = (1.0 + G) * a1
    a2p = (1.0 + G) * a2
    C1p = np.hypot(a1p, b1)
    C2p = np.hypot(a2p, b2)

    h1p = _wrap_deg(np.degrees(np.arctan2(b1, a1p)))
    h2p = _wrap_deg(np.degrees(np.arctan2(b2, a2p)))

    dLp = L2 - L1
    dCp = C2p - C1p

    chroma_zero = (C1p * C2p) == 0
    dhp = h2p - h1p
    dhp = np.where(dhp > 180.0, dhp - 360.0, dhp)
    dhp = np.where(dhp < -180.0, dhp + 360.0, dhp)
    dhp = np.where(chroma_zero, 0.0, dhp)
    dHp = 2.0 * np.sqrt(C1p * C2p) * np.sin(np.radians(dhp / 2.0))

    Lp_bar = (L1 + L2) / 2.0
    Cp_bar = (C1p + C2p) / 2.0

    hsum = h1p + h2p
    habs = np.abs(h1p - h2p)
    hp_bar = np.where(
        chroma_zero,
        hsum,
        np.where(
            habs <= 180.0,
            hsum / 2.0,
            np.where(hsum < 360.0, (hsum + 360.0) / 2.0, (hsum - 360.0) / 2.0),
        ),
    )

    T = (
        1.0
        - 0.17 * np.cos(np.radians(hp_bar - 30.0))
        + 0.24 * np.cos(np.radians(2.0 * hp_bar))
        + 0.32 * np.cos(np.radians(3.0 * hp_bar + 6.0))
        - 0.20 * np.cos(np.radians(4.0 * hp_bar - 63.0))
    )

    d_theta = 30.0 * np.exp(-(((hp_bar - 275.0) / 25.0) ** 2))
    Cp_bar7 = Cp_bar ** 7
    R_C = 2.0 * np.sqrt(Cp_bar7 / (Cp_bar7 + 25.0 ** 7))
    S_L = 1.0 + (0.015 * (Lp_bar - 50.0) ** 2) / np.sqrt(20.0 + (Lp_bar - 50.0) ** 2)
    S_C = 1.0 + 0.045 * Cp_bar
    S_H = 1.0 + 0.015 * Cp_bar * T
    R_T = -np.sin(np.radians(2.0 * d_theta)) * R_C

    t1 = dLp / S_L
    t2 = dCp / S_C
    t3 = dHp / S_H
    return np.sqrt(t1 * t1 + t2 * t2 + t3 * t3 + R_T * t2 * t3)


def self_test() -> bool:
    """Sharma 标准测试对，用于确认实现正确。"""
    pairs = [
        ((50.0000, 2.6772, -79.7751), (50.0000, 0.0000, -82.7485), 2.0425),
        ((50.0000, 3.1571, -77.2803), (50.0000, 0.0000, -82.7485), 2.8615),
        ((50.0000, 2.8361, -74.0200), (50.0000, 0.0000, -82.7485), 3.4412),
        ((50.0000, -1.3802, -84.2814), (50.0000, 0.0000, -82.7485), 1.0000),
        ((50.0000, -1.1848, -84.8006), (50.0000, 0.0000, -82.7485), 1.0000),
        ((50.0000, 0.0000, 0.0000), (50.0000, -1.0000, 2.0000), 2.3669),
        ((50.0000, 2.4900, -0.0010), (50.0000, -2.4900, 0.0009), 7.1792),
        ((50.0000, 2.4900, -0.0010), (50.0000, -2.4900, 0.0012), 7.2195),
    ]
    ok = True
    for a, b, expect in pairs:
        got = float(delta_e_00(np.array(a), np.array(b)))
        if abs(got - expect) > 1e-3:
            ok = False
            print(f"  deltaE00 fail: {a} vs {b} -> {got:.4f}, expect {expect}")
    return ok


if __name__ == "__main__":
    print("deltaE00 self test:", "PASS" if self_test() else "FAIL")
