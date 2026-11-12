@echo off
rem Called by Windows Task Scheduler (see schedule_windows_task.bat). Runs the due jobs with no pause
rem and appends everything it prints to data\processed\daily_update_log.txt
cd /d "%~dp0.."
set "DW_NO_PAUSE=1"
echo ===== %DATE% %TIME% ===== >> "data\processed\daily_update_log.txt"
call "%~dp0..\daily_update.bat" >> "data\processed\daily_update_log.txt" 2>&1
