# -*- coding: utf-8 -*-
"""
activate_cli.py
=================
A simple, guided, step-by-step activation tool. This exists because
the full Activation flow (create request -> send to vendor -> receive
.lic -> import) has multiple steps, and some users find a plain
question-and-answer flow easier to follow than a dialog with several
buttons. It does exactly the same thing as the GUI's ActivationDialog
underneath (same activation.py functions) -- this is just a friendlier
front door.

Ships as its own small executable, GeoGrid_Activate.exe, so a
non-technical user can just double-click it -- no need to even open
GeoGrid first. Safe to run at any time; if GeoGrid is already
activated, it says so and exits.
"""
from __future__ import annotations

import sys
from pathlib import Path

from . import machine_id as _machine_id
from .activation import build_activation_request, import_license_file, load_active_license
from .validator import LicenseState

_DIVIDER = "=" * 60


def _pause_before_exit(code: int = 0):
    print()
    try:
        input("Press Enter to close this window...")
    except (EOFError, KeyboardInterrupt):
        pass
    sys.exit(code)


def _ask(prompt: str) -> str:
    try:
        return input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        print("\nCancelled.")
        _pause_before_exit(1)


def _clean_path(raw: str) -> Path:
    # Users often drag-and-drop a file, which surrounds the path with
    # quotes on Windows -- strip those.
    return Path(raw.strip().strip('"').strip("'"))


def step_check_existing_status() -> bool:
    """Returns True if already activated (nothing more to do)."""
    result = load_active_license()
    if result.state == LicenseState.ACTIVATED:
        print(f"GeoGrid is already activated on this computer ({result.message})")
        print("Nothing more to do.")
        return True
    if result.state == LicenseState.EXPIRED:
        print("Your previous GeoGrid license has EXPIRED.")
        print("You'll need a new license from your vendor -- let's create a new request.\n")
    elif result.state == LicenseState.MACHINE_MISMATCH:
        print("A license file is present but was issued for a different computer.")
        print("You'll need a license for THIS computer -- let's create a new request.\n")
    return False


def step_show_machine_id() -> str:
    try:
        mid = _machine_id.get_machine_id()
    except RuntimeError:
        print("ERROR: could not read this computer's identity. Please contact support.")
        _pause_before_exit(1)
    print(f"Your Machine ID:  {mid}")
    print("(You'll need to give this to your GeoGrid vendor.)")
    return mid


def step_create_request():
    print()
    print("Creating your activation request file...")
    directory_input = _ask(
        "Where should the request file be saved? (press Enter for Desktop): "
    )
    if directory_input:
        directory = _clean_path(directory_input)
    else:
        directory = Path.home() / "Desktop"
        if not directory.exists():
            directory = Path.home()

    try:
        path, code = build_activation_request(directory)
    except Exception as e:
        print(f"ERROR: could not create the request file: {e}")
        _pause_before_exit(1)
        return

    print()
    print(_DIVIDER)
    print("Activation request created:")
    print(f"  File:         {path}")
    print(f"  Request Code: {code}")
    print(_DIVIDER)
    print()
    print("NEXT STEP:")
    print("  Send this file (or just the Request Code) to your GeoGrid vendor.")
    print("  They will reply with a file named GeoGrid.lic")
    print()
    print("Once you have received GeoGrid.lic, run this tool again and")
    print("choose option 2 to finish activation.")


def step_import_license():
    print()
    path_input = _ask("Path to the GeoGrid.lic file you received (or drag it here): ")
    if not path_input:
        print("No file provided.")
        return

    lic_path = _clean_path(path_input)
    if not lic_path.exists():
        print(f"File not found: {lic_path}")
        return

    result = import_license_file(lic_path)
    print()
    if result.state == LicenseState.ACTIVATED:
        print(_DIVIDER)
        print("SUCCESS -- GeoGrid is now activated on this computer.")
        print(f"License type: {result.payload.license_type}")
        if result.payload.expires_at:
            print(f"Expires:      {result.payload.expires_at}")
        print(_DIVIDER)
        print()
        print("You can now open GeoGrid normally.")
    else:
        print(_DIVIDER)
        print(f"ACTIVATION FAILED: {result.message}")
        print(_DIVIDER)
        if result.state == LicenseState.MACHINE_MISMATCH:
            print("This license was issued for a different computer.")
            print("Run this tool again, choose option 1, and send the NEW")
            print("request to your vendor.")
        elif result.state == LicenseState.EXPIRED:
            print("Contact your GeoGrid vendor for a renewal.")
        else:
            print("Contact your GeoGrid vendor -- the license file may be")
            print("damaged or was not sent correctly.")


def main():
    print(_DIVIDER)
    print("  GeoGrid -- License Activation")
    print(_DIVIDER)
    print()

    if step_check_existing_status():
        _pause_before_exit(0)
        return

    step_show_machine_id()
    print()
    print("What would you like to do?")
    print("  1 - I don't have a license yet (create an activation request)")
    print("  2 - I already received GeoGrid.lic (activate now)")
    print("  3 - Exit")
    print()

    choice = _ask("Enter 1, 2, or 3: ")

    if choice == "1":
        step_create_request()
    elif choice == "2":
        step_import_license()
    else:
        print("Exiting without changes.")

    _pause_before_exit(0)


if __name__ == "__main__":
    main()
