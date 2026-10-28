#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gui.dialogs.project_settings_dialog
=====================================
The main pre-conversion settings dialog: project metadata, load/electrical
defaults, diagram layout sizing, per-connection-kind terminal symbols, and
access to the equipment type library override (JSON file, or the
interactive :class:`~shp2dgs.gui.dialogs.type_library_editor_dialog.TypeLibraryEditorDialog`).
"""

import copy
import logging
import os

from shp2dgs.core.constants import DEFAULT_TERMINAL_TYPES, TERMINAL_SYMBOL_CHOICES
from shp2dgs.gui.qt_compat import QtCore, QtWidgets, require_gui
from shp2dgs.gui.widgets.layer_mapping_panel import LayerMappingPanel

logger = logging.getLogger("shp2dgs.gui.project_settings_dialog")

require_gui()

_FIELD_MIN_WIDTH = 180


class ProjectSettingsDialog(QtWidgets.QDialog):
    """Collects everything the :class:`~shp2dgs.core.converter.Converter`
    needs beyond the shapefile/output paths: project metadata, electrical
    defaults, diagram sizing, terminal symbol choices, shapefile-layer
    mapping, and an optional equipment-type-library override.

    Args:
        template: Parsed ``dgs_template.json`` dict, used to pre-fill
            defaults (grid/deck name).
        config: Parsed ``config.json`` dict, used to pre-fill electrical,
            graphics and layer-mapping defaults.
        shp_dir: Optional current input shapefile folder, used to scan
            for available layer files in the Shapefile Layers tab.
        previous: Optional dict of settings collected from an earlier
            time this dialog was opened/accepted in the same session
            (see :meth:`get_all_settings`) -- when given, every field is
            pre-filled from it instead of from ``template``/``config``
            defaults, so reopening the dialog shows what was last saved
            rather than resetting.
        types_override: The equipment-type-library override currently
            active on the parent window, if any -- shown read-only here.
            The Type Library Editor itself is opened via the parent
            window's single canonical entry point (see
            :meth:`open_type_editor`), so there is exactly one place that
            edits it and one place that holds the result, instead of two
            that could drift apart.
        parent: Optional parent widget.
    """

    def __init__(self, template, config, shp_dir=None, previous=None, types_override=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Project Settings \u2013 SHP \u2192 DGS")
        self.setMinimumWidth(680)
        self.resize(680, 620)
        self.template = template
        self.config = config
        self.shp_dir = shp_dir
        self.previous = previous or {}
        self.types_override = types_override
        self._build_ui()
        self._load_defaults()

    @staticmethod
    def _new_form(widget):
        f = QtWidgets.QFormLayout(widget)
        f.setHorizontalSpacing(12)
        f.setVerticalSpacing(8)
        f.setLabelAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        f.setFormAlignment(QtCore.Qt.AlignTop)
        f.setFieldGrowthPolicy(QtWidgets.QFormLayout.AllNonFixedFieldsGrow)
        return f

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        tabs = QtWidgets.QTabWidget()
        layout.addWidget(tabs)

        self._build_project_tab(tabs)
        self._build_electrical_tab(tabs)
        self._build_graphics_tab(tabs)
        self._build_terminal_types_tab(tabs)
        self._build_gps_tab(tabs)
        self._build_layer_mapping_tab(tabs)
        self._build_type_library_tab(tabs)

        btns = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        btns.button(QtWidgets.QDialogButtonBox.Ok).setText("Save")
        btns.button(QtWidgets.QDialogButtonBox.Cancel).setText("Cancel")
        layout.addWidget(btns)

    def _build_layer_mapping_tab(self, tabs):
        self.layer_panel = LayerMappingPanel(self.config)
        self.layer_panel.set_shapefile_directory(self.shp_dir)
        tabs.addTab(self.layer_panel, "Shapefile Layers")

    def _build_project_tab(self, tabs):
        w1 = QtWidgets.QWidget()
        f1 = self._new_form(w1)
        self.ed_project = QtWidgets.QLineEdit()
        self.ed_project.setMinimumWidth(_FIELD_MIN_WIDTH)
        self.ed_deck = QtWidgets.QLineEdit()
        self.ed_deck.setMinimumWidth(_FIELD_MIN_WIDTH)
        self.ed_grid = QtWidgets.QLineEdit()
        self.ed_grid.setMinimumWidth(_FIELD_MIN_WIDTH)
        self.ed_desc = QtWidgets.QLineEdit()
        self.ed_desc.setMinimumWidth(_FIELD_MIN_WIDTH)
        self.sp_vnom = QtWidgets.QDoubleSpinBox()
        self.sp_vnom.setRange(0.1, 500)
        self.sp_vnom.setDecimals(2)
        self.sp_vnom.setSuffix(" kV")
        self.sp_vnom.setMinimumWidth(_FIELD_MIN_WIDTH)
        self.sp_freq = QtWidgets.QDoubleSpinBox()
        self.sp_freq.setRange(10, 100)
        self.sp_freq.setValue(50)
        self.sp_freq.setSuffix(" Hz")
        self.sp_freq.setMinimumWidth(_FIELD_MIN_WIDTH)
        f1.addRow("Project name:", self.ed_project)
        f1.addRow("Deck name (IntGrfnet):", self.ed_deck)
        f1.addRow("Grid name (ElmNet):", self.ed_grid)
        f1.addRow("Description:", self.ed_desc)
        f1.addRow("Nominal MV voltage:", self.sp_vnom)
        f1.addRow("Frequency:", self.sp_freq)
        tabs.addTab(w1, "Project")

    def _build_electrical_tab(self, tabs):
        w2 = QtWidgets.QWidget()
        f2 = self._new_form(w2)
        self.sp_load_factor = QtWidgets.QDoubleSpinBox()
        self.sp_load_factor.setRange(0.01, 2.0)
        self.sp_load_factor.setSingleStep(0.01)
        self.sp_load_factor.setDecimals(3)
        self.sp_load_factor.setMinimumWidth(_FIELD_MIN_WIDTH)
        self.sp_cosphi = QtWidgets.QDoubleSpinBox()
        self.sp_cosphi.setRange(0.5, 1.0)
        self.sp_cosphi.setSingleStep(0.01)
        self.sp_cosphi.setDecimals(3)
        self.sp_cosphi.setMinimumWidth(_FIELD_MIN_WIDTH)
        self.sp_lv = QtWidgets.QDoubleSpinBox()
        self.sp_lv.setRange(0.1, 1.0)
        self.sp_lv.setDecimals(3)
        self.sp_lv.setSuffix(" kV")
        self.sp_lv.setMinimumWidth(_FIELD_MIN_WIDTH)
        f2.addRow("Transformer load factor:", self.sp_load_factor)
        f2.addRow("Load cos \u03c6:", self.sp_cosphi)
        f2.addRow("LV voltage:", self.sp_lv)
        tabs.addTab(w2, "Load / Electrical")

    def _build_graphics_tab(self, tabs):
        w2b = QtWidgets.QWidget()
        f2b = self._new_form(w2b)
        self.sp_elem_size = QtWidgets.QDoubleSpinBox()
        self.sp_elem_size.setRange(0.05, 5.0)
        self.sp_elem_size.setSingleStep(0.05)
        self.sp_elem_size.setDecimals(2)
        self.sp_elem_size.setMinimumWidth(_FIELD_MIN_WIDTH)
        self.sp_term_size = QtWidgets.QDoubleSpinBox()
        self.sp_term_size.setRange(0.01, 5.0)
        self.sp_term_size.setSingleStep(0.01)
        self.sp_term_size.setDecimals(2)
        self.sp_term_size.setMinimumWidth(_FIELD_MIN_WIDTH)
        self.sp_node_spacing = QtWidgets.QDoubleSpinBox()
        self.sp_node_spacing.setRange(0.2, 20.0)
        self.sp_node_spacing.setSingleStep(0.1)
        self.sp_node_spacing.setDecimals(2)
        self.sp_node_spacing.setMinimumWidth(_FIELD_MIN_WIDTH)
        f2b.addRow("Element symbol size:", self.sp_elem_size)
        f2b.addRow("Point terminal size:", self.sp_term_size)
        f2b.addRow("Node spacing (transformer/load layout):", self.sp_node_spacing)
        gnote = QtWidgets.QLabel(
            "Element symbol size sets how big the transformer/load/etc. boxes are drawn "
            "in the DIgSILENT diagram. Point terminal size sets how big a plain (non-busbar) "
            "terminal's dot is drawn. Node spacing sets how far apart the HV busbar, "
            "transformer, LV busbar and load are placed so they don't overlap. The "
            "transformer's HV-side TermStrip is always drawn twice as long as its LV side.")
        gnote.setWordWrap(True)
        gnote.setStyleSheet("color:#666; font-size:9pt;")
        f2b.addRow(gnote)
        tabs.addTab(w2b, "Graphics")

    def _build_terminal_types_tab(self, tabs):
        w3 = QtWidgets.QWidget()
        f3 = self._new_form(w3)
        self.term_combos = {}
        terminal_kinds = [
            ("line_junction", "Line-to-line junction terminal"),
            ("switch", "Switch / disconnector terminal"),
            ("fuse", "Fuse terminal"),
            ("transformer_hv", "Transformer HV terminal"),
            ("transformer_lv", "Transformer LV terminal"),
            ("load", "Load terminal"),
            ("source", "Source / feeder terminal"),
            ("device_node", "Generic node terminal"),
        ]
        for key, label in terminal_kinds:
            cb = QtWidgets.QComboBox()
            cb.setEditable(True)
            cb.setMinimumWidth(_FIELD_MIN_WIDTH)
            cb.addItems(TERMINAL_SYMBOL_CHOICES)
            cb.setCurrentText(DEFAULT_TERMINAL_TYPES.get(key, "PointTerm"))
            self.term_combos[key] = cb
            f3.addRow(label + " :", cb)
        note = QtWidgets.QLabel(
            "DIgSILENT symbols: PointTerm, TermStrip, Busbar, Terminal \u2026\n"
            "You can type a custom value. Transformer HV/LV terminals default to "
            "\"TermStrip\" (horizontal bar).")
        note.setWordWrap(True)
        note.setStyleSheet("color:#666; font-size:9pt;")
        f3.addRow(note)
        tabs.addTab(w3, "Terminal Types")

    def _build_gps_tab(self, tabs):
        w5 = QtWidgets.QWidget()
        v5 = QtWidgets.QVBoxLayout(w5)
        v5.setSpacing(10)

        self.chk_gps = QtWidgets.QCheckBox(
            "Generate GPS coordinates for PowerFactory's Geographic Diagram (GPSlat/GPSlon)")
        v5.addWidget(self.chk_gps)

        form_widget = QtWidgets.QWidget()
        form = self._new_form(form_widget)
        self.ed_epsg = QtWidgets.QLineEdit()
        self.ed_epsg.setPlaceholderText("Auto-detect from .prj (leave blank), or enter EPSG code, e.g. 32639")
        self.ed_epsg.setMinimumWidth(_FIELD_MIN_WIDTH)
        form.addRow("Source CRS (EPSG code):", self.ed_epsg)
        v5.addWidget(form_widget)

        self.chk_gps_lines = QtWidgets.QCheckBox(
            "Also include full line route GPS points (GPSlat:i/GPSlon:i) \u2013 for accurate line "
            "routing on the Geographic Diagram, not just straight endpoint-to-endpoint")
        v5.addWidget(self.chk_gps_lines)

        note = QtWidgets.QLabel(
            "Requires the optional 'pyproj' package (pip install pyproj).\n"
            "This is unrelated to the regular diagram graphics (IntGrf) above \u2014 it "
            "populates real WGS84 latitude/longitude so PowerFactory can show the "
            "network on a map background (Insert \u2192 Geographic Diagram).\n"
            "The shapefiles' source coordinate system is auto-detected from a .prj "
            "sidecar file if present; otherwise enter its EPSG code above "
            "(e.g. 32639 = UTM Zone 39N, common for Iran). If neither is available, "
            "GPS coordinates are simply skipped (with a note in the log) \u2014 the rest "
            "of the conversion still completes normally.\n"
            "After importing the DGS file, rebuild the Geographic Diagram once in "
            "PowerFactory to show the new coordinates."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color:#666; font-size:9pt;")
        v5.addWidget(note)
        v5.addStretch()

        def _toggle_gps_fields(checked):
            self.ed_epsg.setEnabled(checked)
            self.chk_gps_lines.setEnabled(checked)

        self.chk_gps.toggled.connect(_toggle_gps_fields)
        self.chk_gps.setChecked(True)
        self.chk_gps_lines.setChecked(True)
        _toggle_gps_fields(True)

        tabs.addTab(w5, "Geographic (GPS)")

    def _build_type_library_tab(self, tabs):
        w4 = QtWidgets.QWidget()
        v4 = QtWidgets.QVBoxLayout(w4)
        v4.setSpacing(10)
        info = QtWidgets.QLabel(
            "The equipment type library (line/transformer/load/fuse types) is edited in one "
            "place -- the Type Library Editor, also reachable from the Type Library tab and the "
            "Tools menu. Whatever is active there is what this project will use; there's nothing "
            "separate to configure here."
        )
        info.setWordWrap(True)
        v4.addWidget(info)
        self.type_library_status_label = QtWidgets.QLabel()
        self.type_library_status_label.setWordWrap(True)
        self.type_library_status_label.setStyleSheet("color:#444; padding:6px 0;")
        v4.addWidget(self.type_library_status_label)
        btn_edit_types = QtWidgets.QPushButton("Open Type Library Editor\u2026")
        btn_edit_types.clicked.connect(self.open_type_editor)
        v4.addWidget(btn_edit_types, 0, QtCore.Qt.AlignLeft)
        v4.addStretch()
        tabs.addTab(w4, "Type Library")
        self._refresh_type_library_status()

    def _refresh_type_library_status(self):
        if self.types_override:
            n = sum(len(v.get("rows", [])) for v in self.types_override.values() if isinstance(v, dict) and "rows" in v)
            self.type_library_status_label.setText(
                f"Custom type library active ({n} type row(s) across "
                f"{len(self.types_override)} sheet(s)).")
        else:
            self.type_library_status_label.setText("Using template defaults (no custom type library loaded).")

    def _load_defaults(self):
        prev_meta = self.previous.get("project_meta") or {}
        try:
            elmnet_row = self.template["ElmNet"]["rows"][0]
            default_name = str(elmnet_row[1] if len(elmnet_row) > 1 else "Grid")
        except Exception as e:
            logger.warning("Could not read ElmNet defaults from template: %s", e)
            default_name = "Grid"
        self.ed_project.setText(prev_meta.get("project_name", default_name))
        self.ed_grid.setText(prev_meta.get("grid_name", default_name))
        self.ed_desc.setText(prev_meta.get("description", ""))
        try:
            grf_row = self.template["IntGrfnet"]["rows"][0]
            default_deck = str(grf_row[1] if len(grf_row) > 1 else "Network_Deck")
        except Exception as e:
            logger.warning("Could not read IntGrfnet defaults from template: %s", e)
            default_deck = "Network_Deck"
        self.ed_deck.setText(prev_meta.get("deck_name", default_deck))

        self.sp_vnom.setValue(float(prev_meta.get("nominal_mv_kv", self.config.get("nominal_mv_kv", 20.0))))
        self.sp_freq.setValue(float(prev_meta.get("frequency_hz", self.config.get("grid_frequency_hz", 50.0))))
        load_cfg = self.config.get("load", {})
        self.sp_load_factor.setValue(float(prev_meta.get("loading_factor", load_cfg.get("loading_factor", 0.7))))
        self.sp_cosphi.setValue(float(prev_meta.get("power_factor_cos", load_cfg.get("power_factor_cos", 0.9))))
        self.sp_lv.setValue(float(prev_meta.get("lv_voltage_kv",
                                                 self.config.get("transformer", {}).get("lv_voltage_kv", 0.4))))
        gcfg = self.config.get("graphics", {})
        self.sp_elem_size.setValue(float(prev_meta.get("element_size", gcfg.get("element_size", 0.5))))
        self.sp_term_size.setValue(float(prev_meta.get("terminal_size", gcfg.get("terminal_size", 0.2))))
        self.sp_node_spacing.setValue(float(prev_meta.get("node_spacing", gcfg.get("node_spacing", 5))))

        prev_terminal_types = self.previous.get("terminal_types")
        if prev_terminal_types:
            for k, cb in self.term_combos.items():
                if k in prev_terminal_types:
                    cb.setCurrentText(prev_terminal_types[k])

        prev_gps = self.previous.get("gps_settings")
        if prev_gps:
            self.chk_gps.setChecked(prev_gps.get("generate_gps", True))
            self.chk_gps_lines.setChecked(prev_gps.get("gps_line_vertices", True))
            self.ed_epsg.setText(prev_gps.get("source_epsg") or "")

        prev_layers = self.previous.get("layer_mapping") or {}
        self.layer_panel.set_state(
            overrides=prev_layers.get("overrides"),
            include_mv=prev_layers.get("include_mv", True),
            include_lv=prev_layers.get("include_lv", False),
        )

    def open_type_editor(self):
        """Delegate to the parent window's single canonical Type Library
        Editor entry point, then refresh this dialog's status line from
        the result -- rather than opening a second, independent editor
        instance with its own separate state (the previous source of the
        "edits here don't show up in Project Settings" bug)."""
        parent = self.parent()
        if parent is None or not hasattr(parent, "open_type_library_editor"):
            QtWidgets.QMessageBox.warning(self, "Unavailable",
                                           "The Type Library Editor isn't available from here; "
                                           "use the Type Library tab or Tools menu instead.")
            return
        parent.open_type_library_editor()
        self.types_override = getattr(parent, "types_override", None)
        self._refresh_type_library_status()

    def get_project_meta(self):
        """Return the collected project/electrical/graphics settings.

        Returns:
            dict: Ready to pass as ``project_meta`` to
            :class:`~shp2dgs.core.converter.Converter`.
        """
        return {
            "project_name": self.ed_project.text().strip() or "Grid",
            "deck_name": self.ed_deck.text().strip() or "Network_Deck",
            "grid_name": self.ed_grid.text().strip() or "Grid",
            "description": self.ed_desc.text().strip(),
            "nominal_mv_kv": self.sp_vnom.value(),
            "frequency_hz": self.sp_freq.value(),
            "loading_factor": self.sp_load_factor.value(),
            "power_factor_cos": self.sp_cosphi.value(),
            "lv_voltage_kv": self.sp_lv.value(),
            "element_size": self.sp_elem_size.value(),
            "terminal_size": self.sp_term_size.value(),
            "node_spacing": self.sp_node_spacing.value(),
        }

    def get_terminal_types(self):
        """Return the collected connection-kind -> symbol mapping."""
        return {k: cb.currentText().strip() or "PointTerm" for k, cb in self.term_combos.items()}

    def get_gps_settings(self):
        """Return the collected GPS/Geographic Diagram settings.

        Returns:
            dict: ``{"generate_gps": bool, "gps_line_vertices": bool,
            "source_epsg": str or None}``, ready to unpack into
            :class:`~shp2dgs.core.converter.Converter`'s corresponding
            constructor arguments.
        """
        epsg = self.ed_epsg.text().strip()
        return {
            "generate_gps": self.chk_gps.isChecked(),
            "gps_line_vertices": self.chk_gps_lines.isChecked(),
            "source_epsg": epsg or None,
        }

    def get_layer_mapping(self):
        """Return the shapefile-layer mapping settings.

        Returns:
            dict: ``{"overrides": {...}, "include_mv": bool, "include_lv": bool}``,
            ready to unpack into :class:`~shp2dgs.core.converter.Converter`'s
            ``layer_overrides``/``include_mv``/``include_lv`` arguments.
        """
        return {
            "overrides": self.layer_panel.get_overrides(),
            "include_mv": self.layer_panel.get_include_mv(),
            "include_lv": self.layer_panel.get_include_lv(),
        }

    def get_all_settings(self):
        """Bundle every collected setting into one dict, suitable to pass
        back in as ``previous=`` the next time this dialog is opened so
        it reopens showing what was last saved instead of resetting to
        defaults. The type library is intentionally not part of this --
        it lives solely on the parent window (see :meth:`open_type_editor`).

        Returns:
            dict: ``{"project_meta", "terminal_types", "gps_settings",
            "layer_mapping"}``.
        """
        return {
            "project_meta": self.get_project_meta(),
            "terminal_types": self.get_terminal_types(),
            "gps_settings": self.get_gps_settings(),
            "layer_mapping": self.get_layer_mapping(),
        }
