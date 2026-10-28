#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gui.qt_compat
=============
Resolves a single Qt binding (PySide6 preferred, PyQt5 fallback) once,
and re-exports the pieces the rest of :mod:`shp2dgs.gui` needs so no
other module has to repeat the try/except import dance.

Usage::

    from shp2dgs.gui.qt_compat import QtWidgets, QtCore, QtGui, Signal, Slot, GUI_AVAILABLE, QT_BINDING

    if not GUI_AVAILABLE:
        raise RuntimeError("No Qt binding installed")
"""

import logging

logger = logging.getLogger("shp2dgs.gui.qt_compat")

QT_BINDING = None
QtWidgets = QtCore = QtGui = None
Signal = Slot = None

try:
    from PySide6 import QtWidgets, QtCore, QtGui  # noqa: F401,F811
    QT_BINDING = "PySide6"
    Signal = QtCore.Signal
    Slot = QtCore.Slot
    logger.info("Using Qt binding: PySide6")
except ImportError:
    try:
        from PyQt5 import QtWidgets, QtCore, QtGui  # noqa: F401,F811
        QT_BINDING = "PyQt5"
        Signal = QtCore.pyqtSignal
        Slot = QtCore.pyqtSlot
        logger.info("Using Qt binding: PyQt5")
    except ImportError:
        QT_BINDING = None
        logger.warning("Neither PySide6 nor PyQt5 found - GUI disabled")

#: ``True`` if a usable Qt binding was imported successfully.
GUI_AVAILABLE = QT_BINDING is not None


def require_gui():
    """Raise a clear error if no Qt binding is available.

    Raises:
        RuntimeError: If neither PySide6 nor PyQt5 could be imported, with
            an actionable install hint.
    """
    if not GUI_AVAILABLE:
        raise RuntimeError(
            "No Qt binding is installed, so the graphical interface cannot "
            "start. Install one with: pip install PySide6  (or: pip install PyQt5)"
        )
