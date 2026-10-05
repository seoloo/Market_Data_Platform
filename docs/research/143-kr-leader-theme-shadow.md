# 143 — 국장 주도주·테마 그림자 기록: 가격 · 수급 · AI 판단을 앞으로의 자료로 — 등록

작성: 2026-10-05 (Opus) · 트랙: 그림자 기록 (주문 없음) · 상태: **기록 시작 (2026-10-05) · 예약 작업은 사용자가 등록**
사용자 (2026-10-05): "A랑 B 같이 진행해줘" — A 는 [142](142-kr-flow-leaders-preregistration.md)(수급, 과거 자료로 1 회 판정), B 가 이 문서.
방향 기준점: [DIRECTION_LEADER_TREND](DIRECTION_LEADER_TREND.md). 미국판: [115 LT Paper](115-lt-paper-registration.md).

## 1. 왜

- 주도주·테마를 **오르기 전·초반에** 알아보는 것이 핵심인데, 뉴스·테마 판단은 과거 자료로 공정하게 시험할 수 없다(AI 가 결과를 안다, 지금의 테마 목록은 지나고 만든 것).
- 그래서 **오늘부터 매일 기록하고 몇 달 뒤 채점**한다. 앞으로의 자료라 생존 편향·사후 지식이 없다.
- 같은 날 세 판단을 나란히 남겨 무엇이 나았는지 비교한다.

## 2. 매일 기록하는 것 (`python -m scripts.kr_shadow daily`)

| | 무엇 | 규칙 |
|---|---|---|
| ① 가격 주도주 | 141 LT-KR 점수(1·3·6 달 수익 백분위) → 섹터 중앙값 상위 3 → 상위 10 | 141 코드 그대로 |
| ② 수급 주도주 | 142 FLOW 점수(외국인 + 기관 순매수 ÷ 거래대금, 20·60 일) → 같은 두 단계 | 142 코드 그대로 |
| ③ AI 판단 | 그날 모은 **한국 경제 뉴스 제목**(Google 뉴스 RSS 한국어 검색 6 개: 코스피 증시·주도주·테마주 강세·외국인 순매수·업종 강세·증시 전망)과 **종목군(코드·이름)만** 주고 Gemini 에게 주도 테마 3 · 주도주 10 · KOSPI 1~3 달 여지(up·neutral·down)·확신·위험·요약을 묻는다 | 115 와 같은 호출(무료 모델 차례로). **기계 목록·가격 지표는 주지 않는다**(115 에서 AI 가 기계 목록을 베꼈다) |
| 함께 | 종목군 200 · KOSPI 200 일선 위 여부 · 가격·수급 섹터 순위 · 종목군 6 달 수익 중앙값 · 수급 자료가 있는 종목 수 | |
| Paper | ①·② 규칙을 **2026-10-06 부터** 가상 1,000 만 원으로 굴림(141·142 의 `simulate_kr` 그대로 — 매달 마지막 거래일 신호 → 다음 거래일 종가) · 같은 종목군 고르게 들기(EW) | 첫 매수는 2026-10-30 신호 → 11-02 |

- 자료: `data/kr_shadow/` (141·142 연구 자료는 해시가 고정돼 있어 건드리지 않는다). 처음에 141 자료에서 **지금 상장된 보통주 772 개**를 복사하고(`init`), 매일 한국투자증권 **조회 API** 로 이어 받는다. 수급은 거래대금 상위 250 의 최근 100 일을 받아 합친다.
- 보고: `reports/kr_shadow/<날짜>.json` · `latest.json`. 뉴스 `data/kr_shadow/news/<날짜>.jsonl`, AI `data/kr_shadow/ai/<날짜>.json`(질문 해시·사용량 포함).
- 네이버 금융은 robots.txt 가 자동 수집을 막아 쓰지 않는다. KRX Open API(인증키 승인 대기)는 종목별 투자자 매매를 주지 않는다 — 시가총액·KRX 테마 지수는 승인되면 더할 수 있다.
- 지금은 **유가증권시장만**이다. 코스닥은 142 판정 뒤 따로 정한다.

