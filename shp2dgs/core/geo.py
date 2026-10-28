#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
core.geo
========
Optional reprojection support for populating PowerFactory's ``GPSlat``/
``GPSlon`` attributes, which drive PowerFactory's **Insert \u2192 Geographic
Diagram** feature (a real map-background view, distinct from the local
``IntGrf``/``IntGrfcon`` diagram coordinates the converter always writes).

This is entirely separate from -- and independent of -- the normal
``IntGrf`` diagram graphics: ``IntGrf`` positions objects on the regular
Single Line Diagram using arbitrary local units, while ``GPSlat``/
``GPSlon`` are real WGS84 (EPSG:4326) latitude/longitude degrees used only
by the Geographic Diagram / map view.

Requires the optional ``pyproj`` dependency (``pip install pyproj``). No
part of a normal conversion (``generate_gps=False``, the default) ever
imports or requires ``pyproj`` -- it is only touched when GPS generation
is explicitly requested.

**Source CRS resolution**: shapefiles almost never store real lat/lon --
they use a projected CRS (in Iran, commonly a UTM zone, e.g. EPSG:32639
for UTM Zone 39N). This module resolves the source CRS either from an
explicit EPSG override, or by reading the first ``.prj`` sidecar file
found next to the shapefile layers.
"""

import logging
import os

logger = logging.getLogger("shp2dgs.geo")

try:
    import pyproj
except ImportError as _e:  # pragma: no cover
    pyproj = None
    _PYPROJ_IMPORT_ERROR = _e
else:
    _PYPROJ_IMPORT_ERROR = None

#: PowerFactory's GPSlat/GPSlon attributes are always WGS84 decimal degrees.
TARGET_CRS = "EPSG:4326"


class GeoConfigError(Exception):
    """Raised when the source CRS cannot be resolved (no ``.prj`` sidecar
    found and no explicit EPSG override supplied), or when the override
    EPSG code / the found ``.prj`` WKT is invalid."""


def _ensure_pyproj_available():
    if pyproj is None:
        raise ImportError(
            "GPS coordinate generation requires the 'pyproj' package, which is not "
            "installed. Install it with: pip install pyproj"
        ) from _PYPROJ_IMPORT_ERROR


def find_prj_crs(shp_dir):
    """Search ``shp_dir`` recursively for the first ``.prj`` sidecar file
    and return its WKT projection string.

    Args:
        shp_dir: Directory to search recursively for a ``<layer>.prj`` file.

    Returns:
        str or None: The WKT string of the first ``.prj`` file found, or
        ``None`` if none exists.
    """
    for root, _dirs, files in os.walk(shp_dir):
        for fn in sorted(files):
            if fn.lower().endswith(".prj"):
                path = os.path.join(root, fn)
                try:
                    with open(path, "r", encoding="utf-8", errors="ignore") as f:
                        wkt = f.read().strip()
                    if wkt:
                        logger.info("Found CRS definition: %s", path)
                        return wkt
                except OSError as e:
                    logger.warning("Could not read %s: %s", path, e)
    return None


def resolve_source_crs(shp_dir, source_epsg=None):
    """Resolve the source coordinate reference system used to reproject
    shapefile coordinates into WGS84 lat/lon.

    Args:
        shp_dir: Shapefile input directory (searched recursively for a
            ``.prj`` sidecar if ``source_epsg`` is not given).
        source_epsg: Optional explicit EPSG code override (``int`` or
            numeric ``str``); takes priority over any ``.prj`` file found.

    Returns:
        pyproj.CRS: The resolved source CRS.

    Raises:
        ImportError: If ``pyproj`` is not installed.
        GeoConfigError: If ``source_epsg`` is invalid, or if no override
            was given and no usable ``.prj`` sidecar could be found/parsed.
    """
    _ensure_pyproj_available()
    if source_epsg:
        try:
            crs = pyproj.CRS.from_epsg(int(source_epsg))
            logger.info("Using explicit source CRS override: EPSG:%s", source_epsg)
            return crs
        except Exception as e:
            raise GeoConfigError(f"Invalid EPSG code '{source_epsg}': {e}") from e

    wkt = find_prj_crs(shp_dir)
    if not wkt:
        raise GeoConfigError(
            "GPS coordinate generation is enabled, but no .prj file was found next "
            "to the shapefiles and no source EPSG code was provided. Either make "
            "sure the layers' .prj sidecar files are present, or set an explicit "
            "source CRS (e.g. 32639 for UTM Zone 39N, common for Iran) in "
            "Project Settings \u2192 Geographic (GPS)."
        )
    try:
        return pyproj.CRS.from_wkt(wkt)
    except Exception as e:
        raise GeoConfigError(f"Could not parse the .prj coordinate system: {e}") from e


def build_transformer(source_crs):
    """Build a reusable coordinate transformer from ``source_crs`` to
    WGS84 lat/lon.

    Args:
        source_crs: A ``pyproj.CRS`` instance, e.g. from
            :func:`resolve_source_crs`.

    Returns:
        pyproj.Transformer: Call ``.transform(x, y)`` -> ``(lon, lat)``.
        Built with ``always_xy=True`` so the input/output axis order is
        always ``(x, y)`` / ``(lon, lat)`` regardless of the source CRS's
        native axis convention.

    Raises:
        ImportError: If ``pyproj`` is not installed.
    """
    _ensure_pyproj_available()
    return pyproj.Transformer.from_crs(source_crs, TARGET_CRS, always_xy=True)


def transform_point(transformer, x, y):
    """Transform a single ``(x, y)`` point to ``(lat, lon)``.

    Args:
        transformer: A transformer from :func:`build_transformer`.
        x, y: Source-CRS coordinates.

    Returns:
        tuple: ``(lat, lon)`` in WGS84 decimal degrees, or ``(None, None)``
        if the transform fails for this point (logged as a warning; a
        single bad point never aborts the whole conversion).
    """
    try:
        lon, lat = transformer.transform(x, y)
        if lat != lat or lon != lon:  # NaN check without importing math
            raise ValueError("transform produced NaN")
        return round(lat, 8), round(lon, 8)
    except Exception as e:
        logger.warning("GPS transform failed for point (%s, %s): %s", x, y, e)
        return None, None
