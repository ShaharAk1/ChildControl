"""Shared look and feel: a blue-toned palette, a soft gradient+blob backdrop,
and genuinely rounded, drop-shadowed "glass" cards.

Tkinter has no `backdrop-filter: blur()`, no `box-shadow`, no `border-radius`
- so none of that can be done the CSS way. Instead:

  * `Backdrop` is a Canvas painted with a vertical gradient plus a few big,
    soft-edged "blob" glows (concentric rings fading into the background),
    faking the blurred-blob page background of the reference design.
  * `GlassCard` is a Frame whose *content* is embedded (via
    `canvas.create_window`) onto a small backing Canvas that draws a real
    rounded rectangle with a soft stacked-outline drop shadow behind it -
    a true rounded card, not just a bordered rectangle. It only works
    seamlessly against a *flat* background color (passed in as `bg`),
    since the canvas itself is opaque - that's why cards sit on the flat
    `theme.BG` content panel rather than directly on the animated backdrop.
  * `PillButton` is a small Canvas button with fully rounded (pill) ends,
    used for the handful of primary calls to action.

Real Windows compositor blur (`SetWindowCompositionAttribute` acrylic) was
tried and dropped: on this app's own target hardware it silently painted the
whole window a solid color instead of blending, which would have made the
console or the lock screen unusable. Rounded *window* corners
(`round_corners`, Windows 11 only) carry no such risk - a no-op instead of a
blank window wherever it's unsupported - so that one stays, wrapped in
try/except like every other Windows-only call here.
"""

from __future__ import annotations

import ctypes
import sys
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

# ---- palette ----------------------------------------------------------------
# A cooler, more saturated blue than the previous pass, echoing the glass
# look of https://shaharak1.github.io/Assignment-VirtualBussinessCard/ .

BG = "#eef2fb"          # flat surface cards and tabs sit on
BG_ALT = "#e2e9fb"
GLASS = "#f8faff"        # card fill - brighter than BG so cards read as "lifted"
GLASS_BORDER = "#dde6fb"
CARD = GLASS             # back-compat alias
CARD_BORDER = GLASS_BORDER

TEXT = "#20263a"
MUTED = "#5f6b86"

ACCENT = "#5b7fff"
ACCENT_HOVER = "#4368f0"
ACCENT_SOFT = "#e6ecff"
ACCENT_2 = "#3552d6"

SUCCESS = "#3fa579"
SUCCESS_SOFT = "#e3f5ec"
WARNING = "#e0982f"
WARNING_SOFT = "#fbf0dd"
DANGER = "#dd6b7f"
DANGER_SOFT = "#fbe9ed"

SHADOW = "#a9bbe0"       # base tone the card drop-shadow fades from

# backdrop gradient + blob glows (behind the flat content panel) - kept
# visibly deeper than the flat BG so the margin around the content panel
# reads as a deliberate colorful frame, not a rendering glitch.
BG_TOP = "#d7e3fd"
BG_BOTTOM = "#a9c2f7"
BLOB_1 = "#7fa0ff"
BLOB_2 = "#bcd0ff"
BLOB_3 = "#b7a6f5"

# dark "glass" tones, used on the full-screen lock and its popups
GLASS_BG = "#10141f"
GLASS_BG_BOTTOM = "#1a2036"
GLASS_PANEL = "#1c2438"
GLASS_BORDER_DARK = "#2c3a5c"
GLASS_TEXT = "#eef2fc"
GLASS_MUTED = "#8b96b8"
DBLOB_1 = "#3c4d99"
DBLOB_2 = "#2a3568"
DBLOB_3 = "#4a3a7a"

FONT = "Segoe UI"


