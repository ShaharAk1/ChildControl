"""Locate Steam and its game library folders, so every game can be blocked at once."""

from __future__ import annotations

import re
import winreg
from pathlib import Path

_PATH_RE = re.compile(r'"path"\s*"([^"]+)"', re.IGNORECASE)

_REG_LOCATIONS = [
    (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Valve\Steam", "SteamPath"),
    (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
    (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", "InstallPath"),
]


def steam_root() -> Path | None:
    for hive, subkey, value in _REG_LOCATIONS:
        try:
            with winreg.OpenKey(hive, subkey) as key:
                raw, _ = winreg.QueryValueEx(key, value)
        except OSError:
            continue
        if raw:
            path = Path(str(raw).replace("/", "\\"))
            if path.exists():
                return path
    return None


def steam_exe() -> str | None:
    root = steam_root()
    if root and (root / "steam.exe").exists():
        return str(root / "steam.exe")
    return None


def game_folders() -> list[str]:
    r"""Every `steamapps\common` directory across all configured Steam libraries."""
    root = steam_root()
    if not root:
        return []
    roots = {root}
    vdf = root / "steamapps" / "libraryfolders.vdf"
    try:
        text = vdf.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        text = ""
    for match in _PATH_RE.findall(text):
        candidate = Path(match.replace("\\\\", "\\"))
        if candidate.exists():
            roots.add(candidate)
    folders = []
    for base in sorted(roots):
        common = base / "steamapps" / "common"
        if common.exists():
            folders.append(str(common))
    return folders
