"""Windows Firewall rules that cut a blocked program off from the network.

Killing a process handles the obvious case; a firewall rule additionally stops
Steam from downloading or phoning home while it keeps restarting itself.
All rules share one group so they can be toggled with a single command.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from .util import run

GROUP = "ChildControl"


def rule_name(program: str) -> str:
    return f"{GROUP} - {Path(program).name}"


def _netsh(args: list[str]):
    # A hung netsh must never stall the agent's enforcement loop: treat a
    # timeout as a failed command instead of letting it raise.
    try:
        return run(["netsh", "advfirewall", "firewall", *args], timeout=10)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(args, returncode=1, stdout="", stderr="timeout")


def delete_rule(program: str) -> None:
    _netsh(["delete", "rule", f"name={rule_name(program)}"])


def ensure_rules(programs: list[str], enabled: bool) -> bool:
    """Recreate block rules for `programs` (absolute paths). False if netsh refused."""
    ok = True
    for program in programs:
        delete_rule(program)
        if not Path(program).exists():
            continue
        for direction in ("out", "in"):
            result = _netsh([
                "add", "rule",
                f"name={rule_name(program)}",
                f"dir={direction}",
                "action=block",
                f"program={program}",
                f"group={GROUP}",
                f"enable={'yes' if enabled else 'no'}",
            ])
            ok = ok and result.returncode == 0
            if result.stderr == "timeout":
                return False
    return ok


def set_enabled(enabled: bool) -> bool:
    result = _netsh([
        "set", "rule", f"group={GROUP}", "new", f"enable={'yes' if enabled else 'no'}"
    ])
    return result.returncode == 0


def remove_all(programs: list[str]) -> None:
    for program in programs:
        delete_rule(program)
