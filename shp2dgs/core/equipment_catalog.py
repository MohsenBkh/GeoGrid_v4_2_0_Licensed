#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
core.equipment_catalog
========================
Defines every equipment "slot" GeoGrid knows how to convert, grouped by
voltage level (MV/LV). Each slot maps to one ``config["layers"]`` key.

This module is the single source of truth shared by:

- :class:`~shp2dgs.core.converter.Converter`, which reads
  ``config["layers"][key]`` to find each equipment's shapefile layer(s).
- :class:`~shp2dgs.gui.dialogs.layer_mapping_dialog.LayerMappingDialog`,
  which lets the user override any of these mappings per-run when their
  shapefiles don't use the expected default layer names.

Automatic name matching is a convenience default, not a requirement --
if a shapefile export uses different layer names, the mapping dialog
lets the user point each equipment slot at the correct file directly, so
a naming mismatch never silently produces a wrong/incomplete conversion.
"""

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class EquipmentSlot:
    """One equipment "slot" in the catalog.

    Attributes:
        key: The ``config["layers"]`` key for this slot (also used as a
            stable internal identifier).
        label: Human-readable name shown in the mapping UI.
        level: ``"mv"`` or ``"lv"``.
        category: Processing category -- one of ``"line"``, ``"switch"``,
            ``"fuse"``, ``"transformer"``, ``"source"``, ``"capacitor"``,
            ``"distribution_box"``. Determines how the Converter treats
            records from this layer.
        multi: Whether this slot accepts multiple shapefiles at once
            (e.g. ``lv_switch`` historically covers two similar device
            layers). The mapping UI allows selecting more than one file
            when ``True``.
        notes: Optional short explanation shown in the mapping UI for
            slots whose modelling is a simplification (e.g. auto-booster).
    """

    key: str
    label: str
    level: str
    category: str
    multi: bool = False
    notes: str = ""


#: The full equipment catalog, in display order (MV section, then LV).
EQUIPMENT_CATALOG: List[EquipmentSlot] = [
    # --- Medium voltage ---
    EquipmentSlot("mv_overhead_line", "MV Overhead Line", "mv", "line"),
    EquipmentSlot("mv_underground_line", "MV Underground Line (Cable)", "mv", "line"),
    EquipmentSlot("mv_other_lines", "Other MV Lines", "mv", "line"),
    EquipmentSlot("mv_abc_cable", "MV Self-Supporting Cable (ABC)", "mv", "line"),
    EquipmentSlot("mv_jumper", "MV Jumper", "mv", "line"),
    EquipmentSlot("mv_transformer", "Transformer", "mv", "transformer"),
    EquipmentSlot("mv_breaker", "Circuit Breaker", "mv", "switch"),
    EquipmentSlot("mv_disconnector", "Disconnector", "mv", "switch"),
    EquipmentSlot("mv_recloser", "Recloser", "mv", "switch"),
    EquipmentSlot("mv_sectionalizer", "Sectionalizer", "mv", "switch"),
    EquipmentSlot("mv_autobooster", "Auto-Booster", "mv", "switch",
                  notes="Modelled as a simple series switch (no voltage-regulation behaviour)."),
    EquipmentSlot("mv_feeder_switch", "Feeder-Head Switch", "mv", "switch"),
    EquipmentSlot("mv_capacitor", "Capacitor Bank", "mv", "capacitor",
                  notes="Reactive power rating uses a configurable default (config[\"capacitor\"])."),
    EquipmentSlot("mv_fuse", "Fuse / Cutout", "mv", "fuse"),
    EquipmentSlot("mv_source", "Feeder Source", "mv", "source"),
    # --- Low voltage ---
    EquipmentSlot("lv_overhead_line", "LV Overhead Line", "lv", "line"),
    EquipmentSlot("lv_underground_line", "LV Underground Line", "lv", "line"),
    EquipmentSlot("lv_other_lines", "Other LV Lines", "lv", "line"),
    EquipmentSlot("lv_jumper", "LV Jumper", "lv", "line"),
    EquipmentSlot("lv_abc_cable", "LV Self-Supporting Cable (ABC)", "lv", "line"),
    EquipmentSlot("lv_service_cable", "Service Cable", "lv", "line",
                  notes="Customer drop cable; no per-customer load is fabricated (see README)."),
    EquipmentSlot("lv_fuse", "LV Fuse", "lv", "fuse"),
    EquipmentSlot("lv_switch", "LV Switch / Sectionalizer", "lv", "switch", multi=True),
    EquipmentSlot("lv_distribution_box", "LV Distribution Box", "lv", "distribution_box"),
    EquipmentSlot("lv_dg_source", "Distributed Generation Source", "lv", "source",
                  notes="Modelled as an equivalent source (ElmXnet), not a detailed generator model."),
]


def slot_by_key(key):
    """Return the :class:`EquipmentSlot` for ``key``, or ``None``."""
    for slot in EQUIPMENT_CATALOG:
        if slot.key == key:
            return slot
    return None


def slots_for_level(level):
    """Return catalog slots for ``level`` (``"mv"`` or ``"lv"``), in
    display order."""
    return [s for s in EQUIPMENT_CATALOG if s.level == level]


def resolve_layer_config(base_layers, overrides=None):
    """Merge a per-run layer-mapping override on top of
    ``config["layers"]``.

    Args:
        base_layers: The ``config["layers"]`` dict (key -> layer name,
            list of names, or ``None``).
        overrides: Optional ``{key: layer_name_or_list_or_None}`` dict
            from :class:`~shp2dgs.gui.dialogs.layer_mapping_dialog.LayerMappingDialog`.
            A key present with value ``None`` explicitly disables that
            slot for this run (skip it) rather than falling back to the
            default.

    Returns:
        dict: The effective layer mapping to use for this conversion run.
    """
    merged = dict(base_layers or {})
    if overrides:
        for key, value in overrides.items():
            merged[key] = value
    return merged


def find_available_shapefiles(shp_dir):
    """Scan ``shp_dir`` recursively for ``.shp`` files and return their
    base names (without extension), sorted.

    Args:
        shp_dir: Directory to scan.

    Returns:
        list: Sorted list of layer base names found (e.g. ``["ELWOML",
        "ELWDTR", ...]``). Empty list if ``shp_dir`` doesn't exist or
        contains no shapefiles.
    """
    import os
    names = set()
    if not shp_dir or not os.path.isdir(shp_dir):
        return []
    for root, _dirs, files in os.walk(shp_dir):
        for fn in files:
            if fn.lower().endswith(".shp"):
                names.add(os.path.splitext(fn)[0])
    return sorted(names)
