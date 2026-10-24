# 115 — LT Paper: 주도주 추세 보유를 앞으로의 자료로 기록 (가상 1,000 USD) — 등록

작성: 2026-10-03 (Opus) · 트랙: Paper (주문 없음) · 상태: **관찰 중 (2026-10-02 종가 시작) · 뉴스 수집·AI(Gemini) 판단 기록 가동 · 예약 작업 등록됨.**
사용자 (2026-10-03): "둘 다 진행해줘" — (1) S&P 400 재현([114](114-leader-trend-sp400-replication.md)) (2) 앞으로의 자료로 Paper 관찰.
방향 기준점: [DIRECTION_LEADER_TREND](DIRECTION_LEADER_TREND.md). 규칙: [113](113-leader-trend-hold-preregistration.md) 그대로.

## 1. 왜

- 과거 자료(113)로는 결론이 나지 않았다(샤프·낙폭은 이겼지만 2008~2016 반쪽에서 짐). 같은 자료로는 다시 판정할 수 없다.
- 앞으로의 자료는 **생존 편향이 없다** — 오늘 고른 주도주가 앞으로 어떻게 되는지 그대로 기록된다.
- **뉴스·AI 판단**도 여기서만 공정하게 검증할 수 있다(과거 자료에서는 모델이 결과를 안다).

## 2. 규칙 (113 그대로, 엔진 `ops/lt_paper.py`)

- 유니버스: S&P 500 지금 구성 종목(2026-10-03, 503 개), 섹터 GICS.
- 주도 점수(1·3·6 달 순위 평균) → 주도 섹터 상위 3 → 주도주 상위 10 · 종목 추세(T 규칙 + 50 일선) · SPY 200 일선 위일 때만 새로 산다.
- **그달 첫 거래일**에 전날 종가로 고른 주도주를 그날 종가에 산다(최대 10 종목, 각 목표 10%, 들고 있는 종목은 목표까지 추매). 첫 실행일은 그달 첫날이 아니어도 산다.
- 매일 종가로 판단해 다음 거래일 종가에 판다: 종목 추세 꺾임(T 규칙 ≤ 0) 또는 산 뒤 고점 −35%.
- 비용 0.25%/회. 가상 시작 금액 **1,000 USD**. 세금은 기록만(판정에 안 씀).
- 엔진은 113 백테스트와 **같은 매매를 하루씩 재현**한다(시험 `tests/ops/test_lt_paper.py` — 합성 자료로 매매 목록 일치 확인).

## 3. 운영

- `python -m scripts.lt_paper daily` = 한국투자증권 최근 일봉 받기(읽기 전용) + 엔진. 매일 **21:10 KST (화~토)** — 전날 미국 종가로 판단하므로 아침 7 시 무렵부터 그날 밤 미국 장이 열리기 전(서머타임 22:30 · 겨울 23:30)까지 언제 돌려도 결과가 같다. PC 가 켜져 있는 저녁으로 정했다(2026-10-03 사용자가 07:30 → 21:10 으로 바꿈). 예약 작업 등록은 사용자가 한다(아래 명령).
- 자료 `data/lt_paper/bars/` (111 연구 자료는 건드리지 않는다), 상태 `data/lt_paper/state.json`, 보고 `reports/lt_paper/<날짜>.json` · `latest.json`.
- 보고: 평가액·종목별 수익·고점 대비·다음 날 팔 종목·주도주 목록·주도 섹터·SPY 200 일선·50 일선 위 종목 비율.
- 실제 주문은 없다. 이 Paper 는 실거래 승인 단계와 무관하다.

## 4. 2 단계 — 뉴스·AI 판단 기록 (설계, 사용자 결정 필요)

- 매일 같은 시각에 시장 뉴스 제목을 모으고, AI 에게 "지금 주도 섹터·주도주·증시가 더 오를 여지" 를 물어 **기계 규칙의 주도주와 나란히** 기록한다(`news_shadow`).
- 몇 달 뒤 채점: AI 가 고른 종목·판단이 기계 규칙보다 나았는가.
- 필요: 뉴스 출처(무료 RSS 등)와 AI API 키(`.env`, 사용자가 넣음).

## 5. 점검

