@echo off
title DengueWatch India - Windows scheduled task (Step 10)
cd /d "%~dp0"

rem Creates a Windows Task Scheduler task "DengueWatch daily update" that runs daily_update.bat
rem every day at 09:15 (and on the next start if the laptop was off), so data stays fresh
rem even when the app is not open. Run this file once. To remove the task again:
rem     schtasks /Delete /TN "DengueWatch daily update" /F

set "TASK=DengueWatch daily update"
schtasks /Create /TN "%TASK%" /TR "\"%~dp0scripts\scheduled_update.cmd\"" /SC DAILY /ST 09:15 /F
if errorlevel 1 ( echo Could not create the task. & pause & exit /b 1 )

rem Allow the task to run late if the laptop was off at 09:15, and on battery power.
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$s = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 2); Set-ScheduledTask -TaskName '%TASK%' -Settings $s | Out-Null"

echo.
echo Task created. It runs every day at 09:15 and writes its log to data\processed\daily_update_log.txt
echo Check it:   schtasks /Query /TN "%TASK%"
echo Run it now: schtasks /Run /TN "%TASK%"
pause
