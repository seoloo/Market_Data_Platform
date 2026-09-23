# 현재 시스템 명세

기준일: 2026-09-24 · 상태: A1 종료(DATA_INSUFFICIENT) / WA TRAIN `REJECT` / K VALIDATION `REJECT` / M TRAIN `REJECT` / F 전향 관찰 / **T 추세 추종 1회 판정 PASS · 전향 관찰 중**(2026-09-24). 승인된 전략 없음.
목표는 BTC 선물 자동매매로 비용 이후의 재현 가능한 수익을 검증하고, 위험을 통제하며 모든 판단을 연구 데이터로 남기는 것이다. 수익성은 아직 입증되지 않았다.

## 읽기 순서와 권위

AGENTS → PROGRESS → 이 문서 → 작업에 해당하는 아래 문서. Archive 전체를 기본 컨텍스트로 읽지 않는다.

| 단일 기준 문서 | 담당 |
|---|---|
| [TRADING_CORE](TRADING_CORE.md) | 위험 한도·사이징·주문/포지션·실행 안전성 |
| [BACKTEST_AND_TESTING](BACKTEST_AND_TESTING.md) | 검증 기준·연구 절차·Phase 게이트·테스트 |
| [OPERATIONS](OPERATIONS.md) | 데이터·외부 서비스·운영·보안·보존 정책 |
| [RESEARCH_HISTORY](RESEARCH_HISTORY.md) | 연구 판정·근거·재시도 금지 사항 |
| [진행 중 연구](../research/) | 아직 판정 전인 사전등록·후보 비교. 판정이 끝나면 RESEARCH_HISTORY 에 요약하고 원문은 Archive 로 옮긴다 |

최신 사용자 결정이 우선한다. Current는 분야별 단일 기준이며, Archive와 옛 경로의 안내문은 현행 요구사항을 덮어쓰지 않는다.
코드/config는 실제 실행 상태의 증거이지 요구사항 충돌을 자동 해소하는 근거가 아니다.
아래 미해결 차이는 승인·구현·검증 전까지 해소된 것으로 간주하지 않는다.

## 범위 및 현재 결정

| Decision | Status / Reason | Date/Version | Related Module |
|---|---|---|---|
| BTCUSDT, Binance USD-M, One-way, Isolated | 유지: 실제 운용 대상. 타 심볼 데이터는 과거 연구 자산이며 다중 종목 실거래 승인 아님 | 기존 기반 유지, 2026-09-23 정리 | exchange/config |
| 기존 데이터·Risk·Backtest 기반 보존 | 유지: 재현성·실행 안전성 재사용 | D22 직전 비교 기준 96fc771 | core/storage/backtest/risk |
| WA 선택 | A1은 TRAIN `DATA_INSUFFICIENT`로 종료. WA는 TRAIN 1회 실행 결과 `REJECT`(08 §10). 코드는 재현용으로 유지 | 2026-09-23 선택, 09-24 기각 | strategy/research |
| K 펀딩 캐리 연구 | 현물 BTCUSDT 롱 + USD-M 숏 델타 중립. **연구(백테스트) 범위만 승인.** 결과 VALIDATION `REJECT`(연 1.97% < 5%, 10 §10). 현물 캔들은 `exchange='binance_spot'`으로 분리 적재, 실거래 경로는 읽지 않음. 현물 주문·지갑 이체·두 다리 체결은 별도 승인 전 구현 금지 | 2026-09-24 사용자 선택 (09·10) | backtest/research |
| M 다중 종목 캐리 연구 | 월별 유동성 상위 20(현물 페어 필수) 중 funding 상위 최대 4종목, 선물 1x. **연구 범위만 승인**, 알트 실거래는 별도 승인 (13). 결과 TRAIN `REJECT`(연 3.65% < 5%) | 2026-09-24 사용자 선택 M+F | backtest/research |
| F K 전향 관찰 | K/v1 동결, 2026-09 ~ 2027-08 월별 보고, 12개월 뒤 1회 판정 (12) | 2026-09-24 | research |
| T 추세 추종 연구 | BTC·ETH 롱(현물)·숏(선물 1x) 시계열 추세, 2018~2026-08 1회 판정 + 전향 관찰. **연구 범위만 승인** (14·15). 결과 PASS(샤프 0.95, MDD 25.4%, 뒤 절반 0.46) — 실거래 미승인, DD 10% 규칙과 정합 필요 | 2026-09-24 사용자 선택 | backtest/research |
| 위험 한도 정정 | 사용자의 "10% 손실"은 **포지션당**(증거금 10%·손절 −10% → 계좌 1%). 계좌 누적 DD 정지 10% → **30%**(사용자 선택), 일 손실 2%·연패 4회는 사용자 확인으로 유지, 현물은 변동성 목표(15%/종목, 비중 ≤ 50%) | 2026-09-24 사용자 정정 | config/risk |
| 레버리지 범위 | **1x ~ 10x** (사용자 지정). 손절 역산 결과값, 스키마가 10 초과 거부. 실거래는 3x 시작·live 상한 5x, 10x까지 상향은 실측 뒤 승인 | 2026-09-24 사용자 확인·스키마 20→10 | config/risk |
| 거래 횟수 | 하루 거래·같은 방향 연속 진입 횟수 제한 없음. 설정 기본값 `null` | 2026-09-24 사용자 명시 정정 | config/risk |
| WA 연구 G6 | 선택 (b): 거래당 우위 측정에서는 G6 비적용. 전체 규칙 계좌 재생·실거래에는 적용. 연구 결과를 운용 가능 판정으로 취급하지 않음 | 2026-09-24 사용자 선택 | backtest/research |
| D22 | INCOMPLETE / EXCLUDED: 현재 전략·점수·로드맵·연구 방향에서 제외 | 2026-09-23 정리 | [보관 초안](../archive/drafts/D22-btc-multitimeframe-strategy.md) |
| Elliott/JEV/News AI | Optional Shadow만 허용. 초기 실제 주문·차단·사이징에 영향 없음 | 새 사용자 지침 | ai |
| 자본·새 전략 수치 | 미정: 결과를 본 뒤 임의 확정 금지 | Phase 0 대기 | config/research |

