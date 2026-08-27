#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GeoGrid License Manager -- CLI
================================
Vendor-only tool. NEVER ship this project (or the keys/ directory) to
a customer. See docs/license_admin/*.md for the full workflow.

Examples
--------
Generate a new signing key (first-time setup):
    python cli.py keygen --key-id ggkey-2026-01

Inspect a request file the customer sent you:
    python cli.py inspect-request GeoGrid_Activation_Request.ggr

Issue a license from a request file:
    python cli.py issue \\
        --request GeoGrid_Activation_Request.ggr \\
        --customer "Example Utility Co." \\
        --type PROFESSIONAL \\
        --expires 2027-01-01 \\
        --key-id ggkey-2026-01 \\
        --key-file keys/ggkey-2026-01.private.b64 \\
        --out GeoGrid.lic

Issue a license from a typed-in Machine ID (phone/email case):
    python cli.py issue --machine-id GG-MACHINE-ABCDEF01-23456789 ...

Issue a trial license:
    python cli.py issue-trial --machine-id GG-... --days 30 --key-id ... --key-file ...

Inspect / verify an already-issued license:
    python cli.py inspect GeoGrid.lic
    python cli.py verify GeoGrid.lic --key-id ggkey-2026-01 --pub-file keys/ggkey-2026-01.public.b64
"""
from __future__ import annotations

import argparse
import base64
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "generator"))

from generator import LicenseRequestForm, generate_license, generate_trial_license  # noqa: E402
from request_parser import parse_ggr_file, parse_machine_id  # noqa: E402
from license_model import SignedLicense, VALID_LICENSE_TYPES, DEFAULT_FEATURES_BY_TYPE  # noqa: E402
from crypto_utils import Ed25519PublicKey, InvalidSignature  # noqa: E402


def _iso(date_str: str) -> str:
    """Accept 'YYYY-MM-DD' or full ISO-8601; return normalized ISO-8601 UTC."""
    try:
        if len(date_str) == 10:
            dt = datetime.strptime(date_str, "%Y-%m-%d").replace(
                hour=23, minute=59, second=59, tzinfo=timezone.utc
            )
        else:
            dt = datetime.fromisoformat(date_str)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()
    except ValueError:
        raise SystemExit(f"Invalid date: {date_str!r} (use YYYY-MM-DD or full ISO-8601)")


def cmd_keygen(args):
    import keygen  # noqa (generator/ dir is on sys.path, see above)
    keygen.main(["--key-id", args.key_id] + (["--force"] if args.force else []))


def cmd_inspect_request(args):
    req = parse_ggr_file(Path(args.file))
    print(f"Machine ID   : {req.machine_id}")
    print(f"OS           : {req.os}")
    print(f"Requested at : {req.requested_at}")
    print(f"Nonce        : {req.request_nonce}")


def _resolve_machine_id(args) -> str:
    if args.request:
        return parse_ggr_file(Path(args.request)).machine_id
    if args.machine_id:
        return parse_machine_id(args.machine_id).machine_id
    raise SystemExit("Provide either --request <file.ggr> or --machine-id <GG-MACHINE-...>")


def cmd_issue(args):
    machine_id = _resolve_machine_id(args)
    features = args.features.split(",") if args.features else None
    form = LicenseRequestForm(
        machine_id=machine_id,
        customer=args.customer,
        license_type=args.type,
        product_version=args.product_version,
        expires_at=_iso(args.expires) if args.expires else None,
        features=features,
        notes=args.notes or "",
    )
    signed = generate_license(form, private_key_path=args.key_file, key_id=args.key_id)
    out_path = Path(args.out)
    out_path.write_text(signed.to_json(), encoding="utf-8")
    print(f"Issued {args.type} license for {args.customer} -> {out_path}")
    print(f"  Machine ID : {machine_id}")
    print(f"  Expires    : {form.expires_at or 'never'}")
    print(f"  Features   : {', '.join(form.features or DEFAULT_FEATURES_BY_TYPE[args.type])}")


def cmd_issue_trial(args):
    machine_id = _resolve_machine_id(args)
    signed = generate_trial_license(
        machine_id=machine_id,
        trial_days=args.days,
        private_key_path=args.key_file,
        key_id=args.key_id,
        product_version=args.product_version,
        customer=args.customer,
    )
    out_path = Path(args.out)
    out_path.write_text(signed.to_json(), encoding="utf-8")
    print(f"Issued {args.days}-day TRIAL license -> {out_path}")


def cmd_inspect(args):
    signed = SignedLicense.from_json(Path(args.file).read_text(encoding="utf-8"))
    p = signed.payload
    print(f"License ID   : {p.license_id}")
    print(f"Customer     : {p.customer}")
    print(f"Machine ID   : {p.machine_id}")
    print(f"Type         : {p.license_type}")
    print(f"Product/Ver  : {p.product} / {p.product_version}")
    print(f"Issued       : {p.issued_at}")
    print(f"Expires      : {p.expires_at or 'never'}")
    print(f"Features     : {', '.join(p.features)}")
    print(f"Signing key  : {signed.signing_key_id}")


def cmd_verify(args):
    signed = SignedLicense.from_json(Path(args.file).read_text(encoding="utf-8"))
    pub_b64 = Path(args.pub_file).read_text(encoding="ascii").strip()
    pub = Ed25519PublicKey.from_public_bytes(base64.b64decode(pub_b64))
    try:
        pub.verify(base64.b64decode(signed.signature), signed.payload.canonical_bytes())
        print("Signature: VALID")
    except InvalidSignature:
        print("Signature: INVALID")
        sys.exit(1)


def build_parser():
    ap = argparse.ArgumentParser(description="GeoGrid License Manager (vendor-only)")
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("keygen", help="Generate a new signing keypair")
    p.add_argument("--key-id", required=True)
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_keygen)

    p = sub.add_parser("inspect-request", help="Inspect a .ggr activation request")
    p.add_argument("file")
    p.set_defaults(func=cmd_inspect_request)

    p = sub.add_parser("issue", help="Issue a signed license")
    p.add_argument("--request", help="Path to GeoGrid_Activation_Request.ggr")
    p.add_argument("--machine-id", help="Machine ID typed/pasted directly")
    p.add_argument("--customer", required=True)
    p.add_argument("--type", required=True, choices=sorted(VALID_LICENSE_TYPES))
    p.add_argument("--product-version", default="4.x")
    p.add_argument("--expires", help="YYYY-MM-DD (omit for perpetual)")
    p.add_argument("--features", help="Comma-separated feature override")
    p.add_argument("--notes", default="")
    p.add_argument("--key-id", required=True)
    p.add_argument("--key-file", required=True)
    p.add_argument("--out", required=True)
    p.set_defaults(func=cmd_issue)

    p = sub.add_parser("issue-trial", help="Issue a signed TRIAL license")
    p.add_argument("--request")
    p.add_argument("--machine-id")
    p.add_argument("--customer", default="Trial User")
    p.add_argument("--days", type=int, default=30)
    p.add_argument("--product-version", default="4.x")
    p.add_argument("--key-id", required=True)
    p.add_argument("--key-file", required=True)
    p.add_argument("--out", required=True)
    p.set_defaults(func=cmd_issue_trial)

    p = sub.add_parser("inspect", help="Inspect an issued GeoGrid.lic")
    p.add_argument("file")
    p.set_defaults(func=cmd_inspect)

    p = sub.add_parser("verify", help="Verify a GeoGrid.lic signature against a public key")
    p.add_argument("file")
    p.add_argument("--pub-file", required=True)
    p.set_defaults(func=cmd_verify)

    return ap


def main(argv=None):
    ap = build_parser()
    args = ap.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
