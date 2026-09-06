"""Decide which running processes are not allowed right now, and stop them."""

from __future__ import annotations

import os

from . import steamlib, winproc

# Never terminated, even if someone types them into the blocked list by mistake.
PROTECTED = {
    "",
    "[system process]",
    "system",
    "registry",
    "memory compression",
    "smss.exe",
    "csrss.exe",
    "wininit.exe",
    "winlogon.exe",
    "services.exe",
    "lsass.exe",
    "svchost.exe",
    "explorer.exe",
    "dwm.exe",
    "fontdrvhost.exe",
    "sihost.exe",
    "ctfmon.exe",
    "taskhostw.exe",
    "runtimebroker.exe",
    "searchhost.exe",
    "startmenuexperiencehost.exe",
    "shellexperiencehost.exe",
    "logonui.exe",
    "userinit.exe",
    "dllhost.exe",
    "conhost.exe",
    "audiodg.exe",
    "spoolsv.exe",
    "lsaiso.exe",
    "wudfhost.exe",
}


def _norm_dir(path: str) -> str:
    return os.path.normcase(os.path.normpath(path)).rstrip(os.sep) + os.sep


def blocked_folders(cfg: dict) -> list[str]:
    folders = list(cfg.get("blocked_folders") or [])
    if cfg.get("block_steam_library", True):
        folders.extend(steamlib.game_folders())
    return [_norm_dir(f) for f in folders if f]


def blocked_names(cfg: dict) -> set[str]:
    return {
        name.strip().lower()
        for name in (cfg.get("blocked_apps") or [])
        if name.strip() and name.strip().lower() not in PROTECTED
    }


def firewall_programs(cfg: dict) -> list[str]:
    """Absolute paths worth a firewall rule (only Steam can be located reliably)."""
    programs = []
    exe = steamlib.steam_exe()
    if exe and "steam.exe" in blocked_names(cfg):
        programs.append(exe)
    for extra in cfg.get("firewall_programs") or []:
        programs.append(extra)
    return programs


def is_blocked(process: winproc.Process, names: set[str], folders: list[str]) -> bool:
    if process.lname in PROTECTED:
        return False
    if process.lname in names:
        return True
    if process.path:
        normalized = os.path.normcase(os.path.normpath(process.path))
        return any(normalized.startswith(folder) for folder in folders)
    return False


def find_blocked(cfg: dict) -> list[winproc.Process]:
    names = blocked_names(cfg)
    folders = blocked_folders(cfg)
    if not names and not folders:
        return []
    own_pid = os.getpid()
    return [
        p for p in winproc.list_processes()
        if p.pid not in (0, 4, own_pid) and is_blocked(p, names, folders)
    ]


def enforce(cfg: dict) -> list[str]:
    """Terminate everything currently blocked. Returns the names it stopped."""
    stopped = []
    for process in find_blocked(cfg):
        if winproc.terminate(process.pid):
            stopped.append(f"{process.name} (pid {process.pid})")
    return stopped
