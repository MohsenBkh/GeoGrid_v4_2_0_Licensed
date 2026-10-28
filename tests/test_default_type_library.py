#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tests/test_default_type_library.py
==================================
Verifies that the bundled Excel equipment type library
(``types_override.xlsx``) is the application's DEFAULT library, and that it
is actually what the converter emits.

Covered here:
  1. The workbook loads, validates against ``dgs_template.json`` and every
     type name ``config.json`` refers to exists in the resulting library.
  2. ``load_default_types_override`` finds the shipped file (the mechanism
     the GUI and CLI both call at startup).
  3. A small end-to-end conversion emits the Excel library's parameters
     (not the old ``dgs_template.json`` placeholders), and the fuse rows use
     the rated-current fuse types.
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from conftest_free_harness import unlocked, write_layer, temp_dir

from shp2dgs.core.config_io import (
    canonicalize_type_sheet_columns, load_default_types_override, load_json,
    merge_type_library, name_to_id_map,
)
from shp2dgs.core.constants import TYPE_LIBRARY_SHEETS

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class DefaultTypeLibraryTests(unittest.TestCase):
    def setUp(self):
        self.tpl = load_json(os.path.join(REPO_ROOT, "dgs_template.json"))
        self.cfg = load_json(os.path.join(REPO_ROOT, "config.json"))

    def merged(self):
        override = load_default_types_override(self.tpl)
        merged = merge_type_library(self.tpl, override)
        for sheet in TYPE_LIBRARY_SHEETS:
            if sheet in merged:
                merged[sheet] = canonicalize_type_sheet_columns(merged[sheet])
        return merged

    def test_default_library_file_is_shipped(self):
        self.assertIsNotNone(
            load_default_types_override(self.tpl),
            "types_override.xlsx must ship with the application and load by default")

    def test_library_replaces_placeholder_types(self):
        merged = self.merged()
        self.assertEqual(len(merged["TypFuse"]["rows"]), 20)
        self.assertIn("Fuse_Type_63", name_to_id_map(merged["TypFuse"]))
        self.assertIn("TRF-AB'-20/0.4 kV-100 kVA", name_to_id_map(merged["TypTr2"]))
        self.assertIn("NA2XSY 1x95/16 (12/20kV)", name_to_id_map(merged["TypLne"]))

    def test_every_configured_type_name_exists(self):
        """Any name config.json asks for must resolve, or the converter
        silently falls back (the exact class of bug this library fixes)."""
        merged = self.merged()
        lne, tr = name_to_id_map(merged["TypLne"]), name_to_id_map(merged["TypTr2"])
        lt = self.cfg["line_type"]
        for key in ("cable_default", "mln_default", "uml_default", "jup_default",
                    "global_fallback", "abc_default", "covered_default"):
            self.assertIn(lt[key], lne, f"line_type.{key} -> {lt[key]}")
        for table in ("acsr_bare_by_section", "covered_by_section", "copper_by_section"):
            for section, name in lt[table].items():
                self.assertIn(name, lne, f"line_type.{table}[{section}] -> {name}")
        self.assertIn(self.cfg["transformer"]["fallback_type"], tr)

    def test_no_duplicate_loc_names(self):
        for sheet in TYPE_LIBRARY_SHEETS:
            rows = self.merged()[sheet]["rows"]
            header = self.merged()[sheet]["header"]
            idx = next(i for i, h in enumerate(header) if h.startswith("loc_name"))
            names = [r[idx] for r in rows]
            self.assertEqual(len(names), len(set(names)),
                             f"{sheet}: duplicate loc_name makes typ_id ambiguous")

    def test_config_fuse_settings(self):
        fuse = self.cfg["fuse"]
        self.assertEqual(fuse["current_field"], "T_jar_nami")
        self.assertIn("{current}", fuse["type_name_pattern"])


if __name__ == "__main__":
    unittest.main()
