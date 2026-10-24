# 117 — AG Paper: 소액 공격형 세 계좌를 앞으로의 자료로 기록 (가상 1,000 USD 씩) — 등록

작성: 2026-10-04 (Opus) · 트랙: Paper (주문 없음) · 상태: **관찰 중 (AG-US 2026-10-02 미국 종가 · AG-CR 2026-10-02 UTC 일봉 시작).**
사용자 (2026-10-04): "AG Paper와 A도 만들어줘". 규칙: [112](112-aggressive-small-account-preregistration.md) §1·§2 그대로.
A(매달 QQQ)와 AG-US 는 이미 [116 QS Paper](116-qs-paper-registration.md) 장부에 있다 — A 는 QS Paper 화면, AG-US 는 두 화면 모두에 나온다.

## 1. 계좌 (112 그대로)

| 계좌 | 원장 | 규칙 |
|---|---|---|
| **AG-US** | QS Paper 의 AG 장부 (`data/qs_paper/state.json`) | QQQ T 규칙 켜짐 → TQQQ 전액, 꺼짐 → 현금. 실제 TQQQ 종가. 공제 1,786 USD 넘는 실현 이익 22% |
| **AG-CR** | 오버레이 Paper 원장 `ag_cr3` (DB `paper_overlay_days`, `run_paper_o1 --ledger ag`) | T/v2 신호·크기에 노출 3 배, 추가분은 무기한 롱. `OverlayConfig(exposure=3.0, catastrophe_stop=0.20, halt_drawdown=1.0)` — 112 `CR3` 와 같다. 공개 시세만, 키 없음. 원장은 10,000 으로 계산하고 1,000 USD 로 환산(수익률 같음) |
| **AG-MIX** | 위 둘의 값으로 계산 | 두 계좌 값이 모두 있는 첫날 500·500, 해마다 기념일 뒤 첫날 50:50 으로 다시 맞춘다. 코인 날짜(매일)를 따르고 미국은 직전 종가 |

- **정리선 (112 §2)**: 평가액이 처음 돈의 30%(300 USD) 이하가 된 날 정리하고 그 값으로 멈춘다. 셋 다 따로 본다.
- 계산 `ops/ag_paper.py`(시험 `tests/ops/test_ag_paper.py` — 환산·정리선·반반 재조정·주말 미국 종가 이어 쓰기).
- 112 백테스트와 다른 점(작다): AG-MIX 재조정 때 백테스트는 두 계좌를 새로 시작했지만(세금 연도·선물 상태 초기화), Paper 는 각 원장의 수익률을 그대로 이어 쓴다.

## 2. 운영

- `python -m scripts.ag_paper daily` = AG-CR 원장 하루 진행(빠진 날은 보유만 계산 `MISSED`, 30 일 넘게 빠지면 멈추고 수동 확인) + 보고서. 매일 **21:17 KST** (QS Paper 21:15 뒤 — AG-US 값을 읽으므로).
- 코인은 매일(주말 포함) 09:00 KST 마감 일봉이라 작업도 매일 돈다.
- 보고 `reports/ag_paper/<날짜>.json` · `latest.json` (계좌별 평가액·고점 대비·처음 돈 대비 최저·정리 여부·경로), 대시보드 해외 주식 → **AG Paper** (`/ag-paper`).

## 3. 점검

- 정리선(300 USD)에 걸리면 바로 보고.
- 6 개월마다: 112 표의 같은 기간 분포와 비교 (기술 통계 — 판정 아님).

## 4. 시작 기록 (2026-10-04 00:30 KST 실행)

- AG-CR 첫 행 2026-10-02 UTC 일봉: `NO_PRIOR_DECISION`(첫날 — 체결 없음), 다음 목표 BTC 36% · ETH 26% (×3 = 108% · 79%), 가상 1,000 USD.
- AG-US 997.5 USD (10-02 TQQQ 12.31 주). AG-MIX 1,000 USD.

## 5. 예약 작업 (사용자가 PowerShell 에서 등록)

```powershell
$a = New-ScheduledTaskAction -Execute "<사용자 홈>\Documents\GitHub\Auto_Trading\.venv\Scripts\python.exe" -Argument "-m scripts.ag_paper daily" -WorkingDirectory "<사용자 홈>\Documents\GitHub\Auto_Trading"
$t = New-ScheduledTaskTrigger -Daily -At 21:17
$s = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun
Register-ScheduledTask -TaskName "AutoTrading_AG_Paper" -Action $a -Trigger $t -Settings $s
```

## 6. 4 배 장부 추가 (2026-10-04)

사용자 (2026-10-04): AG-CR 4 배와 4 배짜리 AG-MIX 를 3 배 장부들과 나란히 기록.

- **AG-CR4**: 원장 `ag_cr4`, `OverlayConfig(exposure=4.0, catastrophe_stop=0.20, halt_drawdown=1.0, max_futures_leverage=4.0)` — 112 §7 의 4 배와 같다. **AG-MIX4**: AG-US 반 + AG-CR4 반. 정리선 같음.
- 같은 예약 작업(`ag_paper daily`)이 두 원장을 함께 진행한다. 첫 행 2026-10-02 UTC (`NO_PRIOR_DECISION`).
- 참고 계산(결과를 본 뒤, 판정 아님 — 112 연구 시뮬레이터, 자료 끝 2026-08-31, 1,000 USD):

| 시작 | 1 배 (T/v2) | 3 배 | 4 배 | 고점 대비 최악 (1·3·4 배) |
|---|---:|---:|---:|---|
| 2025-09 (최근 1 년) | 958 | 848 | 788 | 10% · 30% · 38% |
| 2024-09 (2 년) | 1,174 | 1,367 | 1,550 | 16% · 41% · 51% |
| 2023-09 (3 년) | 1,638 | 2,844 | 3,767 | 16% · 42% · 52% |

  최근 1 년은 코인이 옆으로·아래로 가서 배율이 손실을 키웠다(4 배 최저 669). 2~3 년으로 보면 배율이 이겼다.
