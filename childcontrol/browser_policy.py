"""Turn off browser DNS-over-HTTPS via machine policy.

Chrome, Edge and Firefox can resolve names through their own encrypted DNS,
which bypasses the hosts file entirely. Without this, site blocking silently
stops working in exactly the browsers a teenager actually uses.
"""

from __future__ import annotations

import winreg

# (hive, key path, value name, type, enabled value)
POLICIES = [
    (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Policies\Google\Chrome",
     "DnsOverHttpsMode", winreg.REG_SZ, "off"),
    (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Policies\Microsoft\Edge",
     "DnsOverHttpsMode", winreg.REG_SZ, "off"),
    (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Policies\BraveSoftware\Brave",
     "DnsOverHttpsMode", winreg.REG_SZ, "off"),
    (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Policies\Mozilla\Firefox\DNSOverHTTPS",
     "Enabled", winreg.REG_DWORD, 0),
    (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Policies\Mozilla\Firefox\DNSOverHTTPS",
     "Locked", winreg.REG_DWORD, 1),
]


def apply() -> list[str]:
    """Write the policies. Returns the key paths that failed (needs admin)."""
    failed = []
    for hive, path, name, kind, value in POLICIES:
        try:
            with winreg.CreateKeyEx(hive, path, 0, winreg.KEY_SET_VALUE) as key:
                winreg.SetValueEx(key, name, 0, kind, value)
        except OSError:
            failed.append(rf"{path}\{name}")
    return failed


def revert() -> None:
    for hive, path, name, _kind, _value in POLICIES:
        try:
            with winreg.OpenKey(hive, path, 0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, name)
        except OSError:
            pass


def status() -> dict[str, object]:
    applied = {}
    for hive, path, name, _kind, _value in POLICIES:
        try:
            with winreg.OpenKey(hive, path) as key:
                applied[rf"{path}\{name}"], _ = winreg.QueryValueEx(key, name)
        except OSError:
            applied[rf"{path}\{name}"] = None
    return applied
