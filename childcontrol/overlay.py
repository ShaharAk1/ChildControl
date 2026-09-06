"""Full-screen lock screen shown in the child's own desktop session.

The agent runs as SYSTEM and therefore cannot draw on the user's desktop, so
this small unprivileged companion reads status.json and covers the screen
while the state is Locked. It also warns a few minutes before study time.
"""

from __future__ import annotations

import hashlib
import os
import time
import tkinter as tk
import uuid
from datetime import datetime
from tkinter import messagebox, simpledialog

from . import activity, config, theme
from . import schedule as sched
from .util import (
    REQUEST_DIR,
    STATUS_PATH,
    read_json,
    setup_logging,
    single_instance,
    write_json,
)

log = setup_logging("overlay")

BG = theme.GLASS_BG
PANEL = theme.GLASS_PANEL
FG = theme.GLASS_TEXT
MUTED = theme.GLASS_MUTED
ACCENT = theme.ACCENT
POLL_MS = 2000
MUTEX_NAME = r"Local\ChildControlOverlay"
UNLOCK_MINUTES = 60


def format_delta(target: datetime, now: datetime) -> str:
    seconds = max(0, int((target - now).total_seconds()))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


class LockScreen:
    """The black full-screen cover shown while the state is Locked."""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.window: tk.Toplevel | None = None
        self.status: dict = {}

    def show(self) -> None:
        if self.window is not None:
            return
        win = tk.Toplevel(self.root)
        win.configure(bg=BG)
        win.attributes("-fullscreen", True)
        win.attributes("-topmost", True)
        win.protocol("WM_DELETE_WINDOW", lambda: None)
        win.bind("<Escape>", lambda _event: "break")

        backdrop = theme.Backdrop(win, top=theme.GLASS_BG, bottom=theme.GLASS_BG_BOTTOM,
                                  blobs=(theme.DBLOB_1, theme.DBLOB_2, theme.DBLOB_3))
        backdrop.pack(fill="both", expand=True)

        panel = tk.Frame(backdrop, bg=PANEL, padx=64, pady=48)
        panel_win = backdrop.create_window(0, 0, window=panel, anchor="center")

        self.clock = tk.Label(panel, text="", bg=PANEL, fg=MUTED, font=(theme.FONT, 20))
        self.clock.pack(pady=(0, 12))
        tk.Label(panel, text="Computer locked", bg=PANEL, fg=FG,
                 font=(theme.FONT, 44, "bold")).pack()
        self.message = tk.Label(panel, text="", bg=PANEL, fg=FG, font=(theme.FONT, 16),
                                wraplength=900, justify="center")
        self.message.pack(pady=(20, 8))
        self.countdown = tk.Label(panel, text="", bg=PANEL, fg=ACCENT, font=(theme.FONT, 18, "bold"))
        self.countdown.pack(pady=(6, 30))
        theme.PillButton(panel, text="Parent unlock", command=self.ask_unlock,
                         bg=PANEL, fill=theme.ACCENT, hover=theme.ACCENT_HOVER,
                         font=(theme.FONT, 11, "bold"), padx=24, pady=12).pack()

        radius = 24

        def redraw(_event=None) -> None:
            backdrop.update_idletasks()
            bw, bh = backdrop.winfo_width(), backdrop.winfo_height()
            if bw < 2 or bh < 2:
                return
            pw, ph = panel.winfo_reqwidth(), panel.winfo_reqheight()
            cx, cy = bw / 2, bh / 2
            x1, y1 = cx - pw / 2, cy - ph / 2
            x2, y2 = cx + pw / 2, cy + ph / 2
            backdrop.delete("lockpanel")
            theme.draw_shadow(backdrop, x1, y1, x2, y2, radius,
                              fade_to=backdrop.color_at(cy, bh), base="#05070c",
                              layers=6, spread=3, drop=6, tags="lockpanel")
            theme.round_rect(backdrop, x1, y1, x2, y2, radius=radius,
                             fill=PANEL, outline=theme.GLASS_BORDER_DARK, width=1,
                             tags="lockpanel")
            backdrop.tag_lower("lockpanel")
            backdrop.tag_raise(panel_win)
            backdrop.coords(panel_win, cx, cy)

        panel.bind("<Configure>", redraw)
        backdrop.bind("<Configure>", redraw, add="+")
        win.after(50, redraw)

        self.window = win
        log.info("lock screen shown")

    def hide(self) -> None:
        if self.window is None:
            return
        try:
            self.window.destroy()
        except tk.TclError:
            pass
        self.window = None
        log.info("lock screen hidden")

    def refresh(self, now: datetime) -> None:
        if self.window is None:
            return
        self.window.attributes("-topmost", True)
        try:
            self.window.focus_force()
        except tk.TclError:
            pass
        self.clock.configure(text=now.strftime("%A %H:%M"))
        self.message.configure(text=self.status.get("lock_message", ""))
        upcoming = self.status.get("next_change")
        if upcoming:
            target = datetime.fromisoformat(upcoming["at"])
            self.countdown.configure(
                text=f"Unlocks in {format_delta(target, now)}  (at {target.strftime('%H:%M')})"
            )
        else:
            self.countdown.configure(text="")

    # --- parent unlock, straight from the lock screen -----------------------

    def ask_unlock(self) -> None:
        parent = self.window or self.root
        password = simpledialog.askstring("Parent unlock", "Parent password:",
                                          show="*", parent=parent)
        if not password:
            return
        salt = self.status.get("password_salt")
        if not salt:
            messagebox.showerror("Parent unlock", "No parent password is set yet.",
                                 parent=parent)
            return
        rounds = int(self.status.get("password_rounds") or config.PBKDF2_ROUNDS)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                     bytes.fromhex(salt), rounds).hex()
        request_id = uuid.uuid4().hex
        try:
            REQUEST_DIR.mkdir(parents=True, exist_ok=True)
            write_json(REQUEST_DIR / f"{request_id}.json",
                       {"id": request_id, "digest": digest, "minutes": UNLOCK_MINUTES})
        except OSError as exc:
            messagebox.showerror("Parent unlock", f"Could not reach the agent: {exc}",
                                 parent=parent)
            return
        if self.await_result(request_id):
            messagebox.showinfo("Parent unlock",
                                f"Unlocked for {UNLOCK_MINUTES} minutes.", parent=parent)
        else:
            messagebox.showerror("Parent unlock",
                                 "Wrong password, or the agent is not running.",
                                 parent=parent)

    def await_result(self, request_id: str, timeout: float = 20.0) -> bool:
        result_path = REQUEST_DIR / f"{request_id}.result.json"
        deadline = time.time() + timeout
        while time.time() < deadline:
            result = read_json(result_path, default=None)
            if isinstance(result, dict):
                try:
                    result_path.unlink()
                except OSError:
                    pass
                return bool(result.get("ok"))
            time.sleep(0.5)
        return False


