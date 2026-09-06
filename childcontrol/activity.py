"""Tracks what happens during a restricted (Study/Locked) session.

Two things are recorded while the agent is enforcing restrictions:

  * a "session" of everything visited (allowed) and blocked (killed sites or
    apps), for the parent console's Now tab - `SessionTracker` below.
  * a raw, deduplicated-per-tick "kill events" log, purely so the lock screen
    can pop up "X has been blocked" the moment it actually happens - see
    `record_kills` / `read_kill_events`.

Website visits are read from the Windows DNS resolver cache (`ipconfig
/displaydns`). This works browser-wide with no extra software because the
installer already disables browser-side encrypted DNS (see browser_policy.py),
which forces every browser to resolve through this same system cache.
"""

from __future__ import annotations

import re
from datetime import datetime

from . import winproc
from .util import DATA_DIR, read_json, run, write_json

ACTIVITY_PATH = DATA_DIR / "activity.json"
KILL_EVENTS_PATH = DATA_DIR / "kill_events.json"

MAX_SESSION_ITEMS = 200
MAX_KILL_EVENTS = 200

_RECORD_NAME_RE = re.compile(r"Record Name[^:]*:\s*(.+)", re.IGNORECASE)

# Best-effort noise filter: infrastructure/telemetry domains that show up in
# the DNS cache constantly and would otherwise clutter the "visited" list.
_NOISE_SITE_SUFFIXES = (
    "microsoft.com", "windowsupdate.com", "msftconnecttest.com", "msftncsi.com",
    "windows.com", "office.com", "office.net", "live.com", "akamaiedge.net",
    "akamaitechnologies.com", "edgesuite.net", "1e100.net", "gstatic.com",
    "trafficmanager.net", "cloudapp.azure.com", "in-addr.arpa", "ip6.arpa",
)


def is_noise_site(domain: str) -> bool:
    if "." not in domain:
        return True
    return any(domain == s or domain.endswith("." + s) for s in _NOISE_SITE_SUFFIXES)


def snapshot_dns_cache() -> set[str]:
    """Hostnames currently in the Windows DNS resolver cache, lowercase."""
    result = run(["ipconfig", "/displaydns"], timeout=15)
    hosts = set()
    for line in (result.stdout or "").splitlines():
        match = _RECORD_NAME_RE.search(line)
        if match:
            hosts.add(match.group(1).strip().rstrip(".").lower())
    return hosts


# --- per-session visited/blocked log ----------------------------------------

def _empty_session(now: datetime, state: str) -> dict:
    return {"started": now.isoformat(timespec="seconds"), "state": state, "visited": [], "blocked": []}


def _append(bucket: list[dict], kind: str, name: str, now: datetime) -> None:
    bucket.append({"kind": kind, "name": name, "at": now.isoformat(timespec="seconds")})
    if len(bucket) > MAX_SESSION_ITEMS:
        del bucket[: len(bucket) - MAX_SESSION_ITEMS]


class SessionTracker:
    """Tracks one restricted session's worth of visited/blocked apps and sites."""

    def __init__(self) -> None:
        data = read_json(ACTIVITY_PATH, default=None) or {}
        self.current: dict | None = data.get("current")
        self.last: dict | None = data.get("last")
        self._seen_apps: set[str] = set()
        self._seen_sites: set[str] = set()
        self._reindex()
        # A freshly (re)started agent - e.g. after the watchdog restarts a
        # crashed one mid-session - has no memory of what was already running
        # before it started watching. Its first observation is treated the
        # same as a session start: seed, don't log, so a restart never dumps
        # the whole process list into "visited" as if it just appeared.
        self._primed = self.current is None

    def _reindex(self) -> None:
        self._seen_apps.clear()
        self._seen_sites.clear()
        if not self.current:
            return
        for bucket in ("visited", "blocked"):
            for entry in self.current.get(bucket, []):
                seen = self._seen_apps if entry["kind"] == "app" else self._seen_sites
                seen.add(entry["name"].lower())

    def update(
        self,
        restricted: bool,
        now: datetime,
        state: str,
        stopped: list[winproc.Process],
        others: list[winproc.Process],
        blocked_sites_hit: list[str],
        visited_sites: list[str],
    ) -> bool:
        """Feed one tick's enforcement results in. Returns True if anything changed."""
        changed = False
        # True exactly on the one tick where we start paying attention to a
        # restricted period: a brand-new session, or the first tick after
        # this tracker was (re)constructed into an already-ongoing one.
        first_observation = not self._primed
        self._primed = True

        if restricted and self.current is None:
            self.current = _empty_session(now, state)
            first_observation = True
            changed = True
        elif not restricted and self.current is not None:
            self.current["ended"] = now.isoformat(timespec="seconds")
            self.last = self.current
            self.current = None
            self._seen_apps.clear()
            self._seen_sites.clear()
            changed = True

        if restricted and self.current is not None:
            if first_observation:
                # Apps/sites already active the moment we start observing are
                # pre-existing background noise, not something "visited" -
                # seed them as already-seen so only genuinely new activity
                # from here on gets logged as visited.
                self._seen_apps.update(p.lname for p in others)
                self._seen_sites.update(d.lower() for d in visited_sites)
            else:
                for process in others:
                    key = process.lname
                    if key not in self._seen_apps:
                        self._seen_apps.add(key)
                        _append(self.current["visited"], "app", process.name, now)
                        changed = True
                for domain in visited_sites:
                    key = domain.lower()
                    if key not in self._seen_sites:
                        self._seen_sites.add(key)
                        _append(self.current["visited"], "site", domain, now)
                        changed = True

            # A kill or a blocked-site hit is always worth logging, whether
            # or not this is the first tick we've observed.
            for process in stopped:
                key = process.lname
                if key not in self._seen_apps:
                    self._seen_apps.add(key)
                    _append(self.current["blocked"], "app", process.name, now)
                    changed = True
            for domain in blocked_sites_hit:
                key = domain.lower()
                if key not in self._seen_sites:
                    self._seen_sites.add(key)
                    _append(self.current["blocked"], "site", domain, now)
                    changed = True

        if changed:
            self.save()
        return changed

    def save(self) -> None:
        write_json(ACTIVITY_PATH, {"current": self.current, "last": self.last})


def read_session_activity() -> dict:
    return read_json(ACTIVITY_PATH, default=None) or {"current": None, "last": None}


# --- raw kill-event log, for the lock screen's "X has been blocked" toast --

def record_kills(processes: list[winproc.Process], now: datetime) -> None:
    if not processes:
        return
    data = read_json(KILL_EVENTS_PATH, default=None) or {"events": [], "next_seq": 1}
    events = data.get("events", [])
    seq = int(data.get("next_seq", 1))
    for process in processes:
        events.append({"seq": seq, "at": now.isoformat(timespec="seconds"), "name": process.name})
        seq += 1
    if len(events) > MAX_KILL_EVENTS:
        del events[: len(events) - MAX_KILL_EVENTS]
    write_json(KILL_EVENTS_PATH, {"events": events, "next_seq": seq})


def read_kill_events() -> dict:
    return read_json(KILL_EVENTS_PATH, default=None) or {"events": [], "next_seq": 1}
