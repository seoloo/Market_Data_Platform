@echo off
rem Korean fundamental research: one day of Open DART downloads (scheduled task AutoTrading_KR_DART).
rem Resumable: once everything is downloaded it exits with 0 requests. Daily cap enforced in scriptsetch_kr_dart.py.
cd /d "%~dp0\.."
if not exist dataesearch\kr_dart mkdir dataesearch\kr_dart
echo ==== %date% %time% >> dataesearch\kr_dart\scheduled.log
.venv\Scripts\python.exe -m scripts.fetch_kr_dart filings >> dataesearch\kr_dart\scheduled.log 2>&1
.venv\Scripts\python.exe -m scripts.fetch_kr_dart fin >> dataesearch\kr_dart\scheduled.log 2>&1
.venv\Scripts\python.exe -m scripts.fetch_kr_dart xbrl >> dataesearch\kr_dart\scheduled.log 2>&1
