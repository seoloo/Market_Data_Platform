# 177 — 나스닥 + 추세 추종 소매 Paper (176 B 의 앞으로의 확인) — 등록

작성: 2026-10-09 (Claude) · 트랙: Paper (돈 · 주문 없음) · 상태: **기록 시작 (첫 기록 = 2026-10-08 미국 종가). 예약 작업은 사용자가 등록.**
근거: [176](176-multi-asset-trend-preregistration.md) B — 나스닥 100 + AQR TSMOM 소매 20% 가 1986 ~ 2026-05 에서 샤프 0.57 → 0.68 · 낙폭 81 → 68% 로 통과했다. 단 소매 근거는 AQR **지수**이고, 살 수 있는 ETF 는 2019 년 뒤에만 있다.
사용자 (2026-10-09): "b 진행해줘".

## 1. 왜 Paper 인가

- 176 의 통과는 '과거 지수로는 지지' 다. 실제 ETF 가 그 지수처럼 주식과 따로 움직이며 하락 때 버팀목이 되는지는 **앞으로의 자료**로만 확인할 수 있다. 데이터 열람 장부상 2026-09-01 뒤가 전향 확인 구간이다.
- 그래서 실거래 전에 실제 상품 가격으로 가상 장부를 굴린다.

## 2. 장부 (`scripts/mf_paper.py run`, 순수 계산 `ops/mf_paper.py`)

| 장부 | 구성 | 시작 |
|---|---|---|
| **MIX** | QNDX 80% + DBMF · KMLM · CTA 각 6.67% (소매 20%) | 1,000 USD |
| **BASE** | QNDX 100% | 1,000 USD |

- **소매 세 ETF**
  - DBMF(iMGP DBi, 2019 ~, 대형 CTA 지수 복제) · KMLM(KraneShares Mount Lucas 지수, 2020 ~) · CTA(Simplify, 2022 ~).
  - 운용 방식이 서로 달라 한 상품에 기대지 않게 고르게 나눴다.
- 주식은 사용자가 실제로 적립하는 QNDX(SPDR 나스닥 100) 다.
- 시세: Yahoo 일봉 배당 조정 종가(키 없음). 장중 봉은 쓰지 않는다. 빈 날은 직전 값이다.
- **매달 첫 거래일 종가**에 목표 비중으로 다시 맞춘다. 비용 편도 0.15%(수수료 0.10% + 환전 0.05%, 176 과 같음). 세금은 Paper 에 넣지 않는다(실거래 때 22% · 250 만 원 공제).
- 상태 `data/mf_paper/state.json` · 보고 `reports/mf_paper/latest.json`.

## 3. 운영

- 권장 예약: **화 ~ 토 07:40 KST**(미국 마감 뒤, 시장 브리핑 07:30 다음). 하루 빠져도 다음 실행이 밀린 날을 모두 처리한다.

```powershell
$a = New-ScheduledTaskAction -Execute "C:\Users\seolh\Documents\GitHub\Auto_Trading\.venv\Scripts\pythonw.exe" -Argument "`"C:\Users\seolh\Documents\GitHub\Auto_Trading\scripts\task_launcher.py`" -m scripts.mf_paper run" -WorkingDirectory "C:\Users\seolh\Documents\GitHub\Auto_Trading"; $t = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Tuesday,Wednesday,Thursday,Friday,Saturday -At 07:40; Register-ScheduledTask -TaskName "AutoTrading_MF_Paper" -Action $a -Trigger $t -Description "나스닥 + 추세 추종 소매 Paper (문서 177, 돈 없음)"
```

## 4. 판단 (미리 정함, `python -m scripts.mf_paper judge`)

판단 시점은 **2027-10**(12 개월) 첫 판단, **2028-10**(24 개월) 최종이다. 월 수익으로 본다.

| # | 기준 | 뜻 |
|---|---|---|
| J1 | 소매(세 ETF 고르게) ↔ QNDX 월 수익 상관 ≤ 0.3 | 주식과 따로 움직이는가(분산 효과의 전제) |
| J2 | 소매 ↔ AQR TSMOM 월 수익 상관 ≥ 0.5 | 176 에서 시험한 지수를 실제 ETF 가 따라가는가 |
| J3 | MIX 최대 낙폭 ≤ BASE **그리고** MIX 샤프 ≥ BASE 샤프 − 0.10 | 섞어서 나빠지지 않았는가 |

- 셋 다 → **소액 실거래 검토 자격**(승인 아님 — 상품 · 비중 · 세금을 정하는 별도 등록이 필요).
- J2 실패 → 실제 ETF 가 시험한 지수를 따라가지 않는다 → 176 B 의 근거가 상품으로 옮겨지지 않는다 → 채택하지 않는다.
- 12 개월은 아주 짧다. 2027-10 은 중간 판단이고 24 개월 결과를 우선한다. 중간에 규칙 · 상품 · 비중을 바꾸면 새 장부로 다시 시작한다.

## 5. 먼저 적는 약점

- 소매 ETF 들은 2023 ~ 2025 에 몇 년 연속 손실이 났다(175 §2). 짧은 Paper 기간에 그런 구간이 걸리면 J3 가 실패할 수 있다. 이것도 실제 위험이다.
- Paper 에는 세금 · 환전 시점 · 체결 차이가 없다.
- QNDX 는 2026-06 상장이라 이력이 짧다. 나스닥 100 추종이므로 QQQ 와 거의 같다.
