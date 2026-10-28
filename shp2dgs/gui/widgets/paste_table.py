#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gui.widgets.paste_table
=========================
A ``QTableWidget`` subclass that supports Excel-style clipboard paste
(``Ctrl+V``), copy (``Ctrl+C``) and clear (``Delete``) across multiple
cells at once -- the plain ``QTableWidget`` only supports editing one
cell at a time.

Used by the Type Library Editor so a user can copy a block of type
parameters from an external spreadsheet and paste it directly into the
table in one action, instead of retyping every field by hand. If the
pasted block is larger than the current table (more rows, or more
columns than the sheet currently has), rows/columns are added
automatically to fit -- e.g. pasting extra reference columns (a
datasheet link, a manufacturer note, ...) alongside the standard DGS
fields simply grows the table.
"""

import logging

from shp2dgs.gui.qt_compat import QtCore, QtGui, QtWidgets, require_gui

logger = logging.getLogger("shp2dgs.gui.paste_table")

require_gui()


class PasteableTableWidget(QtWidgets.QTableWidget):
    """A ``QTableWidget`` with Excel-compatible multi-cell paste/copy.

    Clipboard data is read/written as tab-separated values with newline
    row separators -- the format every spreadsheet application
    (Excel, LibreOffice, Google Sheets) uses for cell-range copy/paste,
    so round-tripping through an external spreadsheet just works.
    """

    def keyPressEvent(self, event):
        if event.matches(QtGui.QKeySequence.Paste):
            self._paste_from_clipboard()
            return
        if event.matches(QtGui.QKeySequence.Copy):
            self._copy_to_clipboard()
            return
        if event.key() in (QtCore.Qt.Key_Delete, QtCore.Qt.Key_Backspace):
            self._clear_selected()
            return
        super().keyPressEvent(event)

    def _visible_columns(self):
        """Return the list of column indices that are not hidden."""
        return [c for c in range(self.columnCount()) if not self.isColumnHidden(c)]

    def _paste_from_clipboard(self):
        """Paste tab/newline-separated clipboard text starting at the
        current cell, growing the table with new rows/columns as needed.

        Hidden columns (see ``setColumnHidden`` -- used by the Type
        Library Editor to keep system-managed fields like ``ID``/
        ``fold_id`` out of the way) are skipped entirely, exactly like
        pasting into a spreadsheet with hidden columns: the pasted values
        fill only the *visible* cells in sequence, never overwriting a
        hidden one.

        New columns beyond the table's current width are labelled
        ``Extra1``, ``Extra2``, ... in the header so they are clearly
        distinguishable from the standard DGS fields, and are preserved
        (as extra, unvalidated fields) through export/import.
        """
        text = QtWidgets.QApplication.clipboard().text()
        if not text:
            return
        text = text.rstrip("\n").rstrip("\r")
        if not text:
            return
        rows = [row.split("\t") for row in text.replace("\r\n", "\n").split("\n")]

        start_row = max(0, self.currentRow())
        current_col = max(0, self.currentColumn())
        visible_cols = self._visible_columns()
        if current_col in visible_cols:
            start_visible_idx = visible_cols.index(current_col)
        else:
            # Landed on a hidden column somehow (e.g. programmatic focus)
            # -- start at the next visible column at/after it.
            later = [i for i, c in enumerate(visible_cols) if c >= current_col]
            start_visible_idx = later[0] if later else len(visible_cols)

        needed_rows = start_row + len(rows)
        max_pasted_cols = max((len(r) for r in rows), default=0)

        added_rows = 0
        while self.rowCount() < needed_rows:
            self.insertRow(self.rowCount())
            added_rows += 1

        added_cols = 0
        while len(visible_cols) - start_visible_idx < max_pasted_cols:
            col_idx = self.columnCount()
            self.insertColumn(col_idx)
            self.setHorizontalHeaderItem(col_idx, QtWidgets.QTableWidgetItem(f"Extra{col_idx + 1}"))
            visible_cols.append(col_idx)  # newly added columns are visible by default
            added_cols += 1

        for i, row_vals in enumerate(rows):
            for j, val in enumerate(row_vals):
                r = start_row + i
                c = visible_cols[start_visible_idx + j]
                item = self.item(r, c)
                if item is None:
                    item = QtWidgets.QTableWidgetItem()
                    self.setItem(r, c, item)
                item.setText(val)

        if added_rows or added_cols:
            logger.info("Paste grew table by %d row(s), %d column(s)", added_rows, added_cols)

    def _copy_to_clipboard(self):
        """Copy the current selection to the clipboard as tab/newline
        separated text (compatible with pasting into Excel). Hidden
        columns are skipped, symmetric with :meth:`_paste_from_clipboard`."""
        ranges = self.selectedRanges()
        if not ranges:
            return
        sel = ranges[0]
        cols = [c for c in range(sel.leftColumn(), sel.rightColumn() + 1) if not self.isColumnHidden(c)]
        lines = []
        for r in range(sel.topRow(), sel.bottomRow() + 1):
            vals = []
            for c in cols:
                item = self.item(r, c)
                vals.append(item.text() if item else "")
            lines.append("\t".join(vals))
        QtWidgets.QApplication.clipboard().setText("\n".join(lines))

    def _clear_selected(self):
        """Clear the text of every selected cell (Delete/Backspace)."""
        for item in self.selectedItems():
            item.setText("")
