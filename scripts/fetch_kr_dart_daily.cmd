@echo off
rem 한국 실적 연구 DART 받기 하루분 (예약 작업 AutoTrading_KR_DART). 이어 받기 — 다 받으면 요청 0 으로 바로 끝난다.
rem 하루 한도(1 만 9 천)는 scripts\fetch_kr_dart.py 가 지킨다. 키는 .env 에서만 읽는다.
cd /d "%~dp0\.."
if not exist data\research\kr_dart mkdir data\research\kr_dart
echo ==== %date% %time% >> data\research\kr_dart\scheduled.log
.venv\Scripts\python.exe -m scripts.fetch_kr_dart filings >> data\research\kr_dart\scheduled.log 2>&1
.venv\Scripts\python.exe -m scripts.fetch_kr_dart fin >> data\research\kr_dart\scheduled.log 2>&1
.venv\Scripts\python.exe -m scripts.fetch_kr_dart xbrl >> data\research\kr_dart\scheduled.log 2>&1
