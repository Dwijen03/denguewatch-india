@echo off
setlocal EnableDelayedExpansion
title DengueWatch India - Database fix
cd /d "%~dp0.."

echo ============================================================
echo   DengueWatch India - reset the project database password
echo ============================================================
echo.

set "PSQL="
for /f "usebackq delims=" %%A in (`powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\find_psql.ps1"`) do set "PSQL=%%A"
if not defined PSQL ( echo [FAIL] Could not find PostgreSQL ^(psql.exe^) on this computer. & goto :end )
echo [ OK ] Found PostgreSQL: !PSQL!
echo.

echo Choose a NEW password for the project database user "denguewatch".
echo Use ONLY letters and numbers ^(no symbols or spaces^), e.g. Dengue2026
set /p DBPASS="New password: "
if "!DBPASS!"=="" ( echo [FAIL] Password cannot be empty. & goto :end )

echo.
echo Now type the password of the "postgres" user - the one you chose while
echo INSTALLING PostgreSQL. The typing is invisible - that is normal. Press Enter after.
echo.
set "SQLF=%TEMP%\denguewatch_fix.sql"
> "!SQLF!" echo DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'denguewatch') THEN CREATE ROLE denguewatch LOGIN; END IF; END $$;
>> "!SQLF!" echo ALTER ROLE denguewatch WITH LOGIN PASSWORD '!DBPASS!';
>> "!SQLF!" echo SELECT 'CREATE DATABASE denguewatch OWNER denguewatch' WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'denguewatch') \gexec
"!PSQL!" -h localhost -U postgres -d postgres -f "!SQLF!"
set "RC=!errorlevel!"
del "!SQLF!" >nul 2>&1
if not "!RC!"=="0" ( echo. & echo [FAIL] Could not log in as postgres. Check that password and run this file again. & goto :end )

powershell -NoProfile -Command "(Get-Content '.env') -replace '^DATABASE_URL=.*$','DATABASE_URL=postgresql+psycopg://denguewatch:!DBPASS!@localhost:5432/denguewatch' | Set-Content '.env'"
echo.
echo [ OK ] Password updated in the database and in .env

echo.
echo ============================================================
echo   Running setup check
echo ============================================================
set "CONDA_ACT="
for /f "usebackq delims=" %%A in (`powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\find_conda.ps1"`) do set "CONDA_ACT=%%A"
if exist ".venv\Scripts\activate.bat" ( call ".venv\Scripts\activate.bat" ) else if defined CONDA_ACT ( call "!CONDA_ACT!" & call conda activate denguewatch )
python scripts\check_setup.py

:end
echo.
pause