- 매달 첫 거래일 뒤: 산 종목·나간 종목·주도 섹터 변화를 짧게 보고.
- 6 개월마다: 같은 기간 SPY·QQQ·고르게 들기와 비교 (기술 통계 — 판정은 아님).

## 6. 시작 기록 (2026-10-03 실행, 2026-10-02 종가 기준)

- `init` → `update`(504 종목 이어 받기, 실패 0) → `run` → `news`.
- 주도 섹터(10-01 종가): 정보기술 · 헬스케어 · 에너지. SPY 200 일선 위. **50 일선 위 종목 비율 24.7%** (넓게 오르는 장은 아니다).
- 가상 매수 10 종목(각 100 USD, 10-02 종가): HPE · MRNA · DELL · ILMN · CRWD · AMD · SMCI · NTAP · VLO · SWKS. 평가 997.5 USD (수수료 0.25%).
- 뉴스: 16 개 출처(고정 6 + 주도주 Yahoo 10)에서 529 건, 실패 0 — `data/lt_paper/news/2026-10-02.jsonl`.

## 7. 예약 작업 (사용자가 PowerShell 에서 등록)

```powershell
$a = New-ScheduledTaskAction -Execute "<사용자 홈>\Documents\GitHub\Auto_Trading\.venv\Scripts\python.exe" -Argument "<사용자 홈>\Documents\GitHub\Auto_Trading\scripts\lt_paper.py daily" -WorkingDirectory "<사용자 홈>\Documents\GitHub\Auto_Trading"
$t = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Tuesday,Wednesday,Thursday,Friday,Saturday -At 21:10
Register-ScheduledTask -TaskName "AutoTrading_LT_Paper" -Action $a -Trigger $t
```

PC 가 꺼져 있던 날은 다음 실행이 밀린 거래일을 모두 처리한다(`run` 은 마지막 처리일 뒤 모든 거래일을 처리). 대시보드 "놓친 작업 다시 실행" 도 `AutoTrading_*` 작업이라 함께 돈다.

## 8. 2 단계 — AI 판단 기록 가동 (2026-10-03)

- 공급자: **Gemini 무료 등급**(`GEMINI_API_KEY`, 사용자 발급). 없으면 ChatGPT(`OPENAI_API_KEY`·`CHATGPT_KEY`). ChatGPT 키는 크레딧이 없어 429 로 거절됐다 — 사용자 선택으로 Gemini.
- 키는 요청 머리말로만 보낸다(주소에 넣지 않음). 값은 로그·보고에 남기지 않는다.
- 모델: 몰림(503)·한도(429)·없음(404)이면 5 초 쉬고 다음 후보로 — `gemini-flash-latest` → `gemini-flash-lite-latest` → `gemini-2.5-flash` → `gemini-2.5-flash-lite` (2026-10-03 모델 목록 조회로 확인). `GEMINI_MODEL` 로 바꿀 수 있다.
- **질문에는 그날 모은 뉴스 제목·요약(최대 150 건)과 S&P 500 종목 목록만 준다.** 첫 호출에서 기계 규칙의 주도주를 함께 줬더니 AI 가 그 10 개를 그대로 베꼈다 → 기계 규칙 결과·가격 지표는 빼서 **독립 판단**이 되게 했다.
- 답(JSON): 주도 섹터 3 · 주도주 10 (이유) · 증시 여지(up·neutral·down) · 확신 · 위험 · 요약. 유니버스 밖 종목은 빼고 따로 적는다.
- 기록 `data/lt_paper/ai/<날짜>.json` (질문 해시·사용량 포함), 보고서 `news_shadow.ai_judgement`, 대시보드 "AI 판단" 칸.
- 첫 기록(10-02 종가, Gemini flash-lite, 약 9,600 토큰): 증시 여지 **중립**(확신 65%) · 주도 섹터 정보기술·커뮤니케이션·경기소비재 · 주도주 NVDA · TSLA · META · GOOGL · AMAT · FTNT · EQIX · PLTR · LRCX · LEN — **기계 규칙과 겹침 0**.
- 채점(몇 달 뒤, 기술 통계): 같은 날 고른 기계 규칙 10 개 vs AI 10 개의 이후 1·3 개월 수익, AI 의 증시 여지 판단 vs 이후 S&P 500.
