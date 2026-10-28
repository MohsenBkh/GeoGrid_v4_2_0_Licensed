#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
shp2dgs.core
============
The conversion engine. Everything in this sub-package is GUI-free by
design: :mod:`shp2dgs.gui` depends on :mod:`shp2dgs.core`, never the other
way around. This keeps the core usable from the CLI, from unit tests, or
embedded in another application without pulling in Qt.
"""