## 3. 운영

- 매 거래일 저녁. 한국 장 마감(15:30) 뒤 투자자별 매매가 확정되는 18 시 이후면 언제 돌려도 같다. 다른 작업(20:45~)과 겹치지 않게 **월~금 20:20** 을 제안한다. 예약 작업은 사용자가 등록한다(아래).
- 실패: 한 종목 실패는 건너뛰고 센다. 뉴스·AI 실패는 기록 전체를 막지 않는다(보고서에 오류를 남긴다).
- 주문은 없다. 이 기록은 실거래 승인 단계와 무관하다. AI 답은 매매에 쓰지 않는다(AGENTS: LLM 이 BUY·SELL 을 내리지 않는다).

```powershell
$a = New-ScheduledTaskAction -Execute "C:\Users\seolh\Documents\GitHub\Auto_Trading\.venv\Scripts\python.exe" -Argument "-m scripts.kr_shadow daily" -WorkingDirectory "C:\Users\seolh\Documents\GitHub\Auto_Trading"
$t = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At 20:20
Register-ScheduledTask -TaskName "AutoTrading_KR_Shadow" -Action $a -Trigger $t
```

## 4. 채점 (미리 정함)

- **기본 단위:** 매달 마지막 거래일의 ①·②·③ 목록(각 최대 10 종목)을 그날 종가에 똑같이 나눠 샀다고 보고, **다음 1 달 · 3 달 수익 − 같은 종목군 EW 수익**(초과 수익). AI 가 고른 종목이 종목군 밖이면 뺀다(따로 센다).
- **AI 의 KOSPI 여지:** up·neutral·down 과 다음 1·3 달 KOSPI 수익의 관계.
- **Paper:** ①·② Paper vs EW vs KOSPI.
- **일정:** 2027-01 첫 보고(3 번의 월말) · 2027-04 중간 보고(6 번) · **2027-10 판단(12 번의 월말, 1 달 초과 수익 기준)**.
- **판단 기준 (2027-10):** 어떤 목록이 "쓸 만하다" = 12 번 중 **8 번 이상** 1 달 초과 수익 > 0 **이고** 12 번 평균 1 달 초과 수익 > 0. 셋 다 미달이면 이 방식의 주도주 판단은 접는다. 하나라도 통과하면 그 목록으로 실거래 전 별도 사전 등록을 한다(바로 매매하지 않는다).
- 중간 보고(2027-01·04)는 기술 통계일 뿐, 규칙·질문·뉴스 검색어를 바꾸는 근거로 쓰지 않는다. 바꾸면 새 기록으로 다시 시작한다.

## 5. 코드

- `scripts/kr_shadow.py` — `init` · `update` · `flows` · `run` · `news` · `ai` · `daily` · `status`.
- `src/auto_trading/ops/news_ai_kr.py` — 한국어 질문(순수 함수). 답 검사는 115 의 `parse_reply` 그대로.
- `scripts/run_lt_kr.py` — `build_panel(data, to)` · `simulate_kr(..., start_date)` · `equal_weight_pit(..., start_date)` 로 폴더·시작일을 넘길 수 있게 했다(기본값은 141 그대로, 141 결과 같음 확인). 끝난 날 보유·다음 날 매도 예정을 `Run` 에 남긴다.
- `scripts/fetch_kr_stocks.py` — 지수 받기를 `index_pages` 로 떼어 냈다.
- 시험 `tests/research/test_kr_shadow.py` (CSV 이어 붙이기, 한국어 뉴스 주소, 질문에 기계 목록이 없음, 종목군 밖 코드 제외).

## 6. 시작 기록

(첫 실행 결과를 여기에 적는다)
