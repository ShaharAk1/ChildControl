"""Shared paths, logging, JSON storage and Windows privilege helpers."""

from __future__ import annotations

import ctypes
import json
import logging
import os
import subprocess
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

APP_NAME = "ChildControl"
DATA_DIR = Path(os.environ.get("ProgramData", r"C:\ProgramData")) / APP_NAME
CONFIG_PATH = DATA_DIR / "config.json"
STATUS_PATH = DATA_DIR / "status.json"
REQUEST_DIR = DATA_DIR / "requests"
LOG_PATH = DATA_DIR / "childcontrol.log"

REPO_DIR = Path(__file__).resolve().parent.parent

CREATE_NO_WINDOW = 0x08000000


def pythonw() -> str:
    """Path to pythonw.exe for the interpreter running us (no console window)."""
    exe = Path(sys.executable)
    candidate = exe.with_name("pythonw.exe")
    return str(candidate if candidate.exists() else exe)


_ERROR_ALREADY_EXISTS = 183
_held_mutexes: list[int] = []


def single_instance(name: str) -> bool:
    """Take a named mutex, so a watchdog relaunch exits instead of duplicating.

    The handle is kept alive for the life of the process on purpose - Windows
    releases the mutex when the process ends.
    """
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.CreateMutexW(None, False, name)
    if not handle:
        return False
    _held_mutexes.append(handle)
    return ctypes.get_last_error() != _ERROR_ALREADY_EXISTS


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def relaunch_as_admin(script: str, args: list[str] | None = None) -> bool:
    """Re-launch `script` elevated. Returns True if the UAC prompt was accepted."""
    parts = [script, *(args or [])]
    params = " ".join(f'"{p}"' for p in parts)
    rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", pythonw(), params, None, 1)
    return int(rc) > 32


def run(cmd: list[str], timeout: int = 30) -> subprocess.CompletedProcess:
    """Run a console command without flashing a window."""
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        creationflags=CREATE_NO_WINDOW,
    )


def ensure_data_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    REQUEST_DIR.mkdir(parents=True, exist_ok=True)


def read_json(path: Path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def write_json(path: Path, data) -> None:
    """Atomic write so a reader never sees a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    os.replace(tmp, path)


def setup_logging(name: str) -> logging.Logger:
    ensure_data_dir()
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    try:
        handler = RotatingFileHandler(LOG_PATH, maxBytes=512_000, backupCount=2, encoding="utf-8")
    except OSError:
        handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    return logger
