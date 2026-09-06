"""Install / remove the two scheduled tasks and the data directory ACLs.

Two components are registered:

  * the agent   - runs as SYSTEM at boot, plus a 5-minute watchdog task that
                  restarts it if it ever dies (the named mutex keeps a second
                  copy from double-enforcing).
  * the overlay - runs as the child's own account at logon, because a SYSTEM
                  process in session 0 cannot draw a lock screen.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from . import browser_policy, config, firewall, hosts_block
from .util import DATA_DIR, REPO_DIR, REQUEST_DIR, ensure_data_dir, is_admin, pythonw, run

AGENT_TASK = "ChildControl Agent"
AGENT_WATCHDOG_TASK = "ChildControl Agent Watchdog"
OVERLAY_TASK = "ChildControl Overlay"
OVERLAY_WATCHDOG_TASK = "ChildControl Overlay Watchdog"

ALL_TASKS = (AGENT_TASK, AGENT_WATCHDOG_TASK, OVERLAY_TASK, OVERLAY_WATCHDOG_TASK)

AGENT_SCRIPT = REPO_DIR / "run_agent.pyw"
OVERLAY_SCRIPT = REPO_DIR / "run_overlay.pyw"

# Well-known SIDs keep this working on non-English Windows installs.
SID_ADMINS = "*S-1-5-32-544"
SID_USERS = "*S-1-5-32-545"
SID_SYSTEM = "*S-1-5-18"


def command_for(script: Path) -> str:
    return f'"{pythonw()}" "{script}"'


def task_exists(name: str) -> bool:
    return run(["schtasks", "/Query", "/TN", name]).returncode == 0


def delete_task(name: str) -> bool:
    return run(["schtasks", "/Delete", "/TN", name, "/F"]).returncode == 0


def create_system_task(name: str, script: Path, schedule: list[str]) -> tuple[bool, str]:
    result = run([
        "schtasks", "/Create", "/TN", name, "/TR", command_for(script),
        *schedule, "/RU", "SYSTEM", "/RL", "HIGHEST", "/F",
    ])
    return result.returncode == 0, (result.stderr or result.stdout).strip()


def create_user_task(name: str, script: Path, user: str, schedule: list[str]) -> tuple[bool, str]:
    """Create a task running in `user`'s interactive session.

    schtasks normally wants a password for /RU; /IT plus an empty /RP asks for
    an interactive-token task instead. Windows builds differ in which spelling
    they accept, so try the variants in order of preference.
    """
    base = ["schtasks", "/Create", "/TN", name, "/TR", command_for(script), *schedule]
    variants = [
        [*base, "/RU", user, "/IT", "/RP", "", "/F"],
        [*base, "/RU", user, "/IT", "/F"],
        [*base, "/RU", user, "/RP", "", "/F"],
        [*base, "/F"],
    ]
    last = ""
    for variant in variants:
        result = run(variant)
        if result.returncode == 0:
            return True, ""
        last = (result.stderr or result.stdout).strip()
    return False, last


def secure_data_dir() -> None:
    """Admins + SYSTEM own the config; the child may only read it.

    The requests folder is the one exception - the lock screen writes unlock
    requests there as the child user.
    """
    ensure_data_dir()
    run(["icacls", str(DATA_DIR), "/inheritance:r"])
    for sid, rights in ((SID_ADMINS, "(OI)(CI)F"), (SID_SYSTEM, "(OI)(CI)F"),
                        (SID_USERS, "(OI)(CI)RX")):
        run(["icacls", str(DATA_DIR), "/grant", f"{sid}:{rights}"])
    run(["icacls", str(REQUEST_DIR), "/grant", f"{SID_USERS}:(OI)(CI)M"])


@dataclass
class StepResult:
    label: str
    ok: bool
    detail: str = ""


def install_steps(child_user: str | None = None) -> Iterator[StepResult]:
    """Do the same work as `install()`, one verified step at a time.

    Each scheduled-task step is confirmed with a `task_exists` round-trip
    query after creation - `schtasks /Create` can report success on a task
    that doesn't actually stick on some locked-down systems, and a silent
    mismatch there is exactly the kind of thing that only shows up as "it
    didn't survive the reboot" days later.
    """
    if not is_admin():
        yield StepResult("Administrator rights", False,
                         "Administrator rights are required to install.")
        return

    cfg = config.load()
    user = child_user or cfg.get("child_user") or ""
    if child_user:
        cfg["child_user"] = child_user
    config.save(cfg)

    secure_data_dir()
    yield StepResult("Secure the data folder", True)

    ok, err = create_system_task(AGENT_TASK, AGENT_SCRIPT, ["/SC", "ONSTART"])
    ok = ok and task_exists(AGENT_TASK)
    yield StepResult(AGENT_TASK, ok, "" if ok else err or "Task did not register.")

    ok, err = create_system_task(AGENT_WATCHDOG_TASK, AGENT_SCRIPT, ["/SC", "MINUTE", "/MO", "5"])
    ok = ok and task_exists(AGENT_WATCHDOG_TASK)
    yield StepResult(AGENT_WATCHDOG_TASK, ok, "" if ok else err or "Task did not register.")

    if user:
        ok, err = create_user_task(OVERLAY_TASK, OVERLAY_SCRIPT, user, ["/SC", "ONLOGON"])
        ok = ok and task_exists(OVERLAY_TASK)
        yield StepResult(OVERLAY_TASK, ok, "" if ok else err or "Task did not register.")

        ok, err = create_user_task(OVERLAY_WATCHDOG_TASK, OVERLAY_SCRIPT, user,
                                   ["/SC", "MINUTE", "/MO", "5"])
        ok = ok and task_exists(OVERLAY_WATCHDOG_TASK)
        yield StepResult(OVERLAY_WATCHDOG_TASK, ok, "" if ok else err or "Task did not register.")
    else:
        yield StepResult(OVERLAY_TASK, False,
                         "No child Windows account set - the lock screen was not installed.")
        yield StepResult(OVERLAY_WATCHDOG_TASK, False, "No child Windows account set.")

    failed_policies = browser_policy.apply()
    yield StepResult("Browser DNS policy", not failed_policies,
                     "" if not failed_policies
                     else f"Could not write policy for: {', '.join(failed_policies)}")

    run(["schtasks", "/Run", "/TN", AGENT_TASK])
    yield StepResult("Start the agent now", True)


def install(child_user: str | None = None) -> list[str]:
    """Register everything. Returns a list of human-readable problems."""
    problems = []
    for step in install_steps(child_user):
        if not step.ok:
            problems.append(f"{step.label}: {step.detail}" if step.detail else step.label)
    return problems


# ---- first-run diagnostics ---------------------------------------------------

def python_all_users() -> tuple[bool, str]:
    """Best-effort check that this interpreter is reachable by a SYSTEM task -
    a per-user install (or the Microsoft Store package) lives under a user
    profile that SYSTEM can't see, and the scheduled task silently fails."""
    exe = str(Path(sys.executable).resolve())
    lowered = exe.lower()
    if "\\windowsapps\\" in lowered:
        return False, ("Python was installed from the Microsoft Store, which installs "
                       "per-user - reinstall from python.org with \"Install for all "
                       "users\" checked.")
    if "\\appdata\\" in lowered:
        return False, (f"Python is installed under your own user profile ({exe}), "
                       "not somewhere a SYSTEM task can reach. Reinstall from "
                       "python.org with \"Install for all users\" checked.")
    return True, exe


