# GeoGrid — SHP → DIgSILENT DGS Converter v3.5

Converts a distribution-network GIS export (ESRI shapefiles) into a
DIgSILENT PowerFactory **DGS** deck (multi-sheet `.xlsx`) that can be
imported directly through PowerFactory's *DGS Import*, including both
**MV and LV network topology**, an optional local diagram, and optional
real-world GPS coordinates for PowerFactory's **Geographic Diagram**.

Conversion logic (topology, line/transformer type resolution, load model)
was re-validated against real MV+LV feeder datasets. The product is
branded **GeoGrid**; the underlying Python package/import path
(`shp2dgs`) is kept unchanged for backward compatibility with existing
scripts.

### What's new in v3.5
- **Manual shapefile-layer mapping**: automatic name matching (e.g.
  "MV Overhead Line" → `ELWOML`) is now just the default -- the new
  **Shapefile Layers** tab in Project Settings lets you point every
  equipment slot at the correct file directly, so a naming mismatch
  never silently produces a wrong/incomplete conversion. Also on the CLI
  via `--layer KEY=VALUE`.
- **Expanded equipment catalog**: sectionalizer, auto-booster,
  feeder-head switch, capacitor banks (now populate the `ElmShnt` sheet),
  MV/LV self-supporting (ABC) cable, distributed generation sources, and
  service cables are all now recognised equipment slots.
- **MV/LV scope toggle**: choose to convert MV only, LV only, or both
  (`--no-mv`/`--no-lv` on the CLI).
- **Project Settings now remembers your last-used values** when reopened
  instead of resetting to defaults, and its main button is now labelled
  **Save** (it only saves settings — running the conversion is still a
  separate, explicit step).
- **Type Library Editor**: new **Use Saved Library** button to reload the
  last saved default template, discarding unsaved edits.

---

## 1. Architecture

```
Shp2GIS_Converter/
├── shp_to_dgs.py            # legacy entry point (backward compatible)
├── requirements.txt
├── pyproject.toml           # pip install -e . / console entry points (shp2dgs, geogrid)
├── shp_to_dgs.spec          # PyInstaller build spec (builds dist/GeoGrid/GeoGrid.exe)
├── config.json               # field-mapping / keyword / graphics / LV-type rules
├── dgs_template.json         # fixed DGS sheets + MV equipment type library
├── types_override.json       # your equipment-type overrides (optional)
├── types_override_example.json
├── terminal_types_example.json
├── assets/
│   ├── icon.png / icon.ico    # app/window/exe icon
│   └── splash.png             # splash-screen artwork
└── shp2dgs/                   # the installable package (import path unchanged)
    ├── __init__.py            # __version__
    ├── __main__.py            # `python -m shp2dgs`
    ├── cli.py                 # argparse CLI + GUI launcher (single argv parser)
    ├── core/                  # NO Qt dependency — usable headless/as a library
    │   ├── constants.py        # defaults: app name/version, terminal symbols, styles
    │   ├── config_io.py        # JSON load/save, resource_path(), type-library merge
    │   ├── shapefile_io.py     # pyshp wrapper, recursive layer search, corrupt-layer resilience
    │   ├── progress.py         # ProgressReporter (percent/ETA, GUI-agnostic)
    │   ├── converter.py        # Converter: the conversion engine (MV + LV)
    │   ├── diagram.py          # DiagramModel: IntGrf/IntGrfcon -> drawable model
    │   └── geo.py               # optional shapefile CRS -> WGS84 GPS reprojection
    ├── io_formats/
    │   └── excel_types.py      # equipment type library <-> Excel (openpyxl)
    ├── gui/                    # depends on core, never the other way around
    │   ├── qt_compat.py        # PySide6 (preferred) / PyQt5 (fallback) resolution
    │   ├── resources.py        # icon/splash loading, QPainter placeholder fallback
    │   ├── app.py               # QApplication + splash + MainWindow bootstrap
    │   ├── main_window.py       # menu bar, status bar, tabs
    │   ├── widgets/
    │   │   ├── progress_panel.py   # inline (embedded) conversion progress panel
    │   │   ├── diagram_view.py     # QGraphicsView: zoom/pan + PNG/SVG export
    │   │   └── paste_table.py      # Excel-style multi-cell paste/copy table widget
    │   └── dialogs/
    │       ├── project_settings_dialog.py    # project/electrical/graphics/terminals/GPS
    │       ├── type_library_editor_dialog.py # spreadsheet editor + Excel/JSON I/O + Add Type
    │       └── summary_dialog.py             # post-conversion element counts
    └── utils/
        └── logging_setup.py    # one-time logging configuration (file + console)
```

