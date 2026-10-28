#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gui.dialogs.type_review_dialog
================================
Pre-conversion review dialog: lists every distinct attribute combination
(underground MV cable material/section, transformer capacity) that would
resolve to a default/fallback type, and lets the user assign a specific
type from the loaded library to each one before running the real
conversion -- rather than the tool guessing silently. Each row's dropdown
is pre-filled with a suggestion (a saved choice from a previous project,
or a neighbor-based recommendation -- see
Converter.prescan_unresolved()'s "recommended_type") but the user always
confirms or changes it themselves; nothing is applied silently. See
Converter.prescan_unresolved() / set_manual_overrides().
"""

import logging

from shp2dgs.gui.qt_compat import QtCore, QtWidgets, require_gui

logger = logging.getLogger("shp2dgs.gui.type_review_dialog")

require_gui()

_KIND_LABELS = {
    "mv_underground_line": "Underground MV cables",
    "transformer": "Transformers",
}


class TypeReviewDialog(QtWidgets.QDialog):
    """Shows every unresolved attribute-combination group from
    :meth:`Converter.prescan_unresolved` with a dropdown (populated from
    the actual loaded type library) to assign a specific type, or leave
    it on the current default.

    Args:
        prescan_result: ``{"mv_underground_line": [group, ...], "transformer": [group, ...]}``.
        typlne_names: Sorted list of available ``TypLne`` loc_names.
        typtr_names: Sorted list of available ``TypTr2`` loc_names.
        parent: Optional parent widget.
    """

    def __init__(self, prescan_result, typlne_names, typtr_names, known_overrides=None, parent=None):
        super().__init__(parent)
        self.prescan_result = prescan_result
        self._type_choices = {
            "mv_underground_line": typlne_names,
            "mv_overhead_line": typlne_names,
            "mv_other_lines": typlne_names,
            "transformer": typtr_names,
        }
        self._known_overrides = known_overrides or {}
        self._combo_by_row = []  # [(kind, key, combo), ...]
        self.chosen_overrides = {"mv_underground_line": {}, "mv_overhead_line": {},
                                  "mv_other_lines": {}, "transformer": {}}
        self.setWindowTitle("Review Unresolved Types Before Converting")
        self.setMinimumSize(760, 480)
        self._build_ui()

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(20, 20, 20, 20)

        total_groups = sum(len(v) for v in self.prescan_result.values())
        total_records = sum(g["count"] for v in self.prescan_result.values() for g in v)

        if total_groups == 0:
            head = QtWidgets.QLabel("\u2713 No unresolved items -- every record matched a type in the library.")
            head.setStyleSheet("color:#0a7d2a; font-weight:bold;")
            layout.addWidget(head)
        else:
            head = QtWidgets.QLabel(
                f"{total_records} record(s) across {total_groups} distinct attribute combination(s) "
                f"would use a default type. Where possible, a type is already suggested (highlighted "
                f"rows) based on neighboring lines on the same conductor run, or a choice saved from a "
                f"previous project -- review and confirm, or pick something else. Rows left on their "
                f"default keep current behaviour (still recorded in the conversion report either way).")
            head.setWordWrap(True)
            layout.addWidget(head)

        table = QtWidgets.QTableWidget()
        table.setColumnCount(5)
        table.setHorizontalHeaderLabels(["Kind", "Attributes", "Count", "Current default", "Recommended Assign Type"])
        table.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.Stretch)
        table.horizontalHeader().setSectionResizeMode(4, QtWidgets.QHeaderView.Stretch)
        table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        table.setAlternatingRowColors(True)

        rows = []
        for kind, groups in self.prescan_result.items():
            for g in sorted(groups, key=lambda x: -x["count"]):
                rows.append((kind, g))
        table.setRowCount(len(rows))

        for r, (kind, g) in enumerate(rows):
            table.setItem(r, 0, QtWidgets.QTableWidgetItem(_KIND_LABELS.get(kind, kind)))
            examples = ", ".join(x for x in g["example_ids"] if x)
            attr_item = QtWidgets.QTableWidgetItem(g["label"])
            if examples:
                attr_item.setToolTip(f"Example record IDs: {examples}")
            table.setItem(r, 1, attr_item)
            cnt_item = QtWidgets.QTableWidgetItem(f"{g['count']:,}")
            cnt_item.setTextAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
            table.setItem(r, 2, cnt_item)
            table.setItem(r, 3, QtWidgets.QTableWidgetItem(g["current_default"]))

            combo = QtWidgets.QComboBox()
            combo.addItem(f"(keep default: {g['current_default']})", None)
            for name in self._type_choices.get(kind, []):
                combo.addItem(name, name)

            known = self._known_overrides.get(kind, {}).get(g["key"])
            recommended = g.get("recommended_type")
            confidence = g.get("recommended_confidence")
            note = None
            if known:
                pick = known
                note = f"Pre-filled from a previous project's saved choice: {known}"
            elif recommended:
                pick = recommended
                note = (f"Suggested from neighboring lines on the same conductor run "
                         f"({confidence} agreed): {recommended}. Please confirm or change it.")
            else:
                pick = None

            if pick:
                idx = combo.findData(pick)
                if idx >= 0:
                    combo.setCurrentIndex(idx)
            if note:
                existing_tip = table.item(r, 1).toolTip()
                table.item(r, 1).setToolTip((existing_tip + "\n" if existing_tip else "") + note)
                if not known:  # a heuristic suggestion, not a confirmed past choice -- flag it visually
                    combo.setStyleSheet("background-color:#fff6e5;")

            table.setCellWidget(r, 4, combo)
            self._combo_by_row.append((kind, g["key"], combo))

        table.resizeRowsToContents()
        layout.addWidget(table, 1)

        btns = QtWidgets.QDialogButtonBox()
        forget_btn = btns.addButton("Forget All Saved Choices\u2026", QtWidgets.QDialogButtonBox.ResetRole)
        skip_btn = btns.addButton("Skip Review (use defaults)", QtWidgets.QDialogButtonBox.ActionRole)
        apply_btn = btns.addButton("Apply && Continue", QtWidgets.QDialogButtonBox.AcceptRole)
        apply_btn.setDefault(True)
        layout.addWidget(btns)
        forget_btn.clicked.connect(self._on_forget_all)
        skip_btn.clicked.connect(self.reject)
        apply_btn.clicked.connect(self._on_apply)

    def _on_forget_all(self):
        reply = QtWidgets.QMessageBox.question(
            self, "Forget saved choices?",
            "This clears every type choice saved from previous projects (on this computer). "
            "It won't affect the type library itself, just these remembered assignments.\n\n"
            "Continue?")
        if reply == QtWidgets.QMessageBox.Yes:
            from shp2dgs.gui import learned_overrides
            learned_overrides.clear()
            self._known_overrides = {"mv_underground_line": {}, "mv_overhead_line": {},
                                      "mv_other_lines": {}, "transformer": {}}
            parent = self.parent()
            if parent is not None and hasattr(parent, "manual_overrides"):
                parent.manual_overrides = {"mv_underground_line": {}, "mv_overhead_line": {},
                                            "mv_other_lines": {}, "transformer": {}}
            for _kind, _key, combo in self._combo_by_row:
                combo.setCurrentIndex(0)

    def _on_apply(self):
        for kind, key, combo in self._combo_by_row:
            chosen = combo.currentData()
            if chosen:
                # setdefault rather than assuming __init__ pre-populated
                # every possible kind -- a missing key here previously
                # raised KeyError inside this slot, before self.accept()
                # ever ran, which silently left the dialog open with no
                # visible error (exactly reported as "Apply and Continue
                # doesn't close the window").
                self.chosen_overrides.setdefault(kind, {})[key] = chosen
        self.accept()