기각된 H1 구현은 회귀·재현용으로 남아 있으나 현행 수익 전략으로 복귀하지 않는다.
D22의 다중 시간대 대량 점수, 패턴 결합, 15분봉 재평가, 뉴스/JEV 점수화는 현재 요구사항이 아니다.
기존 D12 정책 등 D22 이전 기능의 존속 범위는 TRADING_CORE에 명시한다.

## 아키텍처와 데이터 흐름

시장 원자료 → 저장·무결성 검사 → 마감봉 MarketSnapshot → 순수 decide → 전략 의도·Risk 판정 → 실행 어댑터 → 거래소 → 체결/포지션 대조 → 감사 기록.
백테스트는 동일 decide에 과거 스냅샷을 넣고 실행 어댑터 대신 체결 시뮬레이터를 사용한다.
실거래 실행 경로는 아직 구현/검증 완료가 아니다.

| 모듈 | 책임 / 관찰 상태 |
|---|---|
| core/config | 불변 타입·금액·시각·해시, 설정 검증/버전: 구현 |
| storage/marketdata | SQLite 마이그레이션·조회, 백필·funding·causal snapshot: 구현 |
| features/strategy/risk | H1/A1 재현 기반 유지. WA 순수 상태·방향별 손절·6h 청산, 엔진 1h 평가·BACKTEST 전용 우위 측정 모드 구현(2026-09-24). WA는 기각, 재현용 유지 |
| backtest | feed/fills/portfolio/engine, 보고서·기준선·연구 선별: 구현 |
| exchange/execution | 주문·보호주문·reconciliation: 설계, 실거래 구현/검증 대기 |
| ai/notify/ui | 분석 보조·알림·읽기 전용 대시보드: 계획, 완료로 표시하지 않음 |
| ops | Clock 등 일부 기반만 구현, 운영 워치독·복구 검증 대기 |

Pure Core에는 I/O·실시간 시계·시드 없는 난수와 exchange/storage/marketdata/notify/ai import를 금지한다.
판단 시각은 snapshot.as_of_ms. 금액/수량은 Decimal, DB에는 문자열, 시각은 UTC epoch ms다.

## 데이터 계약과 연구 기록

