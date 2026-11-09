"""界面配色, 字体与 ttk 样式.

所有颜色与字号集中在本文件, 换肤只改这里, 页面代码不写死色值.
"""

from __future__ import annotations

import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

# ---- 基本色板: 浅色界面, 与白色卡片形成层次 ----
BG = "#F2F5F8"
PANEL = "#FFFFFF"
PANEL_ALT = "#FAFBFC"
PANEL_SOFT = "#F1F4F8"
LINE = "#DDE3EA"
TEXT = "#1D232A"
MUTED = "#6B7683"
FAINT = "#9AA5B1"
ACCENT = "#2F6FDE"
ACCENT_HOVER = "#2459B8"
ACCENT_SOFT = "#E8F0FD"
OK = "#1E8E4E"
WARN = "#B26A00"
ERR = "#C0392B"

# ---- 控制台配色: 深色底衬托日志层级 ----
CONSOLE_BG = "#191E24"
CONSOLE_FG = "#D5DDE6"
CONSOLE_DIM = "#7A8794"
CONSOLE_STEP = "#6FA8FF"
CONSOLE_OK = "#5FCB8A"
CONSOLE_ERR = "#FF8A80"

UI_PRIMARY = "Microsoft YaHei UI"
UI_FALLBACK = "Microsoft YaHei"
MONO_PRIMARY = "Consolas"
MONO_FALLBACK = "Courier New"

_FONTS: dict[str, tuple] = {}


def _family(root: tk.Misc, primary: str, fallback: str) -> str:
    try:
        families = set(tkfont.families(root))
    except Exception:
        return fallback
    if primary in families:
        return primary
    return fallback


def fonts() -> dict[str, tuple]:
    """返回界面字体表, 需在 apply 之后调用."""
    return _FONTS


