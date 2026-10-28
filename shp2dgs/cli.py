#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
shp2dgs.cli
============
Command-line entry point. Supports both:

- **Headless CLI** conversion: ``shp2dgs --shp <dir> --out <xlsx> [options]``
  or ``--no-gui`` with a bare shapefile dir/out pair.
- **GUI launch**: run with no ``--shp``/``--out`` (or explicitly request
  the GUI) to open the full desktop application.

This module is the *only* place that parses ``sys.argv`` -- both
``shp2dgs/__main__.py`` and the legacy ``shp_to_dgs.py`` wrapper simply
call :func:`main`.
"""

import argparse
import logging
import os
import sys
import traceback

from shp2dgs.core.config_io import (load_default_types_override, load_json,
                                    load_types_override_file, resource_path)
from shp2dgs.core.constants import APP_VERSION
from shp2dgs.core.converter import Converter
from shp2dgs.gui.qt_compat import GUI_AVAILABLE
from shp2dgs.utils.logging_setup import configure_logging, get_log_file_path

logger = logging.getLogger("shp2dgs.cli")


def _ask_paths_gui_legacy(default_out="Network_DGS.xlsx"):
    """Tkinter-based fallback path picker, used only when neither PySide6
    nor PyQt5 is installed but the user still wants an interactive picker
    instead of typing ``--shp``/``--out`` on the command line.

    Returns:
        tuple: ``(shp_dir, out_path)``, either of which may be ``None``
        if the user cancelled.
    """
    try:
        import tkinter as tk
        from tkinter import filedialog, messagebox
    except Exception as e:
        print("GUI (tkinter) unavailable:", e)
        return None, None
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    messagebox.showinfo("SHP -> DGS Converter", "1) Select shapefile FOLDER (ELW*.shp).\n2) Choose output .xlsx")
    shp_dir = filedialog.askdirectory(title="Select the shapefile folder")
    if not shp_dir:
        messagebox.showwarning("Cancelled", "No shapefile folder selected.")
        root.destroy()
        return None, None
    out_path = filedialog.asksaveasfilename(title="Save DGS deck", defaultextension=".xlsx",
                                             initialfile=default_out,
                                             filetypes=[("Excel workbook", "*.xlsx"), ("All files", "*.*")])
    root.destroy()
    if not out_path:
        return None, None
    return shp_dir, out_path


def build_arg_parser():
    """Build the ``argparse.ArgumentParser`` for the CLI.

    Returns:
        argparse.ArgumentParser
    """
    ap = argparse.ArgumentParser(
        prog="shp2dgs",
        description="Convert distribution shapefiles to DIgSILENT DGS xlsx.")
    ap.add_argument("--version", action="version", version=f"%(prog)s {APP_VERSION}")
    ap.add_argument("--shp", help="directory containing shapefiles")
    ap.add_argument("--out", help="output .xlsx path")
    ap.add_argument("--config", default=resource_path("config.json"))
    ap.add_argument("--template", default=resource_path("dgs_template.json"))
    ap.add_argument("--no-graphics", action="store_true")
    ap.add_argument("--no-gui", action="store_true")
    ap.add_argument("--project-name", help="Project / Grid name (ElmNet.loc_name)")
    ap.add_argument("--deck-name", help="Deck / IntGrfnet name")
    ap.add_argument("--grid-name", help="Alias for --project-name")
    ap.add_argument("--nominal-kv", type=float, help="Nominal MV kV")
    ap.add_argument("--frequency", type=float, help="Grid frequency Hz")
    ap.add_argument("--load-factor", type=float, help="Transformer loading factor")
    ap.add_argument("--cosphi", type=float, help="Load power factor")
    ap.add_argument("--element-size", type=float, help="Diagram size of transformer/load/etc. symbol boxes")
    ap.add_argument("--node-spacing", type=float,
                     help="Spacing between HV busbar/transformer/LV busbar/load in the diagram")
    ap.add_argument("--terminal-config", help="JSON file with terminal type mapping")
    ap.add_argument("--types", dest="types_override",
                     help="Equipment type library override file (JSON or Excel .xlsx). "
                          "Defaults to the bundled types_override.xlsx; pass --no-default-types to "
                          "convert with dgs_template.json only.")
    ap.add_argument("--no-default-types", action="store_true",
                     help="Do not load the bundled default type library (types_override.xlsx)")
    ap.add_argument("--gps", action="store_true",
                     help="Generate GPSlat/GPSlon on ElmTerm for PowerFactory's Geographic Diagram "
                          "(requires pyproj; source CRS auto-detected from .prj, or use --epsg)")
    ap.add_argument("--gps-lines", action="store_true",
                     help="With --gps: also write full line-route GPS points (GPSlat:i/GPSlon:i) on ElmLne")
    ap.add_argument("--epsg", help="Explicit source EPSG code for --gps (e.g. 32639 for UTM Zone 39N)")
    ap.add_argument("--no-mv", action="store_true",
                     help="Skip MV line/switch/fuse layers (transformers/sources are still read)")
    ap.add_argument("--no-lv", action="store_true", help="Skip the LV network entirely")
    ap.add_argument("--layer", action="append", metavar="KEY=VALUE", default=[],
                     help="Override a shapefile layer mapping, e.g. --layer mv_capacitor=ELWCNT. "
                          "Use --layer KEY= (empty value) to disable that equipment slot. "
                          "Repeatable. See shp2dgs.core.equipment_catalog for valid keys.")
    ap.add_argument("--prescan", action="store_true",
                     help="Scan the source shapefiles for attribute combinations (underground cable "
                          "material/section, transformer capacity) that would fall back to a default "
                          "type, print/save a review list, and exit WITHOUT converting. Use this to "
                          "check data quality before committing to a full run.")
    ap.add_argument("--type-overrides", metavar="JSON_FILE",
                     help="JSON file of manually-chosen types for prescan-flagged items, in the format "
                          "produced by --prescan (edit the generated file and pass it back in). Consulted "
                          "before any automatic default/fallback during the real conversion.")
    return ap


def _project_meta_from_args(args):
    project_meta = {}
    if args.project_name:
        project_meta["project_name"] = args.project_name
    if args.grid_name:
        project_meta["grid_name"] = args.grid_name
    if args.project_name and not args.grid_name:
        project_meta["grid_name"] = args.project_name
    if args.deck_name:
        project_meta["deck_name"] = args.deck_name
    if args.nominal_kv:
        project_meta["nominal_mv_kv"] = args.nominal_kv
    if args.frequency:
        project_meta["frequency_hz"] = args.frequency
    if args.load_factor:
        project_meta["loading_factor"] = args.load_factor
    if args.cosphi:
        project_meta["power_factor_cos"] = args.cosphi
    if args.element_size:
        project_meta["element_size"] = args.element_size
    if args.node_spacing:
        project_meta["node_spacing"] = args.node_spacing
    return project_meta or None


def _load_types_override(path, template=None):
    """Load a type-library override from either JSON or Excel, based on
    the file extension.

    Args:
        path: Override file (``.xlsx`` or ``.json``).
        template: The parsed ``dgs_template.json`` used to validate an
            Excel workbook and to auto-fill its ID/fold_id columns. Read
            from the bundled template when omitted.

    Returns:
        dict: Override in the shape ``merge_type_library`` accepts.
    """
    if template is None:
        template = load_json(resource_path("dgs_template.json"))
    return load_types_override_file(path, template)


def _prescan_to_records(prescan):
    """Flatten prescan_unresolved()'s ``{kind: [group, ...]}`` result into a
    flat, JSON-serialisable list (tuple keys aren't valid JSON keys) that
    doubles as the ``--type-overrides`` input format: the user fills in
    ``chosen_type`` for whichever rows they want to override and passes
    the same file back with ``--type-overrides``."""
    records = []
    for kind, groups in prescan.items():
        for g in groups:
            key = g["key"]
            rec = {"kind": kind, "count": g["count"], "example_ids": g["example_ids"],
                   "current_default": g["current_default"], "chosen_type": ""}
            if kind == "mv_underground_line":
                rec["section"], rec["material"] = key
            else:
                rec["capacity"] = key
            records.append(rec)
    return records


def _records_to_manual_overrides(records):
    """Inverse of :func:`_prescan_to_records`'s key convention: build the
    ``set_manual_overrides()``-ready dict from a (possibly user-edited)
    flat record list, skipping any row without a ``chosen_type``."""
    overrides = {"mv_underground_line": {}, "transformer": {}}
    for rec in records:
        chosen = (rec.get("chosen_type") or "").strip()
        if not chosen:
            continue
        if rec.get("kind") == "mv_underground_line":
            overrides["mv_underground_line"][(str(rec["section"]), str(rec["material"]))] = chosen
        elif rec.get("kind") == "transformer":
            overrides["transformer"][str(rec["capacity"])] = chosen
    return overrides


def main(argv=None):
    """CLI entry point.

    Args:
        argv: Optional argument list (defaults to ``sys.argv[1:]``);
            mainly useful for testing.

    Returns:
        None. Calls ``sys.exit`` with an appropriate exit code (0 success,
        non-zero on error), matching the original script's behaviour so
        existing automation/batch scripts keep working unchanged.
    """
    log_path = configure_logging()

    ap = build_arg_parser()
    args = ap.parse_args(argv)

    # --- License gate (startup) -------------------------------------
    # The GUI shows its own Activation screen when unlicensed (see
    # shp2dgs/gui/app.py); the headless CLI path fails fast here with a
    # short message instead of a stack trace. Either way this is a
    # *convenience* early-exit -- the authoritative check is repeated
    # inside Converter.__init__ (Core), so patching this block alone
    # does not unlock conversions.
    from shp2dgs.license.gate import require_active_license, LicenseError
    if args.no_gui or (args.shp and args.out):
        try:
            require_active_license(feature="gis_import")
        except LicenseError as e:
            logger.error("License check failed: %s", e.result.message)
            print(f"GeoGrid is not licensed to run: {e.result.message}")
            print("Run GeoGrid without --no-gui to activate it.")
            sys.exit(2)

    shp_dir, out_path = args.shp, args.out
    use_gui = (not shp_dir or not out_path) and not args.no_gui

    if use_gui:
        if GUI_AVAILABLE:
            from shp2dgs.gui.app import run_gui
            if not shp_dir or not out_path:
                # No explicit paths given at all -- launch the full GUI
                # application (menus, tabs, settings dialogs) rather than
                # just a path-picker, since that is what most users expect
                # from double-clicking the .exe.
                sys.exit(run_gui(args.config, args.template))
        else:
            gshp, gout = _ask_paths_gui_legacy()
            shp_dir = shp_dir or gshp
            out_path = out_path or gout

    if not shp_dir or not out_path:
        err_msg = "shapefile folder and output path are required"
        ap.error(err_msg)

    if not os.path.isdir(shp_dir):
        print(f"ERROR: Input directory does not exist: {shp_dir}", file=sys.stderr)
        sys.exit(2)

    project_meta = _project_meta_from_args(args)

    terminal_types = None
    if args.terminal_config:
        try:
            terminal_types = load_json(args.terminal_config)
        except Exception as e:
            print(f"ERROR loading terminal config: {e}", file=sys.stderr)
            sys.exit(2)

    # --- equipment type library -------------------------------------
    # The maintained library ships as types_override.xlsx and is the
    # DEFAULT for every conversion; --types points at a different file and
    # --no-default-types restores the bare dgs_template.json behaviour.
    cfg = load_json(args.config)
    tpl = load_json(args.template)
    types_override = None
    if not args.no_default_types:
        try:
            types_override = load_default_types_override(tpl)
        except Exception as e:
            print(f"ERROR loading the bundled default type library: {e}", file=sys.stderr)
            sys.exit(2)
    if args.types_override:
        try:
            types_override = _load_types_override(args.types_override, tpl)
        except Exception as e:
            print(f"ERROR loading types override: {e}", file=sys.stderr)
            sys.exit(2)

    logger.info("=" * 60)
    logger.info("SHP -> DGS Converter v%s starting", APP_VERSION)
    logger.info("  Input : %s", shp_dir)
    logger.info("  Output: %s", out_path)
    logger.info("  Graphics: %s", not args.no_graphics)
    if args.gps:
        logger.info("  GPS: enabled (lines=%s, epsg=%s)", args.gps_lines, args.epsg or "auto-detect from .prj")
    if project_meta:
        logger.info("  Project meta: %s", project_meta)
    if terminal_types:
        logger.info("  Terminal types: %s", terminal_types)
    if types_override:
        logger.info("  Type library: %d sheet(s) overridden", len(types_override))
    else:
        logger.info("  Type library: dgs_template.json only")
    logger.info("=" * 60)

    layer_overrides = {}
    for item in args.layer:
        if "=" not in item:
            print(f"ERROR: --layer must be KEY=VALUE, got: {item}", file=sys.stderr)
            sys.exit(2)
        key, _, value = item.partition("=")
        layer_overrides[key.strip()] = value.strip() or None

    try:
        conv = Converter(shp_dir, cfg, tpl,
                          make_graphics=not args.no_graphics,
                          project_meta=project_meta,
                          terminal_types=terminal_types,
                          type_library_override=types_override,
                          generate_gps=args.gps,
                          gps_line_vertices=args.gps_lines,
                          source_epsg=args.epsg,
                          layer_overrides=layer_overrides or None,
                          include_mv=not args.no_mv,
                          include_lv=not args.no_lv)

        if args.prescan:
            import json as _json
            prescan = conv.prescan_unresolved()
            records = _prescan_to_records(prescan)
            total_groups = len(records)
            total_records = sum(r["count"] for r in records)
            review_path = os.path.splitext(out_path)[0] + "_TypeReview.json"
            with open(review_path, "w", encoding="utf-8") as f:
                _json.dump(records, f, ensure_ascii=False, indent=2)
            print(f"\nPre-scan complete: {total_groups} distinct attribute combination(s) "
                  f"({total_records} record(s) total) would use a default/fallback type.")
            for r in records:
                loc = f"section={r.get('section')}, material={r.get('material')}" if r["kind"] == "mv_underground_line" \
                    else f"capacity={r.get('capacity')} kVA"
                print(f"  [{r['kind']}] {loc:45s} count={r['count']:5d} default->{r['current_default']!r}")
            print(f"\nReview file written: {review_path}")
            print("Edit 'chosen_type' for any row you want to control, then re-run with:")
            print(f"  --type-overrides {review_path}")
            print("(Rows left with an empty chosen_type keep using the default shown above.)")
            sys.exit(0)

        if args.type_overrides:
            try:
                import json as _json
                with open(args.type_overrides, encoding="utf-8") as f:
                    records = _json.load(f)
                conv.set_manual_overrides(_records_to_manual_overrides(records))
                chosen_count = sum(1 for r in records if (r.get("chosen_type") or "").strip())
                logger.info("Loaded %d manual type override(s) from %s", chosen_count, args.type_overrides)
            except Exception as e:
                print(f"ERROR loading --type-overrides: {e}", file=sys.stderr)
                sys.exit(2)

        conv.build().emit().write(out_path)
        print("\nWrote:", out_path)
        print(f"Log file: {log_path}")
        print(f"Project: {conv.project_meta.get('project_name')} / Deck: {conv.project_meta.get('deck_name')}")
        summary = conv.summary()
        warnings = summary.pop("_warnings", None)
        for k, v in summary.items():
            print(f"  {k:16s} {v}")
        if warnings:
            print("\nWarnings:")
            for w in warnings:
                print(f"  ! {w}")
        logger.info("Conversion completed successfully")
        sys.exit(0)

    except FileNotFoundError as e:
        logger.error(str(e))
        print(f"\nERROR: {e}", file=sys.stderr)
        sys.exit(2)
    except PermissionError as e:
        logger.error(str(e))
        print(f"\nERROR: Permission denied - {e}", file=sys.stderr)
        sys.exit(3)
    except Exception as e:
        logger.error("Fatal error", exc_info=True)
        print(f"\nFATAL ERROR: {e}", file=sys.stderr)
        print(f"See detailed log: {log_path}", file=sys.stderr)
        print(traceback.format_exc(), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