**Dependency rule**: `core` never imports from `gui`. The GUI calls into
`core` through plain function/class APIs and a
`progress_callback(percent, message, step, eta_seconds, elapsed)`
signature — this is what lets the same `Converter` power both the CLI and
the GUI, and lets `core`/`io_formats` be unit-tested without Qt installed.

---

## 2. What it does — equipment catalog & layer mapping

Every equipment type GeoGrid understands is defined once in
`shp2dgs/core/equipment_catalog.py` and matched to a shapefile layer by
name automatically (e.g. "MV Overhead Line" → `ELWOML`) using the
defaults in `config.json["layers"]`. **This is only a convenience
default** — if your export uses different layer names, open
`Project Settings… → Shapefile Layers` and point each equipment slot at
the correct file directly (or use `--layer KEY=VALUE` on the CLI); a
naming mismatch then never silently produces a wrong/incomplete
conversion.

### Medium voltage (MV)

| Equipment | Catalog key | Default layer | DGS object(s) |
|---|---|---|---|
| MV Overhead Line | `mv_overhead_line` | `ELWOML` | `ElmLne` |
| MV Underground Line (Cable) | `mv_underground_line` | `ELWUML` | `ElmLne` |
| Other MV Lines | `mv_other_lines` | `ELWMLN` | `ElmLne` |
| MV Self-Supporting Cable (ABC) | `mv_abc_cable` | *(none)* | `ElmLne` |
| MV Jumper | `mv_jumper` | `ELWJUP` | `ElmLne` |
| Transformer | `mv_transformer` | `ELWDTR` | `ElmTr2` + `Elmlod` |
| Circuit Breaker | `mv_breaker` | `ELWCBR` | `ElmCoup` |
| Disconnector | `mv_disconnector` | `ELWDCS` | `ElmCoup` |
| Recloser | `mv_recloser` | `ELWABR` | `ElmCoup` |
| Sectionalizer | `mv_sectionalizer` | `ELWSEC` | `ElmCoup` |
| Auto-Booster | `mv_autobooster` | *(none)* | `ElmCoup` \* |
| Feeder-Head Switch | `mv_feeder_switch` | *(none)* | `ElmCoup` |
| Capacitor Bank | `mv_capacitor` | *(none)* | `ElmShnt` |
| Fuse / Cutout | `mv_fuse` | `ELWFCT` | `RelFuse` |
| Feeder Source | `mv_source` | `ELWFWT` | `ElmXnet` + `ElmFeeder` |

\* modelled as a simple series switch, not a voltage-regulating device —
full auto-booster behaviour isn't implemented.

### Low voltage (LV)

| Equipment | Catalog key | Default layer | DGS object(s) |
|---|---|---|---|
| LV Overhead Line | `lv_overhead_line` | `ELWOLL` | `ElmLne` |
| LV Underground Line | `lv_underground_line` | `ELWULL` | `ElmLne` |
| Other LV Lines | `lv_other_lines` | `ELWLLN` | `ElmLne` |
| LV Jumper | `lv_jumper` | `ELWLJP` | `ElmLne` |
| LV Self-Supporting Cable (ABC) | `lv_abc_cable` | *(none)* | `ElmLne` |
| Service Cable | `lv_service_cable` | `ELWCSE` | `ElmLne` |
| LV Fuse | `lv_fuse` | `ELWLFU` | `RelFuse` |
| LV Switch / Sectionalizer | `lv_switch` | `ELWLSB`, `ELWLSC` | `ElmCoup` |
| LV Distribution Box | `lv_distribution_box` | `ELWDBX` | forced LV junction node |
| Distributed Generation Source | `lv_dg_source` | *(none)* | `ElmXnet` + `ElmFeeder` \*\* |

\*\* modelled as an equivalent source, not a detailed PV/generator model.

