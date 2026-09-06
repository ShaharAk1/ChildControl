"""The parent's admin console: password, weekly schedule, block lists, install.

Everything here writes to the same config.json the agent reads, so changes
take effect on the agent's next pass (a few seconds) without a restart.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tkinter as tk
from datetime import datetime
from tkinter import messagebox, simpledialog, ttk

from . import activity, browser_policy, config, install, steamlib, theme
from . import schedule as sched
from .util import (
    DATA_DIR,
    STATUS_PATH,
    is_admin,
    read_json,
    relaunch_as_admin,
    setup_logging,
)

log = setup_logging("console")

CELL_W = 17
CELL_H = 28
CELL_RADIUS = 4
LABEL_W = 92
HEADER_H = 22
STALE_SECONDS = 45


class PasswordGate:
    """First run sets the parent password; later runs check it."""

    @staticmethod
    def unlock(root: tk.Tk, cfg: dict) -> bool:
        if not cfg.get("password"):
            first = simpledialog.askstring(
                "Set parent password",
                "No parent password is set yet.\nChoose one (you will need it to change settings):",
                show="*", parent=root)
            if not first:
                return False
            again = simpledialog.askstring("Set parent password", "Repeat the password:",
                                           show="*", parent=root)
            if first != again:
                messagebox.showerror("Set parent password", "The passwords did not match.",
                                     parent=root)
                return False
            cfg["password"] = config.hash_password(first)
            config.save(cfg)
            return True

        for _attempt in range(3):
            entered = simpledialog.askstring("ChildControl", "Parent password:",
                                             show="*", parent=root)
            if entered is None:
                return False
            if config.check_password(cfg["password"], entered):
                return True
            messagebox.showerror("ChildControl", "Wrong password.", parent=root)
        return False


class ScheduleGrid(ttk.Frame):
    """A paintable week: 7 rows of 48 half-hour cells."""

    def __init__(self, master, week: list[str]) -> None:
        super().__init__(master)
        self.week = sched.normalize(week)
        self.brush = tk.StringVar(value=sched.STUDY)
        self.cells: list[list[int]] = []

        self._build_toolbar()
        width = LABEL_W + sched.SLOTS_PER_DAY * CELL_W + 2
        height = HEADER_H + len(sched.DAYS) * CELL_H + 2
        card = theme.card(self)
        card.pack(fill="x", padx=8, pady=(4, 8))
        self.canvas = tk.Canvas(card.body, width=width, height=height, highlightthickness=0,
                                background=theme.GLASS)
        self.canvas.pack(padx=16, pady=16)
        self._draw()
        self.canvas.bind("<Button-1>", self._on_paint)
        self.canvas.bind("<B1-Motion>", self._on_paint)

    def _build_toolbar(self) -> None:
        bar = ttk.Frame(self)
        bar.pack(fill="x", padx=8, pady=(8, 0))
        ttk.Label(bar, text="Paint with", style="Muted.TLabel").pack(side="left")
        for state in sched.STATES:
            ttk.Radiobutton(bar, text=sched.STATE_NAMES[state], value=state,
                            variable=self.brush).pack(side="left", padx=(10, 0))
        ttk.Button(bar, text="Copy Monday to Tue-Fri",
                   command=self.copy_monday_to_weekdays).pack(side="right")
        ttk.Button(bar, text="Fill whole week with brush",
                   command=self.fill_week).pack(side="right", padx=6)

        legend = ttk.Frame(self)
        legend.pack(fill="x", padx=8, pady=(8, 0))
        for state in sched.STATES:
            dot = tk.Canvas(legend, width=14, height=14, highlightthickness=0,
                            background=theme.BG)
            theme.round_rect(dot, 1, 1, 13, 13, radius=4,
                             fill=sched.STATE_COLORS[state], outline="")
            dot.pack(side="left", padx=(0, 6))
            ttk.Label(legend, text=sched.STATE_HELP[state], style="Muted.TLabel"
                      ).pack(side="left", padx=(0, 18))

    def _draw(self) -> None:
        self.canvas.delete("all")
        self.cells = []
        for slot in range(0, sched.SLOTS_PER_DAY, 4):
            x = LABEL_W + slot * CELL_W
            self.canvas.create_text(x + 2, HEADER_H / 2, text=sched.slot_label(slot),
                                    anchor="w", font=(theme.FONT, 8), fill=theme.MUTED)
        for day, name in enumerate(sched.DAYS):
            y = HEADER_H + day * CELL_H
            self.canvas.create_text(LABEL_W - 10, y + CELL_H / 2, text=name, anchor="e",
                                    font=(theme.FONT, 9), fill=theme.TEXT)
            row = []
            for slot in range(sched.SLOTS_PER_DAY):
                x = LABEL_W + slot * CELL_W
                rect = theme.round_rect(
                    self.canvas, x + 1, y + 1, x + CELL_W - 1, y + CELL_H - 1,
                    radius=CELL_RADIUS, fill=sched.STATE_COLORS[self.week[day][slot]],
                    outline=theme.CARD, width=2)
                row.append(rect)
            self.cells.append(row)

    def _cell_at(self, x: float, y: float) -> tuple[int, int] | None:
        day = int((y - HEADER_H) // CELL_H)
        slot = int((x - LABEL_W) // CELL_W)
        if 0 <= day < len(sched.DAYS) and 0 <= slot < sched.SLOTS_PER_DAY:
            return day, slot
        return None

    def _on_paint(self, event) -> None:
        cell = self._cell_at(event.x, event.y)
        if cell is None:
            return
        day, slot = cell
        state = self.brush.get()
        if self.week[day][slot] == state:
            return
        row = list(self.week[day])
        row[slot] = state
        self.week[day] = "".join(row)
        self.canvas.itemconfigure(self.cells[day][slot], fill=sched.STATE_COLORS[state])

    def copy_monday_to_weekdays(self) -> None:
        for day in range(1, 5):
            self.week[day] = self.week[0]
        self._draw()

    def fill_week(self) -> None:
        self.week = [self.brush.get() * sched.SLOTS_PER_DAY for _ in sched.DAYS]
        self._draw()

    def load(self, week: list[str]) -> None:
        self.week = sched.normalize(week)
        self._draw()


class ListEditor(ttk.Frame):
    """Reusable add/remove list used for blocked apps, folders and websites."""

    def __init__(self, master, items: list[str], placeholder: str, height: int = 12) -> None:
        super().__init__(master)
        self.entry_var = tk.StringVar()

        card = theme.card(self)
        card.pack(fill="both", expand=True)
        body = card.body
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)

        self.listbox = tk.Listbox(body, height=height, activestyle="none",
                                  font=("Consolas", 10), selectmode="extended",
                                  bg=theme.GLASS, fg=theme.TEXT, bd=0, highlightthickness=0,
                                  selectbackground=theme.ACCENT_SOFT, selectforeground=theme.TEXT)
        scroll = ttk.Scrollbar(body, orient="vertical", command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=scroll.set)
        self.listbox.grid(row=0, column=0, sticky="nsew", padx=(16, 0), pady=(12, 0))
        scroll.grid(row=0, column=1, sticky="ns", padx=(0, 8), pady=(12, 0))

        row = ttk.Frame(body, style="Card.TFrame")
        row.grid(row=1, column=0, columnspan=2, sticky="ew", padx=16, pady=(10, 4))
        entry = ttk.Entry(row, textvariable=self.entry_var)
        entry.pack(side="left", fill="x", expand=True)
        entry.bind("<Return>", lambda _event: self.add())
        ttk.Button(row, text="Add", style="Accent.TButton",
                   command=self.add).pack(side="left", padx=(6, 0))
        ttk.Button(row, text="Remove selected", command=self.remove).pack(side="left", padx=(6, 0))
        ttk.Label(body, text=placeholder, style="CardMuted.TLabel").grid(
            row=2, column=0, columnspan=2, sticky="w", padx=16, pady=(2, 14))

        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self.load(items)

    def load(self, items: list[str]) -> None:
        self.listbox.delete(0, "end")
        for item in items:
            self.listbox.insert("end", item)

    def add(self) -> None:
        value = self.entry_var.get().strip()
        if not value:
            return
        if value.lower() not in {i.lower() for i in self.items()}:
            self.listbox.insert("end", value)
        self.entry_var.set("")

    def remove(self) -> None:
        for index in reversed(self.listbox.curselection()):
            self.listbox.delete(index)

    def items(self) -> list[str]:
        return list(self.listbox.get(0, "end"))


class HoverActionList(ttk.Frame):
    """A scrollable (Type, Name, Time) list where hovering a row pops up its
    name, timestamp and one action button - "Block" on the visited list,
    "Unblock" on the blocked-attempts list."""

    _HIDE_DELAY_MS = 250

    def __init__(self, master, title: str, action_text: str, action_bg: str, on_action) -> None:
        super().__init__(master)
        self.on_action = on_action
        self.action_text = action_text
        self.action_bg = action_bg
        self._entries: dict[str, dict] = {}
        self._popup: tk.Toplevel | None = None
        self._popup_row: str | None = None
        self._hide_job: str | None = None
        self._hover_row: str | None = None

        ttk.Label(self, text=title, font=(theme.FONT, 10, "bold")).pack(anchor="w")
        self.subtitle = ttk.Label(self, text="", style="Muted.TLabel")
        self.subtitle.pack(anchor="w", pady=(0, 6))

        card = theme.card(self)
        card.pack(fill="both", expand=True)
        container = ttk.Frame(card.body, style="Card.TFrame")
        container.pack(fill="both", expand=True, padx=12, pady=12)
        self.tree = ttk.Treeview(container, columns=("type", "name", "time"),
                                 show="headings", selectmode="none", height=8)
        for col, text, width in (("type", "Type", 45), ("name", "Name", 220), ("time", "Time", 90)):
            self.tree.heading(col, text=text)
            self.tree.column(col, width=width, anchor="w")
        scroll = ttk.Scrollbar(container, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="left", fill="y")
        self.tree.tag_configure("hover", background=theme.BG_ALT)

        self.tree.bind("<Motion>", self._on_motion)
        self.tree.bind("<Leave>", self._on_leave)

    def load(self, entries: list[dict], subtitle: str) -> None:
        """`entries` newest-first: [{kind, name, at}, ...]."""
        self._hide_popup()
        self._hover_row = None
        self.subtitle.configure(text=subtitle)
        self.tree.delete(*self.tree.get_children())
        self._entries.clear()
        for entry in entries:
            when = datetime.fromisoformat(entry["at"]).strftime("%a %H:%M")
            kind_label = "App" if entry["kind"] == "app" else "Site"
            row = self.tree.insert("", "end", values=(kind_label, entry["name"], when))
            self._entries[row] = entry

    def _on_motion(self, event) -> None:
        row = self.tree.identify_row(event.y)
        self._set_hover_row(row)
        if row == self._popup_row:
            return
        self._cancel_hide()
        if row:
            self._show_popup(row)
        else:
            self._schedule_hide()

    def _on_leave(self, _event=None) -> None:
        self._set_hover_row(None)
        self._schedule_hide()

    def _set_hover_row(self, row: str | None) -> None:
        """Highlight the hovered row and switch to a pointer cursor, so it's
        obvious which line a click would act on."""
        if row == self._hover_row:
            return
        if self._hover_row is not None and self.tree.exists(self._hover_row):
            self.tree.item(self._hover_row, tags=())
        if row:
            self.tree.item(row, tags=("hover",))
        self.tree.configure(cursor="hand2" if row else "")
        self._hover_row = row

    def _show_popup(self, row: str) -> None:
        self._hide_popup()
        entry = self._entries.get(row)
        bbox = self.tree.bbox(row)
        if entry is None or not bbox:
            return
        self._popup_row = row

        win = tk.Toplevel(self)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.configure(bg=theme.GLASS_PANEL)
        frame = tk.Frame(win, bg=theme.GLASS_PANEL, padx=14, pady=10,
                         highlightbackground=theme.GLASS_BORDER_DARK, highlightthickness=1)
        frame.pack()
        tk.Label(frame, text=entry["name"], bg=theme.GLASS_PANEL, fg=theme.GLASS_TEXT,
                font=(theme.FONT, 10, "bold")).pack(anchor="w")
        when = datetime.fromisoformat(entry["at"]).strftime("%A %H:%M")
        tk.Label(frame, text=when, bg=theme.GLASS_PANEL, fg=theme.GLASS_MUTED,
                font=(theme.FONT, 9)).pack(anchor="w", pady=(0, 8))
        tk.Button(frame, text=self.action_text, bg=self.action_bg, fg="white",
                  relief="flat", padx=12, pady=4, font=(theme.FONT, 9, "bold"),
                  activebackground=self.action_bg, activeforeground="white",
                  bd=0, cursor="hand2", command=lambda: self._trigger(entry)).pack(anchor="w")
        win.bind("<Enter>", self._cancel_hide)
        win.bind("<Leave>", self._schedule_hide)

        # Anchor to the row itself (its left edge, just below its bottom edge)
        # rather than to the cursor - the popup should sit in the same place
        # regardless of where within the row the mouse happens to be.
        x, y, _width, height = bbox
        x_root = self.tree.winfo_rootx() + x
        y_root = self.tree.winfo_rooty() + y + height + 3
        win.geometry(f"+{x_root}+{y_root}")
        theme.round_corners(win, "small")
        self._popup = win

    def _trigger(self, entry: dict) -> None:
        self._hide_popup()
        self.on_action(entry)

    def _schedule_hide(self, _event=None) -> None:
        self._cancel_hide()
        self._hide_job = self.after(self._HIDE_DELAY_MS, self._hide_popup)

    def _cancel_hide(self, _event=None) -> None:
        if self._hide_job is not None:
            self.after_cancel(self._hide_job)
            self._hide_job = None

    def _hide_popup(self) -> None:
        self._cancel_hide()
        if self._popup is not None:
            try:
                self._popup.destroy()
            except tk.TclError:
                pass
            self._popup = None
        self._popup_row = None


class Console(tk.Tk):
    def __init__(self, cfg: dict) -> None:
        super().__init__()
        self.cfg = cfg
        self.title("ChildControl - parent console")
        self.geometry("980x720")
        self.minsize(900, 640)

        theme.apply(self)
        theme.round_corners(self)
        theme.dark_titlebar(self, False)

        self.configure(bg=theme.BG_TOP)
        backdrop = theme.Backdrop(self)
        backdrop.pack(fill="both", expand=True)
        content = tk.Frame(backdrop, bg=theme.BG, highlightthickness=0)
        content_win = backdrop.create_window(0, 0, window=content, anchor="nw")
        content_radius = 14

        def _fit_content(event) -> None:
            margin = 24
            x1, y1 = margin, margin
            x2, y2 = event.width - margin, event.height - margin
            backdrop.delete("contentcard")
            theme.draw_shadow(backdrop, x1, y1, x2, y2, content_radius,
                              fade_to=theme.BG_BOTTOM, layers=6, spread=3, drop=5,
                              tags="contentcard")
            theme.round_rect(backdrop, x1, y1, x2, y2, radius=content_radius,
                             fill=theme.BG, outline="", tags="contentcard")
            backdrop.tag_lower("contentcard")
            # content itself stays inset by the corner radius, so its square
            # corners never overlap (and square off) the rounded card edge.
            backdrop.coords(content_win, x1 + content_radius, y1 + content_radius)
            backdrop.itemconfigure(
                content_win,
                width=max(1, (x2 - x1) - content_radius * 2),
                height=max(1, (y2 - y1) - content_radius * 2))
            backdrop.tag_raise(content_win)

        backdrop.bind("<Configure>", _fit_content, add="+")

        notebook = ttk.Notebook(content)
        notebook.pack(fill="both", expand=True, pady=(0, 10))
        self._build_status_tab(notebook)
        self._build_schedule_tab(notebook)
        self._build_apps_tab(notebook)
        self._build_sites_tab(notebook)
        self._build_setup_tab(notebook)

        bar = ttk.Frame(content)
        bar.pack(fill="x")
        self.footer = ttk.Label(bar, text="", style="Muted.TLabel")
        self.footer.pack(side="left")
        ttk.Button(bar, text="Reload", command=self.reload).pack(side="right")
        theme.PillButton(bar, text="Save changes", command=self.save,
                         bg=theme.BG, fill=theme.ACCENT, hover=theme.ACCENT_HOVER
                         ).pack(side="right", padx=6)

        self.after(500, self._refresh_status)

    # --- tabs --------------------------------------------------------------

    def _build_status_tab(self, notebook: ttk.Notebook) -> None:
        tab = ttk.Frame(notebook)
        notebook.add(tab, text="Now")

        self.state_label = tk.Label(tab, text="-", font=(theme.FONT, 34, "bold"),
                                    bg=theme.BG)
        self.state_label.pack(pady=(28, 4))
        self.state_detail = ttk.Label(tab, text="", font=(theme.FONT, 11))
        self.state_detail.pack()
        self.agent_label = ttk.Label(tab, text="", font=(theme.FONT, 10), style="Muted.TLabel")
        self.agent_label.pack(pady=(12, 0))

        actions = theme.card(tab, title="Temporary override")
        actions.pack(pady=26, padx=40, fill="x")
        grid = ttk.Frame(actions.body, style="Card.TFrame")
        grid.pack(pady=14)
        buttons = [
            ("Free for 30 min", sched.FREE, 30),
            ("Free for 2 hours", sched.FREE, 120),
            ("Study now for 1 hour", sched.STUDY, 60),
            ("Lock now for 1 hour", sched.LOCKED, 60),
        ]
        for column, (text, state, minutes) in enumerate(buttons):
            ttk.Button(grid, text=text, width=22,
                       command=lambda s=state, m=minutes: self.set_override(s, m)
                       ).grid(row=column // 2, column=column % 2, padx=6, pady=4)
        theme.PillButton(actions.body, text="Back to the schedule", command=self.clear_override,
                         bg=theme.GLASS, fill=theme.ACCENT, hover=theme.ACCENT_HOVER
                         ).pack(pady=(0, 18))

        ttk.Label(tab, text="Overrides win over the weekly schedule until they expire.",
                  style="Muted.TLabel").pack()

        lists = ttk.Frame(tab)
        lists.pack(fill="both", expand=True, padx=16, pady=(10, 12))
        lists.columnconfigure(0, weight=1)
        lists.columnconfigure(1, weight=1)
        lists.rowconfigure(0, weight=1)

        self.visited_list = HoverActionList(
            lists, "Visited this session", "Block", theme.DANGER, self._quick_block)
        self.visited_list.grid(row=0, column=0, sticky="nsew", padx=(0, 8))

        self.blocked_list = HoverActionList(
            lists, "Blocked attempts", "Unblock", theme.SUCCESS, self._quick_unblock)
        self.blocked_list.grid(row=0, column=1, sticky="nsew", padx=(8, 0))

        self.after(500, self._refresh_activity)

    def _build_schedule_tab(self, notebook: ttk.Notebook) -> None:
        tab = ttk.Frame(notebook)
        notebook.add(tab, text="Weekly schedule")
        self.grid_editor = ScheduleGrid(tab, self.cfg["schedule"])
        self.grid_editor.pack(fill="both", expand=True)
        ttk.Label(tab, text="Click or drag across the grid to paint. Each cell is 30 minutes.",
                  style="Muted.TLabel").pack(anchor="w", padx=16, pady=(0, 10))

    def _build_apps_tab(self, notebook: ttk.Notebook) -> None:
        tab = ttk.Frame(notebook)
        notebook.add(tab, text="Blocked apps")

        self.apps_editor = ListEditor(
            tab, self.cfg["blocked_apps"],
            "Executable names, e.g. steam.exe. Matched case-insensitively.")
        self.apps_editor.pack(fill="both", expand=True, padx=12, pady=12)

        options = theme.card(tab, title="Games and network")
        options.pack(fill="x", padx=12, pady=(0, 12))
        self.steam_var = tk.BooleanVar(value=self.cfg.get("block_steam_library", True))
        ttk.Checkbutton(options.body, variable=self.steam_var, style="Card.TCheckbutton",
                        text="Block every program inside the Steam game folders "
                             "(covers all installed games without listing them)"
                        ).pack(anchor="w", padx=16, pady=(10, 2))
        folders = steamlib.game_folders()
        ttk.Label(options.body, style="CardMuted.TLabel",
                  text="Detected: " + (", ".join(folders) if folders else "no Steam install found"),
                  ).pack(anchor="w", padx=36, pady=(0, 8))
        self.firewall_var = tk.BooleanVar(value=self.cfg.get("use_firewall", True))
        ttk.Checkbutton(options.body, variable=self.firewall_var, style="Card.TCheckbutton",
                        text="Also cut Steam off from the network with a firewall rule"
                        ).pack(anchor="w", padx=16, pady=(0, 16))

        ttk.Label(tab, text="Extra folders to block (anything started from inside them):",
                  style="Muted.TLabel").pack(anchor="w", padx=12)
        self.folders_editor = ListEditor(
            tab, self.cfg.get("blocked_folders", []),
            r"Full paths, e.g. D:\Games", height=5)
        self.folders_editor.pack(fill="both", expand=True, padx=12, pady=(4, 12))

    def _build_sites_tab(self, notebook: ttk.Notebook) -> None:
        tab = ttk.Frame(notebook)
        notebook.add(tab, text="Blocked websites")
        self.sites_editor = ListEditor(
            tab, self.cfg["blocked_sites"],
            "One domain per line. www. is added automatically; other subdomains are not.")
        self.sites_editor.pack(fill="both", expand=True, padx=12, pady=12)
        ttk.Label(tab, wraplength=880, style="Muted.TLabel",
                  text="Sites are blocked through the Windows hosts file during Study and "
                       "Locked periods. Browsers can bypass that with their own encrypted DNS, "
                       "so the installer turns DNS-over-HTTPS off by policy for Chrome, Edge, "
                       "Brave and Firefox."
                  ).pack(anchor="w", padx=12, pady=(0, 12))

    def _build_setup_tab(self, notebook: ttk.Notebook) -> None:
        tab = ttk.Frame(notebook)
        notebook.add(tab, text="Setup")

        account = theme.card(tab, title="Child's Windows account")
        account.pack(fill="x", padx=12, pady=12)
        self.user_var = tk.StringVar(value=self.cfg.get("child_user", ""))
        row = ttk.Frame(account.body, style="Card.TFrame")
        row.pack(fill="x", padx=16, pady=(10, 16))
        ttk.Label(row, text="User name:", style="Card.TLabel").pack(side="left")
        ttk.Entry(row, textvariable=self.user_var, width=28).pack(side="left", padx=8)
        ttk.Label(row, text="(the lock screen runs under this account)",
                  style="CardMuted.TLabel").pack(side="left")

        service = theme.card(tab, title="Background service")
        service.pack(fill="x", padx=12, pady=(0, 12))
        buttons = ttk.Frame(service.body, style="Card.TFrame")
        buttons.pack(fill="x", padx=16, pady=(10, 6))
        theme.PillButton(buttons, text="Install / repair", command=self.do_install,
                         bg=theme.GLASS, fill=theme.ACCENT, hover=theme.ACCENT_HOVER
                         ).pack(side="left")
        ttk.Button(buttons, text="Uninstall", style="Danger.TButton",
                   command=self.do_uninstall).pack(side="left", padx=8)
        ttk.Button(buttons, text="Refresh", command=self._refresh_tasks).pack(side="left")
        self.tasks_label = ttk.Label(service.body, text="", style="CardMuted.TLabel", justify="left")
        self.tasks_label.pack(anchor="w", padx=16, pady=(0, 16))

        misc = theme.card(tab, title="Lock screen and misc")
        misc.pack(fill="x", padx=12, pady=(0, 12))
        self.message_var = tk.StringVar(value=self.cfg.get("lock_message", ""))
        row = ttk.Frame(misc.body, style="Card.TFrame")
        row.pack(fill="x", padx=16, pady=(10, 10))
        ttk.Label(row, text="Lock screen message:", style="Card.TLabel").pack(side="left")
        ttk.Entry(row, textvariable=self.message_var).pack(side="left", fill="x",
                                                           expand=True, padx=8)
        row = ttk.Frame(misc.body, style="Card.TFrame")
        row.pack(fill="x", padx=16, pady=(0, 16))
        ttk.Button(row, text="Change parent password", command=self.change_password).pack(side="left")
        ttk.Button(row, text="Re-apply browser DNS policy",
                   command=self.apply_browser_policy).pack(side="left", padx=8)
        ttk.Button(row, text="Open log folder", command=self.open_data_dir).pack(side="left")

        self._refresh_tasks()

    # --- actions -----------------------------------------------------------

    def collect(self) -> dict:
        cfg = dict(self.cfg)
        cfg["schedule"] = list(self.grid_editor.week)
        cfg["blocked_apps"] = self.apps_editor.items()
        cfg["blocked_folders"] = self.folders_editor.items()
        cfg["blocked_sites"] = self.sites_editor.items()
        cfg["block_steam_library"] = bool(self.steam_var.get())
        cfg["use_firewall"] = bool(self.firewall_var.get())
        cfg["child_user"] = self.user_var.get().strip()
        cfg["lock_message"] = self.message_var.get()
        return cfg

    def save(self) -> None:
        self.cfg = self.collect()
        try:
            config.save(self.cfg)
        except OSError as exc:
            messagebox.showerror("Save failed", str(exc), parent=self)
            return
        self._flash("Saved. The agent picks the change up within a few seconds.")

    def reload(self) -> None:
        self.cfg = config.load()
        self.grid_editor.load(self.cfg["schedule"])
        self.apps_editor.load(self.cfg["blocked_apps"])
        self.folders_editor.load(self.cfg.get("blocked_folders", []))
        self.sites_editor.load(self.cfg["blocked_sites"])
        self.steam_var.set(self.cfg.get("block_steam_library", True))
        self.firewall_var.set(self.cfg.get("use_firewall", True))
        self.user_var.set(self.cfg.get("child_user", ""))
        self.message_var.set(self.cfg.get("lock_message", ""))
        self._flash("Reloaded from disk.")

    def set_override(self, state: str, minutes: int) -> None:
        self.cfg = self.collect()
        config.set_override(self.cfg, state, minutes)
        config.save(self.cfg)
        self._flash(f"{sched.STATE_NAMES[state]} for {minutes} minutes.")

    def clear_override(self) -> None:
        self.cfg = self.collect()
        config.clear_override(self.cfg)
        config.save(self.cfg)
        self._flash("Override cleared - following the weekly schedule again.")

    def change_password(self) -> None:
        first = simpledialog.askstring("Change password", "New parent password:",
                                       show="*", parent=self)
        if not first:
            return
        if first != simpledialog.askstring("Change password", "Repeat it:", show="*", parent=self):
            messagebox.showerror("Change password", "The passwords did not match.", parent=self)
            return
        self.cfg = self.collect()
        self.cfg["password"] = config.hash_password(first)
        config.save(self.cfg)
        self._flash("Parent password updated.")

    def do_install(self) -> None:
        self.save()
        problems = install.install(self.user_var.get().strip() or None)
        self._refresh_tasks()
        if problems:
            messagebox.showwarning("Install", os.linesep.join(problems), parent=self)
        else:
            messagebox.showinfo("Install", "ChildControl is installed and running.", parent=self)

    def do_uninstall(self) -> None:
        if not messagebox.askyesno(
                "Uninstall",
                "Remove the scheduled tasks, unblock every website and undo the "
                "firewall and browser policy changes?", parent=self):
            return
        problems = install.uninstall()
        self._refresh_tasks()
        if problems:
            messagebox.showwarning("Uninstall", os.linesep.join(problems), parent=self)
        else:
            messagebox.showinfo("Uninstall", "Everything has been removed.", parent=self)

    def _quick_block(self, entry: dict) -> None:
        """Add a "visited" entry to the block list from its hover popup."""
        self.cfg = self.collect()
        key = "blocked_apps" if entry["kind"] == "app" else "blocked_sites"
        if entry["name"].lower() in {v.lower() for v in self.cfg[key]}:
            self._flash(f'"{entry["name"]}" is already blocked.')
            return
        self.cfg[key].append(entry["name"])
        config.save(self.cfg)
        (self.apps_editor if entry["kind"] == "app" else self.sites_editor).load(self.cfg[key])
        self._flash(f'Added "{entry["name"]}" to the block list.')

    def _quick_unblock(self, entry: dict) -> None:
        """Remove a "blocked attempts" entry from the block list from its hover popup."""
        self.cfg = self.collect()
        key = "blocked_apps" if entry["kind"] == "app" else "blocked_sites"
        before = len(self.cfg[key])
        self.cfg[key] = [v for v in self.cfg[key] if v.lower() != entry["name"].lower()]
        if len(self.cfg[key]) == before:
            self._flash(f'"{entry["name"]}" was not on the block list.')
            return
        config.save(self.cfg)
        (self.apps_editor if entry["kind"] == "app" else self.sites_editor).load(self.cfg[key])
        self._flash(f'Removed "{entry["name"]}" from the block list.')

    def apply_browser_policy(self) -> None:
        failed = browser_policy.apply()
        if failed:
            messagebox.showwarning("Browser policy", os.linesep.join(failed), parent=self)
        else:
            messagebox.showinfo("Browser policy",
                                "Encrypted DNS is now disabled by policy in Chrome, Edge, "
                                "Brave and Firefox.", parent=self)

    def open_data_dir(self) -> None:
        subprocess.Popen(["explorer", str(DATA_DIR)])

    # --- live status -------------------------------------------------------

    def _refresh_tasks(self) -> None:
        lines = [f"{'installed' if present else 'not installed'} - {name}"
                 for name, present in install.status().items()]
        self.tasks_label.configure(text=os.linesep.join(lines))

    def _refresh_status(self) -> None:
        """Show what the agent reports; fall back to computing it ourselves.

        Reading the agent's own view matters because overrides can also be
        created from the lock screen, without this console knowing.
        """
        status = read_json(STATUS_PATH, default={}) or {}
        now = datetime.now()
        updated = status.get("updated")
        age = (now - datetime.fromisoformat(updated)).total_seconds() if updated else None
        live = age is not None and age <= STALE_SECONDS

        if live and status.get("state") in sched.STATES:
            state = status["state"]
            override_until = (status.get("override") or {}).get("until")
            upcoming = status.get("next_change")
            next_pair = ((datetime.fromisoformat(upcoming["at"]), upcoming["state"])
                         if upcoming else None)
        else:
            state, override = config.effective_state(self.cfg, now)
            override_until = override["until"].isoformat() if override else None
            next_pair = sched.next_transition(self.cfg["schedule"], now)

        self.state_label.configure(text=sched.STATE_NAMES[state],
                                   fg=sched.STATE_COLORS[state])
        details = [sched.STATE_HELP[state]]
        if override_until:
            details.append(f"Override until "
                           f"{datetime.fromisoformat(override_until).strftime('%H:%M')}")
        elif next_pair:
            target, next_state = next_pair
            details.append(f"Next: {sched.STATE_NAMES[next_state]} at "
                           f"{target.strftime('%a %H:%M')}")
        self.state_detail.configure(text="   |   ".join(details))

        if age is None:
            note = "Agent has never run. Install it on the Setup tab."
        elif not live:
            note = f"Agent last reported {int(age // 60)} min ago - it may not be running."
        elif not status.get("agent_admin"):
            note = "Agent is running without administrator rights - blocking will fail."
        else:
            note = f"Agent healthy (pid {status.get('agent_pid')})."
        self.agent_label.configure(text=note)
        self.after(2000, self._refresh_status)

    def _refresh_activity(self) -> None:
        """Show the current study/lock session's activity, or the last one."""
        data = activity.read_session_activity()
        session = data.get("current") or data.get("last")
        if session is None:
            self.visited_list.load([], "No study session has happened yet.")
            self.blocked_list.load([], "No study session has happened yet.")
        else:
            started = datetime.fromisoformat(session["started"]).strftime("%a %H:%M")
            if data.get("current") is not None:
                subtitle = f"Ongoing session since {started}"
            else:
                ended = session.get("ended")
                ended_txt = datetime.fromisoformat(ended).strftime("%H:%M") if ended else "?"
                subtitle = f"Last session {started}-{ended_txt}"
            self.visited_list.load(list(reversed(session.get("visited", []))), subtitle)
            self.blocked_list.load(list(reversed(session.get("blocked", []))), subtitle)
        self.after(4000, self._refresh_activity)

    def _flash(self, text: str) -> None:
        self.footer.configure(text=text)
        self.after(6000, lambda: self.footer.configure(text=""))


def main() -> int:
    if not is_admin():
        script = os.path.abspath(sys.argv[0])
        if relaunch_as_admin(script):
            return 0
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(
            "ChildControl",
            "The console needs administrator rights to change the hosts file, "
            "the firewall and the scheduled tasks.")
        return 1

    cfg = config.load()
    root = tk.Tk()
    root.withdraw()
    if not PasswordGate.unlock(root, cfg):
        root.destroy()
        return 1
    root.destroy()

    Console(config.load()).mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
