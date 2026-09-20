# Phase 0 — 설계 문서

상태: **완료** (D3/D5 는 Phase 4·8 시점 결정)
작성일: 2026-09-20 / 최종 갱신: 2026-09-20
대상 시스템: BTC/USDT Perpetual Futures 자동매매

이 문서군은 코드 작성 이전 단계(Phase 0)의 산출물이다.
Phase 0이 확정되기 전까지 `src/` 아래 구현 코드를 작성하지 않는다.

## 읽는 순서

| 문서 | 내용 | 원 요청 항목 |
|---|---|---|
| [01-requirements.md](01-requirements.md) | 요구사항 재정리, 비목적, 성공 기준 | A |
| [02-architecture.md](02-architecture.md) | 시스템 아키텍처, 데이터 흐름, 모듈 구조 | B, C, D |
| [03-data-model.md](03-data-model.md) | DB 스키마 초안, 재현성 설계 | E |
| [04-state-machine.md](04-state-machine.md) | 주문/포지션 상태머신, 정합성 복구 | F |
| [05-risk-engine.md](05-risk-engine.md) | Risk Engine 설계, 포지션 사이징 | G |
| [06-scoring-engine.md](06-scoring-engine.md) | Scoring Engine 설계, 비용 게이트 | H |
| [07-backtest.md](07-backtest.md) | 백테스트 엔진, 검증 프로토콜 | I |
| [08-external-apis.md](08-external-apis.md) | 외부 API 후보와 제약 | J |
| [09-failure-modes.md](09-failure-modes.md) | 위험한 실패 지점 순위 | K |
| [10-roadmap.md](10-roadmap.md) | 단계별 로드맵, MVP 제외 기능 | L, M |
| [11-testing.md](11-testing.md) | 테스트 전략 | N |
| **[12-critique.md](12-critique.md)** | **명세에 대한 비판적 검토 + 미결 결정사항** | 추가 |
| **[13-decisions.md](13-decisions.md)** | **확정 결정 기록 — 구현의 기준** | 추가 |
| [14-ui-and-packaging.md](14-ui-and-packaging.md) | 대시보드(읽기 전용 웹 UI) 및 exe 패키징 | 추가 |

> 처음 읽는다면 **12 → 13** 순서를 권한다. 나머지 문서의 설계 선택 대부분이 거기서 나온다.
> 값이 충돌할 경우 **13번 문서가 우선**한다.

## Phase 0 종료 조건

- [x] MVP 범위 합의 — 12-critique 권고 5가지 전부 수용 (13-decisions **D1**)
- [x] 목표 보유기간 확정 — 2시간 ~ 24시간 (13-decisions **D2**)
- [x] 리스크 한도 확정 — 거래당 최대 손실 총액 1% / DD -10% (13-decisions **D4** → **D9**)
- [x] DB 스키마 1차 확정 (03)
- [x] 멀티 에이전트 개발 체계 + Phase 보고 규약 (13-decisions **D6 / D7**)
- [x] 매매 개념 카탈로그 작성 ([../STRATEGY-CATALOG.md](../STRATEGY-CATALOG.md))
- [x] 포지션 사이징 재설계 (13-decisions **D9**)
- [x] 운영 UI / exe 배포 설계 (13-decisions **D10**, [14](14-ui-and-packaging.md))
- [x] 검증할 시장 가설 1문장 확정 (13-decisions **D11**, 가설 ID `H1`)
- [ ] (Phase 8 이전까지) 운용 자본 규모 (**D3**), 중단 기준 (**D5**)

> **Phase 0 완료.** D3(자본 규모)/D5(중단 기준)는 Phase 1~4 진행을 막지 않는다.

## 이 문서 밖의 관련 파일

| 파일 | 역할 |
|---|---|
| [`../../PROGRESS.md`](../../PROGRESS.md) | 작업 현황 — **에이전트는 작업 전 필독, 작업 후 갱신** |
| [`../../AGENTS.md`](../../AGENTS.md) | AI 에이전트 작업 규약 (Claude / Codex 공통) |
| [`../STRATEGY-CATALOG.md`](../STRATEGY-CATALOG.md) | 매매 개념 — 사람이 읽고 고치는 문서 |