topology nodes → `ElmTerm` + `StaCubic` (+ `IntGrf` / `IntGrfcon`) for both MV and LV.

Slots with no default layer (marked *(none)*) are simply skipped unless
you map them manually — nothing breaks if your data doesn't have that
equipment.

### Choosing what to convert
`Project Settings… → Shapefile Layers` also has two checkboxes,
**Convert MV equipment** and **Convert LV equipment** (both on by
default) — uncheck either to skip that whole level for this run
(`--no-mv`/`--no-lv` on the CLI). Transformers and sources are always
read even with MV unchecked, since the LV network needs them as anchors.

### Topology model (verified against real MV+LV feeder data)
- Every unique line **end-point** (rounded to `round_decimals`) becomes a terminal.
- Each **series device** (switch / fuse) is inserted in-line and adds
  **one** terminal (chain: `line → switch → fuse → transformer`), on both
  the MV and LV side.
- Each **transformer** adds an HV busbar-style terminal plus an LV
  terminal. Its *true* electrical position is the transformer's real GIS
  coordinate (so LV lines connect to the right transformer automatically);
  its *diagram layout* position is offset below the transformer symbol
  purely for legibility -- these are tracked separately, so the diagram
  stays tidy without breaking connectivity or GPS accuracy.
- **Load placement**: if a transformer has **no** downstream LV line
  network, its full rated load is placed directly on the LV busbar
  (unchanged, simple case). If it **does** have a downstream LV network,
  the load is instead split evenly across that network's **leaf nodes**
  (the dead-ends of the LV feeder, found via a breadth-first search over
  the LV connectivity graph) -- reflecting where the load actually is
  physically, which matters for LV voltage-drop studies. The LV busbar
  itself is never treated as a leaf.
- **DGS symbol correctness**: LV lines/switches/fuses (and DG sources)
  are written to the DGS file using the exact same valid DIgSILENT symbol
  names as their MV counterparts (`d_lin`/`d_couple`/`d_fuse`/`d_net`) --
  PowerFactory doesn't recognise made-up symbol names, so this is what
  makes LV elements actually appear in the main Single Line Diagram after
  import. Capacitor banks reuse the `d_load` symbol for the same reason
  (the exact PowerFactory shunt symbol name could not be confirmed). A
  richer, internal-only classification is kept alongside (never written
  to the DGS file) purely so GeoGrid's own Diagram Preview can still
  colour these differently.
- **LV line/cable types are not present in the base template** (only MV
  types ship in `dgs_template.json`), so GeoGrid synthesizes a `TypLne`
  row on demand for each distinct (material, section, installation)
  combination found in the LV layers, and one LV-rated `TypFuse` for LV
  fuses. Resistance is computed from standard conductor resistivity
  (physics-based); reactance/current-rating use single typical values per
  installation category (overhead vs underground) since exact
  manufacturer catalogue data wasn't available — see
  `config["lv_line_type"]["_comment"]`. Capacitor reactive-power rating
  similarly uses a configurable default (`config["capacitor"]`), since no
  rating field was identified in the source schema. **Review these
  generated values in the Type Library Editor (or override them) before
  using results for real engineering decisions.**
- No new per-customer load elements are created for individual LV
  lines/service points — the source data has no reliable per-customer
  load size, so fabricating one would be misleading. LV load stays
  represented in aggregate, split across the LV network's leaf nodes.
- A shapefile layer that exists but is corrupt/truncated is logged as an
  error and **skipped**, not fatal — the rest of the network still
  converts. (A layer that's simply absent is logged as an informational
  warning, same as always.)

---

## 3. Installation


```bash
pip install -r requirements.txt
# or, as an editable package with `shp2dgs`/`geogrid` console commands:
pip install -e .
```

`requirements.txt` installs `pyshp`, `openpyxl` and `PySide6` (PyQt5 is an
accepted drop-in alternative — see the comments in that file). `pyproj` is
**optional**, only needed for the GPS/Geographic Diagram feature — see §7.

---

## 4. Usage

### A) GUI mode (recommended)

```bash
python shp_to_dgs.py
# or:
python -m shp2dgs
```

