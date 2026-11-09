"""分页一: 输入工作图片.

一页完成三件事: 选图并预览, 配参数, 跑流水线并把日志实时显示出来.
左侧设置区分成两栏, 照片与参数并排, 免得在小屏上要来回滚动.
"""

from __future__ import annotations

import os
import queue
import re
import subprocess
import sys
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, ttk

from PIL import Image, ImageTk

import run as pipeline
from . import theme
from .task import EVT_DONE, EVT_LOG, EVT_STEP, PipelineTask, parse_step
from .widgets import Card, Collapse, ScrollFrame, Section, btn

IMAGE_TYPES = [
    ("图片", "*.jpg *.jpeg *.png *.bmp *.webp *.tif *.tiff"),
    ("全部文件", "*.*"),
]

MODES = [
    ("synth", "立面合成 (推荐)", "照片只取层数, 窗距与墙色, 立面按文法重排, 左右严格对称, 最像 Minecraft 建筑"),
    ("building", "按照片重建", "每个格子挑最接近的方块, 保留照片的细节, 也保留它的不完美"),
    ("hall", "室内大厅", "室内一点透视照, 取配色与水池尺度后按空间文法重建一个房间"),
    ("relief", "浮雕", "把明暗当成高度, 做一幅立体画"),
    ("flat", "平板", "只有一格厚的墙面, 适合做壁画, 标识或立面图"),
    ("terrain", "地貌", "山丘与地形, 不套建筑语法"),
]

PARAMS = {
    "synth": [
        ("depth", "建筑进深", "0", "0 表示按宽度自动"),
        ("floors", "楼层数", "", "空表示自动识别"),
    ],
    "building": [("depth", "墙体厚度", "3", "默认 3 格")],
    "flat": [("depth", "墙体厚度", "1", "单层墙面")],
    "relief": [("strength", "起伏强度", "4", "越大越立体")],
    "terrain": [],
    "hall": [
        ("length", "厅深", "0", "0 表示自动"),
        ("height", "净高", "0", "0 表示自动"),
        ("bays", "柱对数", "4", "每侧柱子数量"),
        ("cell", "藻井格边长", "5", "吊顶方格尺寸"),
        ("depth", "池深", "3", "水面下沉格数"),
    ],
}

CLEAN_LEVELS = [
    ("0 不规整", "0"),
    ("1 统一窗尺寸", "1"),
    ("2 窗阵重排 (推荐)", "2"),
]

BAD_CHARS = re.compile(r'[\\/:*?"<>|]')

PRESET_LABELS = {
    key: f"{key} - {info[0]} 格" for key, info in pipeline.PRESETS.items()
}

INT_PARAMS = ("depth", "floors", "length", "height", "bays", "cell")


