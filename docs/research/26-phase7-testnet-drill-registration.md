# 26 — Phase 7 테스트넷 매일 훈련 운영 등록 (2026-09-24)

> **운영 시작 전에 고정했다.** 기간 중 결과를 보고 기준을 바꾸지 않는다.
> 게이트 정의: [BACKTEST_AND_TESTING](../current/BACKTEST_AND_TESTING.md) Phase 7 —
> "1~2주 주문 API·오류·부분체결·스톱·reconcile. 수익성 검증 아님".

| | |
|---|---|
| 대상 | 선물 실행 계층 (`futures_orders`·`futures_guard`·`binance_usdm_testnet`) — 전략이 아니다 |
| 사용자 결정 | 테스트넷 훈련 20/20 뒤 제안한 "매일 1회 자동 실행" 진행 (2026-09-24) |
| 돈 | **가짜 돈만.** 테스트넷 주소가 아니면 주문 어댑터가 생성되지 않는다. 실계좌 주문 경로 없음 |
| 기간 | **2026-09-25 ~ 2026-10-08** (14일) |
| 실행 | `scripts/testnet_drill.py` 매일 09:14 KST → **2026-09-24 부터 21:08 KST**(점검 21:20 앞, 사용자 결정). 기준(§2)은 그대로 |
| 기록 | `reports/testnet/drill-<시각>.json`, DB `data/testnet.db` (운영 DB 와 분리) |
| 보고 | 매일 점검 보고(텔레그램)에 한 줄: 최근 결과 · 14일 중 실행/통과 일수. 실패·밀림은 경고 |

## 1. 매일 훈련이 확인하는 것 (12단계, 20개 판정)

같은 의도 3회 → 주문 1건 · 체결가 기록 · 즉시 발동 스톱 거절(-2021) · Algo 보호 스톱 등록 · 재시작 정합성 복구 ·
닫을 포지션 없는 스톱 거절(-4509) · 같은 방향 두 번째 스톱 거절(-4130) · reduce-only 위반 거절(-2022) ·
최소 명목 미달 거절(-4164) · 거절 뒤 신규 진입 가능 · 전송 직후 강제 종료 → 조회로 FILLED 복구 ·
스톱 취소 → 감시자 재보호 · 청산 뒤 평탄 · 원인 불명 포지션 정리 · 마무리 평탄·RUNNING.

관찰(판정 아님): 실제 수수료율, 청산 뒤 closePosition 스톱 처리, 체결된 client_order_id 재사용을 거래소가 받는지.

매일 반복하는 이유: 한 번의 성공은 그날의 거래소 상태만 보여 준다. 테스트넷 점검·지연·API 변경은
날짜를 두고 나타난다 — 문서와 실제가 두 번 달랐던 것처럼(`openAlgoOrders`, 테스트넷 주소).

## 2. 통과 기준 (시작 전 고정)

| # | 기준 |
|---|---|
| P1 | 14일 **모두** 실행 기록이 있다 (밀린 날 0) |
| P2 | 실패한 날은 전부 **원인이 설명된다** — 테스트넷 점검·네트워크 같은 외부 원인이면 기록하고 넘어간다 |
| P3 | **코드 결함이 원인인 실패 0** — 나오면 고치고, 고친 날부터 14일을 **다시 센다** |
| P4 | 매일 마무리 상태가 평탄·주문 0·스톱 0 (테스트넷에 흔적을 남기지 않는다) |

실패 원인은 그날 `reports/testnet/` 기록과 `data/testnet.db` 의 주문·`risk_events` 로 판단한다.

## 3. 매일 훈련으로 채우지 못하는 Phase 7 항목 (별도로 한다)

| 항목 | 이유 | 계획 |
|---|---|---|
| **실제 스톱 발동** | 가격이 스톱에 닿아야 한다 — 매일 훈련에서 기다릴 수 없다 | **확인 (2026-09-24)**: 선택형 훈련 `scripts/testnet_stop_drill.py`(ETHUSDT, mark 15bp 아래, 최대 60분, 09:05~09:35 KST 시작 거부). 9/9 통과 — §6 |
| **부분 체결** | 소량 시장가는 전량 체결된다. 테스트넷에서 재현이 어렵다 | 가짜 거래소 장애 주입 테스트로 대신하고, 그 한계를 게이트 판정에 적는다 |
| 체결 실시간 수신 | WS 사용자 스트림 | **구현·확인 (2026-09-24)**: `exchange/user_stream.py`(해석)·`binance_usdm_stream.py`(연결 수명)·`execution/stream_sync.py`(이벤트 → 조회 확정), 선택형 훈련 `scripts/testnet_stream_drill.py` — §6 |

## 4. 한계

- 테스트넷은 실계좌와 체결·수수료·유동성이 다르다. 여기서 보는 것은 **주문 경로의 정확성**이지 비용이 아니다.
- 테스트넷 수수료 0.040% 는 실계좌 요율(계좌 등급·할인에 따라 다름)을 뜻하지 않는다.
- Phase 7 통과는 실거래 승인이 아니다. Phase 8 은 사용자 명시 승인·자본/손실 허용액 확정이 먼저다.

