# -*- coding: utf-8 -*-
"""
Entry point for GeoGrid_Activate.exe -- see shp2dgs/license/activate_cli.py
for the actual guided activation flow. Kept as a tiny separate script
(rather than a GeoGrid.exe command-line flag) so it can be a small,
fast-starting, console-mode executable that a non-technical user can
double-click directly, with no dependency on PySide6/Qt initializing.
"""
from shp2dgs.license.activate_cli import main

if __name__ == "__main__":
    main()