class InputPage(ttk.Frame):
    """输入工作图片页."""

    def __init__(self, master: tk.Misc, app):
        super().__init__(master, style="TFrame", padding=(14, 10, 14, 8))
        self.app = app
        self.image_path: Path | None = None
        self.task: PipelineTask | None = None
        self._photo: ImageTk.PhotoImage | None = None
        self._thumb_src: Image.Image | None = None
        self._params: dict[str, dict[str, str]] = {}
        self._param_vars: dict[str, tk.StringVar] = {}
        self._name_edited = False
        self._last_name = ""
        self._buffer = ""
        self.columnconfigure(0, weight=0, minsize=612)
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)
        self._build_settings()
        self._build_log()
        self._build_action()
        self._on_mode_change()

    # ------------------------------------------------------------------
    # 布局
    # ------------------------------------------------------------------
    def _build_settings(self) -> None:
        wrap = ttk.Frame(self, style="TFrame")
        wrap.grid(row=0, column=0, sticky="nsew", padx=(0, 14))
        wrap.columnconfigure(0, weight=1)
        wrap.rowconfigure(0, weight=1)

        scroller = ScrollFrame(wrap, bg=theme.BG)
        scroller.grid(row=0, column=0, sticky="nsew")
        self.scroller = scroller
        body = scroller.inner
        body.columnconfigure(0, weight=0, minsize=280)
        body.columnconfigure(1, weight=1)

        self._card_photo(body).grid(row=0, column=0, sticky="new", padx=(0, 10))
        self._card_mode(body).grid(row=0, column=1, sticky="new")
        self._card_advanced(body).grid(row=1, column=1, sticky="ew", pady=(10, 0))

    def _build_action(self) -> None:
        bar = ttk.Frame(self, style="TFrame")
        bar.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        bar.columnconfigure(1, weight=1)
        ttk.Label(bar, text="模型名", style="Muted.TLabel").grid(
            row=0, column=0, sticky="w", padx=(2, 8)
        )
        self.var_name = tk.StringVar(value="")
        entry = ttk.Entry(bar, textvariable=self.var_name)
        entry.grid(row=0, column=1, sticky="ew")
        entry.bind("<KeyRelease>", lambda e: setattr(self, "_name_edited", True))
        self.btn_run = btn(bar, "开始生成", self._start, style="Accent.TButton")
        self.btn_run.grid(row=0, column=2, sticky="e", padx=(10, 0))

    def _card_photo(self, master: tk.Misc) -> Card:
        card = Card(master)
        Section(card.body, "1 工作图片", "jpg png bmp").pack(fill="x")
        self.canvas = tk.Canvas(
            card.body, width=236, height=112, bg="#F7F9FC", highlightthickness=1,
            highlightbackground=theme.LINE, cursor="hand2",
        )
        self.canvas.pack(fill="x", pady=(8, 8))
        self.canvas.bind("<Button-1>", lambda e: self._pick())
        self.canvas.bind("<Configure>", self._redraw_thumb)

        self.lbl_file = ttk.Label(
            card.body, text="尚未选择图片", style="Card.TLabel", wraplength=248,
            justify="left",
        )
        self.lbl_file.pack(fill="x")
        self.lbl_meta = ttk.Label(
            card.body, text="点击上方区域挑选一张照片", style="CardMuted.TLabel",
            wraplength=248, justify="left",
        )
        self.lbl_meta.pack(fill="x", pady=(2, 0))

        row = ttk.Frame(card.body, style="Card.TFrame")
        row.pack(fill="x", pady=(10, 0))
        btn(row, "选择图片", self._pick, style="Ghost.TButton").pack(side="left")
        btn(row, "清空", self._clear, style="Ghost.TButton").pack(side="left", padx=(4, 0))
        self._draw_placeholder()
        return card

    def _card_mode(self, master: tk.Misc) -> Card:
        card = Card(master)
        Section(card.body, "2 路线与尺寸", "决定模型长什么样").pack(fill="x")
        self.var_mode = tk.StringVar(value=MODES[0][1])
        self.cmb_mode = ttk.Combobox(
            card.body, textvariable=self.var_mode, state="readonly",
            values=[m[1] for m in MODES],
        )
        self.cmb_mode.pack(fill="x", pady=(8, 6))
        self.cmb_mode.bind("<<ComboboxSelected>>", lambda e: self._on_mode_change())
        self.lbl_mode = ttk.Label(
            card.body, text="", style="CardMuted.TLabel", wraplength=252, justify="left"
        )
        self.lbl_mode.pack(fill="x")

        tk.Frame(card.body, bg=theme.LINE, height=1).pack(fill="x", pady=10)

        prow = ttk.Frame(card.body, style="Card.TFrame")
        prow.pack(fill="x")
        ttk.Label(prow, text="尺寸预设", style="CardMuted.TLabel").pack(side="left")
        self.var_preset = tk.StringVar(value=PRESET_LABELS["m"])
        self.cmb_preset = ttk.Combobox(
            prow, textvariable=self.var_preset, state="readonly", width=11,
            values=[PRESET_LABELS[k] for k in ("xs", "s", "m", "l", "xl")],
        )
        self.cmb_preset.pack(side="right")
        self.cmb_preset.bind("<<ComboboxSelected>>", lambda e: self._on_preset())
        self.lbl_preset = ttk.Label(
            card.body, text="", style="CardMuted.TLabel", wraplength=252, justify="left"
        )
        self.lbl_preset.pack(fill="x", pady=(4, 8))

        self.param_frame = ttk.Frame(card.body, style="Card.TFrame")
        self.param_frame.pack(fill="x")
        self._on_preset()
        return card

    def _card_advanced(self, master: tk.Misc) -> Card:
        card = Card(master)
        Section(card.body, "3 高级选项", "通常不用动").pack(fill="x")
        col = Collapse(card.body, "展开更多参数")
        col.pack(fill="x", pady=(8, 0))
        self.collapse = col
        col.head.bind("<Button-1>", lambda e: self._toggle_advanced(), add="+")
        box = col.inner
        box.columnconfigure(0, weight=1)
        box.columnconfigure(1, weight=1)

        self.var_rect = tk.BooleanVar(value=True)
        self.var_flat = tk.BooleanVar(value=True)
        self.var_sky = tk.BooleanVar(value=True)
        self.var_glass = tk.BooleanVar(value=True)
        self.var_hollow = tk.BooleanVar(value=True)
        self.var_iso = tk.BooleanVar(value=True)
        self.var_viewer = tk.BooleanVar(value=True)
        self.var_sym = tk.BooleanVar(value=False)

        checks = [
            ("透视矫正", self.var_rect),
            ("光照归一化", self.var_flat),
            ("剥离天空", self.var_sky),
            ("窗户换玻璃", self.var_glass),
            ("空心壳体", self.var_hollow),
            ("等轴测预览", self.var_iso),
            ("HTML 查看器", self.var_viewer),
            ("强行左右镜像", self.var_sym),
        ]
        for i, (text, var) in enumerate(checks):
            tk.Checkbutton(
                box, text=text, variable=var, bg=theme.PANEL, fg=theme.TEXT,
                activebackground=theme.PANEL, activeforeground=theme.TEXT,
                selectcolor=theme.PANEL, highlightthickness=0, bd=0,
                anchor="w", font=theme.fonts()["ui"],
            ).grid(row=i // 2, column=i % 2, sticky="w", pady=2)

        self.var_sat = tk.DoubleVar(value=1.5)
        self.lbl_sat = ttk.Label(box, text="饱和度 1.50", style="CardMuted.TLabel")
        self.lbl_sat.grid(row=4, column=0, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Scale(
            box, from_=1.0, to=2.2, variable=self.var_sat,
            command=lambda v: self.lbl_sat.configure(text=f"饱和度 {float(v):.2f}"),
        ).grid(row=5, column=0, columnspan=2, sticky="ew")

        self.var_dedupe = tk.DoubleVar(value=10.0)
        self.lbl_dedupe = ttk.Label(box, text="颜色合并 10.0", style="CardMuted.TLabel")
        self.lbl_dedupe.grid(row=6, column=0, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Scale(
            box, from_=0.0, to=24.0, variable=self.var_dedupe,
            command=lambda v: self.lbl_dedupe.configure(text=f"颜色合并 {float(v):.1f}"),
        ).grid(row=7, column=0, columnspan=2, sticky="ew")

        self.var_clean = tk.StringVar(value=CLEAN_LEVELS[2][0])
        self.var_k = tk.StringVar(value="")
        grid2 = ttk.Frame(box, style="Card.TFrame")
        grid2.grid(row=8, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        grid2.columnconfigure(1, weight=1)
        ttk.Label(grid2, text="规整强度", style="CardMuted.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Combobox(
            grid2, textvariable=self.var_clean, state="readonly", width=15,
            values=[c[0] for c in CLEAN_LEVELS],
        ).grid(row=0, column=1, sticky="e")
        ttk.Label(grid2, text="方块种类上限", style="CardMuted.TLabel").grid(
            row=1, column=0, sticky="w", pady=(6, 0)
        )
        ttk.Entry(grid2, textvariable=self.var_k, width=8).grid(
            row=1, column=1, sticky="e", pady=(6, 0)
        )
        return card

    def _build_log(self) -> None:
        right = Card(self, padding=0)
        right.grid(row=0, column=1, sticky="nsew")
        right.body.rowconfigure(1, weight=1)
        right.body.columnconfigure(0, weight=1)

        head = ttk.Frame(right.body, style="Card.TFrame", padding=(14, 11, 14, 9))
        head.grid(row=0, column=0, sticky="ew")
        head.columnconfigure(1, weight=1)
        ttk.Label(head, text="运行日志", style="H2.TLabel").grid(row=0, column=0, sticky="w")
        self.lbl_state = ttk.Label(head, text="待开始", style="CardMuted.TLabel")
        self.lbl_state.grid(row=0, column=1, sticky="e", padx=(8, 10))
        btn(head, "清空", self._clear_log, style="Ghost.TButton").grid(row=0, column=2)
        btn(head, "打开输出目录", self._open_out, style="Ghost.TButton").grid(
            row=0, column=3, padx=(4, 0)
        )

        wrap = tk.Frame(right.body, bg=theme.CONSOLE_BG)
        wrap.grid(row=1, column=0, sticky="nsew")
        wrap.rowconfigure(0, weight=1)
        wrap.columnconfigure(0, weight=1)
        self.log = tk.Text(
            wrap, bg=theme.CONSOLE_BG, fg=theme.CONSOLE_FG, bd=0,
            padx=14, pady=12, wrap="none", font=theme.fonts()["mono"],
            insertbackground=theme.CONSOLE_FG, state="disabled", takefocus=0,
        )
        self.log.grid(row=0, column=0, sticky="nsew")
        bar = ttk.Scrollbar(wrap, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=bar.set)
        bar.grid(row=0, column=1, sticky="ns")
        self.log.tag_configure("step", foreground=theme.CONSOLE_STEP)
        self.log.tag_configure("ok", foreground=theme.CONSOLE_OK)
        self.log.tag_configure("err", foreground=theme.CONSOLE_ERR)
        self.log.tag_configure("dim", foreground=theme.CONSOLE_DIM)

        foot = ttk.Frame(right.body, style="Card.TFrame", padding=(14, 10, 14, 12))
        foot.grid(row=2, column=0, sticky="ew")
        foot.columnconfigure(0, weight=1)
        self.progress = ttk.Progressbar(foot, mode="determinate", maximum=100)
        self.progress.grid(row=0, column=0, sticky="ew")
        self.lbl_step = ttk.Label(foot, text="0 / 0", style="CardMuted.TLabel")
        self.lbl_step.grid(row=0, column=1, sticky="e", padx=(10, 0))

    def _toggle_advanced(self) -> None:
        self.after(80, self.scroller.scroll_to_end)

    # ------------------------------------------------------------------
    # 图片与缩略图
    # ------------------------------------------------------------------
    def _draw_placeholder(self) -> None:
        self.canvas.delete("all")
        w = self.canvas.winfo_width() or 236
        h = self.canvas.winfo_height() or 112
        self.canvas.create_text(
            w / 2, h / 2 - 9, text="点击选择照片",
            fill=theme.FAINT, font=theme.fonts()["sm"],
        )
        self.canvas.create_text(
            w / 2, h / 2 + 10, text="建议正面拍摄的建筑照",
            fill=theme.FAINT, font=theme.fonts()["xs"],
        )

    def _redraw_thumb(self, _event=None) -> None:
        if self._thumb_src is None:
            self._draw_placeholder()
            return
        w = max(60, self.canvas.winfo_width())
        h = max(60, self.canvas.winfo_height())
        img = self._thumb_src.copy()
        img.thumbnail((w - 12, h - 12), Image.LANCZOS)
        self._photo = ImageTk.PhotoImage(img)
        self.canvas.delete("all")
        self.canvas.create_image(w / 2, h / 2, image=self._photo)

    def _pick(self) -> None:
        path = filedialog.askopenfilename(title="选择工作图片", filetypes=IMAGE_TYPES)
        if path:
            self._load(Path(path))

    def _load(self, path: Path) -> None:
        try:
            img = Image.open(path)
            img.load()
        except Exception as exc:
            self.app.status(f"打不开这张图片: {exc}", "err")
            return
        self.image_path = path
        self._thumb_src = img.convert("RGB")
        self._redraw_thumb()
        w, h = img.size
        size_kb = path.stat().st_size / 1024
        self.lbl_file.configure(text=path.name)
        self.lbl_meta.configure(text=f"{w} x {h} 像素, {size_kb:.0f} KB")
        if not self._name_edited:
            self.var_name.set(BAD_CHARS.sub("_", path.stem))
        self.app.status(f"已载入 {path.name}", "ok")

    def _clear(self) -> None:
        self.image_path = None
        self._thumb_src = None
        self._photo = None
        self.lbl_file.configure(text="尚未选择图片")
        self.lbl_meta.configure(text="点击上方区域挑选一张照片")
        self._draw_placeholder()

    # ------------------------------------------------------------------
    # 参数联动
    # ------------------------------------------------------------------
    def mode_key(self) -> str:
        label = self.var_mode.get()
        for key, name, _desc in MODES:
            if name == label:
                return key
        return "synth"

    def preset_key(self) -> str:
        text = self.var_preset.get().strip()
        key = text.split(" ")[0] if text else "m"
        return key if key in pipeline.PRESETS else "m"

    def _on_mode_change(self) -> None:
        key = self.mode_key()
        for k, _name, desc in MODES:
            if k == key:
                self.lbl_mode.configure(text=desc)
        self._rebuild_params(key)

    def _rebuild_params(self, key: str) -> None:
        self._params[key] = {name: var.get() for name, var in self._param_vars.items()}
        saved = self._params.get(key, {})
        for child in self.param_frame.winfo_children():
            child.destroy()
        self._param_vars.clear()
        spec = PARAMS.get(key, [])
        if not spec:
            ttk.Label(
                self.param_frame, text="这条路线没有额外参数", style="CardMuted.TLabel"
            ).grid(row=0, column=0, columnspan=2, sticky="w")
            return
        for i, (name, label, default, note) in enumerate(spec):
            r, c = divmod(i, 2)
            cell = ttk.Frame(self.param_frame, style="Card.TFrame")
            cell.grid(row=r, column=c, sticky="ew", padx=(0, 8), pady=(0, 6))
            ttk.Label(cell, text=label, style="CardMuted.TLabel").pack(anchor="w")
            var = tk.StringVar(value=saved.get(name, default))
            ttk.Entry(cell, textvariable=var, width=10).pack(fill="x", pady=(2, 0))
            if note:
                ttk.Label(cell, text=note, style="Faint.TLabel").pack(anchor="w")
            self._param_vars[name] = var

    def _on_preset(self) -> None:
        key = self.preset_key()
        _width, kinds, desc = pipeline.PRESETS[key]
        self.lbl_preset.configure(text=f"{desc}, 种类上限 {kinds}")
        if hasattr(self, "param_frame"):
            self._rebuild_params(self.mode_key())

    # ------------------------------------------------------------------
    # 运行
    # ------------------------------------------------------------------
    def _collect_argv(self) -> list[str] | None:
        if self.image_path is None:
            self.app.status("请先选择一张工作图片", "err")
            return None
        name = BAD_CHARS.sub("_", self.var_name.get().strip() or self.image_path.stem)
        argv = [
            "-i", str(self.image_path),
            "-o", str(self.app.out_dir),
            "--name", name,
            "--mode", self.mode_key(),
            "--preset", self.preset_key(),
        ]
        for key, var in self._param_vars.items():
            text = var.get().strip()
            if not text:
                continue
            try:
                int(text) if key in INT_PARAMS else float(text)
            except ValueError:
                self.app.status(f"参数 {key} 需要填数字", "err")
                return None
            argv += [f"--{key}", text]

        k = self.var_k.get().strip()
        if k:
            if not k.isdigit():
                self.app.status("方块种类上限需要填整数", "err")
                return None
            argv += ["--k", k]
        argv += ["--sat", f"{self.var_sat.get():.2f}"]
        argv += ["--dedupe", f"{self.var_dedupe.get():.1f}"]
        for label, value in CLEAN_LEVELS:
            if label == self.var_clean.get():
                argv += ["--clean", value]
                break
        if not self.var_rect.get():
            argv.append("--no-rect")
        if not self.var_flat.get():
            argv.append("--no-flat")
        if not self.var_sky.get():
            argv.append("--no-sky")
        if not self.var_glass.get():
            argv.append("--no-glass")
        if not self.var_hollow.get():
            argv.append("--no-hollow")
        if not self.var_iso.get():
            argv.append("--no-iso")
        if not self.var_viewer.get():
            argv.append("--no-viewer")
        if self.var_sym.get():
            argv.append("--sym")
        return argv

    def _start(self) -> None:
        if self.task is not None and self.task.running:
            return
        argv = self._collect_argv()
        if argv is None:
            return
        self.app.out_dir.mkdir(parents=True, exist_ok=True)
        self._buffer = ""
        self._last_name = name_of(argv)
        self._clear_log()
        self._append(f">>> 开始生成  {Path(argv[1]).name}\n", "dim")
        self._set_running(True)
        self.task = PipelineTask(argv)
        self.task.start()
        self.after(60, self._pump)

    def _set_running(self, running: bool) -> None:
        if running:
            self.btn_run.state(["disabled"])
            self.btn_run.configure(text="正在生成 ...")
            self.lbl_state.configure(text="运行中", style="CardMuted.TLabel")
        else:
            self.btn_run.state(["!disabled"])
            self.btn_run.configure(text="开始生成")

    def _pump(self) -> None:
        task = self.task
        if task is None:
            return
        done = False
        while True:
            try:
                kind, payload = task.queue.get_nowait()
            except queue.Empty:
                break
            if kind == EVT_LOG:
                self._feed(str(payload))
            elif kind == EVT_STEP:
                self._set_step(int(payload[0]), int(payload[1]))
            elif kind == EVT_DONE:
                done = True
        if done:
            self._finish(int(task.code or 0))
            return
        self.after(60, self._pump)

    def _feed(self, text: str) -> None:
        self._buffer += text
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            self._write_line(line + "\n")
            step = parse_step(line)
            if step:
                self._set_step(step[0], step[1])

    def _write_line(self, line: str, tag: str | None = None) -> None:
        if tag is None:
            tag = self._tag_for(line)
        self.log.configure(state="normal")
        if tag:
            self.log.insert("end", line, tag)
        else:
            self.log.insert("end", line)
        self.log.see("end")
        self.log.configure(state="disabled")

    def _append(self, text: str, tag: str = "dim") -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text, tag)
        self.log.see("end")
        self.log.configure(state="disabled")

    def _tag_for(self, line: str) -> str:
        body = line.strip()
        if not body:
            return "dim"
        if parse_step(body) is not None:
            return "step"
        if "校验: 通过" in body:
            return "ok"
        if (
            "校验: 失败" in body
            or "Traceback" in body
            or body.startswith("错误")
            or "Error" in body
            or "找不到" in body
        ):
            return "err"
        return ""

    def _set_step(self, done: int, total: int) -> None:
        if total <= 0:
            return
        self.progress.configure(value=100.0 * done / total)
        self.lbl_step.configure(text=f"{done} / {total}")

    def _finish(self, code: int) -> None:
        self._set_running(False)
        if self._buffer.strip():
            self._write_line(self._buffer + "\n")
            self._buffer = ""
        if code == 0:
            self.progress.configure(value=100)
            self.lbl_state.configure(text="生成完成", style="Ok.TLabel")
            self.app.status("生成完成, 产物已写入 output 目录", "ok")
            self.app.refresh_all()
            self.after(500, lambda: self.app.go_output(self._last_name))
        else:
            self.lbl_state.configure(text=f"失败 (退出码 {code})", style="Err.TLabel")
            self.app.status("生成失败, 请看日志末尾的错误信息", "err")

    def _clear_log(self) -> None:
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")
        self.progress.configure(value=0)
        self.lbl_step.configure(text="0 / 0")

    def _open_out(self) -> None:
        self.app.out_dir.mkdir(parents=True, exist_ok=True)
        open_path(self.app.out_dir)

    def on_show(self) -> None:
        if self.image_path is not None and not self.image_path.exists():
            self.app.status("原来选中的图片已经不在原位置了", "warn")
            self._clear()


def name_of(argv: list[str]) -> str:
    """从命令行参数里取 --name 的值."""
    if "--name" in argv:
        index = argv.index("--name")
        if index + 1 < len(argv):
            return argv[index + 1]
    return ""


def open_path(path: Path) -> None:
    """用系统默认程序打开文件或目录."""
    try:
        if sys.platform == "win32":
            os.startfile(str(path))
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except Exception:
        pass
