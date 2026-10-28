#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
core.constants
==============
Static defaults shared by the converter, the CLI and the GUI. Keeping
these in one place avoids the classic bug where the GUI's "default"
terminal-symbol dropdown silently drifts from what the converter actually
falls back to.
"""

#: Application/package version, surfaced in the splash screen, the About
#: dialog, log headers and the ``--version`` CLI flag.
APP_VERSION = "4.2.0"

#: Application display name (window titles, splash screen, installer).
#: The underlying Python package/import path (``shp2dgs``) is kept
#: unchanged for backward compatibility with existing scripts/imports;
#: "GeoGrid" is purely the product/brand name shown to users.
APP_NAME = "GeoGrid"

#: Short tagline shown under the app name on the splash screen / About dialog.
APP_TAGLINE = "GIS \u2192 DIgSILENT DGS Converter"

#: Default mapping of *connection kind* -> DIgSILENT graphic symbol name,
#: used whenever the user (or ``terminal_types_example.json``) does not
#: override a given kind. Keys mirror ``Converter.new_terminal(kind=...)``.
DEFAULT_TERMINAL_TYPES = {
    "line_junction": "PointTerm",
    "device_node": "PointTerm",
    "switch": "PointTerm",
    "fuse": "PointTerm",
    # HV/LV transformer terminals default to a horizontal busbar-style
    # symbol instead of a dot, per product requirement. ``TermStrip`` is
    # used specifically (not the plain "Busbar" symbol) for both sides.
    "transformer_hv": "TermStrip",
    "transformer_lv": "TermStrip",
    "load": "PointTerm",
    "source": "PointTerm",
    # LV-network specific kinds (downstream of the transformer's LV
    # busbar): same default symbols as their MV counterparts, but kept
    # distinct so the diagram can colour LV nodes differently.
    "lv_line_junction": "PointTerm",
    "lv_distribution_box": "PointTerm",
    "lv_fuse": "PointTerm",
    "lv_switch": "PointTerm",
}

#: DIgSILENT common terminal graphic symbols offered in the GUI dropdowns.
TERMINAL_SYMBOL_CHOICES = [
    "PointTerm",
    "TermStrip",
    "Busbar",
    "Terminal",
    "SimpleTerminal",
    "BusbarDouble",
    "PointTerm3",
]

#: Symbols that should be drawn as an elongated horizontal bar rather than
#: a small square dot (rSizeX >> rSizeY, iRot=0), both when emitting
#: ``IntGrf`` rows and when rendering the diagram preview.
BUSBAR_SYMBOLS = {"Busbar", "TermStrip", "BusbarDouble"}

#: Fallback project metadata used whenever the caller does not supply a
#: (partial) override. Mirrors the fields in ``ProjectSettingsDialog``.
DEFAULT_PROJECT_META = {
    "project_name": "Grid",
    "deck_name": "Network_Deck",
    "grid_name": "Grid",
    "nominal_mv_kv": 20.0,
    "frequency_hz": 50.0,
    "description": "",
}

#: Equipment-type sheets that make up the "type library" (as opposed to
#: instance sheets like ``ElmLne``). Shared by the type-library editor and
#: the Excel import/export code so the sheet list only needs to change in
#: one place if the template ever grows more type sheets.
TYPE_LIBRARY_SHEETS = ["TypLne", "TypTr2", "TypLod", "TypFuse"]

#: Network element kinds rendered in the diagram preview, mapped to a
#: display label and a fill colour (hex). Used by ``core.diagram`` and by
#: ``gui.widgets.diagram_view`` so both stay in sync.
DIAGRAM_ELEMENT_STYLE = {
    "d_lin": {"label": "Line", "color": "#4b6f8a"},
    "d_tr2": {"label": "Transformer", "color": "#c97b2f"},
    "d_couple": {"label": "Switch", "color": "#8a4bd6"},
    "d_fuse": {"label": "Fuse", "color": "#d64b4b"},
    "d_load": {"label": "Load", "color": "#3fa34d"},
    "d_net": {"label": "Source", "color": "#d6b74b"},
    "d_lv_lin": {"label": "LV Line", "color": "#7fb3d5"},
    "d_lv_couple": {"label": "LV Switch", "color": "#b48ee0"},
    "d_lv_fuse": {"label": "LV Fuse", "color": "#e59090"},
    "d_cap": {"label": "Capacitor Bank", "color": "#4bb3a0"},
    "d_lv_net": {"label": "DG Source", "color": "#e0c34b"},
}

#: Terminal (node) kinds rendered in the diagram preview.
DIAGRAM_TERMINAL_STYLE = {
    "line_junction": {"label": "Line junction", "color": "#7b8a99"},
    "device_node": {"label": "Device node", "color": "#7b8a99"},
    "switch": {"label": "Switch node", "color": "#8a4bd6"},
    "fuse": {"label": "Fuse node", "color": "#d64b4b"},
    "transformer_hv": {"label": "Transformer HV", "color": "#c97b2f"},
    "transformer_lv": {"label": "Transformer LV", "color": "#c97b2f"},
    "load": {"label": "Load node", "color": "#3fa34d"},
    "source": {"label": "Source node", "color": "#d6b74b"},
    "lv_line_junction": {"label": "LV line junction", "color": "#a9c6da"},
    "lv_distribution_box": {"label": "LV distribution box", "color": "#5aa0c9"},
    "lv_fuse": {"label": "LV fuse node", "color": "#e59090"},
    "lv_switch": {"label": "LV switch node", "color": "#b48ee0"},
}
