# -*- coding: utf-8 -*-
"""
activation_dialog.py
=====================
GeoGrid Activation screen (offline). Shown at startup whenever
license.gate.require_active_license() would fail. Lets the user:

  * See their Machine ID
  * Save an activation request file (GeoGrid_Activation_Request.ggr)
    and see the equivalent short Request Code
  * Browse to and import a GeoGrid.lic file they received back

On success, closes with QDialog.Accepted and the caller proceeds to
open MainWindow. This dialog performs NO network I/O -- activation is
fully offline (spec section 5).
"""
from __future__ import annotations

from pathlib import Path

from .qt_compat import QtWidgets, QtCore

from shp2dgs.license import machine_id as _machine_id
from shp2dgs.license.activation import build_activation_request, import_license_file, load_active_license
from shp2dgs.license.validator import LicenseState


_ERROR_TEXT = {
    LicenseState.EXPIRED: "This license has expired. Please contact your GeoGrid vendor for a renewal.",
    LicenseState.INVALID: "The license file is corrupted, or its signature is invalid.",
    LicenseState.TAMPERED: "A required application file failed integrity verification. Please reinstall GeoGrid.",
    LicenseState.MACHINE_MISMATCH: "This license belongs to a different computer. Contact your GeoGrid vendor.",
    LicenseState.UNLICENSED: "",
}


class ActivationDialog(QtWidgets.QDialog):
    def __init__(self, parent=None, initial_message: str = ""):
        super().__init__(parent)
        self.setWindowTitle("GeoGrid \u2014 License Activation")
        self.setMinimumWidth(480)
        self.setModal(True)

        try:
            self._machine_id = _machine_id.get_machine_id()
        except RuntimeError:
            self._machine_id = "UNAVAILABLE"

        layout = QtWidgets.QVBoxLayout(self)

        title = QtWidgets.QLabel("<h2>GeoGrid</h2><h3>License Activation</h3>")
        layout.addWidget(title)

        self.status_label = QtWidgets.QLabel(initial_message or "Status: Not Activated")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        mid_box = QtWidgets.QGroupBox("Machine ID")
        mid_layout = QtWidgets.QHBoxLayout(mid_box)
        self.machine_id_field = QtWidgets.QLineEdit(self._machine_id)
        self.machine_id_field.setReadOnly(True)
        mid_layout.addWidget(self.machine_id_field)
        copy_btn = QtWidgets.QPushButton("Copy")
        copy_btn.clicked.connect(self._copy_machine_id)
        mid_layout.addWidget(copy_btn)
        layout.addWidget(mid_box)

        req_box = QtWidgets.QGroupBox("Step 1 \u2014 Create Activation Request")
        req_layout = QtWidgets.QVBoxLayout(req_box)
        req_layout.addWidget(QtWidgets.QLabel(
            "Save the activation request file and send it to your GeoGrid vendor."
        ))
        save_req_btn = QtWidgets.QPushButton("Save Activation Request\u2026")
        save_req_btn.clicked.connect(self._save_request)
        req_layout.addWidget(save_req_btn)
        self.request_code_field = QtWidgets.QLineEdit()
        self.request_code_field.setReadOnly(True)
        self.request_code_field.setPlaceholderText("Request Code will appear here")
        req_layout.addWidget(self.request_code_field)
        layout.addWidget(req_box)

        act_box = QtWidgets.QGroupBox("Step 2 \u2014 Import License")
        act_layout = QtWidgets.QVBoxLayout(act_box)
        act_layout.addWidget(QtWidgets.QLabel(
            "Once you receive GeoGrid.lic from your vendor, import it here."
        ))
        import_btn = QtWidgets.QPushButton("Browse and Activate\u2026")
        import_btn.clicked.connect(self._import_license)
        act_layout.addWidget(import_btn)
        layout.addWidget(act_box)

        btn_row = QtWidgets.QHBoxLayout()
        btn_row.addStretch(1)
        self.close_btn = QtWidgets.QPushButton("Exit GeoGrid")
        self.close_btn.clicked.connect(self.reject)
        btn_row.addWidget(self.close_btn)
        layout.addLayout(btn_row)

    def _copy_machine_id(self):
        QtWidgets.QApplication.clipboard().setText(self._machine_id)

    def _save_request(self):
        directory = QtWidgets.QFileDialog.getExistingDirectory(self, "Choose a folder to save the request")
        if not directory:
            return
        try:
            path, code = build_activation_request(Path(directory))
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Error", f"Could not create activation request.\n{e}")
            return
        self.request_code_field.setText(code)
        QtWidgets.QMessageBox.information(
            self, "Activation Request Saved",
            f"Saved to:\n{path}\n\nSend this file to your GeoGrid vendor, "
            f"or give them the Request Code shown above.",
        )

    def _import_license(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Select GeoGrid.lic", "", "GeoGrid License (*.lic)"
        )
        if not path:
            return
        result = import_license_file(Path(path))
        if result.state.name == "ACTIVATED":
            QtWidgets.QMessageBox.information(self, "Activated", "GeoGrid has been activated successfully.")
            self.accept()
            return
        message = _ERROR_TEXT.get(result.state, result.message)
        QtWidgets.QMessageBox.warning(self, "Activation Failed", message)
        self.status_label.setText(f"Status: {message}")

    @staticmethod
    def ensure_activated(parent=None) -> bool:
        """Show the dialog in a loop until either a valid license is
        active or the user chooses to exit. Returns True iff activated."""
        result = load_active_license()
        if result.state.name == "ACTIVATED":
            return True
        message = _ERROR_TEXT.get(result.state, "") or "Status: Not Activated"
        dlg = ActivationDialog(parent, initial_message=message)
        outcome = dlg.exec()
        return outcome == QtWidgets.QDialog.Accepted
