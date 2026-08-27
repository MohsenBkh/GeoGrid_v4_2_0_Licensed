# -*- coding: utf-8 -*-
"""
keygen.py
==========
One-time (or key-rotation-time) Ed25519 keypair generation for GeoGrid
License signing.

USAGE
-----
    python -m generator.keygen --key-id ggkey-2026-01

Produces:
    keys/<key-id>.private.b64   <- NEVER commit, NEVER copy to the
                                    GeoGrid client tree, back this up
                                    to an offline/encrypted location
                                    (see docs/license_admin/key_management.md)
    keys/<key-id>.public.b64    <- safe to embed in GeoGrid's
                                    crypto_utils.py TRUSTED_PUBLIC_KEYS

This script deliberately refuses to overwrite an existing key file, to
avoid an accidental key-rotation that silently invalidates every
license already issued under the old key.
"""
from __future__ import annotations

import argparse
import stat
import sys
from pathlib import Path

from crypto_utils import generate_keypair

KEYS_DIR = Path(__file__).resolve().parent.parent / "keys"


def main(argv=None):
    ap = argparse.ArgumentParser(description="Generate a GeoGrid license-signing Ed25519 keypair.")
    ap.add_argument("--key-id", required=True, help="e.g. ggkey-2026-01")
    ap.add_argument("--force", action="store_true", help="overwrite existing key files (DANGEROUS)")
    args = ap.parse_args(argv)

    KEYS_DIR.mkdir(parents=True, exist_ok=True)
    priv_path = KEYS_DIR / f"{args.key_id}.private.b64"
    pub_path = KEYS_DIR / f"{args.key_id}.public.b64"

    if (priv_path.exists() or pub_path.exists()) and not args.force:
        print(f"Refusing to overwrite existing key files for '{args.key_id}'. Use --force to override.")
        sys.exit(1)

    priv_b64, pub_b64 = generate_keypair()

    priv_path.write_text(priv_b64, encoding="ascii")
    try:
        priv_path.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 0600, best-effort on Windows
    except Exception:
        pass
    pub_path.write_text(pub_b64, encoding="ascii")

    print(f"Generated keypair '{args.key_id}'.")
    print(f"  Private key: {priv_path}  (KEEP OFFLINE/BACKED UP, NEVER COMMIT, NEVER SHIP)")
    print(f"  Public key : {pub_path}  (copy this value into GeoGrid's crypto_utils.py)")
    print()
    print("Next step: paste the following into")
    print("  GeoGrid_v4_2_0/shp2dgs/license/crypto_utils.py -> TRUSTED_PUBLIC_KEYS")
    print(f'  "{args.key_id}": "{pub_b64}",')


if __name__ == "__main__":
    main()
