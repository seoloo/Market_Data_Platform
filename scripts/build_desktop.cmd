@echo off
rem Build dist\AutoTrading.exe (dashboard desktop window, docs: scripts\desktop_app.py)
rem and a desktop shortcut. Build tools live in build\desktop-venv, not in the project .venv.
cd /d "%~dp0.."
set "REPO=%CD%"
if not exist build\desktop-venv\Scripts\python.exe (
    py -3 -m venv build\desktop-venv || exit /b 1
)
build\desktop-venv\Scripts\python.exe -m pip install -q --disable-pip-version-check "pywebview>=6,<7" "pyinstaller>=6.16,<7" || exit /b 1
> build\repo_path.txt echo %REPO%
build\desktop-venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --onefile --windowed ^
    --name AutoTrading --icon "%REPO%\src\auto_trading\ui\assets\auto-trading.ico" ^
    --add-data "%REPO%\build\repo_path.txt;." ^
    --distpath "%REPO%\dist" --workpath "%REPO%\build\pyinstaller" --specpath "%REPO%\build" ^
    "%REPO%\scripts\desktop_app.py" || exit /b 1
powershell -NoProfile -ExecutionPolicy Bypass -File "%REPO%\scripts\make_desktop_shortcut.ps1"
