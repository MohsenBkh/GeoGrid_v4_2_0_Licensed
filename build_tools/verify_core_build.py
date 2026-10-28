#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
verify_core_build.py
======================
Post-build sanity check for `build_core.py`:

  1. The compiled extension modules import successfully.
  2. No `.py` source for the compiled modules remains on the import
     path (only `.py.source`, which importlib will not pick up).
  3. `Converter` still raises the expected LicenseError (not some
     unrelated crash) when unlicensed -- i.e. the embedded license
     gate survived compilation intact.
  4. The module's compiled file extension is `.so`/`.pyd`, never `.py`/`.pyc`.

Run this after every `build_core.py` invocation and before packaging
a release.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

MODULES = ["converter", "equipment_catalog", "diagram"]


def main():
    failures = []

    for mod_name in MODULES:
        full_name = f"shp2dgs.core.{mod_name}"
        try:
            mod = importlib.import_module(full_name)
        except Exception as e:
            failures.append(f"FAILED to import {full_name}: {e}")
            continue

        origin = getattr(mod.__spec__, "origin", "") or ""
        if origin.endswith(".py"):
            failures.append(f"{full_name} loaded from PLAIN PYTHON SOURCE ({origin}) -- build_core.py did not take effect")
        elif origin.endswith((".so", ".pyd")):
            print(f"OK   {full_name:35s} -> {Path(origin).name}")
        else:
            failures.append(f"{full_name} loaded from unexpected location: {origin!r}")

        src_leftover = ROOT / "shp2dgs" / "core" / f"{mod_name}.py"
        if src_leftover.exists():
            failures.append(f".py source still present and importable: {src_leftover}")

    # Functional check: Converter() with no active license should raise
    # LicenseError (from the embedded gate), not ImportError/AttributeError/etc.
    try:
        from shp2dgs.license.gate import LicenseError
        from shp2dgs.core.converter import Converter

        import os
        import tempfile
        verify_appdata = Path(tempfile.gettempdir()) / "geogridcore_verify_appdata"
        os.environ["LOCALAPPDATA"] = str(verify_appdata)
        os.environ["GEOGRID_TIME_STATE_FILE"] = str(verify_appdata / "timestate.txt")
        verify_appdata.mkdir(parents=True, exist_ok=True)
        lic_file = verify_appdata / "GeoGrid" / "GeoGrid.lic"
        if lic_file.exists():
            lic_file.unlink()

        try:
            Converter(shp_dir="/nonexistent", config={"id_field": "x", "layers": {}}, template={})
            failures.append("Converter() did NOT raise LicenseError while unlicensed -- gate did not survive compilation!")
        except LicenseError:
            print("OK   Converter() correctly raises LicenseError while unlicensed (compiled gate intact)")
    except Exception as e:
        failures.append(f"Functional license-gate check errored unexpectedly: {e!r}")

    print()
    if failures:
        print("VERIFICATION FAILED:")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    print("All GeoGridCore build checks passed.")


if __name__ == "__main__":
    main()