def apply(root: tk.Misc) -> dict[str, tuple]:
    """设置 DPI 缩放, 字体表与全部 ttk 样式, 返回字体表."""
    global _FONTS

    try:
        dpi = float(root.winfo_fpixels("1i"))
    except Exception:
        dpi = 96.0
    root.tk.call("tk", "scaling", max(1.0, dpi / 72.0))

    ui = _family(root, UI_PRIMARY, UI_FALLBACK)
    mono = _family(root, MONO_PRIMARY, MONO_FALLBACK)
    _FONTS = {
        "ui": (ui, 10),
        "sm": (ui, 9),
        "xs": (ui, 8),
        "bold": (ui, 10, "bold"),
        "h2": (ui, 11, "bold"),
        "title": (ui, 15, "bold"),
        "sub": (ui, 9),
        "mono": (mono, 9),
        "mono_sm": (mono, 8),
    }

    root.option_add("*TCombobox*Listbox.background", PANEL)
    root.option_add("*TCombobox*Listbox.foreground", TEXT)
    root.option_add("*TCombobox*Listbox.selectBackground", ACCENT_SOFT)
    root.option_add("*TCombobox*Listbox.selectForeground", TEXT)
    root.option_add("*TCombobox*Listbox.font", _FONTS["ui"])
    root.option_add("*Font", _FONTS["ui"])

    st = ttk.Style(root)
    st.theme_use("clam")

    st.configure(".", background=BG, foreground=TEXT, font=_FONTS["ui"])
    st.configure("TFrame", background=BG)
    st.configure("Line.TFrame", background=LINE)
    st.configure("Card.TFrame", background=PANEL)
    st.configure("Soft.TFrame", background=PANEL_SOFT)
    st.configure("Bar.TFrame", background=PANEL)

    st.configure("TLabel", background=BG, foreground=TEXT)
    st.configure("Card.TLabel", background=PANEL, foreground=TEXT)
    st.configure("Muted.TLabel", background=BG, foreground=MUTED, font=_FONTS["sm"])
    st.configure("CardMuted.TLabel", background=PANEL, foreground=MUTED, font=_FONTS["sm"])
    st.configure("Faint.TLabel", background=PANEL, foreground=FAINT, font=_FONTS["xs"])
    st.configure("Title.TLabel", background=BG, foreground=TEXT, font=_FONTS["title"])
    st.configure("Sub.TLabel", background=BG, foreground=MUTED, font=_FONTS["sub"])
    st.configure("H2.TLabel", background=PANEL, foreground=TEXT, font=_FONTS["h2"])
    st.configure("Ok.TLabel", background=PANEL, foreground=OK, font=_FONTS["sm"])
    st.configure("Err.TLabel", background=PANEL, foreground=ERR, font=_FONTS["sm"])
    st.configure("Warn.TLabel", background=PANEL, foreground=WARN, font=_FONTS["sm"])

    st.configure("TNotebook", background=BG, borderwidth=0, tabmargins=(12, 8, 12, 0))
    st.configure(
        "TNotebook.Tab",
        padding=(20, 7),
        background="#E3E9F0",
        foreground=MUTED,
        borderwidth=0,
        font=_FONTS["ui"],
    )
    st.map(
        "TNotebook.Tab",
        background=[("selected", PANEL)],
        foreground=[("selected", ACCENT)],
        expand=[("selected", (0, 0, 0, 0))],
    )

    st.configure(
        "TButton",
        padding=(13, 6),
        background=PANEL_SOFT,
        foreground=TEXT,
        borderwidth=0,
        focusthickness=0,
        relief="flat",
    )
    st.map(
        "TButton",
        background=[("active", "#E2E8F0"), ("pressed", "#D6DEE8"), ("disabled", "#F1F3F5")],
        foreground=[("disabled", FAINT)],
    )
    st.configure(
        "Accent.TButton",
        padding=(16, 10),
        background=ACCENT,
        foreground="#FFFFFF",
        font=_FONTS["bold"],
        borderwidth=0,
    )
    st.map(
        "Accent.TButton",
        background=[("active", ACCENT_HOVER), ("pressed", "#1E4C9C"), ("disabled", "#B7C9E9")],
        foreground=[("disabled", "#F2F6FC")],
    )
    st.configure(
        "Ghost.TButton",
        padding=(10, 5),
        background=PANEL,
        foreground=ACCENT,
        borderwidth=0,
    )
    st.map(
        "Ghost.TButton",
        background=[("active", ACCENT_SOFT), ("disabled", PANEL)],
        foreground=[("disabled", FAINT)],
    )
    st.configure("Small.TButton", padding=(9, 4), font=_FONTS["sm"])
    st.configure(
        "Sel.TButton",
        padding=(9, 4),
        font=_FONTS["sm"],
        background=ACCENT_SOFT,
        foreground=ACCENT,
        borderwidth=0,
    )
    st.map("Sel.TButton", background=[("active", ACCENT_SOFT)])

    st.configure(
        "TEntry",
        fieldbackground=PANEL,
        bordercolor=LINE,
        lightcolor=LINE,
        darkcolor=LINE,
        foreground=TEXT,
        insertcolor=TEXT,
        padding=5,
    )
    st.map("TEntry", bordercolor=[("focus", ACCENT)], lightcolor=[("focus", ACCENT)])
    st.configure(
        "TCombobox",
        fieldbackground=PANEL,
        background=PANEL,
        arrowcolor=MUTED,
        bordercolor=LINE,
        lightcolor=LINE,
        darkcolor=LINE,
        foreground=TEXT,
        padding=5,
    )
    st.map(
        "TCombobox",
        fieldbackground=[("readonly", PANEL), ("disabled", PANEL_SOFT)],
        foreground=[("disabled", FAINT)],
        bordercolor=[("focus", ACCENT)],
    )
    st.configure(
        "TSpinbox",
        fieldbackground=PANEL,
        background=PANEL,
        arrowcolor=MUTED,
        bordercolor=LINE,
        foreground=TEXT,
        padding=4,
    )
    st.configure(
        "TCheckbutton",
        background=PANEL,
        foreground=TEXT,
        focuscolor=PANEL,
        indicatorcolor=PANEL,
    )
    st.map(
        "TCheckbutton",
        background=[("active", PANEL)],
        indicatorcolor=[("selected", ACCENT), ("pressed", ACCENT)],
    )
    st.configure(
        "TRadiobutton",
        background=PANEL,
        foreground=TEXT,
        focuscolor=PANEL,
    )
    st.map("TRadiobutton", background=[("active", PANEL)])
    st.configure(
        "TScale",
        background=PANEL,
        troughcolor="#E4EAF1",
        bordercolor=PANEL,
        lightcolor=ACCENT,
        darkcolor=ACCENT,
    )
    st.configure(
        "TProgressbar",
        background=ACCENT,
        troughcolor="#E4EAF1",
        bordercolor="#E4EAF1",
        lightcolor=ACCENT,
        darkcolor=ACCENT,
        thickness=6,
    )
    st.configure("TSeparator", background=LINE)
    st.configure("Vertical.TScrollbar", background="#CBD3DC", troughcolor=PANEL_SOFT,
                 bordercolor=PANEL_SOFT, arrowcolor=MUTED, width=10)
    st.map("Vertical.TScrollbar", background=[("active", "#B4BFCB")])

    st.configure(
        "Treeview",
        background=PANEL,
        fieldbackground=PANEL,
        foreground=TEXT,
        borderwidth=0,
        rowheight=26,
        font=_FONTS["ui"],
    )
    st.configure(
        "Treeview.Heading",
        background=PANEL_SOFT,
        foreground=MUTED,
        font=_FONTS["sm"],
        borderwidth=0,
        padding=(8, 6),
        relief="flat",
    )
    st.map(
        "Treeview",
        background=[("selected", ACCENT_SOFT)],
        foreground=[("selected", TEXT)],
    )
    st.map("Treeview.Heading", background=[("active", "#E7ECF3")])
    return _FONTS
