"""The background enforcer.

Runs as SYSTEM from a scheduled task. Every few seconds it works out which
state the schedule (or an override) puts the machine in, applies the matching
restrictions, publishes status.json for the overlay/console, and handles
unlock requests coming from the lock screen.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime

from . import activity, apps_block, browser_policy, config, firewall, hosts_block, winproc
from . import schedule as sched
from .util import (
    REQUEST_DIR,
    STATUS_PATH,
    ensure_data_dir,
    is_admin,
    read_json,
    setup_logging,
    single_instance,
    write_json,
)

log = setup_logging("agent")

MUTEX_NAME = r"Global\ChildControlAgent"


class Enforcer:
    def __init__(self) -> None:
        self._firewall_programs: list[str] | None = None
        self._firewall_enabled: bool | None = None
        self._last_state: str | None = None
        self._session = activity.SessionTracker()

    def tick(self) -> dict:
        cfg = config.load()
        now = datetime.now()
        state, override = config.effective_state(cfg, now)
        restricted = state in (sched.STUDY, sched.LOCKED)

        if state != self._last_state:
            log.info("state -> %s%s", sched.STATE_NAMES[state], " (override)" if override else "")
            self._last_state = state

        self._apply_sites(cfg, restricted)
        self._apply_firewall(cfg, restricted)
        stopped, others = self._apply_processes(cfg, restricted)
        visited_sites, blocked_sites_hit = self._scan_sites(cfg, restricted)

        self._session.update(restricted, now, state, stopped, others, blocked_sites_hit, visited_sites)
        if stopped:
            activity.record_kills(stopped, now)

        status = self._status(cfg, now, state, override, stopped)
        write_json(STATUS_PATH, status)
        return status

    def _apply_sites(self, cfg: dict, restricted: bool) -> None:
        domains = list(cfg.get("blocked_sites") or []) if restricted else []
        try:
            if hosts_block.apply(domains):
                log.info("hosts file updated (%d domains)", len(domains))
        except PermissionError:
            log.warning("cannot write hosts file - agent needs administrator rights")
        except OSError as exc:
            log.warning("hosts file update failed: %s", exc)

    def _apply_firewall(self, cfg: dict, restricted: bool) -> None:
        if not cfg.get("use_firewall", True):
            if self._firewall_programs:
                firewall.remove_all(self._firewall_programs)
                self._firewall_programs, self._firewall_enabled = None, None
            return
        programs = apps_block.firewall_programs(cfg)
        if programs != self._firewall_programs:
            ok = firewall.ensure_rules(programs, restricted)
            self._firewall_programs, self._firewall_enabled = programs, restricted
            if ok:
                log.info("firewall rules rebuilt for %d program(s)", len(programs))
            else:
                log.warning("firewall rules could not be created - needs administrator rights")
        elif restricted != self._firewall_enabled:
            if firewall.set_enabled(restricted):
                log.info("firewall rules %s", "enabled" if restricted else "disabled")
            self._firewall_enabled = restricted

    def _apply_processes(
        self, cfg: dict, restricted: bool
    ) -> tuple[list[winproc.Process], list[winproc.Process]]:
        if not restricted:
            return [], []
        stopped, others = apps_block.enforce(cfg)
        for process in stopped:
            log.info("stopped %s (pid %d)", process.name, process.pid)
        return stopped, others

    def _scan_sites(self, cfg: dict, restricted: bool) -> tuple[list[str], list[str]]:
        """Which domains showed up in the DNS cache: (visited, blocked-attempts)."""
        if not restricted:
            return [], []
        try:
            hostnames = activity.snapshot_dns_cache()
        except Exception:
            log.exception("dns cache scan failed")
            return [], []
        blocked_set = {hosts_block.normalize_domain(d) for d in cfg.get("blocked_sites", [])}
        visited, blocked_hits = [], []
        for host in hostnames:
            domain = hosts_block.normalize_domain(host)
            if not domain:
                continue
            if domain in blocked_set:
                blocked_hits.append(domain)
            elif not activity.is_noise_site(domain):
                visited.append(domain)
        return visited, blocked_hits

    def _status(self, cfg, now, state, override, stopped) -> dict:
        upcoming = sched.next_transition(cfg["schedule"], now)
        next_free = sched.next_free(cfg["schedule"], now)
        password = cfg.get("password") or {}
        return {
            "state": state,
            "state_name": sched.STATE_NAMES[state],
            "updated": now.isoformat(timespec="seconds"),
            "override": (
                {"state": override["state"], "until": override["until"].isoformat(timespec="seconds")}
                if override else None
            ),
            "next_change": (
                {"at": upcoming[0].isoformat(timespec="seconds"), "state": upcoming[1]}
                if upcoming else None
            ),
            "next_free": next_free.isoformat(timespec="seconds") if next_free else None,
            "lock_message": cfg.get("lock_message", ""),
            "last_stopped": [f"{p.name} (pid {p.pid})" for p in stopped],
            "agent_pid": os.getpid(),
            "agent_admin": is_admin(),
            "password_salt": password.get("salt"),
            "password_rounds": password.get("rounds"),
        }

    # --- unlock requests coming from the lock screen ------------------------

    def handle_requests(self) -> None:
        if not REQUEST_DIR.exists():
            return
        for path in sorted(REQUEST_DIR.glob("*.json")):
            request = read_json(path, default=None)
            try:
                path.unlink()
            except OSError:
                pass
            if not isinstance(request, dict):
                continue
            self._handle_request(request)

    def _handle_request(self, request: dict) -> None:
        cfg = config.load()
        stored = cfg.get("password") or {}
        digest = str(request.get("digest", ""))
        ok = bool(stored.get("hash")) and digest and digest == stored["hash"]
        result_path = REQUEST_DIR / f"{request.get('id', 'unknown')}.result.json"
        if ok:
            minutes = max(1, min(int(request.get("minutes", 30)), 12 * 60))
            config.set_override(cfg, sched.FREE, minutes)
            config.save(cfg)
            log.info("unlock request accepted for %d minutes", minutes)
        else:
            log.warning("unlock request rejected (bad password)")
        write_json(result_path, {"ok": ok, "at": datetime.now().isoformat(timespec="seconds")})


def run(poll_seconds: float | None = None) -> None:
    ensure_data_dir()
    if not single_instance(MUTEX_NAME):
        log.info("another agent instance is already running - exiting")
        return
    if not is_admin():
        log.warning("agent is not elevated; hosts/firewall changes will fail")
    log.info("agent started (pid %d, admin=%s)", os.getpid(), is_admin())
    enforcer = Enforcer()
    while True:
        try:
            enforcer.handle_requests()
            status = enforcer.tick()
            delay = poll_seconds or config.load().get("poll_seconds", 5)
        except Exception:
            log.exception("enforcement pass failed")
            delay = 10
        time.sleep(max(1.0, float(delay)))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="childcontrol-agent")
    parser.add_argument("--once", action="store_true", help="run a single pass and print the status")
    parser.add_argument("--poll", type=float, default=None, help="seconds between passes")
    parser.add_argument("--apply-browser-policy", action="store_true",
                        help="disable browser DNS-over-HTTPS, then exit")
    args = parser.parse_args(argv)

    if args.apply_browser_policy:
        failed = browser_policy.apply()
        print("browser policy applied" if not failed else f"failed: {failed}")
        return 0 if not failed else 1
    if args.once:
        enforcer = Enforcer()
        enforcer.handle_requests()
        status = enforcer.tick()
        for key in ("state_name", "override", "next_change", "last_stopped", "agent_admin"):
            print(f"{key}: {status[key]}")
        return 0
    run(args.poll)
    return 0


if __name__ == "__main__":
    sys.exit(main())
