@echo off
rem Open the read-only operations dashboard without a console window.
rem If it is already running, only the browser opens.
cd /d "%~dp0.."
start "" ".venv\Scripts\pythonw.exe" scripts\dashboard.py
