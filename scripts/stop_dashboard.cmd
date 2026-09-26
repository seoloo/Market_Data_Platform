@echo off
rem Stop the windowless dashboard. Trading, Paper and drills are not affected.
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe'\" | Where-Object { $_.CommandLine -like '*dashboard.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -ErrorAction SilentlyContinue; 'stopped ' + $_.ProcessId }"
