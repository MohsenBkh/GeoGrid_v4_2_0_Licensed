#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_core.py
==============
Compiles GeoGrid's highest-value logic (converter.py,
equipment_catalog.py, diagram.py -- see shp2dgs/core/protected/README.md
for the ranking that selected these three) into native Cython
extension modules ("GeoGridCore").

This is a **pure-Python-mode** Cython compile: the .py source is
compiled as-is, with no cdef/type annotations added. That is a
deliberate, conservative choice for this pass:

  * It guarantees byte-for-byte identical *behavior* to the audited
    Python source (Cython in this mode is a compiler, not a rewrite --
    there is no manual porting step that could silently change
    output), which matters a great deal for a tool that generates
    engineering data consumed by PowerFactory.
  * It still delivers the primary security goal: the shipped artifact
    is a compiled `.pyd`/`.so`, not `.py`/`.pyc` -- there is no
    bytecode to run `uncompyle6`/`decompyle3` against, and reading the
    logic back out requires disassembling native machine code.
  * A follow-up pass CAN add `cdef`/`cpdef` annotations to specific
    hot inner loops for an additional (mostly performance, some
    additional obfuscation) benefit once there is a regression-test
    baseline to compile the "before" and "after" outputs against --
    see the TODO at the bottom of this file.

Usage
-----
    # Linux/macOS (for build-machine testing only -- GeoGrid itself only
    # ships on Windows, see docs/developer/build_pipeline.md):
    python build_tools/build_core.py

    # Windows build machine (produces .pyd):
    python build_tools\\build_core.py

Output: compiled extension modules are written IN PLACE next to their
.py sources (e.g. shp2dgs/core/converter.cpython-3xx-*.so /
shp2dgs/core/converter.pyd), and the matching .py file is renamed to
<name>.py.source so it is not accidentally imported instead of the
compiled module, but is kept on disk as the auditable source of
truth for this build. `--restore` reverses this for local development.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CORE_DIR = ROOT / "shp2dgs" / "core"

# Ranked by business value in shp2dgs/core/protected/README.md.
MODULES = ["converter", "equipment_catalog", "diagram"]


def _cythonize(build_dir: Path):
    import os
    from Cython.Build import cythonize
    from setuptools import Extension
    from setuptools.dist import Distribution
    from setuptools.command.build_ext import build_ext

    build_dir.mkdir(parents=True, exist_ok=True)
    for mod in MODULES:
        src = CORE_DIR / f"{mod}.py"
        if not src.exists():
            raise FileNotFoundError(f"Expected source not found: {src}")
        shutil.copy2(src, build_dir / f"{mod}.py")

    # IMPORTANT: chdir into build_dir and use RELATIVE paths everywhere
    # below (sources, build_dir for generated .c files, build_temp,
    # build_lib). Mixing an absolute build_dir/build_temp with absolute
    # source paths makes distutils on Windows re-derive the object-file
    # path by prefixing the (stripped) absolute source path onto the
    # absolute build_temp path, which nests the whole project path
    # inside itself and fails with
    # "The system cannot find the path specified" (especially once the
    # path contains spaces, e.g. "My apps"). Relative paths sidestep
    # this entirely and is also what a plain `python setup.py build_ext`
    # would use.
    prev_cwd = os.getcwd()
    os.chdir(build_dir)
    try:
        sources = [f"{mod}.py" for mod in MODULES]
        extensions = [
            Extension(f"shp2dgs.core.{mod}", [f"{mod}.py"])
            for mod in MODULES
        ]

        compiled = cythonize(
            extensions,
            compiler_directives={
                "language_level": "3",
                "boundscheck": False,   # safe here: no manual indexing added by us
                "wraparound": True,     # keep Python negative-indexing semantics
                "embedsignature": False,
                "binding": False,
            },
            build_dir="c",
            annotate=False,
        )

        dist = Distribution({"ext_modules": compiled})
        dist.script_args = ["build_ext", "--inplace"]
        dist.parse_command_line()
        cmd = build_ext(dist)
        cmd.build_lib = "lib"
        cmd.build_temp = "temp"
        cmd.inplace = False
        cmd.finalize_options()
        cmd.run()
        return build_dir / "lib"
    finally:
        os.chdir(prev_cwd)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--restore", action="store_true",
                     help="Undo an in-place compile: restore .py sources, remove compiled artifacts")
    ap.add_argument("--keep-py", action="store_true",
                     help="Leave the original .py sources importable alongside the compiled "
                          "extension (dev convenience; do NOT use for a release build -- the "
                          ".py would still be extractable even though Python prefers the .so/.pyd)")
    args = ap.parse_args(argv)

    if args.restore:
        restored = 0
        for mod in MODULES:
            src = CORE_DIR / f"{mod}.py.source"
            if src.exists():
                src.rename(CORE_DIR / f"{mod}.py")
                restored += 1
            for artifact in CORE_DIR.glob(f"{mod}.*.so"):
                artifact.unlink()
            for artifact in CORE_DIR.glob(f"{mod}.pyd"):
                artifact.unlink()
            for artifact in CORE_DIR.glob(f"{mod}.*.pyd"):
                artifact.unlink()
        print(f"Restored {restored} module(s) to pure-Python source.")
        return

    # Auto-recovery: if this script already ran once (.py renamed to
    # .py.source, compiled artifact left in place) and is being run
    # again -- e.g. BUILD.bat re-run after a Nuitka-stage failure --
    # transparently restore the .py sources first instead of failing
    # with "source not found". Equivalent to running --restore then
    # continuing, but automatic since re-running the *same* build
    # script twice is an expected, not exceptional, workflow.
    already_compiled = [
        mod for mod in MODULES
        if not (CORE_DIR / f"{mod}.py").exists() and (CORE_DIR / f"{mod}.py.source").exists()
    ]
    if already_compiled:
        print(f"Detected a previous compiled build for: {', '.join(already_compiled)}")
        print("Restoring .py sources before rebuilding...")
        main(["--restore"])

    build_dir = ROOT / "build" / "geogridcore"
    lib_dir = _cythonize(build_dir)

    built = list(lib_dir.rglob("*.so")) + list(lib_dir.rglob("*.pyd"))
    if not built:
        print("ERROR: no compiled artifacts produced.", file=sys.stderr)
        sys.exit(1)

    for artifact in built:
        dest = CORE_DIR / artifact.name
        shutil.copy2(artifact, dest)
        print(f"Built: {dest}")

    if not args.keep_py:
        for mod in MODULES:
            py_path = CORE_DIR / f"{mod}.py"
            if py_path.exists():
                py_path.rename(CORE_DIR / f"{mod}.py.source")
                print(f"Moved source out of import path: {mod}.py -> {mod}.py.source")

    print()
    print("GeoGridCore build complete. Verify with:")
    print("    python build_tools/verify_core_build.py")


if __name__ == "__main__":
    main()

# TODO (future hardening pass, not this one): once a regression-test
# corpus of real production shapefiles + expected .xlsx outputs exists,
# revisit converter.py's hottest functions (topology construction,
# per-row DGS emission) for targeted cdef/cpdef typing. Only do this
# with before/after output diffing in CI -- see
# shp2dgs/core/protected/README.md.
