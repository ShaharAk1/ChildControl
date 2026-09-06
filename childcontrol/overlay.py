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

from . import config
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

BG = "#0f1319"
FG = "#f2f5f8"
MUTED = "#8b97a8"
ACCENT = "#68a0ff"
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

        frame = tk.Frame(win, bg=BG)
        frame.place(relx=0.5, rely=0.5, anchor="center")

        self.clock = tk.Label(frame, text="", bg=BG, fg=MUTED, font=("Segoe UI", 22))
        self.clock.pack(pady=(0, 10))
        tk.Label(frame, text="Computer locked", bg=BG, fg=FG,
                 font=("Segoe UI Semibold", 46)).pack()
        self.message = tk.Label(frame, text="", bg=BG, fg=FG, font=("Segoe UI", 18),
                                wraplength=900, justify="center")
        self.message.pack(pady=(18, 6))
        self.countdown = tk.Label(frame, text="", bg=BG, fg=ACCENT, font=("Segoe UI", 20))
        self.countdown.pack(pady=(6, 26))
        tk.Button(frame, text="Parent unlock", command=self.ask_unlock,
                  bg="#1d2734", fg=FG, activebackground="#28374a", activeforeground=FG,
                  relief="flat", padx=18, pady=8, font=("Segoe UI", 11),
                  cursor="hand2").pack()

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
    """Small topmost notice shown shortly before restrictions start."""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.window: tk.Toplevel | None = None

    def show(self, text: str, seconds: int = 12) -> None:
        self.dismiss()
        win = tk.Toplevel(self.root)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.configure(bg="#1d2734")
        tk.Label(win, text=text, bg="#1d2734", fg=FG, font=("Segoe UI", 13),
                 padx=22, pady=16, wraplength=420, justify="left").pack()
        win.update_idletasks()
        x = win.winfo_screenwidth() - win.winfo_width() - 30
        win.geometry(f"+{x}+40")
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


class OverlayApp:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.withdraw()
        self.lock = LockScreen(self.root)
        self.toast = WarningToast(self.root)
        self.warned_for: str | None = None

    def poll(self) -> None:
        status = read_json(STATUS_PATH, default={}) or {}
        self.lock.status = status
        now = datetime.now()
        if status.get("state") == sched.LOCKED:
            self.lock.show()
            self.lock.refresh(now)
        else:
            self.lock.hide()
            self.maybe_warn(status, now)
        self.root.after(POLL_MS, self.poll)

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