## 5. 운영

작업 스케줄러 등록 (사용자가 PowerShell 에서 1회 실행 — 시스템 설정이라 에이전트가 직접 등록하지 않는다):

```powershell
Register-ScheduledTask -TaskName "AutoTrading_Testnet_Drill" -Action (New-ScheduledTaskAction -Execute "C:\Users\seolh\Documents\GitHub\Auto_Trading\.venv\Scripts\python.exe" -Argument "C:\Users\seolh\Documents\GitHub\Auto_Trading\scripts\testnet_drill.py") -Trigger (New-ScheduledTaskTrigger -Daily -At 09:14)
```

## 6. 기록

| 날짜 | 내용 |
|---|---|
| 2026-09-24 | 개발 중 훈련 3회: 16/17 → 18/19 → **20/20**. 실패는 모두 거래소 실측 규칙(-4130·-4509·평균가 0·취소 직후 조회 지연)을 코드에 반영하며 해소. 이 3회는 기간 통계에 넣지 않는다 |
| 2026-09-24 | **스톱 발동 훈련 9/9 (TRIGGERED)**: ETH 0.010 롱 @ 2,683.90 → 스톱 2,679.49(mark 15bp 아래, Algo·MARK_PRICE) → 235초 뒤 발동, 체결 2,679.33(스톱 대비 **0.60bp 불리**). 스톱 기록 FILLED·수량 일치·평탄·남은 스톱 0, 재시작 복구 RUNNING(스톱 체결을 로컬 기록이 반영 — Algo `actualOrderId` 경로 확인). 기록 `reports/testnet/stop-20260924T031236Z.json`. 테스트넷 유동성 기준이며 실계좌 급변·갭 슬리피지를 뜻하지 않는다 |
| 2026-09-24 | **WS 사용자 스트림 실측 조사** (일회성, ETH): 테스트넷 WS 주소 `wss://fstream.binancefuture.com/private/ws?listenKey=…&events=…`(문서에 없음), listenKey 는 `demo-fapi` POST. 받은 이벤트 12개로 확인: ① **발동한 스톱의 실제 시장가 주문이 스톱과 같은 client ID**(`st=ALGO_CONDITION`, `si=algoId`) ② **순서 역전** — 체결(ORDER_TRADE_UPDATE FILLED)이 ALGO_UPDATE TRIGGERING 보다 먼저 ③ ALGO 상태 NEW → TRIGGERING → TRIGGERED(`ai`) → FINISHED(`ap`·`aq`) ④ 이벤트 시각(E)과 체결 시각(T) 차이 ≈ 10ms. 그래서 이벤트를 상태로 쓰지 않고 "그 주문을 다시 조회" 신호로만 쓴다 |
| 2026-09-24 | **스트림 훈련 1차 8/9 (TRIGGERED)**: 연결·진입 체결 이벤트(전송 뒤 87ms)·스톱 NEW·모르는 ID 스톱 UNTRACKED·강제 끊기 뒤 재연결 6.3초·**가까운 스톱 발동을 이벤트로 확정**(37분 대기 중 listenKey 연장 1회, 체결 FILLED 이벤트 → LAGGING → TRIGGERED 이벤트에서 FILLED)·마무리 RUNNING 통과. 실패 1: 관리자 밖 취소 — 취소 직후 조회가 아직 NEW 라 LAGGING 이 됐고 재조회로 풀렸는데 **훈련 판정이 재조회 결과를 세지 않았다**(훈련 스크립트 결함, 코드 결함 아님) → 수정. 기록 `stream-20260924T041857Z.json` |
| 2026-09-24 | **스트림 훈련 2차 8/8 · NOT_TRIGGERED**: 수정한 취소 반영 통과(LAGGING → 재조회로 CANCELED). 스톱(10bp)은 가격이 멀어져 60분 안에 발동하지 않아 정리 후 판정 불가. 이벤트 수신 지연(서버 시계 보정) 최대 약 0.3초. 기록 `stream-20260924T051952Z.json` |
| 2026-09-24 | **스트림 훈련 3차 9/9 (TRIGGERED)** — 최종 코드로 한 번에 전부 통과: 진입 체결 이벤트 87ms · 스톱 NEW · 관리자 밖 취소 → CANCELED · UNTRACKED · 재연결 6.3초 · 스톱(5bp) 815초 뒤 발동, 체결 2,688.24(스톱 2,688.33, 0.3bp 불리)를 **조회 루프 없이 이벤트로 확정** · RUNNING. LAGGING 3건 모두 재조회로 해소, 이벤트 수신 지연 최대 73ms(서버 시계 보정). 기록 `stream-20260924T053405Z.json`. **남은 것**: 운영 프로세스(Paper·실거래 실행기)에 스트림을 붙이는 일 — 지금은 훈련에서만 쓴다 |
