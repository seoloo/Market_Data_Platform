# 148 — 주간 미래 주도 테마 알림 (국장 + 미국) — 정보 도구 등록

작성: 2026-10-05 (Opus) · 트랙: 정보 도구 (매매 아님, 주문 없음) · 상태: **만듦 — 매주 토요일 09:00 KST 예약 작업은 사용자가 등록.**
사용자 (2026-10-05): "아니면 ai가 미래 주도주 및 테마를 판단하고 내게 알림을 주도록 할까? 일주일 당 한번씩? 미래 주도주 및 테마로 생각한 이유, 근거, 판단, 미래 주도주 추천 이런식으로" → 시장 **국장 + 미국**, 147 장부는 **그대로**.

## 1. 무엇을 하나 (`python -m scripts.theme_alert weekly`)

1. 지난 7 일 동안 이미 모은 뉴스 제목을 쓴다 — 국장 `data/kr_shadow/news`(143), 미국 `data/lt_paper/news`(115). 새로 받지 않는다. 중복 제목을 빼고 최근 것부터 시장마다 최대 200 개에 번호(K1…, U1…)를 붙인다.
2. AI(Gemini, 없으면 OpenAI)에게 **뉴스 제목과 종목군(코드:이름)만** 주고 묻는다: 앞으로 6~24 달 주도할 테마 최대 3 개(왜·근거 번호·확신) · 주목 종목 최대 5 개(테마·이유·근거 번호) · 위험. 가격·주도주 목록 같은 우리 계산은 주지 않는다.
3. 답 검사(코드): 종목군 밖 코드, 없는 뉴스 번호, **근거 번호가 없는 주장**은 뺀다(뺀 개수 기록). 근거 번호는 코드가 **실제 뉴스 제목**으로 바꿔 붙인다 — AI 가 지어낸 근거가 알림에 실리지 않게.
4. 종목마다 **우리 자료로 확인한 사실**을 코드가 붙인다: 추세 위/아래(T 규칙 + 50 일선) · 3 달(63 거래일) 수익 · 가격·수급 주도주(국장 143) 또는 LT 주도주(미국 115) 목록에 있는지.
5. 지난주 알림과 견줘 새로 나온·빠진 테마와 종목.
6. 텔레그램 두 통(국장 · 미국) + `reports/theme_alert/<날짜>.json` · `latest.json` · `outbox.log`. 실패해도 텔레그램으로 실패를 알린다.

## 2. 지키는 것

- **검증된 매매 신호가 아니다** — 알림 끝에 적는다. 146 에서 그럴듯한 패턴(삼각형 돌파)이 무작위보다 나빴다.
- AI 판단의 성적은 147 장부(매일 AI 판단 → 매달 적립, 2028-10 판단)가 따로 쌓는다. 이 주간 알림의 질문은 147 의 질문과 다르다(147 은 143 의 매일 질문을 쓴다) — 이 알림의 문구를 바꿔도 147 은 바뀌지 않는다.
- 프로젝트 규칙: AI 가 매수·매도를 직접 정하지 않는다. 이 알림은 사람이 읽는 정보다.

## 3. 첫 실행 (2026-10-05 23시, `--no-telegram`)

- 국장: 반도체(90%) · 전력기기(80%) · 화장품(70%), 종목 삼성전자 · SK하이닉스 · HD현대일렉트릭 · 아모레퍼시픽. 미국: AI·반도체 인프라(90%) · 비만 치료제(70%), 종목 NVDA · MU · LLY. 뺀 항목 0.
- **먼저 보인 약점:** 뉴스 기반이라 **지금 이미 유명한 주도주**(삼성전자·엔비디아)가 주로 나온다 — "미래" 보다 "현재"에 가깝다. 147 의 성적과 함께 본다.

## 4. 코드

`src/auto_trading/ops/theme_alert.py`(순수 함수 — 번호 붙이기 · 질문 · 답 검사 · 지난주 비교 · 메시지), `scripts/theme_alert.py`. 시험 `tests/ops/test_theme_alert.py`(번호·중복, 질문에 우리 계산 없음, 종목군 밖·근거 없는 주장 제거, 실제 제목·우리 사실이 메시지에, 지난주 비교).

## 5. 예약 작업 (사용자, 관리자 PowerShell)

```powershell
$repo = "C:\Users\seolh\Documents\GitHub\Auto_Trading"
$a = New-ScheduledTaskAction -Execute "$repo\.venv\Scripts\pythonw.exe" -Argument "`"$repo\scripts\task_launcher.py`" -m scripts.theme_alert weekly" -WorkingDirectory $repo
$t = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Saturday -At 09:00
$s = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun
$p = New-ScheduledTaskPrincipal -UserId "seolh" -LogonType S4U -RunLevel Limited
Register-ScheduledTask -TaskName "AutoTrading_Theme_Alert" -Action $a -Trigger $t -Settings $s -Principal $p
```