실제 스키마는 [마이그레이션](../../src/auto_trading/storage/sql/)에서 확인한다. 문서 DDL 복제본을 새 스키마로 취급하지 않는다.
시장 원자료, 결정·주문·체결·거래, 계좌·위험 이벤트, config/code 버전은 재현과 복구의 연결고리다.

| 기록 | 현재 / 다음 최소 요구 |
|---|---|
| decisions | components_json, sizing_json, scores, veto 및 config/code/snapshot 식별자 기반 존재. 주문 여부와 무관한 매 평가 기록 요구 |
| trades | 비용·funding·slippage·보유시간·MAE/MFE·청산 사유 기반 존재 |
| 진입 근거 | trade_id/decision 연결, side·entry/stop/TP·notional·margin·leverage·equity·risk·예상손절손실·비용·feature·regime·승인/거부 이유의 저장 완전성 감사 필요 |
| 전략 버전 | strategy_id/version 및 변경 이유/가설 연결 추가 설계 필요. 기존 config/code 버전을 대체하지 않음 |
| Shadow | 입력 시각/출처/모델/프롬프트/응답·신뢰도, 결정과 분리된 저장 계약 필요 |
| 사후 결과 | forward returns와 post-trade labels 전용 구조 미구현. 실시간 feature와 물리적/논리적 분리 필요 |

최소 확장안은 기존 decisions/trades를 우선 재사용하고, 부족한 strategy version·shadow·사후 분석 연결만 제안한다.
테이블을 미리 모두 만들지 않으며 이번 작업에는 DB 마이그레이션이 없다.
진입 후 5m/15m/30m/1h/2h/4h/12h/24h 수익률은 가능한 범위에서 관찰 종료 시각과 함께 사후 저장한다.
false_breakout, stop_then_reversal, tp_then_continuation 등 미래 의존 label은 별도 사후 분석이며 당시 exit_reason을 덮어쓰지 않는다.

## 현재 단계와 미해결 등록부

Phase 1~3 기반의 과거 완료 기록은 보존하되 새 전략의 게이트 통과로 전이하지 않는다.
Phase 4에서 승인된 전략은 없다. Phase 5 이후와 실거래는 미착수다. 세부 게이트는 BACKTEST_AND_TESTING만 관리한다.

| ID | 충돌 / 상태 | 후속 처리 |
|---|---|---|
| C1 | 새 지침은 전략 손절 유지, 구현은 min_stop_distance=1% + WIDEN | TRADING_CORE 기준으로 별도 코드 변경/설정 정합성/경계 테스트 검토. 이번에 수정하지 않음 |
| C2 | 옛 검증 문서의 1일/1봉 지연, 유지/소멸 기준 충돌 | 사전등록 전에 사용자와 단위·판정 확정. 해당 게이트 미해결 |
| C3 | 과거 진행률의 기반 게이트 완료와 2019 데이터·계좌 스냅샷 체크리스트 불일치 | 데이터 시작점/예외 및 미구현 항목 감사. 일괄 통과 주장 금지 |
| C4 | 무제한 보유와 “최대 보유기간만큼 embargo” 양립 불명확 | 전략별 label/포지션 구간 purge 및 경계 보유 처리 사전등록 |
| C5 | 운용 자본 및 새 전략 미선정 | 후보 비교 → 사용자 선택 → 수치 사전등록 |
| C6 | 실거래 관련 API 명세와 안전장치는 설계만 존재 | Phase 5에서 당시 공식 API 확인 후 구현·장애검증 |
| C7 | D22 제외가 사용자 의도인지 미확인 (2026-09-22 `요구사항 확정` → 다음 날 `EXCLUDED`) | 사용자 확인. 원문은 [초안 보관](../archive/drafts/D22-btc-multitimeframe-strategy.md) |
| C8 | Phase 4 OOS `실제 거래 n≥100` 이 TRAIN 보다 약해, 23~35bps 효과는 TRAIN 통과 후 OOS 확인 불가 | 사전등록 전 사용자 결정. 선택지는 [후보 비교 §6](../research/01-candidate-comparison.md) |

## 문서 정리 이력

2026-09-23 사용자 승인으로 Current 5개로 병합했다. 기존 34개 문서는 [Archive 색인](../archive/README.md)에 보존했다.
기존 경로는 호환 안내문이며 별도 사양이 아니다. 코드/config/시장 DB 및 연구 결과 파일은 변경하지 않았다.
