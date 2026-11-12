@echo off
setlocal EnableDelayedExpansion
title DengueWatch India - Train model (Step 7)
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

python -m pip install -q -r requirements.txt
echo Training the dengue early-warning model and scoring all districts (2-5 minutes)...
echo.
python scripts\train_model.py %*
if not errorlevel 1 python scripts\plot_model_results.py
pause
