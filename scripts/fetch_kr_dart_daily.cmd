@echo off
rem Korean fundamental research: one day of Open DART downloads (scheduled task AutoTrading_KR_DART).
rem Resumable: once everything is downloaded it exits with 0 requests. Daily cap is enforced in scripts\fetch_kr_dart.py.
cd /d "%~dp0\.."
if not exist data\research\kr_dart mkdir data\research\kr_dart
echo ==== %date% %time% >> data\research\kr_dart\scheduled.log
.venv\Scripts\python.exe -m scripts.fetch_kr_dart filings >> data\research\kr_dart\scheduled.log 2>&1
.venv\Scripts\python.exe -m scripts.fetch_kr_dart fin >> data\research\kr_dart\scheduled.log 2>&1
.venv\Scripts\python.exe -m scripts.fetch_kr_dart xbrl >> data\research\kr_dart\scheduled.log 2>&1
