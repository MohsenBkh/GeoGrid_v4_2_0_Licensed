#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gui.widgets.progress_panel
============================
An embeddable (non-modal) progress panel showing percent complete,
elapsed/remaining time and a short scrolling log tail. It is a plain
``QWidget`` meant to be placed directly inside a tab/layout -- the
Conversion tab shows conversion progress in place, rather than opening a
separate window.

Driven by :class:`~shp2dgs.core.progress.ProgressReporter` callbacks via a
Qt signal, so it is safe to update from the conversion callback even
though the conversion itself runs synchronously on the UI thread (the
signal handler pumps the event loop, same pattern as the rest of the app).
"""

import logging
import time
from datetime import timedelta, datetime

from shp2dgs.gui.qt_compat import QtWidgets, QtCore, Signal, Slot, require_gui

logger = logging.getLogger("shp2dgs.gui.progress_panel")

require_gui()

_STEP_LABELS = {
    "gps_setup": "Resolving GPS coordinate system",
    "read_lines": "Reading lines",
    "read_devices": "Reading devices",
    "topology": "Topology",
    "elements": "Generating elements",
    "lines": "Lines",
    "emit": "Generating sheets",
    "gps": "Generating GPS coordinates",
    "graphics": "Graphics",
    "write": "Writing file",
    "done": "Done",
}


class ProgressPanel(QtWidgets.QWidget):
    """Inline conversion-progress panel, embedded directly in a tab.

    Call :meth:`report` (thread-safe via a Qt signal) with the same
    signature as :class:`~shp2dgs.core.progress.ProgressReporter`'s
    callback: ``report(percent, message, step, eta_seconds, elapsed)``.
    Call :meth:`reset` before starting a new conversion run.
    """

    update_signal = Signal(float, str, str, float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.start_time = time.time()
        self._build_ui()
        self.update_signal.connect(self._on_update)

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(8)

        frame = QtWidgets.QFrame()
        frame.setFrameShape(QtWidgets.QFrame.StyledPanel)
        frame.setStyleSheet("QFrame { border:1px solid #d8dde3; border-radius:6px; background:#fafbfc; }")
        flayout = QtWidgets.QVBoxLayout(frame)
        flayout.setContentsMargins(14, 12, 14, 12)
        flayout.setSpacing(8)
        layout.addWidget(frame)

        header_row = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel("Conversion Progress")
        tf = title.font()
        tf.setBold(True)
        title.setFont(tf)
        header_row.addWidget(title)
        header_row.addStretch()
        self.percent_label = QtWidgets.QLabel("0.0 %")
        pf = self.percent_label.font()
        pf.setBold(True)
        self.percent_label.setFont(pf)
        header_row.addWidget(self.percent_label)
        flayout.addLayout(header_row)

        self.stage_label = QtWidgets.QLabel("Idle. Click \u201cRun Conversion\u201d to start.")
        self.stage_label.setWordWrap(True)
        self.stage_label.setStyleSheet("color:#444;")
        flayout.addWidget(self.stage_label)

        self.progress_bar = QtWidgets.QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setMinimumHeight(22)
        self.progress_bar.setStyleSheet("""
            QProgressBar { border:1px solid #bbb; border-radius:5px; text-align:center; }
            QProgressBar::chunk { background:qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #4facfe, stop:1 #00f2fe); border-radius:4px; }
        """)
        flayout.addWidget(self.progress_bar)

        info_row = QtWidgets.QHBoxLayout()
        self.elapsed_label = QtWidgets.QLabel("Elapsed: 00:00")
        self.eta_label = QtWidgets.QLabel("Remaining: \u2014")
        self.step_label = QtWidgets.QLabel("")
        self.step_label.setStyleSheet("color:#666;")
        info_row.addWidget(self.elapsed_label)
        info_row.addWidget(self.eta_label)
        info_row.addStretch()
        info_row.addWidget(self.step_label)
        flayout.addLayout(info_row)

        self.log_box = QtWidgets.QPlainTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setMaximumHeight(110)
        self.log_box.setStyleSheet("font-family: Consolas, monospace; font-size:9pt; background:#f2f2f2;")
        flayout.addWidget(self.log_box)

        self.setVisible(False)

    def reset(self):
        """Reset the panel to its initial state before starting a new
        conversion run, and make it visible."""
        self.start_time = time.time()
        self.progress_bar.setValue(0)
        self.percent_label.setText("0.0 %")
        self.elapsed_label.setText("Elapsed: 00:00")
        self.eta_label.setText("Remaining: \u2014")
        self.step_label.setText("")
        self.stage_label.setText("Starting\u2026")
        self.log_box.clear()
        self.setVisible(True)

    @Slot(float, str, str, float, float)
    def _on_update(self, percent, message, step, eta_seconds, elapsed):
        self.progress_bar.setValue(int(percent))
        self.percent_label.setText(f"{percent:.1f} %")
        self.elapsed_label.setText(f"Elapsed: {timedelta(seconds=int(elapsed))}")
        self.eta_label.setText(
            f"Remaining: {timedelta(seconds=int(eta_seconds))}" if eta_seconds > 1 else "Remaining: \u2014")
        if message:
            self.stage_label.setText(message)
            ts = datetime.now().strftime("%H:%M:%S")
            self.log_box.appendPlainText(f"[{ts}] {message}")
            sb = self.log_box.verticalScrollBar()
            sb.setValue(sb.maximum())
        if step:
            self.step_label.setText(_STEP_LABELS.get(step, step))
        QtWidgets.QApplication.processEvents()

    def report(self, percent, message="", step="", eta_seconds=0, elapsed=0):
        """Thread-safe progress update entry point; matches the signature
        expected by :class:`~shp2dgs.core.progress.ProgressReporter`."""
        self.update_signal.emit(percent, message, step, eta_seconds, elapsed)

    def mark_failed(self, error_text):
        """Mark the panel as failed (red stage text) without clearing the log."""
        self.stage_label.setText(f"\u274c Failed: {error_text}")
        self.stage_label.setStyleSheet("color:#b00020; font-weight:bold;")

    def mark_done(self, message="Conversion completed successfully."):
        """Mark the panel as successfully completed (green stage text)."""
        self.stage_label.setStyleSheet("color:#0a7d2a; font-weight:bold;")
        self.stage_label.setText(f"\u2713 {message}")
