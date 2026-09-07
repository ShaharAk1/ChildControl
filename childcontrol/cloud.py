"""Optional remote control: pair this device with the Firebase-backed
website and pull schedule/override changes it pushes.

Design constraint: the enforcement loop in agent.py must never depend on
network availability - `sync_once` is called from a slow, separate step
there (like `handle_requests`, not like `tick`), and every failure here (no
internet, Firebase down, malformed response, not yet paired) is caught and
turned into "nothing to sync this cycle," never an exception that reaches
the caller.

The device gets its own Firebase Auth account, created automatically the
first time it's paired, and only ever reads/writes its own Firestore
document. What actually stops a device from reading or altering anyone
else's data is the Firestore Security Rules deployed on the backend, not
this code - see the project plan for the exact rules text.

No third-party packages: every call here is a plain HTTPS/JSON request via
`urllib.request`, matching this project's dependency-free stdlib-only style.
"""

from __future__ import annotations

import json
import secrets
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

from .util import CLOUD_PATH, STATUS_PATH, ensure_data_dir, read_json, setup_logging, write_json

log = setup_logging("cloud")

# Public by design - a Firebase Web API key isn't a secret. Access control is
# enforced by the Firestore Security Rules on the backend, not by hiding
# this value (see the project plan for the deployed rules).
FIREBASE_API_KEY = "AIzaSyBaE0ixwC5MArO1EqHbfbegnJxx19BA1oE"
FIREBASE_PROJECT_ID = "childcontrol-fae4f"

_AUTH_BASE = "https://identitytoolkit.googleapis.com/v1"
_TOKEN_URL = "https://securetoken.googleapis.com/v1/token"
_FIRESTORE_BASE = (
    f"https://firestore.googleapis.com/v1/projects/{FIREBASE_PROJECT_ID}/databases/(default)/documents"
)
_TIMEOUT = 10
_SYNC_INTERVAL_SECONDS = 60
_PAIRING_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I
_NULL = {"nullValue": None}


# --- local state (childcontrol/cloud.json) ------------------------------------

def _load() -> dict:
    return read_json(CLOUD_PATH, default={}) or {}


def _save(data: dict) -> None:
    ensure_data_dir()
    write_json(CLOUD_PATH, data)


def _secure_cloud_file() -> None:
    """Lock the credentials file down immediately after it first exists -
    unlike config.json, the child's account gets no access to this at all."""
    try:
        from . import install
        install.secure_cloud_file()
    except Exception:
        log.exception("could not restrict permissions on cloud.json")


# --- plain HTTP helpers --------------------------------------------------------

def _post_json(url: str, body: dict) -> dict:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _post_form(url: str, fields: dict) -> dict:
    data = "&".join(f"{k}={v}" for k, v in fields.items()).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _firestore_request(method: str, url: str, id_token: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json",
                                          "Authorization": f"Bearer {id_token}"})
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
        raw = resp.read()
        return json.loads(raw.decode("utf-8")) if raw else {}


def _firestore_get(collection: str, doc_id: str, id_token: str) -> dict | None:
    try:
        return _firestore_request("GET", f"{_FIRESTORE_BASE}/{collection}/{doc_id}", id_token)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise


def _firestore_create(collection: str, doc_id: str, fields: dict, id_token: str) -> None:
    url = f"{_FIRESTORE_BASE}/{collection}?documentId={doc_id}"
    _firestore_request("POST", url, id_token, {"fields": fields})


def _firestore_patch(collection: str, doc_id: str, fields: dict, id_token: str) -> None:
    mask = "&".join(f"updateMask.fieldPaths={name}" for name in fields)
    url = f"{_FIRESTORE_BASE}/{collection}/{doc_id}?{mask}"
    _firestore_request("PATCH", url, id_token, {"fields": fields})


# --- Firestore value (en/de)coding, scoped to just what this module uses -----

def _v_str(value: str) -> dict:
    return {"stringValue": value}


def _v_timestamp(iso: str) -> dict:
    return {"timestampValue": iso}


def _v_map(fields: dict) -> dict:
    return {"mapValue": {"fields": fields}}


def _decode(value: dict):
    if "stringValue" in value:
        return value["stringValue"]
    if "nullValue" in value:
        return None
    if "timestampValue" in value:
        return value["timestampValue"]
    if "booleanValue" in value:
        return value["booleanValue"]
    if "mapValue" in value:
        return {k: _decode(v) for k, v in value.get("mapValue", {}).get("fields", {}).items()}
    if "arrayValue" in value:
        return [_decode(v) for v in value.get("arrayValue", {}).get("values", [])]
    return None


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


# --- device identity (its own Firebase Auth account) --------------------------

def _signup_device() -> dict:
    email = f"device-{secrets.token_hex(8)}@childcontrol.local"
    password = secrets.token_urlsafe(24)
    result = _post_json(f"{_AUTH_BASE}/accounts:signUp?key={FIREBASE_API_KEY}",
                        {"email": email, "password": password, "returnSecureToken": True})
    return {
        "uid": result["localId"],
        "email": email,
        "password": password,
        "id_token": result["idToken"],
        "refresh_token": result["refreshToken"],
        "id_token_expires": time.time() + int(result.get("expiresIn", 3600)) - 60,
    }