1. **Splash screen** (>= 2s) with the GeoGrid logo, version and a loading bar.
2. **Main window** opens on the **Conversion** tab:
   - Pick the input shapefile folder and the output `.xlsx` path.
   - Open **Project Settings…** to set project/deck name, nominal
     voltage, load factor/cos φ, diagram sizing, per-connection-kind
     terminal symbols, **Shapefile Layers** (manual equipment-to-file
     mapping, MV/LV scope — see §2), an equipment-type override (JSON or
     Excel), and **Geographic (GPS)** settings. Click **Save** to keep
     these settings (it only saves — it does not start the conversion).
     Reopening the dialog later shows what you last saved, not the
     defaults again.
   - Click **Run Conversion**. Progress (percent, elapsed/remaining time,
     a scrolling log) is shown **inline, in the same tab** — no separate
     progress window pops up.
3. On completion, the app automatically switches to the **Diagram Preview**
   tab and shows a **Summary** dialog with full DGS element counts
   (MV and LV broken out separately).
4. The **Type Library** tab opens the equipment-type editor — see §5.

On Windows: double-click the built `.exe` (see §8).

### B) Command line (automation / server)

```bash
python shp_to_dgs.py \
    --shp   /path/to/shapefile_folder \
    --out   MyNetwork_DGS.xlsx \
    [--config config.json] [--template dgs_template.json] \
    [--no-graphics] [--no-gui] \
    [--project-name "My Grid"] [--deck-name "My Deck"] \
    [--nominal-kv 20] [--frequency 50] \
    [--load-factor 0.7] [--cosphi 0.9] \
    [--element-size 0.5] [--node-spacing 5] \
    [--terminal-config terminal_types_example.json] \
    [--types types_override.json] \          # JSON or .xlsx
    [--gps] [--gps-lines] [--epsg 32639] \    # GPS / Geographic Diagram
    [--no-mv] [--no-lv] \                     # convert only one voltage level
    [--layer mv_capacitor=ELWCNT] [--layer mv_recloser=]   # manual layer mapping (repeatable)
```

- `--layer KEY=VALUE` overrides one equipment slot's shapefile layer (see
  the catalog table in §2 for valid keys); `--layer KEY=` (empty value)
  disables that slot entirely. Repeat for multiple overrides.

LV layers are read automatically from the same `--shp` folder (see
`config.json["layers"]`) — there is no separate flag needed to enable LV
conversion; it runs whenever those layers are present.

### Importing into PowerFactory
`File → Import → DGS…` → select the generated `.xlsx`.

---

## 5. Equipment type library editor

`Tools → Type Library Editor…` (or the **Type Library** tab) is a
spreadsheet-style editor, one sub-tab per equipment type
(`TypLne`, `TypTr2`, `TypLod`, `TypFuse`, plus any custom categories you
add). The bookkeeping columns `ID` and `fold_id` are hidden and fully
auto-managed -- you only ever see/enter technically meaningful fields.

### Adding a new type
Click **Add Type…**, enter a name — a new row is inserted with the name
filled in and `ID`/`fold_id` generated automatically (hidden). **Add
Blank Row** adds an empty row (still auto-numbered), and **Delete Row**
removes the selected row.

### Adding a whole new equipment category
Click the **"+"** tab (to the right of the existing tabs, like opening a
new tab in a browser) to define a brand-new equipment category beyond
`TypLne`/`TypTr2`/`TypLod`/`TypFuse` — name it (e.g. `TypCap` for shunt
capacitors) and list its starting columns. The new category behaves like
any built-in one: it participates in Add Type/paste, Excel/JSON
import-export, and the final override.

### Editing columns
**Double-click a column header** to rename it (handy for columns named
`Extra1`, `Extra2`, ... after a paste that was wider than the table).
**Add Column…** appends a new named column directly.

### Pasting from an external spreadsheet
Select a starting cell and paste (`Ctrl+V`) a block of cells copied from
Excel/LibreOffice/Google Sheets — every cell in the pasted block fills in
starting at the selected cell, **skipping the hidden `ID`/`fold_id`
columns** exactly like pasting into a spreadsheet with hidden columns
(the paste never lands in the wrong column just because a system column
sits in the middle of the row). If the pasted block is **wider or
taller** than the current table, rows and columns are added automatically
to fit (new columns are labelled `Extra1`, `Extra2`, ... — rename them as
above). `Ctrl+C` copies the current selection back out in the same
tab-separated format; `Delete`/`Backspace` clears selected cells.

