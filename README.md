# ChildControl

A local Windows parental-control tool: a weekly schedule decides when the PC is
**Free**, in **Study** mode (games and distracting sites blocked) or **Locked**
(unusable). A password-protected console lets you edit the schedule and the
block lists; a background agent enforces them.

Everything is local. No accounts, no network, no third-party packages —
Python 3.11+ and its bundled Tkinter only.

## The three states

| State | Games / blocked apps | Blocked websites | Computer usable |
|-------|----------------------|------------------|-----------------|
| Free   | allowed | allowed | yes |
| Study  | killed on sight | blocked | yes |
| Locked | killed on sight | blocked | no — full-screen lock |

Each day is 48 half-hour cells you paint in the console. A temporary override
("Free for 30 min", "Lock now for 1 hour") beats the schedule until it expires.

## How the blocking actually works

**Applications** — the agent enumerates running processes through the Win32
ToolHelp API (`childcontrol/winproc.py`) and terminates anything that matches:

* an executable name from the blocked list (`steam.exe`, `Discord.exe`, …), or
* any program started from inside a Steam library folder. The library paths are
  read from the registry and `libraryfolders.vdf`, so **every installed game is
  covered without listing them one by one** — including games installed later.

A hard-coded protected list (`explorer.exe`, `csrss.exe`, …) can never be killed,
even if someone types it into the blocked list.

Steam additionally gets Windows Firewall rules (in/out block) so it cannot keep
downloading or reconnecting. All rules live in one `ChildControl` firewall group,
so restrictions are toggled with a single `netsh` call rather than rebuilt.

**Websites** — the agent rewrites the region between two markers in
`C:\Windows\System32\drivers\etc\hosts`, pointing each domain (and its `www.`
form) at `0.0.0.0`, then flushes the DNS cache. Anything you wrote in that file
by hand is left untouched.

Modern browsers can resolve names over their own encrypted DNS (DoH) and
completely bypass the hosts file, so the installer also writes machine policy
turning DoH off for Chrome, Edge, Brave and Firefox. Without this step, site
blocking silently stops working in exactly the browsers a teenager uses.

**Locking** — a SYSTEM process cannot draw on a user's desktop (session 0
isolation), so the lock screen is a separate unprivileged program running in your
brother's session. It reads `status.json` and covers the screen while the state
is Locked, showing a countdown and a *Parent unlock* button.

## What he sees when something gets blocked

