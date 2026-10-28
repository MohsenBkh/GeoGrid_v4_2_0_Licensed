#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gui.dialogs.summary_dialog
============================
Post-conversion results dialog: element counts, terminal-kind breakdown,
and quick actions to open the output file/folder.
"""

import logging
import os
import sys

from shp2dgs.gui.qt_compat import QtCore, QtWidgets, require_gui

logger = logging.getLogger("shp2dgs.gui.summary_dialog")

require_gui()

_FRIENDLY_NAMES = {
    "ElmLne": "ElmLne  (lines)",
    "ElmTr2": "ElmTr2  (transformers)",
    "Elmlod": "Elmlod  (loads)",
    "ElmCoup": "ElmCoup (switches/disconnectors)",
    "RelFuse": "RelFuse (fuses)",
    "ElmXnet": "ElmXnet (external grid / DG sources)",
    "ElmFeeder": "ElmFeeder (feeder)",
    "ElmShnt": "ElmShnt (capacitor banks)",
    "ElmTerm": "ElmTerm (terminals/busbars)",
    "StaCubic": "StaCubic (cubicles)",
    "IntGrf": "IntGrf (graphics)",
    "IntGrfcon": "IntGrfcon (graphic connections)",
}


class SummaryDialog(QtWidgets.QDialog):
    """Shows a table of DGS element counts after a successful conversion.

    Args:
        summary_dict: Result of :meth:`~shp2dgs.core.converter.Converter.summary`.
        output_path: Path of the written ``.xlsx`` file.
        project_meta: The project metadata used for the conversion, for
            display context.
        parent: Optional parent widget.
    """

    def __init__(self, summary_dict, output_path, project_meta=None, parent=None):
        super().__init__(parent)
        self.output_path = output_path
        self.summary = summary_dict
        self.project_meta = project_meta or {}
        self.setWindowTitle("Conversion completed successfully \u2713")
        self.setMinimumWidth(520)
        self._build_ui()

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(24, 24, 24, 24)
        head = QtWidgets.QLabel("DGS output generated successfully")
        hf = head.font()
        hf.setPointSize(13)
        hf.setBold(True)
        head.setFont(hf)
        head.setStyleSheet("color:#0a7d2a;")
        layout.addWidget(head)
        if self.project_meta:
            info = f"Project: {self.project_meta.get('project_name', '')}  |  Deck: {self.project_meta.get('deck_name', '')}"
            layout.addWidget(QtWidgets.QLabel(info))
        path_edit = QtWidgets.QLineEdit(self.output_path)
        path_edit.setReadOnly(True)
        layout.addWidget(path_edit)

        table = QtWidgets.QTableWidget()
        table.setColumnCount(2)
        table.setHorizontalHeaderLabels(["DGS Element", "Count"])
        table.horizontalHeader().setStretchLastSection(True)
        table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeToContents)
        table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        table.setAlternatingRowColors(True)
        warnings = self.summary.get("_warnings") or []
        main_items = [(k, v) for k, v in self.summary.items()
                      if not k.startswith("T_") and k != "_warnings"]
        table.setRowCount(len(main_items))
        for r, (k, v) in enumerate(main_items):
            table.setItem(r, 0, QtWidgets.QTableWidgetItem(_FRIENDLY_NAMES.get(k, k)))
            vi = QtWidgets.QTableWidgetItem(f"{v:,}")
            vi.setTextAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
            table.setItem(r, 1, vi)
        table.resizeRowsToContents()
        table.setMinimumHeight(min(340, 32 * len(main_items) + 50))
        layout.addWidget(table)

        term_items = [(k[2:], v) for k, v in self.summary.items() if k.startswith("T_")]
        if term_items:
            layout.addWidget(QtWidgets.QLabel("Terminal type breakdown:"))
            ttxt = " | ".join([f"{k}:{v}" for k, v in term_items])
            tlab = QtWidgets.QLabel(ttxt)
            tlab.setWordWrap(True)
            tlab.setStyleSheet("color:#444; font-size:9pt;")
            layout.addWidget(tlab)

        if warnings:
            wlab = QtWidgets.QLabel("\u26a0 " + "\n".join(warnings))
            wlab.setWordWrap(True)
            wlab.setStyleSheet("color:#8a5300; background:#fff6e5; padding:8px; border-radius:4px;")
            layout.addWidget(wlab)

        btns = QtWidgets.QDialogButtonBox()
        open_folder_btn = btns.addButton("Open Output Folder", QtWidgets.QDialogButtonBox.ActionRole)
        open_file_btn = btns.addButton("Open File", QtWidgets.QDialogButtonBox.ActionRole)
        ok_btn = btns.addButton(QtWidgets.QDialogButtonBox.Ok)
        ok_btn.setText("Close")
        layout.addWidget(btns)
        open_folder_btn.clicked.connect(self.open_folder)
        open_file_btn.clicked.connect(self.open_file)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.accept)

    def open_folder(self):
        """Open the output file's containing folder in the OS file
        manager. Errors are shown in a message box, never raised."""
        folder = os.path.dirname(os.path.abspath(self.output_path))
        try:
            if sys.platform.startswith("win"):
                os.startfile(folder)  # noqa: S606 - user-initiated, local path
            elif sys.platform == "darwin":
                os.system(f'open "{folder}"')
            else:
                os.system(f'xdg-open "{folder}"')
        except Exception as e:
            logger.warning("Could not open output folder %s: %s", folder, e)
            QtWidgets.QMessageBox.warning(self, "Error", f"Could not open folder:\n{e}")

    def open_file(self):
        """Open the output file with the OS default application. Errors
        are shown in a message box, never raised."""
        try:
            if sys.platform.startswith("win"):
                os.startfile(self.output_path)  # noqa: S606
            elif sys.platform == "darwin":
                os.system(f'open "{self.output_path}"')
            else:
                os.system(f'xdg-open "{self.output_path}"')
        except Exception as e:
            logger.warning("Could not open output file %s: %s", self.output_path, e)
            QtWidgets.QMessageBox.warning(self, "Error", f"Could not open file:\n{e}")
