#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gui.widgets.diagram_view
=========================
Renders a :class:`~shp2dgs.core.diagram.DiagramModel` in a
``QGraphicsView`` with mouse-wheel zoom, click-drag pan, and PNG/SVG
export. This widget is purely a *renderer* -- all the geometry and
element/terminal classification comes from :mod:`shp2dgs.core.diagram`,
so this file only concerns itself with Qt drawing.
"""

import logging

from shp2dgs.core.constants import DIAGRAM_ELEMENT_STYLE
from shp2dgs.gui.qt_compat import QtCore, QtGui, QtWidgets, require_gui

logger = logging.getLogger("shp2dgs.gui.diagram_view")

require_gui()


class _PannableGraphicsView(QtWidgets.QGraphicsView):
    """QGraphicsView with mouse-wheel zoom and click-drag panning."""

    def __init__(self, scene, parent=None):
        super().__init__(scene, parent)
        self.setRenderHints(QtGui.QPainter.Antialiasing | QtGui.QPainter.TextAntialiasing)
        self.setDragMode(QtWidgets.QGraphicsView.ScrollHandDrag)
        self.setTransformationAnchor(QtWidgets.QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QtWidgets.QGraphicsView.AnchorViewCenter)
        # A large feeder can produce 100k+ graphics items (one per
        # terminal/element). FullViewportUpdate repaints the ENTIRE
        # viewport on every scene change, and the scene's default BSP-tree
        # indexing is expensive to build/maintain for that many items --
        # together these can make the view take a very long time to
        # become responsive on first load, which looks like a blank/frozen
        # page even though the data loaded correctly. The diagram is
        # effectively static (nothing moves after set_model()), so a
        # cheaper update mode plus disabling the index (recommended by Qt
        # for large mostly-static scenes) trades away fast item lookups we
        # don't need for much faster paint/build times.
        self.setViewportUpdateMode(QtWidgets.QGraphicsView.SmartViewportUpdate)
        self.setBackgroundBrush(QtGui.QColor("#f4f6f8"))
        self._zoom = 1.0

    def wheelEvent(self, event):
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        new_zoom = self._zoom * factor
        if 0.02 <= new_zoom <= 60:
            self._zoom = new_zoom
            self.scale(factor, factor)
        event.accept()

    def reset_zoom(self):
        self._zoom = 1.0


class DiagramView(QtWidgets.QWidget):
    """Diagram Preview tab content: toolbar (zoom/fit/export) + graphics
    canvas.

    Usage::

        view = DiagramView()
        view.set_model(diagram_model)   # from shp2dgs.core.diagram
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._model = None
        self._build_ui()

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        toolbar = QtWidgets.QToolBar()
        toolbar.setIconSize(QtCore.QSize(18, 18))
        act_zoom_in = toolbar.addAction("Zoom In (+)")
        act_zoom_out = toolbar.addAction("Zoom Out (\u2212)")
        act_fit = toolbar.addAction("Fit to Window")
        toolbar.addSeparator()
        act_export_png = toolbar.addAction("Export PNG\u2026")
        act_export_svg = toolbar.addAction("Export SVG\u2026")
        layout.addWidget(toolbar)
        self.toolbar = toolbar
        act_zoom_in.triggered.connect(lambda: self._zoom_by(1.25))
        act_zoom_out.triggered.connect(lambda: self._zoom_by(0.8))
        act_fit.triggered.connect(self.fit_to_window)
        act_export_png.triggered.connect(self.export_png_dialog)
        act_export_svg.triggered.connect(self.export_svg_dialog)

        self.scene = QtWidgets.QGraphicsScene()
        # Recommended by Qt for scenes with a very large number of items
        # that are rarely (if ever) removed/moved after being added --
        # skips the cost of building/maintaining a BSP tree we don't
        # benefit from here (see __init__ comment on FullViewportUpdate
        # for the full context: without this, a large feeder's diagram
        # can take a very long time to become responsive).
        self.scene.setItemIndexMethod(QtWidgets.QGraphicsScene.NoIndex)
        self.view = _PannableGraphicsView(self.scene)
        layout.addWidget(self.view, 1)

        self.status_label = QtWidgets.QLabel("No diagram loaded yet. Run a conversion to see the network here.")
        self.status_label.setStyleSheet("color:#666; padding:4px;")
        layout.addWidget(self.status_label)

    # ------------------------------------------------------------------
    def _zoom_by(self, factor):
        self.view.scale(factor, factor)

    def fit_to_window(self):
        """Fit the whole diagram in the viewport, preserving aspect ratio."""
        if self.scene.itemsBoundingRect().isEmpty():
            return
        self.view.fitInView(self.scene.itemsBoundingRect(), QtCore.Qt.KeepAspectRatio)

    # ------------------------------------------------------------------
    def set_model(self, model):
        """Render a new :class:`~shp2dgs.core.diagram.DiagramModel`.

        Args:
            model: The model to render. If empty (``model.is_empty``),
                the canvas is cleared and a helpful status message is
                shown instead of an error.
        """
        if getattr(self, "_rendering", False):
            # A large scene pumps the event loop periodically (see
            # _draw_symbols/_draw_connections) to stay visually responsive
            # while it builds -- if that lets a stray click re-enter this
            # method before the first call finishes, the two renders would
            # race on the same scene and could easily produce exactly the
            # "briefly shows a message, then nothing happens" hang this is
            # guarding against. Toolbar actions are also disabled below for
            # the same reason.
            logger.warning("set_model() called while a previous render was still in progress; ignoring")
            return
        self._rendering = True
        self.toolbar.setEnabled(False)
        try:
            self._model = model
            self.scene.clear()

            if model is None or model.is_empty:
                reason = model.warnings[0] if model and model.warnings else "Enable graphics and run a conversion first."
                self.status_label.setText(f"No graphic data to display. {reason}")
                self._show_empty_state(reason)
                return

            large_scene = len(model.symbols) > 20000
            if large_scene:
                # Antialiasing ~100k+ primitives makes every pan/zoom
                # repaint noticeably slower; for a diagram this size, a
                # crisp render matters far less than staying responsive.
                self.view.setRenderHint(QtGui.QPainter.Antialiasing, False)
                self.status_label.setText(
                    f"Rendering {len(model.symbols):,} symbols\u2026 this may take a moment for a large network.")
                self._pump_events()
            self._draw_connections(model)
            self._draw_symbols(model)
            note = ""
            if model.warnings:
                note = "  \u26a0 " + "; ".join(model.warnings)
            self.status_label.setText(
                f"{len(model.symbols)} symbols, {len(model.connections)} connections.{note}"
            )
            self.fit_to_window()
        except Exception as e:
            logger.error("Failed to render diagram: %s", e, exc_info=True)
            self.status_label.setText(f"\u26a0 Failed to render diagram: {e}")
        finally:
            self.toolbar.setEnabled(True)
            self._rendering = False

    @staticmethod
    def _pump_events():
        """Keep the window painting/responsive during a long render
        without processing new user input (clicks, key presses) -- the
        latter is what could trigger the reentrancy guarded against in
        :meth:`set_model`."""
        QtWidgets.QApplication.processEvents(QtCore.QEventLoop.ExcludeUserInputEvents)

    def _show_empty_state(self, reason):
        """Draw a large, centered placeholder directly in the scene so an
        empty diagram is unmistakably "nothing was converted, here's why"
        rather than looking identical to a rendering bug -- a small status
        label above an otherwise totally blank view is easy to miss."""
        text_item = self.scene.addText("No diagram to show")
        font = text_item.font()
        font.setPointSize(16)
        font.setBold(True)
        text_item.setFont(font)
        text_item.setDefaultTextColor(QtGui.QColor("#9aa5b1"))
        detail_item = self.scene.addText(reason)
        detail_font = detail_item.font()
        detail_font.setPointSize(10)
        detail_item.setFont(detail_font)
        detail_item.setDefaultTextColor(QtGui.QColor("#b0b8c1"))
        tb = text_item.boundingRect()
        db = detail_item.boundingRect()
        text_item.setPos(-tb.width() / 2, -tb.height())
        detail_item.setPos(-db.width() / 2, 4)
        self.scene.setSceneRect(-200, -100, 400, 200)
        self.view.resetTransform()
        self.view.centerOn(0, 0)

    def _draw_connections(self, model):
        pen = QtGui.QPen(QtGui.QColor("#9aa5b1"))
        pen.setWidthF(0.05)
        n = len(model.connections)
        drawn_polylines = set()  # id(con.points) already drawn, to avoid double-drawing a line's path (it has one IntGrfcon row per end, both carrying the same vertex list)
        for i, con in enumerate(model.connections):
            # Qt's Y axis grows downward; DGS/GIS Y grows upward, so flip.
            # A 2-point "polyline" is identical to a straight line -- since
            # line segmentation (Converter._split_line_into_segments) means
            # most segments' own vertex list is trivially just their own
            # two endpoints, only bother with the pricier multi-segment
            # QPainterPath when there's a genuine bend (>2 points) to show;
            # otherwise use the much cheaper addLine for the same result.
            if con.points and len(con.points) > 2:
                key = id(con.points)
                if key not in drawn_polylines:
                    drawn_polylines.add(key)
                    path = QtGui.QPainterPath()
                    x0, y0 = con.points[0]
                    path.moveTo(x0, -y0)
                    for x, y in con.points[1:]:
                        path.lineTo(x, -y)
                    self.scene.addPath(path, pen)
            else:
                self.scene.addLine(con.elem_x, -con.elem_y, con.term_x, -con.term_y, pen)
            if n > 20000 and i % 5000 == 0:
                self._pump_events()

    def _draw_symbols(self, model):
        # One QPen/QBrush per distinct style, reused across every symbol
        # of that kind, instead of allocating new ones per item -- a real
        # cost at 100k+ items.
        pens, brushes = {}, {}

        def style_pen_brush(style):
            key = style["color"]
            if key not in pens:
                color = QtGui.QColor(key)
                brushes[key] = QtGui.QBrush(color)
                pen = QtGui.QPen(color.darker(140))
                pen.setWidthF(0.03)
                pens[key] = pen
            return pens[key], brushes[key]

        n = len(model.symbols)
        for i, sym in enumerate(model.symbols):
            style = sym.style
            pen, brush = style_pen_brush(style)
            x = sym.x - sym.width / 2
            y = -(sym.y + sym.height / 2)  # flip Y, and offset for top-left rect origin
            if sym.is_busbar or not sym.is_terminal:
                item = self.scene.addRect(x, y, sym.width, sym.height, pen, brush)
            else:
                item = self.scene.addEllipse(x, y, sym.width, sym.height, pen, brush)
            item.setToolTip(f"{style['label']}: {sym.loc_name}\nsymbol={sym.symbol}")
            item.setZValue(1)
            if n > 20000 and i % 5000 == 0:
                self._pump_events()

    # ------------------------------------------------------------------
    def export_png_dialog(self):
        """Prompt for a destination and export the current view as PNG."""
        if self._model is None or self._model.is_empty:
            QtWidgets.QMessageBox.information(self, "Nothing to export", "There is no diagram to export yet.")
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Export diagram as PNG", "diagram.png",
                                                          "PNG image (*.png)")
        if not path:
            return
        try:
            self.export_png(path)
            QtWidgets.QMessageBox.information(self, "Exported", f"Diagram exported to:\n{path}")
        except Exception as e:
            logger.error("PNG export failed: %s", e, exc_info=True)
            QtWidgets.QMessageBox.critical(self, "Export failed", f"Could not export PNG:\n{e}")

    def export_svg_dialog(self):
        """Prompt for a destination and export the current view as SVG."""
        if self._model is None or self._model.is_empty:
            QtWidgets.QMessageBox.information(self, "Nothing to export", "There is no diagram to export yet.")
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Export diagram as SVG", "diagram.svg",
                                                          "SVG image (*.svg)")
        if not path:
            return
        try:
            self.export_svg(path)
            QtWidgets.QMessageBox.information(self, "Exported", f"Diagram exported to:\n{path}")
        except Exception as e:
            logger.error("SVG export failed: %s", e, exc_info=True)
            QtWidgets.QMessageBox.critical(self, "Export failed", f"Could not export SVG:\n{e}")

    def export_png(self, path, width=2000):
        """Render the full scene (not just the current viewport) to a PNG
        file at ``path``.

        Args:
            path: Destination ``.png`` path.
            width: Output image width in pixels; height is derived from
                the scene's aspect ratio.

        Raises:
            ValueError: If there is nothing to export (empty scene).
            OSError: If the file cannot be written.
        """
        rect = self.scene.itemsBoundingRect()
        if rect.isEmpty():
            raise ValueError("Diagram is empty - nothing to export")
        aspect = rect.height() / rect.width() if rect.width() else 1.0
        height = max(1, int(width * aspect))
        image = QtGui.QImage(width, height, QtGui.QImage.Format_ARGB32)
        image.fill(QtGui.QColor("#ffffff"))
        painter = QtGui.QPainter(image)
        try:
            painter.setRenderHint(QtGui.QPainter.Antialiasing)
            self.scene.render(painter, QtCore.QRectF(0, 0, width, height), rect)
        finally:
            painter.end()
        if not image.save(path, "PNG"):
            raise OSError(f"Failed to save PNG image to {path}")
        logger.info("Diagram exported to PNG: %s (%dx%d)", path, width, height)

    def export_svg(self, path):
        """Render the full scene (not just the current viewport) to an
        SVG file at ``path``.

        Args:
            path: Destination ``.svg`` path.

        Raises:
            ImportError: If the Qt SVG module is not available (some
                minimal Qt installs omit it).
            ValueError: If there is nothing to export (empty scene).
        """
        try:
            from shp2dgs.gui.qt_compat import QT_BINDING
            if QT_BINDING == "PySide6":
                from PySide6.QtSvg import QSvgGenerator
            else:
                from PyQt5.QtSvg import QSvgGenerator
        except ImportError as e:
            raise ImportError(
                "SVG export requires the Qt SVG module. Install it with: "
                "pip install PySide6-Addons  (PySide6)  or  pip install PyQtSvg-compatible PyQt5 build"
            ) from e

        rect = self.scene.itemsBoundingRect()
        if rect.isEmpty():
            raise ValueError("Diagram is empty - nothing to export")
        generator = QSvgGenerator()
        generator.setFileName(path)
        generator.setSize(QtCore.QSize(int(rect.width() * 10), int(rect.height() * 10)))
        generator.setViewBox(QtCore.QRectF(0, 0, rect.width() * 10, rect.height() * 10))
        generator.setTitle("SHP\u2192DGS Network Diagram")
        painter = QtGui.QPainter(generator)
        try:
            painter.setRenderHint(QtGui.QPainter.Antialiasing)
            self.scene.render(painter, QtCore.QRectF(0, 0, rect.width() * 10, rect.height() * 10), rect)
        finally:
            painter.end()
        logger.info("Diagram exported to SVG: %s", path)
