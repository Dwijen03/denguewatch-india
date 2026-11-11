@echo off
title DengueWatch India - Save to GitHub
cd /d "%~dp0"

echo ============================================================
echo   Save your work to GitHub
echo ============================================================
echo.
echo Files that changed since the last save:
git status --short
echo.
set "MSG="
set /p MSG="Describe what changed (e.g. Step 4: data pipeline): "
if "%MSG%"=="" set "MSG=Update project files"

git add .
git commit -m "%MSG%"
git push
echo.
if errorlevel 1 ( echo [FAIL] Push failed - send a screenshot of the messages above. ) else ( echo [ OK ] Saved to https://github.com/Dwijen03/denguewatch-india )
echo.
pause
