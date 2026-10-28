# Protected Core -- Native Migration (COMPLETE for converter.py, equipment_catalog.py, diagram.py)

**Status: implemented and verified.** `converter.py`, `equipment_catalog.py`,
and `diagram.py` now compile to native Cython extension modules via
`build_tools/build_core.py`. This document records what was done, how
it was verified, and what remains as future work.


## Audit result: what's actually worth protecting

Reviewing the current `shp2dgs/core/` package, ranked by business
value (harder to reproduce independently) and sensitivity:

| File | Business value | Why |
|---|---|---|
| `converter.py` | **Highest** | The core SHP -> DGS transformation: topology construction, per-equipment DGS row generation, the whole conversion pipeline. This is the product. |
| `equipment_catalog.py` | High | Layer-name resolution rules and per-equipment field mappings encode significant domain knowledge (Persian-language utility conventions, keyword tables). |
| `diagram.py` | Medium-High | Geographic diagram layout/generation logic. |
| `geo.py` | Medium | Coordinate transforms, GPS/CRS handling -- useful but closer to "well-known GIS math" than proprietary. |
| `config_io.py`, `constants.py`, `progress.py`, `shapefile_io.py` | Low | Generic I/O/plumbing; little unique value if extracted. |

`config.json`'s domain tables (transformer naming patterns, conductor
resistivity assumptions, layer-code defaults) are arguably as valuable
as the code and are currently **plain JSON shipped alongside the
exe** -- migrating the *code* to native form while leaving this
configuration as an easily-copied JSON file would leave a large gap.
Recommendation: fold the default tables into the native core as
compiled-in defaults, with `config.json` becoming an optional
*override* file (already close to how `config_io.py` is structured).

## What was actually done

`build_tools/build_core.py` compiles `converter.py`,
`equipment_catalog.py`, and `diagram.py` with Cython in **pure-Python
mode** (the `.py` source compiled as-is, no `cdef`/type annotations
added). This was a deliberate choice over a manual `cdef`-annotated
rewrite or a C++ port, for two reasons specific to this codebase:

* **Correctness risk.** GeoGrid's output feeds directly into
  PowerFactory network models for real distribution feeders. A manual
  rewrite (Cython with hand-added types, or C++) is a second
  implementation that could silently diverge from the audited Python
  logic. Pure-Python-mode Cython is a *compiler*, not a rewrite --
  there is no manual porting step to get wrong.
* **No regression suite existed** to validate a more aggressive
  rewrite against (see the original "why this wasn't done
  automatically" reasoning below, which still applies to further
  `cdef` optimization).

### Verification performed

1. `build_tools/build_core.py` run end-to-end: compiles all three
   modules to native extensions (`.so` on Linux build/test machines,
   `.pyd` on the Windows release machine), moves the `.py` sources
   aside to `.py.source` (kept on the build machine only, never
   packaged -- see the safety check in `build_release.bat`).
2. `build_tools/verify_core_build.py`: confirms (a) the compiled
   extensions import successfully, (b) no `.py` source for those three
   modules remains on the import path, (c) `Converter()` still raises
   `LicenseError` when unlicensed -- i.e. the Core-level license gate
   (added in `converter.py`, both in `__init__` and again in `build()`)
   survived compilation intact, unmodified in behavior.
3. **Full functional regression**: ran an actual conversion (synthetic
   shapefile input, real `config.json`/`dgs_template.json`) through
   both the compiled-Core build and an unmodified pure-Python baseline
   copy, using the same signed license. Compared all 18 output sheets
   cell-by-cell: **zero differences**. This confirms the Cython compile
   is behaviorally transparent, not just "it imports."

### What this buys, security-wise

* No `.py`/`.pyc` shipped for these three files -- nothing for
  `uncompyle6`/`decompyle3`-style tools to target.
* The Core-level license check (`gate.require_active_license()`,
  called from both `Converter.__init__` and `Converter.build()`) is
  now embedded inside compiled native code. An attacker who patches
  out the GUI/CLI-level checks (pure Python, easy) still has to
  reverse-engineer and patch compiled machine code to remove the
  in-Core checks -- and there are two of them, in two different
  methods, compiled together.

### Known limitation (unchanged from before)

This does **not** defend against an attacker willing to disassemble
the compiled `.pyd`/`.so` directly (see `docs/security_review.md`,
scenario 11). It raises the skill/time cost from "read Python source"
to "reverse-engineer compiled x86-64 code," which is the realistic,
non-absolute goal stated throughout this project.

## Recommended next step (not done in this pass)

Add targeted `cdef`/`cpdef` type annotations to `converter.py`'s
hottest inner loops (topology construction, per-row DGS emission) for
additional performance and a further, modest obfuscation benefit
beyond plain compilation. This should only be attempted now that a
concrete regression baseline exists: re-run the same
compiled-vs-baseline sheet-by-sheet diff performed for this migration
(see "Verification performed" above) against real production feeder
data (F1 Meshkin, Ardabil, Aslandouz), not just the synthetic
single-layer shapefile used to validate this pass, before shipping any
`cdef`-optimized version. `geo.py` and `shapefile_io.py` remain
reasonable candidates to leave in pure Python, since PySide6/pyshp
availability there doesn't change the threat model much.
