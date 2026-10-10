@echo off
rem Open a fresh latest-source Orbit UI. No privileged old server or trading process is stopped.
start "" "%~dp0..\dist\AutoTrading.exe" --restart-dashboard
