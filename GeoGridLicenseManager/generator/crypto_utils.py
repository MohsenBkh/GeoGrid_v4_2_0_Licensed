# -*- coding: utf-8 -*-
"""
crypto_utils.py
================
Ed25519 signature verification for GeoGrid (client side).

IMPORTANT: this module holds ONLY public-key material. There is no
private key anywhere in the GeoGrid client tree. Signing happens
exclusively in GeoGridLicenseManager (a separate, never-shipped project).

Key-rotation support
---------------------
``TRUSTED_PUBLIC_KEYS`` is a dict of {key_id: base64 public key}. A
signed license carries a ``signing_key_id`` telling GeoGrid which key
to verify against, so a future GeoGrid release can add a new key
without invalidating already-issued licenses signed with an older one.
Only append to this dict; never remove a key that might still be in
the field unless you are intentionally revoking it.
"""
from __future__ import annotations

import base64
import hashlib
from typing import Dict

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PublicKey,
    Ed25519PrivateKey,
)
from cryptography.hazmat.primitives import serialization

# ---------------------------------------------------------------------------
# TRUSTED PUBLIC KEYS (safe to ship inside GeoGrid.exe)
# ---------------------------------------------------------------------------
# Replace "PLACEHOLDER..." with the real base64-encoded raw Ed25519 public
# key produced by `GeoGridLicenseManager/generator/keygen.py`. Keep the
# private key OFF this machine's disk once generated (see docs/key
# management policy). This placeholder key exists only so the codebase
# is runnable/testable before a real keypair is provisioned.
TRUSTED_PUBLIC_KEYS: Dict[str, str] = {
    # key_id: base64 raw public key bytes
    "ggkey-2026-01": "REPLACE_WITH_BASE64_PUBLIC_KEY",
}

DEFAULT_KEY_ID = "ggkey-2026-01"


def key_id_for_public_key(pub_b64: str) -> str:
    """Short, stable id derived from a public key (used for display/logs
    only -- the *authoritative* mapping is TRUSTED_PUBLIC_KEYS)."""
    raw = base64.b64decode(pub_b64)
    return "kid-" + hashlib.sha256(raw).hexdigest()[:12]


def load_public_key(key_id: str) -> Ed25519PublicKey:
    if key_id not in TRUSTED_PUBLIC_KEYS:
        raise ValueError(f"Unknown signing_key_id: {key_id!r}")
    raw = base64.b64decode(TRUSTED_PUBLIC_KEYS[key_id])
    return Ed25519PublicKey.from_public_bytes(raw)


def verify_signature(payload_bytes: bytes, signature_b64: str, key_id: str) -> bool:
    """Return True iff signature_b64 is a valid Ed25519 signature over
    payload_bytes for the given trusted key_id. Never raises for a bad
    signature -- returns False. Raises ValueError only for an unknown
    key_id (a configuration/version problem, not a forged license)."""
    pub = load_public_key(key_id)
    try:
        pub.verify(base64.b64decode(signature_b64), payload_bytes)
        return True
    except InvalidSignature:
        return False
    except Exception:
        return False


# ---------------------------------------------------------------------------
# The functions below (private-key operations) are used ONLY by
# GeoGridLicenseManager. They are kept here too so both projects can
# share a single, audited implementation of the primitive -- but
# GeoGridLicenseManager is the only project that ever calls them, and
# GeoGrid's build must never bundle a real private key value.
# ---------------------------------------------------------------------------

def generate_keypair():
    """Generate a new Ed25519 keypair. Returns (priv_b64, pub_b64)."""
    priv = Ed25519PrivateKey.generate()
    pub = priv.public_key()
    priv_bytes = priv.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pub_bytes = pub.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return (
        base64.b64encode(priv_bytes).decode("ascii"),
        base64.b64encode(pub_bytes).decode("ascii"),
    )


def sign_bytes(payload_bytes: bytes, priv_b64: str) -> str:
    raw = base64.b64decode(priv_b64)
    priv = Ed25519PrivateKey.from_private_bytes(raw)
    sig = priv.sign(payload_bytes)
    return base64.b64encode(sig).decode("ascii")
