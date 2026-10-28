#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gui.widgets.layer_mapping_panel
==================================
A panel listing every equipment slot in
:mod:`shp2dgs.core.equipment_catalog`, each with a dropdown to manually
pick which shapefile in the input folder it corresponds to -- so a
conversion never silently produces wrong/incomplete results just because
a utility's export uses different layer names than GeoGrid's defaults.
"""

import logging

from shp2dgs.core.equipment_catalog import EQUIPMENT_CATALOG, find_available_shapefiles, slots_for_level
from shp2dgs.gui.qt_compat import QtCore, QtWidgets, require_gui

logger = logging.getLogger("shp2dgs.gui.layer_mapping_panel")

require_gui()

_DEFAULT_PREFIX = "(default"
_SKIP_LABEL = "(none \u2013 skip)"


class LayerMappingPanel(QtWidgets.QWidget):
    """Equipment-to-shapefile mapping panel.

    Args:
        config: The current ``config.json`` dict (used for each slot's
            default layer name(s), shown in the dropdown label).
        parent: Optional parent widget.
    """

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = config
        self._combos = {}  # slot key -> QComboBox
        self._shp_dir = ""
        self._build_ui()

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        toggle_row = QtWidgets.QHBoxLayout()
        self.chk_include_mv = QtWidgets.QCheckBox("Convert MV (medium voltage) equipment")
        self.chk_include_lv = QtWidgets.QCheckBox("Convert LV (low voltage) equipment")
        self.chk_include_mv.setChecked(True)
        self.chk_include_lv.setChecked(False)
        toggle_row.addWidget(self.chk_include_mv)
        toggle_row.addWidget(self.chk_include_lv)
        toggle_row.addStretch()
        self.btn_refresh = QtWidgets.QPushButton("Rescan Folder")
        self.btn_refresh.clicked.connect(self.refresh_available_files)
        toggle_row.addWidget(self.btn_refresh)
        layout.addLayout(toggle_row)

        note = QtWidgets.QLabel(
            "Equipment is matched to shapefiles by name automatically (e.g. \u201cMV Overhead Line\u201d "
            "\u2192 ELWOML). If your export uses different layer names, pick the correct file for each "
            "row below \u2014 this always overrides the automatic match.")
        note.setWordWrap(True)
        note.setStyleSheet("color:#666; font-size:9pt;")
        layout.addWidget(note)

        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QtWidgets.QWidget()
        inner_layout = QtWidgets.QVBoxLayout(inner)

        for level, title in (("mv", "Medium Voltage (MV)"), ("lv", "Low Voltage (LV)")):
            box = QtWidgets.QGroupBox(title)
            form = QtWidgets.QFormLayout(box)
            form.setLabelAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
            for slot in slots_for_level(level):
                combo = QtWidgets.QComboBox()
                combo.setEditable(False)
                combo.setMinimumWidth(260)
                self._combos[slot.key] = combo
                label_text = slot.label
                if slot.notes:
                    label_text += "  \u24d8"
                    combo.setToolTip(slot.notes)
                form.addRow(label_text + ":", combo)
            inner_layout.addWidget(box)
        inner_layout.addStretch()
        scroll.setWidget(inner)
        layout.addWidget(scroll)

        self.refresh_available_files()

    def _default_label(self, slot):
        val = self.config.get("layers", {}).get(slot.key)
        if not val:
            return f"{_DEFAULT_PREFIX}: none)"
        if isinstance(val, str):
            return f"{_DEFAULT_PREFIX}: {val})"
        return f"{_DEFAULT_PREFIX}: {', '.join(val)})"

    def set_shapefile_directory(self, shp_dir):
        """Update the input folder to scan for available layer files, and
        refresh the dropdowns."""
        self._shp_dir = shp_dir or ""
        self.refresh_available_files()

    def refresh_available_files(self):
        """Rescan the current shapefile directory and repopulate every
        dropdown, preserving each one's current selection where still
        valid."""
        available = find_available_shapefiles(self._shp_dir)
        for slot in EQUIPMENT_CATALOG:
            combo = self._combos[slot.key]
            current = combo.currentText()
            combo.blockSignals(True)
            combo.clear()
            combo.addItem(self._default_label(slot))
            combo.addItem(_SKIP_LABEL)
            for name in available:
                combo.addItem(name)
            idx = combo.findText(current)
            combo.setCurrentIndex(idx if idx >= 0 else 0)
            combo.blockSignals(False)

    def get_overrides(self):
        """Return ``{slot_key: layer_name_or_None}`` for every slot the
        user explicitly changed away from "(default)". Slots left on
        "(default)" are omitted (so :func:`~shp2dgs.core.equipment_catalog.resolve_layer_config`
        falls through to ``config["layers"]`` for them).

        Returns:
            dict: Ready to pass as ``layer_overrides`` to
            :class:`~shp2dgs.core.converter.Converter`.
        """
        overrides = {}
        for key, combo in self._combos.items():
            text = combo.currentText()
            if text.startswith(_DEFAULT_PREFIX):
                continue
            overrides[key] = None if text == _SKIP_LABEL else text
        return overrides

    def get_include_mv(self):
        return self.chk_include_mv.isChecked()

    def get_include_lv(self):
        return self.chk_include_lv.isChecked()

    def set_state(self, overrides=None, include_mv=True, include_lv=False):
        """Restore a previously-collected mapping state (see
        :meth:`get_overrides`/:meth:`get_include_mv`/:meth:`get_include_lv`),
        e.g. when the Project Settings dialog is reopened and should show
        the last-used mapping instead of resetting to defaults.
        """
        self.chk_include_mv.setChecked(include_mv)
        self.chk_include_lv.setChecked(include_lv)
        if not overrides:
            return
        for key, value in overrides.items():
            combo = self._combos.get(key)
            if combo is None:
                continue
            target_text = _SKIP_LABEL if value is None else value
            idx = combo.findText(target_text)
            if idx >= 0:
                combo.setCurrentIndex(idx)
