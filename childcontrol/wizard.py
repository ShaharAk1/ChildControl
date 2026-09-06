"""First-run setup wizard: password, prerequisites, child's account, install.

Shown once, automatically, the first time the console runs and nothing is
installed yet (see `gui.main`). It exists to turn the fragile parts of
first-time setup - is Python reachable by a SYSTEM task, is Task Scheduler
even running, does the typed account name actually exist - into something
checked and shown, rather than discovered days later as "it didn't survive
the reboot". Later repairs and uninstalling stay on the console's Setup tab;
this is only the guided first pass.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from . import config, install, theme
from .util import is_admin

STEPS_BEFORE_INSTALL = ("password", "prereqs", "account")
CONTINUE_LABELS = {"account": "Install now", "install": "Finish", "done": "Open the console"}


class InstallWizard(tk.Toplevel):
    def __init__(self, root: tk.Tk, cfg: dict) -> None:
        super().__init__(root)
        self.cfg = cfg
        self.result = False
        self.install_failed = False
        self.child_user = cfg.get("child_user", "")

        self.title("ChildControl - first-time setup")
        self.geometry("640x640")
        self.minsize(600, 480)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        theme.apply(self)
        theme.round_corners(self)
        theme.dark_titlebar(self, False)
        self.configure(bg=theme.BG)

        self.step_names = (["password"] if not cfg.get("password") else []) + \
            ["prereqs", "account", "install", "done"]
        self.step_pos = 0

        # A step's content (the install checklist especially, with failures
        # spelled out) can end up taller than the window - scroll rather
        # than clip, instead of gambling on a fixed height being enough.
        scroll_area = ttk.Frame(self)
        scroll_area.pack(fill="both", expand=True, padx=28, pady=(28, 0))
        body_canvas = tk.Canvas(scroll_area, bg=theme.BG, highlightthickness=0)
        body_scroll = ttk.Scrollbar(scroll_area, orient="vertical", command=body_canvas.yview)
        body_canvas.configure(yscrollcommand=body_scroll.set)
        body_canvas.pack(side="left", fill="both", expand=True)
        body_scroll.pack(side="right", fill="y")

        self.body = tk.Frame(body_canvas, bg=theme.BG)
        body_window = body_canvas.create_window((0, 0), window=self.body, anchor="nw")
        self.body.bind("<Configure>",
                       lambda _e: body_canvas.configure(scrollregion=body_canvas.bbox("all")))
        body_canvas.bind("<Configure>",
                         lambda e: body_canvas.itemconfigure(body_window, width=e.width))
        body_canvas.bind("<MouseWheel>",
                         lambda e: body_canvas.yview_scroll(int(-e.delta / 120), "units"))

        nav = ttk.Frame(self)
        nav.pack(fill="x", padx=28, pady=20)
        self.skip_link = ttk.Label(nav, text="Skip setup for now", style="Muted.TLabel",
                                   cursor="hand2")
        self.skip_link.bind("<Button-1>", lambda _e: self._skip())
        self.continue_btn = theme.PillButton(nav, text="Continue", command=self._on_continue,
                                             bg=theme.BG, fill=theme.ACCENT,
                                             hover=theme.ACCENT_HOVER)
        self.continue_btn.pack(side="right")
        self.back_btn = ttk.Button(nav, text="Back", command=self._on_back)

        self.transient(root)
        self._show_step()
        # A Toplevel created against a withdrawn root can end up mapped but
        # not actually visible on Windows - force it forward explicitly
        # rather than relying on the implicit show-on-create behavior.
        self.deiconify()
        self.lift()
        self.after(10, self.grab_set)
        self.after(10, self.focus_force)

    # --- shell ---------------------------------------------------------------

    def _show_step(self) -> None:
        for widget in self.body.winfo_children():
            widget.destroy()
        name = self.step_names[self.step_pos]

        self.continue_btn.set_enabled(False)
        self.back_btn.pack_forget()
        self.skip_link.pack_forget()

        getattr(self, f"_render_{name}")()

        self.continue_btn.set_text(CONTINUE_LABELS.get(name, "Continue"))
        self.continue_btn.set_enabled(True)
        if self.step_pos > 0 and name not in ("install", "done"):
            self.back_btn.pack(side="right", padx=(0, 8))
        if name in STEPS_BEFORE_INSTALL:
            self.skip_link.pack(side="left")

    def _on_continue(self) -> None:
        name = self.step_names[self.step_pos]
        if name == "done":
            self.result = True
            self.destroy()
            return
        validator = getattr(self, f"_validate_{name}", None)
        if validator and not validator():
            return
        self.step_pos += 1
        self._show_step()

    def _on_back(self) -> None:
        if self.step_pos > 0:
            self.step_pos -= 1
            self._show_step()

    def _skip(self) -> None:
        if not self.cfg.get("password"):
            messagebox.showinfo("Set a password first",
                                "Choose a parent password before skipping the rest of setup.",
                                parent=self)
            return
        self.result = True
        self.destroy()

    def _on_close(self) -> None:
        self.result = False
        self.destroy()

    # --- heading helper --------------------------------------------------------

    def _heading(self, title: str, subtitle: str) -> None:
        ttk.Label(self.body, text=title, style="Heading.TLabel").pack(anchor="w")
        ttk.Label(self.body, text=subtitle, style="Muted.TLabel",
                  wraplength=560, justify="left").pack(anchor="w", pady=(6, 18))

    def _check_row(self, parent: tk.Misc, label: str, ok: bool, detail: str = "") -> None:
        row = ttk.Frame(parent, style="Card.TFrame")
        row.pack(fill="x", padx=16, pady=(12, 0 if detail else 12))
        tk.Label(row, text="✓" if ok else "!", bg=theme.GLASS,
                 fg=theme.SUCCESS if ok else theme.WARNING,
                 font=(theme.FONT, 12, "bold"), width=2).pack(side="left")
        ttk.Label(row, text=label, style="Card.TLabel").pack(side="left")
        if detail:
            ttk.Label(parent, text=detail, style="CardMuted.TLabel", wraplength=520,
                      justify="left").pack(anchor="w", padx=44, pady=(0, 12))

    # --- steps -----------------------------------------------------------------

    def _render_password(self) -> None:
        self._heading("Welcome to ChildControl",
                      "First, choose the parent password you'll use to change settings "
                      "and to unlock the lock screen from his desktop.")
        card = theme.card(self.body, title="Parent password")
        card.pack(fill="x")
        self.pw1, self.pw2 = tk.StringVar(), tk.StringVar()
        row1 = ttk.Frame(card.body, style="Card.TFrame")
        row1.pack(fill="x", padx=16, pady=(16, 6))
        ttk.Label(row1, text="Password:", style="Card.TLabel", width=13).pack(side="left")
        ttk.Entry(row1, textvariable=self.pw1, show="*").pack(side="left", fill="x", expand=True)
        row2 = ttk.Frame(card.body, style="Card.TFrame")
        row2.pack(fill="x", padx=16, pady=(0, 16))
        ttk.Label(row2, text="Repeat:", style="Card.TLabel", width=13).pack(side="left")
        ttk.Entry(row2, textvariable=self.pw2, show="*").pack(side="left", fill="x", expand=True)
        self.password_error = ttk.Label(self.body, text="", foreground=theme.DANGER,
                                        background=theme.BG)
        self.password_error.pack(anchor="w", pady=(10, 0))

    def _validate_password(self) -> bool:
        p1, p2 = self.pw1.get(), self.pw2.get()
        if not p1:
            self.password_error.configure(text="Choose a password.")
            return False
        if p1 != p2:
            self.password_error.configure(text="The passwords did not match.")
            return False
        self.cfg["password"] = config.hash_password(p1)
        config.save(self.cfg)
        return True

    def _render_prereqs(self) -> None:
        self._heading("Checking this computer",
                      "A few things need to be true for background enforcement to "
                      "actually survive a restart.")
        card = theme.card(self.body, title="Prerequisites")
        card.pack(fill="both", expand=True)
        body = card.body

        self._check_row(body, "Running as Administrator", is_admin(),
                        "" if is_admin() else "Restart the console and accept the UAC prompt.")

        py_ok, py_detail = install.python_all_users()
        self._check_row(body, "Python reachable by a SYSTEM task", py_ok,
                        "" if py_ok else py_detail)

        sched_ok = install.scheduler_service_running()
        self._check_row(body, "Task Scheduler service running", sched_ok,
                        "" if sched_ok else "Nothing below can work without this.")
        if not sched_ok:
            def _start() -> None:
                install.start_scheduler_service()
                self._show_step()
            theme.PillButton(body, text="Start it now", command=_start, bg=theme.GLASS,
                             fill=theme.ACCENT, hover=theme.ACCENT_HOVER, padx=16, pady=6
                             ).pack(anchor="w", padx=16, pady=(0, 16))

    def _render_account(self) -> None:
        self._heading("His Windows account",
                      "The lock screen has to run inside his own Windows session, so "
                      "pick the account he actually logs into.")
        card = theme.card(self.body, title="Local accounts on this PC")
        card.pack(fill="both", expand=True)
        body = card.body

        self.account_var = tk.StringVar(value=self.child_user)
        users = install.list_local_users()
        list_frame = ttk.Frame(body, style="Card.TFrame")
        list_frame.pack(fill="x", padx=16, pady=(16, 6))
        if users:
            for name in users:
                ttk.Radiobutton(list_frame, text=name, value=name, variable=self.account_var,
                                style="Card.TRadiobutton").pack(anchor="w", pady=2)
        else:
            ttk.Label(list_frame, text="Could not list local accounts - type the name below.",
                      style="CardMuted.TLabel").pack(anchor="w")

        ttk.Separator(body).pack(fill="x", padx=16, pady=10)
        manual_row = ttk.Frame(body, style="Card.TFrame")
        manual_row.pack(fill="x", padx=16, pady=(0, 16))
        ttk.Label(manual_row, text="Or type it exactly:", style="Card.TLabel").pack(side="left")
        ttk.Entry(manual_row, textvariable=self.account_var).pack(
            side="left", fill="x", expand=True, padx=8)

    def _validate_account(self) -> bool:
        name = self.account_var.get().strip()
        if not name:
            messagebox.showwarning("Account needed",
                                   "Pick or type the Windows account the lock screen "
                                   "should run under.", parent=self)
            return False
        if not install.user_exists(name):
            if not messagebox.askyesno(
                    "Account not found",
                    f'Windows does not recognize the account "{name}".\n\n'
                    "Continue anyway? The lock-screen task will fail to install if "
                    "this isn't a real local account name.", parent=self):
                return False
        self.child_user = name
        return True

    def _render_install(self) -> None:
        self._heading("Installing",
                      "Registering the background tasks that keep this running.")
        card = theme.card(self.body, title="Progress")
        card.pack(fill="both", expand=True)
        self.update_idletasks()
        self._run_install(card.body)

    def _run_install(self, body: tk.Frame) -> None:
        any_failed = False
        for step in install.install_steps(self.child_user):
            row = ttk.Frame(body, style="Card.TFrame")
            row.pack(fill="x", padx=16, pady=(12, 0 if step.detail else 12))
            tk.Label(row, text="✓" if step.ok else "✗", bg=theme.GLASS,
                     fg=theme.SUCCESS if step.ok else theme.DANGER,
                     font=(theme.FONT, 11, "bold"), width=2).pack(side="left")
            ttk.Label(row, text=step.label, style="Card.TLabel").pack(side="left")
            if step.detail:
                ttk.Label(body, text=step.detail, style="CardMuted.TLabel", wraplength=520,
                          justify="left").pack(anchor="w", padx=44, pady=(0, 12))
            any_failed = any_failed or not step.ok
            self.update()
        self.install_failed = any_failed

    def _render_done(self) -> None:
        if self.install_failed:
            self._heading("Installed with some problems",
                          "Some steps above failed - open the Setup tab in the console "
                          "afterward to review and retry them.")
        else:
            self._heading("All set",
                          "The background agent is running now. Reboot his PC once, then "
                          "check the Now tab here - it'll tell you honestly whether the "
                          "agent came back up healthy.")
