"""分页三: 查看仓库内容.

仓库是 `mc12.palette` 里的 1.12.2 方块表, 也是流水线唯一的取材来源.
这里按色块网格展示全部方块, 支持关键字与分类过滤, 选中后看详情与历史用量.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from mc12.palette import BLOCKS, Block

from . import library, theme
from .widgets import Card, btn, readable_on

CELL_W = 92
CELL_H = 96
GAP = 8
PAD = 12
SW = 50
TEXT_SELECTED = theme.ACCENT
BG_SELECTED = theme.ACCENT_SOFT


class LibraryPage(ttk.Frame):
    """方块仓库页."""

    def __init__(self, master: tk.Misc, app):
        super().__init__(master, style="TFrame", padding=(14, 10, 14, 8))
        self.app = app
        self.blocks: list[Block] = []
        self.selected: Block | None = None
        self._usage: dict[tuple[int, int], tuple[int, int]] = {}
        self._cols = 1
        self._job: str | None = None
        self._cells: list[tuple[float, float, Block]] = []
        self.columnconfigure(0, weight=1)
        self.columnconfigure(1, weight=0, minsize=272)
        self.rowconfigure(1, weight=1)
        self._build_head()
        self._build_grid()
        self._build_detail()
        self.refresh()

    # ------------------------------------------------------------------
    # 布局
    # ------------------------------------------------------------------
    def _build_head(self) -> None:
        card = Card(self, padding=(14, 12))
        card.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 10))
        card.body.columnconfigure(1, weight=1)
        ttk.Label(card.body, text="方块仓库", style="H2.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(
            card.body,
            text="1.12.2 全部可用方块, 流水线只会从可配色方块里取材",
            style="CardMuted.TLabel",
        ).grid(row=0, column=1, sticky="w", padx=(12, 0))

        bar = ttk.Frame(card.body, style="Card.TFrame")
        bar.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(10, 0))
        bar.columnconfigure(3, weight=1)

        self.var_text = tk.StringVar(value="")
        entry = ttk.Entry(bar, textvariable=self.var_text, width=24)
        entry.grid(row=0, column=0, sticky="w")
        self.var_text.trace_add("write", lambda *a: self._schedule())

        self.var_cat = tk.StringVar(value="全部")
        cmb = ttk.Combobox(
            bar, textvariable=self.var_cat, state="readonly", width=8,
            values=library.CATEGORIES,
        )
        cmb.grid(row=0, column=1, padx=(8, 0))
        cmb.bind("<<ComboboxSelected>>", lambda e: self._apply())

        self.var_paint = tk.BooleanVar(value=False)
        tk.Checkbutton(
            bar, text="只看可配色", variable=self.var_paint, command=self._apply,
            bg=theme.PANEL, fg=theme.TEXT, activebackground=theme.PANEL,
            activeforeground=theme.TEXT, selectcolor=theme.PANEL,
            highlightthickness=0, bd=0, font=theme.fonts()["sm"],
        ).grid(row=0, column=2, padx=(10, 0))

        self.lbl_stat = ttk.Label(bar, text="", style="CardMuted.TLabel")
        self.lbl_stat.grid(row=0, column=4, padx=(12, 0))

    def _build_grid(self) -> None:
        card = Card(self, padding=0)
        card.grid(row=1, column=0, sticky="nsew", padx=(0, 12))
        card.body.rowconfigure(0, weight=1)
        card.body.columnconfigure(0, weight=1)
        self.canvas = tk.Canvas(
            card.body, bg=theme.PANEL, highlightthickness=0, bd=0, yscrollincrement=24
        )
        self.canvas.grid(row=0, column=0, sticky="nsew", padx=1, pady=1)
        self.bar = ttk.Scrollbar(card.body, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.bar.set)
        self.bar.grid(row=0, column=1, sticky="ns")
        self.canvas.bind("<Configure>", self._on_resize)
        self.canvas.bind("<Button-1>", self._on_click)
        self.canvas.bind("<Enter>", lambda e: self.canvas.bind_all("<MouseWheel>", self._wheel))
        self.canvas.bind("<Leave>", lambda e: self.canvas.unbind_all("<MouseWheel>"))

    def _build_detail(self) -> None:
        card = Card(self, padding=14)
        card.grid(row=1, column=1, sticky="nsew")
        box = card.body
        box.columnconfigure(0, weight=1)
        fonts = theme.fonts()

        ttk.Label(box, text="方块详情", style="H2.TLabel").grid(row=0, column=0, sticky="w")

        top = tk.Frame(box, bg=theme.PANEL)
        top.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        self.swatch = tk.Canvas(
            top, width=90, height=90, bg=theme.PANEL_ALT,
            highlightthickness=1, highlightbackground=theme.LINE,
        )
        self.swatch.pack(side="left")
        info = tk.Frame(top, bg=theme.PANEL)
        info.pack(side="left", fill="both", expand=True, padx=(12, 0))
        self.lbl_dname = tk.Label(
            info, text="-", bg=theme.PANEL, fg=theme.TEXT, font=fonts["h2"],
            anchor="w", justify="left", wraplength=132,
        )
        self.lbl_dname.pack(fill="x")
        self.lbl_den = tk.Label(
            info, text="-", bg=theme.PANEL, fg=theme.MUTED, font=fonts["xs"],
            anchor="w", justify="left", wraplength=132,
        )
        self.lbl_den.pack(fill="x", pady=(4, 0))
        self.lbl_did = tk.Label(
            info, text="-", bg=theme.PANEL, fg=theme.ACCENT, font=fonts["mono"], anchor="w",
        )
        self.lbl_did.pack(fill="x", pady=(4, 0))

        tk.Frame(box, bg=theme.LINE, height=1).grid(row=2, column=0, sticky="ew", pady=12)

        grid = tk.Frame(box, bg=theme.PANEL)
        grid.grid(row=3, column=0, sticky="ew")
        self.detail_vars: dict[str, tk.StringVar] = {}
        for i, (key, label) in enumerate(
            (("hex", "颜色"), ("rgb", "RGB"), ("cat", "分类"), ("tex", "纹理"))
        ):
            r, c = divmod(i, 2)
            cell = tk.Frame(grid, bg=theme.PANEL)
            cell.grid(row=r, column=c, sticky="w", padx=(0, 10), pady=(0, 8))
            tk.Label(
                cell, text=label, bg=theme.PANEL, fg=theme.FAINT,
                font=fonts["xs"], anchor="w",
            ).pack(fill="x")
            var = tk.StringVar(value="-")
            tk.Label(
                cell, textvariable=var, bg=theme.PANEL, fg=theme.TEXT,
                font=fonts["sm"], anchor="w",
            ).pack(fill="x")
            self.detail_vars[key] = var

        tk.Label(
            box, text="在产物里的用量", bg=theme.PANEL, fg=theme.FAINT,
            font=fonts["xs"], anchor="w",
        ).grid(row=4, column=0, sticky="w", pady=(2, 0))
        self.var_use = tk.StringVar(value="-")
        tk.Label(
            box, textvariable=self.var_use, bg=theme.PANEL, fg=theme.TEXT,
            font=fonts["sm"], anchor="w",
        ).grid(row=5, column=0, sticky="w")

        self.var_note = tk.StringVar(value="")
        tk.Label(
            box, textvariable=self.var_note, bg=theme.PANEL, fg=theme.MUTED,
            font=fonts["xs"], anchor="w", justify="left", wraplength=236,
        ).grid(row=6, column=0, sticky="w", pady=(10, 0))

        btn(box, "去第一页生成", self.app.go_input, style="Ghost.TButton").grid(
            row=7, column=0, sticky="w", pady=(12, 0)
        )

    # ------------------------------------------------------------------
    # 数据
    # ------------------------------------------------------------------
    def refresh(self) -> None:
        self._usage = library.usage(self.app.out_dir)
        self._apply()

    def _apply(self) -> None:
        self.blocks = library.select(
            self.var_text.get(), self.var_cat.get(), self.var_paint.get()
        )
        stat = library.summary(BLOCKS)
        self.lbl_stat.configure(
            text=f"共 {stat['全部']} 种, 可配色 {stat['全部'] - stat['语义']} 种, 当前 {len(self.blocks)} 种"
        )
        keep = library.identity(self.selected) if self.selected is not None else None
        self.selected = None
        for b in self.blocks:
            if keep is not None and library.identity(b) == keep:
                self.selected = b
                break
        if self.selected is None and self.blocks:
            self.selected = self.blocks[0]
        self.canvas.yview_moveto(0.0)
        self._draw()

    def _schedule(self) -> None:
        if self._job is not None:
            self.after_cancel(self._job)
        self._job = self.after(140, self._apply)

    # ------------------------------------------------------------------
    # 绘制
    # ------------------------------------------------------------------
    def _on_resize(self, _event=None) -> None:
        width = max(CELL_W, self.canvas.winfo_width())
        cols = max(1, int((width - PAD * 2 + GAP) // (CELL_W + GAP)))
        if cols != self._cols:
            self._cols = cols
            self._draw()

    def _draw(self) -> None:
        self.canvas.delete("all")
        self._cells = []
        if not self.blocks:
            self.canvas.create_text(
                max(200, self.canvas.winfo_width() // 2), 120,
                text="没有匹配的方块", fill=theme.FAINT, font=theme.fonts()["ui"],
            )
            self.canvas.configure(scrollregion=(0, 0, 0, 0))
            self._fill_detail(None)
            return

        cols = max(1, self._cols)
        fonts = theme.fonts()
        for i, block in enumerate(self.blocks):
            row, col = divmod(i, cols)
            x = PAD + col * (CELL_W + GAP)
            y = PAD + row * (CELL_H + GAP)
            active = (self.selected is not None
                      and library.identity(block) == library.identity(self.selected))
            self.canvas.create_rectangle(
                x, y, x + CELL_W, y + CELL_H,
                fill=BG_SELECTED if active else theme.PANEL_ALT,
                outline=TEXT_SELECTED if active else theme.LINE,
                width=2 if active else 1,
            )
            sx = x + (CELL_W - SW) / 2
            sy = y + 10
            self.canvas.create_rectangle(
                sx, sy, sx + SW, sy + SW, fill=block.hex_color, outline=theme.LINE,
            )
            self.canvas.create_text(
                sx + SW - 4, sy + SW - 3, text=block.old_id, anchor="se",
                fill=readable_on(block.hex_color), font=fonts["xs"],
            )
            self.canvas.create_text(
                x + CELL_W / 2, sy + SW + 9, text=block.name_zh, anchor="n",
                fill=theme.TEXT, font=fonts["sm"], width=CELL_W - 8,
            )
            self._cells.append((x, y, block))

        rows = (len(self.blocks) + cols - 1) // cols
        height = PAD * 2 + rows * (CELL_H + GAP)
        self.canvas.configure(scrollregion=(0, 0, self.canvas.winfo_width(), height))
        self._fill_detail(self.selected)

    def _on_click(self, event) -> None:
        y0 = self.canvas.canvasy(event.y)
        for x, y, block in self._cells:
            if x <= event.x <= x + CELL_W and y <= y0 <= y + CELL_H:
                self.selected = block
                self._draw()
                return

    def _wheel(self, event) -> None:
        self.canvas.yview_scroll(int(-event.delta / 120), "units")

    # ------------------------------------------------------------------
    # 详情
    # ------------------------------------------------------------------
    def _fill_detail(self, block: Block | None) -> None:
        c = self.swatch
        c.delete("all")
        if block is None:
            c.create_text(45, 45, text="未选中", fill=theme.FAINT, font=theme.fonts()["xs"])
            self.lbl_dname.configure(text="-")
            self.lbl_den.configure(text="-")
            self.lbl_did.configure(text="-")
            for var in self.detail_vars.values():
                var.set("-")
            self.var_use.set("-")
            self.var_note.set("")
            return

        c.create_rectangle(0, 0, 90, 90, fill=block.hex_color, outline="")
        cat = library.category(block)
        total, models = self._usage.get(library.identity(block), (0, 0))
        self.lbl_dname.configure(text=block.name_zh)
        self.lbl_den.configure(text=block.name_en)
        self.lbl_did.configure(text=block.old_id)
        self.detail_vars["hex"].set(block.hex_color.upper())
        self.detail_vars["rgb"].set(", ".join(str(v) for v in block.rgb))
        self.detail_vars["cat"].set(cat)
        self.detail_vars["tex"].set(library.texture_text(block))
        if total:
            self.var_use.set(f"{total:,} 块, 出现在 {models} 个模型里")
        else:
            self.var_use.set("还没用过")
        if block.semantic:
            self.var_note.set("语义方块, " + library.ROLE_TEXT["语义"])
        else:
            self.var_note.set(library.ROLE_TEXT.get(cat, ""))

    def on_show(self) -> None:
        self.refresh()
