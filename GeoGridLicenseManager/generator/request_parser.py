# -*- coding: utf-8 -*-
"""
request_parser.py
===================
Parses a GeoGrid_Activation_Request.ggr file (or a manually-entered
Machine ID / Request Code) produced by shp2dgs.license.activation on
the customer's machine.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

MACHINE_ID_RE = re.compile(r"^GG-MACHINE-[0-9A-F]{8}-[0-9A-F]{8}$")


@dataclass
class ActivationRequest:
    machine_id: str
    os: Optional[str] = None
    requested_at: Optional[str] = None
    request_nonce: Optional[str] = None
    source_file: Optional[str] = None


def parse_ggr_file(path: Path) -> ActivationRequest:
    path = Path(path)
    obj = json.loads(path.read_text(encoding="utf-8"))

    machine_id = obj.get("machine_id", "")
    if not MACHINE_ID_RE.match(machine_id):
        raise ValueError(f"Request file does not contain a valid Machine ID: {machine_id!r}")

    # Best-effort checksum re-verification (defense against a hand-edited
    # request file; not a cryptographic guarantee since the request
    # itself is unsigned by design -- it carries no secret).
    checksum = obj.pop("checksum", None)
    if checksum is not None:
        recomputed = json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")
        import hashlib
        if hashlib.sha256(recomputed).hexdigest()[:16] != checksum:
            raise ValueError("Request file checksum mismatch -- it may have been edited.")

    return ActivationRequest(
        machine_id=machine_id,
        os=obj.get("os"),
        requested_at=obj.get("requested_at"),
        request_nonce=obj.get("request_nonce"),
        source_file=str(path),
    )


def parse_machine_id(text: str) -> ActivationRequest:
    """Accept a Machine ID typed/pasted directly (no .ggr file available)."""
    mid = text.strip().upper()
    if not MACHINE_ID_RE.match(mid):
        raise ValueError(f"Not a valid Machine ID: {text!r}")
    return ActivationRequest(machine_id=mid)
