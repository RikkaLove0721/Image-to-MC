"""把项目打包成单文件 exe.

必须用带 tkinter 的 Python 运行, 项目自带的 3.13 没有 GUI 模块:
    temp/venv/Scripts/python.exe build_exe.py

产物: dist/MC工程图.exe
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
NAME = "MC工程图"
ICON = ROOT / "temp" / "app.ico"
DIST = ROOT / "dist"

TOP = [(128, 26), (232, 84), (128, 142), (24, 84)]
LEFT = [(24, 84), (128, 142), (128, 238), (24, 180)]
RIGHT = [(232, 84), (232, 180), (128, 238), (128, 142)]


def make_icon() -> Path:
    """画一个等轴测立方体当作程序图标."""
    from PIL import Image, ImageDraw

    ICON.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.polygon(TOP, fill="#7FA6E8")
    draw.polygon(LEFT, fill="#2F6FDE")
    draw.polygon(RIGHT, fill="#1F4FA8")
    img.save(
        ICON,
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    return ICON


def build(onefile: bool = True) -> int:
    icon = make_icon()
    args = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile" if onefile else "--onedir",
        "--windowed",
        "--name", NAME,
        "--icon", str(icon),
        "--add-data", f"{ROOT / 'VERSION'}{';' if sys.platform == 'win32' else ':'}.",
        "--collect-submodules", "mc12",
        "--collect-submodules", "mc12gui",
        "--distpath", str(DIST),
        "--workpath", str(ROOT / "temp" / "build"),
        "--specpath", str(ROOT / "temp"),
        str(ROOT / "gui.py"),
    ]
    print(" ".join(args))
    return subprocess.call(args)


def main() -> int:
    onefile = "--onedir" not in sys.argv
    code = build(onefile)
    if code == 0:
        exe = DIST / f"{NAME}.exe" if onefile else DIST / NAME / f"{NAME}.exe"
        size = exe.stat().st_size / 1024 / 1024 if exe.exists() else 0
        print(f"\n打包完成: {exe}  {size:.1f} MB")
    else:
        print(f"\n打包失败, PyInstaller 退出码 {code}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
