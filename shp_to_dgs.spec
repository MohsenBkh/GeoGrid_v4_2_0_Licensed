# -*- mode: python ; coding: utf-8 -*-
#
# PyInstaller spec for the SHP -> DGS Converter (v3.0.0, package edition).
#
# Build with:
#   pip install pyinstaller
#   pyinstaller shp_to_dgs.spec
#
# The resulting onedir bundle (dist/shp_to_dgs/) contains everything
# needed to run standalone: the shp2dgs package, its runtime config
# (config.json, dgs_template.json, the example override files) and the
# assets/ folder (icon.png/splash.png, falling back to a drawn
# placeholder if either is missing).
#
# The bundled data files are looked up at runtime via
# shp2dgs.core.config_io.resource_path(), which resolves relative to
# sys._MEIPASS when frozen -- so this spec's `datas` list below is the
# single source of truth for "what ships in the .exe".

import os
import sys
from PyInstaller.utils.hooks import collect_submodules

block_cipher = None

# Windows EXE icons must be .ico (a .png will be silently ignored / can
# error out depending on PyInstaller version). Ship assets/icon.ico for a
# branded taskbar/.exe icon; without it the app still runs fine and uses
# the assets/icon.png (or drawn placeholder) as the in-app window icon.
_ico_path = "assets/icon.ico"
_exe_icon = _ico_path if (sys.platform.startswith("win") and os.path.exists(_ico_path)) else None

# Pull in every shp2dgs submodule explicitly, in case PyInstaller's static
# analysis misses a lazily-imported one (e.g. modules imported inside a
# function body, like shp2dgs.gui.main_window's local imports).
hidden_imports = collect_submodules("shp2dgs")

datas = [
    ("config.json", "."),
    ("dgs_template.json", "."),
    ("types_override_example.json", "."),
    ("terminal_types_example.json", "."),
    ("assets", "assets"),
]

a = Analysis(
    ["shp_to_dgs.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="GeoGrid",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,          # GUI app; use True while debugging startup errors
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=_exe_icon,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="GeoGrid",
)
