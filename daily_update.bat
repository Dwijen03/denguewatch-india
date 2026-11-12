@echo off
setlocal EnableDelayedExpansion
title DengueWatch India - Update data now (Step 10)
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

rem Runs whatever is due: new IDSP reports (weekly), weather + risk scores (daily), retraining (monthly).
rem Extra options are passed on, e.g.  daily_update.bat --status   or   daily_update.bat --job weekly_reports
python scripts\run_jobs.py %*
set "RC=%ERRORLEVEL%"
if /i not "%DW_NO_PAUSE%"=="1" pause
exit /b %RC%
