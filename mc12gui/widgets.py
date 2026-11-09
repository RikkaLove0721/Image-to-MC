"""通用小部件: 卡片, 滚动容器, 折叠块, 色块图标.

页面里反复出现的容器都由这里生成, 避免每个页面各写一套布局代码.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from . import theme


class Card(ttk.Frame):
    """带 1px 描边的白色卡片.

    外层用线色填充, 内层留 1px 露出边线, 比 relief 边框干净.
    """

    def __init__(self, master: tk.Misc, padding: int = 14, **kw):
        super().__init__(master, style="Line.TFrame", **kw)
        self.body = ttk.Frame(self, style="Card.TFrame", padding=padding)
        self.body.pack(fill="both", expand=True, padx=1, pady=1)


class ScrollFrame(ttk.Frame):
    """纵向滚动容器, 内容放进 .inner."""

    def __init__(self, master: tk.Misc, bg: str = theme.BG, **kw):
        super().__init__(master, style="TFrame", **kw)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self.canvas = tk.Canvas(
            self, bg=bg, highlightthickness=0, bd=0, takefocus=0, height=80
        )
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.bar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self._on_scroll)
        self.inner = ttk.Frame(self.canvas, style="TFrame")
        self._win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", self._sync_region)
        self.canvas.bind("<Configure>", self._sync_width)
        self.canvas.bind("<Enter>", self._grab_wheel)
        self.canvas.bind("<Leave>", self._drop_wheel)

    def _sync_region(self, _event=None) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _sync_width(self, event) -> None:
        self.canvas.itemconfigure(self._win, width=event.width)

    def _on_scroll(self, first: str, last: str) -> None:
        if float(first) <= 0.0 and float(last) >= 1.0:
            self.bar.grid_remove()
        else:
            self.bar.grid(row=0, column=1, sticky="ns")
        self.bar.set(first, last)

    def _grab_wheel(self, _event=None) -> None:
        self.canvas.bind_all("<MouseWheel>", self._wheel)

    def _drop_wheel(self, _event=None) -> None:
        self.canvas.unbind_all("<MouseWheel>")

    def scroll_to_end(self) -> None:
        self.canvas.update_idletasks()
        self.canvas.yview_moveto(1.0)

    def _wheel(self, event) -> None:
        if self.canvas.winfo_height() >= self.inner.winfo_reqheight():
            return
        self.canvas.yview_scroll(int(-event.delta / 120), "units")


class Section(ttk.Frame):
    """卡片内的小节: 标题 + 右侧说明 + 内容区."""

    def __init__(self, master: tk.Misc, title: str, note: str = "", **kw):
        super().__init__(master, style="Card.TFrame", **kw)
        head = ttk.Frame(self, style="Card.TFrame")
        head.pack(fill="x")
        ttk.Label(head, text=title, style="H2.TLabel").pack(side="left")
        if note:
            ttk.Label(head, text=note, style="CardMuted.TLabel").pack(side="right")
        self.content = ttk.Frame(self, style="Card.TFrame")
        self.content.pack(fill="x", pady=(8, 0))


class Collapse(ttk.Frame):
    """可折叠区块, 默认收起, 用来安置低频参数."""

    def __init__(self, master: tk.Misc, title: str, **kw):
        super().__init__(master, style="Card.TFrame", **kw)
        self._open = False
        self.head = tk.Frame(self, bg=theme.PANEL_SOFT, cursor="hand2")
        self.head.pack(fill="x")
        self.arrow = tk.Label(
            self.head, text="+", bg=theme.PANEL_SOFT, fg=theme.MUTED,
            font=theme.fonts()["bold"], padx=8, pady=4,
        )
        self.arrow.pack(side="left")
        self.title = tk.Label(
            self.head, text=title, bg=theme.PANEL_SOFT, fg=theme.MUTED,
            font=theme.fonts()["sm"], anchor="w", pady=4,
        )
        self.title.pack(side="left", fill="x", expand=True)
        for w in (self.head, self.arrow, self.title):
            w.bind("<Button-1>", self.toggle)
        self.body = ttk.Frame(self, style="Card.TFrame")
        self.inner = ttk.Frame(self.body, style="Card.TFrame")
        self.inner.pack(fill="x", pady=(8, 0))

    def toggle(self, _event=None) -> None:
        self._open = not self._open
        if self._open:
            self.body.pack(fill="x")
            self.arrow.configure(text="-")
            self.title.configure(fg=theme.TEXT)
        else:
            self.body.pack_forget()
            self.arrow.configure(text="+")
            self.title.configure(fg=theme.MUTED)

    def set_open(self, opened: bool) -> None:
        if opened != self._open:
            self.toggle()


def hline(master: tk.Misc, color: str = theme.LINE) -> tk.Frame:
    line = tk.Frame(master, bg=color, height=1)
    line.pack(fill="x")
    return line


def swatch(color: str, size: tuple[int, int] = (14, 14)) -> tk.PhotoImage:
    """生成纯色小图标, 供 Treeview 行首显示方块颜色."""
    img = tk.PhotoImage(width=size[0], height=size[1])
    img.put(color, to=(0, 0, size[0], size[1]))
    return img


def readable_on(color: str) -> str:
    """按底色亮度选黑或白字, 保证色块上的文字可读."""
    h = color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
    return "#14181C" if lum > 148 else "#F6F9FC"


def btn(master: tk.Misc, text: str, command, style: str = "TButton", **kw) -> ttk.Button:
    return ttk.Button(master, text=text, command=command, style=style, **kw)
