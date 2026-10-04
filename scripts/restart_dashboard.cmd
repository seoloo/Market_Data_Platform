@echo off
rem Restart the windowless dashboard so it picks up new code. Trading, Paper and drills are not affected.
call "%~dp0stop_dashboard.cmd"
ping -n 3 127.0.0.1 >nul
call "%~dp0open_dashboard.cmd"