### Import/Export
- **Export to Excel**: one sheet per equipment type, ready to fill in or
  hand off to a colleague; row 1 is the header
  (`field(type:length)` syntax, e.g. `rline(r)`, `chr_name(a:20)`).
- **Import from Excel**: matching is **name-based, not positional** --
  every column your template expects must be present in the sheet
  *somewhere* (any order, interleaved with extra/custom columns is fine);
  each cell is validated against its DGS field type (`r` numeric, `i`
  integer, `a[:N]` text, `p` pointer/unvalidated). A genuinely missing
  required column, or an invalid value, raises a clear, specific error
  message (sheet, row, field).
- **Import/Export to JSON**: the native `types_override.json` format,
  for reuse from the CLI's `--types` flag.

A successful import is merged into the running template and immediately
reflected in the editor's tables; **OK && Use** applies it to the next
conversion only. **Save as Default Template** instead writes the current
library back to the application's `dgs_template.json` (with a timestamped
backup of the previous version kept alongside it), so it becomes the
default for every future session — no override file needed. **Use Saved
Library** does the reverse: reload the library fresh from that saved
default template, discarding any unsaved changes made in the current
editor session (asks for confirmation first).

---

## 6. Diagram Preview

After a conversion, the **Diagram Preview** tab renders the network from
the `IntGrf`/`IntGrfcon` sheets: zoom with the mouse wheel, pan by
dragging, export to PNG/SVG from the toolbar. MV and LV elements/nodes
use distinct colours (see `core.constants.DIAGRAM_ELEMENT_STYLE`/
`DIAGRAM_TERMINAL_STYLE`) so the two voltage levels are visually
distinguishable at a glance.

---

## 7. GPS coordinates for PowerFactory's Geographic Diagram

PowerFactory's **Insert → Geographic Diagram** shows the network on a real
map background — a separate feature from the regular `IntGrf` diagram
GeoGrid always generates. It reads real WGS84 latitude/longitude stored
on the objects themselves (`GPSlat`/`GPSlon` on `ElmTerm`, and optionally
per-vertex `GPSlat:i`/`GPSlon:i` on `ElmLne`, for both MV and LV lines).

- **GUI**: `Project Settings… → Geographic (GPS)` tab — **on by default**;
  uncheck it if you don't want GPS coordinates generated.
- **CLI**: `--gps` (optionally `--gps-lines`, `--epsg <code>`) — off by
  default on the CLI (opt-in, to keep scripted/automated conversions'
  output unchanged unless explicitly requested).