def apply(root: tk.Misc) -> ttk.Style:
    """Configure a soft, flat ttk theme rooted at `root`. Returns the Style."""
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    style.configure(".", background=BG, foreground=TEXT, font=(FONT, 10))
    style.configure("TFrame", background=BG)
    style.configure("TLabel", background=BG, foreground=TEXT)
    style.configure("Muted.TLabel", background=BG, foreground=MUTED)
    style.configure("Heading.TLabel", background=BG, foreground=TEXT,
                     font=(FONT, 12, "bold"))

    style.configure("Card.TFrame", background=GLASS)
    style.configure("Card.TLabel", background=GLASS, foreground=TEXT)
    style.configure("CardMuted.TLabel", background=GLASS, foreground=MUTED)
    style.configure("CardTitle.TLabel", background=GLASS, foreground=MUTED,
                     font=(FONT, 8, "bold"))
    style.configure("Card.TCheckbutton", background=GLASS, foreground=TEXT)
    style.configure("Card.TRadiobutton", background=GLASS, foreground=TEXT)

    style.configure("TNotebook", background=BG, borderwidth=0, tabmargins=(6, 8, 6, 0))
    style.configure("TNotebook.Tab", background=BG_ALT, foreground=MUTED,
                     padding=(18, 10), borderwidth=0, font=(FONT, 10))
    style.map("TNotebook.Tab",
              background=[("selected", GLASS)],
              foreground=[("selected", TEXT)])
    style.layout("TNotebook.Tab", [
        ("Notebook.tab", {"sticky": "nswe", "children": [
            ("Notebook.padding", {"side": "top", "sticky": "nswe", "children": [
                ("Notebook.label", {"side": "top", "sticky": ""})]})]})])

    style.configure("TButton", background=ACCENT_SOFT, foreground=ACCENT_HOVER,
                     borderwidth=0, focusthickness=0, padding=(14, 8), font=(FONT, 10))
    style.map("TButton",
              background=[("pressed", ACCENT_SOFT), ("active", "#dbe4ff")],
              relief=[("pressed", "flat"), ("!pressed", "flat")])

    style.configure("Accent.TButton", background=ACCENT, foreground="#ffffff",
                     borderwidth=0, focusthickness=0, padding=(14, 8), font=(FONT, 10, "bold"))
    style.map("Accent.TButton", background=[("pressed", ACCENT_HOVER), ("active", ACCENT_HOVER)])

    style.configure("Danger.TButton", background=DANGER_SOFT, foreground=DANGER,
                     borderwidth=0, focusthickness=0, padding=(14, 8), font=(FONT, 10))
    style.map("Danger.TButton", background=[("active", "#f6d7dd")])

    style.configure("TEntry", fieldbackground=GLASS, background=GLASS,
                     bordercolor=GLASS_BORDER, lightcolor=GLASS_BORDER, darkcolor=GLASS_BORDER,
                     borderwidth=1, padding=7, foreground=TEXT)
    style.map("TEntry", bordercolor=[("focus", ACCENT)])

    style.configure("TCheckbutton", background=BG, foreground=TEXT)
    style.configure("TRadiobutton", background=BG, foreground=TEXT)

    style.configure("TLabelframe", background=BG, bordercolor=GLASS_BORDER, borderwidth=1)
    style.configure("TLabelframe.Label", background=BG, foreground=MUTED,
                     font=(FONT, 9, "bold"))

    style.configure("Treeview", background=GLASS, fieldbackground=GLASS,
                     foreground=TEXT, borderwidth=0, rowheight=27, font=(FONT, 10))
    style.configure("Treeview.Heading", background=BG_ALT, foreground=MUTED,
                     borderwidth=0, relief="flat", font=(FONT, 9, "bold"))
    style.map("Treeview.Heading", background=[("active", BG_ALT)])
    style.map("Treeview", background=[("selected", ACCENT_SOFT)],
              foreground=[("selected", TEXT)])
    style.layout("Treeview", [("Treeview.treearea", {"sticky": "nswe"})])

    style.configure("Vertical.TScrollbar", background=BG_ALT, troughcolor=BG,
                     bordercolor=BG, arrowcolor=MUTED, borderwidth=0, arrowsize=13)
    style.map("Vertical.TScrollbar", background=[("active", GLASS_BORDER)])

    style.configure("TSeparator", background=GLASS_BORDER)

    return style


# ---- color helpers ------------------------------------------------------------

def _hex_to_rgb(color: str) -> tuple[int, int, int]:
    color = color.lstrip("#")
    return int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16)


def _rgb_to_hex(rgb: tuple[float, float, float]) -> str:
    return "#{:02x}{:02x}{:02x}".format(*(max(0, min(255, round(c))) for c in rgb))


def lerp_color(c1: str, c2: str, t: float) -> str:
    """Blend two hex colors; t=0 -> c1, t=1 -> c2."""
    r1, g1, b1 = _hex_to_rgb(c1)
    r2, g2, b2 = _hex_to_rgb(c2)
    return _rgb_to_hex((r1 + (r2 - r1) * t, g1 + (g2 - g1) * t, b1 + (b2 - b1) * t))


def round_rect(canvas: tk.Canvas, x1: float, y1: float, x2: float, y2: float,
               radius: float = 8, **kwargs):
    """Draw (and return the item id for) a rounded rectangle on `canvas`."""
    radius = max(0, min(radius, (x2 - x1) / 2, (y2 - y1) / 2))
    points = [
        x1 + radius, y1, x2 - radius, y1, x2, y1, x2, y1 + radius,
        x2, y2 - radius, x2, y2, x2 - radius, y2, x1 + radius, y2,
        x1, y2, x1, y2 - radius, x1, y1 + radius, x1, y1,
    ]
    return canvas.create_polygon(points, smooth=True, **kwargs)


