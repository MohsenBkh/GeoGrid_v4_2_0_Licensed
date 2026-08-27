#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
shp_to_dgs.py
=============
Legacy, backward-compatible entry point.

As of v3.0.0 all functionality lives in the ``shp2dgs`` package (see
``shp2dgs/core``, ``shp2dgs/gui``, ``shp2dgs/cli.py``). This thin wrapper
is kept so existing scripts, shortcuts and the PyInstaller ``.spec`` file
that invoke ``python shp_to_dgs.py [args]`` keep working unchanged --
every CLI flag from v2.x (``--shp``, ``--out``, ``--project-name``,
``--terminal-config``, ``--types``, ``--no-gui``, ``--no-graphics``, ...)
is still accepted, and running with no arguments still opens the GUI.

For new code, prefer either::

    python -m shp2dgs [args]

or importing the library directly::

    from shp2dgs.core.converter import Converter
"""

import os
import sys

# Ensure the package is importable when this script is run directly from
# the project root (e.g. a PyInstaller onefile build, or `python
# shp_to_dgs.py` from a source checkout without `pip install -e .`).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from shp2dgs.cli import main  # noqa: E402

if __name__ == "__main__":
    main()
