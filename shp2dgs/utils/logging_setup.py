#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
utils.logging_setup
====================
Central logging configuration for the ``shp2dgs`` package.

The rest of the code base only ever does ``logging.getLogger("shp2dgs.xxx")``
so it never needs to know *where* the log file lives or how handlers are
wired up. :func:`configure_logging` performs that wiring exactly once (it is
idempotent) and returns the resolved log-file path so callers (CLI / GUI)
can display it to the user.
"""

import logging
import os
import sys

#: Name of the root logger for the whole application. Every module should
#: obtain its logger via ``logging.getLogger(f"{APP_LOGGER_NAME}.<module>")``.
APP_LOGGER_NAME = "shp2dgs"

_configured = False
_log_file_path = None


def default_log_path():
    """Return the preferred log file location.

    Prefers the user's home directory (writable on every platform without
    admin rights); falls back to the current working directory if the home
    directory cannot be resolved or is not writable.
    """
    try:
        candidate = os.path.join(os.path.expanduser("~"), "shp_to_dgs.log")
        # Verify the directory is writable before committing to it.
        test_dir = os.path.dirname(candidate) or "."
        if os.access(test_dir, os.W_OK):
            return candidate
    except Exception:
        pass
    return os.path.join(os.getcwd(), "shp_to_dgs.log")


def configure_logging(log_path=None, level=logging.INFO):
    """Configure the application-wide logging handlers.

    Args:
        log_path: Optional explicit path for the log file. If omitted,
            :func:`default_log_path` is used, with an automatic fallback to
            the current working directory if the home directory is not
            writable (e.g. restricted service accounts, read-only images).
        level: Logging level for both handlers (default ``INFO``).

    Returns:
        str: The path of the log file actually used.

    Notes:
        This function is safe to call multiple times; only the first call
        installs handlers, subsequent calls are no-ops and simply return the
        previously resolved path. This avoids duplicate log lines when both
        a library entry point and the CLI/GUI bootstrap call it.
    """
    global _configured, _log_file_path
    if _configured:
        return _log_file_path

    candidate = log_path or default_log_path()
    handlers = [logging.StreamHandler(sys.stdout)]
    try:
        handlers.insert(0, logging.FileHandler(candidate, encoding="utf-8"))
    except Exception:
        # Home directory not writable (or path invalid) -> fall back to CWD.
        fallback = os.path.join(os.getcwd(), "shp_to_dgs.log")
        try:
            handlers.insert(0, logging.FileHandler(fallback, encoding="utf-8"))
            candidate = fallback
        except Exception:
            # Even CWD isn't writable -> log to console only.
            candidate = None

    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=handlers,
        force=True,
    )
    _configured = True
    _log_file_path = candidate
    logging.getLogger(APP_LOGGER_NAME).info("Logging initialised (file=%s)", candidate)
    return candidate


def get_log_file_path():
    """Return the resolved log file path (``None`` if console-only)."""
    return _log_file_path
