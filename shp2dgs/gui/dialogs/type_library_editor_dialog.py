#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gui.dialogs.type_library_editor_dialog
=========================================
Interactive equipment-type-library editor: one spreadsheet-like tab per
type sheet (``TypLne``, ``TypTr2``, ``TypLod``, ``TypFuse``, plus any
custom equipment categories the user adds), with add/delete row,
add-new-category, editable column headers, Excel-style paste, and
import/export in both the native JSON override format and Excel.

System-managed columns (``ID``, ``fold_id``) are hidden from the table --
the editor generates and maintains them automatically -- so the visible
columns are only the technically meaningful ones (name, electrical
parameters, ...).
"""

import copy
import logging
import os

from shp2dgs.core.config_io import save_json
from shp2dgs.core.constants import TYPE_LIBRARY_SHEETS
from shp2dgs.gui.qt_compat import QtCore, QtWidgets, require_gui
from shp2dgs.gui.widgets.paste_table import PasteableTableWidget
from shp2dgs.io_formats.excel_types import (
    ExcelTypeLibraryError,
    export_type_library_to_excel,
    import_type_library_from_excel,
)

logger = logging.getLogger("shp2dgs.gui.type_library_editor_dialog")

require_gui()

#: Header names (bare, before the "(type:length)" suffix) that are
#: system-managed rather than technical/user-meaningful: the editor
#: generates and maintains them automatically, and hides the columns so
#: they don't clutter the table or interfere with pasted data alignment.
SYSTEM_COLUMN_NAMES = {"ID", "fold_id"}


def _bare_name(header_entry):
    """Strip the ``"(type:length)"`` suffix from a header entry, if any."""
    return header_entry.split("(")[0]


class TypeLibraryEditorDialog(QtWidgets.QDialog):
    """Spreadsheet-style editor for the equipment type library.

    Args:
        template: Parsed ``dgs_template.json`` dict (or an
            already-merged variant); a deep copy is taken so the caller's
            copy is never mutated unless :meth:`get_override` result is
            explicitly applied.
        template_path: Optional path to the ``dgs_template.json`` file
            currently loaded by the application. When given, a
            **Save as Default Template** button is offered that writes
            the current (possibly edited) template back to this path, so
            it becomes the new default for future sessions -- without
            this, the button is hidden (there is nowhere sensible to save
            "the default" to).
        parent: Optional parent widget.
    """

    def __init__(self, template, template_path=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Equipment Type Library Editor")
        self.resize(940, 660)
        self.template = copy.deepcopy(template)
        self.template_path = template_path
        self._build_ui()

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)

        toolbar = QtWidgets.QToolBar()
        act_import_excel = toolbar.addAction("Import from Excel\u2026")
        act_export_excel = toolbar.addAction("Export to Excel\u2026")
        toolbar.addSeparator()
        act_import_json = toolbar.addAction("Import from JSON\u2026")
        act_export_json = toolbar.addAction("Export to JSON\u2026")
        layout.addWidget(toolbar)
        act_import_excel.triggered.connect(self.import_from_excel)
        act_export_excel.triggered.connect(self.export_to_excel)
        act_import_json.triggered.connect(self.import_from_json)
        act_export_json.triggered.connect(self.export_to_json)

        hint = QtWidgets.QLabel(
            "Tip: select a cell and paste (Ctrl+V) a block copied from Excel to fill multiple cells at "
            "once. Double-click a column header to rename it. Use the \u201c+\u201d tab to add a whole new "
            "equipment category.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#666; font-size:9pt; padding:2px 4px;")
        layout.addWidget(hint)

        self.tabs = QtWidgets.QTabWidget()
        self.tabs.setTabsClosable(False)
        self.tabs.currentChanged.connect(self._on_tab_changed)
        layout.addWidget(self.tabs)
        self.table_widgets = {}
        for sheet_name in TYPE_LIBRARY_SHEETS:
            if sheet_name not in self.template:
                continue
            self._add_sheet_tab(sheet_name)
        self._add_plus_tab()

        btn_box = QtWidgets.QDialogButtonBox()
        btn_save = btn_box.addButton("Save to File\u2026", QtWidgets.QDialogButtonBox.ActionRole)
        if self.template_path:
            btn_default = btn_box.addButton("Save as Default Template", QtWidgets.QDialogButtonBox.ActionRole)
            btn_default.clicked.connect(self.save_as_default_template)
            btn_use_saved = btn_box.addButton("Use Saved Library", QtWidgets.QDialogButtonBox.ActionRole)
            btn_use_saved.setToolTip(
                "Reload the equipment library from the last saved default template on disk, "
                "discarding any unsaved changes made in this editor session.")
            btn_use_saved.clicked.connect(self.use_saved_library)
        btn_ok = btn_box.addButton(QtWidgets.QDialogButtonBox.Ok)
        btn_cancel = btn_box.addButton(QtWidgets.QDialogButtonBox.Cancel)
        btn_ok.setText("OK && Use")
        btn_cancel.setText("Cancel")
        layout.addWidget(btn_box)
        btn_ok.clicked.connect(self.accept)
        btn_cancel.clicked.connect(self.reject)
        btn_save.clicked.connect(self.export_to_json)

    # ------------------------------------------------------------------
    # Tabs: standard sheets, custom categories, "+" add-category tab
    # ------------------------------------------------------------------
    def _add_plus_tab(self):
        """Add a persistent trailing "+" tab; selecting it prompts for a
        new equipment-category name/columns and inserts a real tab before
        it (the "+" tab itself is never a real data sheet)."""
        plus_widget = QtWidgets.QWidget()
        self.tabs.addTab(plus_widget, "+")
        self._plus_tab_index = self.tabs.indexOf(plus_widget)

    def _on_tab_changed(self, index):
        if index == getattr(self, "_plus_tab_index", -1):
            self._prompt_add_category()

    def _prompt_add_category(self):
        """Prompt for a new equipment-category (sheet) name and starting
        columns, then insert a new tab for it before the "+" tab.

        The new category becomes a first-class entry in ``self.template``
        (an empty ``{"header": [...], "rows": []}``), participates in
        Export/Import (Excel and JSON) and in :meth:`get_override` like
        any built-in sheet.
        """
        name, ok = QtWidgets.QInputDialog.getText(
            self, "Add Equipment Category", "Name for the new equipment type category (e.g. TypCap):")
        if not ok or not name.strip():
            self.tabs.setCurrentIndex(0)
            return
        name = name.strip()
        if name in self.template or name == "+":
            QtWidgets.QMessageBox.warning(self, "Name already used",
                                           f"'{name}' already exists. Choose a different name.")
            self.tabs.setCurrentIndex(0)
            return

        cols_text, ok = QtWidgets.QInputDialog.getText(
            self, "Add Equipment Category",
            "Comma-separated column names for this new category\n"
            "(loc_name is added automatically; ID/fold_id are managed automatically):",
            text="param1,param2")
        if not ok:
            self.tabs.setCurrentIndex(0)
            return
        extra_cols = [c.strip() for c in cols_text.split(",") if c.strip()]

        header = ["ID(a:40)", "loc_name(a:40)", "fold_id(p)"] + extra_cols
        self.template[name] = {"header": header, "rows": []}
        self._add_sheet_tab(name, insert_before_plus=True)
        self.tabs.setCurrentWidget(self._tab_container_for(name))
        logger.info("Added new equipment category '%s' with columns %s", name, extra_cols)

    def _tab_container_for(self, sheet_name):
        tw = self.table_widgets.get(sheet_name)
        return tw.parentWidget() if tw else None

    def _add_sheet_tab(self, sheet_name, insert_before_plus=False):
        tw = PasteableTableWidget()
        hdr = self.template[sheet_name]["header"]
        rows = self.template[sheet_name]["rows"]
        tw.setColumnCount(len(hdr))
        tw.setHorizontalHeaderLabels(hdr)
        for c, h in enumerate(hdr):
            if _bare_name(h) in SYSTEM_COLUMN_NAMES:
                tw.setColumnHidden(c, True)
        tw.setRowCount(len(rows))
        for r, row_data in enumerate(rows):
            for c in range(len(hdr)):
                val = row_data[c] if c < len(row_data) else ""
                item = QtWidgets.QTableWidgetItem("" if val is None else str(val))
                tw.setItem(r, c, item)
        tw.resizeColumnsToContents()
        tw.setAlternatingRowColors(True)
        tw.setSelectionMode(QtWidgets.QAbstractItemView.ContiguousSelection)
        tw.horizontalHeader().setSectionsClickable(True)
        tw.horizontalHeader().sectionDoubleClicked.connect(
            lambda col, t=tw: self._rename_column(t, col))
        self.table_widgets[sheet_name] = tw
        container = QtWidgets.QWidget()
        cl = QtWidgets.QVBoxLayout(container)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.addWidget(tw)
        btn_row = QtWidgets.QHBoxLayout()
        btn_add_type = QtWidgets.QPushButton("Add Type\u2026")
        btn_add_row = QtWidgets.QPushButton("Add Blank Row")
        btn_del = QtWidgets.QPushButton("Delete Row")
        btn_add_col = QtWidgets.QPushButton("Add Column\u2026")
        btn_row.addWidget(btn_add_type)
        btn_row.addWidget(btn_add_row)
        btn_row.addWidget(btn_del)
        btn_row.addWidget(btn_add_col)
        btn_row.addStretch()
        cl.addLayout(btn_row)
        btn_add_type.clicked.connect(lambda _, t=tw, sn=sheet_name: self._add_type(t, sn))
        btn_add_row.clicked.connect(lambda _, t=tw: self._add_blank_row(t))
        btn_del.clicked.connect(lambda _, t=tw: t.removeRow(t.currentRow()) if t.currentRow() >= 0 else None)
        btn_add_col.clicked.connect(lambda _, t=tw: self._add_column(t))

        if insert_before_plus and hasattr(self, "_plus_tab_index"):
            idx = self.tabs.indexOf(self.tabs.widget(self._plus_tab_index))
            self.tabs.insertTab(idx, container, sheet_name)
            self._plus_tab_index = self.tabs.indexOf(self.tabs.widget(self._plus_tab_index))
        else:
            self.tabs.addTab(container, sheet_name)

    def _rename_column(self, table, col_index):
        """Rename a column header on double-click. System-managed columns
        (hidden) can't be reached this way, so they're implicitly
        protected."""
        current_item = table.horizontalHeaderItem(col_index)
        current_name = current_item.text() if current_item else f"Column{col_index + 1}"
        new_name, ok = QtWidgets.QInputDialog.getText(
            self, "Rename Column", "New column name:", text=current_name)
        if not ok or not new_name.strip():
            return
        table.setHorizontalHeaderItem(col_index, QtWidgets.QTableWidgetItem(new_name.strip()))

    def _add_column(self, table):
        """Add a new (visible) column to the end of ``table``."""
        name, ok = QtWidgets.QInputDialog.getText(self, "Add Column", "New column name:")
        if not ok or not name.strip():
            return
        col_idx = table.columnCount()
        table.insertColumn(col_idx)
        table.setHorizontalHeaderItem(col_idx, QtWidgets.QTableWidgetItem(name.strip()))

    # ------------------------------------------------------------------
    # Rows: add type / add blank row / system-column auto-fill
    # ------------------------------------------------------------------
    def _add_blank_row(self, table):
        """Add a fully blank row, with system-managed columns (ID/fold_id)
        still auto-filled so the row is valid even though those columns
        aren't shown."""
        row_idx = table.rowCount()
        table.insertRow(row_idx)
        self._autofill_system_columns(table, row_idx)

    def _add_type(self, table, sheet_name):
        """Add a new type (row) to ``table`` via a guided prompt: asks for
        the new type's name, then inserts a row with the name already
        filled in and system-managed columns (ID/fold_id) auto-filled,
        ready for the remaining fields to be filled in by hand or via
        paste from an external spreadsheet.

        Args:
            table: The ``PasteableTableWidget`` for ``sheet_name``.
            sheet_name: e.g. ``"TypLne"`` -- used only for the dialog title.
        """
        name, ok = QtWidgets.QInputDialog.getText(
            self, f"Add New {sheet_name} Type", "Name of the new type (loc_name):")
        if not ok or not name.strip():
            return
        name = name.strip()

        row_idx = table.rowCount()
        table.insertRow(row_idx)
        loc_name_col = self._find_column(table, "loc_name")
        if loc_name_col is not None:
            table.setItem(row_idx, loc_name_col, QtWidgets.QTableWidgetItem(name))
        self._autofill_system_columns(table, row_idx)

        first_visible = next((c for c in range(table.columnCount())
                               if not table.isColumnHidden(c) and c != loc_name_col), None)
        if first_visible is not None:
            table.setCurrentCell(row_idx, first_visible)
        table.scrollToItem(table.item(row_idx, loc_name_col or 0))
        logger.info("Added new %s type '%s'", sheet_name, name)

    @staticmethod
    def _find_column(table, bare_name):
        """Find the column index whose header's bare name (before the
        type suffix) matches ``bare_name``, or ``None``."""
        for c in range(table.columnCount()):
            item = table.horizontalHeaderItem(c)
            if item and _bare_name(item.text()) == bare_name:
                return c
        return None

    def _autofill_system_columns(self, table, row_idx):
        """Fill the hidden ``ID``/``fold_id`` columns for ``row_idx`` with
        an auto-generated unique id and the ``fold_id`` inherited from an
        existing row, so every row is valid even though the user never
        sees or edits these columns directly.

        Args:
            table: The table to update.
            row_idx: The (already-inserted) row to fill in.
        """
        id_col = self._find_column(table, "ID")
        fold_col = self._find_column(table, "fold_id")

        if id_col is not None:
            existing_id = table.item(row_idx, id_col)
            if not existing_id or not existing_id.text().strip():
                max_id = 0
                for r in range(table.rowCount()):
                    if r == row_idx:
                        continue
                    it = table.item(r, id_col)
                    if it and it.text().strip().isdigit():
                        max_id = max(max_id, int(it.text()))
                new_id = max_id + 1 if max_id else 9_000_000
                table.setItem(row_idx, id_col, QtWidgets.QTableWidgetItem(str(new_id)))

        if fold_col is not None:
            existing_fold = table.item(row_idx, fold_col)
            if not existing_fold or not existing_fold.text().strip():
                fold_id = ""
                for r in range(table.rowCount()):
                    if r == row_idx:
                        continue
                    it = table.item(r, fold_col)
                    if it and it.text().strip():
                        fold_id = it.text().strip()
                        break
                if fold_id:
                    table.setItem(row_idx, fold_col, QtWidgets.QTableWidgetItem(fold_id))

    def _ensure_all_system_columns_filled(self):
        """Safety net: scan every table and auto-fill any row whose
        system-managed ``ID`` column is still blank (e.g. a row added via
        paste that only covered the visible columns). Called before
        collecting/exporting data so the output is always valid regardless
        of how rows were created."""
        for tw in self.table_widgets.values():
            id_col = self._find_column(tw, "ID")
            if id_col is None:
                continue
            for r in range(tw.rowCount()):
                item = tw.item(r, id_col)
                if not item or not item.text().strip():
                    # Skip fully-blank rows (nothing else filled in either) --
                    # those are dropped later in _collect_current(), no
                    # need to manufacture an ID for a row with no data.
                    if any(tw.item(r, c) and tw.item(r, c).text().strip()
                           for c in range(tw.columnCount()) if c != id_col):
                        self._autofill_system_columns(tw, r)

    def _rebuild_tabs(self):
        """Rebuild all sheet tabs from ``self.template`` (used after an
        import replaces the underlying data). Includes any custom
        equipment categories, not just the built-in sheets."""
        self.tabs.clear()
        self.table_widgets = {}
        for sheet_name, data in self.template.items():
            if not isinstance(data, dict) or "header" not in data or "rows" not in data:
                continue  # skip non-sheet template entries, if any
            self._add_sheet_tab(sheet_name)
        self._add_plus_tab()

    def _collect_current(self):
        """Read every table widget back into a
        ``{sheet: {"header":..., "rows":...}}`` dict, converting numeric-
        looking text back to ``int``/``float``.

        The header is read **live from the table** (not from
        ``self.template``), so columns added/renamed via the UI (see
        :class:`~shp2dgs.gui.widgets.paste_table.PasteableTableWidget`,
        :meth:`_rename_column`, :meth:`_add_column`) are preserved on
        export/use. System-managed columns (hidden) are included too --
        :meth:`_ensure_all_system_columns_filled` runs first so they're
        never blank for a non-empty row.

        Returns:
            dict: The override, in native full-sheet-replacement shape.
        """
        self._ensure_all_system_columns_filled()
        out = {}
        for sheet_name, tw in self.table_widgets.items():
            hdr = []
            for c in range(tw.columnCount()):
                hi = tw.horizontalHeaderItem(c)
                hdr.append(hi.text() if hi and hi.text() else f"Extra{c + 1}")
            rows = []
            for r in range(tw.rowCount()):
                row = []
                empty = True
                for c in range(tw.columnCount()):
                    it = tw.item(r, c)
                    txt = it.text() if it else ""
                    if txt.strip() != "":
                        empty = False
                    if txt == "" or txt.lower() in ("null", "none"):
                        row.append(None)
                    else:
                        try:
                            if "." in txt:
                                row.append(float(txt))
                            else:
                                row.append(int(txt))
                        except ValueError:
                            row.append(txt)
                if not empty:
                    rows.append(row)
            out[sheet_name] = {"header": hdr, "rows": rows}
        return out

    # ------------------------------------------------------------------
    # JSON import/export
    # ------------------------------------------------------------------
    def export_to_json(self):
        """Save the current table contents as a ``types_override.json``-
        shaped file."""
        data = self._collect_current()
        fn, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Save type override", "types_override.json",
                                                        "JSON (*.json)")
        if not fn:
            return
        try:
            save_json(fn, data)
            QtWidgets.QMessageBox.information(self, "Saved", fn)
        except OSError as e:
            QtWidgets.QMessageBox.critical(self, "Save failed", f"Could not save the file:\n{e}")

    def save_as_default_template(self):
        """Write the current (possibly edited/extended) template back to
        ``self.template_path`` -- typically the application's
        ``dgs_template.json`` -- so it becomes the default for future
        sessions, without needing a separate override file.

        Asks for confirmation first, since this overwrites the file the
        application loads types from on every startup. A timestamped
        backup of the previous content is written alongside it.
        """
        if not self.template_path:
            return
        resp = QtWidgets.QMessageBox.question(
            self, "Save as Default Template",
            f"This will overwrite the application's default template:\n{self.template_path}\n\n"
            "All future conversions (and new Type Library Editor sessions) will start with the "
            "types shown here. A backup of the current file will be kept alongside it.\n\n"
            "Continue?",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No)
        if resp != QtWidgets.QMessageBox.Yes:
            return

        current = self._collect_current()
        snapshot = copy.deepcopy(self.template)
        for sheet_name, data in current.items():
            snapshot[sheet_name] = data

        try:
            if os.path.exists(self.template_path):
                import datetime
                backup_path = self.template_path + "." + datetime.datetime.now().strftime("%Y%m%d%H%M%S") + ".bak"
                with open(self.template_path, "r", encoding="utf-8") as src, \
                     open(backup_path, "w", encoding="utf-8") as dst:
                    dst.write(src.read())
                logger.info("Backed up previous default template to %s", backup_path)
            save_json(self.template_path, snapshot)
            self.template = snapshot
            QtWidgets.QMessageBox.information(
                self, "Saved", f"Default template updated:\n{self.template_path}")
        except OSError as e:
            logger.error("Failed to save default template: %s", e, exc_info=True)
            QtWidgets.QMessageBox.critical(self, "Save failed", f"Could not save the default template:\n{e}")

    def use_saved_library(self):
        """Reload the equipment library fresh from ``self.template_path``
        (the last saved default template on disk), discarding any
        unsaved changes made in this editor session, and rebuild the tabs.

        Asks for confirmation first since this is destructive to unsaved
        edits.
        """
        if not self.template_path:
            return
        resp = QtWidgets.QMessageBox.question(
            self, "Use Saved Library",
            "This will discard any unsaved changes in this editor and reload the equipment "
            f"library from the last saved default template:\n{self.template_path}\n\nContinue?",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No)
        if resp != QtWidgets.QMessageBox.Yes:
            return
        from shp2dgs.core.config_io import load_json
        try:
            self.template = load_json(self.template_path)
            self._rebuild_tabs()
            QtWidgets.QMessageBox.information(
                self, "Loaded", f"Equipment library reloaded from:\n{self.template_path}")
        except Exception as e:
            logger.error("Failed to reload saved library: %s", e, exc_info=True)
            QtWidgets.QMessageBox.critical(self, "Load failed", f"Could not reload the saved library:\n{e}")

    def import_from_json(self):
        """Load a ``types_override.json``-shaped file, merge it into the
        current template, and refresh the tables."""
        from shp2dgs.core.config_io import load_json, merge_type_library

        fn, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Import type override (JSON)", "", "JSON (*.json)")
        if not fn:
            return
        try:
            override = load_json(fn)
            self.template = merge_type_library(self.template, override)
            self._rebuild_tabs()
            QtWidgets.QMessageBox.information(self, "Imported", f"Loaded overrides from:\n{fn}")
        except Exception as e:
            logger.error("JSON type-library import failed: %s", e, exc_info=True)
            QtWidgets.QMessageBox.critical(self, "Import failed", f"Could not import the JSON file:\n{e}")

    # ------------------------------------------------------------------
    # Excel import/export
    # ------------------------------------------------------------------
    def export_to_excel(self):
        """Export the current table contents to an Excel workbook, one
        sheet per equipment type (also usable as a fill-in template)."""
        current = self._collect_current()
        # Build a temporary template snapshot merged with what's on screen
        # right now so the exported workbook reflects unsaved edits too.
        snapshot = copy.deepcopy(self.template)
        for sheet_name, data in current.items():
            snapshot[sheet_name] = data
        fn, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Export type library to Excel",
                                                        "types_override.xlsx", "Excel workbook (*.xlsx)")
        if not fn:
            return
        try:
            export_type_library_to_excel(snapshot, fn, sheet_names=list(current.keys()))
            QtWidgets.QMessageBox.information(self, "Exported", f"Type library exported to:\n{fn}")
        except ExcelTypeLibraryError as e:
            QtWidgets.QMessageBox.critical(self, "Export failed", str(e))
        except Exception as e:
            logger.error("Excel type-library export failed: %s", e, exc_info=True)
            QtWidgets.QMessageBox.critical(self, "Export failed", f"Could not export the Excel file:\n{e}")

    def import_from_excel(self):
        """Import an Excel workbook (see
        :func:`~shp2dgs.io_formats.excel_types.import_type_library_from_excel`
        for the expected layout and validation rules), merge it into the
        current template, and refresh the tables."""
        from shp2dgs.core.config_io import merge_type_library

        fn, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Import type library (Excel)", "", "Excel workbook (*.xlsx)")
        if not fn:
            return
        try:
            override = import_type_library_from_excel(fn, self.template)
            self.template = merge_type_library(self.template, override)
            self._rebuild_tabs()
            QtWidgets.QMessageBox.information(
                self, "Imported",
                f"Loaded {sum(len(v['rows']) for v in override.values())} rows across "
                f"{len(override)} sheet(s) from:\n{fn}")
        except ExcelTypeLibraryError as e:
            QtWidgets.QMessageBox.critical(self, "Import failed", str(e))
        except Exception as e:
            logger.error("Excel type-library import failed: %s", e, exc_info=True)
            QtWidgets.QMessageBox.critical(self, "Import failed", f"Could not import the Excel file:\n{e}")

    # ------------------------------------------------------------------
    def get_override(self):
        """Return the current table contents as a full-sheet-replacement
        override dict, for use as ``type_library_override`` in
        :class:`~shp2dgs.core.converter.Converter`."""
        return self._collect_current()
