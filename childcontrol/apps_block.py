"""Decide which running processes are not allowed right now, and stop them."""

from __future__ import annotations

import hashlib
import os
from datetime import datetime

from . import steamlib, winproc
from .util import HASHES_PATH, read_json, setup_logging, write_json

log = setup_logging("apps_block")

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


_HASH_CHUNK = 1 << 20
# A running process's own executable file can't change out from under it, so
# cache by path for this agent run rather than re-hashing every tick.
_path_hash_cache: dict[str, str] = {}


def _file_hash(path: str) -> str | None:
    normalized = os.path.normcase(os.path.normpath(path))
    cached = _path_hash_cache.get(normalized)
    if cached:
        return cached
    try:
        digest = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(_HASH_CHUNK), b""):
                digest.update(chunk)
    except OSError:
        return None
    value = digest.hexdigest()
    _path_hash_cache[normalized] = value
    return value


def _load_fingerprints() -> dict[str, dict]:
    return read_json(HASHES_PATH, default={}) or {}


def _remember_fingerprint(path: str, label: str, fingerprints: dict[str, dict]) -> None:
    """Record this file's content hash so a later rename of the same file
    still matches - called for everything actually stopped, regardless of
    whether a name, a folder, or an earlier fingerprint is what caught it
    this time."""
    if not path:
        return
    file_hash = _file_hash(path)
    if not file_hash or file_hash in fingerprints:
        return
    fingerprints[file_hash] = {
        "name": label,
        "first_seen": datetime.now().isoformat(timespec="seconds"),
    }
    try:
        write_json(HASHES_PATH, fingerprints)
    except OSError:
        log.warning("could not persist blocked-file fingerprint for %s", label)


def is_blocked(
    process: winproc.Process,
    names: set[str],
    folders: list[str],
    fingerprints: dict[str, dict] | None = None,
) -> bool:
    if process.lname in PROTECTED:
        return False
    if process.lname in names:
        return True
    if not process.path:
        return False
    normalized = os.path.normcase(os.path.normpath(process.path))
    if any(normalized.startswith(folder) for folder in folders):
        return True
    if fingerprints:
        file_hash = _file_hash(process.path)
        if file_hash and file_hash in fingerprints:
            return True
    return False


def classify(
    cfg: dict,
    processes: list[winproc.Process],
    fingerprints: dict[str, dict] | None = None,
) -> tuple[list[winproc.Process], list[winproc.Process]]:
    """Split running processes into (blocked matches, everything else worth noting).

    "Everything else" excludes protected OS processes, so the second list is a
    reasonable proxy for "programs the child is actually using right now".
    """
    names = blocked_names(cfg)
    folders = blocked_folders(cfg)
    if fingerprints is None:
        fingerprints = _load_fingerprints()
    own_pid = os.getpid()
    blocked, others = [], []
    for p in processes:
        if p.pid in (0, 4, own_pid):
            continue
        if is_blocked(p, names, folders, fingerprints):
            blocked.append(p)
        elif p.lname not in PROTECTED:
            others.append(p)
    return blocked, others


def find_blocked(cfg: dict, processes: list[winproc.Process] | None = None) -> list[winproc.Process]:
    if processes is None:
        processes = winproc.list_processes()
    return classify(cfg, processes)[0]


def enforce(
    cfg: dict, processes: list[winproc.Process] | None = None
) -> tuple[list[winproc.Process], list[winproc.Process]]:
    """Terminate everything currently blocked.

    Returns (stopped, others) - the processes actually terminated, and every
    other non-protected process that was left running, for activity tracking.
    Anything actually stopped also gets fingerprinted by file content, so a
    later rename of the same file still gets caught.
    """
    if processes is None:
        processes = winproc.list_processes()
    fingerprints = _load_fingerprints()
    blocked, others = classify(cfg, processes, fingerprints)
    stopped = [p for p in blocked if winproc.terminate(p.pid)]
    for p in stopped:
        _remember_fingerprint(p.path, p.name, fingerprints)
    return stopped, others
