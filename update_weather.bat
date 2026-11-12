@echo off
setlocal EnableDelayedExpansion
title DengueWatch India - Weather update (Step 6)
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

echo Downloading daily weather for all 640 districts.
echo First run: about 30-60 minutes. You can close this window and run it again later -
echo it continues where it stopped.
echo.
python scripts\update_weather.py %*
pause
