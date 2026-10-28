#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
make_manifest.py
==================
Release-pipeline step: build and sign integrity_manifest.json against
the FINAL packaged output (post-Nuitka build), not the raw .py
sources. Run this from the vendor/build machine, which has access to
a manifest-signing private key (may be the SAME keypair as license
signing, but a distinct signing_key_id namespace is used so a
compromised license key and a compromised manifest key are at least
logically separable in the trust model -- see docs).

Usage:
    python build_tools/make_manifest.py \\
        --root dist/GeoGrid \\
        --key-id manifest-2026-01 \\
        --key-file /secure/path/manifest-2026-01.private.b64 \\
        --files GeoGrid.exe GeoGridCore.pyd shp2dgs/license/crypto_utils.pyc \\
        --out dist/GeoGrid/integrity_manifest.json

This script intentionally lives OUTSIDE shp2dgs/ (it is a build-time
tool, never shipped) but reuses the exact same hashing/signing
primitives as the runtime verifier (shp2dgs/license/integrity.py) to
avoid any drift between "what we signed" and "what we check".
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shp2dgs.license.integrity import build_manifest, canonical_manifest_bytes  # noqa: E402
from shp2dgs.license.crypto_utils import sign_bytes  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description="Build + sign GeoGrid's integrity manifest.")
    ap.add_argument("--root", required=True, help="Root of the final packaged build output")
    ap.add_argument("--files", nargs="+", required=True, help="Files (relative to --root) to track")
    ap.add_argument("--key-id", required=True)
    ap.add_argument("--key-file", required=True, help="Path to the manifest-signing private key (.b64)")
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)

    root = Path(args.root)
    manifest = build_manifest(root, args.files)
    priv_b64 = Path(args.key_file).read_text(encoding="ascii").strip()
    signature = sign_bytes(canonical_manifest_bytes(manifest), priv_b64)

    out_obj = {"files": manifest, "signature": signature, "signing_key_id": args.key_id}
    Path(args.out).write_text(json.dumps(out_obj, indent=2, sort_keys=True), encoding="utf-8")
    print(f"Wrote manifest for {len(manifest)} file(s) -> {args.out}")


if __name__ == "__main__":
    main()
