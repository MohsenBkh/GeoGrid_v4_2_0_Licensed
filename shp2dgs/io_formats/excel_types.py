#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
io_formats.excel_types
=======================
Import/export the equipment type library (``TypLne``, ``TypTr2``,
``TypLod``, ``TypFuse``) to/from an Excel workbook, as an alternative to
the native ``types_override.json`` format.

**Excel layout** (see :func:`export_type_library_to_excel`):
    - One worksheet per equipment type, named exactly ``TypLne``,
      ``TypTr2``, ``TypLod``, ``TypFuse``.
    - Row 1: header, taken verbatim from ``dgs_template.json`` (the
      ``field(type:length)`` syntax, e.g. ``rline(r)``), so a round-trip
      export/import always matches the template's column order and types.
    - Row 2+: equipment data, one row per type.

**Field type validation** (see :func:`parse_field_spec`):
    - ``a`` / ``a:NN`` -- alphanumeric text, optionally capped at ``NN``
      characters.
    - ``r`` -- real number (float).
    - ``i`` -- integer.
    - ``p`` -- pointer/id field; treated as opaque text, not validated.
"""

import logging
import re

from shp2dgs.core.constants import TYPE_LIBRARY_SHEETS

logger = logging.getLogger("shp2dgs.excel_types")

try:
    import openpyxl
except ImportError as _e:  # pragma: no cover
    openpyxl = None
    _OPENPYXL_IMPORT_ERROR = _e
else:
    _OPENPYXL_IMPORT_ERROR = None

_FIELD_SPEC_RE = re.compile(r"^(?P<name>[^()]+)\((?P<type>[airp])(?::(?P<length>\d+))?\)$")

#: Columns the app generates and maintains itself (see
#: ``gui.dialogs.type_library_editor_dialog.SYSTEM_COLUMN_NAMES``): a
#: hand-authored or third-party-exported workbook is not expected to
#: supply these, so they are auto-filled on import rather than rejected.
_SYSTEM_COLUMNS = {"ID", "fold_id"}


def _bare(header_entry):
    """Strip the ``"(type:length)"`` suffix from a header entry."""
    return header_entry.split("(")[0]


def _existing_fold_id(template, sheet_name):
    """Return the ``fold_id`` used by ``sheet_name``'s existing rows in
    ``template`` (they should all share one folder), or ``None``."""
    sheet = template.get(sheet_name) or {}
    header = sheet.get("header", [])
    fold_idx = next((i for i, h in enumerate(header) if _bare(h) == "fold_id"), None)
    if fold_idx is None:
        return None
    for row in sheet.get("rows", []):
        if fold_idx < len(row) and row[fold_idx] is not None:
            return row[fold_idx]
    return None


def _next_free_id(template, already_used):
    """Return an integer ID higher than any ``ID`` value already used
    anywhere in ``template`` or in ``already_used``, so auto-generated
    IDs never collide with existing objects or each other."""
    max_id = max(already_used, default=0)
    for sheet in template.values():
        if not isinstance(sheet, dict):
            continue
        header = sheet.get("header", [])
        id_idx = next((i for i, h in enumerate(header) if _bare(h) == "ID"), None)
        if id_idx is None:
            continue
        for row in sheet.get("rows", []):
            if id_idx < len(row) and isinstance(row[id_idx], (int, float)):
                max_id = max(max_id, int(row[id_idx]))
    return max_id + 1


class ExcelTypeLibraryError(Exception):
    """Raised for any Excel type-library import/export problem that should
    be surfaced to the user as a clear, actionable message (bad sheet
    name, missing header, wrong data type, ...)."""


def _ensure_openpyxl_available():
    if openpyxl is None:
        raise ImportError(
            "The 'openpyxl' package is required for Excel import/export but is "
            "not installed. Install it with: pip install openpyxl"
        ) from _OPENPYXL_IMPORT_ERROR


def parse_field_spec(header_name):
    """Parse a ``field(type:length)`` header entry.

    Args:
        header_name: e.g. ``"rline(r)"``, ``"chr_name(a:20)"``, ``"ID(a:40)"``.

    Returns:
        tuple: ``(field_name, type_code, max_length_or_None)`` where
        ``type_code`` is one of ``"a"``, ``"r"``, ``"i"``, ``"p"``.

    Raises:
        ValueError: If ``header_name`` does not match the expected syntax.
    """
    m = _FIELD_SPEC_RE.match(header_name.strip())
    if not m:
        raise ValueError(f"Header entry does not match 'field(type[:length])' syntax: {header_name!r}")
    length = int(m.group("length")) if m.group("length") else None
    return m.group("name"), m.group("type"), length


def validate_cell(value, type_code, max_length, field_name, sheet_name, row_number):
    """Validate/coerce a single cell value against its DGS field type.

    Args:
        value: Raw cell value as read from Excel (may already be an
            ``int``/``float`` if openpyxl parsed it as numeric, or a
            ``str``, or ``None`` for an empty cell).
        type_code: ``"a"``, ``"r"``, ``"i"``, or ``"p"`` (see
            :func:`parse_field_spec`).
        max_length: Max character length for ``"a"`` fields, else ``None``.
        field_name: Field name, for error messages.
        sheet_name: Sheet name, for error messages.
        row_number: 1-based Excel row number, for error messages.

    Returns:
        The coerced value (``float`` for ``"r"``, ``int`` for ``"i"``,
        ``str`` for ``"a"``/``"p"``), or ``None`` if ``value`` was empty.

    Raises:
        ExcelTypeLibraryError: If the value cannot be coerced to the
            expected type, or exceeds ``max_length``.
    """
    if value is None or (isinstance(value, str) and value.strip() == ""):
        return None
    where = f"sheet '{sheet_name}', row {row_number}, field '{field_name}'"
    if type_code == "r":
        try:
            return float(value)
        except (TypeError, ValueError):
            raise ExcelTypeLibraryError(f"Expected a numeric value at {where}, got: {value!r}")
    if type_code == "i":
        try:
            return int(float(value))
        except (TypeError, ValueError):
            raise ExcelTypeLibraryError(f"Expected an integer value at {where}, got: {value!r}")
    # "a" (alphanumeric) and "p" (pointer) are both treated as text.
    text = str(value).strip()
    if type_code == "a" and max_length is not None and len(text) > max_length:
        raise ExcelTypeLibraryError(
            f"Text too long at {where}: {len(text)} characters, maximum is {max_length}"
        )
    return text


def export_type_library_to_excel(template, out_path, sheet_names=None):
    """Export the equipment type library to an Excel workbook, one sheet
    per equipment type.

    Args:
        template: The (possibly already-overridden) ``dgs_template.json``
            dict, e.g. as edited in :class:`TypeLibraryEditorDialog`.
        out_path: Destination ``.xlsx`` path.
        sheet_names: Which sheets to export; defaults to
            :data:`~shp2dgs.core.constants.TYPE_LIBRARY_SHEETS`.

    Returns:
        str: ``out_path``.

    Raises:
        ImportError: If ``openpyxl`` is not installed.
        ExcelTypeLibraryError: If a requested sheet is missing from
            ``template``.
        OSError: If the file cannot be written.
    """
    _ensure_openpyxl_available()
    sheet_names = sheet_names or TYPE_LIBRARY_SHEETS
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for sheet_name in sheet_names:
        if sheet_name not in template:
            raise ExcelTypeLibraryError(f"Template has no sheet named '{sheet_name}'")
        ws = wb.create_sheet(sheet_name)
        header = template[sheet_name]["header"]
        ws.append(list(header))
        for row in template[sheet_name].get("rows", []):
            ws.append(list(row))
        for col_idx, _ in enumerate(header, start=1):
            ws.column_dimensions[openpyxl.utils.get_column_letter(col_idx)].width = 16
        ws.freeze_panes = "A2"
    try:
        wb.save(out_path)
    except PermissionError:
        logger.error("Permission denied writing Excel type library to %s", out_path)
        raise
    except OSError as e:
        logger.error("Failed to save Excel type library to %s: %s", out_path, e)
        raise
    logger.info("Exported type library to Excel: %s (%d sheets)", out_path, len(sheet_names))
    return out_path



def _drop_duplicate_named_rows(sheet_name, header, rows):
    """Drop rows that repeat an existing ``loc_name`` with *identical*
    values.

    A DGS type sheet is addressed by ``loc_name`` (that is how the
    converter resolves ``typ_id``), so two rows sharing a name make the
    resolved type ambiguous, and PowerFactory shows two indistinguishable
    entries. Real, hand-maintained workbooks do occasionally contain a
    copy-paste duplicate (the shipped ``types_override.xlsx`` has three,
    e.g. ``ABC33KV120``); silently importing them is worse than dropping
    them, but silently dropping *differing* rows would hide a real data
    conflict -- so only exact duplicates are removed, and anything that
    differs is left alone (with a warning naming the conflict).

    Args:
        sheet_name: Sheet name, for logging.
        header: The sheet's header list (already column-aligned).
        rows: The data rows (mutated-free; a new list is returned).

    Returns:
        list: Rows with exact duplicates removed, in their original order.
    """
    name_idx = next((i for i, h in enumerate(header) if _bare(h) == "loc_name"), None)
    if name_idx is None:
        return rows
    # Labels that are *expected* to differ between otherwise-identical rows:
    # the app-managed key columns, and chr_name (a free-text characteristic
    # tag, often just a running serial like 15 / 17). Two rows that agree on
    # every real parameter but differ only here are a copy-paste duplicate.
    ignorable = {i for i, h in enumerate(header)
                 if _bare(h) in ("ID", "fold_id", "chr_name")}
    compare_idx = [i for i in range(len(header)) if i not in ignorable]

    def signature(row):
        return tuple(row[i] if i < len(row) else None for i in compare_idx)

    seen = {}
    out = []
    for row in rows:
        name = row[name_idx] if name_idx < len(row) else None
        if name is None or (isinstance(name, str) and not name.strip()):
            out.append(row)
            continue
        key = str(name).strip().lower()
        if key not in seen:
            seen[key] = signature(row)
            out.append(row)
            continue
        if signature(row) == seen[key]:
            logger.warning("Sheet '%s': dropped duplicate row for loc_name %r "
                           "(identical parameters; the type name must be unique)",
                           sheet_name, name)
        else:
            logger.warning(
                "Sheet '%s': loc_name %r appears more than once with different values; "
                "keeping the first row -- the type name must be unique", sheet_name, name)
    return out


def import_type_library_from_excel(path, template):
    """Import an equipment-type-library Excel workbook (as produced by
    :func:`export_type_library_to_excel`, or hand-authored following the
    same layout) and validate it against ``template``'s field types.

    Column matching is **name-based, not positional**: every column name
    that appears in the corresponding sheet's expected header (from
    ``template``) must be present in the workbook sheet, but may appear
    in any order and may be interleaved with extra/custom columns (e.g.
    added via paste, or a reference-only column like a datasheet link).
    Extra columns are preserved in the resulting override as unvalidated
    fields.

    Args:
        path: Source ``.xlsx`` path.
        template: The current ``dgs_template.json`` dict (including any
            custom equipment-type sheets already added in the Type
            Library Editor), used to recognise which sheets/fields are
            expected. A sheet in the workbook that is not in ``template``
            is reported as an error (unknown equipment type); a sheet in
            ``template`` missing from the workbook is simply skipped (not
            an error -- the workbook may only update some types).

    Returns:
        dict: An override dict in the native ``types_override.json``
        "full sheet replacement" shape --
        ``{"TypLne": {"header": [...], "rows": [...]}, ...}`` -- ready to
        pass to :func:`~shp2dgs.core.config_io.merge_type_library` or to
        save directly as ``types_override.json``.

    Raises:
        ImportError: If ``openpyxl`` is not installed.
        ExcelTypeLibraryError: On any structural problem (unreadable
            file, unknown sheet name, missing required column, invalid
            field value) with a message that pinpoints the sheet/row/field.
    """
    _ensure_openpyxl_available()
    try:
        wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    except Exception as e:
        raise ExcelTypeLibraryError(f"Could not open Excel file '{path}': {e}") from e

    override = {}
    all_errors = []  # collected across every sheet, so one run reports everything at once
    used_ids = set()

    for sheet_name in wb.sheetnames:
        if sheet_name not in template:
            all_errors.append(
                f"Sheet '{sheet_name}' has no matching equipment type in the loaded template. "
                f"If this is a new custom equipment type, add it first via 'Add Type Category (+)' "
                f"in the Type Library Editor, then re-import."
            )
            continue
        ws = wb[sheet_name]
        rows_iter = ws.iter_rows(values_only=True)
        try:
            header_row = next(rows_iter)
        except StopIteration:
            all_errors.append(f"Sheet '{sheet_name}' is empty (no header row)")
            continue
        # Keep positions aligned with data rows -- a blank header cell
        # becomes a placeholder column name rather than silently
        # shifting every later column left.
        header = [str(h) if h is not None else f"Column{i + 1}" for i, h in enumerate(header_row)]

        expected_header = list(template[sheet_name]["header"])
        missing = [
            h for h in expected_header
            if h not in header and _bare(h) not in _SYSTEM_COLUMNS
        ]
        if missing:
            all_errors.append(
                f"Sheet '{sheet_name}' is missing required column(s): {missing}\n"
                f"    Found columns: {header}\n"
                f"    Re-export a fresh template from the Type Library Editor if unsure of the exact "
                f"column names. Extra/custom columns are fine and may appear in any order alongside "
                f"the required ones."
            )
            continue
        # ID/fold_id are app-managed and commonly absent from a
        # hand-authored or third-party-exported workbook -- auto-fill
        # them rather than rejecting an otherwise-valid file.
        missing_system_cols = [
            h for h in expected_header
            if _bare(h) in _SYSTEM_COLUMNS and h not in header
        ]

        # Columns beyond the required/known ones (e.g. added via paste, or
        # a custom reference-only column) are accepted as extra,
        # unvalidated fields -- parse_field_spec() only understands the
        # "field(type[:length])" syntax used by the real DGS columns.
        field_specs = []
        for h in header:
            try:
                field_specs.append(parse_field_spec(h))
            except ValueError:
                field_specs.append((h, "a", None))

        data_rows = []
        sheet_row_errors = []
        for row_number, raw_row in enumerate(rows_iter, start=2):
            if raw_row is None or all(v is None for v in raw_row):
                continue  # skip fully blank rows
            row_values = []
            for col_idx, (field_name, type_code, max_length) in enumerate(field_specs):
                raw_value = raw_row[col_idx] if col_idx < len(raw_row) else None
                try:
                    row_values.append(
                        validate_cell(raw_value, type_code, max_length, field_name, sheet_name, row_number)
                    )
                except ExcelTypeLibraryError as e:
                    sheet_row_errors.append(str(e))
                    row_values.append(None)
            data_rows.append(row_values)

        if sheet_row_errors:
            all_errors.extend(sheet_row_errors)
            continue

        if not data_rows:
            logger.warning("Sheet '%s' had a valid header but no data rows; skipping", sheet_name)
            continue

        out_header = list(header)
        if missing_system_cols:
            fold_id = _existing_fold_id(template, sheet_name)
            for col_header in missing_system_cols:
                out_header.append(col_header)
                is_id_col = _bare(col_header) == "ID"
                for row_values in data_rows:
                    if is_id_col:
                        new_id = _next_free_id(template, used_ids)
                        used_ids.add(new_id)
                        row_values.append(new_id)
                    else:
                        row_values.append(fold_id)
            logger.info(
                "Sheet '%s': auto-generated %s (not present in the workbook)",
                sheet_name, [_bare(h) for h in missing_system_cols])

        deduped = _drop_duplicate_named_rows(sheet_name, out_header, data_rows)
        if len(deduped) != len(data_rows):
            logger.info("Sheet '%s': %d duplicate row(s) removed", sheet_name,
                        len(data_rows) - len(deduped))
        data_rows = deduped

        override[sheet_name] = {"header": out_header, "rows": data_rows}
        logger.info("Imported %d rows from Excel sheet '%s'", len(data_rows), sheet_name)

    if all_errors:
        shown = all_errors[:25]
        more = f"\n  ... and {len(all_errors) - 25} more" if len(all_errors) > 25 else ""
        listing = "\n".join(f"  - {msg}" for msg in shown)
        raise ExcelTypeLibraryError(
            f"The workbook has {len(all_errors)} problem(s) that must be fixed before import:\n"
            f"{listing}{more}"
        )

    if not override:
        raise ExcelTypeLibraryError("No usable data rows found in any recognised sheet of the workbook")
    return override
