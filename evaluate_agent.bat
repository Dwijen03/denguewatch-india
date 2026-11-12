@echo off
setlocal EnableDelayedExpansion
title DengueWatch India - Evaluate the AI agent (Step 11)
cd /d "%~dp0"

rem Use the .venv environment if setup created one, otherwise the conda environment.
if exist ".venv\Scripts\activate.bat" (
    call ".venv\Scripts\activate.bat"
) else (
    set "CONDA_ACT="
    for /f "usebackq delims=" %%A in (`powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\find_conda.ps1"`) do set "CONDA_ACT=%%A"
    if not defined CONDA_ACT ( echo Python environment not found. Run setup_windows.bat first. & pause & exit /b 1 )
    call "!CONDA_ACT!"
    call conda activate denguewatch
)

echo Asking the AI agent the test questions (about 4-8 minutes)...
echo.
python scripts\evaluate_agent.py %*
pause
