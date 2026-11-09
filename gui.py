"""MC 工程图 图形界面入口.

开发时: python gui.py
打包后: 双击 exe, 与命令行参数无关, 所有操作都在窗口里完成.

关于高分屏: 这里刻意不调用 SetProcessDpiAwareness. 界面布局用的是固定像素
留白, 一旦声明 DPI 感知, 系统会按物理像素绘制, 而字号仍按 DPI 放大, 结果是
150% 缩放下文字撑破卡片. 交给系统做整窗等比缩放, 各种缩放比例下版式一致.
"""

from __future__ import annotations

import sys
import tkinter.messagebox as messagebox
from pathlib import Path


def app_dir() -> Path:
    """打包后取 exe 所在目录, 开发时取项目根目录."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def read_version(base: Path) -> str:
    candidates: list[Path] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass) / "VERSION")
    candidates.append(base / "VERSION")
    for path in candidates:
        try:
            text = path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if text:
            return text
    return "0.0.0"


def main() -> int:
    base = app_dir()
    try:
        from mc12gui.app import App

        App(base, read_version(base)).mainloop()
        return 0
    except Exception as exc:  # 打包后没有控制台, 出错必须弹窗
        try:
            messagebox.showerror("MC 工程图 启动失败", f"{type(exc).__name__}: {exc}")
        except Exception:
            pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