class WarningToast:
    """Small topmost notice shown shortly before restrictions start (or right
    after an app gets blocked). `corner` keeps the two kinds from overlapping
    if they ever land at the same moment."""

    def __init__(self, root: tk.Tk, corner: str = "top-right") -> None:
        self.root = root
        self.window: tk.Toplevel | None = None
        self.corner = corner

    def show(self, text: str, seconds: int = 12, accent: str | None = None) -> None:
        self.dismiss()
        win = tk.Toplevel(self.root)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.configure(bg=PANEL)
        outer = tk.Frame(win, bg=PANEL, highlightbackground=theme.GLASS_BORDER_DARK,
                         highlightthickness=1)
        outer.pack()
        if accent:
            tk.Frame(outer, bg=accent, height=4).pack(fill="x")
        tk.Label(outer, text=text, bg=PANEL, fg=FG, font=(theme.FONT, 13),
                 padx=22, pady=16, wraplength=420, justify="left").pack()
        win.update_idletasks()
        if self.corner == "top-left":
            x = 30
        else:
            x = win.winfo_screenwidth() - win.winfo_width() - 30
        win.geometry(f"+{x}+40")
        theme.round_corners(win, "small")
        self.window = win
        self.root.after(seconds * 1000, self.dismiss)

    def dismiss(self) -> None:
        if self.window is None:
            return
        try:
            self.window.destroy()
        except tk.TclError:
            pass
        self.window = None


