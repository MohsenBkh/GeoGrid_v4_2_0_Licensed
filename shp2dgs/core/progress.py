#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
core.progress
=============
A GUI-agnostic progress reporter with a simple linear ETA estimate.
The :class:`Converter` calls :meth:`ProgressReporter.report` throughout
``build()``/``emit()``/``write()``; the GUI wires its own callback in to
update a ``QProgressBar``, while the CLI simply logs each update.
"""

import logging
import time

logger = logging.getLogger("shp2dgs.progress")


class ProgressReporter:
    """Tracks conversion progress and estimates time remaining.

    Args:
        callback: Optional ``callback(percent, message, step, eta_seconds,
            elapsed)`` invoked on each significant update. If omitted,
            updates are written to the logger instead.
    """

    def __init__(self, callback=None):
        self.callback = callback
        self.start_time = time.time()
        self.last_percent = -1

    def report(self, percent, message="", step=""):
        """Report progress.

        Args:
            percent: Overall completion percentage (0-100). Clamped to
                range; small changes (<0.3%) without a message are
                coalesced/skipped to avoid flooding the UI/log.
            message: Optional human-readable status line.
            step: Optional short machine-readable stage id (e.g.
                ``"read_lines"``), used by the GUI to show a friendly
                stage label.
        """
        percent = max(0, min(100, float(percent)))
        if abs(percent - self.last_percent) < 0.3 and not message:
            return
        self.last_percent = percent
        elapsed = time.time() - self.start_time
        eta_seconds = 0
        if percent > 1:
            total_est = elapsed / (percent / 100.0)
            eta_seconds = max(0, total_est - elapsed)
        if self.callback:
            try:
                self.callback(percent, message, step, eta_seconds, elapsed)
            except Exception as e:
                logger.warning("Progress callback error: %s", e)
        else:
            if message:
                logger.info("[%5.1f%%] %s | ETA: %ds", percent, message, int(eta_seconds))
