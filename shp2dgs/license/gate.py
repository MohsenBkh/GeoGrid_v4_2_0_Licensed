# -*- coding: utf-8 -*-
"""
gate.py
========
Thin enforcement helper, called from >1 independent place (see
docs/developer/anti_tamper.md "defense in depth" table):

  1. shp2dgs/cli.py main()            -- GUI/CLI startup gate
  2. shp2dgs/core/converter.py        -- Converter.__init__ gate (Core)
  3. shp2dgs/gui/app.py               -- shown as the Activation screen

Removing the call in (1) alone does not help an attacker: (2) still
raises LicenseError before any conversion work happens, because the
GUI's "Convert" action always goes through Converter().
"""
from __future__ import annotations

from typing import Optional

from .activation import load_active_license
from .validator import LicenseState, ValidationResult
from . import integrity


class LicenseError(Exception):
    def __init__(self, result: ValidationResult):
        self.result = result
        super().__init__(result.message)


def require_active_license(feature: Optional[str] = None) -> ValidationResult:
    """Raise LicenseError unless there is a currently ACTIVATED license
    (and, if `feature` is given, that feature is included). Returns the
    ValidationResult on success so callers can e.g. show the license
    type in a status bar."""
    result = load_active_license()
    if result.state != LicenseState.ACTIVATED:
        raise LicenseError(result)
    if feature is not None and not result.has_feature(feature):
        raise LicenseError(
            ValidationResult(
                LicenseState.INVALID,
                f"Your license does not include the '{feature}' feature.",
            )
        )
    return result


def require_integrity(app_root) -> None:
    """Wraps integrity.verify() so a failure surfaces as the same
    LicenseError/TAMPERED path the UI already knows how to render."""
    try:
        integrity.verify(app_root)
    except integrity.IntegrityError:
        raise LicenseError(
            ValidationResult(LicenseState.TAMPERED, "A required application file failed integrity verification.")
        )
