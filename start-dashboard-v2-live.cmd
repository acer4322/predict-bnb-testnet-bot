@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-dashboard-v2-live.ps1"
if errorlevel 1 pause
