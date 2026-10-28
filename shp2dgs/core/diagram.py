#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
core.diagram
============
Builds a GUI-agnostic geographic diagram model directly from the
``IntGrf``/``IntGrfcon`` sheets produced by
:meth:`~shp2dgs.core.converter.Converter.emit`.

This module intentionally has **no Qt dependency** so it can be unit
tested, or reused by a future non-Qt renderer (e.g. a headless PNG export
CLI command), independently of :mod:`shp2dgs.gui`. The GUI's
``DiagramView`` widget consumes :class:`DiagramModel` and does the actual
Qt drawing/zooming/panning/export.
"""

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from shp2dgs.core.constants import BUSBAR_SYMBOLS, DIAGRAM_ELEMENT_STYLE, DIAGRAM_TERMINAL_STYLE

logger = logging.getLogger("shp2dgs.diagram")


@dataclass
class DiagramSymbol:
    """A single drawable node in the diagram (a terminal or an element).

    Attributes:
        obj_id: The DGS object id this symbol represents (``pDataObj``).
        loc_name: Display name (``IntGrf.loc_name``).
        x: Centre X, in diagram units (already page-margin shifted).
        y: Centre Y, in diagram units.
        width: Symbol width.
        height: Symbol height.
        symbol: DIgSILENT graphic symbol name (``sSymNam``), e.g.
            ``"PointTerm"``, ``"d_tr2"``.
        is_terminal: ``True`` for ``ElmTerm`` nodes, ``False`` for
            elements (line/transformer/switch/fuse/load/source).
        kind: Semantic kind used to pick a colour/label -- terminal kind
            (``line_junction``, ``switch``, ...) or element symbol
            (``d_lin``, ``d_tr2``, ...).
    """

    obj_id: int
    loc_name: str
    x: float
    y: float
    width: float
    height: float
    symbol: str
    is_terminal: bool
    kind: str

    @property
    def is_busbar(self):
        """Whether this symbol should be drawn as an elongated bar."""
        return self.symbol in BUSBAR_SYMBOLS

    @property
    def style(self):
        """Return ``{"label": ..., "color": ...}`` for this symbol."""
        table = DIAGRAM_TERMINAL_STYLE if self.is_terminal else DIAGRAM_ELEMENT_STYLE
        return table.get(self.kind, {"label": self.kind or "Unknown", "color": "#888888"})


@dataclass
class DiagramConnection:
    """A single ``IntGrfcon`` edge between an element and a terminal.

    Attributes:
        elem_x, elem_y: Element-side endpoint.
        term_x, term_y: Terminal-side endpoint.
        points: For line elements, every intermediate GIS vertex between
            ``(elem_x, elem_y)`` and ``(term_x, term_y)`` (inclusive of
            both ends), in the same diagram-coordinate space -- lets the
            renderer draw the actual routed path instead of a straight
            symbol-to-symbol segment. ``None`` for non-line elements, or
            when vertex data wasn't available.
    """

    elem_x: float
    elem_y: float
    term_x: float
    term_y: float
    points: Optional[List[Tuple[float, float]]] = None


@dataclass
class DiagramModel:
    """The full diagram: all symbols plus all connections, and the
    overall bounding box for framing/zoom-to-fit.

    Attributes:
        symbols: All drawable nodes (terminals + elements).
        connections: All drawable edges.
        bounds: ``(min_x, min_y, max_x, max_y)`` covering every symbol,
            expanded slightly for padding. ``None`` if there is nothing
            to draw (empty network).
        warnings: Non-fatal issues encountered while building the model
            (e.g. a connection referencing a symbol that was not found),
            surfaced by the GUI as a status-bar message rather than a
            blocking error.
    """

    symbols: List[DiagramSymbol] = field(default_factory=list)
    connections: List[DiagramConnection] = field(default_factory=list)
    bounds: Optional[Tuple[float, float, float, float]] = None
    warnings: List[str] = field(default_factory=list)

    @property
    def is_empty(self):
        return not self.symbols


def build_diagram_model(headers, sheets, elem_kind_hint=None, term_kind_hint=None, line_points_hint=None):
    """Build a :class:`DiagramModel` from the ``IntGrf``/``IntGrfcon``
    sheets of a converted DGS deck.

    Args:
        headers: The ``Converter.headers`` dict (sheet name -> column
            list), used to resolve column indices defensively instead of
            hard-coding positions.
        sheets: The ``Converter.sheets`` dict (sheet name -> list of rows).
        elem_kind_hint: Optional ``{object_id: kind}`` mapping (e.g.
            ``Converter.elem_kind``) giving a richer classification than
            the raw ``sSymNam`` written to the DGS file -- e.g.
            distinguishing an LV line from an MV line even though both
            must use the same valid ``"d_lin"`` DIgSILENT symbol in the
            actual DGS output. When given and a symbol's object id is
            present, this overrides the symbol-name-derived kind purely
            for diagram colouring; the DGS data itself is untouched.
        term_kind_hint: Optional ``{object_id: kind}`` mapping (e.g.
            ``Converter.term_kind``) giving the true terminal kind
            (``line_junction``, ``lv_line_junction``, ...) instead of
            guessing it from the terminal's graphic symbol name.
        line_points_hint: Optional ``{object_id: [(x, y), ...]}`` mapping
            (e.g. ``Converter.line_points_diagram_xy``) of true GIS
            polyline vertices per line element, already in this same
            diagram-coordinate space. When given, line connections are
            drawn following the actual routed path instead of a straight
            symbol-to-symbol segment.

    Returns:
        DiagramModel: Always returned, even for an empty/missing
        ``IntGrf`` sheet (in which case :attr:`DiagramModel.is_empty` is
        ``True`` and a warning is recorded).
    """
    elem_kind_hint = elem_kind_hint or {}
    term_kind_hint = term_kind_hint or {}
    line_points_hint = line_points_hint or {}
    model = DiagramModel()
    intgrf_hdr = headers.get("IntGrf")
    intgrf_rows = sheets.get("IntGrf")
    if not intgrf_hdr or not intgrf_rows:
        model.warnings.append("No IntGrf graphic data available (graphics may be disabled, or the network is empty).")
        logger.warning("build_diagram_model: no IntGrf data")
        return model

    try:
        col = {name.split("(")[0]: i for i, name in enumerate(intgrf_hdr)}
        required = ["pDataObj", "rCenterX", "rCenterY", "rSizeX", "rSizeY", "sSymNam", "loc_name"]
        missing = [c for c in required if c not in col]
        if missing:
            model.warnings.append(f"IntGrf sheet is missing expected columns: {missing}")
            logger.error("IntGrf missing columns: %s", missing)
            return model

        by_obj_id = {}
        graphic_id_to_obj_id = {}
        has_graphic_id_col = "ID" in col
        xs, ys = [], []
        for row in intgrf_rows:
            try:
                obj_id = row[col["pDataObj"]]
                loc_name = row[col["loc_name"]]
                cx = float(row[col["rCenterX"]])
                cy = float(row[col["rCenterY"]])
                sx = float(row[col["rSizeX"]])
                sy = float(row[col["rSizeY"]])
                sym = row[col["sSymNam"]] or ""
            except (TypeError, ValueError, IndexError) as e:
                model.warnings.append(f"Skipped a malformed IntGrf row: {e}")
                logger.warning("Skipping malformed IntGrf row %s: %s", row, e)
                continue
            if has_graphic_id_col:
                graphic_id_to_obj_id[row[col["ID"]]] = obj_id

            is_terminal = sym not in DIAGRAM_ELEMENT_STYLE
            if is_terminal:
                # Terminal symbols are DIgSILENT graphic symbol names
                # (PointTerm, TermStrip, ...) which don't map 1:1 to our
                # DIAGRAM_TERMINAL_STYLE kinds; prefer the precise kind
                # from term_kind_hint (available when called right after
                # a conversion in the same process), else fall back to a
                # best-effort guess from the symbol name alone.
                kind = term_kind_hint.get(obj_id) or _guess_terminal_kind(sym)
            else:
                kind = elem_kind_hint.get(obj_id, sym)
            symbol_obj = DiagramSymbol(
                obj_id=obj_id, loc_name=str(loc_name), x=cx, y=cy,
                width=sx, height=sy, symbol=sym, is_terminal=is_terminal, kind=kind,
            )
            by_obj_id[obj_id] = symbol_obj
            model.symbols.append(symbol_obj)
            xs.extend([cx - sx / 2, cx + sx / 2])
            ys.extend([cy - sy / 2, cy + sy / 2])

        if xs and ys:
            pad = max((max(xs) - min(xs)) * 0.03, (max(ys) - min(ys)) * 0.03, 1.0)
            model.bounds = (min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad)

        intgrfcon_hdr = headers.get("IntGrfcon")
        intgrfcon_rows = sheets.get("IntGrfcon", [])
        if intgrfcon_hdr and intgrfcon_rows:
            # Column names are "rX:0(r)", "rX:1(r)", "rY:0(r)", "rY:1(r)"
            # -- strip the trailing "(type)" suffix but keep the ":N" part
            # (it is part of the DGS naming scheme, not our type syntax).
            bare = [h.split("(")[0] for h in intgrfcon_hdr]
            idx_ex = bare.index("rX:1") if "rX:1" in bare else None
            idx_ey = bare.index("rY:1") if "rY:1" in bare else None
            idx_tx = bare.index("rX:0") if "rX:0" in bare else None
            idx_ty = bare.index("rY:0") if "rY:0" in bare else None
            idx_fold = bare.index("fold_id") if "fold_id" in bare else None
            if None in (idx_ex, idx_ey, idx_tx, idx_ty):
                model.warnings.append("IntGrfcon sheet is missing expected coordinate columns; connections not drawn.")
            else:
                for row in intgrfcon_rows:
                    try:
                        points = None
                        if idx_fold is not None and line_points_hint:
                            elem_obj_id = graphic_id_to_obj_id.get(row[idx_fold])
                            pts = line_points_hint.get(elem_obj_id)
                            if pts and len(pts) >= 2:
                                points = pts
                        model.connections.append(DiagramConnection(
                            elem_x=float(row[idx_tx]), elem_y=float(row[idx_ty]),
                            term_x=float(row[idx_ex]), term_y=float(row[idx_ey]),
                            points=points,
                        ))
                    except (TypeError, ValueError, IndexError) as e:
                        logger.warning("Skipping malformed IntGrfcon row %s: %s", row, e)
        else:
            model.warnings.append("No IntGrfcon connection data available.")

        logger.info(
            "Diagram model built: %d symbols, %d connections, bounds=%s",
            len(model.symbols), len(model.connections), model.bounds,
        )
        return model
    except Exception as e:
        logger.error("Failed to build diagram model: %s", e, exc_info=True)
        model.warnings.append(f"Diagram build error: {e}")
        return model


def _guess_terminal_kind(symbol_name):
    """Best-effort mapping from a raw DIgSILENT terminal symbol name back
    to one of our semantic terminal kinds, purely for diagram colouring.
    Falls back to ``"line_junction"`` (the most common/neutral kind).
    """
    guess = {
        "TermStrip": "transformer_hv",
        "Busbar": "transformer_lv",
        "BusbarDouble": "transformer_lv",
    }
    return guess.get(symbol_name, "line_junction")