- Requires the optional **`pyproj`** package (`pip install pyproj`) and a
  source CRS, resolved from a `.prj` sidecar file next to the shapefiles
  or an explicit EPSG code (e.g. `32639` for UTM Zone 39N). If neither is
  available (or `pyproj` isn't installed), GPS generation is simply
  **skipped with a logged warning** -- it never blocks the rest of the
  conversion.
- After importing, rebuild the Geographic Diagram once in PowerFactory to
  pick up the new coordinates.

---

## 8. Building a standalone Windows executable

```bash
pip install -r requirements.txt
pip install pyinstaller
pyinstaller shp_to_dgs.spec
```

Output: `dist/GeoGrid/GeoGrid.exe` (onedir — fast startup). `pyproj` is
**not** bundled by default — add it before building if you want the GPS
feature in the shipped `.exe`.

---

## 9. Production quality

### Error handling
- `FileNotFoundError` / missing shapefile layer → clear message + log,
  conversion continues with that layer empty.
- A shapefile layer that **exists but is corrupt/truncated** (e.g. a
  damaged archive) → logged as an error identifying exactly which layer,
  but the rest of the conversion still completes rather than aborting.
- `PermissionError` (Excel file open elsewhere) → actionable message.
- Corrupt shapefile record → skipped with a warning; processing continues.
- Invalid JSON config/template → precise `json.JSONDecodeError` surfaced.
- Missing `pyproj` / unresolvable source CRS (GPS feature) → clear,
  actionable error raised before any file is written.
- Every unhandled exception is logged with a full traceback and shown to
  the user (GUI: message box with "Show Details"; CLI: non-zero exit code).

### Logging
- Shared logger hierarchy (`shp2dgs.*`), one-time setup via
  `shp2dgs.utils.logging_setup.configure_logging()`.
- Log file: `~/shp_to_dgs.log` (falls back to CWD if home isn't writable).
- The inline progress panel shows a live tail of the same messages.

### Documentation
Every public class/function has a docstring: signature, behaviour,
possible errors, and (for non-obvious ones) a usage example.

---

## 10. Conversion rules

All rules live in **`config.json`**:

- **MV line type** (`TypLne`): material `T_jns_hadi`, section `T_cod_sath`, net `T_typ_shab`
- **LV line type** (`TypLne`, synthesized): material `T_jns_hadi`,
  section `T_cod_sath`, installation `T_typ_shab` (overhead) /
  `T_noe_arys` (underground) — see `config["lv_line_type"]`
- **Transformer type**: `"{cap}KVA20-O"` from `T_strn`
- **Load**: `plini = capacity_kVA × loading_factor / 1000` MW, default `cosφ = 0.9`
- **Line length**: `Shape_STLe / 1000` km
- **Feeder**: `T_FeederNa`
- **Switch/fuse in-service state** (`T__outserv`): recognises both
  boolean-style (`"1"`/`"true"`) and the Persian-language "in-circuit"/
  "out-of-circuit" phrases seen in real exports

Diagram sizing (`config.json["graphics"]`, also editable per-run in
**Project Settings → Graphics**): `node_spacing`, `terminal_size`,
`busbar_length`/`busbar_thickness`, `element_size`, `page_margin_ratio`/`page_margin_min`.

---

## 11. Files

| File/Folder | Purpose |
|---|---|
| `shp_to_dgs.py` | legacy entry point (delegates to `shp2dgs.cli.main`) |
| `shp2dgs/` | the installable package (core + gui + cli + utils), incl. `core/geo.py`, `core/equipment_catalog.py` |
| `config.json` | mapping/keyword/graphics/LV-type rules — editable |
| `dgs_template.json` | fixed DGS sheets + MV equipment type library |
| `types_override.json` | your equipment-type overrides (optional) |
| `types_override_example.json` / `terminal_types_example.json` | worked examples |
| `assets/` | `icon.png` / `icon.ico` / `splash.png` |
| `shp_to_dgs.spec` | PyInstaller build spec (→ `GeoGrid.exe`) |
| `pyproject.toml` | `pip install -e .` packaging + `shp2dgs`/`geogrid` console entry points |
| `requirements.txt` | Python dependencies |

---

## 12. Compatibility

- All CLI flags from previous versions are fully preserved
- Core MV conversion logic (topology, type resolution, load model) unchanged
- LV support is additive: a shapefile set with no LV layers converts
  exactly as before
- Python 3.9 – 3.12
- Windows 10/11 (`.exe`), Linux, macOS (run from source)
- PySide6 preferred, PyQt5 automatic fallback, tkinter legacy path-picker
  fallback if neither Qt binding is installed

## 13. Troubleshooting

| Problem | Solution |
|---|---|
| `No Qt binding is installed` | `pip install PySide6` (or `PyQt5`) |
| `Permission denied` writing `.xlsx` | close the output file in Excel first |
| A layer logged as "corrupt or unreadable" | that specific export is damaged — re-export/re-download just that layer; everything else still converted |
| SVG export fails: "SVG module missing" | `pip install PySide6-Addons`, or use PNG export instead |
| `pyproj` not installed (GPS feature) | `pip install pyproj` |
| GPS: "no .prj file found and no EPSG" | enter the source EPSG code manually (e.g. `32639`) |
| LV line electrical parameters look approximate | expected — see §2; refine them in the Type Library Editor |
| Geographic Diagram is empty after import | rebuild it once in PowerFactory after the DGS import |
| Where's the log? | `~/shp_to_dgs.log`, or `Help → Open Log File` in the GUI |

---

**GeoGrid v3.3** — Production-ready, modular package edition with MV+LV topology, GPS support, and an enhanced type library editor.
