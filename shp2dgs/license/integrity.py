# -*- coding: utf-8 -*-
"""
integrity.py
=============
Runtime integrity verification against a signed manifest of expected
SHA-256 hashes for GeoGrid's sensitive files (built by
build_tools/make_manifest.py at release time, run against the final
Nuitka build output -- .exe / .pyd / .dll -- not the raw .py sources,
since those won't exist in the shipped product).

Anti-tamper posture (see docs/developer/anti_tamper.md for the full
threat-model writeup):

  * The manifest itself is Ed25519-signed with the SAME key-rotation
    mechanism as licenses (but a distinct key_id namespace: "manifest"
    vs "license"), so an attacker cannot just edit the manifest to
    match a patched binary.
  * verify() is called from more than one place: once at GUI/CLI
    startup (cheap, fast fail), and again from inside the Protected
    Core before performing a conversion (see core/protected/README.md)
    so that patching only the startup check does not unlock
    conversions.
  * A failed check does not crash with a raw stack trace (which would
    hand the attacker a convenient breakpoint location); it raises
    IntegrityError, which calling code turns into the generic
    "TAMPERED" state and a generic user-facing message.

Honest limitation: this detects *file* tampering. It does not defend
against an attacker who patches the running process in memory (a
debugger-based patch of `verify()` itself). Raising that bar further
requires the native Core work described in docs/developer/architecture.md
(section: "Protected Core / anti-tamper depth"), which sits outside
what a pure-Python module can guarantee.
"""
from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from typing import Dict, Optional

from .crypto_utils import verify_signature

MANIFEST_FILENAME = "integrity_manifest.json"


class IntegrityError(Exception):
    pass


def _hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def build_manifest(root: Path, tracked_files: list) -> Dict[str, str]:
    """Build the {relative_path: sha256} map for the given files, all
    paths relative to `root`. Called by build_tools/make_manifest.py
    at release time, against the FINAL packaged output."""
    manifest = {}
    for rel in tracked_files:
        full = root / rel
        if not full.exists():
            raise FileNotFoundError(f"Manifest input missing: {full}")
        manifest[str(rel).replace("\\", "/")] = _hash_file(full)
    return manifest


def canonical_manifest_bytes(manifest: Dict[str, str]) -> bytes:
    return json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")


def verify(
    root: Path,
    manifest_path: Optional[Path] = None,
    signing_key_id: str = "manifest-2026-01",
) -> bool:
    """Verify tracked files under `root` against the signed manifest.
    Returns True if OK; raises IntegrityError with a generic message
    otherwise (never leaks which file / what hash mismatched)."""
    manifest_path = manifest_path or (root / MANIFEST_FILENAME)
    if not manifest_path.exists():
        # No manifest shipped (e.g. a dev/source checkout) -- integrity
        # checking is skipped rather than failing closed, so developers
        # aren't blocked. Release builds MUST include a manifest; the
        # build pipeline fails the release step if one is missing (see
        # build_tools/make_manifest.py).
        return True

    try:
        obj = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest = obj["files"]
        signature = obj["signature"]
        key_id = obj["signing_key_id"]
    except Exception:
        raise IntegrityError("integrity manifest unreadable")

    if not verify_signature(canonical_manifest_bytes(manifest), signature, key_id):
        raise IntegrityError("integrity manifest signature invalid")

    for rel, expected_hash in manifest.items():
        full = root / rel
        if not full.exists():
            raise IntegrityError("tracked file missing")
        actual = _hash_file(full)
        if actual != expected_hash:
            raise IntegrityError("tracked file hash mismatch")

    return True
