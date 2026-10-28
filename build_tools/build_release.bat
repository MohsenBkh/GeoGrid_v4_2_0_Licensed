@echo off
REM ==========================================================================
REM  GeoGrid Release Build (Windows, Nuitka, Standalone)
REM ==========================================================================
REM  Run on a clean Windows 10/11 x64 build machine with the release
REM  Python environment activated (matching requirements.txt + Nuitka).
REM
REM  This is a STANDALONE build (multi-file output directory), not
REM  --onefile: a licensed commercial product benefits from Nuitka's
REM  standalone mode because (a) it avoids the onefile runtime's
REM  temp-directory self-extraction step, which is both a minor
REM  performance cost and a location an attacker can inspect for
REM  extracted intermediate files while the app is running, and
REM  (b) it lets us ship GeoGridCore as a separate, independently
REM  integrity-checked .pyd/.dll (see docs/developer/architecture.md).
REM  Do not switch to --onefile without re-reading that tradeoff.
REM ==========================================================================

setlocal
set BUILD_CONFIG=%1
if "%BUILD_CONFIG%"=="" set BUILD_CONFIG=release

REM In "debug" mode, GeoGrid.exe keeps its console window open so
REM startup errors are visible instead of the app silently failing to
REM appear. Use this to diagnose a build that produces an .exe that
REM doesn't start: `build_tools\build_release.bat debug`
if /I "%BUILD_CONFIG%"=="debug" (
    set MAIN_CONSOLE_MODE=force
) else (
    set MAIN_CONSOLE_MODE=disable
)

echo === GeoGrid Release Build (%BUILD_CONFIG%) ===

REM --------------------------------------------------------------------
REM Step 0: Build GeoGridCore (Cython extensions) FIRST. This produces
REM shp2dgs\core\converter.pyd, equipment_catalog.pyd, diagram.pyd and
REM renames the matching .py sources to .py.source so Nuitka bundles
REM the compiled .pyd files rather than compiling those three modules
REM itself. Always run verify_core_build.py immediately after -- do
REM not proceed to the Nuitka build if it reports any failure.
REM --------------------------------------------------------------------
python build_tools\build_core.py
if %ERRORLEVEL% NEQ 0 (
    echo GeoGridCore build FAILED.
    exit /b 1
)
python build_tools\verify_core_build.py
if %ERRORLEVEL% NEQ 0 (
    echo GeoGridCore verification FAILED -- aborting before Nuitka build.
    exit /b 1
)

REM NOTE on --include-module below: shp2dgs.core.converter/
REM equipment_catalog/diagram/progress/shapefile_io/geo are listed
REM explicitly because converter/equipment_catalog/diagram are
REM precompiled Cython extensions (see build_tools\build_core.py), not
REM plain .py source Nuitka compiles itself -- so Nuitka cannot see
REM what they import internally. progress/shapefile_io/geo are only
REM ever imported FROM inside those compiled files (not from any plain
REM .py file Nuitka does trace), so without listing them here the build
REM succeeds but GeoGrid.exe crashes at startup with
REM ModuleNotFoundError. If you add a new import inside
REM converter.py/equipment_catalog.py/diagram.py, check whether the
REM target module is reachable from anywhere else Nuitka traces -- if
REM not, add a matching --include-module line below.
python -m nuitka ^
    --standalone ^
    --enable-plugin=pyside6 ^
    --windows-console-mode=%MAIN_CONSOLE_MODE% ^
    --windows-icon-from-ico=assets\icon.ico ^
    --company-name="Your Company" ^
    --product-name="GeoGrid" ^
    --file-version=4.2.0.0 ^
    --product-version=4.2.0.0 ^
    --file-description="GeoGrid - GIS to DIgSILENT DGS Converter" ^
    --copyright="Proprietary - All rights reserved" ^
    --remove-output ^
    --output-dir=dist ^
    --output-filename=GeoGrid.exe ^
    --nofollow-import-to=GeoGridLicenseManager ^
    --include-module=shp2dgs.core.converter ^
    --include-module=shp2dgs.core.equipment_catalog ^
    --include-module=shp2dgs.core.diagram ^
    --include-module=shp2dgs.core.progress ^
    --include-module=shp2dgs.core.shapefile_io ^
    --include-module=shp2dgs.core.geo ^
    --include-data-files=config.json=config.json ^
    --include-data-files=dgs_template.json=dgs_template.json ^
    --include-data-files=terminal_types_example.json=terminal_types_example.json ^
    --include-data-files=types_override_example.json=types_override_example.json ^
    --include-data-files=types_override.xlsx=types_override.xlsx ^
    --include-data-dir=assets=assets ^
    --python-flag=no_docstrings ^
    --python-flag=-OO ^
    shp_to_dgs.py

if %ERRORLEVEL% NEQ 0 (
    echo Build FAILED.
    exit /b 1
)

