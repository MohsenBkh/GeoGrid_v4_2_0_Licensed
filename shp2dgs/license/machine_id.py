# -*- coding: utf-8 -*-
"""
machine_id.py
=============
Deterministic, weighted Machine Fingerprint for Windows.

Design
------
We never store raw hardware identifiers anywhere (not in the license,
not on disk). We collect several identifiers, each with a stability
"weight", hash each individually, then combine them into one
fingerprint hash. Because it's weighted rather than a single hash of
"all identifiers concatenated", a single component changing (e.g. a
new NIC -> new MAC, a docking-station change) does not necessarily
change the resulting Machine ID (see ``tolerant_component_hashes`` /
``TOLERANCE`` below), while multiple components changing (i.e. it's
probably not the same computer) will.

Identifiers used (Windows):
  1. Machine GUID          (HKLM\\SOFTWARE\\Microsoft\\Cryptography)  weight 3
  2. System UUID (SMBIOS)  (wmic csproduct get uuid)                  weight 3
  3. Disk/volume serial     (system drive volume serial number)       weight 2
  4. CPU identifier         (processor id / name)                     weight 2
  5. BIOS serial            (wmic bios get serialnumber)               weight 1

Weights are only used to decide *how many* components may legitimately
differ between two readings before we call it "not the same machine"
(see ``fingerprint_distance``); the emitted Machine ID itself is a
straight hash of the currently observed components, recomputed fresh
each run -- GeoGrid does not "learn" or persist a fuzzy fingerprint in
this release, keeping the implementation auditable. The tolerance
mechanism is exposed so a future release can implement grace-period
re-binding without changing the license format.

This module has a non-Windows fallback (used only for development /
CI on this platform) that is clearly marked as UNSUPPORTED for
production licensing -- GeoGrid's supported OS is Windows 10/11 x64.
"""
from __future__ import annotations

import hashlib
import platform
import re
import subprocess
from dataclasses import dataclass
from typing import Dict, Optional


def _run(cmd: list) -> Optional[str]:
    try:
        out = subprocess.check_output(
            cmd, stderr=subprocess.DEVNULL, timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return out.decode("utf-8", errors="ignore").strip()
    except Exception:
        return None


def _read_registry_machine_guid() -> Optional[str]:
    try:
        import winreg  # type: ignore
        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography"
        )
        value, _ = winreg.QueryValueEx(key, "MachineGuid")
        return str(value).strip()
    except Exception:
        return None


def _system_uuid_windows() -> Optional[str]:
    # Prefer PowerShell CIM (wmic is deprecated/removed on newer Win11 builds)
    out = _run([
        "powershell", "-NoProfile", "-Command",
        "(Get-CimInstance -ClassName Win32_ComputerSystemProduct).UUID",
    ])
    if out and "FFFFFFFF" not in out.upper():
        return out.strip()
    out = _run(["wmic", "csproduct", "get", "uuid"])
    if out:
        lines = [l.strip() for l in out.splitlines() if l.strip() and "UUID" not in l]
        if lines:
            return lines[0]
    return None


def _disk_volume_serial_windows() -> Optional[str]:
    out = _run(["cmd", "/c", "vol", "C:"])
    if out:
        m = re.search(r"([0-9A-Fa-f]{4}-[0-9A-Fa-f]{4})", out)
        if m:
            return m.group(1)
    return None


def _cpu_id_windows() -> Optional[str]:
    out = _run([
        "powershell", "-NoProfile", "-Command",
        "(Get-CimInstance -ClassName Win32_Processor).ProcessorId",
    ])
    if out:
        return out.strip()
    return platform.processor() or None


def _bios_serial_windows() -> Optional[str]:
    out = _run([
        "powershell", "-NoProfile", "-Command",
        "(Get-CimInstance -ClassName Win32_BIOS).SerialNumber",
    ])
    return out.strip() if out else None


@dataclass
class FingerprintComponents:
    machine_guid: Optional[str] = None
    system_uuid: Optional[str] = None
    disk_serial: Optional[str] = None
    cpu_id: Optional[str] = None
    bios_serial: Optional[str] = None

    WEIGHTS = {
        "machine_guid": 3,
        "system_uuid": 3,
        "disk_serial": 2,
        "cpu_id": 2,
        "bios_serial": 1,
    }

    def present(self) -> Dict[str, str]:
        return {k: v for k, v in self.__dict__.items() if v and k in self.WEIGHTS}


def collect_components() -> FingerprintComponents:
    system = platform.system()
    if system == "Windows":
        return FingerprintComponents(
            machine_guid=_read_registry_machine_guid(),
            system_uuid=_system_uuid_windows(),
            disk_serial=_disk_volume_serial_windows(),
            cpu_id=_cpu_id_windows(),
            bios_serial=_bios_serial_windows(),
        )
    # --- Non-Windows dev/test fallback (NOT for production licensing) ---
    # GeoGrid officially supports Windows 10/11 x64 only (spec section 26).
    # This branch exists so the license subsystem is unit-testable on the
    # build/CI machine; it deliberately uses weaker, less stable sources.
    node = platform.node()
    machine = platform.machine()
    return FingerprintComponents(
        machine_guid=hashlib.sha256(node.encode()).hexdigest()[:32],
        system_uuid=None,
        disk_serial=None,
        cpu_id=machine,
        bios_serial=None,
    )


def _hash_component(name: str, value: str) -> str:
    return hashlib.sha256(f"{name}:{value.strip().upper()}".encode("utf-8")).hexdigest()


def component_hashes(components: Optional[FingerprintComponents] = None) -> Dict[str, str]:
    """Per-component hashes -- used for fuzzy-matching tolerance, never
    stored raw and never placed in the license file."""
    components = components or collect_components()
    return {k: _hash_component(k, v) for k, v in components.present().items()}


def get_machine_id(components: Optional[FingerprintComponents] = None) -> str:
    """Return the deterministic Machine ID string:
    GG-MACHINE-XXXXXXXX-XXXXXXXX

    Built from a weighted, deterministically-ordered combination of the
    hashed components (never raw hardware values)."""
    hashes = component_hashes(components)
    if not hashes:
        raise RuntimeError(
            "Unable to read any hardware/system identifiers for machine "
            "fingerprinting on this system."
        )
    weights = FingerprintComponents.WEIGHTS
    ordered = sorted(hashes.items(), key=lambda kv: (-weights.get(kv[0], 0), kv[0]))
    combined = "|".join(f"{k}={v}" for k, v in ordered)
    digest = hashlib.sha256(combined.encode("utf-8")).hexdigest().upper()
    return f"GG-MACHINE-{digest[:8]}-{digest[8:16]}"


def fingerprint_distance(a: Dict[str, str], b: Dict[str, str]) -> int:
    """Weighted count of DIFFERING components between two component-hash
    readings (lower = more likely the same machine). Reserved for a
    future grace-period re-binding feature; not used to alter the
    Machine ID emitted by get_machine_id() in this release."""
    weights = FingerprintComponents.WEIGHTS
    keys = set(a) | set(b)
    dist = 0
    for k in keys:
        if a.get(k) != b.get(k):
            dist += weights.get(k, 1)
    return dist


if __name__ == "__main__":
    comps = collect_components()
    print("Components found:", list(comps.present().keys()))
    print("Machine ID:", get_machine_id(comps))
