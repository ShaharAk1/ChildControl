"""Website blocking by rewriting the Windows hosts file.

Only the region between our two markers is ever touched, so hand-written
entries in the hosts file survive untouched.
"""

from __future__ import annotations

import os
from pathlib import Path

from .util import run

BEGIN = "# >>> ChildControl >>>"
END = "# <<< ChildControl <<<"

HOSTS_PATH = Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32" / "drivers" / "etc" / "hosts"

SINKHOLE_V4 = "0.0.0.0"
SINKHOLE_V6 = "::1"


def normalize_domain(raw: str) -> str:
    domain = raw.strip().lower()
    for prefix in ("http://", "https://"):
        if domain.startswith(prefix):
            domain = domain[len(prefix):]
    domain = domain.split("/", 1)[0].split("?", 1)[0]
    if domain.startswith("www."):
        domain = domain[4:]
    return domain


def _read() -> str:
    try:
        return HOSTS_PATH.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def _strip_managed(text: str) -> str:
    lines = text.splitlines()
    out, inside = [], False
    for line in lines:
        stripped = line.strip()
        if stripped == BEGIN:
            inside = True
            continue
        if stripped == END:
            inside = False
            continue
        if not inside:
            out.append(line)
    while out and not out[-1].strip():
        out.pop()
    return "\n".join(out)


def _build(domains: list[str]) -> str:
    seen, entries = set(), []
    for raw in domains:
        domain = normalize_domain(raw)
        if not domain or domain in seen:
            continue
        seen.add(domain)
        entries.append(f"{SINKHOLE_V4} {domain}")
        entries.append(f"{SINKHOLE_V4} www.{domain}")
        entries.append(f"{SINKHOLE_V6} {domain}")
        entries.append(f"{SINKHOLE_V6} www.{domain}")
    if not entries:
        return ""
    body = "\n".join(entries)
    return f"{BEGIN}\n# Managed automatically - do not edit by hand.\n{body}\n{END}"


def desired_text(domains: list[str]) -> str:
    base = _strip_managed(_read())
    block = _build(domains)
    if not block:
        return base + "\n"
    return f"{base}\n\n{block}\n"


def apply(domains: list[str]) -> bool:
    """Write the block list into the hosts file. Returns True if anything changed."""
    wanted = desired_text(domains)
    if _read() == wanted:
        return False
    HOSTS_PATH.write_text(wanted, encoding="utf-8")
    flush_dns()
    return True


def clear() -> bool:
    return apply([])


def flush_dns() -> None:
    try:
        run(["ipconfig", "/flushdns"], timeout=15)
    except Exception:
        pass
