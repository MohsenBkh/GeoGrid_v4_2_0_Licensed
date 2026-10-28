#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gui.learned_overrides
======================
Persists the person's Pre-check Types ("assign type") choices to a
per-user file, keyed by the same attribute signature used within a
single conversion (see ``Converter.prescan_unresolved`` /
``set_manual_overrides``) -- so a decision made once (e.g. "section=0,
material=unspecified -> use this cable type") doesn't have to be
re-entered on every new project that hits the same ambiguous data.

Storage location: Qt's per-user AppDataLocation (falls back to the
current directory if Qt can't resolve one, e.g. in a stripped-down
environment), so it survives reinstalls/updates and doesn't require the
app's own install directory to be writable.
"""

import json
import logging
import os

from shp2dgs.gui.qt_compat import QtCore

logger = logging.getLogger("shp2dgs.gui.learned_overrides")

_FILENAME = "learned_type_overrides.json"


def _store_path():
    try:
        d = QtCore.QStandardPaths.writableLocation(QtCore.QStandardPaths.AppDataLocation)
    except Exception:
        d = ""
    if not d:
        d = os.path.join(os.path.expanduser("~"), ".geogrid")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, _FILENAME)


#: Every kind prescan_unresolved()/set_manual_overrides() can produce.
#: Centralized here (and in type_review_dialog.py) because a hardcoded
#: partial list has silently dropped choices for newer kinds more than
#: once when a new kind was added without updating every initializer.
_TUPLE_KEY_KINDS = ("mv_underground_line", "mv_overhead_line")  # keyed by (section, material)
_STRING_KEY_KINDS = ("mv_other_lines", "transformer")  # keyed by "__all__" or capacity


def _empty_store():
    return {k: {} for k in _TUPLE_KEY_KINDS + _STRING_KEY_KINDS}


def load():
    """Return previously-saved overrides.

    Returns:
        dict: ``{kind: {"section|material": type_name, ...}}`` for
        tuple-keyed kinds, ``{kind: {key: type_name, ...}}`` for
        string-keyed kinds -- tuple keys are stored as ``"a|b"`` strings
        since JSON object keys must be strings; see
        :func:`to_manual_overrides` to convert back for
        ``Converter.set_manual_overrides``. Empty per-kind dicts if
        nothing was saved yet or the file is unreadable.
    """
    path = _store_path()
    if not os.path.exists(path):
        return _empty_store()
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        for k in _TUPLE_KEY_KINDS + _STRING_KEY_KINDS:
            data.setdefault(k, {})
        return data
    except Exception as e:
        logger.warning("Could not read learned overrides at %s: %s", path, e)
        return _empty_store()


def save(overrides):
    """Merge ``overrides`` (same shape as :meth:`Converter.set_manual_overrides`
    expects, i.e. tuple keys for the tuple-keyed kinds) into the
    persisted store and write it out.

    Args:
        overrides: ``{kind: {(section, material): type_name, ...}}`` for
            tuple-keyed kinds, ``{kind: {key: type_name, ...}}`` for
            string-keyed kinds.
    """
    path = _store_path()
    existing = load()
    for kind in _TUPLE_KEY_KINDS:
        for key, chosen in overrides.get(kind, {}).items():
            sec, mat = key
            existing[kind][f"{sec}|{mat}"] = chosen
    for kind in _STRING_KEY_KINDS:
        for key, chosen in overrides.get(kind, {}).items():
            existing[kind][str(key)] = chosen
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(existing, f, ensure_ascii=False, indent=2)
        logger.info("Saved learned type overrides to %s", path)
    except Exception as e:
        logger.warning("Could not save learned overrides to %s: %s", path, e)


def to_manual_overrides(stored):
    """Convert :func:`load`'s string-keyed JSON shape back into the
    shape ``Converter.set_manual_overrides`` expects (tuple keys for the
    tuple-keyed kinds)."""
    out = _empty_store()
    for kind in _TUPLE_KEY_KINDS:
        for combined, chosen in stored.get(kind, {}).items():
            sec, _, mat = combined.partition("|")
            out[kind][(sec, mat)] = chosen
    for kind in _STRING_KEY_KINDS:
        for key, chosen in stored.get(kind, {}).items():
            out[kind][key] = chosen
    return out


def clear():
    """Delete the persisted overrides file, if any."""
    path = _store_path()
    try:
        if os.path.exists(path):
            os.remove(path)
            logger.info("Cleared learned type overrides at %s", path)
    except Exception as e:
        logger.warning("Could not clear learned overrides at %s: %s", path, e)
