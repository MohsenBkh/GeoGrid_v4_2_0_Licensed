@echo off
REM ==========================================================================
REM  BUILD.bat -- One-click GeoGrid release build
REM ==========================================================================
REM  Run this from the project root (same folder as this file) on a clean
REM  Windows 10/11 x64 build machine. It installs required build tools,
REM  compiles the Protected Core, builds GeoGrid.exe and
REM  GeoGrid_Activate.exe with Nuitka, and tells you exactly where the
REM  finished files are.
REM
REM  Prerequisites this script does NOT install for you:
REM    - Python 3.11+ (must already be on PATH)
REM    - A C compiler for Nuitka/Cython (MSVC Build Tools, or MinGW64)
REM  Everything else (nuitka, cython, pyside6, etc.) is installed
REM  automatically from requirements.txt below.
REM ==========================================================================

setlocal

echo ============================================================
echo   GeoGrid -- One-Click Release Build
echo ============================================================
echo.

echo [1/4] Checking Python...

REM Actively prefer Python 3.12 or 3.13 via the "py" launcher, even if
REM a different version (e.g. 3.14) is what "python" currently
REM resolves to on PATH. Nuitka only experimentally supports 3.14 at
REM the time of writing, and has been observed to produce a GeoGrid.exe
REM that builds successfully but fails to start with no visible error.
REM Just having 3.12/3.13 installed alongside 3.14 does NOT change
REM what "python" points to -- this block finds it explicitly via the
REM py launcher and prepends it to PATH for this build only.
set FORCED_PY=
where py >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    for %%V in (3.12 3.13) do (
        if not defined FORCED_PY (
            py -%%V --version >nul 2>&1
            if not errorlevel 1 set FORCED_PY=%%V
        )
    )
)

if defined FORCED_PY (
    echo Found Python %FORCED_PY% via the py launcher -- using it for this build.
    for /f "delims=" %%P in ('py -%FORCED_PY% -c "import sys,os;print(os.path.dirname(sys.executable))"') do set PYDIR=%%P
    set "PATH=%PYDIR%;%PYDIR%\Scripts;%PATH%"
) else (
    where python >nul 2>&1
    if %ERRORLEVEL% NEQ 0 (
        echo ERROR: Python was not found on PATH, and no 3.12/3.13 was
        echo found via the py launcher either.
        echo Install Python 3.12 or 3.13 from https://www.python.org/downloads/
        echo and make sure "Add python.exe to PATH" is checked during install.
        pause
        exit /b 1
    )
    echo No Python 3.12/3.13 found via the py launcher -- using whatever
    echo "python" currently resolves to:
)

where python
python --version

for /f "tokens=2 delims= " %%V in ('python --version 2^>^&1') do set PY_VER=%%V
echo %PY_VER% | findstr /B "3.14" >nul
if %ERRORLEVEL% EQU 0 (
    echo.
    echo ============================================================
    echo   WARNING: about to build with Python 3.14, which is only
    echo   EXPERIMENTALLY supported by the current Nuitka release. The
    echo   build itself may succeed while the resulting GeoGrid.exe
    echo   fails to start with no visible error ^(this has been
    echo   observed^).
    echo.
    echo   Strongly recommended: install Python 3.12 or 3.13 via the
    echo   official installer from python.org ^(check "Add python.exe
    echo   to PATH" AND leave "Install launcher for all users" checked
    echo   so the py launcher can find it^), then run this script again.
    echo ============================================================
    echo.
    set /p CONTINUE_314="Continue with Python 3.14 anyway? [y/N]: "
    if /I not "%CONTINUE_314%"=="y" (
        echo Build cancelled. Install Python 3.12 or 3.13 and try again.
        pause
        exit /b 1
    )
)

echo.
echo [2/4] Installing/updating build dependencies (this may take a few minutes)...
python -m pip install --upgrade pip -q
python -m pip install -r requirements.txt -q
if %ERRORLEVEL% NEQ 0 (
    echo ERROR: failed to install requirements.txt dependencies.
    pause
    exit /b 1
)
python -m pip install nuitka cython setuptools -q
if %ERRORLEVEL% NEQ 0 (
    echo ERROR: failed to install nuitka/cython/setuptools.
    pause
    exit /b 1
)

echo.
echo [3/4] Checking the license signing key is configured...
findstr /C:"REPLACE_WITH_BASE64_PUBLIC_KEY" shp2dgs\license\crypto_utils.py >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    echo.
    echo ============================================================
    echo   WARNING: No real signing key is configured yet.
    echo ============================================================
    echo   shp2dgs\license\crypto_utils.py still has the placeholder
    echo   TRUSTED_PUBLIC_KEYS entry. GeoGrid.exe will build, but no
    echo   license you issue will actually validate against it.
    echo.
    echo   To fix this before building:
    echo     1. cd ..\GeoGridLicenseManager
    echo     2. python cli.py keygen --key-id ggkey-2026-01
    echo     3. Paste the printed public key into crypto_utils.py
    echo        ^(replacing REPLACE_WITH_BASE64_PUBLIC_KEY^)
    echo.
    set /p CONTINUE_ANYWAY="Continue building anyway? [y/N]: "
    if /I not "%CONTINUE_ANYWAY%"=="y" (
        echo Build cancelled.
        pause
        exit /b 1
    )
)

echo.
echo [4/4] Running the full build pipeline (Core compile + Nuitka)...
echo.
call build_tools\build_release.bat
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo ============================================================
    echo   BUILD FAILED -- see the errors above.
    echo ============================================================
    pause
    exit /b 1
)

echo.
echo ============================================================
echo   BUILD SUCCEEDED
echo ============================================================
echo   GeoGrid.exe          : dist\shp_to_dgs.dist\GeoGrid.exe
echo   GeoGrid_Activate.exe : dist\shp_to_dgs.dist\GeoGrid_Activate.exe
echo.
echo   Remaining manual steps (see docs\developer\architecture.md):
echo     - Authenticode-sign both .exe files (optional but recommended)
echo     - Build the integrity manifest (build_tools\make_manifest.py)
echo     - Package dist\shp_to_dgs.dist with your installer
echo ============================================================
echo.
pause

endlocal