def build_block_message(name: str, next_free_iso: str | None, now: datetime) -> str:
    """'"steam.exe" has been blocked.' plus, when the schedule allows it, a
    second line saying when the computer is free again. If the schedule never
    has a Free slot, the second line is simply omitted."""
    line = f'"{name}" has been blocked.'
    if not next_free_iso:
        return line
    target = datetime.fromisoformat(next_free_iso)
    return line + "\n" + f"You can use the computer freely again {sched.describe_relative(target, now)}."


class OverlayApp:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.withdraw()
        self.lock = LockScreen(self.root)
        self.toast = WarningToast(self.root)
        self.block_toast = WarningToast(self.root, corner="top-left")
        self.warned_for: str | None = None
        self.status: dict = {}
        self._last_kill_seq: int | None = None
        self._kill_queue: list[dict] = []
        self._kill_toast_busy = False

    def poll(self) -> None:
        status = read_json(STATUS_PATH, default={}) or {}
        self.status = status
        self.lock.status = status
        now = datetime.now()
        if status.get("state") == sched.LOCKED:
            self.lock.show()
            self.lock.refresh(now)
        else:
            self.lock.hide()
            self.maybe_warn(status, now)
        self.poll_kill_events(status)
        self.root.after(POLL_MS, self.poll)

    def poll_kill_events(self, status: dict) -> None:
        """Show 'X has been blocked' the moment the agent reports a kill.

        The full-screen lock already explains "you can't use anything, here's
        when it lifts" - a block toast on top of that would be redundant, so
        this only fires while the computer is otherwise usable (Study).
        """
        data = activity.read_kill_events()
        events = data.get("events", [])
        if self._last_kill_seq is None:
            self._last_kill_seq = events[-1]["seq"] if events else 0
            return
        new_events = [e for e in events if e["seq"] > self._last_kill_seq]
        if not new_events:
            return
        self._last_kill_seq = new_events[-1]["seq"]
        if status.get("state") != sched.LOCKED:
            self._kill_queue.extend(new_events)
            self._drain_kill_queue()

    def _drain_kill_queue(self) -> None:
        if self._kill_toast_busy or not self._kill_queue:
            return
        event = self._kill_queue.pop(0)
        self._kill_toast_busy = True
        message = build_block_message(event["name"], self.status.get("next_free"), datetime.now())
        self.block_toast.show(message, seconds=8, accent=theme.DANGER)
        self.root.after(8500, self._kill_toast_done)

    def _kill_toast_done(self) -> None:
        self._kill_toast_busy = False
        self._drain_kill_queue()

    def maybe_warn(self, status: dict, now: datetime) -> None:
        upcoming = status.get("next_change")
        if not upcoming or upcoming.get("state") == sched.FREE:
            return
        target = datetime.fromisoformat(upcoming["at"])
        minutes_left = (target - now).total_seconds() / 60
        if 0 < minutes_left <= 5 and self.warned_for != upcoming["at"]:
            self.warned_for = upcoming["at"]
            label = "Study time" if upcoming["state"] == sched.STUDY else "Computer lock"
            lines = [
                f"{label} starts at {target.strftime('%H:%M')}",
                f"({int(minutes_left) + 1} minutes from now). Save your work.",
            ]
            self.toast.show(os.linesep.join(lines))

    def run(self) -> None:
        log.info("overlay started (pid %d, user %s)", os.getpid(),
                 os.environ.get("USERNAME", "?"))
        self.root.after(500, self.poll)
        self.root.mainloop()


def main() -> int:
    # The 5-minute watchdog task relaunches us; one lock screen per session is enough.
    if not single_instance(MUTEX_NAME):
        log.info("overlay already running in this session - exiting")
        return 0
    OverlayApp().run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
