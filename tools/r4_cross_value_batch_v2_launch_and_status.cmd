@echo off
for /f %%p in ('python tools\launch_r4_cross_value_batch_v2.py') do echo PID=%%p
timeout /t 5 /nobreak >nul
python tools\r4_cross_value_batch_status_v2.py
