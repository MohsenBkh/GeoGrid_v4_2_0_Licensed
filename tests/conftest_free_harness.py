#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tests/conftest_free_harness.py
==============================
Shared helpers for GeoGrid's dependency-free test scripts.

The application is licensed, and its ``Converter.__init__``/``build()`` call
``shp2dgs.license.gate.require_active_license``. Tests must never need a real
license (CI has none), so :func:`unlocked` patches that single gate for the
duration of a test -- everything else (conversion, type resolution, DGS
output) runs exactly as it does in production.

Run the tests with::

    python -m unittest discover -s tests -v
"""
import os
import sys
import tempfile

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)


class unlocked:
    """Context manager that makes the license gate a no-op."""

    def __enter__(self):
        from shp2dgs.license import gate
        self._original = gate.require_active_license
        gate.require_active_license = lambda feature=None: None
        return self

    def __exit__(self, *exc):
        from shp2dgs.license import gate
        gate.require_active_license = self._original
        return False


def write_layer(shp_dir, name, fields, records, shapes):
    """Write one shapefile layer.

    Args:
        shp_dir: Destination folder.
        name: Layer base name (e.g. ``"ELWFCT"``).
        fields: ``[(field_name, type, size, decimals), ...]`` for pyshp.
        records: List of dicts matching ``fields``.
        shapes: A single point as ``(x, y)``, or a polyline as a list of
            ``(x, y)`` vertices (two or more).
    """
    import shapefile
    os.makedirs(shp_dir, exist_ok=True)
    path = os.path.join(shp_dir, name)
    with shapefile.Writer(path, encoding="utf-8") as w:
        for fname, ftype, size, dec in fields:
            w.field(fname, ftype, size, dec)
        for rec, shp in zip(records, shapes):
            is_point = (len(shp) == 2 and all(isinstance(v, (int, float)) for v in shp))
            if is_point:
                w.point(*shp)
            else:
                w.line([list(shp)])
            w.record(**rec)
    return path


def temp_dir(prefix="geogrid_test_"):
    """Create (and return the path of) a throw-away test directory."""
    return tempfile.mkdtemp(prefix=prefix)
