#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
shp2dgs
=======
Convert Iranian 20 kV distribution-network GIS exports (ESRI shapefiles)
into a DIgSILENT PowerFactory **DGS** deck (multi-sheet ``.xlsx``).

This package is the production-ready, modularised successor to the
original single-file ``shp_to_dgs.py`` script. The public surface is:

- :mod:`shp2dgs.core` -- shapefile reading, type-library handling and the
  :class:`~shp2dgs.core.converter.Converter` engine (no GUI dependency).
- :mod:`shp2dgs.io_formats` -- Excel <-> JSON conversion for the equipment
  type library.
- :mod:`shp2dgs.gui` -- PySide6/PyQt5 desktop application (optional; the
  package still works headless/CLI-only if no Qt binding is installed).
- :mod:`shp2dgs.cli` -- command line entry point used by both
  ``python -m shp2dgs`` and the legacy ``shp_to_dgs.py`` wrapper.

Example (headless/CLI use as a library)::

    from shp2dgs.core.config_io import load_json
    from shp2dgs.core.converter import Converter

    cfg = load_json("config.json")
    tpl = load_json("dgs_template.json")
    conv = Converter("path/to/shapefiles", cfg, tpl)
    conv.build().emit().write("output.xlsx")
    print(conv.summary())
"""

from shp2dgs.core.constants import APP_VERSION as __version__
__all__ = ["__version__"]
