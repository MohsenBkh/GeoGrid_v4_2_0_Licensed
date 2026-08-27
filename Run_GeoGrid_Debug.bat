@echo off
REM ==========================================================================
REM  Run_GeoGrid_Debug.bat -- captures GeoGrid.exe's console output to a file
REM ==========================================================================
REM  Use this if GeoGrid.exe's own window closes too fast to read (even a
REM  debug build with a console window will close immediately once the
REM  process exits/crashes). This launches GeoGrid.exe, saves everything
REM  it prints to geogrid_run_log.txt in this same folder, and keeps this
REM  window open afterwards so you can read it (or just open the .txt
REM  file directly).
REM
REM  This file is placed automatically next to GeoGrid.exe by
REM  build_tools\build_release.bat -- just double-click it there.
REM ==========================================================================

setlocal
set LOG=%~dp0geogrid_run_log.txt

echo Starting GeoGrid.exe, saving output to geogrid_run_log.txt ...
echo.

"%~dp0GeoGrid.exe" > "%LOG%" 2>&1
set RESULT=%ERRORLEVEL%

echo.
echo ============================================================
echo   GeoGrid.exe exited with code %RESULT%
echo ============================================================
echo   Output (also saved to geogrid_run_log.txt):
echo ------------------------------------------------------------
type "%LOG%"
echo ------------------------------------------------------------
echo.
if %RESULT% NEQ 0 (
    echo   Send the text above ^(or the geogrid_run_log.txt file^) back
    echo   for diagnosis.
) else (
    echo   Exit code 0 -- if the app window still didn't visibly appear,
    echo   it may have opened and closed very quickly; the log above
    echo   should still show what happened during startup.
)
echo.
pause

endlocal