REM --------------------------------------------------------------------
REM Step 2: Build GeoGrid_Activate.exe -- a small, separate, console-mode
REM executable for the simple guided activation flow (see
REM shp2dgs/license/activate_cli.py). Ships next to GeoGrid.exe so a
REM customer can activate by double-clicking it directly, without first
REM opening GeoGrid or its GUI. Deliberately kept as its own exe (not a
REM GeoGrid.exe command-line flag) so it starts instantly with no
REM PySide6/Qt initialization.
REM --------------------------------------------------------------------
python -m nuitka ^
    --standalone ^
    --windows-console-mode=force ^
    --windows-icon-from-ico=assets\icon.ico ^
    --company-name="Your Company" ^
    --product-name="GeoGrid Activation" ^
    --file-version=4.2.0.0 ^
    --product-version=4.2.0.0 ^
    --file-description="GeoGrid - License Activation" ^
    --copyright="Proprietary - All rights reserved" ^
    --remove-output ^
    --output-dir=dist_activate ^
    --output-filename=GeoGrid_Activate.exe ^
    --nofollow-import-to=GeoGridLicenseManager ^
    --python-flag=no_docstrings ^
    --python-flag=-OO ^
    geogrid_activate.py

if %ERRORLEVEL% NEQ 0 (
    echo GeoGrid_Activate.exe build FAILED.
    exit /b 1
)

REM Merge the activation exe into the main app's output folder so the
REM installer only has to package one directory.
copy /Y dist_activate\geogrid_activate.dist\GeoGrid_Activate.exe dist\shp_to_dgs.dist\GeoGrid_Activate.exe >nul
if %ERRORLEVEL% NEQ 0 (
    echo Could not copy GeoGrid_Activate.exe into the main output folder.
    exit /b 1
)

REM Also copy the debug-run helper next to the built exe so it's always
REM available for diagnosing a startup crash without rebuilding.
copy /Y Run_GeoGrid_Debug.bat dist\shp_to_dgs.dist\Run_GeoGrid_Debug.bat >nul

echo.
echo === Build complete ===
echo   Main app:     dist\shp_to_dgs.dist\GeoGrid.exe
echo   Activation:   dist\shp_to_dgs.dist\GeoGrid_Activate.exe
echo.
echo NEXT STEPS (manual, see docs/developer/build_pipeline.md):
echo   1. Authenticode-sign GeoGrid.exe with your Code Signing Certificate
echo      (this is DIFFERENT from and unrelated to the license-signing key).
echo   2. Run build_tools\make_manifest.py against dist\shp_to_dgs.dist to
echo      produce integrity_manifest.json. Track GeoGrid.exe,
echo      GeoGrid_Activate.exe, and the three compiled .pyd Core files.
echo      See docs\developer\architecture.md for the exact command.
echo   3. Package dist\shp_to_dgs.dist with your installer (Inno Setup / WiX).
echo   4. Verify GeoGridLicenseManager/ was NOT included anywhere in the output.
echo   5. Confirm no *.py.source files for converter/equipment_catalog/diagram
echo      were accidentally packaged (they must stay on the build machine only).
echo.

REM Extra safety check: fail loudly if the .py.source files (kept on the
REM build machine as the auditable pre-compile originals) ever end up in
REM the packaged output directory.
if exist dist\shp_to_dgs.dist\shp2dgs\core\converter.py.source (
    echo ERROR: converter.py.source leaked into build output. Aborting.
    exit /b 1
)

REM Safety check: fail loudly if the vendor-only License Manager or any
REM of ITS specific private key files ever end up inside the client
REM build output. (Deliberately NOT a generic grep for the text
REM "PRIVATE KEY" across all output files: Qt/OpenSSL/TLS DLLs bundled
REM by the pyside6 plugin legitimately contain that string as part of
REM their PEM-parsing code, which caused false-positive build failures
REM here previously.)
if exist dist\shp_to_dgs.dist\keys (
    echo WARNING: a 'keys' folder was found in build output!
    exit /b 1
)
for /r dist\shp_to_dgs.dist %%F in (*.private.b64) do (
    echo WARNING: private key file %%F found in build output!
    exit /b 1
)
if exist dist\shp_to_dgs.dist\GeoGridLicenseManager (
    echo ERROR: GeoGridLicenseManager was bundled into the client build. Aborting.
    exit /b 1
)

REM Required-resource check: config.json/dgs_template.json missing from
REM the bundle causes GeoGrid.exe to crash silently at startup (console
REM is disabled in the release build, so this failure shows nothing to
REM the user -- catch it here instead).
if not exist dist\shp_to_dgs.dist\config.json (
    echo ERROR: config.json missing from build output -- GeoGrid.exe would crash silently at startup. Aborting.
    exit /b 1
)
if not exist dist\shp_to_dgs.dist\dgs_template.json (
    echo ERROR: dgs_template.json missing from build output -- GeoGrid.exe would crash silently at startup. Aborting.
    exit /b 1
)
if not exist dist\shp_to_dgs.dist\types_override.xlsx (
    echo ERROR: types_override.xlsx missing from build output -- the default equipment type library would not be available (fuses/types would fall back to dgs_template.json placeholders). Aborting.
    exit /b 1
)

echo All required resource files confirmed present in build output.

endlocal
