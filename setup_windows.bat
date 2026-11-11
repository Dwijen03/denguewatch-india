@echo off
setlocal EnableDelayedExpansion
title DengueWatch India - Setup
cd /d "%~dp0"

echo ============================================================
echo   DengueWatch India - one-click setup for Windows
echo ============================================================
echo.

rem ---------- 1. Find a Python 3.11+ installation ----------
rem Option A: Anaconda / Miniconda
set "CONDA_ACT="
for /f "usebackq delims=" %%A in (`powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\find_conda.ps1"`) do set "CONDA_ACT=%%A"

if defined CONDA_ACT (
    echo [ OK ] Found conda: !CONDA_ACT!
    call "!CONDA_ACT!"
    call conda env list | findstr /R /C:"^denguewatch " >nul
    if errorlevel 1 (
        echo Creating Python environment "denguewatch" ^(this takes a few minutes^)...
        call conda create -n denguewatch python=3.11 -y
    ) else (
        echo [ OK ] Environment "denguewatch" already exists
    )
    call conda activate denguewatch
    if errorlevel 1 ( echo [FAIL] Could not activate the conda environment. & goto :end )
    goto :install
)

rem Option B: a normal Python installation (python.org or Microsoft Store)
echo [INFO] Anaconda was not found - looking for a normal Python installation instead.
set "PY="
for %%C in ("py -3.13" "py -3.12" "py -3.11" "python") do (
    if not defined PY (
        %%~C -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1 && set "PY=%%~C"
    )
)
if not defined PY (
    echo.
    echo [FAIL] No Python 3.11 or newer was found on this computer.
    echo        1. Download Python 3.12 from https://www.python.org/downloads/windows/
    echo        2. While installing, TICK the box "Add python.exe to PATH".
    echo        3. Run this file again.
    goto :end
)
echo [ OK ] Found Python: !PY!
if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment in .venv ...
    !PY! -m venv .venv
    if errorlevel 1 ( echo [FAIL] Could not create the virtual environment. & goto :end )
)
call ".venv\Scripts\activate.bat"

:install
rem ---------- 2. Install packages ----------
echo.
echo Installing Python packages ^(this takes a few minutes the first time^)...
python -m pip install --upgrade pip >nul
python -m pip install -r requirements.txt
if errorlevel 1 ( echo [FAIL] Package installation failed. & goto :end )
echo [ OK ] Packages installed

rem ---------- 3. PostgreSQL database and .env ----------
echo.
set "PSQL="
for /f "usebackq delims=" %%A in (`powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\find_psql.ps1"`) do set "PSQL=%%A"
if not defined PSQL (
    echo [WARN] PostgreSQL ^(psql.exe^) was not found on this computer.
    echo        Install it from https://www.postgresql.org/download/windows/ and run this file again.
    goto :check
)
echo [ OK ] Found PostgreSQL: !PSQL!

if exist ".env" (
    echo [ OK ] .env already exists - skipping database creation
    goto :check
)

echo.
echo Now we create the project database.
echo Choose a NEW password for the project database user "denguewatch".
echo Use only letters and numbers, e.g. Dengue2026
set /p DBPASS="New password for denguewatch: "
if "!DBPASS!"=="" ( echo [FAIL] Password cannot be empty. & goto :end )

echo.
echo Next you will be asked for the password of the "postgres" user
echo ^(the one you chose while installing PostgreSQL^). Typing will be invisible - that is normal.
"!PSQL!" -h localhost -U postgres -d postgres -c "CREATE USER denguewatch WITH PASSWORD '!DBPASS!';"
"!PSQL!" -h localhost -U postgres -d postgres -c "CREATE DATABASE denguewatch OWNER denguewatch;"

powershell -NoProfile -Command "(Get-Content '.env.example') -replace 'CHANGE_ME','!DBPASS!' | Set-Content '.env'"
echo [ OK ] .env created

:check
rem ---------- 4. Run the setup check ----------
echo.
echo ============================================================
echo   Running setup check
echo ============================================================
python scripts\check_setup.py

:end
echo.
pause
