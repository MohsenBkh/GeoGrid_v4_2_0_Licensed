#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
core.converter
==============
The conversion engine: reads ELW* shapefiles and produces a DIgSILENT
PowerFactory DGS deck (multi-sheet ``.xlsx``), including an optional
geographic diagram (``IntGrf``/``IntGrfcon`` sheets).

Layer mapping (unchanged from the original v1/v2 scripts)::

    ELWOML/UML/MLN/JUP -> ElmLne
    ELWDTR              -> ElmTr2 + Elmlod
    ELWCBR/ELWDCS       -> ElmCoup
    ELWFCT               -> RelFuse
    ELWFWT               -> ElmXnet + ElmFeeder
    topology              -> ElmTerm + StaCubic + IntGrf/IntGrfcon

Typical usage::

    from shp2dgs.core.config_io import load_json
    from shp2dgs.core.converter import Converter

    cfg = load_json("config.json")
    tpl = load_json("dgs_template.json")
    conv = Converter("./shapefiles", cfg, tpl, make_graphics=True)
    conv.build().emit().write("output.xlsx")
    print(conv.summary())
"""

import collections
import math
import re
import copy
import logging
import os

try:
    import openpyxl
except ImportError as _e:  # pragma: no cover
    openpyxl = None
    _OPENPYXL_IMPORT_ERROR = _e
else:
    _OPENPYXL_IMPORT_ERROR = None

from shp2dgs.core.config_io import (merge_type_library, name_to_id_map,
                                    canonicalize_type_sheet_columns, header_col_index)
from shp2dgs.core.constants import BUSBAR_SYMBOLS, DEFAULT_PROJECT_META, DEFAULT_TERMINAL_TYPES
from shp2dgs.core.equipment_catalog import resolve_layer_config
from shp2dgs.core.progress import ProgressReporter
from shp2dgs.core.shapefile_io import read_layer, s
from shp2dgs.core import geo

logger = logging.getLogger("shp2dgs.converter")


def _ensure_openpyxl_available():
    if openpyxl is None:
        raise ImportError(
            "The 'openpyxl' package is required to write the DGS workbook but is "
            "not installed. Install it with: pip install openpyxl"
        ) from _OPENPYXL_IMPORT_ERROR


class Converter:
    """Converts a folder of ELW* shapefiles into a DIgSILENT DGS workbook.

    Args:
        shp_dir: Directory containing (or recursively containing) the
            ``.shp``/``.shx``/``.dbf`` layer triplets.
        config: Parsed ``config.json`` dict (field mappings, keyword
            tables, graphics sizing).
        template: Parsed ``dgs_template.json`` dict (fixed sheets +
            equipment-type-library sheets).
        make_graphics: Whether to emit ``IntGrf``/``IntGrfcon`` geographic
            diagram sheets (default ``True``). Disable for faster,
            smaller output when the diagram is not needed.
        progress_callback: Optional ``callback(percent, message, step,
            eta_seconds, elapsed)``, see :class:`~shp2dgs.core.progress.ProgressReporter`.
        project_meta: Optional dict overriding entries of
            :data:`~shp2dgs.core.constants.DEFAULT_PROJECT_META` (project
            name, deck name, nominal voltage, graphics sizing, ...).
        terminal_types: Optional dict overriding entries of
            :data:`~shp2dgs.core.constants.DEFAULT_TERMINAL_TYPES`
            (connection-kind -> DIgSILENT graphic symbol name).
        type_library_override: Optional equipment-type-library override,
            see :func:`~shp2dgs.core.config_io.merge_type_library`.
        generate_gps: If ``True``, additionally populate ``GPSlat``/
            ``GPSlon`` (WGS84 decimal degrees) on ``ElmTerm`` rows, which
            PowerFactory's **Insert \u2192 Geographic Diagram** feature uses
            to place the network on a real map background. This is
            independent of ``make_graphics`` (which only controls the
            local ``IntGrf`` diagram). Requires the optional ``pyproj``
            dependency and a resolvable source CRS (see ``source_epsg``).
            If either is unavailable, GPS generation is skipped with a
            logged warning rather than aborting the conversion -- it is
            supplementary data, and its absence should never block an
            otherwise-good MV/LV network conversion.
        gps_line_vertices: If ``True`` (and ``generate_gps`` is also
            ``True``), additionally write the full route of each line as
            ``GPSlat:0``/``GPSlon:0``, ``GPSlat:1``/``GPSlon:1``, \u2026
            columns on ``ElmLne`` (one pair per vertex), so lines follow
            their real physical path on the Geographic Diagram instead of
            a straight segment between their two terminals -- this is the
            mechanism for accurate line-path positioning inside
            PowerFactory itself (as opposed to this app's own Diagram
            Preview, which always draws the true path regardless of this
            setting). Verified against a real ~50k-line feeder; after
            import, rebuild the Geographic Diagram once in PowerFactory to
            show the route vertices. Defaults to ``False`` at this API
            level (explicit opt-in for CLI/library callers), but the GUI's
            corresponding checkbox defaults to checked.
        source_epsg: Optional explicit EPSG code (e.g. ``32639`` for UTM
            Zone 39N, common for Iran) for the shapefiles' source
            coordinate system. If omitted, the source CRS is
            auto-detected from a ``.prj`` sidecar file found next to the
            shapefiles. Only used when ``generate_gps=True``.
        layer_overrides: Optional ``{equipment_key: layer_name(s)}`` dict
            (see :mod:`shp2dgs.core.equipment_catalog`) overriding
            ``config["layers"]`` for this run -- e.g. from the GUI's
            Layer Mapping dialog when a shapefile export uses different
            layer names than the defaults. A key explicitly mapped to
            ``None`` disables that equipment slot for this run.
        include_mv: If ``False``, skip reading MV line/switch/fuse
            layers (transformers and sources are still read, since the
            LV network needs them as anchors). Default ``True``.
        include_lv: If ``False``, skip building the LV network entirely,
            regardless of whether LV layers are configured/present.
            Default ``True``.

    Raises:
        NotADirectoryError: If ``shp_dir`` does not exist.
        ValueError: If ``template`` is missing required keys/sheets.
    """

    def __init__(self, shp_dir, config, template,
                 make_graphics=True,
                 progress_callback=None,
                 project_meta=None,
                 terminal_types=None,
                 type_library_override=None,
                 generate_gps=False,
                 gps_line_vertices=False,
                 source_epsg=None,
                 layer_overrides=None,
                 include_mv=True,
                 include_lv=True):
        # --- License gate (Core-level; independent of the GUI/CLI gate) ---
        # This check exists here, in the Protected Core entry point, so
        # that removing the license check from the GUI/CLI startup path
        # does not by itself unlock conversions. See
        # shp2dgs/license/gate.py and docs/developer/anti_tamper.md.
        from shp2dgs.license.gate import require_active_license
        require_active_license(feature="gis_import")

        self.shp_dir = shp_dir
        self.cfg = copy.deepcopy(config)
        if type_library_override:
            template = merge_type_library(template, type_library_override)
        template = dict(template)
        for sheet_name in ("TypLne", "TypTr2", "TypLod", "TypFuse"):
            if sheet_name in template:
                template[sheet_name] = canonicalize_type_sheet_columns(template[sheet_name])
        self.tpl = template
        self.make_graphics = make_graphics
        self.progress = ProgressReporter(progress_callback)

        # Effective shapefile-layer mapping for this run: config.json's
        # defaults, with any per-run overrides from the Layer Mapping
        # dialog applied on top (see core.equipment_catalog). A key
        # explicitly set to None in layer_overrides disables that
        # equipment slot for this run.
        self.layers_cfg = resolve_layer_config(self.cfg.get("layers", {}), layer_overrides)
        self.include_mv = include_mv
        self.include_lv = include_lv

        self.generate_gps = generate_gps
        self.gps_line_vertices = gps_line_vertices and generate_gps
        self.source_epsg = source_epsg
        self._gps_transformer = None
        self.line_points_by_id = {}
        self.line_points_diagram_xy = {}  # populated by emit() if graphics are generated

        # project metadata
        self.project_meta = DEFAULT_PROJECT_META.copy()
        if project_meta:
            self.project_meta.update(project_meta)

        # allow config overrides from project_meta
        if "nominal_mv_kv" in self.project_meta:
            self.cfg["nominal_mv_kv"] = float(self.project_meta["nominal_mv_kv"])
        if "frequency_hz" in self.project_meta:
            self.cfg["grid_frequency_hz"] = float(self.project_meta["frequency_hz"])
        if "loading_factor" in self.project_meta:
            self.cfg.setdefault("load", {})["loading_factor"] = float(self.project_meta["loading_factor"])
        if "power_factor_cos" in self.project_meta:
            self.cfg.setdefault("load", {})["power_factor_cos"] = float(self.project_meta["power_factor_cos"])
        # element_size is the size (in DIgSILENT diagram units) of the
        # transformer/load/etc. symbol boxes. It is a *fixed* value (not
        # scaled by the network's geographic extent -- that made symbols
        # huge on real, large-extent data) and can be overridden by the
        # user via project_meta / config.json.
        if "element_size" in self.project_meta:
            self.cfg.setdefault("graphics", {})["element_size"] = float(self.project_meta["element_size"])
        if "node_spacing" in self.project_meta:
            self.cfg.setdefault("graphics", {})["node_spacing"] = float(self.project_meta["node_spacing"])
        if "terminal_size" in self.project_meta:
            self.cfg.setdefault("graphics", {})["terminal_size"] = float(self.project_meta["terminal_size"])

        # terminal types
        self.terminal_types = DEFAULT_TERMINAL_TYPES.copy()
        if terminal_types:
            self.terminal_types.update(terminal_types)
        logger.info("Terminal types: %s", self.terminal_types)
        logger.info("Project meta: %s", self.project_meta)

        if not os.path.isdir(shp_dir):
            raise NotADirectoryError(f"Input shapefile directory not found: {shp_dir}")

        try:
            self.rnd = self.cfg["round_decimals"]
            self.uknom = self.cfg["nominal_mv_kv"]
        except KeyError as e:
            raise ValueError(f"Invalid config.json - missing key {e}") from e

        self._next_id = 1_000_000
        self.sheets = collections.OrderedDict()
        self.headers = {}
        self._elm = None  # populated by build()

        try:
            self.typlne_id = name_to_id_map(self.tpl["TypLne"])
            self.typtr_id = name_to_id_map(self.tpl["TypTr2"])
            self.typlod_id = name_to_id_map(self.tpl["TypLod"])
            self.typfuse_id = name_to_id_map(self.tpl["TypFuse"])
        except KeyError as e:
            logger.error("Template missing expected key: %s", e)
            raise ValueError(f"Invalid dgs_template.json - missing {e}") from e

        try:
            self.GRID_ID = self.tpl["ElmNet"]["rows"][0][0]
            self.GRFNET_ID = self.tpl["IntGrfnet"]["rows"][0][0]
        except Exception as e:
            logger.error("Template structure invalid: %s", e)
            raise ValueError(f"Invalid dgs_template.json structure: {e}") from e

        self.node_term = {}
        self.terminals = []
        self.cubicles = []
        self.term_xy = {}
        self.term_display_xy = {}
        self.term_uknom = {}
        self.term_kind = {}  # term_id -> terminal_kind string
        self._termcount = 0
        self.lv_kv = float(self.cfg.get("transformer", {}).get("lv_voltage_kv", 0.4))

        # LV-network topology (separate from the MV registries above, since
        # MV and LV coordinates can coincide -- e.g. at a transformer pole --
        # without being the same electrical node). See _build_lv_network().
        self.lv_node_term = {}       # snap_key -> terminal id of a transformer's true LV anchor point
        self.lv_line_node_term = {}  # snap_key -> terminal id of an LV line-junction/device node
        self.lv_adjacency = collections.defaultdict(set)  # terminal id -> directly-connected LV terminal ids
        self._lv_type_cache = {}     # synthesized LV TypLne name -> new type id
        self._lv_type_rows = []      # synthesized LV TypLne rows, appended to the sheet in emit()
        self._lv_type_counter = 0
        self._lv_fuse_type_rows = []  # synthesized LV TypFuse row(s), appended to the sheet in emit()
        self._lv_fuse_type_ids = {}   # rated current (A) -> synthesized LV fuse type id
        # Fuses whose rated current is missing/zero borrow it from the
        # capacity of the nearest MV transformer (see _derive_fuse_current).
        # The index is filled in build() once every transformer has been
        # read, because fuses are read before transformers.
        self._mv_trafo_index = []     # [{"key", "cap", "feeder", "code"}, ...]
        self._fuse_derived_seen = set()      # keeps the report compact (one entry per capacity/rating)
        self._fuse_derived_counts = collections.Counter()  # (capacity, amps) -> number of fuses
        self._fuse_unresolved_count = 0      # fuses with neither a rating nor a usable transformer
        self.lv_cfg = self.cfg.get("lv_line_type", {})
        # Every assumption, default, or skipped/corrupt record encountered
        # during conversion is recorded here so a full discrepancy report
        # can be generated at the end of every run (see build_report()) --
        # not just logged to a file that's easy to miss.
        self.issues = []
        # User-selected types from the prescan-review workflow (see
        # prescan_unresolved() / set_manual_overrides()), consulted before
        # any automatic default/fallback. Structure:
        # {"mv_underground_line": {(section, material): type_name, ...},
        #  "mv_overhead_line": {(section, material): type_name, ...},
        #  "mv_other_lines": {"__all__": type_name},  # one type for this whole category
        #  "transformer": {capacity: type_name, ...}}
        self.manual_overrides = {"mv_underground_line": {}, "mv_overhead_line": {},
                                  "mv_other_lines": {}, "transformer": {}}
        self._trafo_fallback_capacities = collections.Counter()  # cap (kVA) -> count of records that used the fallback type

        self.elem_center = {}
        self.elem_symbol = {}
        # Internal-only classification, richer than elem_symbol/term_kind
        # (which must stay valid DIgSILENT identifiers for the DGS file
        # itself). Used only by the in-app diagram preview to colour LV
        # elements differently from MV ones without writing any
        # non-standard data into the DGS output. Populated alongside
        # elem_symbol wherever an element is created.
        self.elem_kind = {}
        self.term_symbol = {}

        # Graphics layout sizes (fixed, configurable -- see the "graphics"
        # section of config.json / the Graphics tab in the settings
        # dialog). build() only recomputes page_margin from the real data
        # extent; symbol sizes and node_spacing stay fixed so they don't
        # blow up on large networks.
        gcfg = self.cfg.get("graphics", {})
        self.graphics_cfg = gcfg
        self.node_spacing = gcfg.get("node_spacing", 5)
        self.term_size = gcfg.get("terminal_size", 0.2)
        self.busbar_len = gcfg.get("busbar_length", 0.6)
        self.busbar_thick = gcfg.get("busbar_thickness", 0.1)
        # Per product requirement: the transformer's HV-side TermStrip is
        # drawn longer than its LV side, to visually distinguish the two
        # even though both use the identical "TermStrip" symbol name.
        self.hv_busbar_len = self.busbar_len * gcfg.get("hv_busbar_length_multiplier", 2.0)
        self.elem_size = gcfg.get("element_size", 0.5)
        self.page_margin = gcfg.get("page_margin_min", 5.0)

        logger.info("Converter initialized | input=%s | graphics=%s", shp_dir, make_graphics)

    # ------------------------------------------------------------------
    # id / terminal primitives
    # ------------------------------------------------------------------
    def _layer_names(self, key):
        """Resolve one :mod:`~shp2dgs.core.equipment_catalog` key to a
        flat list of shapefile layer names from ``self.layers_cfg``.

        Args:
            key: An equipment catalog key, e.g. ``"mv_overhead_line"``.

        Returns:
            list: Layer name(s) for this slot; empty if unmapped/disabled.
        """
        val = self.layers_cfg.get(key)
        if not val:
            return []
        if isinstance(val, str):
            return [val]
        return list(val)

    def nid(self):
        """Return the next unique DGS object id (monotonically increasing)."""
        v = self._next_id
        self._next_id += 1
        return v

    def new_terminal(self, xy, kind="line_junction", uknom=None, busbar=False, display_xy=None):
        """Create a new ``ElmTerm`` terminal with a user-selectable graphic
        symbol.

        Args:
            xy: ``(x, y)`` coordinate tuple (real-world GIS units). This is
                the terminal's *true* position, used for topology
                snapping and for GPS reprojection.
            kind: One of ``line_junction``, ``device_node``, ``switch``,
                ``fuse``, ``transformer_hv``, ``transformer_lv``, ``load``,
                ``source``, or an ``lv_*`` LV-network equivalent. Selects
                the default symbol via ``self.terminal_types``.
            uknom: Nominal voltage override (kV) for this terminal;
                defaults to the network's ``uknom``.
            busbar: If ``True``, force the ``TermStrip`` symbol regardless
                of ``kind`` (used for HV busbars feeding multiple
                transformers).
            display_xy: Optional ``(x, y)`` override used *only* for the
                ``IntGrf`` diagram layout (e.g. the transformer/load stack
                is drawn offset below its true position so symbols don't
                overlap). When omitted, the diagram uses ``xy`` directly.
                Never affects topology or GPS -- those always use ``xy``.

        Returns:
            int: The new terminal's object id.
        """
        tid = self.nid()
        self._termcount += 1
        self.terminals.append((tid, "point_term%d" % self._termcount, "term%d" % self._termcount))
        self.term_xy[tid] = xy
        if display_xy is not None:
            self.term_display_xy[tid] = display_xy
        if busbar:
            symbol = "TermStrip"
        else:
            symbol = self.terminal_types.get(kind, "PointTerm")
        self.term_symbol[tid] = symbol
        self.term_kind[tid] = kind
        self.term_uknom[tid] = self.uknom if uknom is None else uknom
        return tid

    def base_terminal(self, xy, kind="line_junction"):
        """Return the (creating if necessary) terminal id at ``xy``,
        snapped to ``round_decimals`` so nearby GIS vertices coincide."""
        key = (round(xy[0], self.rnd), round(xy[1], self.rnd))
        if key not in self.node_term:
            self.node_term[key] = self.new_terminal(key, kind=kind)
        return self.node_term[key]

    def base_terminal_lv(self, xy, kind="lv_line_junction"):
        """Return the (creating if necessary) **LV-network** terminal id
        at ``xy``.

        This mirrors :meth:`base_terminal` but looks up a separate LV
        registry, since an LV coordinate can coincide with an MV one
        (e.g. right at a transformer pole) without being the same
        electrical node. If ``xy`` matches a transformer's true LV anchor
        point (registered by :meth:`build` when the transformer was
        created), that terminal is reused so the LV network connects to
        the right transformer's LV busbar.

        Args:
            xy: ``(x, y)`` coordinate tuple (real-world GIS units).
            kind: Terminal kind for a newly-created node (ignored if an
                existing terminal is reused).

        Returns:
            int: The terminal id at ``xy`` on the LV network.
        """
        key = self.snap_key(xy)
        if key in self.lv_node_term:
            return self.lv_node_term[key]
        if key not in self.lv_line_node_term:
            self.lv_line_node_term[key] = self.new_terminal(key, kind=kind, uknom=self.lv_kv)
        return self.lv_line_node_term[key]

    def add_cubicle(self, term_id, elem_id, obj_bus):
        """Register a ``StaCubic`` connecting ``elem_id`` to ``term_id``.

        Args:
            term_id: Terminal object id.
            elem_id: Element object id (line, transformer, load, etc.).
            obj_bus: Terminal-side bus index (0/1) as expected by DIgSILENT.

        Returns:
            int: The new cubicle's object id.
        """
        cid = self.nid()
        self.cubicles.append((cid, term_id, elem_id, obj_bus))
        return cid

    def snap_key(self, xy):
        """Round ``xy`` to ``round_decimals`` for topology snapping."""
        return (round(xy[0], self.rnd), round(xy[1], self.rnd))

    def _split_line_into_segments(self, ln):
        """Split one line record into a list of 2-point segment dicts,
        one per pair of consecutive GIS vertices in ``ln["points"]``.

        This is the mechanism for accurate line-path positioning inside
        PowerFactory itself: previously every line -- even a physically
        curved/bent one -- became a single straight ``ElmLne`` between
        its two endpoints, discarding every intermediate vertex. Since
        the topology-building loop that consumes the ``lines`` list
        already works generically off each entry's own ``a``/``b`` (it
        has no idea whether an entry is "the whole original line" or
        "one piece of it"), and terminal creation already snaps/dedupes
        by rounded coordinate (see :meth:`base_terminal`), pre-splitting
        here is enough to make every consecutive segment naturally chain
        together into the true routed path -- no other code needed to
        change. A useful side effect: a switch/fuse/transformer whose own
        GIS point coincides with what used to be just an internal bend
        (not the line's true start/end) now correctly splices into the
        network instead of being silently stranded.

        Each segment's length is the parent's true total length (from
        the shapefile's own length field, not a straight-line estimate)
        distributed proportionally by each segment's share of the total
        vertex-to-vertex Euclidean distance -- so the sum of all segment
        lengths still exactly equals the original, geometrically-accurate
        total.

        Degenerate segments -- where both endpoints snap to the exact
        same terminal key (see :meth:`snap_key`), typically true
        duplicate vertices from a digitizing double-click -- are dropped
        rather than creating a zero-length ``ElmLne``. This is the only
        criterion that's safe to drop on: since both ends are already
        going to be treated as the identical topology node regardless,
        skipping the hop between them can never disconnect anything.

        An earlier version of this method instead dropped any segment
        under a fixed distance threshold (0.5m) -- that's unsafe in
        general: real GIS data legitimately contains short segments
        (a tight bend right at a branch point, a short jog near a pole)
        well under half a metre, and dropping those genuinely
        disconnects the network rather than just simplifying it. Verified
        against real feeder data where that threshold silently dropped
        19 legitimate 0.03-0.48m segments, each one a real disconnection
        the user had to find and fix by hand.

        Args:
            ln: One entry from the ``lines``/``lv_lines`` list, as built
                while reading line layers in :meth:`build`.

        Returns:
            list[dict]: One or more entries with the same keys as ``ln``
            (``code``, ``feeder``, ``typ``, ``length_km``, ``a``, ``b``,
            ``points``), ready to be used exactly like the original list
            by all downstream code. Always at least one entry -- if the
            line has only 2 (or, after dropping duplicates, effectively
            fewer than 2 distinct) vertices, or vertex data isn't
            available, the original 2-point line is returned unsplit.
        """
        pts = ln.get("points")
        if not pts or len(pts) < 3:
            return [ln]

        seg_dists = [math.dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1)]
        total_dist = sum(seg_dists)
        if total_dist <= 0:
            return [ln]  # degenerate polyline (all points coincide) -- nothing to split

        segments = []
        for i, d in enumerate(seg_dists):
            if self.snap_key(pts[i]) == self.snap_key(pts[i + 1]):
                continue  # true duplicate point once snapped; dropping it cannot disconnect anything
            segments.append(dict(
                code=ln["code"], feeder=ln["feeder"], typ=ln["typ"],
                length_km=ln["length_km"] * (d / total_dist),
                a=pts[i], b=pts[i + 1], points=[pts[i], pts[i + 1]],
            ))
        if len(segments) > 1:
            # Disambiguate in PowerFactory's data browser/diagram -- without
            # this every piece of one split line would show the identical
            # loc_name, which is confusing even though object ids are
            # already unique underneath.
            for i, seg in enumerate(segments, 1):
                seg["code"] = f"{seg['code']}_{i}"
        return segments or [ln]

    # ------------------------------------------------------------------
    # type-name resolution
    # ------------------------------------------------------------------
    def line_type_name(self, rec, layer, dry_run=False):
        """Resolve the ``TypLne`` name for a line record based on
        material/section/network-type keyword matching (see
        ``config["line_type"]``).

        Args:
            rec: Shapefile attribute record (dict).
            layer: Source layer name actually read (compared against the
                *resolved* ``mv_other_lines``/``mv_underground_line``/
                ``mv_jumper``/``mv_abc_cable`` mapping, so manual layer
                mapping overrides are respected, not just the defaults).
            dry_run: If True, don't write to the discrepancy report or
                consult manual overrides -- used by :meth:`prescan_unresolved`
                to check resolvability without side effects.

        Returns:
            str: A ``TypLne`` type name (guaranteed to be a key that
            *should* exist in the template, though callers still fall back
            to ``""`` via ``self.typlne_id.get(...)`` if it does not).
        """
        c = self.cfg["line_type"]
        mat = s(rec.get(c["material_field"]))
        net = s(rec.get(c["nettype_field"]))
        try:
            sec = str(int(float(rec.get(c["section_field"]) or 0)))
        except Exception:
            sec = "0"
        kw = c["keywords"]
        record_id = self._record_id(rec) if not dry_run else ""

        def has(field, keys):
            return any(k in field for k in keys)

        if layer in self._layer_names("mv_other_lines"):
            # This category has no material/section signal to pick a
            # type from -- it's just "one type for all of these", chosen
            # by the user (see prescan_unresolved()'s "mv_other_lines"
            # group) if the configured default isn't in the library.
            if not dry_run:
                override = self.manual_overrides.get("mv_other_lines", {}).get("__all__")
                if override:
                    if override in self.typlne_id:
                        return override
                    self._record_issue(
                        "manual_override_invalid",
                        f"Manual override '{override}' for mv_other_lines is not a TypLne entry in the "
                        f"loaded type library; ignored, used the fallback instead",
                        record_id, loud=True)
            if c["mln_default"] in self.typlne_id:
                return c["mln_default"]
            if not dry_run:
                self._record_issue(
                    "mv_other_lines_no_type",
                    f"config['line_type']['mln_default'] ('{c['mln_default']}') is not in the loaded type "
                    f"library and no type was assigned via Pre-check Types; used the global fallback "
                    f"('{c['global_fallback']}') instead",
                    record_id, loud=True)
            return c["global_fallback"]

        if layer in self._layer_names("mv_underground_line"):
            return self._underground_cable_type_name(rec, mat, sec, dry_run=dry_run)
        if layer in self._layer_names("mv_abc_cable"):
            return c.get("abc_default", c["global_fallback"])
        if layer in self._layer_names("mv_jumper"):
            if has(mat, kw["acsr"]):
                return c["acsr_bare_by_section"].get(sec, c["jup_default"])
            if has(mat, kw.get("aluminum", [])) or has(mat, kw["aaac"]):
                return "ACSR-FOX"
            return c["jup_default"]

        # --- overhead / other MV conductor lines ---
        covered = has(net, kw["covered"]) or has(mat, kw["covered"])
        if covered:
            candidate = c["covered_by_section"].get(sec, c["global_fallback"])
        elif has(mat, kw["copper"]):
            candidate = c["copper_by_section"].get(sec, c["global_fallback"])
        elif has(mat, kw["cable"]):
            candidate = c["cable_default"]
        else:
            candidate = c["acsr_bare_by_section"].get(sec, c["global_fallback"])
        if candidate in self.typlne_id:
            return candidate

        # The naming table (e.g. covered_by_section for an uncommon
        # section) produced a name that isn't actually in this library --
        # try a manually-assigned type (see prescan_unresolved()'s
        # "mv_overhead_line" group, which suggests one based on
        # neighboring lines on the same conductor run) before falling
        # back to a covered-type default (if this was a covered line) or
        # the generic global default.
        if not dry_run:
            override = self.manual_overrides.get("mv_overhead_line", {}).get((sec, mat))
            if override:
                if override in self.typlne_id:
                    return override
                self._record_issue(
                    "manual_override_invalid",
                    f"Manual override '{override}' for section={sec}, material={mat!r} is not a TypLne "
                    f"entry in the loaded type library; ignored, used the fallback instead",
                    record_id, loud=True)
            self._record_issue(
                "mv_overhead_unmatched_type",
                f"Resolved type '{candidate}' (section={sec}, material={mat!r}, covered={covered}) is not "
                f"in the loaded type library; used the fallback instead",
                record_id, loud=True)
        if covered and c.get("covered_default") in self.typlne_id:
            return c["covered_default"]
        return c["global_fallback"]

    def _underground_material_key(self, mat_raw, kw_map):
        """Classify a raw material string into 'aluminum'/'copper'/None
        using ``config["line_type"]["underground_material_keywords"]``."""
        mat_lc = mat_raw.lower()
        for key, words in kw_map.items():
            if any(w.lower() in mat_lc for w in words):
                return key
        return None

    def _safe_uml_default(self, record_id="", dry_run=False):
        """``config["line_type"]["uml_default"]`` if it's actually in the
        loaded type library; otherwise the smallest available type that
        looks like an underground cable (matches the VDE ``...2XSY...``
        naming used by :meth:`_underground_cable_type_name`), so a
        misconfigured default doesn't silently leave ``typ_id`` blank the
        way ``uml_default`` pointing at a nonexistent name used to."""
        c = self.cfg["line_type"]
        if c["uml_default"] in self.typlne_id:
            return c["uml_default"]
        candidates = sorted(n for n in self.typlne_id if "2xsy" in n.lower())
        substitute = candidates[0] if candidates else c["global_fallback"]
        if not dry_run and not getattr(self, "_uml_default_missing_logged", False):
            self._uml_default_missing_logged = True
            self._record_issue(
                "mv_underground_default_missing",
                f"config['line_type']['uml_default'] ('{c['uml_default']}') is not in the loaded type "
                f"library; used '{substitute}' for this and every other affected record instead",
                loud=True)
        return substitute

    def _underground_cable_type_name(self, rec, mat_raw, sec, dry_run=False, strict=False):
        """Resolve the ``TypLne`` name for an underground MV cable using
        the VDE ``{prefix}2XSY 1x{section}/{screen} ({voltage_class})``
        convention (NA = aluminum conductor, N = copper conductor, 2X =
        XLPE insulation) -- e.g. ``"NA2XSY 1x95/16 (12/20kV)"`` for a
        95 mm2 aluminum XLPE cable.

        Falls back to ``uml_default`` (recording why, in the discrepancy
        report) when the material is unrecognised, the section is
        invalid/placeholder (``underground_invalid_sections``, e.g. ``0``
        or ``9999`` -- common GIS data-entry placeholders), or no matching
        ``TypLne`` entry exists for the resolved name. A manual override
        keyed by ``(section, material)`` -- see :meth:`prescan_unresolved`
        -- takes priority over all of this when present.

        Args:
            strict: If True, return ``None`` instead of substituting
                ``_safe_uml_default()`` at either fallback point -- used
                by :meth:`prescan_unresolved` to detect "this record
                needed a generic substitute" even though that substitute
                is itself a valid, real type (and so wouldn't otherwise
                be distinguishable from a genuine section/material
                match). Implies ``dry_run=True`` behaviour (no side
                effects) regardless of ``dry_run``.
        """
        c = self.cfg["line_type"]
        record_id = self._record_id(rec) if not (dry_run or strict) else ""

        if not (dry_run or strict):
            override = self.manual_overrides.get("mv_underground_line", {}).get((sec, mat_raw))
            if override:
                if override in self.typlne_id:
                    return override
                self._record_issue(
                    "manual_override_invalid",
                    f"Manual override '{override}' for section={sec}, material={mat_raw!r} is not a "
                    f"TypLne entry in the loaded type library; ignored, used the default instead",
                    record_id, loud=True)

        invalid_sections = set(c.get("underground_invalid_sections", ["0"]))
        if sec in invalid_sections or sec == "0":
            if strict:
                return None
            if not dry_run:
                self._record_issue(
                    "mv_underground_invalid_section",
                    f"Section field '{c['section_field']}'={sec!r} is missing/placeholder; "
                    f"used the default type ('{c['uml_default']}') instead of guessing",
                    record_id, loud=True)
            return self._safe_uml_default(record_id, dry_run)

        material = self._underground_material_key(mat_raw, c.get("underground_material_keywords", {}))
        if material is None:
            material = c.get("underground_material_default", "aluminum")
            if not (dry_run or strict):
                self._record_issue(
                    "mv_underground_material_default",
                    f"Material field '{c['material_field']}'={mat_raw!r} not recognised as "
                    f"aluminum/copper; assumed default ({material})",
                    record_id)

        prefix = c.get("underground_prefix_by_material", {}).get(material, "NA")
        pattern = c.get("underground_name_pattern", "{prefix}2XSY 1x{cap}/{screen} ({voltage_class})")
        name = pattern.format(prefix=prefix, cap=sec,
                               screen=c.get("underground_screen_section", "16"),
                               voltage_class=c.get("underground_voltage_class", "12/20kV"))
        if name in self.typlne_id:
            return name
        if strict:
            return None
        if not dry_run:
            self._record_issue(
                "mv_underground_unmatched_section",
                f"No TypLne entry named '{name}' in the type library; used the default "
                f"type ('{c['uml_default']}') instead",
                record_id, loud=True)
        return self._safe_uml_default(record_id, dry_run)



    def _record_issue(self, category, message, record_id="", loud=False):
        """Record one entry in the end-of-conversion discrepancy report.

        Args:
            category: Short machine-readable bucket, e.g.
                ``"transformer_insulation_default"`` -- used to group and
                count entries in the report summary.
            message: Human-readable explanation.
            record_id: The source record's identifier (``config["id_field"]``
                value) if available, so the entry can be traced back to the
                GIS data. Left blank for conversion-wide (non-per-record)
                issues.
            loud: If True, also emit a ``logger.warning`` (for rarer,
                higher-severity issues). High-frequency per-record
                assumptions are logged at DEBUG instead, to avoid flooding
                the log file -- they still always land in the report.
        """
        self.issues.append({"category": category, "id": record_id, "message": message})
        if loud:
            logger.warning("[%s] %s%s", category, message, f" (id={record_id})" if record_id else "")
        else:
            logger.debug("[%s] %s%s", category, message, f" (id={record_id})" if record_id else "")

    def _record_id(self, rec):
        """Best-effort identifier for a raw shapefile record, using
        ``config["id_field"]``, for discrepancy-report traceability."""
        idf = self.cfg.get("id_field")
        return s(rec.get(idf)) if idf else ""

    def feeder_name(self, rec):
        """Resolve a record's feeder name with one consistent precedence
        everywhere it's needed (topology grouping, ``ElmXxx.chr_name``,
        ``ElmFeeder``/``ElmXnet.loc_name``):
        ``config["feeder_field"]`` (default ``"Feeder"``) takes priority;
        ``config["feeder_field_fallback"]`` (default ``"T_FeederNa"``) is
        used next if that's empty; ``config["feeder_field_fallback2"]``
        (default ``"T_feader"`` -- note the schema's own typo, missing an
        'e') is tried next -- needed because the feeder-start-switch/
        source layer has been seen carrying neither of the first two
        fields at all, only this one, and its value already matches the
        same ``"F<n> - <DIRECTION>"`` convention used everywhere else
        (unlike ``feeder_field_fallback3``, whose value is just a bare,
        non-unique direction word shared by several different feeders).
        ``config["feeder_field_fallback3"]`` (default ``"T_name_fdr"``)
        is the last resort. ``""`` if all four are empty.

        Every equipment element's ``chr_name`` is set to this value, and
        it's also the ``loc_name`` for ``ElmFeeder``/``ElmXnet`` (see
        ``emit()``) -- an empty result there isn't just a blank label:
        PowerFactory shows an unnamed object as an auto-numbered
        placeholder like ``(30)``, so getting this fallback chain right
        matters beyond cosmetics.
        """
        c = self.cfg
        fields = [c.get("feeder_field", "Feeder"), c.get("feeder_field_fallback", "T_FeederNa"),
                  c.get("feeder_field_fallback2", "T_feader"), c.get("feeder_field_fallback3", "T_name_fdr")]
        for f in fields:
            if f:
                v = s(rec.get(f))
                if v:
                    return v
        return ""

    _TYPTR_SUFFIX_RE = re.compile(r"-(o|ols|d|h)\s*$", re.IGNORECASE)

    def _field_scan_text(self, rec, fields):
        """Concatenate the given record fields (lowercased) for keyword scanning."""
        return " ".join(str(rec.get(f)) for f in fields if rec.get(f)).lower()

    def transformer_series(self, rec, dry_run=False):
        """Determine the loss-class ('series') token for a transformer
        record -- ``"AB'"`` (low-loss) or ``"Normal"`` -- by scanning
        ``config["transformer"]["loss_class_scan_fields"]`` for the
        configured keyword lists. Per product requirement, low-loss is
        also the default when nothing is mentioned -- that default is
        recorded as a discrepancy-report entry since it's an assumption,
        not a fact read from the data."""
        t = self.cfg["transformer"]
        text = self._field_scan_text(rec, t.get("loss_class_scan_fields", []))
        low_kw = [k.lower() for k in t.get("loss_class_low_loss_keywords", [])]
        high_kw = [k.lower() for k in t.get("loss_class_high_loss_keywords", [])]
        if any(k in text for k in low_kw):
            cls = "low_loss"
        elif any(k in text for k in high_kw):
            cls = "normal"
        else:
            cls = t.get("loss_class_default", "low_loss")
            if not dry_run:
                self._record_issue(
                    "transformer_series_default",
                    f"No loss-class keyword found in {t.get('loss_class_scan_fields', [])}; "
                    f"assumed default ({cls})",
                    self._record_id(rec))
        return t.get("series_names", {}).get(cls, "AB'")

    def transformer_insulation(self, rec, dry_run=False):
        """Determine the insulation token (``"O"`` oil / ``"D"`` dry) for a
        transformer record from ``config["transformer"]["insulation_field"]``,
        defaulting to ``insulation_default`` when empty/unrecognised (recorded
        as a discrepancy-report entry, since it's an assumption)."""
        t = self.cfg["transformer"]
        field = t.get("insulation_field")
        text = s(rec.get(field)).lower() if field else ""
        dry_kw = [k.lower() for k in t.get("insulation_dry_keywords", [])]
        oil_kw = [k.lower() for k in t.get("insulation_oil_keywords", [])]
        if any(k in text for k in dry_kw):
            return "D"
        if any(k in text for k in oil_kw):
            return "O"
        default = t.get("insulation_default", "O")
        if not dry_run:
            self._record_issue(
                "transformer_insulation_default",
                f"Field '{field}' empty/unrecognised (value={rec.get(field)!r}); assumed default ({default})",
                self._record_id(rec))
        return default

    def _typtr_candidates_by_capacity(self):
        """Lazily build (and cache) ``{capacity_kVA: [names...]}`` by
        scanning ``self.typtr_id`` keys for an embedded ``"<number>kVA"``
        (case-insensitive, optional space). Used as a fallback for type
        libraries whose ``loc_name`` convention doesn't match
        ``config["transformer"]["name_patterns"]``/``type_pattern``
        exactly."""
        cache = getattr(self, "_typtr_capacity_candidates", None)
        if cache is not None:
            return cache
        cache = collections.defaultdict(list)
        for name in self.typtr_id:
            m = re.search(r"(\d+(?:\.\d+)?)\s*kva", name, re.IGNORECASE)
            if m:
                cache[str(int(float(m.group(1))))].append(name)
        self._typtr_capacity_candidates = cache
        return cache

    def _resolve_by_capacity(self, cap, series, insulation, record_id="", dry_run=False):
        """Among TypTr2 names embedding this capacity, pick the one that
        also matches the detected series/insulation -- but only when that
        match is unambiguous. Guessing between equally-plausible
        candidates could silently assign the wrong transformer model,
        which is worse than the visible fallback type, so ties or
        all-zero scores return ``None`` (and record the candidates that
        were considered in the discrepancy report) rather than picking
        arbitrarily."""
        candidates = self._typtr_candidates_by_capacity().get(cap, [])
        if not candidates:
            return None
        if len(candidates) == 1:
            return candidates[0]
        series_lc = series.lower()
        best_score, best_names = None, []
        for name in candidates:
            name_lc = name.lower()
            score = 2 if series_lc in name_lc else 0
            m = self._TYPTR_SUFFIX_RE.search(name.strip())
            if m:
                suf = m.group(1).lower()
                if suf == "d":
                    score += 1 if insulation == "D" else -1
                elif suf in ("o", "ols"):
                    score += 1 if insulation == "O" else -1
                # suf == "h": unrecognised legacy suffix, no signal
            else:
                # No O/D suffix in the name (e.g. the "TRF-..." series) --
                # treated as the implicit default (oil).
                score += 1 if insulation == "O" else 0
            if best_score is None or score > best_score:
                best_score, best_names = score, [name]
            elif score == best_score:
                best_names.append(name)
        if best_score is not None and best_score > 0 and len(best_names) == 1:
            return best_names[0]
        if not dry_run:
            self._record_issue(
                "transformer_ambiguous_type",
                f"{cap} kVA matches multiple TypTr2 entries ({candidates}) and none is an "
                f"unambiguous match for series={series} insulation={insulation}; used the fallback type",
                record_id, loud=True)
        return None

    def trafo_type_name(self, rec, dry_run=False):
        """Resolve the ``TypTr2`` name for a transformer record.

        Resolution order:
          0. A manual override for this exact capacity, if the user chose
             one via the prescan-review workflow (see
             :meth:`prescan_unresolved` / :meth:`set_manual_overrides`).
          1. Config-driven candidate patterns for the record's detected
             insulation (O/D, from :meth:`transformer_insulation`) and
             loss-class series (AB'/Normal, from :meth:`transformer_series`)
             -- ``config["transformer"]["name_patterns"]``.
          2. The legacy ``config["transformer"]["type_pattern"]``.
          3. An unambiguous capacity-only match, preferring a candidate
             that also matches the detected series/insulation when more
             than one type shares the capacity -- see
             :meth:`_resolve_by_capacity`.
          4. ``fallback_type``.

        Args:
            dry_run: If True, don't write to the discrepancy report or
                consult manual overrides -- used by
                :meth:`prescan_unresolved` to check resolvability without
                side effects.
        """
        t = self.cfg["transformer"]
        record_id = self._record_id(rec) if not dry_run else ""
        cap_raw = s(rec.get(t["capacity_field"]))
        try:
            cap = str(int(float(cap_raw)))
        except Exception:
            cap = None

        if not dry_run and cap is not None:
            override = self.manual_overrides.get("transformer", {}).get(cap)
            if override:
                if override in self.typtr_id:
                    return override
                self._record_issue(
                    "manual_override_invalid",
                    f"Manual override '{override}' for capacity={cap} kVA is not a TypTr2 entry in "
                    f"the loaded type library; ignored, used the default instead",
                    record_id, loud=True)

        if cap is None:
            if not dry_run:
                self._record_issue(
                    "transformer_invalid_capacity",
                    f"Capacity field '{t['capacity_field']}' is missing/non-numeric (value={cap_raw!r}); "
                    f"used the fallback type",
                    record_id, loud=True)
            return t["fallback_type"]
        if cap == "0":
            if not dry_run:
                self._record_issue(
                    "transformer_invalid_capacity",
                    f"Capacity field '{t['capacity_field']}' is 0; used the fallback type",
                    record_id, loud=True)
            return t["fallback_type"]

        series = self.transformer_series(rec, dry_run=dry_run)
        insulation = self.transformer_insulation(rec, dry_run=dry_run)

        for pattern in t.get("name_patterns", {}).get(insulation, []):
            name = pattern.format(cap=cap, series=series)
            if name in self.typtr_id:
                return name

        legacy_name = t["type_pattern"].format(cap=cap)
        if legacy_name in self.typtr_id:
            return legacy_name

        resolved = self._resolve_by_capacity(cap, series, insulation, record_id, dry_run=dry_run)
        if resolved:
            return resolved

        if not dry_run:
            self._trafo_fallback_capacities[cap] += 1
            self._record_issue(
                "transformer_unknown_type",
                f"No TypTr2 entry found for {cap} kVA (series={series}, insulation={insulation}); "
                f"used the fallback type ('{t['fallback_type']}')",
                record_id, loud=True)
        return t["fallback_type"]

    def lv_line_type_name(self, rec, layer):
        """Resolve (synthesizing on first use) the ``TypLne`` name for an
        LV line/cable record.

        Unlike MV line types (which come pre-defined from
        ``dgs_template.json``), LV types do not exist in the base
        template, so this method builds a small set of ``TypLne`` rows on
        demand -- one per distinct (material, section, installation)
        combination actually encountered in the input data -- using the
        physics-based/typical defaults in ``config["lv_line_type"]`` (see
        that section's ``_comment`` for the accuracy caveat). Rows are
        collected in ``self._lv_type_rows`` and appended to the ``TypLne``
        sheet by :meth:`emit`; the synthesized name is also registered in
        ``self.typlne_id`` so the normal MV-style ``typlne_id.get(name)``
        lookup in :meth:`emit` resolves it transparently.

        Args:
            rec: Shapefile attribute record (dict).
            layer: Source layer name (one of the configured
                ``layers.lv_lines`` entries, e.g. ``"ELWOLL"``/``"ELWULL"``/
                ``"ELWLLN"``/``"ELWLJP"``).

        Returns:
            str: A ``TypLne`` type name (always resolvable via
            ``self.typlne_id`` -- never empty).
        """
        c = self.lv_cfg
        if not c:
            # No lv_line_type config present (e.g. an older config.json) --
            # fall back to the plain MV default so the conversion still
            # completes rather than crashing.
            return self.cfg.get("line_type", {}).get("global_fallback", "")

        kw = c.get("keywords", {})
        material_raw = s(rec.get(c.get("material_field", "T_jns_hadi")))

        def has(field, keys):
            return any(k in field for k in keys)

        material = "copper" if has(material_raw, kw.get("copper", [])) else (
            "aluminum" if has(material_raw, kw.get("aluminum", [])) else "default")

        is_underground = layer in self._layer_names("lv_underground_line")
        is_forced_abc = layer in self._layer_names("lv_abc_cable")
        if is_forced_abc:
            install = "abc"
        elif is_underground:
            arrangement = s(rec.get(c.get("underground_arrangement_field", "T_noe_arys")))
            install = "busbar" if has(arrangement, kw.get("busbar", [])) else "underground"
        else:
            install_raw = s(rec.get(c.get("overhead_install_field", "T_typ_shab")))
            if has(install_raw, kw.get("abc", [])):
                install = "abc"
            elif has(install_raw, kw.get("covered", [])):
                install = "covered"
            elif has(install_raw, kw.get("bare", [])):
                install = "bare"
            else:
                install = "overhead"

        try:
            section = int(float(rec.get(c.get("section_field", "T_cod_sath")) or 0))
        except Exception:
            section = 0
        if section <= 0:
            section = int(c.get("default_section_mm2", 16))

        type_name = f"LV-{install.upper()}-{material.upper()}-{section}"
        if type_name in self._lv_type_cache:
            return type_name
        self._create_lv_type(type_name, material, section, install)
        return type_name

    # ------------------------------------------------------------------
    # fuse types: rated current read from the source data
    # ------------------------------------------------------------------
    #: Persian/Arabic-Indic digits, so a nominal-current field exported from
    #: a Persian-language GIS ("۶۳") parses exactly like "63".
    _DIGIT_TABLE = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")

    @staticmethod
    def _format_current(value):
        """Render a rated current for use inside a type name: ``63`` for
        whole amps (not ``63.0``), one decimal otherwise."""
        try:
            f = float(value)
        except (TypeError, ValueError):
            return str(value)
        return str(int(round(f))) if abs(f - round(f)) < 1e-9 else f"{f:g}"

    def _type_sheet_rows(self, sheet_name):
        """Return a type-library sheet's rows as ``[{column: value}, ...]``.

        Columns are resolved **by header name** (via
        :func:`~shp2dgs.core.config_io.header_col_index`), never by a
        fixed position, because an Excel-imported type library legitimately
        carries its columns in a different order from the bundled
        ``dgs_template.json`` (see ``io_formats.excel_types``).

        Args:
            sheet_name: e.g. ``"TypFuse"``.

        Returns:
            list[dict]: One dict per row; missing trailing cells are
            ``None``. Empty if the sheet does not exist.
        """
        sheet = self.tpl.get(sheet_name) or {}
        header = sheet.get("header", [])
        bare = [h.split("(")[0] for h in header]
        rows = []
        for row in sheet.get("rows", []):
            padded = list(row) + [None] * (len(bare) - len(row))
            rows.append(dict(zip(bare, padded)))
        return rows

    def _fuse_current_a(self, rec):
        """Read a fuse record's nominal current (A) from the source data.

        The field name comes from ``config["fuse"]["current_field"]``
        (default ``"T_jar_nami"``). Parsing tolerates the shapes real GIS
        exports produce: plain numbers, ``"63"``/``"63.0"``, decorated
        text such as ``"63 A"`` or ``"فیوز ۶۳"``, and Persian/Arabic-Indic
        digits.

        Args:
            rec: Shapefile attribute record (dict).

        Returns:
            tuple: ``(current_or_None, raw_value)`` -- ``current`` is a
            positive float when a usable rating was found, else ``None``
            (so callers can distinguish "no field", "empty" and "0").
        """
        fc = self.cfg.get("fuse", {}) or {}
        field = fc.get("current_field", "T_jar_nami")
        if not field:
            return None, None
        raw = rec.get(field)
        if raw is None:
            return None, raw
        text = str(raw).translate(self._DIGIT_TABLE).replace(",", ".")
        m = re.search(r"\d+(?:\.\d+)?", text)
        if not m:
            return None, raw
        try:
            value = float(m.group(0))
        except ValueError:  # pragma: no cover - regex guarantees a float
            return None, raw
        return (value if value > 0 else None), raw

    def _record_issue_once(self, key, category, message, record_id="", loud=False):
        """Record a discrepancy-report entry only the first time ``key`` is
        seen.

        The per-record report is the right default for rare problems, but a
        fuse rating derived from a nearby transformer can legitimately apply
        to thousands of fuses (an export where ``T_jar_nami`` is blank
        everywhere). One aggregated entry per distinct cause keeps the
        report readable while still being traceable (it names an example
        record id); the total count is logged separately.

        Args:
            key: Hashable identity of the cause (e.g. ``("derived", 100, 8)``).
            category: Report bucket, e.g. ``"fuse_current_derived"``.
            message: Human-readable explanation.
            record_id: Example source record id.
            loud: Also emit a warning-level log line.
        """
        if key in self._fuse_derived_seen:
            return
        self._fuse_derived_seen.add(key)
        self._record_issue(category, message, record_id, loud=loud)

    def _capacity_to_fuse_current(self, capacity_kva):
        """Look up the fuse rating implied by a transformer's capacity.

        Uses ``config["fuse"]["capacity_current_table"]`` -- the utility's
        own distribution-transformer/fuse coordination table, e.g.
        ``{"25": 3, "50": 6, "100": 8, ...}``, mapping kVA to amps.

        A capacity that is not a table entry (e.g. a 63 kVA unit) uses the
        closest capacity in the table, since a fuse must still be chosen.

        Args:
            capacity_kva: The transformer's rated capacity in kVA.

        Returns:
            tuple: ``(amps_or_None, table_capacity_key_or_None)`` -- the
            second element tells the caller whether the lookup was exact.
        """
        fc = self.cfg.get("fuse", {}) or {}
        table = fc.get("capacity_current_table") or {}
        try:
            cap = float(capacity_kva)
        except (TypeError, ValueError):
            return None, None
        if cap <= 0 or not table:
            return None, None
        parsed = {}
        for k, v in table.items():
            try:
                parsed[str(int(float(k)))] = float(v)
            except (TypeError, ValueError):
                continue
        if not parsed:
            return None, None
        cap_key = str(int(round(cap)))
        if cap_key in parsed:
            return parsed[cap_key], cap_key
        nearest = min(parsed, key=lambda k: (abs(float(k) - cap), float(k)))
        return parsed[nearest], nearest

    def _nearest_transformer(self, xy, feeder=""):
        """Find the MV transformer nearest to ``xy`` (a fuse's position).

        A distribution fuse normally sits on its transformer's MV busbar, so
        an exact coordinate match is checked first (the common case: both
        symbols are digitised on the same pole). Otherwise the nearest
        transformer **on the same feeder** wins, then the nearest one
        overall -- but only within ``config["fuse"]["capacity_max_distance_m"]``
        (0 disables the limit), so a fuse far from any transformer is not
        given an unrelated rating.

        Args:
            xy: ``(x, y)`` of the fuse, in the shapefiles' own GIS units.
            feeder: The fuse's feeder name, used to prefer same-feeder
                transformers.

        Returns:
            tuple: ``(trafo_dict_or_None, distance_or_None)``.
        """
        trafos = getattr(self, "_mv_trafo_index", None) or []
        if not trafos or xy is None:
            return None, None
        key = self.snap_key(xy)
        for trafo in trafos:
            if trafo["key"] == key:
                return trafo, 0.0

        def dist(trafo):
            return math.dist(trafo["key"], key)

        same_feeder = [t for t in trafos if feeder and t.get("feeder") == feeder]
        best = min(same_feeder or trafos, key=dist)
        d = dist(best)
        try:
            max_d = float((self.cfg.get("fuse", {}) or {}).get("capacity_max_distance_m", 0) or 0)
        except (TypeError, ValueError):
            max_d = 0.0
        if max_d > 0 and d > max_d:
            return None, d
        return best, d

    def _derive_fuse_current(self, xy, feeder="", record_id="", dry_run=False):
        """Derive a fuse's rated current from the nearest transformer's
        capacity (see :meth:`_capacity_to_fuse_current`).

        Args:
            xy: The fuse's ``(x, y)`` position.
            feeder: The fuse's feeder name.
            record_id: Source record id, for the discrepancy report.
            dry_run: If True, don't write to the report or count anything.

        Returns:
            tuple: ``(amps_or_None, description_or_None)`` where the
            description reads like ``"100 kVA transformer -> 8 A"``.
        """
        trafo, distance = self._nearest_transformer(xy, feeder)
        if trafo is None:
            return None, None
        amps, table_cap = self._capacity_to_fuse_current(trafo.get("cap"))
        if amps is None:
            return None, None
        cap_label = self._format_current(trafo.get("cap"))
        note = (f"{cap_label} kVA transformer -> {self._format_current(amps)} A"
                + ("" if table_cap is not None and table_cap == self._format_current(trafo.get("cap"))
                   else f" (nearest table entry {table_cap} kVA)"))
        if not dry_run:
            self._fuse_derived_counts[(self._format_current(trafo.get("cap")),
                                       self._format_current(amps))] += 1
            where = "same point as" if distance == 0 else f"{distance:.1f} m from"
            self._record_issue_once(
                ("derived", self._format_current(trafo.get("cap")), self._format_current(amps)),
                "fuse_current_derived",
                f"Fuse rated current missing/zero -> derived from the nearest MV transformer "
                f"({where} transformer {trafo.get('code') or '(no id)'}, {note}); "
                f"applies to every fuse with the same attribute/transform situation. "
                f"Adjust config['fuse']['capacity_current_table'] if this coordination table is not yours",
                record_id)
        return amps, note

    def _fuse_candidates(self, urat_kv):
        """Return the fuse types usable at ``urat_kv``, as
        ``[(irat_a, loc_name), ...]`` sorted by rated current.

        Voltage classes are kept strictly separate (a 0.4 kV LV fuse is
        never matched against a 20 kV MV cutout type), using a relative
        tolerance so a nominal 20 kV library still matches a network
        configured at 20.0 kV but never matches 0.4 kV.

        Args:
            urat_kv: The nominal voltage whose fuse types are wanted
                (``self.uknom`` for MV, ``self.lv_kv`` for LV).

        Returns:
            list[tuple]: Empty if the library has no type for that
            voltage class (e.g. the bundled library ships MV-only
            ``TypFuse`` rows, so LV fuses are synthesized instead).
        """
        target = float(urat_kv)
        out = []
        for row in self._type_sheet_rows("TypFuse"):
            name = s(row.get("loc_name"))
            irat, urat = row.get("irat"), row.get("urat")
            if not name or irat is None or urat is None:
                continue
            try:
                irat, urat = float(irat), float(urat)
            except (TypeError, ValueError):
                continue
            if abs(urat - target) > max(0.25 * target, 0.05):
                continue
            out.append((irat, name))
        out.sort(key=lambda c: (c[0], c[1]))
        return out

    @staticmethod
    def _snap_fuse_candidate(candidates, current, prefer="lower"):
        """Pick the best available fuse type when the data's rated
        current is not an exact entry in the type library.

        Args:
            candidates: ``[(irat_a, name), ...]`` from
                :meth:`_fuse_candidates`.
            current: The data's nominal current (A).
            prefer: ``"lower"`` (default -- never over-rate a device),
                ``"higher"``, or ``"nearest"``.

        Returns:
            str: The chosen type's ``loc_name``.
        """
        lower = [c for c in candidates if c[0] <= current + 1e-9]
        higher = [c for c in candidates if c[0] >= current - 1e-9]
        if prefer == "nearest":
            return min(candidates, key=lambda c: (abs(c[0] - current), c[0]))[1]
        if prefer == "higher":
            return (min(higher, key=lambda c: c[0]) if higher
                    else max(candidates, key=lambda c: c[0]))[1]
        # default: "lower" -- a fuse rated above the device's rating would
        # silently under-protect the network, so err downwards.
        return (max(lower, key=lambda c: c[0]) if lower
                else min(candidates, key=lambda c: c[0]))[1]

    def fuse_type_name(self, rec, level="mv", dry_run=False, xy=None):
        """Resolve the ``TypFuse`` name for one fuse record.

        Resolution order:

        0a. If the record's rated current is missing/zero (and ``xy`` is
            given, ``level == "mv"``, and
            ``config["fuse"]["derive_from_transformer"]`` is on), the rating
            is derived from the capacity of the nearest MV transformer via
            ``config["fuse"]["capacity_current_table"]`` -- e.g. a 100 kVA
            unit implies an 8 A fuse. The result is reported as
            ``fuse_current_derived`` (one aggregated entry per capacity).
        0b. ``config["fuse"]["type_name_pattern"]`` (default
           ``"Fuse_Type_{current}"``) formatted with the record's rated
           current -- matches the naming used by the bundled Excel type
           library.
        1. An exact ``irat`` match among the library's fuse types valid at
           this voltage class.
        2. The nearest available rating (see :meth:`_snap_fuse_candidate`
           and ``config["fuse"]["snap_prefer"]``), recorded in the
           discrepancy report because it is an assumption.
        3. ``config["fuse"]["type_name"]`` -- the configured fallback for
           records with no/unparsable rated current.
        4. The lowest-rated type of this voltage class, then any fuse type
           at all (so a lookup never silently yields an untyped fuse).

        Args:
            rec: Shapefile attribute record (dict).
            level: ``"mv"`` or ``"lv"`` -- selects the voltage class.
            dry_run: If True, don't write to the discrepancy report (used
                where a resolution is only exploratory).
            xy: The fuse's ``(x, y)`` position, used only when the rated
                current has to be derived from a nearby transformer.

        Returns:
            str: A ``TypFuse`` ``loc_name``; ``""`` only if the library has
            no fuse types at all.
        """
        fc = self.cfg.get("fuse", {}) or {}
        record_id = self._record_id(rec) if not dry_run else ""
        current, raw = self._fuse_current_a(rec)
        field = fc.get("current_field", "T_jar_nami")
        target_u = self.uknom if level == "mv" else self.lv_kv
        try:
            tol = abs(float(fc.get("match_tolerance_a", 0.0) or 0.0))
        except (TypeError, ValueError):
            tol = 0.0
        candidates = self._fuse_candidates(target_u)

        # Missing/zero rated current: borrow it from the nearest transformer's
        # capacity (MV only -- LV fuses are handled by _get_lv_fuse_type_id).
        if (current is None and level == "mv" and xy is not None
                and fc.get("derive_from_transformer", True)):
            derived, _note = self._derive_fuse_current(
                xy, self.feeder_name(rec), record_id, dry_run=dry_run)
            if derived is not None:
                current = derived

        if current is not None:
            pattern = fc.get("type_name_pattern")
            if pattern:
                try:
                    name = pattern.format(current=self._format_current(current),
                                          current_a=self._format_current(current),
                                          irat=self._format_current(current))
                except (KeyError, IndexError, ValueError):
                    name = None
                if name and name in self.typfuse_id:
                    return name
            for irat, cand_name in candidates:
                if abs(irat - current) <= tol + 1e-9:
                    return cand_name
            if candidates and fc.get("snap_to_nearest", True):
                chosen = self._snap_fuse_candidate(candidates, current,
                                                   fc.get("snap_prefer", "lower"))
                if not dry_run:
                    self._record_issue(
                        "fuse_rating_snapped",
                        f"Rated current {self._format_current(current)} A from '{field}' has no "
                        f"matching TypFuse entry at {target_u:g} kV; used '{chosen}' "
                        f"(available: {[self._format_current(c[0]) for c in candidates]})",
                        record_id, loud=True)
                return chosen

        default = fc.get("type_name") or ""
        if not dry_run:
            self._fuse_unresolved_count += 1
        if default and default in self.typfuse_id:
            if not dry_run:
                self._record_issue_once(
                    ("missing", default),
                    "fuse_current_missing",
                    f"No usable rated current in field '{field}' (value={raw!r}) and no MV "
                    f"transformer close enough to derive it from; used the configured fallback "
                    f"fuse type ('{default}'). One report entry covers every such fuse",
                    record_id)
            return default
        if candidates:
            if not dry_run:
                self._record_issue_once(
                    ("missing_lowest", candidates[0][1]),
                    "fuse_current_missing",
                    f"No usable rated current in field '{field}' (value={raw!r}) and no MV "
                    f"transformer close enough to derive it from; used the lowest-rated "
                    f"available fuse type ('{candidates[0][1]}')",
                    record_id)
            return candidates[0][1]
        all_names = sorted(self.typfuse_id)
        if not dry_run and all_names:
            self._record_issue(
                "fuse_type_library_empty",
                f"No TypFuse entry is valid at {target_u:g} kV; used '{all_names[0]}'",
                record_id, loud=True)
        return all_names[0] if all_names else ""

    def fuse_type_id(self, rec, level="mv", dry_run=False, xy=None):
        """Return the ``TypFuse`` object id for one fuse record.

        Thin wrapper around :meth:`fuse_type_name` that also handles the LV
        case: the bundled type library ships only 20 kV ``TypFuse`` rows, so
        an LV fuse whose rating can't be matched against an existing 0.4 kV
        entry gets a synthesized LV type of exactly that rating
        (see :meth:`_get_lv_fuse_type_id`).

        Args:
            rec: Shapefile attribute record (dict).
            level: ``"mv"`` or ``"lv"``.
            dry_run: If True, don't create types or write report entries.
            xy: The fuse's ``(x, y)`` position -- required for deriving a
                missing MV rating from the nearest transformer.

        Returns:
            int or str: The type id, or ``""`` if none could be resolved.
        """
        current, _raw = self._fuse_current_a(rec)
        if level == "lv" and not self._fuse_candidates(self.lv_kv):
            return "" if dry_run else self._get_lv_fuse_type_id(current)
        name = self.fuse_type_name(rec, level=level, dry_run=dry_run, xy=xy)
        tid = self.typfuse_id.get(name)
        if tid is None and level == "lv" and not dry_run:
            return self._get_lv_fuse_type_id(current)
        return "" if tid is None else tid

    def _get_lv_fuse_type_id(self, current_a=None):
        """Return the (creating on first use) ``TypFuse`` id for an LV
        fuse, rated at LV voltage instead of reusing the MV fuse type.

        Unlike the MV library (whose types come from
        ``types_override.xlsx``), the bundled template has no LV-rated
        ``TypFuse`` rows, so one type is synthesized per distinct rated
        current actually found in the data -- named from
        ``config["fuse"]["lv_type_name_pattern"]`` (default
        ``"LV_Fuse_{current}A"``). When the data carries no rating at all,
        the single configurable default type
        (``lv_type_name``/``lv_irat_a``) is used instead, exactly as
        before.

        Args:
            current_a: The fuse's nominal current in amps, or ``None`` if
                unknown (falls back to ``config["fuse"]["lv_irat_a"]``).

        Returns:
            int: The LV fuse type's object id.
        """
        fc = self.cfg.get("fuse", {}) or {}
        base = fc.get("lv_type_name", "LV_Default_Fuse")
        lv_urat = float(fc.get("lv_urat_kv", self.lv_kv))
        lv_frq = fc.get("lv_frq_hz", 50)
        if current_a is None:
            irat = float(fc.get("lv_irat_a", 100))
            name = base
        else:
            irat = float(current_a)
            pattern = fc.get("lv_type_name_pattern") or "LV_Fuse_{current}A"
            try:
                name = pattern.format(base=base, current=self._format_current(irat),
                                      current_a=self._format_current(irat))
            except (KeyError, IndexError, ValueError):
                name = f"LV_Fuse_{self._format_current(irat)}A"

        key = (round(irat, 3), name)
        if key in self._lv_fuse_type_ids:
            return self._lv_fuse_type_ids[key]
        if name in self.typfuse_id:  # already present in the loaded library
            self._lv_fuse_type_ids[key] = self.typfuse_id[name]
            return self.typfuse_id[name]

        tid = self.nid()
        fold_id = self.tpl["TypFuse"]["rows"][0][2] if self.tpl["TypFuse"]["rows"] else ""
        row = [tid, name, fold_id, lv_urat, irat, lv_frq]
        self._lv_fuse_type_rows.append(row)
        self._lv_fuse_type_ids[key] = tid
        self.typfuse_id[name] = tid
        logger.info("Synthesized LV fuse type %s (urat=%.2fkV, irat=%sA)", name, lv_urat, irat)
        return tid

    def _create_lv_type(self, type_name, material, section_mm2, install):
        """Synthesize and register a new ``TypLne`` row for an LV
        line/cable type. Internal; called by :meth:`lv_line_type_name`.

        Electrical parameters:
            - ``rline`` (Ω/km): computed from standard conductor
              resistivity / cross-section (physics-based).
            - ``xline`` (Ω/km) and ``Ithr``/``sline`` (thermal rating):
              single typical value per installation category (overhead
              vs underground), since exact manufacturer data was not
              available -- see ``config["lv_line_type"]["_comment"]``.
            - ``rline0``/``xline0`` (zero-sequence): approximated as
              3x the positive-sequence value, a common rule-of-thumb
              when explicit zero-sequence data is unavailable.

        Returns:
            int: The new type's object id.
        """
        c = self.lv_cfg
        resistivity = c.get("resistivity_ohm_mm2_per_km", {}).get(material,
                      c.get("resistivity_ohm_mm2_per_km", {}).get("default", 22.0))
        rline = round(resistivity / max(section_mm2, 1), 4)
        is_underground = install in ("underground", "busbar")
        xline = c.get("reactance_ohm_per_km", {}).get(
            "underground" if is_underground else "overhead", 0.1)
        ampacity = c.get("ampacity_a_per_mm2", {}).get(
            "underground" if is_underground else "overhead", 4.0)
        ithr_a = ampacity * section_mm2
        sline_ka = round(ithr_a / 1000.0, 4)
        uline = c.get("nominal_voltage_kv", 0.4)

        tid = self.nid()
        self._lv_type_counter += 1
        fold_id = self.tpl["TypLne"]["rows"][0][2] if self.tpl["TypLne"]["rows"] else ""
        row = [
            tid, type_name, fold_id, self._lv_type_counter,
            None,                       # Ithr(r) -- left blank, matching the existing MV rows' convention
            "cbl" if is_underground else "ohl",
            3,                          # nlnph
            1,                          # nneutral -- LV is modelled as 3-phase + neutral (TN system)
            640,                        # rtemp -- reused from the validated MV entries' convention
            rline, round(rline * 3, 4),  # rline, rline0 (rule-of-thumb X0/R0 ~ 3x)
            sline_ka, uline,
            xline, round(xline * 3, 4),  # xline, xline0
            0,                          # bline -- negligible for short LV lines
        ]
        self._lv_type_rows.append(row)
        self._lv_type_cache[type_name] = tid
        self.typlne_id[type_name] = tid
        logger.info(
            "Synthesized LV line type %s: rline=%.3f xline=%.3f uline=%.2fkV Ithr~%.0fA (defaults, verify before real analysis)",
            type_name, rline, xline, uline, ithr_a,
        )
        return tid

    def _switch_on(self, rec):
        v = s(rec.get("T__outserv"))
        off_values = {"1", "true", "True", "\u062e\u0627\u0631\u062c \u0627\u0632 \u0645\u062f\u0627\u0631"}  # "out of circuit"
        return 0 if v in off_values else 1

    def _cap(self, rec):
        try:
            return float(rec.get(self.cfg["transformer"]["capacity_field"]) or 0)
        except Exception:
            return 0.0

    def _progress_build(self, raw_percent, message="", step=""):
        """Report build()/emit() progress, compressed from their existing
        internal 0-95 scale into the first 25% of the overall bar.

        build()+emit() and write() were originally reported on the same
        0-100 scale as if each percentage point took equally long, but
        write() (streaming potentially hundreds of thousands of rows to
        disk) can take far longer per point than build()/emit() -- for a
        real ~50k-line feeder, write() measured at ~80% of total
        wall-clock time despite only ever being allocated the last 4
        points (96-100) of the bar. Since ProgressReporter's ETA is a
        simple linear extrapolation from percent-so-far, that mismatch
        made the estimated time remaining collapse to ~0 as soon as
        build()/emit() finished, long before the process actually
        finished. Rescaling build()/emit() into 0-25% and giving write()
        25-95% (see Converter.write()) makes the percentage -- and so the
        ETA -- track actual elapsed time much more honestly. The exact
        split is a reasonable general-purpose approximation, not a
        per-dataset measurement, so some drift is still expected for
        very small or very LV-heavy conversions.
        """
        self.progress.report(raw_percent * (25.0 / 95.0), message, step)

    # ------------------------------------------------------------------
    # build: read shapefiles, build topology and DGS element lists
    # ------------------------------------------------------------------
    def build(self):
        """Read all configured shapefile layers and build the in-memory
        network topology (terminals, cubicles, element lists).

        Must be called before :meth:`emit`. Reports progress from 0-82%.

        Returns:
            Converter: ``self``, for chaining (``conv.build().emit().write(...)``).

        Raises:
            Exception: Any I/O or data error is logged and re-raised; see
                the module-level docstring for the overall error-handling
                philosophy (fail loud, log everything, let the caller
                decide whether to abort or show a dialog).
        """
        cfg = self.cfg
        idf = cfg["id_field"]

        # --- Second, independent Core-level check -----------------------
        # Deliberately distinct from the license gate in __init__: this
        # one re-verifies application file integrity (not just license
        # validity) right before real work starts, and lives in a
        # different method than __init__'s check. An attacker who
        # patches out the __init__ check still hits this one; an
        # attacker who patches out both still has to find and neutralize
        # this call specifically inside compiled Core code (see
        # shp2dgs/core/protected/README.md) rather than a single
        # Python-level `if`.
        from shp2dgs.license.gate import require_active_license
        require_active_license(feature="gis_import")

        if self.generate_gps:
            self._progress_build(1, "Resolving source coordinate system for GPS\u2026", "gps_setup")
            try:
                crs = geo.resolve_source_crs(self.shp_dir, self.source_epsg)
                self._gps_transformer = geo.build_transformer(crs)
                logger.info("GPS generation enabled | source CRS=%s -> WGS84", crs)
            except (geo.GeoConfigError, ImportError) as e:
                # GPS is supplementary data (e.g. enabled by default) --
                # its absence should never block an otherwise-good MV/LV
                # conversion. Log clearly (visible in the GUI's inline
                # progress log too) and simply skip it for this run.
                self._record_issue("gps_skipped", f"GPS coordinates skipped: {e}", loud=True)
                self._progress_build(1.5, f"GPS coordinates skipped: {e}", "gps_setup")
                self.generate_gps = False
                self.gps_line_vertices = False

        self._progress_build(2, "Starting to read line layers\u2026", "read_lines")

        lines = []
        line_layers = []
        if self.include_mv:
            for k in ("mv_overhead_line", "mv_underground_line", "mv_other_lines",
                      "mv_abc_cable", "mv_jumper"):
                line_layers.extend(self._layer_names(k))
        for idx, layer in enumerate(line_layers, 1):
            self._progress_build(2 + idx * 4, f"Reading layer {layer} \u2026", "read_lines")
            recs, shapes = read_layer(self.shp_dir, layer, None)
            for rec, sh in zip(recs, shapes):
                pts = sh.points
                if len(pts) < 2:
                    continue
                length_m = float(rec.get("Shape_STLe") or rec.get("T_FeatureL") or 0.0)
                try:
                    lines.append(dict(
                        code=s(rec.get(idf)) or "LINE",
                        feeder=self.feeder_name(rec),
                        typ=self.line_type_name(rec, layer),
                        length_km=length_m / 1000.0,
                        a=pts[0], b=pts[-1],
                        points=list(pts)))  # always captured now, for the local diagram preview; GPS export still gated separately below
                except Exception as e:
                    self._record_issue("corrupt_line_record", f"Skipping corrupt line record in {layer}: {e}", loud=True)
                    continue

        raw_line_count = len(lines)
        lines = [seg for ln in lines for seg in self._split_line_into_segments(ln)]
        logger.info("Total lines collected: %d (expanded to %d segments following true GIS vertices)",
                    raw_line_count, len(lines))
        self._progress_build(20, f"Finished reading lines: {raw_line_count} lines "
                                  f"({len(lines)} segments)", "read_lines")

        self._progress_build(22, "Reading point devices\u2026", "read_devices")
        node_switches = collections.defaultdict(list)
        node_fuses = collections.defaultdict(list)
        node_trafos = collections.defaultdict(list)
        node_sources = collections.defaultdict(list)
        elmshnt = []

        mv_switch_layers = []
        mv_fuse_layers = []
        if self.include_mv:
            for k in ("mv_breaker", "mv_disconnector", "mv_recloser",
                      "mv_sectionalizer", "mv_autobooster", "mv_feeder_switch"):
                mv_switch_layers.extend(self._layer_names(k))
            mv_fuse_layers.extend(self._layer_names("mv_fuse"))

        device_map = [
            (mv_switch_layers, node_switches, "switch", "MV switches"),
            (mv_fuse_layers, node_fuses, "fuse", "mv_fuse"),
            (self._layer_names("mv_transformer"), node_trafos, "transformer", "mv_transformer"),
            (self._layer_names("mv_source"), node_sources, "source", "mv_source"),
        ]

        dev_progress_base = 22
        for di, (layers, store, kind, label) in enumerate(device_map):
            if isinstance(layers, str):
                layers = [layers]
            for layer in layers:
                self._progress_build(dev_progress_base + di * 2, f"Reading {label} ({layer}) \u2026", "read_devices")
                recs, shapes = read_layer(self.shp_dir, layer, None)
                for rec, sh in zip(recs, shapes):
                    if not sh.points:
                        continue
                    k = self.snap_key(sh.points[0])
                    try:
                        if kind == "switch":
                            store[k].append(dict(code=s(rec.get(idf)) or "SW",
                                                  feeder=self.feeder_name(rec),
                                                  on=self._switch_on(rec)))
                        elif kind == "fuse":
                            # The fuse *type* is resolved later, in the node
                            # loop below: a fuse with no rated current takes
                            # its rating from the nearest transformer's
                            # capacity, and transformers are read after
                            # fuses (see fuse_type_id/_derive_fuse_current).
                            store[k].append(dict(code=s(rec.get(idf)) or "FU",
                                                  feeder=self.feeder_name(rec),
                                                  rec=rec))
                        elif kind == "transformer":
                            store[k].append(dict(code=s(rec.get(idf)) or "TR",
                                                  feeder=self.feeder_name(rec),
                                                  typ=self.trafo_type_name(rec),
                                                  cap=self._cap(rec)))
                        elif kind == "source":
                            store[k].append(dict(feeder=self.feeder_name(rec),
                                                  xy=self.snap_key(sh.points[0])))
                    except Exception as e:
                        self._record_issue(f"corrupt_{kind}_record", f"Skipping corrupt {kind} record: {e}", loud=True)
                        continue

        # Index every MV transformer's position + capacity so a fuse with a
        # missing/zero rated current can borrow its rating from the nearest
        # one (the fuse on that transformer's MV busbar). Built here, after
        # every device layer has been read.
        self._mv_trafo_index = [
            dict(key=k, cap=t.get("cap", 0.0), feeder=t.get("feeder", ""), code=t.get("code", ""))
            for k, trafo_list in node_trafos.items()
            for t in trafo_list
            if t.get("cap")
        ]
        logger.info("Transformer index for fuse-rating derivation: %d transformer(s)",
                    len(self._mv_trafo_index))

        logger.info(
            "Devices: switches=%d fuses=%d trafos=%d sources=%d",
            sum(len(v) for v in node_switches.values()),
            sum(len(v) for v in node_fuses.values()),
            sum(len(v) for v in node_trafos.values()),
            sum(len(v) for v in node_sources.values()),
        )

        cap_count = 0
        if self.include_mv:
            cap_cfg = cfg.get("capacitor", {})
            for layer in self._layer_names("mv_capacitor"):
                recs, shapes = read_layer(self.shp_dir, layer, None)
                for rec, sh in zip(recs, shapes):
                    if not sh.points:
                        continue
                    xy = sh.points[0]
                    try:
                        code = s(rec.get(idf)) or "CAP"
                        term = self.base_terminal(xy, kind="device_node")
                        eid = self.nid()
                        elmshnt.append((eid, code, cap_cfg.get("shtype", 0), self.uknom,
                                         cap_cfg.get("default_qcapn_mvar", 0.15),
                                         cap_cfg.get("ncapx", 1), self.feeder_name(rec)))
                        # Reuses the "d_load" symbol -- the exact DIgSILENT
                        # DGS symbol name for ElmShnt could not be
                        # confirmed, and an unrecognised symbol would
                        # silently fail to render (see the LV-symbol fix
                        # in the changelog); "d_load" is a safe, valid
                        # fallback with a similar single-terminal footprint.
                        self.elem_symbol[eid] = "d_load"
                        self.elem_kind[eid] = "d_cap"
                        self.elem_center[eid] = xy
                        self.add_cubicle(term, eid, 0)
                        cap_count += 1
                    except Exception as e:
                        self._record_issue("corrupt_capacitor_record", f"Skipping corrupt capacitor record in {layer}: {e}", loud=True)
                        continue
            if cap_count:
                logger.info("MV capacitor banks added: %d", cap_count)

        # --- page margin (auto-scaled) ---
        # Only the page margin scales with the network's real coordinate
        # extent, so the outermost elements keep a sensible gap from the
        # page edge. Symbol sizes and node_spacing are fixed, user-
        # configurable values (see __init__) -- they no longer scale with
        # extent, since that previously made elements huge on real,
        # large-extent networks.
        gcfg = self.graphics_cfg
        extent_pts = []
        for ln in lines:
            extent_pts.append(ln["a"])
            extent_pts.append(ln["b"])
        for store in (node_switches, node_fuses, node_trafos, node_sources):
            extent_pts.extend(store.keys())
        if extent_pts:
            xs_e = [p[0] for p in extent_pts]
            ys_e = [p[1] for p in extent_pts]
            extent = max(max(xs_e) - min(xs_e), max(ys_e) - min(ys_e), 1.0)
        else:
            extent = 1.0
        self.page_margin = max(extent * gcfg.get("page_margin_ratio", 0.05),
                                gcfg.get("page_margin_min", 5.0))
        logger.info(
            "Graphics scale | extent=%.1f node_spacing=%.3f term_size=%.3f "
            "busbar=%.2fx%.2f hv_busbar_len=%.2f elem_size=%.3f page_margin=%.2f",
            extent, self.node_spacing, self.term_size, self.busbar_len,
            self.busbar_thick, self.hv_busbar_len, self.elem_size, self.page_margin,
        )

        self._progress_build(32, "Building base terminals\u2026", "topology")
        for ln in lines:
            self.base_terminal(ln["a"], kind="line_junction")
            self.base_terminal(ln["b"], kind="line_junction")
        for store, tkind in [(node_switches, "switch"), (node_fuses, "fuse"),
                              (node_trafos, "transformer_hv"), (node_sources, "source")]:
            for k in store:
                if k not in self.node_term:
                    self.node_term[k] = self.new_terminal(k, kind=tkind)

        node_line_ends = collections.defaultdict(list)
        for i, ln in enumerate(lines):
            node_line_ends[self.snap_key(ln["a"])].append((i, "a"))
            node_line_ends[self.snap_key(ln["b"])].append((i, "b"))

        self._progress_build(38, "Preparing network topology\u2026", "topology")

        elmlne, elmtr2, elmlod, elmcoup, relfuse, elmxnet, elmfeeder = [], [], [], [], [], [], []
        line_bus = {}
        # Transformer loads are NOT created here -- placement depends on
        # whether an LV network exists downstream of the transformer,
        # which isn't known until _build_lv_network() runs (later in this
        # method). See _place_transformer_loads().
        pending_transformer_loads = []

        all_keys = set(self.node_term.keys())
        total_nodes = len(all_keys)
        logger.info("Processing %d topological nodes\u2026", total_nodes)
        self._progress_build(40, f"Generating DGS elements for {total_nodes} nodes\u2026", "elements")

        processed = 0
        for key in all_keys:
            processed += 1
            if processed % 500 == 0 or processed == total_nodes:
                pct = 40 + (processed / total_nodes) * 30
                self._progress_build(pct, f"Processing node {processed}/{total_nodes} \u2026", "elements")

            T0 = self.node_term[key]
            xy = key
            chain_end = T0
            sw_list = node_switches.get(key, [])
            fu_list = node_fuses.get(key, [])
            tr_list = node_trafos.get(key, [])
            src_list = node_sources.get(key, [])
            line_ends = node_line_ends.get(key, [])

            def add_series(devs, kind):
                nonlocal chain_end
                for dev in devs:
                    tnext = self.new_terminal(xy, kind=kind)
                    eid = self.nid()
                    if kind == "switch":
                        elmcoup.append((eid, dev["code"], dev["on"], dev["feeder"]))
                        self.elem_symbol[eid] = "d_couple"
                    else:  # fuse
                        ftype = dev.get("typ_id")
                        if ftype is None:
                            ftype = self.fuse_type_id(dev.get("rec", {}), "mv", xy=xy)
                        relfuse.append((eid, dev["code"], dev["feeder"], ftype))
                        self.elem_symbol[eid] = "d_fuse"
                    self.elem_kind[eid] = self.elem_symbol[eid]
                    self.elem_center[eid] = xy
                    self.add_cubicle(chain_end, eid, 0)
                    self.add_cubicle(tnext, eid, 1)
                    chain_end = tnext

            add_series(sw_list, "switch")
            add_series(fu_list, "fuse")

            if (sw_list or fu_list) and line_ends:
                first, rest = line_ends[0], line_ends[1:]
                line_bus.setdefault(first[0], {})[first[1]] = T0
                for (li, end) in rest:
                    line_bus.setdefault(li, {})[end] = chain_end
            else:
                for (li, end) in line_ends:
                    line_bus.setdefault(li, {})[end] = T0

            if tr_list:
                # The HV connection point feeds one or more transformers
                # here, so render it as a busbar rather than a plain point.
                # This does NOT change the electrical node -- chain_end is
                # the same ElmTerm used for any adjoining lines/switches/
                # fuses -- only its graphic symbol is overridden.
                self.term_symbol[chain_end] = self.terminal_types.get("transformer_hv", "TermStrip")
                self.term_kind[chain_end] = "transformer_hv"

                # Lay the transformer(s) + LV busbar + load out in a small
                # vertical stack *below* the true HV point, staggered
                # horizontally when several transformers share one node,
                # so nothing overlaps. The HV bus itself stays at the real
                # GIS coordinate to keep line connections geographically
                # accurate.
                n_tr = len(tr_list)
                row_span = (n_tr - 1) * self.node_spacing
                start_dx = -row_span / 2.0
                for ti, tr in enumerate(tr_list):
                    dx = start_dx + ti * self.node_spacing
                    tr_xy = (xy[0] + dx, xy[1] - self.node_spacing)
                    lv_xy = (xy[0] + dx, xy[1] - 2 * self.node_spacing)
                    ld_xy = (xy[0] + dx, xy[1] - 3 * self.node_spacing)

                    lvT = self.new_terminal(xy, kind="transformer_lv", uknom=self.lv_kv, display_xy=lv_xy)
                    # Register this transformer's true position as the LV
                    # network's attachment point here, so LV lines/fuses
                    # read from ELWOLL/ELWULL/ELWLFU/etc. that start at
                    # this same pole connect to the right LV busbar
                    # (first transformer wins if several share one point --
                    # see base_terminal_lv()).
                    self.lv_node_term.setdefault(self.snap_key(xy), lvT)
                    eid = self.nid()
                    elmtr2.append((eid, tr["code"], tr["typ"], tr["feeder"]))
                    self.elem_symbol[eid] = "d_tr2"
                    self.elem_kind[eid] = self.elem_symbol[eid]
                    self.elem_center[eid] = tr_xy
                    self.add_cubicle(chain_end, eid, 0)
                    self.add_cubicle(lvT, eid, 1)
                    # Defer: see _place_transformer_loads() for how this
                    # becomes either one load at lvT (no downstream LV
                    # network) or one load per LV leaf node (LV network
                    # present), called after _build_lv_network().
                    pending_transformer_loads.append(dict(
                        lvT=lvT, cap=tr["cap"], code=tr["code"], fallback_xy=ld_xy, feeder=tr["feeder"]))

            for src in src_list:
                eid = self.nid()
                elmxnet.append((eid, src["feeder"]))
                self.elem_symbol[eid] = "d_net"
                self.elem_kind[eid] = self.elem_symbol[eid]
                self.elem_center[eid] = xy
                self.add_cubicle(T0, eid, 0)
                fid = self.nid()
                elmfeeder.append((fid, src["feeder"], T0))

        self._progress_build(72, f"Generating {len(lines)} line elements\u2026", "lines")
        for i, ln in enumerate(lines):
            if i % 1000 == 0:
                pct = 72 + (i / max(1, len(lines))) * 8
                self._progress_build(pct, f"Generating ElmLne {i}/{len(lines)} \u2026", "lines")
            eid = self.nid()
            elmlne.append((eid, ln["code"], ln["typ"], ln["feeder"], ln["length_km"]))
            self.elem_symbol[eid] = "d_lin"
            self.elem_kind[eid] = self.elem_symbol[eid]
            if ln.get("points"):  # always kept now, for the local diagram preview
                self.line_points_by_id[eid] = ln["points"]
            ax, ay = ln["a"]
            bx, by = ln["b"]
            self.elem_center[eid] = ((ax + bx) / 2.0, (ay + by) / 2.0)
            busa = line_bus.get(i, {}).get("a") or self.base_terminal(ln["a"], kind="line_junction")
            busb = line_bus.get(i, {}).get("b") or self.base_terminal(ln["b"], kind="line_junction")
            self.add_cubicle(busa, eid, 0)
            self.add_cubicle(busb, eid, 1)

        self._progress_build(81, "Building LV (low-voltage) network\u2026", "lv_network")
        self._build_lv_network(elmlne, relfuse, elmcoup, elmxnet, elmfeeder)

        if self._fuse_derived_counts or self._fuse_unresolved_count:
            detail = ", ".join(f"{cap} kVA->{amps} A x{count}"
                               for (cap, amps), count in sorted(self._fuse_derived_counts.items()))
            logger.info(
                "Fuse ratings: %d derived from transformer capacity (%s); %d fell back to the "
                "configured default (no rating, no nearby transformer)",
                sum(self._fuse_derived_counts.values()), detail or "-", self._fuse_unresolved_count)

        self._progress_build(81.5, "Placing transformer loads\u2026", "lv_network")
        self._place_transformer_loads(pending_transformer_loads, elmlod)

        self._elm = dict(lne=elmlne, tr2=elmtr2, lod=elmlod, coup=elmcoup,
                          fuse=relfuse, xnet=elmxnet, feeder=elmfeeder, shnt=elmshnt)
        logger.info(
            "Build complete: Lne=%d Tr2=%d Lod=%d Coup=%d Fuse=%d",
            len(elmlne), len(elmtr2), len(elmlod), len(elmcoup), len(relfuse),
        )
        self._progress_build(82, "Element generation complete.", "emit")
        return self

    # ------------------------------------------------------------------
    # LV (low-voltage) network
    # ------------------------------------------------------------------
    def _build_lv_network(self, elmlne, relfuse, elmcoup, elmxnet, elmfeeder):
        """Read the configured LV shapefile layers (see
        :mod:`shp2dgs.core.equipment_catalog` for the ``lv_*`` keys) and
        extend the network with the LV (0.4 kV-class) topology downstream
        of each transformer's LV busbar.

        Mirrors the MV topology logic in :meth:`build` at a smaller
        scale: LV lines become additional ``ElmLne`` rows (appended to
        ``elmlne``), LV fuses/switches are inserted in series (appended to
        ``relfuse``/``elmcoup``) using the same electrical semantics as
        their MV counterparts. LV line/cable and fuse *types* are
        synthesized on demand (see :meth:`lv_line_type_name` and
        :meth:`_get_lv_fuse_type_id``) since the base template only ships
        MV-rated types. Distributed generation points (``lv_dg_source``)
        are added as equivalent sources (``ElmXnet``/``ElmFeeder``),
        mirroring how a feeder-head source is modelled at MV.

        LV lines/devices whose endpoint coincides with a transformer's
        true position (registered in ``self.lv_node_term`` during the
        main loop in :meth:`build`) connect to that transformer's LV
        busbar; all other LV points get their own LV line-junction
        terminal via :meth:`base_terminal_lv`.

        Note: this method intentionally does **not** create any new
        ``Elmlod`` (load) elements for individual LV lines/service points
        -- the source data has no reliable per-customer load size, so
        fabricating one would be misleading. LV loads remain represented
        only in aggregate, split across the LV network's leaf nodes (see
        :meth:`_place_transformer_loads`).

        Args:
            elmlne: The in-progress list of ``ElmLne`` tuples (mutated in
                place -- LV lines are appended here, alongside the MV ones).
            relfuse: The in-progress list of ``RelFuse`` tuples (mutated
                in place).
            elmcoup: The in-progress list of ``ElmCoup`` tuples (mutated
                in place).
            elmxnet: The in-progress list of ``ElmXnet`` tuples (mutated
                in place -- LV DG sources are appended here).
            elmfeeder: The in-progress list of ``ElmFeeder`` tuples
                (mutated in place).

        Raises:
            Exception: Any I/O or data error is logged and re-raised,
                consistent with :meth:`build`'s overall error-handling
                philosophy.
        """
        if not self.include_lv:
            logger.info("LV network disabled for this run (include_lv=False) - skipping")
            return

        cfg = self.cfg
        idf = cfg["id_field"]

        lv_line_layers = []
        for k in ("lv_overhead_line", "lv_underground_line", "lv_other_lines",
                  "lv_jumper", "lv_abc_cable", "lv_service_cable"):
            lv_line_layers.extend(self._layer_names(k))
        if not lv_line_layers:
            logger.info("No LV line layers configured - skipping LV network")
            return

        lv_lines = []
        for layer in lv_line_layers:
            recs, shapes = read_layer(self.shp_dir, layer, None)
            for rec, sh in zip(recs, shapes):
                pts = sh.points
                if len(pts) < 2:
                    continue
                length_m = float(rec.get("Shape_STLe") or rec.get("T_FeatureL") or 0.0)
                try:
                    lv_lines.append(dict(
                        code=s(rec.get(idf)) or "LVLINE",
                        feeder=self.feeder_name(rec),
                        typ=self.lv_line_type_name(rec, layer),
                        length_km=length_m / 1000.0,
                        a=pts[0], b=pts[-1],
                        points=list(pts)))  # always captured now, for the local diagram preview; GPS export still gated separately below
                except Exception as e:
                    self._record_issue("corrupt_lv_line_record", f"Skipping corrupt LV line record in {layer}: {e}", loud=True)
                    continue
        raw_lv_line_count = len(lv_lines)
        lv_lines = [seg for ln in lv_lines for seg in self._split_line_into_segments(ln)]
        logger.info("LV lines collected: %d (expanded to %d segments; layers=%s)",
                    raw_lv_line_count, len(lv_lines), lv_line_layers)
        if not lv_lines:
            return

        node_lv_fuses = collections.defaultdict(list)
        for layer in self._layer_names("lv_fuse"):
            recs, shapes = read_layer(self.shp_dir, layer, None)
            for rec, sh in zip(recs, shapes):
                if not sh.points:
                    continue
                k = self.snap_key(sh.points[0])
                node_lv_fuses[k].append(dict(code=s(rec.get(idf)) or "LFU", feeder=self.feeder_name(rec),
                                              typ_id=self.fuse_type_id(rec, "lv")))

        node_lv_switches = collections.defaultdict(list)
        for layer in self._layer_names("lv_switch"):
            recs, shapes = read_layer(self.shp_dir, layer, None)
            for rec, sh in zip(recs, shapes):
                if not sh.points:
                    continue
                k = self.snap_key(sh.points[0])
                node_lv_switches[k].append(dict(code=s(rec.get(idf)) or "LSW",
                                                 feeder=self.feeder_name(rec), on=self._switch_on(rec)))

        for layer in self._layer_names("lv_distribution_box"):
            recs, shapes = read_layer(self.shp_dir, layer, None)
            for rec, sh in zip(recs, shapes):
                if not sh.points:
                    continue
                k = self.snap_key(sh.points[0])
                if k not in self.lv_node_term and k not in self.lv_line_node_term:
                    self.lv_line_node_term[k] = self.new_terminal(k, kind="lv_distribution_box", uknom=self.lv_kv)

        node_line_ends = collections.defaultdict(list)
        for i, ln in enumerate(lv_lines):
            node_line_ends[self.snap_key(ln["a"])].append((i, "a"))
            node_line_ends[self.snap_key(ln["b"])].append((i, "b"))

        line_bus = {}
        all_keys = set(node_line_ends.keys()) | set(node_lv_fuses.keys()) | set(node_lv_switches.keys())
        logger.info("Processing %d LV topological nodes\u2026", len(all_keys))

        for key in all_keys:
            T0 = self.base_terminal_lv(key, kind="lv_line_junction")
            chain_end = T0
            fu_list = node_lv_fuses.get(key, [])
            sw_list = node_lv_switches.get(key, [])
            line_ends = node_line_ends.get(key, [])

            for dev in sw_list:
                tnext = self.new_terminal(key, kind="lv_switch", uknom=self.lv_kv)
                eid = self.nid()
                elmcoup.append((eid, dev["code"], dev["on"], dev["feeder"]))
                self.elem_symbol[eid] = "d_couple"  # must be a valid DIgSILENT symbol name
                self.elem_kind[eid] = "d_lv_couple"  # internal-only, for diagram colouring
                self.elem_center[eid] = key
                self.add_cubicle(chain_end, eid, 0)
                self.add_cubicle(tnext, eid, 1)
                self.lv_adjacency[chain_end].add(tnext)
                self.lv_adjacency[tnext].add(chain_end)
                chain_end = tnext

            for dev in fu_list:
                tnext = self.new_terminal(key, kind="lv_fuse", uknom=self.lv_kv)
                eid = self.nid()
                relfuse.append((eid, dev["code"], dev["feeder"], dev.get("typ_id", "")))
                self.elem_symbol[eid] = "d_fuse"  # must be a valid DIgSILENT symbol name
                self.elem_kind[eid] = "d_lv_fuse"  # internal-only, for diagram colouring
                self.elem_center[eid] = key
                self.add_cubicle(chain_end, eid, 0)
                self.add_cubicle(tnext, eid, 1)
                self.lv_adjacency[chain_end].add(tnext)
                self.lv_adjacency[tnext].add(chain_end)
                chain_end = tnext

            if (sw_list or fu_list) and line_ends:
                first, rest = line_ends[0], line_ends[1:]
                line_bus.setdefault(first[0], {})[first[1]] = T0
                for (li, end) in rest:
                    line_bus.setdefault(li, {})[end] = chain_end
            else:
                for (li, end) in line_ends:
                    line_bus.setdefault(li, {})[end] = T0

        for i, ln in enumerate(lv_lines):
            eid = self.nid()
            elmlne.append((eid, ln["code"], ln["typ"], ln["feeder"], ln["length_km"]))
            self.elem_symbol[eid] = "d_lin"  # must be a valid DIgSILENT symbol name
            self.elem_kind[eid] = "d_lv_lin"  # internal-only, for diagram colouring
            if ln.get("points"):  # always kept now, for the local diagram preview
                self.line_points_by_id[eid] = ln["points"]
            ax, ay = ln["a"]
            bx, by = ln["b"]
            self.elem_center[eid] = ((ax + bx) / 2.0, (ay + by) / 2.0)
            busa = line_bus.get(i, {}).get("a") or self.base_terminal_lv(ln["a"], kind="lv_line_junction")
            busb = line_bus.get(i, {}).get("b") or self.base_terminal_lv(ln["b"], kind="lv_line_junction")
            self.add_cubicle(busa, eid, 0)
            self.add_cubicle(busb, eid, 1)
            self.lv_adjacency[busa].add(busb)
            self.lv_adjacency[busb].add(busa)

        dg_count = 0
        for layer in self._layer_names("lv_dg_source"):
            recs, shapes = read_layer(self.shp_dir, layer, None)
            for rec, sh in zip(recs, shapes):
                if not sh.points:
                    continue
                xy = sh.points[0]
                try:
                    feeder = self.feeder_name(rec) or "DG"
                    term = self.base_terminal_lv(xy, kind="lv_line_junction")
                    eid = self.nid()
                    elmxnet.append((eid, feeder))
                    self.elem_symbol[eid] = "d_net"
                    self.elem_kind[eid] = "d_lv_net"
                    self.elem_center[eid] = xy
                    self.add_cubicle(term, eid, 0)
                    fid = self.nid()
                    elmfeeder.append((fid, feeder, term))
                    dg_count += 1
                except Exception as e:
                    self._record_issue("corrupt_dg_source_record", f"Skipping corrupt DG source record in {layer}: {e}", loud=True)
                    continue
        if dg_count:
            logger.info("LV distributed generation sources added: %d", dg_count)

        logger.info(
            "LV network complete: Lne=%d Fuse=%d Switch=%d, %d synthesized LV line types",
            len(lv_lines), sum(len(v) for v in node_lv_fuses.values()),
            sum(len(v) for v in node_lv_switches.values()), len(self._lv_type_rows),
        )

    def _place_transformer_loads(self, pending_transformer_loads, elmlod):
        """Create each transformer's ``Elmlod`` load(s) now that the LV
        network topology (built by :meth:`_build_lv_network`) is known.

        For each transformer:

        - If **no** LV line network is connected to its LV busbar (the
          common case when no LV shapefile layers are supplied, or that
          particular transformer simply has none digitised), a single
          load is created directly on the LV busbar, at its full rated
          capacity -- unchanged from previous versions.
        - If an LV network **is** connected, the transformer's rated
          capacity is split evenly across the LV network's leaf nodes
          (the dead-ends of the downstream LV feeder, found via a
          breadth-first search over ``self.lv_adjacency``), instead of
          being lumped on the LV busbar. This reflects where the load
          actually is physically, which matters for LV voltage-drop
          studies. The LV busbar itself is never treated as a leaf, even
          if it happens to have only one outgoing LV line.

        Args:
            pending_transformer_loads: List of dicts (as queued by
                :meth:`build`) with keys ``lvT`` (LV busbar terminal id),
                ``cap`` (rated capacity), ``code`` (transformer code, used
                to name the load(s)), ``fallback_xy`` (diagram position
                to use for the single-load fallback case).
            elmlod: The in-progress list of ``Elmlod`` tuples (mutated in
                place -- one or more loads are appended per transformer).
        """
        total_loads = 0
        transformers_with_lv_network = 0
        for entry in pending_transformer_loads:
            lvT, cap, code, fallback_xy, feeder = (
                entry["lvT"], entry["cap"], entry["code"], entry["fallback_xy"], entry.get("feeder", ""))

            visited = {lvT}
            queue = collections.deque([lvT])
            while queue:
                node = queue.popleft()
                for neighbor in self.lv_adjacency.get(node, ()):
                    if neighbor not in visited:
                        visited.add(neighbor)
                        queue.append(neighbor)

            leaves = [n for n in visited if n != lvT and len(self.lv_adjacency.get(n, ())) == 1]

            if len(visited) <= 1 or not leaves:
                # No downstream LV network (or a degenerate one with no
                # clear leaf, e.g. a single isolated point) -- fall back
                # to the original behaviour: one load on the LV busbar.
                lid = self.nid()
                elmlod.append((lid, "load_" + code, cap, feeder))
                self.elem_symbol[lid] = "d_load"
                self.elem_kind[lid] = "d_load"
                self.elem_center[lid] = fallback_xy
                self.add_cubicle(lvT, lid, 0)
                total_loads += 1
                continue

            transformers_with_lv_network += 1
            cap_each = cap / len(leaves)
            for i, leaf in enumerate(leaves, 1):
                lid = self.nid()
                elmlod.append((lid, f"load_{code}_{i}", cap_each, feeder))
                self.elem_symbol[lid] = "d_load"
                self.elem_kind[lid] = "d_load"
                self.elem_center[lid] = self.term_xy[leaf]
                self.add_cubicle(leaf, lid, 0)
                total_loads += 1

        logger.info(
            "Placed %d load(s) for %d transformers (%d with a downstream LV network, split across leaf nodes)",
            total_loads, len(pending_transformer_loads), transformers_with_lv_network,
        )

    # ------------------------------------------------------------------
    # emit: turn the in-memory topology into DGS sheets
    # ------------------------------------------------------------------
    def emit(self):
        """Populate ``self.sheets``/``self.headers`` (the DGS worksheet
        contents) from the topology built by :meth:`build`.

        Must be called after :meth:`build` and before :meth:`write`.
        Reports progress from 84-95% (plus 90-95% for graphics, if
        enabled).

        Returns:
            Converter: ``self``, for chaining.

        Raises:
            RuntimeError: If called before :meth:`build`.
        """
        if self._elm is None:
            raise RuntimeError("Converter.emit() called before build()")
        cfg = self.cfg
        L = cfg["load"]
        elm = self._elm

        self._progress_build(84, "Writing Excel sheets \u2026", "emit")

        def sheet(name, header):
            self.sheets[name] = []
            self.headers[name] = header

        def add(name, row):
            self.sheets[name].append(row)

        # fixed sheets - copy from template, then apply project meta overrides
        for name in ["General", "ElmNet", "IntFolder", "IntGrfnet",
                     "TypLne", "TypTr2", "TypLod", "TypFuse"]:
            self.headers[name] = list(self.tpl[name]["header"])
            self.sheets[name] = [list(r) for r in self.tpl[name]["rows"]]

        # LV line/cable types are not present in the base template -- they
        # were synthesized on demand during build() (see lv_line_type_name())
        # and are appended here so they end up in the same TypLne sheet as
        # the MV types.
        if self._lv_type_rows:
            self.sheets["TypLne"].extend(self._lv_type_rows)
            logger.info("Appended %d synthesized LV line types to TypLne", len(self._lv_type_rows))
        if self._lv_fuse_type_rows:
            self.sheets["TypFuse"].extend(self._lv_fuse_type_rows)
            logger.info("Appended %d synthesized LV fuse type(s) to TypFuse", len(self._lv_fuse_type_rows))

        # --- project meta injection ---
        # ElmNet: [ID, loc_name, fold_id, frnom]
        try:
            if self.sheets["ElmNet"]:
                if self.project_meta.get("grid_name"):
                    self.sheets["ElmNet"][0][1] = self.project_meta["grid_name"]
                if self.project_meta.get("frequency_hz"):
                    if len(self.sheets["ElmNet"][0]) > 3:
                        self.sheets["ElmNet"][0][3] = float(self.project_meta["frequency_hz"])
        except Exception as e:
            self._record_issue("meta_injection_failed", f"Failed to inject ElmNet meta: {e}", loud=True)

        # IntGrfnet: [ID, loc_name, ...]
        try:
            if self.sheets["IntGrfnet"]:
                deck_name = self.project_meta.get("deck_name") or self.project_meta.get("project_name")
                if deck_name:
                    self.sheets["IntGrfnet"][0][1] = deck_name
        except Exception as e:
            self._record_issue("meta_injection_failed", f"Failed to inject IntGrfnet meta: {e}", loud=True)

        # --- ElmLne ---
        sheet("ElmLne", ["ID(a:40)", "loc_name(a:40)", "fold_id(p)", "typ_id(p)",
                          "chr_name(a:20)", "dline(r)"])
        for (eid, code, typ, feeder, km) in elm["lne"]:
            add("ElmLne", [eid, code, self.GRID_ID, self.typlne_id.get(typ, ""), feeder, round(km, 6)])
        # ElmTr2
        sheet("ElmTr2", ["ID(a:40)", "loc_name(a:40)", "fold_id(p)", "typ_id(p)",
                          "chr_name(a:20)", "constr(i)", "cgnd_h(i)", "cgnd_l(i)",
                          "i_auto(i)", "nntap(i)", "ntrcn(i)", "outserv(i)", "ratfac(r)"])
        for (eid, code, typ, feeder) in elm["tr2"]:
            add("ElmTr2", [eid, code, self.GRID_ID, self.typtr_id.get(typ, ""), feeder,
                            0, 0, 0, 0, 0, 0, 0, 1])
        # Elmlod
        sheet("Elmlod", ["ID(a:40)", "loc_name(a:40)", "fold_id(p)", "typ_id(p)",
                          "chr_name(a:20)", "outserv(i)", "phtech(a)", "i_sym(r)",
                          "mode_inp(a)", "plini(r)", "coslini(r)"])
        lodtype = self.typlod_id.get(L["load_type_name"], "")
        for (lid, name, cap, feeder) in elm["lod"]:
            plini = round(cap * L["loading_factor"] / 1000.0, 6)
            add("Elmlod", [lid, name, self.GRID_ID, lodtype, feeder, 0,
                            L["phtech"], 0, L["mode_inp"], plini, L["power_factor_cos"]])
        # ElmCoup
        sheet("ElmCoup", ["ID(a:40)", "loc_name(a:40)", "fold_id(p)", "typ_id(p)",
                           "on_off(i)", "aUsage(a:4)", "chr_name(a:20)"])
        for (eid, code, on, feeder) in elm["coup"]:
            add("ElmCoup", [eid, code, self.GRID_ID, "", on, "cbk", feeder])
        # RelFuse
        sheet("RelFuse", ["ID(a:40)", "loc_name(a:40)", "fold_id(p)", "chr_name(a:20)",
                           "on_off(i)", "typ_id(p)", "aUsage(a:4)"])
        for (eid, code, feeder, ftype) in elm["fuse"]:
            add("RelFuse", [eid, code, self.GRID_ID, feeder, 1, ftype, "fus"])
        # ElmXnet
        sheet("ElmXnet", ["ID(a:40)", "loc_name(a:40)", "fold_id(p)", "chr_name(a:20)",
                           "bustp(a:2)", "cgnd(i)", "iintgnd(i)", "usetp(r)", "r0tx0(r)",
                           "r0tx0min(r)", "rntxn(r)", "rntxnmin(r)", "snss(r)", "snssmin(r)"])
        for (eid, feeder) in elm["xnet"]:
            add("ElmXnet", [eid, feeder, self.GRID_ID, feeder, "SL", 0, 0, 1,
                             0.1, 0.1, 0.5, 0.4, 600, 500])
        # ElmFeeder
        term_line_cub = {}
        term_any_cub = {}
        for (cid, term, elemid, bus) in self.cubicles:
            term_any_cub.setdefault(term, cid)
            if self.elem_symbol.get(elemid) == "d_lin":
                term_line_cub.setdefault(term, cid)
        sheet("ElmFeeder", ["ID(a:40)", "loc_name(a:40)", "fold_id(p)", "obj_id(p)",
                             "iorient(i)", "i_scale(i)", "Iset(r)", "icolor(i)", "chr_name(a:20)"])
        # icolor(i) palette: PowerFactory's well-differentiated diagram
        # colors are 2-7 and 10-15 (12 distinct values); 0/1/8/9 etc. are
        # skipped. Feeders beyond the 12th reuse the palette from the top
        # rather than drifting into unintended color-index values.
        feeder_color_palette = [2, 3, 4, 5, 6, 7, 10, 11, 12, 13, 14, 15]
        for i, (fid, feeder, term) in enumerate(elm["feeder"]):
            obj = term_line_cub.get(term) or term_any_cub.get(term, "")
            color = feeder_color_palette[i % len(feeder_color_palette)]
            add("ElmFeeder", [fid, feeder, "", obj, "", "", "", color, feeder])
        # ElmShnt (capacitor banks)
        sheet("ElmShnt", ["ID(a:40)", "loc_name(a:40)", "fold_id(p)", "chr_name(a:20)",
                           "shtype(i)", "ushnm(r)", "qcapn(r)", "ncapx(i)", "ctech(i)", "tandc(r)"])
        for (eid, code, shtype, ushnm, qcapn, ncapx, feeder) in elm.get("shnt", []):
            add("ElmShnt", [eid, code, self.GRID_ID, feeder, shtype, ushnm, qcapn, ncapx, 0, 0])
        # ElmTerm
        sheet("ElmTerm", ["ID(a:40)", "loc_name(a:40)", "fold_id(p)", "typ_id(p)",
                           "chr_name(a:20)", "iUsage(i)", "outserv(i)", "phtech(i)", "uknom(r)"])
        for (tid, loc, chrn) in self.terminals:
            add("ElmTerm", [tid, loc, self.GRID_ID, "", chrn, 0, 0, 0, self.term_uknom.get(tid, self.uknom)])
        # StaCubic
        sheet("StaCubic", ["ID(a:40)", "loc_name(a:40)", "fold_id(p)", "chr_name(a:20)",
                            "obj_bus(i)", "obj_id(p)"])
        for n, (cid, term_id, elem_id, bus) in enumerate(self.cubicles):
            add("StaCubic", [cid, "cub_%d" % n, term_id, "CUB%d" % n, bus, elem_id])

        if self.generate_gps:
            self._progress_build(88, "Generating GPS coordinates for Geographic Diagram\u2026", "gps")
            self._emit_gps()

        if self.make_graphics:
            self._progress_build(90, "Generating graphic sheets \u2026", "graphics")
            self._emit_graphics()
        else:
            logger.info("Graphics skipped (make_graphics=False)")

        self._progress_build(95, "Sheets are ready.", "emit")
        return self

    def _emit_gps(self):
        """Append ``GPSlat``/``GPSlon`` (WGS84) columns to ``ElmTerm``,
        and -- if ``gps_line_vertices`` is enabled -- ``GPSlat:i``/
        ``GPSlon:i`` route-vertex columns to ``ElmLne``. Internal; called
        by :meth:`emit` when ``generate_gps`` is enabled.

        These are consumed by PowerFactory's Geographic Diagram (map
        view), which is unrelated to the ``IntGrf`` diagram coordinates
        emitted by :meth:`_emit_graphics`.

        Raises:
            RuntimeError: If the GPS transformer was not resolved (should
                not happen if :meth:`build` ran first with
                ``generate_gps=True``).
        """
        if self._gps_transformer is None:
            raise RuntimeError("_emit_gps() called without a resolved GPS transformer")

        # --- ElmTerm: single GPSlat/GPSlon per terminal ---
        term_hdr = self.headers["ElmTerm"]
        term_hdr.extend(["GPSlat(r)", "GPSlon(r)"])
        ok, failed = 0, 0
        for row in self.sheets["ElmTerm"]:
            tid = row[0]
            x, y = self.term_xy.get(tid, (None, None))
            if x is None:
                row.extend([None, None])
                failed += 1
                continue
            lat, lon = geo.transform_point(self._gps_transformer, x, y)
            row.extend([lat, lon])
            ok += 1 if lat is not None else 0
            failed += 0 if lat is not None else 1
        logger.info("GPS coordinates written for ElmTerm: %d ok, %d failed/skipped", ok, failed)

        # --- ElmLne: optional full route as GPSlat:i/GPSlon:i ---
        if self.gps_line_vertices and self.line_points_by_id:
            # Cap the column count: one over-digitized outlier line (a
            # common GIS glitch -- hundreds of near-duplicate vertices)
            # would otherwise force every single line's row to carry that
            # many mostly-empty columns. The in-app diagram preview is
            # unaffected by this cap -- it reads the uncapped
            # line_points_by_id directly, not these GPS columns.
            max_pts_cap = 60
            true_max = max((len(p) for p in self.line_points_by_id.values()), default=0)
            max_pts = min(true_max, max_pts_cap)
            if true_max > max_pts_cap:
                n_truncated = sum(1 for p in self.line_points_by_id.values() if len(p) > max_pts_cap)
                self._record_issue(
                    "gps_line_route_truncated",
                    f"{n_truncated} line(s) have more than {max_pts_cap} route vertices "
                    f"(max seen: {true_max}); GPS route columns were capped at {max_pts_cap} points "
                    f"for these -- likely an over-digitized/glitched shapefile geometry, worth checking in GIS",
                    loud=True)
            lne_hdr = self.headers["ElmLne"]
            for i in range(max_pts):
                lne_hdr.extend([f"GPSlat:{i}(r)", f"GPSlon:{i}(r)"])
            for row in self.sheets["ElmLne"]:
                eid = row[0]
                pts = self.line_points_by_id.get(eid, [])
                extra = []
                for i in range(max_pts):
                    if i < len(pts):
                        lat, lon = geo.transform_point(self._gps_transformer, pts[i][0], pts[i][1])
                    else:
                        lat, lon = None, None
                    extra.extend([lat, lon])
                row.extend(extra)
            logger.info("GPS route vertices written for ElmLne: up to %d points/line", max_pts)

    def _emit_graphics(self):
        """Populate the ``IntGrf``/``IntGrfcon`` sheets from
        ``self.term_xy``/``self.elem_center``. Internal; called by
        :meth:`emit` when ``make_graphics`` is enabled.

        Raises:
            Exception: Any failure while computing symbol geometry is
                logged with a full traceback and re-raised, since a
                partially-written graphics sheet would silently corrupt
                the DGS deck.
        """
        try:
            xs = [xy[0] for xy in self.term_xy.values()]
            ys = [xy[1] for xy in self.term_xy.values()]
            if not xs or not ys:
                self._record_issue("no_terminal_coordinates", "No terminal coordinates available - graphics sheets will be empty", loud=True)
                self.headers["IntGrf"] = ["ID(a:40)", "loc_name(a:40)", "fold_id(p)", "iCol(i)", "iRot(i)",
                                           "pDataObj(p)", "rCenterX(r)", "rCenterY(r)", "rSizeX(r)", "rSizeY(r)",
                                           "sSymNam(a:40)"]
                self.sheets["IntGrf"] = []
                self.headers["IntGrfcon"] = ["ID(a:40)", "loc_name(a:40)", "fold_id(p)", "rX:SIZEX(i)",
                                              "rX:0(r)", "rX:1(r)", "rX:2(r)", "rY:SIZEX(i)", "rY:0(r)", "rY:1(r)"]
                self.sheets["IntGrfcon"] = []
                return
            minx, miny = min(xs), min(ys)
            # Shift the whole diagram so the outermost elements sit
            # `page_margin` units in from the (0,0) drawing origin instead
            # of touching it, giving proper breathing room from the page edge.
            margin = self.page_margin

            def tx(p):
                return (round(p[0] - minx + margin, 3), round(p[1] - miny + margin, 3))

            def term_box_size(symbol, tid):
                if symbol in BUSBAR_SYMBOLS:
                    length = self.hv_busbar_len if self.term_kind.get(tid) == "transformer_hv" else self.busbar_len
                    return (length, self.busbar_thick)
                return (self.term_size, self.term_size)

            self.headers["IntGrf"] = ["ID(a:40)", "loc_name(a:40)", "fold_id(p)", "iCol(i)", "iRot(i)",
                                       "pDataObj(p)", "rCenterX(r)", "rCenterY(r)", "rSizeX(r)", "rSizeY(r)",
                                       "sSymNam(a:40)"]
            self.sheets["IntGrf"] = []
            grf_of = {}
            gi = 0
            for (tid, loc, chrn) in self.terminals:
                cx, cy = tx(self.term_display_xy.get(tid, self.term_xy[tid]))
                sx, sy = term_box_size(self.term_symbol[tid], tid)
                gid = self.nid()
                grf_of[tid] = gid
                # iRot=0 keeps busbar-style symbols horizontal (a flat line)
                self.sheets["IntGrf"].append([gid, "Grf%d" % gi, self.GRFNET_ID, 1, 0,
                                               tid, cx, cy, round(sx, 3), round(sy, 3),
                                               self.term_symbol[tid]])
                gi += 1
                if gi % 2000 == 0:
                    pct = 90 + (gi / max(1, len(self.terminals) + len(self.elem_symbol))) * 4
                    self._progress_build(pct, f"Terminal graphics {gi} \u2026", "graphics")
            for eid, sym in self.elem_symbol.items():
                cx, cy = tx(self.elem_center[eid])
                gid = self.nid()
                grf_of[eid] = gid
                self.sheets["IntGrf"].append([gid, "Grf%d" % gi, self.GRFNET_ID, 1, 0,
                                               eid, cx, cy, round(self.elem_size, 3),
                                               round(self.elem_size, 3), sym])
                gi += 1
            # True line paths (every intermediate GIS vertex, not just the
            # two endpoints), in this same already-margin-shifted diagram
            # coordinate space -- consumed only by this app's own Diagram
            # Preview widget (see core.diagram.build_diagram_model) to draw
            # the actual routed path instead of a straight symbol-to-symbol
            # segment. Deliberately NOT written into IntGrfcon itself: that
            # object's exact semantics for bend points beyond start/end
            # aren't something we can safely verify against real
            # DIgSILENT import behaviour here, and getting it wrong risks
            # corrupting the actual DGS deck rather than just this preview.
            self.line_points_diagram_xy = {
                eid: [tx(p) for p in pts] for eid, pts in self.line_points_by_id.items()
            }
            self.headers["IntGrfcon"] = ["ID(a:40)", "loc_name(a:40)", "fold_id(p)", "rX:SIZEX(i)",
                                          "rX:0(r)", "rX:1(r)", "rX:2(r)", "rY:SIZEX(i)", "rY:0(r)", "rY:1(r)"]
            self.sheets["IntGrfcon"] = []
            for ci, (cid, term_id, elem_id, bus) in enumerate(self.cubicles):
                if ci % 2000 == 0:
                    pct = 94 + (ci / max(1, len(self.cubicles))) * 1
                    self._progress_build(pct, f"Graphic connections {ci}/{len(self.cubicles)} \u2026", "graphics")
                ex, ey = tx(self.elem_center[elem_id])
                txp = tx(self.term_display_xy.get(term_id, self.term_xy[term_id]))
                self.sheets["IntGrfcon"].append([self.nid(), "ConGrf%d" % ci, grf_of[elem_id], 2, ex, txp[0],
                                                  None, 2, ey, txp[1]])
            logger.info(
                "Graphics generated: IntGrf=%d IntGrfcon=%d",
                len(self.sheets["IntGrf"]), len(self.sheets["IntGrfcon"]),
            )
        except Exception as e:
            logger.error("Graphics generation failed: %s", e, exc_info=True)
            raise

    # ------------------------------------------------------------------
    # write: persist the sheets to an .xlsx workbook
    # ------------------------------------------------------------------
    def write(self, out_path):
        """Write the emitted sheets to an ``.xlsx`` workbook.

        Args:
            out_path: Destination path. Parent directories are created
                automatically if missing.

        Returns:
            str: ``out_path`` (for convenience/chaining with callers that
            want the final path back).

        Raises:
            RuntimeError: If called before :meth:`emit`.
            PermissionError: If the destination file is locked/read-only
                (e.g. open in Excel).
            Exception: Any other I/O failure while saving the workbook.
        """
        _ensure_openpyxl_available()
        if not self.sheets:
            raise RuntimeError("Converter.write() called before emit()")
        self.progress.report(25, "Writing Excel file \u2026", "write")
        try:
            out_dir = os.path.dirname(os.path.abspath(out_path))
            if out_dir and not os.path.exists(out_dir):
                os.makedirs(out_dir, exist_ok=True)
            # write_only mode streams rows straight to disk instead of
            # keeping every cell as an in-memory Cell object, which is
            # dramatically faster for large sheets (measured: the write
            # step took ~80% of total conversion time for a ~50k-line
            # feeder before this change). This is safe here because the
            # loop below only ever appends rows in order and never reads
            # a cell back or re-styles it afterwards.
            wb = openpyxl.Workbook(write_only=True)
            order = ["General", "ElmNet", "ElmLne", "ElmTr2", "Elmlod", "ElmCoup",
                     "RelFuse", "ElmTerm", "ElmXnet", "IntFolder", "IntGrfnet",
                     "IntGrf", "IntGrfcon", "StaCubic", "TypLne", "TypLod", "TypTr2",
                     "ElmFeeder", "ElmShnt", "TypFuse"]
            if "ElmShnt" not in self.sheets:
                self.headers["ElmShnt"] = ["ID(a:40)", "loc_name(a:40)", "fold_id(p)",
                                            "chr_name(a:20)", "shtype(i)", "ushnm(r)",
                                            "qcapn(r)", "ncapx(i)", "ctech(i)", "tandc(r)"]
                self.sheets["ElmShnt"] = []
            # The progress budget for this stage (55-95%) is split by each
            # sheet's *row count*, not evenly by sheet -- a ~230k-row
            # IntGrf sheet should move the bar far more than a 20-row
            # TypFuse sheet, or the percentage would again race ahead of
            # the actual remaining work.
            sheets_to_write = [n for n in order if n in self.sheets or n in self.headers]
            total_rows = sum(len(self.sheets.get(n, [])) for n in sheets_to_write) or 1
            rows_done = 0
            for name in sheets_to_write:
                ws = wb.create_sheet(name)
                ws.append(self.headers[name])
                for row in self.sheets.get(name, []):
                    ws.append(row)
                    rows_done += 1
                    if rows_done % 5000 == 0:
                        pct = 25 + (rows_done / total_rows) * 70
                        self.progress.report(pct, f"Writing sheet {name} \u2026 ({rows_done:,}/{total_rows:,} rows)", "write")
            self.progress.report(95, "Saving workbook to disk \u2026", "write")
            wb.save(out_path)
            logger.info("Workbook saved: %s (%.2f MB)", out_path, os.path.getsize(out_path) / 1024 / 1024)
            self.report_path = self.write_report(out_path)
            self.progress.report(100, "Completed successfully!", "done")
            return out_path
        except PermissionError:
            logger.error("Permission denied writing %s", out_path)
            raise
        except Exception as e:
            logger.error("Failed to write workbook: %s", e, exc_info=True)
            raise

    def set_manual_overrides(self, overrides):
        """Install user-chosen types from the prescan-review workflow.

        Args:
            overrides: ``{"mv_underground_line": {(section, material): type_name},
                          "mv_overhead_line": {(section, material): type_name},
                          "mv_other_lines": {"__all__": type_name},
                          "transformer": {capacity: type_name}}``. Missing
                keys/kinds are left at their current (empty) value.
        """
        for kind, mapping in (overrides or {}).items():
            self.manual_overrides.setdefault(kind, {}).update(mapping)

    def _build_endpoint_index(self, shapes):
        """Map each record's snapped start/end point -> the indices of
        every record (in the same ``shapes`` list) touching it -- i.e.
        "what's connected here", the same way the real topology-building
        code finds it. Used by :meth:`_neighbor_recommendation`."""
        endpoint_index = collections.defaultdict(list)
        for i, sh in enumerate(shapes):
            if sh.points and len(sh.points) >= 2:
                endpoint_index[self.snap_key(sh.points[0])].append(i)
                endpoint_index[self.snap_key(sh.points[-1])].append(i)
        return endpoint_index

    def _neighbor_recommendation(self, i, shapes, endpoint_index, resolved_type_for):
        """The type recommended for record ``i`` based on the line
        immediately before/after it on the same conductor run (sharing
        its start/end point respectively): if both resolve to the same
        type, that's a strong signal this record's missing/bad data is
        simply a gap in an otherwise-uniform run. If they disagree --
        including the common case of a transition between an underground
        and an overhead segment, which by definition resolve to different
        type families -- prefer the "previous" (start-side) neighbor.

        Args:
            i: Index into ``shapes``/the corresponding records list.
            shapes: The layer's shapes (from ``read_layer``).
            endpoint_index: From :meth:`_build_endpoint_index` on the
                same ``shapes``.
            resolved_type_for: ``f(neighbor_index) -> Optional[str]``,
                the neighbor's own resolved type name if it resolves
                cleanly, else ``None`` (an unresolved neighbor isn't a
                useful reference).

        Returns:
            str or None: The recommended type name, if any.
        """
        sh = shapes[i] if i < len(shapes) else None
        if not (sh and sh.points and len(sh.points) >= 2):
            return None
        prev_type = next((t for t in (resolved_type_for(j) for j in endpoint_index.get(
            self.snap_key(sh.points[0]), []) if j != i) if t), None)
        next_type = next((t for t in (resolved_type_for(j) for j in endpoint_index.get(
            self.snap_key(sh.points[-1]), []) if j != i) if t), None)
        return prev_type or next_type

    def prescan_unresolved(self):
        """Scan the source shapefiles for attribute combinations that
        *would* fall back to a default/fallback type if converted now --
        without running the full conversion -- so the user can review and
        assign types themselves first, per the product requirement that
        indeterminate data (e.g. section ``0``/``9999``, unrecognised
        material) should be surfaced for manual review rather than guessed
        silently.

        Checks: underground MV cables and overhead/other MV lines
        (material + section), and transformers (capacity). Distinct
        attribute combinations are grouped together (not one row per
        record) so a feeder with thousands of records still produces a
        short, reviewable list.

        Returns:
            dict: ``{"mv_underground_line": [group, ...], "mv_overhead_line": [group, ...],
            "mv_other_lines": [group, ...], "transformer": [group, ...]}``
            where each group is
            ``{"key": <tuple/str used for set_manual_overrides>, "label": str,
            "count": int, "example_ids": [str, ...], "current_default": str,
            "recommended_type": str or None, "recommended_confidence": str or None}``.
            ``recommended_type`` (line kinds only) is a neighbor-based
            suggestion -- the type shared by the lines immediately
            before/after this one on the same conductor run, when
            available -- meant to pre-fill the review dialog's choice,
            not to be applied silently; the user still confirms or
            changes it. ``recommended_confidence`` is
            ``"<votes>/<count>"``, how many of this group's records
            agreed on that suggestion. A kind's list is empty if every
            record of that kind already resolves cleanly.
        """
        result = {"mv_underground_line": [], "mv_overhead_line": [], "mv_other_lines": [], "transformer": []}
        c = self.cfg["line_type"]

        # --- underground MV cables ---
        groups = collections.OrderedDict()  # (sec, mat) -> {"count", "ids", "votes": Counter}
        for layer in self._layer_names("mv_underground_line"):
            recs, shapes = read_layer(self.shp_dir, layer, None)
            endpoint_index = self._build_endpoint_index(shapes)

            def resolved_type_for(i, recs=recs):
                r = recs[i]
                m = s(r.get(c["material_field"]))
                try:
                    sc = str(int(float(r.get(c["section_field"]) or 0)))
                except Exception:
                    sc = "0"
                return self._underground_cable_type_name(r, m, sc, strict=True)

            for i, rec in enumerate(recs):
                mat = s(rec.get(c["material_field"]))
                try:
                    sec = str(int(float(rec.get(c["section_field"]) or 0)))
                except Exception:
                    sec = "0"
                if self._underground_cable_type_name(rec, mat, sec, strict=True) is not None:
                    continue  # genuinely matched by section+material, nothing to review
                key = (sec, mat)
                g = groups.setdefault(key, {"count": 0, "ids": [], "votes": collections.Counter()})
                g["count"] += 1
                if len(g["ids"]) < 5:
                    g["ids"].append(self._record_id(rec))
                rec_i = self._neighbor_recommendation(i, shapes, endpoint_index, resolved_type_for)
                if rec_i:
                    g["votes"][rec_i] += 1

        for (sec, mat), g in groups.items():
            recommended_type, votes = (g["votes"].most_common(1) or [(None, 0)])[0]
            result["mv_underground_line"].append({
                "key": (sec, mat),
                "label": f"section={sec or '(empty)'}, material={mat or '(empty)'}",
                "count": g["count"],
                "example_ids": g["ids"],
                "current_default": c["uml_default"],
                "recommended_type": recommended_type,
                "recommended_confidence": f"{votes}/{g['count']}" if recommended_type else None,
            })

        # --- overhead / other MV conductor lines (same idea: attribute
        # combos whose resolved name isn't actually in the loaded type
        # library -- e.g. a covered-conductor section this project's
        # naming table doesn't have an entry for) ---
        kw = c["keywords"]

        def has(field, keys):
            return any(k in field for k in keys)

        def overhead_candidate(mat, sec, net):
            covered = has(net, kw["covered"]) or has(mat, kw["covered"])
            if covered:
                return c["covered_by_section"].get(sec, c["global_fallback"])
            if has(mat, kw["copper"]):
                return c["copper_by_section"].get(sec, c["global_fallback"])
            if has(mat, kw["cable"]):
                return c["cable_default"]
            return c["acsr_bare_by_section"].get(sec, c["global_fallback"])

        groups = collections.OrderedDict()
        for layer in self._layer_names("mv_overhead_line"):
            recs, shapes = read_layer(self.shp_dir, layer, None)
            endpoint_index = self._build_endpoint_index(shapes)

            def resolved_type_for(i, recs=recs):
                r = recs[i]
                m = s(r.get(c["material_field"]))
                n = s(r.get(c["nettype_field"]))
                try:
                    sc = str(int(float(r.get(c["section_field"]) or 0)))
                except Exception:
                    sc = "0"
                nm = overhead_candidate(m, sc, n)
                return nm if nm in self.typlne_id else None

            for i, rec in enumerate(recs):
                mat = s(rec.get(c["material_field"]))
                net = s(rec.get(c["nettype_field"]))
                try:
                    sec = str(int(float(rec.get(c["section_field"]) or 0)))
                except Exception:
                    sec = "0"
                name = overhead_candidate(mat, sec, net)
                if name in self.typlne_id:
                    continue
                key = (sec, mat)
                g = groups.setdefault(key, {"count": 0, "ids": [], "votes": collections.Counter()})
                g["count"] += 1
                if len(g["ids"]) < 5:
                    g["ids"].append(self._record_id(rec))
                rec_i = self._neighbor_recommendation(i, shapes, endpoint_index, resolved_type_for)
                if rec_i:
                    g["votes"][rec_i] += 1

        for (sec, mat), g in groups.items():
            recommended_type, votes = (g["votes"].most_common(1) or [(None, 0)])[0]
            result["mv_overhead_line"].append({
                "key": (sec, mat),
                "label": f"section={sec or '(empty)'}, material={mat or '(empty)'}",
                "count": g["count"],
                "example_ids": g["ids"],
                "current_default": c["global_fallback"],
                "recommended_type": recommended_type,
                "recommended_confidence": f"{votes}/{g['count']}" if recommended_type else None,
            })

        # --- other/miscellaneous MV lines (MLN): no material/section
        # signal to select from -- just "is the one configured default
        # actually usable", and if not, one type for the whole category ---
        if c["mln_default"] not in self.typlne_id:
            total = 0
            ids = []
            for layer in self._layer_names("mv_other_lines"):
                recs, _shapes = read_layer(self.shp_dir, layer, None)
                total += len(recs)
                for rec in recs[:5 - len(ids)]:
                    ids.append(self._record_id(rec))
            if total:
                result["mv_other_lines"].append({
                    "key": "__all__",
                    "label": f"all 'other MV line' records (no configured type in the library)",
                    "count": total,
                    "example_ids": ids,
                    "current_default": c["global_fallback"],
                    "recommended_type": None,
                    "recommended_confidence": None,
                })

        # --- transformers ---
        t = self.cfg["transformer"]
        groups = collections.OrderedDict()  # capacity -> {"count", "ids"}
        for layer in self._layer_names("mv_transformer"):
            recs, _shapes = read_layer(self.shp_dir, layer, None)
            for rec in recs:
                name = self.trafo_type_name(rec, dry_run=True)
                if name in self.typtr_id and name != t["fallback_type"]:
                    continue
                cap_raw = s(rec.get(t["capacity_field"]))
                try:
                    cap = str(int(float(cap_raw)))
                except Exception:
                    cap = cap_raw or "(empty)"
                g = groups.setdefault(cap, {"count": 0, "ids": []})
                g["count"] += 1
                if len(g["ids"]) < 5:
                    g["ids"].append(self._record_id(rec))
        for cap, g in groups.items():
            result["transformer"].append({
                "key": cap,
                "label": f"capacity={cap} kVA",
                "count": g["count"],
                "example_ids": g["ids"],
                "current_default": t["fallback_type"],
                "recommended_type": None,
                "recommended_confidence": None,
            })

        return result

    def build_report(self):
        """Return the end-of-conversion discrepancy report as a plain
        dict, independent of any file format.

        Returns:
            dict: ``{"summary": {category: count, ...}, "details": [...]}``
            where each ``details`` entry is ``{"category", "id", "message"}``
            (see :meth:`_record_issue`). ``summary`` is empty and
            ``details`` is ``[]`` when the conversion had no discrepancies
            or assumptions to report.
        """
        counts = collections.Counter(issue["category"] for issue in self.issues)
        return {"summary": dict(sorted(counts.items(), key=lambda kv: -kv[1])),
                "details": list(self.issues)}

    def write_report(self, out_path):
        """Write the discrepancy report to a companion ``.xlsx`` file next
        to the DGS deck (``<out_path stem>_ConversionReport.xlsx``).

        This runs unconditionally at the end of every :meth:`write` call
        -- including when there are zero discrepancies, in which case the
        report simply confirms that -- so a report always exists to check,
        rather than only appearing when something happens to go wrong.

        Args:
            out_path: The DGS workbook path just written; the report is
                placed alongside it.

        Returns:
            str: Path to the report file.
        """
        report = self.build_report()
        stem, _ = os.path.splitext(out_path)
        report_path = f"{stem}_ConversionReport.xlsx"
        try:
            rwb = openpyxl.Workbook()
            ws_sum = rwb.active
            ws_sum.title = "Summary"
            ws_sum.append(["Category", "Count", "Description"])
            category_help = {
                "transformer_series_default": "Loss-class (AB'/Normal) not stated in source data - default used",
                "transformer_insulation_default": "Insulation (oil/dry) not stated in source data - default used",
                "transformer_ambiguous_type": "Multiple type-library entries matched by capacity; could not disambiguate",
                "transformer_unknown_type": "No matching type-library entry found at all",
                "transformer_invalid_capacity": "Capacity missing, non-numeric, or zero in source data",
                "gps_skipped": "GPS/geographic coordinates could not be generated",
                "meta_injection_failed": "Non-critical metadata block failed to write",
                "no_terminal_coordinates": "No terminal coordinates available for diagrams",
            }
            if not report["summary"]:
                ws_sum.append(["(none)", 0, "No discrepancies or assumptions were recorded for this conversion"])
            for cat, cnt in report["summary"].items():
                ws_sum.append([cat, cnt, category_help.get(cat, cat.replace("corrupt_", "Corrupt ").replace("_", " "))])
            for col, w in zip("ABC", (32, 10, 70)):
                ws_sum.column_dimensions[col].width = w

            ws_det = rwb.create_sheet("Details")
            ws_det.append(["Category", "Record ID", "Message"])
            for issue in report["details"]:
                ws_det.append([issue["category"], issue["id"], issue["message"]])
            for col, w in zip("ABC", (32, 20, 100)):
                ws_det.column_dimensions[col].width = w

            rwb.save(report_path)
            logger.info("Discrepancy report saved: %s (%d issue(s))", report_path, len(report["details"]))
        except Exception as e:
            # The report is diagnostic, not the deliverable -- never let a
            # report-writing problem take down an otherwise-successful
            # conversion, but always surface it loudly.
            logger.error("Failed to write discrepancy report %s: %s", report_path, e, exc_info=True)
        return report_path

    # ------------------------------------------------------------------
    # summary
    # ------------------------------------------------------------------
    def summary(self):
        """Return a dict of DGS element counts for the completed
        conversion, suitable for display in the GUI's Summary dialog or
        the CLI's final report.

        Returns:
            dict: Element-type name -> count, plus ``T_<kind>`` entries
            breaking down terminal counts by connection kind.

        Raises:
            RuntimeError: If called before :meth:`build`.
        """
        if self._elm is None:
            raise RuntimeError("Converter.summary() called before build()")
        term_kinds = collections.Counter(self.term_kind.values())
        base = {
            "ElmLne": len(self._elm["lne"]),
            "ElmTr2": len(self._elm["tr2"]),
            "Elmlod": len(self._elm["lod"]),
            "ElmCoup": len(self._elm["coup"]),
            "RelFuse": len(self._elm["fuse"]),
            "ElmXnet": len(self._elm["xnet"]),
            "ElmFeeder": len(self._elm["feeder"]),
            "ElmShnt": len(self._elm.get("shnt", [])),
            "ElmTerm": len(self.terminals),
            "StaCubic": len(self.cubicles),
            "IntGrf": len(self.sheets.get("IntGrf", [])),
            "IntGrfcon": len(self.sheets.get("IntGrfcon", [])),
        }
        for k, v in term_kinds.items():
            base[f"T_{k}"] = v
        if self.issues:
            report = self.build_report()
            top = ", ".join(f"{cat} x{cnt}" for cat, cnt in list(report["summary"].items())[:5])
            report_path = getattr(self, "report_path", None)
            where = f" Full details in: {report_path}" if report_path else ""
            base["_warnings"] = base.get("_warnings", []) + [
                f"{len(self.issues)} discrepancy/assumption record(s) were logged during conversion "
                f"({top}).{where}"
            ]
        return base
