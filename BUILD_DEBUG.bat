@echo off
REM ==========================================================================
REM  BUILD_DEBUG.bat -- diagnostic build that SAVES output to a log file
REM ==========================================================================
REM  Use this to figure out why GeoGrid.exe builds successfully but
REM  doesn't appear/start. Everything this script does is written to
REM  debug_build_log.txt in this same folder -- if the window closes
REM  too fast to read, just open that file afterwards. The full log is
REM  also printed to the screen before the final pause.
REM
REM  Just double-click this file and wait for it to finish.
REM ==========================================================================

setlocal
set LOG=%~dp0debug_build_log.txt

echo Running... full output is being saved to:
echo   %LOG%
echo (open that file if this window closes before you can read it)
echo.

call :run_all > "%LOG%" 2>&1
set RESULT=%ERRORLEVEL%

echo.
echo ============================================================
echo   FULL LOG (also saved to debug_build_log.txt)
echo ============================================================
type "%LOG%"
echo ============================================================
if %RESULT% EQU 0 (
    echo   Build steps finished. Now run this and watch what happens:
    echo     dist\shp_to_dgs.dist\GeoGrid.exe
    echo   If it crashes, copy the debug_build_log.txt file and any
    echo   text GeoGrid.exe itself prints and send both back.
) else (
    echo   FAILED -- see the log above ^(or debug_build_log.txt^) for
    echo   the exact error, and send it back.
)
echo ============================================================
echo.
pause
exit /b %RESULT%

REM --------------------------------------------------------------------
:run_all
echo === Environment ===
echo Date/time: %DATE% %TIME%
echo Folder: %CD%
echo.
echo === Python ===
where py 2>&1
where python 2>&1
echo.
py --version 2>&1
python --version 2>&1
echo.
echo === Installing build dependencies ===
python -m pip install --upgrade pip
if errorlevel 1 (
    echo PIP UPGRADE FAILED
    exit /b 1
)
python -m pip install -r requirements.txt
if errorlevel 1 (
    echo REQUIREMENTS INSTALL FAILED
    exit /b 1
)
python -m pip install nuitka cython setuptools
if errorlevel 1 (
    echo NUITKA/CYTHON INSTALL FAILED
    exit /b 1
)
echo.
echo === Running build_tools\build_release.bat debug ===
call build_tools\build_release.bat debug
if errorlevel 1 (
    echo BUILD_RELEASE.BAT FAILED
    exit /b 1
)
echo.
echo === Done ===
exit /b 0
