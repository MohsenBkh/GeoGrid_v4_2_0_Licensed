# -*- coding: utf-8 -*-
"""
license_model.py
=================
Canonical, versioned License payload structure shared between GeoGrid
(verification side, public key only) and GeoGridLicenseManager
(generation side, private key).

This module intentionally contains NO cryptographic keys and NO signing
logic -- see crypto_utils.py for that. Keeping the model separate from
the crypto means both GeoGrid and the License Manager can import an
*identical* copy of this file (it is small and dependency-free) without
either side needing the other's secrets.

Design notes
------------
* ``license_format`` is a small integer so that the verification logic
  can evolve (e.g. add fields, rotate keys) without breaking older
  licenses still in the field -- see ``validator.py`` for how format
  version selects the verification key.
* Canonicalization uses ``json.dumps(..., sort_keys=True,
  separators=(",", ":"), ensure_ascii=True)``. This is deterministic
  across platforms/Python versions, which is required for signature
  verification to be stable.
* The ``signature`` field is never part of the signed payload -- it is
  stripped before canonicalization, appended afterwards.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from typing import List, Optional

LICENSE_FORMAT_VERSION = 1

# Enterprise implies "every currently known feature". New features added
# in future releases should be appended here; a license generated before
# a feature existed does NOT automatically gain it (explicit is safer),
# except for ENTERPRISE-class licenses re-issued after the update.
FEATURE_GIS_IMPORT = "gis_import"
FEATURE_DGS_EXPORT = "dgs_export"
FEATURE_DIAGRAM = "diagram_preview"
FEATURE_BATCH_CONVERSION = "batch_conversion"
FEATURE_GPS_GEOGRAPHIC = "gps_geographic_diagram"
FEATURE_TYPE_LIBRARY_EDITOR = "type_library_editor"

ALL_FEATURES = [
    FEATURE_GIS_IMPORT,
    FEATURE_DGS_EXPORT,
    FEATURE_DIAGRAM,
    FEATURE_BATCH_CONVERSION,
    FEATURE_GPS_GEOGRAPHIC,
    FEATURE_TYPE_LIBRARY_EDITOR,
]

LICENSE_TYPE_TRIAL = "TRIAL"
LICENSE_TYPE_STANDARD = "STANDARD"
LICENSE_TYPE_PROFESSIONAL = "PROFESSIONAL"
LICENSE_TYPE_ENTERPRISE = "ENTERPRISE"

DEFAULT_FEATURES_BY_TYPE = {
    LICENSE_TYPE_TRIAL: [FEATURE_GIS_IMPORT, FEATURE_DGS_EXPORT],
    LICENSE_TYPE_STANDARD: [FEATURE_GIS_IMPORT, FEATURE_DGS_EXPORT],
    LICENSE_TYPE_PROFESSIONAL: [
        FEATURE_GIS_IMPORT,
        FEATURE_DGS_EXPORT,
        FEATURE_DIAGRAM,
        FEATURE_BATCH_CONVERSION,
        FEATURE_TYPE_LIBRARY_EDITOR,
    ],
    LICENSE_TYPE_ENTERPRISE: list(ALL_FEATURES),
}

VALID_LICENSE_TYPES = frozenset(DEFAULT_FEATURES_BY_TYPE.keys())


@dataclass
class LicensePayload:
    """Everything that gets cryptographically signed."""

    license_format: int = LICENSE_FORMAT_VERSION
    product: str = "GeoGrid"
    product_version: str = "4.x"          # major.x compatibility band, e.g. "4.x"
    license_id: str = ""                  # UUID4, generator-assigned
    customer: str = ""
    machine_id: str = ""                  # GG-MACHINE-XXXXXXXX-XXXXXXXX
    license_type: str = LICENSE_TYPE_STANDARD
    issued_at: str = ""                   # ISO-8601 UTC
    expires_at: Optional[str] = None      # ISO-8601 UTC, None = perpetual (not used for TRIAL)
    max_machines: int = 1                 # always 1 in this release (no transfer)
    features: List[str] = field(default_factory=list)
    notes: str = ""                       # free text for the vendor's own records

    def canonical_bytes(self) -> bytes:
        """Deterministic byte representation used as the signing input."""
        payload = asdict(self)
        return json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode("utf-8")

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: dict) -> "LicensePayload":
        known = {f: data.get(f) for f in LicensePayload.__dataclass_fields__}
        return LicensePayload(**known)


@dataclass
class SignedLicense:
    """LicensePayload + Ed25519 signature, this is what GeoGrid.lic holds."""

    payload: LicensePayload
    signature: str  # base64, Ed25519 signature over payload.canonical_bytes()
    signing_key_id: str  # which public key (by fingerprint) should verify this

    def to_json(self) -> str:
        obj = {
            "payload": self.payload.to_dict(),
            "signature": self.signature,
            "signing_key_id": self.signing_key_id,
        }
        return json.dumps(obj, indent=2, ensure_ascii=False, sort_keys=True)

    @staticmethod
    def from_json(text: str) -> "SignedLicense":
        obj = json.loads(text)
        return SignedLicense(
            payload=LicensePayload.from_dict(obj["payload"]),
            signature=obj["signature"],
            signing_key_id=obj["signing_key_id"],
        )