def _ensure_identity() -> dict:
    data = _load()
    identity = data.get("identity")
    if identity:
        return identity
    identity = _signup_device()
    data["identity"] = identity
    _save(data)
    _secure_cloud_file()
    log.info("registered new device identity %s", identity["uid"])
    return identity


def _ensure_token() -> str:
    data = _load()
    identity = data.get("identity") or _ensure_identity()
    if time.time() < identity.get("id_token_expires", 0):
        return identity["id_token"]

    result = _post_form(f"{_TOKEN_URL}?key={FIREBASE_API_KEY}",
                        {"grant_type": "refresh_token", "refresh_token": identity["refresh_token"]})
    identity["id_token"] = result["id_token"]
    identity["refresh_token"] = result["refresh_token"]
    identity["id_token_expires"] = time.time() + int(result.get("expires_in", 3600)) - 60
    data = _load()
    data["identity"] = identity
    _save(data)
    return identity["id_token"]


# --- pairing -------------------------------------------------------------------

def _generate_code() -> str:
    return "".join(secrets.choice(_PAIRING_CODE_ALPHABET) for _ in range(8))


def ensure_paired_device() -> str:
    """Make sure this device has an identity and a Firestore document, then
    issue a fresh pairing code for the parent to type into the website.
    Safe to call repeatedly - each call issues a new code (the previous one,
    if unclaimed, simply ages out of the 15-minute window the Security Rule
    enforces)."""
    identity = _ensure_identity()
    id_token = _ensure_token()
    uid = identity["uid"]

    if _firestore_get("devices", uid, id_token) is None:
        _firestore_create("devices", uid, {
            "ownerUid": _NULL,
            "name": _v_str(""),
            "schedule": _NULL,
            "scheduleUpdatedAt": _NULL,
            "override": _NULL,
            "overrideUpdatedAt": _NULL,
            "status": _NULL,
            "lastSeen": _NULL,
        }, id_token)

    code = _generate_code()
    _firestore_create("pairing_codes", code, {
        "deviceUid": _v_str(uid),
        "createdAt": _v_timestamp(_now_iso()),
    }, id_token)

    data = _load()
    data["last_pairing_code"] = code
    _save(data)
    log.info("issued pairing code")
    return code


def pairing_status() -> str | None:
    """The linked parent's Firebase uid, or None if this device has never
    been paired, isn't claimed yet, or the check itself failed (offline,
    backend unreachable) - callers can't distinguish those cases from the
    return value alone, which is fine for a status label."""
    try:
        data = _load()
        if "identity" not in data:
            return None
        id_token = _ensure_token()
        doc = _firestore_get("devices", data["identity"]["uid"], id_token)
        if doc is None:
            return None
        owner = doc.get("fields", {}).get("ownerUid")
        return _decode(owner) if owner else None
    except Exception:
        log.warning("pairing_status check failed", exc_info=True)
        return None


# --- the sync step agent.py calls every tick -----------------------------------

def sync_once(cfg: dict) -> dict | None:
    """Best-effort: if it's been long enough since the last attempt, check
    the website for a newer schedule/override and report a status
    heartbeat. Returns a dict of config keys to merge in (only the keys
    that actually changed), or None if there's nothing to apply / this
    device isn't paired yet / the attempt was skipped or failed.

    `cfg` isn't used for anything but is accepted for symmetry with the
    other agent hooks and in case a future check needs to compare against
    the current local config.
    """
    data = _load()
    if "identity" not in data:
        return None

    now = time.time()
    if now - data.get("last_sync_attempt", 0) < _SYNC_INTERVAL_SECONDS:
        return None
    data["last_sync_attempt"] = now
    _save(data)

    try:
        id_token = _ensure_token()
        uid = _load()["identity"]["uid"]

        doc = _firestore_get("devices", uid, id_token)
        if doc is None:
            return None
        fields = doc.get("fields", {})

        data = _load()
        last = data.get("last_applied", {})
        changes: dict = {}

        schedule_updated = _decode(fields.get("scheduleUpdatedAt", _NULL))
        if schedule_updated and schedule_updated != last.get("schedule_updated_at"):
            schedule = _decode(fields.get("schedule", _NULL))
            if isinstance(schedule, list) and len(schedule) == 7:
                changes["schedule"] = schedule
            last["schedule_updated_at"] = schedule_updated

        override_updated = _decode(fields.get("overrideUpdatedAt", _NULL))
        if override_updated and override_updated != last.get("override_updated_at"):
            changes["override"] = _decode(fields.get("override", _NULL))
            last["override_updated_at"] = override_updated

        data["last_applied"] = last
        _save(data)

        _push_heartbeat(uid, id_token)
        return changes or None
    except Exception:
        log.warning("cloud sync failed", exc_info=True)
        return None


def _push_heartbeat(uid: str, id_token: str) -> None:
    status = read_json(STATUS_PATH, default={}) or {}
    try:
        _firestore_patch("devices", uid, {
            "status": _v_map({
                "state": _v_str(status.get("state", "")),
                "stateName": _v_str(status.get("state_name", "")),
                "updatedLocal": _v_str(status.get("updated", "")),
            }),
            "lastSeen": _v_timestamp(_now_iso()),
        }, id_token)
    except Exception:
        log.warning("cloud heartbeat failed", exc_info=True)
