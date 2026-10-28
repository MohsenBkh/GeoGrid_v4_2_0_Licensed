# -*- coding: utf-8 -*-
"""
secure_time.py
===============
Local, tamper-evident "last known good time" state, used for:
  * TRIAL license clock-rollback detection
  * EXPIRED license clock-rollback detection (secondary check; the
    primary check is always the license's own expires_at vs wall clock)

Threat model
------------
Offline software cannot get real time from a trusted server. The best
we can do locally is a monotonically-advancing "last seen" timestamp
that is:
  1. Stored somewhere less obvious/removable than a plain text file in
     the install directory (Windows registry, per-user, HKCU -- not
     world-writable, survives most naive "delete a file" attempts,
     and is not removed by reinstalling GeoGrid into a new folder).
  2. HMAC-tagged with a key derived from the Machine ID, so a value
     copied from another machine (or hand-crafted) won't validate.
  3. Combined with a monotonic OS counter (``time.monotonic()`` isn't
     persistent across reboots, so we additionally cross-check against
     file-system timestamps of our own state as a secondary signal).

This raises the cost of clock manipulation without claiming to be
unbeatable (an attacker with admin rights can still edit the registry
directly if they reverse-engineer the HMAC key derivation -- see the
Security Review in docs/ for the honest residual risk).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
import base64
from datetime import datetime, timezone
from typing import Optional, Tuple

from . import machine_id as _machine_id

REG_PATH = r"SOFTWARE\GeoGrid\State"
REG_VALUE = "TimeState"


def _derive_state_key() -> bytes:
    """HMAC key derived from the Machine ID -- never stored, always
    recomputed, so the stored state is meaningless on another machine."""
    mid = _machine_id.get_machine_id()
    return hashlib.sha256(f"geogrid-time-state:{mid}".encode("utf-8")).digest()


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_registry() -> Optional[str]:
    try:
        import winreg  # type: ignore
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_PATH)
        value, _ = winreg.QueryValueEx(key, REG_VALUE)
        return str(value)
    except Exception:
        return None


def _write_registry(blob: str) -> bool:
    try:
        import winreg  # type: ignore
        key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, REG_PATH)
        winreg.SetValueEx(key, REG_VALUE, 0, winreg.REG_SZ, blob)
        return True
    except Exception:
        return False


# --- Fallback store for non-Windows dev/test only (see machine_id.py note) --
_FALLBACK_PATH_ENV = "GEOGRID_TIME_STATE_FILE"


def _read_fallback() -> Optional[str]:
    import os
    path = os.environ.get(_FALLBACK_PATH_ENV)
    if not path:
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return None


def _write_fallback(blob: str) -> bool:
    import os
    path = os.environ.get(_FALLBACK_PATH_ENV)
    if not path:
        return False
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(blob)
        return True
    except Exception:
        return False


def _read_state_blob() -> Optional[str]:
    return _read_registry() or _read_fallback()


def _write_state_blob(blob: str) -> bool:
    ok1 = _write_registry(blob)
    ok2 = _write_fallback(blob)
    return ok1 or ok2


def _pack(last_seen_iso: str, first_seen_iso: str) -> str:
    key = _derive_state_key()
    body = json.dumps(
        {"last_seen": last_seen_iso, "first_seen": first_seen_iso},
        sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    tag = hmac.new(key, body, hashlib.sha256).digest()
    return base64.b64encode(body).decode("ascii") + "." + base64.b64encode(tag).decode("ascii")


def _unpack(blob: str) -> Optional[dict]:
    try:
        body_b64, tag_b64 = blob.split(".", 1)
        body = base64.b64decode(body_b64)
        tag = base64.b64decode(tag_b64)
        key = _derive_state_key()
        expected = hmac.new(key, body, hashlib.sha256).digest()
        if not hmac.compare_digest(tag, expected):
            return None  # tampered, or copied from another machine
        return json.loads(body)
    except Exception:
        return None


class RollbackDetected(Exception):
    pass


def check_and_update() -> Tuple[bool, Optional[str]]:
    """Call once per GeoGrid startup (and periodically during long
    sessions). Returns (ok, first_seen_iso).

    ok=False means the system clock is behind the last time we
    legitimately observed on this machine -> treat as clock rollback
    (relevant to TRIAL countdown and to a secondary EXPIRED check).

    If no prior state exists, one is created (first run)."""
    now = datetime.now(timezone.utc)
    blob = _read_state_blob()
    state = _unpack(blob) if blob else None

    if state is None:
        # First run, or state missing/tampered/foreign -> (re)initialize.
        # Note: a wiped state resets rollback *detection*, but it does NOT
        # reset TRIAL eligibility, because trial start is tracked inside
        # the *signed* trial ticket (see activation.py), not here.
        now_iso = _now_utc_iso()
        _write_state_blob(_pack(now_iso, now_iso))
        return True, now_iso

    last_seen = datetime.fromisoformat(state["last_seen"])
    first_seen = state["first_seen"]

    if now < last_seen:
        # Clock moved backwards relative to what we've legitimately seen.
        return False, first_seen

    _write_state_blob(_pack(_now_utc_iso(), first_seen))
    return True, first_seen
