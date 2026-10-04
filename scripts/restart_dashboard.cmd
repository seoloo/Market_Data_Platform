@echo off
rem Restart the windowless dashboard so it picks up new code. Trading, Paper and drills are not affected.
call "%~dp0stop_dashboard.cmd"
timeout /t 2 /nobreak >nul
call "%~dp0open_dashboard.cmd"
