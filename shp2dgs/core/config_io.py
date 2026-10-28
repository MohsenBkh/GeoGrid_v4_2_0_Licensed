#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
core.config_io
===============
JSON configuration I/O and equipment-type-library merging.

This module has no Qt dependency and no dependency on the converter
itself, so it can be unit tested (or reused by the Excel import/export
code) in isolation.
"""

import copy
import json
import logging
import os
import sys

logger = logging.getLogger("shp2dgs.config_io")


def resource_path(relative_path):
    """Resolve a path that works both when run from source and when
    bundled by PyInstaller.

    PyInstaller's ``--onefile`` mode extracts bundled data files (see the
    ``.spec`` file's ``datas=`` list) into a temporary directory exposed as
    ``sys._MEIPASS``. In source form there is no such attribute, so we fall
    back to the directory containing this file's package root.

    Args:
        relative_path: Path relative to the application/bundle root, e.g.
            ``"config.json"`` or ``"assets/icon.png"``.

    Returns:
        str: Absolute path to the resource.
    """
    try:
        base_path = sys._MEIPASS  # type: ignore[attr-defined]
    except Exception:
        # shp2dgs/core/config_io.py -> project root is two levels up.
        base_path = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(base_path, relative_path)


def load_json(path):
    """Load a JSON file, transparently falling back to the bundled
    resource copy (via :func:`resource_path`) if ``path`` does not exist.

    Args:
        path: Path to the JSON file. May be relative or absolute.

    Returns:
        The parsed JSON content (usually a ``dict``).

    Raises:
        FileNotFoundError: If neither ``path`` nor its bundled fallback
            exists.
        json.JSONDecodeError: If the file exists but is not valid JSON.
    """
    if not os.path.exists(path):
        alt = resource_path(os.path.basename(path))
        if os.path.exists(alt):
            path = alt
        else:
            raise FileNotFoundError(f"Config file not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    logger.info("Loaded JSON: %s", path)
    return data


def save_json(path, data):
    """Write ``data`` to ``path`` as pretty-printed, UTF-8 JSON.

    Args:
        path: Destination file path. Parent directories are *not* created
            automatically; the caller is expected to pick an existing
            directory (e.g. via a file-save dialog).
        data: JSON-serialisable object to write.

    Raises:
        OSError: If the file cannot be written (permissions, missing
            directory, disk full, etc.). The error is logged before being
            re-raised so both the log file and the caller see it.
    """
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logger.info("Saved JSON: %s", path)
    except OSError as e:
        logger.error("Failed to save JSON %s: %s", path, e)
        raise


def _bare(header_entry):
    """Strip the ``"(type:length)"`` suffix from a header entry, e.g.
    ``"loc_name(a:40)"`` -> ``"loc_name"``."""
    return header_entry.split("(")[0]


def header_col_index(header, bare_name, default=None):
    """Find the index of the column named ``bare_name`` in ``header``.

    Type-library sheets are **not guaranteed to keep a fixed column
    order** -- the Excel import explicitly allows columns in any order
    (see ``io_formats.excel_types.import_type_library_from_excel``), and
    a hand-authored override may omit or reorder columns entirely. Any
    code that needs "the ID column" or "the loc_name column" must look
    it up by name via this helper rather than assuming a fixed position
    (e.g. ``row[0]`` / ``row[1]``), or it will silently read the wrong
    field the moment a sheet's column order differs from the bundled
    ``dgs_template.json``.

    Args:
        header: A sheet's header list, e.g. ``["ID(a:40)", "loc_name(a:40)", ...]``.
        bare_name: Column name without the type suffix, e.g. ``"loc_name"``.
        default: Value returned if the column is not found.

    Returns:
        int or ``default``: The column index, or ``default`` if absent.
    """
    for i, h in enumerate(header):
        if _bare(h) == bare_name:
            return i
    return default


def name_to_id_map(sheet):
    """Build a ``{loc_name: ID}`` lookup for a type-library sheet,
    resolving both columns by header name (not fixed position).

    Args:
        sheet: A ``{"header": [...], "rows": [...]}`` sheet dict, e.g.
            ``template["TypTr2"]``.

    Returns:
        dict: ``{loc_name: ID}``. Rows missing either column, or whose
        ``loc_name`` is empty, are skipped.
    """
    header = sheet.get("header", [])
    id_idx = header_col_index(header, "ID")
    name_idx = header_col_index(header, "loc_name")
    if id_idx is None or name_idx is None:
        logger.warning(
            "Type sheet missing 'ID' and/or 'loc_name' column (header=%s) - "
            "type lookups against it will not resolve", header)
        return {}
    out = {}
    for row in sheet.get("rows", []):
        if name_idx < len(row) and id_idx < len(row) and row[name_idx]:
            out[row[name_idx]] = row[id_idx]
    return out


_CANONICAL_PREFIX = ("ID", "loc_name", "fold_id")


def canonicalize_type_sheet_columns(sheet):
    """Reorder one type-library sheet so ``ID``, ``loc_name``, ``fold_id``
    are always columns 0/1/2 in that order, followed by every other
    column in its original relative order. No-op (returns the sheet
    unchanged) if any of the three canonical columns is missing, or the
    sheet is already in that order.

    Import/merge deliberately accept any column order (a hand-authored
    workbook may list ``loc_name`` first, or omit ``ID``/``fold_id``
    entirely -- see ``io_formats.excel_types``). That flexibility is
    fine for *reading*, but two things need a stable, known order
    afterwards: the final output workbook should present these system
    columns in a predictable place, and any code that builds a brand new
    row by position (e.g. synthesized LV line/fuse types) needs to know
    exactly where to put the ID/name/fold_id it's constructing.

    Args:
        sheet: A ``{"header": [...], "rows": [...]}`` sheet dict.

    Returns:
        dict: A new sheet dict with reordered ``header`` and ``rows``
        (the input is not mutated).
    """
    header = sheet.get("header", [])
    idx = {name: header_col_index(header, name) for name in _CANONICAL_PREFIX}
    if any(i is None for i in idx.values()):
        return sheet  # sheet doesn't have all three -- leave it as-is
    order = [idx["ID"], idx["loc_name"], idx["fold_id"]]
    order += [i for i in range(len(header)) if i not in order]
    if order == list(range(len(header))):
        return sheet  # already canonical
    new_header = [header[i] for i in order]
    new_rows = [[row[i] if i < len(row) else None for i in order] for row in sheet.get("rows", [])]
    return {"header": new_header, "rows": new_rows}


def merge_type_library(base_template, override):
    """Deep-merge an equipment-type-library override into the base
    ``dgs_template.json`` structure.

    Three override shapes are supported per sheet (see
    ``types_override_example.json`` for worked examples):

    - **Full sheet replacement**: ``{"TypLne": {"header": [...], "rows": [...]}}``
    - **Rows-only replacement**: ``{"TypLne": {"rows": [...]}}``
    - **Per-type parameter patch**: ``{"TypLne": {"ACSR-FOX": {"rline": 0.8}}}``
      -- looked up by ``loc_name`` (resolved from the sheet's own header,
      not a fixed column position) against the sheet's existing rows;
      unknown field names are matched with a prefix-fuzzy fallback
      against the ``field(type:length)`` header syntax.

    Args:
        base_template: The parsed ``dgs_template.json`` dict (not
            mutated -- a deep copy is returned).
        override: The parsed override dict, or ``None``/empty to skip
            merging entirely.

    Returns:
        dict: A new, merged template. If ``override`` is falsy, the
        original ``base_template`` object is returned unchanged (not
        copied), to avoid needless work on the common "no override" path.
    """
    if not override:
        return base_template
    merged = copy.deepcopy(base_template)
    for sheet_name, patch in override.items():
        if sheet_name.startswith("_"):
            # Convention: keys starting with "_" (e.g. "_comment",
            # "_formats") are documentation, not data.
            continue
        if sheet_name not in merged:
            logger.warning("Type override sheet %s not in template - adding", sheet_name)
            merged[sheet_name] = patch
            continue
        if not isinstance(patch, dict):
            logger.warning("Ignoring malformed override for sheet %s (expected object)", sheet_name)
            continue

        # Case A: full sheet dict with header+rows
        if "header" in patch and "rows" in patch:
            merged[sheet_name] = patch
            logger.info("Type library %s fully replaced (%d rows)", sheet_name, len(patch["rows"]))
            continue
        # Case C: {"rows": [...]}
        if "rows" in patch and "header" not in patch:
            merged[sheet_name]["rows"] = patch["rows"]
            logger.info("Type library %s rows replaced", sheet_name)
            continue

        # Case B: per-type parameter patch by loc_name
        _apply_parameter_patch(merged, sheet_name, patch)
    return merged


def _apply_parameter_patch(merged, sheet_name, patch):
    """Apply a per-type-name parameter patch (override Case B) to
    ``merged[sheet_name]`` in place.

    Args:
        merged: The in-progress merged template (mutated in place).
        sheet_name: Sheet key, e.g. ``"TypLne"``.
        patch: Mapping of ``{loc_name: {field: value, ...}, ...}``.
    """
    hdr = merged[sheet_name]["header"]
    loc_idx = header_col_index(hdr, "loc_name", default=1)
    try:
        name_to_row = {r[loc_idx]: i for i, r in enumerate(merged[sheet_name]["rows"]) if len(r) > loc_idx}
    except Exception as e:
        logger.warning("Could not index %s rows by loc_name: %s", sheet_name, e)
        name_to_row = {}

    # Map header field names (strip type suffix e.g. "rline(r)" -> "rline")
    col_map = {}
    for ci, h in enumerate(hdr):
        key = h.split("(")[0]
        col_map[key] = ci
        col_map[h] = ci

    patched = 0
    for type_name, param_dict in patch.items():
        if type_name not in name_to_row:
            logger.warning("Type %s not found in %s, skipping", type_name, sheet_name)
            continue
        row_i = name_to_row[type_name]
        row = merged[sheet_name]["rows"][row_i]
        if len(row) < len(hdr):
            row = row + [None] * (len(hdr) - len(row))
            merged[sheet_name]["rows"][row_i] = row
        for pkey, pval in param_dict.items():
            ci = col_map.get(pkey)
            if ci is None:
                for hk, hv in col_map.items():
                    if hk.startswith(pkey):
                        ci = hv
                        break
            if ci is not None and ci < len(row):
                row[ci] = pval
                patched += 1
    logger.info("Type library %s: patched %d fields across %d types", sheet_name, patched, len(patch))


# ----------------------------------------------------------------------
# Default (bundled) equipment type library
# ----------------------------------------------------------------------
#: The equipment type library that ships with the application and is loaded
#: automatically for every conversion. ``types_override.xlsx`` is the
#: maintained, human-editable source of truth (see
#: ``io_formats.excel_types`` for the workbook layout); a JSON override in
#: the same "full sheet replacement" shape would be accepted as well.
DEFAULT_TYPES_OVERRIDE_FILE = "types_override.xlsx"

#: No JSON fallback is used on purpose: ``types_override.json`` is the
#: user's *own* override file (see ``--types``), and silently treating a
#: leftover copy as the shipped library would resurrect the old placeholder
#: types this workbook replaces.
DEFAULT_TYPES_OVERRIDE_FALLBACKS = ()


def default_types_override_path():
    """Return the absolute path of the bundled default type library.

    Returns:
        str: Path inside the PyInstaller bundle (``sys._MEIPASS``) when
        frozen, else the repository/installation root.
    """
    return resource_path(DEFAULT_TYPES_OVERRIDE_FILE)


def load_types_override_file(path, template):
    """Load a type-library override from ``.xlsx`` (Excel) or ``.json``.

    Args:
        path: Source file. Chosen by extension: ``.xlsx``/``.xlsm`` go
            through :func:`~shp2dgs.io_formats.excel_types.
            import_type_library_from_excel` (validated against
            ``template``'s columns and field types), anything else is read
            as a native ``types_override.json``.
        template: The parsed ``dgs_template.json`` dict, used to validate
            and to supply auto-generated ``ID``/``fold_id`` values for an
            Excel workbook that doesn't carry those columns.

    Returns:
        dict: Override in the shape accepted by :func:`merge_type_library`.

    Raises:
        FileNotFoundError: If ``path`` does not exist.
        ExcelTypeLibraryError: If an Excel workbook is structurally invalid
            (unknown sheet, missing column, bad value).
    """
    if not os.path.exists(path):
        raise FileNotFoundError(f"Type library override not found: {path}")
    if path.lower().endswith((".xlsx", ".xlsm")):
        from shp2dgs.io_formats.excel_types import import_type_library_from_excel
        override = import_type_library_from_excel(path, template)
        logger.info("Loaded Excel type library: %s (%d sheet(s))", path, len(override))
        return override
    return load_json(path)


def load_default_types_override(template, path=None):
    """Load the bundled default equipment type library.

    This is what makes the shipped ``types_override.xlsx`` the *default*
    type library rather than an optional extra: the GUI and the CLI both
    call this at startup, so every conversion uses the maintained types
    unless the user supplies their own override.

    A missing default is not an error (the application falls back to plain
    ``dgs_template.json``); an *invalid* default is, so a broken/mis-edited
    workbook surfaces immediately instead of silently producing a deck with
    the old placeholder types.

    Args:
        template: The parsed ``dgs_template.json`` dict.
        path: Optional explicit path (e.g. for tests). Defaults to
            :func:`default_types_override_path`.

    Returns:
        dict: The override, or ``None`` if no default file is present.

    Raises:
        ExcelTypeLibraryError: If the default file exists but is invalid.
    """
    candidates = [path] if path else (
        [default_types_override_path()]
        + [resource_path(p) for p in DEFAULT_TYPES_OVERRIDE_FALLBACKS]
    )
    for candidate in candidates:
        if candidate and os.path.exists(candidate):
            logger.info("Using default equipment type library: %s", candidate)
            return load_types_override_file(candidate, template)
    logger.info("No default equipment type library found (checked %s); using dgs_template.json only",
                [os.path.basename(c) for c in candidates if c])
    return None
