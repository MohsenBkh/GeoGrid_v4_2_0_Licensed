# -*- coding: utf-8 -*-
"""
generator.py
=============
Builds and signs a GeoGrid.lic file. This is the ONLY place in the
entire GeoGrid product line that touches a private signing key.

Usage (library):

    from generator import LicenseRequestForm, generate_license

    form = LicenseRequestForm(
        machine_id="GG-MACHINE-ABCDEF01-23456789",
        customer="Example Utility Co.",
        license_type="PROFESSIONAL",
        product_version="4.x",
        expires_at="2027-01-01T00:00:00+00:00",
    )
    signed = generate_license(form, private_key_path="keys/ggkey-2026-01.private.b64", key_id="ggkey-2026-01")
    Path("GeoGrid.lic").write_text(signed.to_json())
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from license_model import (
    LicensePayload,
    SignedLicense,
    LICENSE_FORMAT_VERSION,
    VALID_LICENSE_TYPES,
    DEFAULT_FEATURES_BY_TYPE,
)
from crypto_utils import sign_bytes


@dataclass
class LicenseRequestForm:
    machine_id: str
    customer: str
    license_type: str
    product_version: str = "4.x"
    expires_at: Optional[str] = None          # ISO-8601 UTC; None = perpetual
    features: Optional[List[str]] = None       # None -> use type defaults
    notes: str = ""


def _load_private_key(private_key_path: str) -> str:
    return Path(private_key_path).read_text(encoding="ascii").strip()


def generate_license(
    form: LicenseRequestForm,
    private_key_path: str,
    key_id: str,
) -> SignedLicense:
    if form.license_type not in VALID_LICENSE_TYPES:
        raise ValueError(f"Unknown license_type: {form.license_type!r}")

    features = form.features if form.features is not None else DEFAULT_FEATURES_BY_TYPE[form.license_type]

    payload = LicensePayload(
        license_format=LICENSE_FORMAT_VERSION,
        product="GeoGrid",
        product_version=form.product_version,
        license_id=str(uuid.uuid4()),
        customer=form.customer,
        machine_id=form.machine_id,
        license_type=form.license_type,
        issued_at=datetime.now(timezone.utc).isoformat(),
        expires_at=form.expires_at,
        max_machines=1,
        features=list(features),
        notes=form.notes,
    )

    priv_b64 = _load_private_key(private_key_path)
    signature = sign_bytes(payload.canonical_bytes(), priv_b64)

    return SignedLicense(payload=payload, signature=signature, signing_key_id=key_id)


def generate_trial_license(
    machine_id: str,
    trial_days: int,
    private_key_path: str,
    key_id: str,
    product_version: str = "4.x",
    customer: str = "Trial User",
) -> SignedLicense:
    from datetime import timedelta

    expires = (datetime.now(timezone.utc) + timedelta(days=trial_days)).isoformat()
    form = LicenseRequestForm(
        machine_id=machine_id,
        customer=customer,
        license_type="TRIAL",
        product_version=product_version,
        expires_at=expires,
        notes=f"Trial, {trial_days} days",
    )
    return generate_license(form, private_key_path, key_id)
