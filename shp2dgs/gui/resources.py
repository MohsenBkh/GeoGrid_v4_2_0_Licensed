#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gui.resources
=============
Loads the application icon and splash-screen artwork.

If ``assets/icon.png`` / ``assets/splash.png`` exist (ship your own logo
there), they are used as-is. Otherwise a simple placeholder is drawn on
the fly with ``QPainter`` so the application always has *some* icon/splash
even before a real logo is supplied -- this mirrors (and keeps working
exactly like) the original script's ``_build_splash_pixmap`` behaviour.
"""

import logging
import os

from shp2dgs.core.config_io import resource_path
from shp2dgs.core.constants import APP_NAME, APP_TAGLINE, APP_VERSION
from shp2dgs.gui.qt_compat import QtCore, QtGui, require_gui

logger = logging.getLogger("shp2dgs.gui.resources")

ICON_FILENAME = os.path.join("assets", "icon.png")
SPLASH_FILENAME = os.path.join("assets", "splash.png")


def icon_file_path():
    """Return the path to the shipped ``assets/icon.png`` if present,
    else ``None``."""
    path = resource_path(ICON_FILENAME)
    return path if os.path.isfile(path) else None


def splash_file_path():
    """Return the path to the shipped ``assets/splash.png`` if present,
    else ``None``."""
    path = resource_path(SPLASH_FILENAME)
    return path if os.path.isfile(path) else None


def build_default_icon_pixmap(size=256):
    """Draw a simple placeholder application icon with ``QPainter``.

    Args:
        size: Square icon size in pixels.

    Returns:
        QPixmap: The rendered icon.
    """
    require_gui()
    pix = QtGui.QPixmap(size, size)
    pix.fill(QtCore.Qt.transparent)
    painter = QtGui.QPainter(pix)
    try:
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(QtGui.QColor("#102a43"))
        painter.drawRoundedRect(0, 0, size, size, size * 0.18, size * 0.18)
        painter.setBrush(QtGui.QColor("#f2b134"))
        r = size * 0.34
        painter.drawEllipse(QtCore.QPointF(size / 2, size * 0.42), r, r)
        bolt = QtGui.QPolygonF([
            QtCore.QPointF(size * 0.56, size * 0.28),
            QtCore.QPointF(size * 0.40, size * 0.52),
            QtCore.QPointF(size * 0.49, size * 0.52),
            QtCore.QPointF(size * 0.42, size * 0.72),
            QtCore.QPointF(size * 0.62, size * 0.44),
            QtCore.QPointF(size * 0.51, size * 0.44),
        ])
        painter.setBrush(QtGui.QColor("#102a43"))
        painter.drawPolygon(bolt)
    finally:
        painter.end()
    return pix


def build_default_splash_pixmap():
    """Draw the placeholder splash-screen artwork with ``QPainter``: a
    logo mark, the application title, a short description and the
    version string. Used whenever ``assets/splash.png`` is not shipped.

    Returns:
        QPixmap: The rendered splash artwork (560x340).
    """
    require_gui()
    w, h = 560, 340
    pix = QtGui.QPixmap(w, h)
    pix.fill(QtGui.QColor("#102a43"))
    painter = QtGui.QPainter(pix)
    try:
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(QtGui.QColor("#f2b134"))
        painter.drawEllipse(w // 2 - 42, 28, 84, 84)
        bolt = QtGui.QPolygon([
            QtCore.QPoint(w // 2 + 8, 40), QtCore.QPoint(w // 2 - 18, 74),
            QtCore.QPoint(w // 2 - 2, 74), QtCore.QPoint(w // 2 - 10, 100),
            QtCore.QPoint(w // 2 + 20, 62), QtCore.QPoint(w // 2 + 2, 62),
        ])
        painter.setBrush(QtGui.QColor("#102a43"))
        painter.drawPolygon(bolt)
        painter.setPen(QtGui.QColor("#ffffff"))
        painter.setFont(QtGui.QFont("Tahoma", 17, QtGui.QFont.Bold))
        painter.drawText(QtCore.QRect(0, 130, w, 36), QtCore.Qt.AlignCenter, APP_NAME)
        painter.setPen(QtGui.QColor("#bcccdc"))
        painter.setFont(QtGui.QFont("Tahoma", 10))
        painter.drawText(
            QtCore.QRect(24, 172, w - 48, 70),
            QtCore.Qt.AlignHCenter | QtCore.Qt.AlignTop | QtCore.Qt.TextWordWrap,
            f"{APP_TAGLINE}\nfor medium-voltage distribution networks")
        painter.setPen(QtGui.QColor("#829ab1"))
        painter.setFont(QtGui.QFont("Consolas", 9))
        painter.drawText(QtCore.QRect(0, h - 34, w, 20), QtCore.Qt.AlignCenter,
                          f"Version {APP_VERSION}  \u00b7  Loading\u2026")
    finally:
        painter.end()
    return pix


#: Maximum splash-screen dimensions on screen (px). A shipped
#: assets/splash.png can be any resolution (e.g. a large marketing
#: graphic); it is scaled down to fit within this box, preserving aspect
#: ratio, so it never dwarfs the user's screen. The drawn placeholder is
#: already within this size, so this is a no-op for it.
MAX_SPLASH_WIDTH = 640
MAX_SPLASH_HEIGHT = 480


def load_app_icon():
    """Load the application icon: shipped ``assets/icon.png`` if present,
    otherwise the drawn placeholder.

    Returns:
        QIcon: Ready to pass to ``QApplication.setWindowIcon`` /
        ``QMainWindow.setWindowIcon``.
    """
    require_gui()
    path = icon_file_path()
    if path:
        icon = QtGui.QIcon(path)
        if not icon.isNull():
            return icon
        logger.warning("assets/icon.png exists but could not be loaded; using placeholder icon")
    return QtGui.QIcon(build_default_icon_pixmap())


def load_splash_pixmap():
    """Load the splash-screen pixmap: shipped ``assets/splash.png`` if
    present, otherwise the drawn placeholder. A shipped image larger than
    :data:`MAX_SPLASH_WIDTH`/:data:`MAX_SPLASH_HEIGHT` is scaled down
    (preserving aspect ratio) so it fits comfortably on screen.

    Returns:
        QPixmap: Ready to pass to ``QSplashScreen``.
    """
    require_gui()
    path = splash_file_path()
    if path:
        pix = QtGui.QPixmap(path)
        if not pix.isNull():
            if pix.width() > MAX_SPLASH_WIDTH or pix.height() > MAX_SPLASH_HEIGHT:
                pix = pix.scaled(MAX_SPLASH_WIDTH, MAX_SPLASH_HEIGHT,
                                  QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
            return pix
        logger.warning("assets/splash.png exists but could not be loaded; using placeholder splash")
    return build_default_splash_pixmap()