def draw_shadow(canvas: tk.Canvas, x1: float, y1: float, x2: float, y2: float,
                radius: float, fade_to: str, base: str = SHADOW,
                layers: int = 5, spread: float = 2.5, drop: float = 3.0,
                tags: str | tuple = ()) -> None:
    """Fake a soft drop shadow: `layers` rounded rects growing outward and
    downward, blending from `base` into `fade_to` (the real background color
    behind the shadow) so the edge dissolves instead of banding."""
    for i in range(layers, 0, -1):
        t = i / layers
        grow = spread * i
        color = lerp_color(base, fade_to, 0.15 + 0.75 * t)
        round_rect(canvas, x1 - grow, y1 - grow + drop, x2 + grow, y2 + grow + drop,
                   radius=radius + grow, fill=color, outline="", tags=tags)


def _blob(canvas: tk.Canvas, cx: float, cy: float, radius: float,
         color: str, fade_to: str, rings: int = 10, tags: str | tuple = ()) -> None:
    """A soft radial glow: concentric ovals blending `color` into `fade_to`,
    faking a gaussian-blurred blob with plain flat fills."""
    for i in range(rings, 0, -1):
        t = i / rings
        r = radius * t
        ring_color = lerp_color(color, fade_to, t ** 1.6)
        canvas.create_oval(cx - r, cy - r, cx + r, cy + r, fill=ring_color, outline="", tags=tags)


