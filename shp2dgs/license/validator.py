# -*- coding: utf-8 -*-
"""
validator.py
=============
License validation state machine. This is the single source of truth
for "is GeoGrid allowed to run, and with which features" -- but it is
deliberately called from *multiple* independent points (GUI startup,
CLI startup, and again inside the Protected Core before any
conversion runs) so that patching one call site does not unlock the
whole application. See docs/developer/anti_tamper.md.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional

from .license_model import SignedLicense, LicensePayload, VALID_LICENSE_TYPES
from .crypto_utils import verify_signature
from . import machine_id as _machine_id
from . import secure_time as _secure_time

PRODUCT_NAME = "GeoGrid"
SUPPORTED_VERSION_BAND = "4.x"  # licenses issued for a different band are rejected


class LicenseState(enum.Enum):
    UNLICENSED = "UNLICENSED"
    REQUEST_CREATED = "REQUEST_CREATED"
    ACTIVATED = "ACTIVATED"
    EXPIRED = "EXPIRED"
    INVALID = "INVALID"
    TAMPERED = "TAMPERED"
    MACHINE_MISMATCH = "MACHINE_MISMATCH"


@dataclass
class ValidationResult:
    state: LicenseState
    message: str
    payload: Optional[LicensePayload] = None
    features: List[str] = None

    def ok(self) -> bool:
        return self.state == LicenseState.ACTIVATED

    def has_feature(self, feature: str) -> bool:
        return self.ok() and self.features is not None and feature in self.features


# User-facing messages -- deliberately generic, no internals leaked.
_MESSAGES = {
    LicenseState.UNLICENSED: "GeoGrid is not activated on this computer.",
    LicenseState.EXPIRED: "This license has expired.",
    LicenseState.INVALID: "License file is corrupted or the signature is invalid.",
    LicenseState.TAMPERED: "A required application file failed integrity verification.",
    LicenseState.MACHINE_MISMATCH: "This license belongs to a different computer.",
}


def _version_band_matches(license_band: str, running_band: str) -> bool:
    return license_band.strip() == running_band.strip()


def validate_license(
    signed: Optional[SignedLicense],
    running_product_version_band: str = SUPPORTED_VERSION_BAND,
    skip_secure_time: bool = False,
) -> ValidationResult:
    """Pure-ish validation: does NOT touch disk except via secure_time's
    rollback check (needed for expiry robustness). Caller (activation.py)
    is responsible for loading the SignedLicense from GeoGrid.lic."""

    if signed is None:
        return ValidationResult(LicenseState.UNLICENSED, _MESSAGES[LicenseState.UNLICENSED])

    payload = signed.payload

    if payload.product != PRODUCT_NAME:
        return ValidationResult(LicenseState.INVALID, _MESSAGES[LicenseState.INVALID])

    if payload.license_type not in VALID_LICENSE_TYPES:
        return ValidationResult(LicenseState.INVALID, _MESSAGES[LicenseState.INVALID])

    # 1) Signature -- must check BEFORE trusting any other field.
    try:
        sig_ok = verify_signature(
            payload.canonical_bytes(), signed.signature, signed.signing_key_id
        )
    except ValueError:
        # Unknown signing_key_id: could be a much newer/older license
        # format than this GeoGrid build understands.
        return ValidationResult(LicenseState.INVALID, _MESSAGES[LicenseState.INVALID])

    if not sig_ok:
        return ValidationResult(LicenseState.INVALID, _MESSAGES[LicenseState.INVALID])

    # 2) Machine binding.
    try:
        current_machine_id = _machine_id.get_machine_id()
    except RuntimeError:
        return ValidationResult(LicenseState.INVALID, "Unable to read this computer's identity.")

    if payload.machine_id != current_machine_id:
        return ValidationResult(
            LicenseState.MACHINE_MISMATCH, _MESSAGES[LicenseState.MACHINE_MISMATCH]
        )

    # 3) Product version band.
    if not _version_band_matches(payload.product_version, running_product_version_band):
        return ValidationResult(
            LicenseState.INVALID,
            "This license is not valid for this version of GeoGrid.",
        )

    # 4) Expiration + clock-rollback resistance.
    if payload.expires_at:
        expires = datetime.fromisoformat(payload.expires_at)
        now = datetime.now(timezone.utc)

        if not skip_secure_time:
            time_ok, _first_seen = _secure_time.check_and_update()
            if not time_ok:
                # Clock was rolled back. Treat conservatively as expired
                # rather than silently trusting the (possibly manipulated)
                # wall clock.
                return ValidationResult(LicenseState.EXPIRED, _MESSAGES[LicenseState.EXPIRED])

        if now > expires:
            return ValidationResult(LicenseState.EXPIRED, _MESSAGES[LicenseState.EXPIRED])

    return ValidationResult(
        LicenseState.ACTIVATED,
        f"License valid ({payload.license_type}).",
        payload=payload,
        features=list(payload.features),
    )