Whenever the agent kills a blocked app, the lock screen program pops up a small
notice: *"steam.exe" has been blocked. You can use the computer freely again
today at 19:00* (or *tomorrow at 8:00*, or a weekday name if it's further out).
If the schedule genuinely has no Free time coming up, that second line is just
left off. This only shows during Study — during Locked the full-screen cover
already explains the same thing, so it stays out of the way.

## The Now tab's activity lists

Underneath the override buttons, the **Now** tab splits into two scrollable
lists covering the current Study/Locked session (or the last one, if none is
running):

* **Visited this session** — every app and website that ran or resolved during
  the session *without* being blocked. Hover a row for its name, the time, and
  a red **Block** button that adds it to the matching block list immediately.
* **Blocked attempts** — everything that actually got killed or hit a blocked
  site. Hover for a green **Unblock** button that removes it from the block
  list.

Blocking/unblocking only edits the config — it doesn't move the historical
entry between lists, since that record of what actually happened doesn't
change. A name only ever appears in one bucket per session (first occurrence
wins), so relaunching the same blocked game repeatedly doesn't spam the list.

Website visits are read from the Windows DNS cache (`ipconfig /displaydns`),
which works across every browser because the DoH policy above forces them all
through it — no extra software needed. A short built-in noise filter skips
OS/telemetry domains (Windows Update, Microsoft telemetry, CDN infrastructure)
so the visited list stays about what he actually opened. Background apps
already running when a session starts aren't logged as "visited" — only things
that start during the session are, so logon-time clutter (antivirus, tray
apps, etc.) doesn't fill the list up front. This also means a background app
that only starts mid-session (e.g. a scheduled updater) can still show up —
there's no attempt to guess whether a process has a visible window.

## Layout

```
run_console.pyw        parent console      (elevates itself, asks for the password)
run_agent.pyw          enforcement agent   (scheduled task, SYSTEM, at boot)
run_overlay.pyw        lock screen         (scheduled task, child's account, at logon)

childcontrol/
  config.py            defaults, PBKDF2 password, overrides
  schedule.py          7 x 48 half-hour week, state lookup, next transition
  agent.py             the enforcement loop
  gui.py               the console (Tkinter/ttk)
  overlay.py           the lock screen and pre-study warning toast
  install.py           scheduled tasks + data-directory ACLs
  winproc.py           ctypes process list / terminate
  apps_block.py        which processes are blocked right now
  activity.py          session visited/blocked log + DNS-cache site detection
  hosts_block.py       hosts-file website blocking
  firewall.py          Windows Firewall rule group
  browser_policy.py    disables browser DNS-over-HTTPS
  steamlib.py          finds Steam and its game library folders
```

State lives in `C:\ProgramData\ChildControl\`:
`config.json` (settings), `status.json` (what the agent is doing, world-readable),
`activity.json` (current/last session's visited + blocked lists),
`kill_events.json` (raw per-kill log driving the lock screen's block toast),
`requests\` (unlock requests from the lock screen), `childcontrol.log`.

## Install on his PC

1. Copy this folder to his machine and install Python 3.11+ **for all users**
   (the SYSTEM task needs an interpreter it can reach — not a per-user install
   under `C:\Users\<you>\AppData`).
2. Run `run_console.pyw`. It asks for admin rights, then — the first time,
   with nothing installed yet — walks you through a setup wizard: set the
   parent password, checks that this machine can actually run the background
   tasks (admin rights, Python reachable by a SYSTEM task, Task Scheduler
   running), lets you pick his account from the real local accounts on the
   machine, then installs everything with a live pass/fail per step instead
   of one lump error dialog at the end.
3. **Weekly schedule** tab → paint his week → **Save changes**.
4. Check the **Blocked apps** and **Blocked websites** tabs, then Save.

The **Now** tab shows the live state and whether the agent is healthy.

The wizard only appears once, when nothing is installed yet. Afterward, use
the **Setup** tab's **Install / repair** to redo it (e.g. after moving the
folder or reinstalling Python) and **Uninstall** to remove everything —
tasks, hosts entries, firewall rules, browser policy.

### Command line equivalents

```powershell
python -m childcontrol.install install --child-user Danny   # register tasks (admin)
python -m childcontrol.install status                       # are the tasks there?
python -m childcontrol.install uninstall                    # undo everything (admin)
python -m childcontrol.agent --once                         # one enforcement pass, verbose
python -m childcontrol.agent --apply-browser-policy         # DoH off only
```

## Why it survives normal use

* Four scheduled tasks: the agent at boot and the overlay at logon, each with a
  5-minute watchdog task that relaunches it if it dies. Named mutexes
  (`Global\ChildControlAgent`, `Local\ChildControlOverlay`) keep the watchdog
  from stacking up duplicate copies.
* `config.json` lives in `ProgramData` with inheritance stripped: Administrators
  and SYSTEM have full control, the child's account gets read-only. Only
  `requests\` is writable by him, and requests are authenticated with the
  PBKDF2 hash of the parent password.

It is deliberately not tamper-proof. An account with administrator rights can
stop the tasks, so **give him a standard (non-administrator) Windows account**
— that one setting does more than anything else in this program.

## Known limits

* Site blocking is a deny-list, not an allow-list. A hosts file cannot express
  "only these study sites" — that needs a proxy or a local DNS server.
* Subdomains other than `www.` are not covered automatically; add
  `m.example.com` explicitly if you need it.
* A VPN or a phone hotspot bypasses hosts-file blocking entirely.