class Backdrop(tk.Canvas):
    """A full-bleed vertical gradient with a few soft blob glows, redrawn on
    resize (debounced so interactive resizing stays smooth)."""

    def __init__(self, parent: tk.Misc, top: str = BG_TOP, bottom: str = BG_BOTTOM,
                 blobs: tuple[str, str, str] = (BLOB_1, BLOB_2, BLOB_3)) -> None:
        super().__init__(parent, highlightthickness=0, bd=0, bg=top)
        self._top, self._bottom, self._blobs = top, bottom, blobs
        self._redraw_job: str | None = None
        self.bind("<Configure>", self._schedule_redraw)

    def _schedule_redraw(self, _event=None) -> None:
        if self._redraw_job is not None:
            self.after_cancel(self._redraw_job)
        self._redraw_job = self.after(16, self._redraw)

    def _redraw(self) -> None:
        self._redraw_job = None
        w, h = self.winfo_width(), self.winfo_height()
        if w < 2 or h < 2:
            return
        # Only ever remove our own "bg"-tagged items - a plain delete("all")
        # would also nuke embedded content windows (the notebook, a lock
        # panel) that other code has placed on this same canvas.
        self.delete("bg")
        steps = max(1, min(60, h // 4))
        for i in range(steps):
            t0, t1 = i / steps, (i + 1) / steps
            color = lerp_color(self._top, self._bottom, (t0 + t1) / 2)
            self.create_rectangle(0, t0 * h - 1, w, t1 * h + 1, fill=color, outline="", tags="bg")
        blob1, blob2, blob3 = self._blobs
        span = min(w, h)
        _blob(self, w * 0.05, h * 0.02, span * 0.42, blob1, self._top, tags="bg")
        _blob(self, w * 0.98, h * 0.95, span * 0.40, blob2, self._bottom, tags="bg")
        _blob(self, w * 0.80, h * 0.10, span * 0.26, blob3,
              lerp_color(self._top, self._bottom, 0.15), tags="bg")
        self.tag_lower("bg")

    def color_at(self, y: float, height: float) -> str:
        """The gradient's color at vertical position `y` of `height` - lets a
        widget placed on the backdrop pick a matching flat background."""
        t = 0 if height <= 0 else max(0.0, min(1.0, y / height))
        return lerp_color(self._top, self._bottom, t)


class _GlassCardFrame(tk.Frame):
    """The Frame returned by `card()`: pack/grid it like any Frame, then
    build content into `.body` (a plain tk.Frame with bg=GLASS)."""

    RADIUS = 16

    def __init__(self, parent: tk.Misc, title: str | None = None, bg: str = BG) -> None:
        self._bg = bg
        super().__init__(parent, bg=bg, highlightthickness=0)
        self._canvas = tk.Canvas(self, highlightthickness=0, bd=0, bg=bg)
        self._canvas.pack(fill="both", expand=True)
        self.body = tk.Frame(self._canvas, bg=GLASS)
        self._win = self._canvas.create_window(self.RADIUS, self.RADIUS,
                                               window=self.body, anchor="nw")
        if title:
            ttk.Label(self.body, text=title.upper(), style="CardTitle.TLabel").pack(
                anchor="w", padx=20, pady=(16, 4))
        self._last_size = (0, 0)
        self.body.bind("<Configure>", lambda _e: self._redraw())
        self.bind("<Configure>", lambda _e: self._redraw())

    def _redraw(self) -> None:
        self.update_idletasks()
        req_w = self.body.winfo_reqwidth() + self.RADIUS * 2
        req_h = self.body.winfo_reqheight() + self.RADIUS * 2
        alloc_w = self.winfo_width()
        w = max(alloc_w, req_w) if alloc_w > 1 else req_w
        h = req_h
        if (w, h) == self._last_size:
            return
        self._last_size = (w, h)
        self._canvas.configure(width=w, height=h)
        self._canvas.itemconfigure(self._win, width=w - self.RADIUS * 2)
        self._canvas.delete("shape")
        draw_shadow(self._canvas, self.RADIUS, self.RADIUS, w - self.RADIUS, h - self.RADIUS,
                    self.RADIUS, fade_to=self._bg, tags="shape")
        round_rect(self._canvas, 1, 1, w - 2, h - 2, radius=self.RADIUS,
                   fill=GLASS, outline=GLASS_BORDER, width=1, tags="shape")
        self._canvas.tag_raise(self._win)


def card(parent: tk.Misc, title: str | None = None, bg: str = BG) -> _GlassCardFrame:
    """A rounded, drop-shadowed "glass" card. Pack/grid the result, then
    parent new content to `<result>.body` (background GLASS) rather than to
    the result itself - the result is just the canvas wrapper that draws the
    rounded shape.

    `bg` must match the *flat* color of whatever this card sits directly on
    (the default `theme.BG` content panel) - the wrapper canvas is opaque,
    so a mismatch would show as square corners peeking out.
    """
    return _GlassCardFrame(parent, title=title, bg=bg)


class PillButton(tk.Canvas):
    """A small pill-shaped (fully rounded) button for primary actions.

    `bg` is the flat color of whatever this button sits on directly (GLASS
    inside a card, BG on the plain content panel) - like GlassCard, this
    widget is opaque, so pass the right one or the corners will show a seam.
    """

    def __init__(self, parent: tk.Misc, text: str, command=None,
                 bg: str = BG, fill: str = ACCENT, hover: str = ACCENT_HOVER,
                 fg: str = "#ffffff", font: tuple | None = None,
                 padx: int = 22, pady: int = 11) -> None:
        super().__init__(parent, highlightthickness=0, bd=0, bg=bg, cursor="hand2")
        self._command = command
        self._text = text
        self._fill = fill
        self._hover = hover
        self._fg = fg
        self._font = font or (FONT, 10, "bold")
        self._padx, self._pady = padx, pady
        self._render(hover=False)
        self.bind("<Enter>", lambda _e: self._render(hover=True))
        self.bind("<Leave>", lambda _e: self._render(hover=False))
        self.bind("<Button-1>", lambda _e: self._command and self._command())

    def _render(self, hover: bool) -> None:
        self.delete("all")
        font_obj = tkfont.Font(font=self._font)
        w = font_obj.measure(self._text) + self._padx * 2
        h = font_obj.metrics("linespace") + self._pady * 2
        self.configure(width=w, height=h)
        round_rect(self, 0, 0, w - 1, h - 1, radius=h / 2,
                   fill=self._hover if hover else self._fill, outline="")
        self.create_text(w / 2, h / 2, text=self._text, fill=self._fg, font=self._font)


# ---- Windows glass effects ---------------------------------------------------

def _hwnd(window: tk.Misc) -> int:
    window.update_idletasks()
    return ctypes.windll.user32.GetParent(window.winfo_id())


def round_corners(window: tk.Misc, radius: str = "round") -> None:
    """Windows 11 soft window corners; a no-op everywhere else."""
    if sys.platform != "win32":
        return
    try:
        DWMWA_WINDOW_CORNER_PREFERENCE = 33
        preference = {"round": 2, "small": 3, "square": 1}.get(radius, 2)
        value = ctypes.c_int(preference)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            _hwnd(window), DWMWA_WINDOW_CORNER_PREFERENCE,
            ctypes.byref(value), ctypes.sizeof(value))
    except Exception:
        pass


def dark_titlebar(window: tk.Misc, enabled: bool = True) -> None:
    """Match the Windows titlebar to a dark or light window body."""
    if sys.platform != "win32":
        return
    try:
        DWMWA_USE_IMMERSIVE_DARK_MODE = 20
        value = ctypes.c_int(1 if enabled else 0)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            _hwnd(window), DWMWA_USE_IMMERSIVE_DARK_MODE,
            ctypes.byref(value), ctypes.sizeof(value))
    except Exception:
        pass
