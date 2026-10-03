# 116 — QS Paper: QQQ 적립 · TQQQ 전환을 앞으로의 자료로 기록 (가상) — 등록

작성: 2026-10-03 (Opus) · 트랙: Paper (주문 없음) · 상태: **관찰 중 (2026-10-02 종가 시작).**
사용자 (2026-10-03): "그래 QS Paper도 만들어줘" — 오늘 연구 중 가장 바로 쓸 수 있는 결과(110 `PASS`)를 앞으로의 자료로 확인.
규칙: [110](110-qqq-tqqq-switch-accumulation-preregistration.md) QS · 비교 A 그대로, [112](112-aggressive-small-account-preregistration.md) AG-US 를 함께.

## 1. 왜

- 110 은 2008~2026 한 번의 강세장 표본으로 통과했다(닷컴 붕괴 없음, 2010 년 뒤 낙폭은 A 보다 14%p 큼). 같은 자료로는 다시 확인할 수 없다.
- 실제로 지금 QNDX 를 사고 있으므로, **같은 돈을 QS 로 했으면 어땠을지**를 나란히 적어 두면 전환할지 판단할 근거가 된다.
- 백테스트는 가상 TQQQ(3 배 − 비용)였다. Paper 는 **실제 TQQQ 종가**를 쓴다.

## 2. 규칙 (엔진 `ops/qs_paper.py`, 바꾸지 않는다)

- 추세: QQQ 20·60·120 거래일 수익 부호 평균 > 0 이면 켜짐. **전날 종가로 계산해 오늘 종가에 실행.**
- **QS**: 매달 첫 처리일에 1,000 USD 입금. 켜짐이면 QQQ 매수, 꺼짐이면 현금으로 둔다. 꺼짐 → 켜짐 날 쌓인 현금 전부 TQQQ 매수, 켜짐 → 꺼짐 날 TQQQ 전부 매도. QQQ 는 팔지 않는다. 해마다 실현 이익 > 0 이면 22%(110 과 같다, 공제 없음).
- **A (비교)**: 같은 입금으로 매달 QQQ 만 산다 — 지금 적립 방식.
- **AG-US**: 처음 1,000 USD 한 번만. 켜짐이면 전액 TQQQ, 꺼짐이면 현금. 해마다 공제 1,786 USD 넘는 실현 이익 22%.
- 수수료 0.25%/회. 세금은 연말(그해 마지막 처리일, 모르고 넘겼으면 새해 첫 처리일 앞)에 현금에서 뺀다.
- 엔진은 110 `simulate`(QS·A)와 112 `us_path`(AG-US)를 **하루씩 재현**한다 — 시험 `tests/ops/test_qs_paper.py` (합성 자료로 최종 평가액·낸 세금 일치, 연말 늦은 정산도 같음).
- 참고: 2008~2026 에 추세는 해마다 약 6 번 꺼졌다 켜졌고 꺼진 날은 20% — TQQQ 매매도 해마다 6 번쯤. QS 와 A 는 추세가 켜져 있는 동안 똑같고, 꺼지는 날부터 갈린다.

## 3. 운영

- `python -m scripts.qs_paper daily` = 한국투자증권 QQQ·TQQQ 최근 일봉(읽기 전용, 수정주가가 바뀌면 처음부터 다시) + 엔진. 매일 **21:15 KST (화~토)** — LT Paper(21:10) 바로 뒤.
- 자료 `data/qs_paper/bars/` (110 연구 자료 `data/research/tradfi/` 는 건드리지 않는다), 상태 `data/qs_paper/state.json`, 보고 `reports/qs_paper/<날짜>.json` · `latest.json`.
- 보고: 장부별 평가액·넣은 돈·QQQ/TQQQ/현금·낸 세금·오늘 한 일·다음 거래일 할 일, 추세(20·60·120 일 수익).
- 대시보드 해외 주식 → **QS Paper** (`/qs-paper`).
- 실제 주문은 없다. 실거래 승인 단계와 무관하다.

## 4. 점검

- 추세가 바뀔 때(TQQQ 매매가 생길 때) 짧게 보고.
- 6 개월마다: QS vs A vs AG-US 평가액·낙폭 (기술 통계 — 판정은 아님). 실제 TQQQ 와 가상 TQQQ 차이도 적는다.

## 5. 시작 기록 (2026-10-03 실행, 2026-10-02 종가 기준)

- `init`(110 자료 복사) → `update`(QQQ·TQQQ 각 23 거래일 붙임, 다시 받기 없음) → `run`.
- 추세 켜짐 (10-01 종가 기준으로 실행; 10-02 종가 20 일 +4.6% · 60 일 +3.7% · 120 일 +21.7%).
- QS · A: 입금 1,000 → QQQ 매수 (QQQ 749.58). AG-US: 1,000 → TQQQ 매수 (TQQQ 81.01). 셋 모두 평가 997.5 USD (수수료).

## 6. 예약 작업 (사용자가 PowerShell 에서 등록)

```powershell
$a = New-ScheduledTaskAction -Execute "C:\Users\seolh\Documents\GitHub\Auto_Trading\.venv\Scripts\python.exe" -Argument "-m scripts.qs_paper daily" -WorkingDirectory "C:\Users\seolh\Documents\GitHub\Auto_Trading"
$t = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Tuesday,Wednesday,Thursday,Friday,Saturday -At 21:15
Register-ScheduledTask -TaskName "AutoTrading_QS_Paper" -Action $a -Trigger $t
```

PC 가 꺼져 있던 날은 다음 실행이 밀린 거래일을 모두 처리한다.
