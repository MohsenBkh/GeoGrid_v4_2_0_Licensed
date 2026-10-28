#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gui.app
=======
GUI application bootstrap: creates the ``QApplication``, shows the splash
screen for at least a couple of seconds while "loading" (mirrors the
original script's UX), then shows the :class:`~shp2dgs.gui.main_window.MainWindow`.
"""

import logging
import sys

from shp2dgs.core.constants import APP_VERSION
from shp2dgs.gui.qt_compat import QtCore, QtGui, QtWidgets, require_gui
from shp2dgs.gui.resources import load_app_icon, load_splash_pixmap

logger = logging.getLogger("shp2dgs.gui.app")

#: Minimum splash-screen display time, in milliseconds. The requirement
#: is "at least 2 seconds"; default a little above that so the loading
#: progress bar has time to visibly animate.
DEFAULT_SPLASH_DURATION_MS = 2200


def _show_splash(app, duration_ms=DEFAULT_SPLASH_DURATION_MS):
    """Show the startup splash for ``duration_ms`` while keeping the UI
    responsive, with a simple animated loading progress bar overlaid.

    Args:
        app: The running ``QApplication``.
        duration_ms: Minimum time to display the splash, in milliseconds
            (must be >= 2000 to satisfy the "at least 2 seconds" product
            requirement; smaller values are clamped up).

    Returns:
        None. Any failure while building/showing the splash is logged and
        swallowed -- a missing splash must never prevent the application
        from starting.
    """
    duration_ms = max(2000, duration_ms)
    try:
        pixmap = load_splash_pixmap()
        splash = QtWidgets.QSplashScreen(pixmap)
        splash.setWindowFlag(QtCore.Qt.WindowStaysOnTopHint, True)

        bar = QtWidgets.QProgressBar(splash)
        bar.setRange(0, 100)
        bar.setTextVisible(False)
        bar.setFixedSize(pixmap.width() - 48, 10)
        bar.move(24, pixmap.height() - 18)
        bar.setStyleSheet(
            "QProgressBar{background:#1c3a56; border-radius:5px;}"
            "QProgressBar::chunk{background:#f2b134; border-radius:5px;}"
        )

        splash.show()
        app.processEvents()

        loop = QtCore.QEventLoop()
        steps = 40
        interval = max(1, duration_ms // steps)

        state = {"i": 0}

        def tick():
            state["i"] += 1
            bar.setValue(min(100, int(state["i"] * 100 / steps)))
            if state["i"] >= steps:
                timer.stop()
                loop.quit()

        timer = QtCore.QTimer()
        timer.timeout.connect(tick)
        timer.start(interval)
        loop.exec()
        splash.close()
    except Exception as e:
        logger.warning("Splash screen skipped due to error: %s", e)


def run_gui(config_path, template_path, splash_duration_ms=DEFAULT_SPLASH_DURATION_MS):
    """Start the full GUI application: splash screen, then the main
    window, then hand control to the Qt event loop.

    Args:
        config_path: Path to ``config.json``.
        template_path: Path to ``dgs_template.json``.
        splash_duration_ms: Minimum splash-screen display time.

    Returns:
        int: The process exit code from ``QApplication.exec()``.

    Raises:
        RuntimeError: If no Qt binding is installed.
    """
    require_gui()
    # Local import: MainWindow itself requires Qt to be present, and we
    # want require_gui() above to raise a friendly error first if it
    # is not, rather than an ImportError deep in main_window's imports.
    from shp2dgs.gui.main_window import MainWindow

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    app.setApplicationName("SHP2DGS")
    app.setApplicationVersion(APP_VERSION)
    app.setWindowIcon(load_app_icon())

    _show_splash(app, splash_duration_ms)

    # --- License gate (GUI) ------------------------------------------
    # Shown after the splash screen, before MainWindow is constructed.
    # This is a convenience/UX gate; Converter() (Core) independently
    # re-checks the license before any conversion runs, so this dialog
    # existing/being skipped does not by itself grant access to
    # conversions -- see shp2dgs/license/gate.py.
    from shp2dgs.gui.activation_dialog import ActivationDialog
    if not ActivationDialog.ensure_activated():
        return 0

    window = MainWindow(config_path, template_path)
    window.show()
    return app.exec()
