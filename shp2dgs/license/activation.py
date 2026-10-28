# -*- coding: utf-8 -*-
"""
activation.py
==============
Offline activation flow for the GeoGrid client side:

  1. build_activation_request()  -> writes GeoGrid_Activation_Request.ggr
                                     and returns a short human-readable
                                     Request Code.
  2. import_license_file()       -> reads a GeoGrid.lic the customer
                                     received back, verifies it, and
                                     (if valid) installs it as the
                                     active license for this machine.
  3. load_active_license()       -> used at every startup / Core entry
                                     point to get the current
                                     ValidationResult.

Where the active license is stored
-----------------------------------
%LOCALAPPDATA%\\GeoGrid\\GeoGrid.lic (per-machine, per-user-profile-
independent would require %ProgramData% instead if multiple Windows
users share one machine and must share one activation -- see docs).
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import platform
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple

from .license_model import LicensePayload, SignedLicense
from . import machine_id as _machine_id
from .validator import validate_license, ValidationResult, LicenseState

REQUEST_FORMAT_VERSION = 1


def _app_data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~/.local/share")
    d = Path(base) / "GeoGrid"
    d.mkdir(parents=True, exist_ok=True)
    return d


def license_file_path() -> Path:
    return _app_data_dir() / "GeoGrid.lic"


def build_activation_request(destination_dir: Optional[Path] = None) -> Tuple[Path, str]:
    """Create GeoGrid_Activation_Request.ggr and return (file_path, request_code).

    The request contains only the (hashed) machine fingerprint plus
    context needed by the vendor to fill in a license -- no personal
    data is collected automatically."""
    machine_id = _machine_id.get_machine_id()
    request = {
        "request_format": REQUEST_FORMAT_VERSION,
        "product": "GeoGrid",
        "machine_id": machine_id,
        "os": f"{platform.system()} {platform.release()}",
        "requested_at": datetime.now(timezone.utc).isoformat(),
        "request_nonce": uuid.uuid4().hex,
    }
    payload_bytes = json.dumps(request, sort_keys=True, separators=(",", ":")).encode("utf-8")
    request["checksum"] = hashlib.sha256(payload_bytes).hexdigest()[:16]

    destination_dir = destination_dir or _app_data_dir()
    destination_dir = Path(destination_dir)
    destination_dir.mkdir(parents=True, exist_ok=True)
    out_path = destination_dir / "GeoGrid_Activation_Request.ggr"
    out_path.write_text(json.dumps(request, indent=2), encoding="utf-8")

    # Short request code for cases where sending a file is inconvenient
    # (e.g. reading it over the phone): base32 chunks of the machine id
    # hash + nonce prefix, uppercase, grouped for readability.
    code_source = (machine_id + request["request_nonce"]).encode("utf-8")
    code_hash = hashlib.sha256(code_source).hexdigest().upper()
    request_code = "-".join(code_hash[i:i + 5] for i in range(0, 20, 5))

    return out_path, request_code


def import_license_file(lic_path: Path) -> ValidationResult:
    """Read, verify, and (if valid) install a GeoGrid.lic file as the
    active license for this machine. Returns the ValidationResult so
    the caller can show an appropriate message either way."""
    lic_path = Path(lic_path)
    try:
        text = lic_path.read_text(encoding="utf-8")
        signed = SignedLicense.from_json(text)
    except Exception:
        return ValidationResult(LicenseState.INVALID, "License file is corrupted or the signature is invalid.")

    result = validate_license(signed)

    if result.state in (LicenseState.ACTIVATED,):
        # Persist only after successful full validation.
        license_file_path().write_text(signed.to_json(), encoding="utf-8")
    elif result.state == LicenseState.MACHINE_MISMATCH:
        # Still worth telling the caller clearly; do not persist.
        pass

    return result


def load_active_license() -> ValidationResult:
    """Load + validate whatever license is currently installed on this
    machine (if any). This is the function GUI/CLI/Core call at every
    checkpoint."""
    path = license_file_path()
    if not path.exists():
        return ValidationResult(LicenseState.UNLICENSED, "GeoGrid is not activated on this computer.")
    try:
        signed = SignedLicense.from_json(path.read_text(encoding="utf-8"))
    except Exception:
        return ValidationResult(LicenseState.INVALID, "License file is corrupted or the signature is invalid.")
    return validate_license(signed)
