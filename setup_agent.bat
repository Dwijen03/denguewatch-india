@echo off
setlocal EnableDelayedExpansion
title DengueWatch India - Set up the AI agent (Step 11)
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
rem Asks for your free Gemini API key (hidden), saves it in .env, adds the Step 11 tables and tests the agent.
rem Use  setup_agent.bat --test  to test again without entering the key.
python scripts\setup_agent.py %*
pause