def scheduler_service_running() -> bool:
    """Query the Task Scheduler service by its numeric state code (4 =
    running) rather than the STATE_NAME text, which `sc query` localizes on
    non-English Windows installs."""
    result = run(["sc", "query", "Schedule"])
    match = re.search(r"STATE\s*:\s*(\d+)", result.stdout)
    return bool(match) and match.group(1) == "4"


def start_scheduler_service() -> bool:
    return run(["sc", "start", "Schedule"]).returncode == 0


def list_local_users() -> list[str]:
    """Parse `net user`'s three-column account listing. Returns [] (rather
    than raising) on any unexpected format - callers fall back to manual
    entry.

    The listing has exactly one "---" separator line before the accounts on
    some Windows builds and two on others, so the end of the table is found
    by content (a blank line, another separator, or the trailing "command
    completed" line) rather than by counting separators.
    """
    result = run(["net", "user"])
    if result.returncode != 0:
        return []
    lines = result.stdout.splitlines()
    try:
        start = next(i for i, line in enumerate(lines) if line.strip().startswith("---")) + 1
    except StopIteration:
        return []
    names: list[str] = []
    for line in lines[start:]:
        stripped = line.strip()
        if not stripped or stripped.startswith("---") or "command completed" in stripped.lower():
            break
        names.extend(part for part in re.split(r"\s{2,}", stripped) if part)
    return names


def user_exists(name: str) -> bool:
    if not name.strip():
        return False
    return run(["net", "user", name]).returncode == 0


def uninstall() -> list[str]:
    """Remove tasks and undo every change to the machine."""
    problems: list[str] = []
    if not is_admin():
        return ["Administrator rights are required to uninstall."]
    for name in ALL_TASKS:
        if task_exists(name) and not delete_task(name):
            problems.append(f"Could not remove scheduled task {name}")
    try:
        hosts_block.clear()
    except OSError as exc:
        problems.append(f"Could not clean the hosts file: {exc}")
    cfg = config.load()
    firewall.remove_all([*(cfg.get("firewall_programs") or []), *_steam_paths()])
    browser_policy.revert()
    return problems


def _steam_paths() -> list[str]:
    from . import steamlib
    exe = steamlib.steam_exe()
    return [exe] if exe else []


def status() -> dict[str, bool]:
    return {name: task_exists(name) for name in ALL_TASKS}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="childcontrol-install")
    parser.add_argument("action", choices=("install", "uninstall", "status"))
    parser.add_argument("--child-user", default=None,
                        help="Windows account name the lock screen runs under")
    args = parser.parse_args(argv)

    if args.action == "status":
        for name, present in status().items():
            print(f"{'installed' if present else 'missing  '}  {name}")
        return 0

    problems = install(args.child_user) if args.action == "install" else uninstall()
    if problems:
        print(f"{args.action} finished with problems:")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print(f"{args.action} completed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
