@echo off
setlocal DisableDelayedExpansion
cd /d "%~dp0"
if errorlevel 1 (
    echo Cannot open the GEAR folder.
    pause
    exit /b 3
)

if not exist ".venv\Scripts\python.exe" (
    echo GEAR requires a local Python environment. Run these commands in this folder:
    echo   python -m venv .venv
    echo   .venv\Scripts\python.exe -m pip install -e ./doc/contracts -e ".[gui,test]"
    pause
    exit /b 3
)

".venv\Scripts\python.exe" -m gear_framework gui --app-dir .
set "GEAR_EXIT_CODE=%ERRORLEVEL%"
if not "%GEAR_EXIT_CODE%"=="0" (
    echo.
    echo GEAR exited with an error. See the message above and README.md for setup.
    pause
)
exit /b %GEAR_EXIT_CODE%
