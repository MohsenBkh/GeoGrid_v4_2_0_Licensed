#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
core.shapefile_io
==================
Thin, defensive wrapper around ``pyshp`` for reading the ELW* GIS layers.
"""

import logging
import os

try:
    import shapefile
except ImportError as _e:  # pragma: no cover - surfaced at import time by caller
    shapefile = None
    _IMPORT_ERROR = _e
else:
    _IMPORT_ERROR = None

logger = logging.getLogger("shp2dgs.shapefile_io")


def ensure_pyshp_available():
    """Raise a clear, actionable error if ``pyshp`` is not installed.

    Call this near the start of any entry point (CLI/GUI) so the user gets
    ``pip install pyshp`` instead of a raw ``ModuleNotFoundError`` deep in
    a traceback.
    """
    if shapefile is None:
        raise ImportError(
            "The 'pyshp' package is required to read shapefiles but is not "
            "installed. Install it with: pip install pyshp"
        ) from _IMPORT_ERROR


def s(v):
    """Coerce a shapefile attribute value to a stripped string.

    Args:
        v: Raw attribute value (may be ``None``, ``str``, ``int``, ``float``).

    Returns:
        str: ``""`` for ``None``, otherwise ``str(v).strip()``.
    """
    return "" if v is None else str(v).strip()


def _find_layer_path(shp_dir, name):
    """Return the base path (without extension) for layer ``name`` if its
    ``.shp`` exists directly under ``shp_dir`` or anywhere in a recursive
    walk of it (handles ZIP-extracted exports that nest layers in
    sub-folders); ``None`` if not found either way."""
    direct = os.path.join(shp_dir, name)
    if os.path.exists(direct + ".shp"):
        return direct
    for root, _dirs, files in os.walk(shp_dir):
        if name + ".shp" in files:
            found = os.path.join(root, name)
            logger.info("Found layer %s at: %s.shp", name, found)
            return found
    return None


def _w_prefix_alias(name):
    """Toggle the 'W' this utility's layer names sometimes carry right
    after the 'EL' prefix, e.g. ``"ELOML"`` <-> ``"ELWOML"``. Returns
    ``None`` if ``name`` doesn't start with ``"EL"`` at all."""
    if name.startswith("ELW"):
        return "EL" + name[3:]
    if name.startswith("EL"):
        return "ELW" + name[2:]
    return None


def read_layer(shp_dir, name, progress_cb=None):
    """Read one shapefile layer (e.g. ``ELWOML``) from ``shp_dir``.

    The layer is first looked for directly at ``shp_dir/name.shp``; if not
    found there, ``shp_dir`` is walked recursively (handles ZIP-extracted
    exports that nest layers in sub-folders).

    Args:
        shp_dir: Directory containing (or containing subfolders with) the
            ``.shp``/``.shx``/``.dbf`` triplets.
        name: Layer base name without extension, e.g. ``"ELWCBR"``.
        progress_cb: Optional ``callback(percent, message)`` invoked once
            after a successful read (percent is informational only here;
            callers typically pass ``None`` and compute their own percent).

    Returns:
        tuple: ``(records, shapes)`` where ``records`` is a list of dicts
        (field name -> value) and ``shapes`` is the parallel list of
        ``pyshp`` shape objects. Returns ``([], [])`` both when the layer
        file is missing (logged as a warning -- not every deck contains
        every optional layer, e.g. no fuses) and when it exists but is
        corrupt/truncated/unreadable (logged as an error, since that
        usually indicates a real data problem worth investigating -- but
        one bad optional layer should not abort an otherwise-good
        conversion of everything else).
    """
    ensure_pyshp_available()
    path = _find_layer_path(shp_dir, name)
    if path is None:
        alias = _w_prefix_alias(name)
        if alias:
            path = _find_layer_path(shp_dir, alias)
            if path is not None:
                logger.info(
                    "Layer '%s' not found; using '%s' instead (this utility's shapefiles have been "
                    "seen with and without a 'W' after 'EL' for the same layer -- both are tried "
                    "automatically). Consider updating the Layer Mapping settings to match what your "
                    "exports actually use, so this fallback isn't needed.", name, alias)
                name = alias
    if path is None:
        logger.warning("Shapefile layer missing: %s (also tried '%s') in %s",
                        name, _w_prefix_alias(name) or "(no alias)", shp_dir)
        return [], []
    try:
        r = shapefile.Reader(path, encoding="utf-8")
        flds = [f[0] for f in r.fields[1:]]
        recs = [dict(zip(flds, list(rec))) for rec in r.records()]
        shapes = r.shapes()
        logger.info("Read layer %s: %d records", name, len(recs))
        if progress_cb:
            progress_cb(0, f"Reading {name}: {len(recs)} records")
        return recs, shapes
    except Exception as e:
        # Broad catch is intentional: pyshp/struct can raise several
        # different exception types for a corrupt/truncated .shp/.dbf
        # (shapefile.ShapefileException, struct.error, UnicodeDecodeError,
        # ...). Any of them means "this one layer is unusable" -- log it
        # clearly so the user can fix/re-export that layer, but let the
        # rest of the conversion proceed rather than aborting entirely.
        logger.error("Shapefile layer %s is corrupt or unreadable, skipping it: %s", name, e)
        return [], []
