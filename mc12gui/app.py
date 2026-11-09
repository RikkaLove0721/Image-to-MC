"""主窗口: 顶部标题栏, 三个分页, 底部状态栏.

分页顺序与用户习惯的动线一致: 先给照片, 再看结果, 最后查仓库.
"""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import ttk

from . import theme
from .page_input import InputPage, open_path
from .page_library import LibraryPage
from .page_output import OutputPage


class App(tk.Tk):
    """MC 工程图主窗口."""

    def __init__(self, base_dir: Path, version: str):
        super().__init__()
        self.base_dir = base_dir
        self.version = version
        self.out_dir = resolve_output(base_dir)
        self.in_dir = base_dir / "input"
        theme.apply(self)

        self.title(f"MC 工程图 {version} - 照片转 Minecraft 方块模型")
        self.minsize(940, 560)
        self.configure(bg=theme.BG)
        self._center()

        self._build_header()
        self._build_status()
        self._build_body()
        self.bind("<Control-o>", lambda e: self.pages[0]._pick())
        self.after(120, self._iconize)

    # ------------------------------------------------------------------
    # 骨架
    # ------------------------------------------------------------------
    def _center(self) -> None:
        self.update_idletasks()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        w = min(1220, max(980, sw - 80))
        h = min(840, max(560, sh - 110))
        x = max(0, (sw - w) // 2)
        y = max(0, (sh - h) // 2 - 16)
        self.geometry(f"{w}x{h}+{x}+{y}")

    def _build_header(self) -> None:
        head = tk.Frame(self, bg=theme.BG)
        head.pack(fill="x", padx=16, pady=(10, 0))
        left = tk.Frame(head, bg=theme.BG)
        left.pack(side="left")
        tk.Label(
            left, text="MC 工程图", bg=theme.BG, fg=theme.TEXT,
            font=theme.fonts()["title"],
        ).pack(side="left")
        tk.Label(
            left, text="照片转 Minecraft 1.12.2 方块模型", bg=theme.BG, fg=theme.MUTED,
            font=theme.fonts()["sub"],
        ).pack(side="left", padx=(12, 0), pady=(4, 0))

        right = tk.Frame(head, bg=theme.BG)
        right.pack(side="right")
        tk.Label(
            right, text="输出目录", bg=theme.BG, fg=theme.FAINT,
            font=theme.fonts()["xs"],
        ).pack(side="left", padx=(0, 6))
        self.lbl_out = tk.Label(
            right, text=str(self.out_dir), bg=theme.BG, fg=theme.MUTED,
            font=theme.fonts()["sm"],
        )
        self.lbl_out.pack(side="left")
        ttk.Button(right, text="打开", style="Ghost.TButton", command=self._open_out).pack(
            side="left", padx=(6, 0)
        )

    def _build_body(self) -> None:
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=10, pady=(8, 0))

        self.page_input = InputPage(self.notebook, self)
        self.page_output = OutputPage(self.notebook, self)
        self.page_library = LibraryPage(self.notebook, self)
        self.pages = [self.page_input, self.page_output, self.page_library]
        for page, title in zip(
            self.pages, ["输入工作图片", "查看输出结果", "查看仓库内容"]
        ):
            self.notebook.add(page, text=title)
        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab)

    def _build_status(self) -> None:
        bar = tk.Frame(self, bg=theme.PANEL, height=28)
        bar.pack(fill="x", side="bottom")
        tk.Frame(bar, bg=theme.LINE, height=1).pack(fill="x", side="top")
        self.lbl_status = tk.Label(
            bar, text="就绪", bg=theme.PANEL, fg=theme.MUTED,
            font=theme.fonts()["sm"], anchor="w", padx=16, pady=6,
        )
        self.lbl_status.pack(side="left", fill="x", expand=True)
        tk.Label(
            bar, text=f"v{self.version}   1.12.2", bg=theme.PANEL, fg=theme.FAINT,
            font=theme.fonts()["xs"], padx=16,
        ).pack(side="right")

    def _iconize(self) -> None:
        try:
            from PIL import Image, ImageDraw, ImageTk

            size = 64
            img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
            d = ImageDraw.Draw(img)
            top = [(32, 4), (60, 18), (32, 32), (4, 18)]
            left = [(4, 18), (32, 32), (32, 60), (4, 46)]
            right = [(60, 18), (60, 46), (32, 60), (32, 32)]
            d.polygon(top, fill="#7FA6E8")
            d.polygon(left, fill="#2F6FDE")
            d.polygon(right, fill="#1F4FA8")
            self._icon = ImageTk.PhotoImage(img)
            self.iconphoto(True, self._icon)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # 交互
    # ------------------------------------------------------------------
    def _on_tab(self, _event=None) -> None:
        try:
            index = self.notebook.index(self.notebook.select())
        except tk.TclError:
            return
        page = self.pages[index]
        if hasattr(page, "on_show"):
            page.on_show()

    def go_output(self, name: str | None = None) -> None:
        self.notebook.select(1)
        self.page_output.refresh()
        if name:
            self.page_output.select(name)

    def go_input(self) -> None:
        self.notebook.select(0)

    def refresh_all(self) -> None:
        self.page_output.refresh()
        self.page_library.refresh()

    def status(self, text: str, kind: str = "info") -> None:
        color = {
            "ok": theme.OK,
            "err": theme.ERR,
            "warn": theme.WARN,
            "info": theme.MUTED,
        }.get(kind, theme.MUTED)
        self.lbl_status.configure(text=text, fg=color)

    def _open_out(self) -> None:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        open_path(self.out_dir)


def resolve_output(base_dir: Path) -> Path:
    """优先写 exe 同级的 output, 无写权限时退回用户目录."""
    candidate = base_dir / "output"
    try:
        candidate.mkdir(parents=True, exist_ok=True)
        probe = candidate / ".write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return candidate
    except OSError:
        fallback = Path.home() / "MC工程图" / "output"
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback
