"""Configuration file handling: defaults, password hashing and override state."""

from __future__ import annotations

import hashlib
import hmac
import os
from datetime import datetime, timedelta

from . import schedule as sched
from .util import CONFIG_PATH, ensure_data_dir, read_json, write_json

PBKDF2_ROUNDS = 240_000

DEFAULT_BLOCKED_APPS = [
    "steam.exe",
    "steamwebhelper.exe",
    "EpicGamesLauncher.exe",
    "Battle.net.exe",
    "RiotClientServices.exe",
    "LeagueClient.exe",
    "GalaxyClient.exe",
    "Origin.exe",
    "EADesktop.exe",
    "UbisoftConnect.exe",
    "upc.exe",
    "RobloxPlayerBeta.exe",
    "Minecraft.exe",
    "MinecraftLauncher.exe",
    "Discord.exe",
    "vlc.exe",
]

DEFAULT_BLOCKED_SITES = [
    "youtube.com",
    "tiktok.com",
    "instagram.com",
    "facebook.com",
    "twitter.com",
    "x.com",
    "reddit.com",
    "twitch.tv",
    "netflix.com",
    "roblox.com",
    "discord.com",
    "9gag.com",
    "snapchat.com",
    "store.steampowered.com",
    "steamcommunity.com",
    "crazygames.com",
    "poki.com",
]


def default_schedule() -> list[str]:
    """Weekdays: study 16:00-19:00, asleep 22:00-07:00. Weekends: asleep only."""
    week = []
    for day in range(7):
        row = [sched.FREE] * sched.SLOTS_PER_DAY
        for slot in range(sched.SLOTS_PER_DAY):
            minutes = slot * sched.SLOT_MINUTES
            if minutes < 7 * 60 or minutes >= 22 * 60:
                row[slot] = sched.LOCKED
            elif day < 5 and 16 * 60 <= minutes < 19 * 60:
                row[slot] = sched.STUDY
        week.append("".join(row))
    return week


def defaults() -> dict:
    return {
        "version": 1,
        "password": None,
        "child_user": os.environ.get("USERNAME", ""),
        "schedule": default_schedule(),
        "blocked_apps": list(DEFAULT_BLOCKED_APPS),
        "blocked_folders": [],
        "block_steam_library": True,
        "blocked_sites": list(DEFAULT_BLOCKED_SITES),
        "use_firewall": True,
        "poll_seconds": 5,
        "warn_minutes": 5,
        "lock_message": "Time's up for now. The computer unlocks again automatically.",
        "override": None,
    }


def load() -> dict:
    cfg = defaults()
    stored = read_json(CONFIG_PATH, default=None)
    if isinstance(stored, dict):
        cfg.update(stored)
    cfg["schedule"] = sched.normalize(cfg.get("schedule"))
    return cfg


def save(cfg: dict) -> None:
    ensure_data_dir()
    write_json(CONFIG_PATH, cfg)


def exists() -> bool:
    return CONFIG_PATH.exists()


# --- password ---------------------------------------------------------------

def hash_password(password: str, salt: bytes | None = None) -> dict:
    salt = salt or os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ROUNDS)
    return {"salt": salt.hex(), "hash": digest.hex(), "rounds": PBKDF2_ROUNDS}


def check_password(record: dict | None, password: str) -> bool:
    if not record:
        return False
    try:
        salt = bytes.fromhex(record["salt"])
        rounds = int(record.get("rounds", PBKDF2_ROUNDS))
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, rounds)
    except (KeyError, ValueError):
        return False
    return hmac.compare_digest(digest.hex(), record.get("hash", ""))


# --- overrides --------------------------------------------------------------

def set_override(cfg: dict, state: str, minutes: int) -> None:
    until = datetime.now() + timedelta(minutes=minutes)
    cfg["override"] = {"state": state, "until": until.isoformat(timespec="seconds")}


def clear_override(cfg: dict) -> None:
    cfg["override"] = None


def override_active(cfg: dict, now: datetime | None = None) -> dict | None:
    now = now or datetime.now()
    override = cfg.get("override")
    if not isinstance(override, dict):
        return None
    try:
        until = datetime.fromisoformat(override["until"])
    except (KeyError, ValueError):
        return None
    if now >= until or override.get("state") not in sched.STATES:
        return None
    return {"state": override["state"], "until": until}


def effective_state(cfg: dict, now: datetime | None = None) -> tuple[str, dict | None]:
    """Current state, plus the override responsible for it (or None)."""
    now = now or datetime.now()
    override = override_active(cfg, now)
    if override:
        return override["state"], override
    return sched.state_at(cfg["schedule"], now), None
