"""分页二: 查看输出结果.

左侧列出 output 目录里所有模型, 右侧给出概要, 预览图, 方块清单与快捷打开入口.
预览与清单左右并排, 让预览图拿到更多高度.
"""

from __future__ import annotations

import webbrowser
import tkinter as tk
from pathlib import Path
from tkinter import ttk

from PIL import Image, ImageTk

from . import outputs, theme
from .outputs import ModelInfo
from .page_input import open_path
from .widgets import Card, btn, swatch

POLY = Image.Resampling.LANCZOS
IMAGE_SHORT = {"iso": "等轴测", "cut": "剖切", "preview": "对比"}


class OutputPage(ttk.Frame):
    """输出结果页."""

    def __init__(self, master: tk.Misc, app):
        super().__init__(master, style="TFrame", padding=(14, 10, 14, 8))
        self.app = app
        self.models: list[ModelInfo] = []
        self.current: ModelInfo | None = None
        self._images: list[tuple[str, Path]] = []
        self._image_path: Path | None = None
        self._photo: ImageTk.PhotoImage | None = None
        self._swatches: list[tk.PhotoImage] = []
        self._resize_job: str | None = None
        self._img_buttons: dict[str, ttk.Button] = {}
        self.columnconfigure(0, weight=0, minsize=296)
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)
        self._build()
        self.refresh()

    # ------------------------------------------------------------------
    # 布局
    # ------------------------------------------------------------------
    def _build(self) -> None:
        self._build_list()
        right = ttk.Frame(self, style="TFrame")
        right.grid(row=0, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)
        right.columnconfigure(1, weight=0, minsize=338)
        right.rowconfigure(1, weight=1)
        self._card_summary(right).grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 10))
        self._card_image(right).grid(row=1, column=0, sticky="nsew", padx=(0, 10))
        self._card_blocks(right).grid(row=1, column=1, sticky="nsew")

    def _build_list(self) -> None:
        card = Card(self, padding=0)
        card.grid(row=0, column=0, sticky="nsew", padx=(0, 14))
        card.body.rowconfigure(1, weight=1)
        card.body.columnconfigure(0, weight=1)

        head = ttk.Frame(card.body, style="Card.TFrame", padding=(14, 12, 14, 8))
        head.grid(row=0, column=0, sticky="ew")
        head.columnconfigure(0, weight=1)
        ttk.Label(head, text="已生成的模型", style="H2.TLabel").grid(row=0, column=0, sticky="w")
        btn(head, "刷新", self.refresh, style="Ghost.TButton").grid(row=0, column=1)

        wrap = ttk.Frame(card.body, style="Card.TFrame")
        wrap.grid(row=1, column=0, sticky="nsew", padx=8)
        wrap.rowconfigure(0, weight=1)
        wrap.columnconfigure(0, weight=1)
        self.tree = ttk.Treeview(
            wrap, columns=("mode", "size", "total"), show="tree headings",
            selectmode="browse",
        )
        self.tree.heading("#0", text="模型")
        self.tree.heading("mode", text="路线")
        self.tree.heading("size", text="尺寸")
        self.tree.heading("total", text="方块数")
        self.tree.column("#0", width=100, minwidth=80, stretch=True)
        self.tree.column("mode", width=72, anchor="center", stretch=False)
        self.tree.column("size", width=88, anchor="center", stretch=False)
        self.tree.column("total", width=62, anchor="e", stretch=False)
        self.tree.grid(row=0, column=0, sticky="nsew")
        bar = ttk.Scrollbar(wrap, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=bar.set)
        bar.grid(row=0, column=1, sticky="ns")
        self.tree.bind("<<TreeviewSelect>>", self._on_pick)

        foot = ttk.Frame(card.body, style="Card.TFrame", padding=(12, 10, 12, 12))
        foot.grid(row=2, column=0, sticky="ew")
        foot.columnconfigure(0, weight=1)
        btn(foot, "打开输出目录", self._open_dir).grid(row=0, column=0, sticky="ew")
        self.lbl_dir = ttk.Label(foot, text="", style="Faint.TLabel", wraplength=250)
        self.lbl_dir.grid(row=1, column=0, sticky="w", pady=(6, 0))

    def _card_summary(self, master: tk.Misc) -> Card:
        card = Card(master)
        top = ttk.Frame(card.body, style="Card.TFrame")
        top.pack(fill="x")
        self.lbl_name = ttk.Label(top, text="没有产物", style="H2.TLabel")
        self.lbl_name.pack(side="left")
        self.chip = tk.Label(
            top, text="", bg=theme.ACCENT_SOFT, fg=theme.ACCENT,
            font=theme.fonts()["xs"], padx=8, pady=2,
        )

        stats = ttk.Frame(card.body, style="Card.TFrame")
        stats.pack(fill="x", pady=(10, 0))
        self.stat_vars: dict[str, tk.StringVar] = {}
        titles = (
            ("size", "尺寸 X x Y x Z"), ("total", "总方块数"),
            ("kinds", "方块种类"), ("source", "来源照片"), ("elapsed", "产物文件"),
        )
        for i, (key, title) in enumerate(titles):
            cell = ttk.Frame(stats, style="Card.TFrame")
            cell.grid(row=0, column=i, sticky="w", padx=(0, 24))
            ttk.Label(cell, text=title, style="Faint.TLabel").pack(anchor="w")
            var = tk.StringVar(value="-")
            ttk.Label(cell, textvariable=var, style="Card.TLabel").pack(anchor="w", pady=(2, 0))
            self.stat_vars[key] = var
        return card

    def _card_image(self, master: tk.Misc) -> Card:
        card = Card(master, padding=0)
        card.body.rowconfigure(1, weight=1)
        card.body.columnconfigure(0, weight=1)
        head = ttk.Frame(card.body, style="Card.TFrame", padding=(14, 11, 14, 8))
        head.grid(row=0, column=0, sticky="ew")
        head.columnconfigure(0, weight=1)
        ttk.Label(head, text="预览", style="H2.TLabel").grid(row=0, column=0, sticky="w")
        btn(head, "在浏览器打开", self._open_viewer, style="Ghost.TButton").grid(row=0, column=1)

        self.canvas = tk.Canvas(
            card.body, bg=theme.PANEL_ALT, highlightthickness=0, bd=0, height=200
        )
        self.canvas.grid(row=1, column=0, sticky="nsew", padx=1)
        self.canvas.bind("<Configure>", self._on_resize)

        self.img_bar = ttk.Frame(card.body, style="Card.TFrame", padding=(12, 8, 12, 10))
        self.img_bar.grid(row=2, column=0, sticky="ew")
        return card

    def _card_blocks(self, master: tk.Misc) -> Card:
        card = Card(master, padding=0)
        card.body.rowconfigure(1, weight=1)
        card.body.columnconfigure(0, weight=1)
        head = ttk.Frame(card.body, style="Card.TFrame", padding=(14, 11, 14, 8))
        head.grid(row=0, column=0, sticky="ew")
        head.columnconfigure(0, weight=1)
        ttk.Label(head, text="方块清单", style="H2.TLabel").grid(row=0, column=0, sticky="w")

        wrap = ttk.Frame(card.body, style="Card.TFrame")
        wrap.grid(row=1, column=0, sticky="nsew", padx=8)
        wrap.rowconfigure(0, weight=1)
        wrap.columnconfigure(0, weight=1)
        self.blocks_tree = ttk.Treeview(
            wrap, columns=("id", "count"), show="tree headings", selectmode="none",
        )
        self.blocks_tree.heading("#0", text="方块")
        self.blocks_tree.heading("id", text="id:meta")
        self.blocks_tree.heading("count", text="数量")
        self.blocks_tree.column("#0", width=120, minwidth=90, stretch=True)
        self.blocks_tree.column("id", width=70, anchor="center", stretch=False)
        self.blocks_tree.column("count", width=62, anchor="e", stretch=False)
        self.blocks_tree.grid(row=0, column=0, sticky="nsew")
        bar = ttk.Scrollbar(wrap, orient="vertical", command=self.blocks_tree.yview)
        self.blocks_tree.configure(yscrollcommand=bar.set)
        bar.grid(row=0, column=1, sticky="ns")

        foot = ttk.Frame(card.body, style="Card.TFrame", padding=(12, 8, 12, 12))
        foot.grid(row=2, column=0, sticky="ew")
        foot.columnconfigure(0, weight=1)
        foot.columnconfigure(1, weight=1)
        btn(foot, "方块数量", self._open_blocks, style="Small.TButton").grid(
            row=0, column=0, sticky="ew", padx=(0, 4)
        )
        btn(foot, "分层平面图", self._open_layers, style="Small.TButton").grid(
            row=0, column=1, sticky="ew", padx=(4, 0)
        )
        return card

    # ------------------------------------------------------------------
    # 数据
    # ------------------------------------------------------------------
    def refresh(self) -> None:
        keep = self.current.name if self.current else None
        self.models = outputs.scan(self.app.out_dir)
        self.lbl_dir.configure(text=str(self.app.out_dir))
        self.tree.delete(*self.tree.get_children())
        for m in self.models:
            self.tree.insert(
                "", "end", iid=m.name, text=f" {m.name}",
                values=(m.mode_text, m.size_text, f"{m.total:,}"),
            )
        if not self.models:
            self.current = None
            self._show_empty()
            return
        names = {m.name for m in self.models}
        target = keep if keep in names else self.models[0].name
        self.tree.selection_set(target)
        self.tree.focus(target)

    def select(self, name: str) -> None:
        for model in self.models:
            if model.name == name:
                self.tree.selection_set(name)
                self.tree.focus(name)
                self.tree.see(name)
                self._show(model)
                return

    def _on_pick(self, _event=None) -> None:
        sel = self.tree.selection()
        if not sel:
            return
        for m in self.models:
            if m.name == sel[0]:
                self._show(m)
                return

    def _show_empty(self) -> None:
        self.lbl_name.configure(text="没有产物")
        self.chip.pack_forget()
        for var in self.stat_vars.values():
            var.set("-")
        self._images = []
        self._image_path = None
        self._photo = None
        self.canvas.delete("all")
        self._render_image()
        for child in self.img_bar.winfo_children():
            child.destroy()
        self.blocks_tree.delete(*self.blocks_tree.get_children())

    def _show(self, model: ModelInfo) -> None:
        self.current = model
        self.lbl_name.configure(text=model.name)
        self.chip.configure(text=model.mode_text)
        self.chip.pack(side="left", padx=(10, 0))
        self.stat_vars["size"].set(model.size_text)
        self.stat_vars["total"].set(f"{model.total:,}")
        self.stat_vars["kinds"].set(str(model.kinds))
        self.stat_vars["source"].set(model.source_name)
        self.stat_vars["elapsed"].set(str(len(model.files)))

        self._images = outputs.images_of(model)
        for child in self.img_bar.winfo_children():
            child.destroy()
        self._img_buttons = {}
        if not self._images:
            ttk.Label(
                self.img_bar, text="这个模型没有生成预览图", style="CardMuted.TLabel"
            ).pack(side="left")
        for kind, path in self._images:
            b = btn(
                self.img_bar, IMAGE_SHORT.get(kind, kind),
                lambda p=path: self._use_image(p), style="Small.TButton",
            )
            b.pack(side="left", padx=(0, 6))
            self._img_buttons[str(path)] = b
        self._use_image(self._images[0][1] if self._images else None)

        self.blocks_tree.delete(*self.blocks_tree.get_children())
        self._swatches = []
        for row in model.blocks:
            img = swatch(str(row.get("hex", "#888888")))
            self._swatches.append(img)
            self.blocks_tree.insert(
                "", "end", text=f" {row.get('name_zh', '')}", image=img,
                values=(
                    f"{row.get('id')}:{row.get('meta', 0)}",
                    f"{int(row.get('count', 0)):,}",
                ),
            )

    def _use_image(self, path: Path | None) -> None:
        self._image_path = path
        for key, button in self._img_buttons.items():
            button.configure(style="Sel.TButton" if path and key == str(path) else "Small.TButton")
        self._render_image()

    # ------------------------------------------------------------------
    # 渲染
    # ------------------------------------------------------------------
    def _on_resize(self, _event=None) -> None:
        if self._resize_job is not None:
            self.after_cancel(self._resize_job)
        self._resize_job = self.after(120, self._render_image)

    def _render_image(self) -> None:
        self._resize_job = None
        self.canvas.delete("all")
        cw = max(40, self.canvas.winfo_width())
        ch = max(40, self.canvas.winfo_height())
        if self.current is None:
            self.canvas.create_text(
                cw / 2, ch / 2, justify="center", fill=theme.FAINT,
                font=theme.fonts()["ui"],
                text="还没有任何模型\n去第一页选张照片生成一个",
            )
            return
        if self._image_path is None or not self._image_path.exists():
            self.canvas.create_text(
                cw / 2, ch / 2, text="这个模型没有可显示的图片",
                fill=theme.FAINT, font=theme.fonts()["sm"],
            )
            return
        try:
            img = Image.open(self._image_path)
            img.load()
        except Exception:
            self.canvas.create_text(
                cw / 2, ch / 2, text="预览图读取失败",
                fill=theme.ERR, font=theme.fonts()["sm"],
            )
            return
        img.thumbnail((cw - 24, ch - 24), POLY)
        self._photo = ImageTk.PhotoImage(img)
        self.canvas.create_image(cw / 2, ch / 2, image=self._photo)

    # ------------------------------------------------------------------
    # 动作
    # ------------------------------------------------------------------
    def _open_dir(self) -> None:
        self.app.out_dir.mkdir(parents=True, exist_ok=True)
        open_path(self.app.out_dir)

    def _open_viewer(self) -> None:
        if self.current and "view" in self.current.files:
            webbrowser.open(self.current.files["view"].as_uri())
            return
        self.app.status("这个模型没有生成 HTML 查看器", "warn")

    def _open_blocks(self) -> None:
        if self.current and "blocks" in self.current.files:
            open_path(self.current.files["blocks"])
            return
        self.app.status("找不到方块数量清单", "warn")

    def _open_layers(self) -> None:
        if self.current and "layers" in self.current.files:
            open_path(self.current.files["layers"])
            return
        self.app.status("找不到分层平面图", "warn")

    def on_show(self) -> None:
        self.refresh()
