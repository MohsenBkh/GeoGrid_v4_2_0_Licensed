#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gui.main_window
================
The application's main window: menu bar (File/Tools/Settings/Help),
status bar, and a tabbed main panel (Conversion / Type Library /
Diagram Preview). Every dialog in the application is reachable both from
the menu bar and from the relevant tab's buttons.
"""

import copy
import logging
import os
import sys
import traceback

from shp2dgs.core.config_io import (load_default_types_override, load_json,
                                    merge_type_library)
from shp2dgs.core.constants import APP_NAME, APP_TAGLINE, APP_VERSION
from shp2dgs.core.converter import Converter
from shp2dgs.core.diagram import build_diagram_model
from shp2dgs.gui.dialogs.project_settings_dialog import ProjectSettingsDialog
from shp2dgs.gui.dialogs.summary_dialog import SummaryDialog
from shp2dgs.gui.dialogs.type_library_editor_dialog import TypeLibraryEditorDialog
from shp2dgs.gui.dialogs.type_review_dialog import TypeReviewDialog
from shp2dgs.gui import learned_overrides
from shp2dgs.gui.qt_compat import QtCore, QtGui, QtWidgets, require_gui
from shp2dgs.gui.resources import load_app_icon
from shp2dgs.gui.widgets.diagram_view import DiagramView
from shp2dgs.gui.widgets.progress_panel import ProgressPanel
from shp2dgs.utils.logging_setup import get_log_file_path

logger = logging.getLogger("shp2dgs.gui.main_window")

require_gui()


def qt_message(parent, icon, title, text, detail=""):
    """Show a modal ``QMessageBox`` and return the pressed button id."""
    msg = QtWidgets.QMessageBox(parent)
    msg.setIcon(icon)
    msg.setWindowTitle(title)
    msg.setText(text)
    if detail:
        msg.setDetailedText(detail)
    msg.setStandardButtons(QtWidgets.QMessageBox.Ok)
    return msg.exec()


class MainWindow(QtWidgets.QMainWindow):
    """The SHP \u2192 DGS Converter main window.

    Args:
        config_path: Initial path to ``config.json``.
        template_path: Initial path to ``dgs_template.json``.
    """

    def __init__(self, config_path, template_path):
        super().__init__()
        self.config_path = config_path
        self.template_path = template_path
        self.cfg = None
        self.tpl = None
        self.project_meta = None
        self.terminal_types = None
        self.types_override = None
        self.gps_settings = None
        self.layer_mapping = None
        self.last_dialog_settings = None
        self.last_output_path = None
        self.manual_overrides = learned_overrides.to_manual_overrides(learned_overrides.load())

        self.setWindowTitle(f"{APP_NAME} v{APP_VERSION}")
        self.setWindowIcon(load_app_icon())
        self.resize(880, 640)

        self._reload_config()
        self._build_menu_bar()
        self._build_central_widget()
        self._build_status_bar()

    # ------------------------------------------------------------------
    # configuration loading
    # ------------------------------------------------------------------
    def _reload_config(self):
        """(Re)load ``config.json``/``dgs_template.json`` from the
        currently selected paths, showing a message box on failure."""
        try:
            self.cfg = load_json(self.config_path)
            self.tpl = load_json(self.template_path)
            logger.info("Configuration loaded: %s / %s", self.config_path, self.template_path)
        except Exception as e:
            logger.error("Failed to load configuration: %s", e, exc_info=True)
            qt_message(self, QtWidgets.QMessageBox.Critical, "Configuration error",
                       f"Could not load configuration files:\n{e}", traceback.format_exc())
            return
        self.load_default_type_library(silent=True)

    def load_default_type_library(self, silent=False):
        """(Re)load the bundled equipment type library as the default.

        The application ships ``types_override.xlsx`` (the maintained
        ``TypLne``/``TypTr2``/``TypLod``/``TypFuse`` library) and uses it
        for every conversion unless the user loads a different override
        from the Type Library Editor. Reloading is needed after switching
        ``dgs_template.json``, because the workbook is validated against
        the template's columns.

        Args:
            silent: If True, a failure is only logged (used during startup,
                where a modal dialog before the window is shown would be
                jarring); otherwise the user sees a message box.
        """
        self.types_override = None
        if self.tpl is None:
            return
        try:
            self.types_override = load_default_types_override(self.tpl)
            if self.types_override:
                counts = ", ".join(f"{k}: {len(v.get('rows', []))}"
                                   for k, v in self.types_override.items()
                                   if isinstance(v, dict) and "rows" in v)
                logger.info("Default type library loaded (%s)", counts)
            else:
                logger.info("No bundled default type library found; using dgs_template.json only")
        except Exception as e:
            logger.error("Failed to load the default type library: %s", e, exc_info=True)
            if not silent:
                qt_message(self, QtWidgets.QMessageBox.Warning, "Type library error",
                           f"Could not load the bundled type library (types_override.xlsx):\n{e}",
                           traceback.format_exc())

    # ------------------------------------------------------------------
    # menu bar
    # ------------------------------------------------------------------
    def _build_menu_bar(self):
        mb = self.menuBar()

        file_menu = mb.addMenu("&File")
        act_open_shp = file_menu.addAction("Open Shapefile Folder\u2026")
        act_save_out = file_menu.addAction("Set Output File\u2026")
        file_menu.addSeparator()
        act_exit = file_menu.addAction("Exit")
        act_open_shp.triggered.connect(self.browse_shp_dir)
        act_save_out.triggered.connect(self.browse_out_path)
        act_exit.triggered.connect(self.close)

        tools_menu = mb.addMenu("&Tools")
        act_project_settings = tools_menu.addAction("Project Settings\u2026")
        act_type_editor = tools_menu.addAction("Type Library Editor\u2026")
        act_run = tools_menu.addAction("Run Conversion")
        act_project_settings.triggered.connect(self.open_project_settings)
        act_type_editor.triggered.connect(self.open_type_library_editor)
        act_run.triggered.connect(self.run_conversion)

        settings_menu = mb.addMenu("&Settings")
        act_choose_config = settings_menu.addAction("Choose config.json\u2026")
        act_choose_template = settings_menu.addAction("Choose dgs_template.json\u2026")
        act_reload = settings_menu.addAction("Reload Configuration")
        act_reload_types = settings_menu.addAction("Reload Default Type Library (types_override.xlsx)")
        act_choose_config.triggered.connect(self.browse_config_path)
        act_choose_template.triggered.connect(self.browse_template_path)
        act_reload.triggered.connect(self._reload_config)
        act_reload_types.triggered.connect(self._reload_default_types_action)

        help_menu = mb.addMenu("&Help")
        act_about = help_menu.addAction("About")
        act_view_log = help_menu.addAction("Open Log File")
        act_about.triggered.connect(self.show_about)
        act_view_log.triggered.connect(self.open_log_file)

    # ------------------------------------------------------------------
    # central widget: tabs
    # ------------------------------------------------------------------
    def _build_central_widget(self):
        self.tabs = QtWidgets.QTabWidget()
        self.setCentralWidget(self.tabs)
        self._build_conversion_tab()
        self._build_type_library_tab()
        self._build_diagram_tab()

    def _build_conversion_tab(self):
        w = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(w)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        form = QtWidgets.QFormLayout()
        self.ed_shp_dir = QtWidgets.QLineEdit()
        self.ed_shp_dir.setPlaceholderText("Folder containing ELW*.shp layers")
        btn_shp = QtWidgets.QPushButton("Browse\u2026")
        btn_shp.clicked.connect(self.browse_shp_dir)
        row1 = QtWidgets.QHBoxLayout()
        row1.addWidget(self.ed_shp_dir, 1)
        row1.addWidget(btn_shp)
        form.addRow("Input shapefile folder:", row1)

        self.ed_out_path = QtWidgets.QLineEdit()
        self.ed_out_path.setPlaceholderText("Destination .xlsx path")
        btn_out = QtWidgets.QPushButton("Browse\u2026")
        btn_out.clicked.connect(self.browse_out_path)
        row2 = QtWidgets.QHBoxLayout()
        row2.addWidget(self.ed_out_path, 1)
        row2.addWidget(btn_out)
        form.addRow("Output DGS file:", row2)
        layout.addLayout(form)

        self.chk_graphics = QtWidgets.QCheckBox("Generate geographic diagram graphics (IntGrf/IntGrfcon)")
        self.chk_graphics.setChecked(True)
        layout.addWidget(self.chk_graphics)

        btn_row = QtWidgets.QHBoxLayout()
        self.btn_settings = QtWidgets.QPushButton("Project Settings\u2026")
        self.btn_settings.clicked.connect(self.open_project_settings)
        self.btn_precheck = QtWidgets.QPushButton("Pre-check Types\u2026")
        self.btn_precheck.setToolTip(
            "Scan the shapefiles for attribute combinations (cable material/section, transformer "
            "capacity) that would use a default type, and choose the correct type yourself before converting.")
        self.btn_precheck.clicked.connect(self.precheck_types)
        self.btn_run = QtWidgets.QPushButton("Run Conversion")
        self.btn_run.setStyleSheet("font-weight:bold; padding:6px 18px;")
        self.btn_run.clicked.connect(self.run_conversion)
        btn_row.addWidget(self.btn_settings)
        btn_row.addWidget(self.btn_precheck)
        btn_row.addStretch()
        btn_row.addWidget(self.btn_run)
        layout.addLayout(btn_row)

        self.settings_summary_label = QtWidgets.QLabel(
            "Project settings: using defaults (open Project Settings to customise).\n"
            + self._type_library_summary())
        self.settings_summary_label.setWordWrap(True)
        self.settings_summary_label.setStyleSheet("color:#666; font-size:9pt;")
        layout.addWidget(self.settings_summary_label)

        # Conversion progress is shown inline, right here, instead of in a
        # separate popup window -- hidden until a conversion starts.
        self.progress_panel = ProgressPanel()
        layout.addWidget(self.progress_panel)

        layout.addStretch()
        self.tabs.addTab(w, "Conversion")

    def _build_type_library_tab(self):
        w = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(w)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)
        info = QtWidgets.QLabel(
            "Edit equipment types (lines, transformers, loads, fuses) used by the conversion. "
            "Import/export as JSON or Excel."
        )
        info.setWordWrap(True)
        layout.addWidget(info)
        btn = QtWidgets.QPushButton("Open Type Library Editor\u2026")
        btn.clicked.connect(self.open_type_library_editor)
        layout.addWidget(btn, 0, QtCore.Qt.AlignLeft)
        self.type_library_status_label = QtWidgets.QLabel(
            "No custom type-library override loaded (using template defaults).")
        self.type_library_status_label.setStyleSheet("color:#666; font-size:9pt;")
        self.type_library_status_label.setWordWrap(True)
        layout.addWidget(self.type_library_status_label)
        layout.addStretch()
        self.tabs.addTab(w, "Type Library")

    def _build_diagram_tab(self):
        self.diagram_view = DiagramView()
        self.tabs.addTab(self.diagram_view, "Diagram Preview")

    def _build_status_bar(self):
        self.status = self.statusBar()
        self.status.showMessage("Ready.")

    # ------------------------------------------------------------------
    # File menu / browse actions
    # ------------------------------------------------------------------
    def browse_shp_dir(self):
        d = QtWidgets.QFileDialog.getExistingDirectory(self, "Input shapefile folder (ELW*.shp)")
        if d:
            self.ed_shp_dir.setText(d)
            if not self.ed_out_path.text().strip():
                self.ed_out_path.setText(os.path.join(d, "Network_DGS.xlsx"))
            self.status.showMessage(f"Input folder set: {d}", 5000)

    def browse_out_path(self):
        default = self.ed_out_path.text().strip() or "Network_DGS.xlsx"
        fn, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Save DGS output file", default,
                                                        "Excel Workbook (*.xlsx);;All Files (*.*)")
        if fn:
            if not fn.lower().endswith(".xlsx"):
                fn += ".xlsx"
            self.ed_out_path.setText(fn)
            self.status.showMessage(f"Output file set: {fn}", 5000)

    def browse_config_path(self):
        fn, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Select config.json", self.config_path, "JSON (*.json)")
        if fn:
            self.config_path = fn
            self._reload_config()

    def browse_template_path(self):
        fn, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Select dgs_template.json", self.template_path, "JSON (*.json)")
        if fn:
            self.template_path = fn
            self._reload_config()

    # ------------------------------------------------------------------
    # Tools menu actions
    # ------------------------------------------------------------------
    def open_project_settings(self):
        if self.tpl is None or self.cfg is None:
            qt_message(self, QtWidgets.QMessageBox.Warning, "Configuration not loaded",
                       "Load a valid config.json / dgs_template.json first (Settings menu).")
            return
        shp_dir = self.ed_shp_dir.text().strip()
        dlg = ProjectSettingsDialog(self.tpl, self.cfg, shp_dir=shp_dir,
                                     previous=self.last_dialog_settings,
                                     types_override=self.types_override, parent=self)
        if dlg.exec() == QtWidgets.QDialog.Accepted:
            self.project_meta = dlg.get_project_meta()
            self.terminal_types = dlg.get_terminal_types()
            self.gps_settings = dlg.get_gps_settings()
            self.layer_mapping = dlg.get_layer_mapping()
            # The type library itself may have been edited via the dialog's
            # delegated "Open Type Library Editor..." button, which updates
            # self.types_override directly through open_type_library_editor()
            # (the single canonical entry point) -- nothing further to load
            # or reconcile here.
            self.last_dialog_settings = dlg.get_all_settings()
            gps = self.gps_settings or {}
            gps_note = " | GPS: ON" if gps.get("generate_gps") else ""
            lm = self.layer_mapping or {}
            n_overrides = len(lm.get("overrides", {}))
            layer_note = f" | {n_overrides} layer mapping override(s)" if n_overrides else ""
            self.settings_summary_label.setText(
                f"Project: {self.project_meta.get('project_name')}  |  "
                f"Deck: {self.project_meta.get('deck_name')}  |  "
                f"MV: {self.project_meta.get('nominal_mv_kv')} kV{gps_note}{layer_note}\n"
                + self._type_library_summary()
            )
            self.status.showMessage("Project settings saved.", 5000)

    def _type_library_summary(self):
        """One-line description of the equipment type library in use."""
        if not self.types_override:
            return "Type library: dgs_template.json only (no default override loaded)."
        parts = [f"{k} {len(v.get('rows', []))}" for k, v in self.types_override.items()
                 if isinstance(v, dict) and "rows" in v]
        return ("Type library: types_override.xlsx ("
                + ", ".join(parts) + " types) -- Tools \u2192 Type Library Editor to inspect.")

    def _reload_default_types_action(self):
        """Settings-menu action: discard any edited override and go back to
        the type library shipped with the application."""
        self.load_default_type_library()
        if self.types_override:
            self.status.showMessage("Default type library (types_override.xlsx) reloaded.", 5000)
        else:
            self.status.showMessage("No bundled default type library found.", 5000)

    def open_type_library_editor(self):
        if self.tpl is None:
            qt_message(self, QtWidgets.QMessageBox.Warning, "Configuration not loaded",
                       "Load a valid dgs_template.json first (Settings menu).")
            return
        seed = self.tpl
        if self.types_override:
            from shp2dgs.core.config_io import merge_type_library
            seed = merge_type_library(self.tpl, self.types_override)
        dlg = TypeLibraryEditorDialog(seed, template_path=self.template_path, parent=self)
        if dlg.exec() == QtWidgets.QDialog.Accepted:
            self.types_override = dlg.get_override()
            self.type_library_status_label.setText("Type-library override set from the interactive editor.")
            self.status.showMessage("Type library updated.", 5000)

    # ------------------------------------------------------------------
    # Help menu actions
    # ------------------------------------------------------------------
    def show_about(self):
        QtWidgets.QMessageBox.about(
            self, f"About {APP_NAME}",
            f"<b>{APP_NAME}</b> v{APP_VERSION}<br>{APP_TAGLINE}<br><br>"
            "Converts Iranian 20\u202fkV distribution-network GIS shapefile exports "
            "into a DIgSILENT PowerFactory DGS deck, including an optional local "
            "diagram and optional GPS coordinates for PowerFactory's Geographic "
            "Diagram.<br><br>"
            "See README.md for full documentation."
        )

    def open_log_file(self):
        log_path = get_log_file_path()
        if not log_path or not os.path.exists(log_path):
            qt_message(self, QtWidgets.QMessageBox.Information, "Log file",
                       "No log file is available yet.")
            return
        try:
            if sys.platform.startswith("win"):
                os.startfile(log_path)  # noqa: S606
            elif sys.platform == "darwin":
                os.system(f'open "{log_path}"')
            else:
                os.system(f'xdg-open "{log_path}"')
        except Exception as e:
            qt_message(self, QtWidgets.QMessageBox.Warning, "Could not open log file", str(e))

    # ------------------------------------------------------------------
    # Conversion
    # ------------------------------------------------------------------
    def _validate_inputs(self):
        shp_dir = self.ed_shp_dir.text().strip()
        out_path = self.ed_out_path.text().strip()
        if not shp_dir:
            qt_message(self, QtWidgets.QMessageBox.Warning, "Missing input", "Please select the input shapefile folder.")
            return None, None
        if not os.path.isdir(shp_dir):
            qt_message(self, QtWidgets.QMessageBox.Critical, "Invalid input folder",
                       f"The selected folder does not exist:\n{shp_dir}")
            return None, None
        if not out_path:
            qt_message(self, QtWidgets.QMessageBox.Warning, "Missing output", "Please choose an output .xlsx path.")
            return None, None
        if not out_path.lower().endswith(".xlsx"):
            out_path += ".xlsx"
            self.ed_out_path.setText(out_path)
        out_dir = os.path.dirname(os.path.abspath(out_path)) or "."
        if not os.path.isdir(out_dir):
            try:
                os.makedirs(out_dir, exist_ok=True)
            except OSError as e:
                qt_message(self, QtWidgets.QMessageBox.Critical, "Invalid output folder",
                           f"Could not create the output folder:\n{out_dir}\n{e}")
                return None, None
        return shp_dir, out_path

    def _new_converter(self, out_path=None, progress_callback=None):
        """Build a :class:`Converter` from the current GUI settings.

        Args:
            out_path: Unused by the converter itself but accepted for
                call-site symmetry with :meth:`run_conversion`.
            progress_callback: Optional Qt-safe progress callback.
        """
        gps = self.gps_settings or {}
        lm = self.layer_mapping or {}
        return Converter(
            self.ed_shp_dir.text().strip(), self.cfg, self.tpl,
            make_graphics=self.chk_graphics.isChecked(),
            progress_callback=progress_callback,
            project_meta=self.project_meta,
            terminal_types=self.terminal_types,
            type_library_override=self.types_override,
            generate_gps=gps.get("generate_gps", False),
            gps_line_vertices=gps.get("gps_line_vertices", False),
            source_epsg=gps.get("source_epsg"),
            layer_overrides=lm.get("overrides"),
            include_mv=lm.get("include_mv", True),
            include_lv=lm.get("include_lv", False),
        )

    def precheck_types(self):
        """Scan the input shapefiles for attribute combinations that would
        resolve to a default/fallback type and let the user assign a
        specific type to each before running the real conversion. Choices
        are stored in ``self.manual_overrides`` and applied automatically
        the next time :meth:`run_conversion` runs."""
        if self.cfg is None or self.tpl is None:
            qt_message(self, QtWidgets.QMessageBox.Critical, "Configuration not loaded",
                       "Fix the configuration (Settings menu) before pre-checking types.")
            return
        shp_dir = self.ed_shp_dir.text().strip()
        if not shp_dir or not os.path.isdir(shp_dir):
            qt_message(self, QtWidgets.QMessageBox.Warning, "Missing input",
                       "Please select a valid input shapefile folder first.")
            return
        self.status.showMessage("Scanning for unresolved types\u2026")
        QtWidgets.QApplication.processEvents()
        try:
            conv = self._new_converter()
            result = conv.prescan_unresolved()
        except Exception as e:
            logger.error("Pre-check failed", exc_info=True)
            qt_message(self, QtWidgets.QMessageBox.Critical, "Pre-check error",
                       f"Could not scan the shapefiles:\n{e}", traceback.format_exc())
            self.status.showMessage("Pre-check failed. See log for details.", 8000)
            return
        finally:
            self.status.clearMessage()

        typlne_names = sorted(conv.typlne_id.keys())
        typtr_names = sorted(conv.typtr_id.keys())
        dlg = TypeReviewDialog(result, typlne_names, typtr_names, known_overrides=self.manual_overrides, parent=self)
        if dlg.exec() == QtWidgets.QDialog.Accepted:
            for kind, mapping in dlg.chosen_overrides.items():
                self.manual_overrides.setdefault(kind, {}).update(mapping)
            learned_overrides.save(dlg.chosen_overrides)
            chosen_count = sum(len(v) for v in dlg.chosen_overrides.values())
            self.status.showMessage(
                f"Pre-check: {chosen_count} type(s) assigned and saved -- these will be reused "
                f"automatically in this and future projects.", 8000)

    def run_conversion(self):
        """Validate inputs, run the conversion synchronously (the inline
        progress panel pumps the Qt event loop via
        :meth:`QApplication.processEvents` on each update), then show the
        summary dialog and switch to the Diagram Preview tab."""
        if self.cfg is None or self.tpl is None:
            qt_message(self, QtWidgets.QMessageBox.Critical, "Configuration not loaded",
                       "Fix the configuration (Settings menu) before running a conversion.")
            return
        shp_dir, out_path = self._validate_inputs()
        if not shp_dir:
            return
        if self.project_meta is None:
            # No explicit settings yet -- prompt once, like the very first run.
            self.open_project_settings()
            if self.project_meta is None:
                self.status.showMessage("Conversion cancelled (no project settings).", 5000)
                return

        self.progress_panel.reset()
        QtWidgets.QApplication.processEvents()

        def qt_progress(percent, message, step, eta_seconds, elapsed):
            self.progress_panel.report(percent, message, step, eta_seconds, elapsed)

        self.btn_run.setEnabled(False)
        try:
            conv = self._new_converter(out_path, progress_callback=qt_progress)
            if any(self.manual_overrides.values()):
                conv.set_manual_overrides(self.manual_overrides)
            conv.build()
            conv.emit()
            conv.write(out_path)
            self.progress_panel.mark_done(f"Conversion completed: {os.path.basename(out_path)}")
            self.last_output_path = out_path

            summary = conv.summary()
            total_elements = sum(summary.get(k, 0) for k in ("ElmLne", "ElmTr2", "Elmlod", "ElmCoup"))
            if total_elements == 0:
                qt_message(
                    self, QtWidgets.QMessageBox.Warning, "Nothing was converted",
                    "The conversion finished but produced 0 lines/transformers/loads/switches -- "
                    "there's nothing to show in the diagram either, since it has no data.\n\n"
                    "This almost always means the shapefile layer names in your input folder don't "
                    "match what the tool is looking for. Open Project Settings \u2192 Shapefile Layers "
                    "to check/fix the mapping against your actual .shp file names.")

            self.status.showMessage(f"Conversion completed: {out_path}", 10000)
            summ = SummaryDialog(summary, out_path, self.project_meta, self)
            summ.exec()

            model = build_diagram_model(conv.headers, conv.sheets,
                                         elem_kind_hint=conv.elem_kind, term_kind_hint=conv.term_kind,
                                         line_points_hint=conv.line_points_diagram_xy)
            # Switch to the Diagram Preview tab BEFORE loading the model:
            # QGraphicsView.fitInView() (called inside set_model()) needs
            # the widget to already be visible/laid out to compute a
            # correct zoom transform. Calling it while the tab is still
            # hidden behind another one (as it would be if this ran before
            # the modal summary above) can silently produce a degenerate
            # transform, leaving the view blank even though the scene has
            # data (this was the "Diagram Preview shows nothing" bug).
            self.tabs.setCurrentWidget(self.diagram_view)
            QtWidgets.QApplication.processEvents()
            self.diagram_view.set_model(model)
            # Safety net: re-fit shortly after, in case the very first
            # layout pass still wasn't complete when set_model() ran.
            QtCore.QTimer.singleShot(50, self.diagram_view.fit_to_window)
        except Exception as e:
            logger.error("Conversion failed", exc_info=True)
            self.progress_panel.mark_failed(str(e))
            qt_message(self, QtWidgets.QMessageBox.Critical, "Conversion error",
                       f"Conversion stopped due to an error:\n{e}", traceback.format_exc())
            self.status.showMessage("Conversion failed. See log for details.", 8000)
        finally:
            self.btn_run.setEnabled(True)
