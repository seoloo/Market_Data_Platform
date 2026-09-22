# Archive — 역사적 기록, 현행 사양 아님

보관일: 2026-09-23. 승인된 문서 병합 직전 작업본 34개를 보존했다.
현행 기준은 [CURRENT_SPEC](../current/CURRENT_SPEC.md)에서 시작한다.
과거 문서의 “확정”, “다음 작업”, 완료율은 당시 기록이며 현재 지시가 아니다.
D22가 섞인 원문도 증거 보존을 위해 유지했다. 현재 전략·점수·연구·로드맵에는 적용하지 않는다.

## 보존 방법과 범위

원문 본문은 보존하고 상단 상태 안내 및 Markdown 상대 링크 위치만 변경했다.
루트 README는 ROOT-README로 보관하여 옛 phase0/README와 구분했다.
D22 직전 Git 비교 기준은 96fc771. 여기 보관된 것은 그 커밋의 사본이 아니라 **중단된 D22 미커밋 변경까지 포함한 작업본**이다.
AGENTS/PROGRESS/STRATEGY-CATALOG, 01/02/03/04/06/08/10/13/옛 색인의 분산된 D22 내용과 28 초안을 모두 보존했다.
과거 코드·보고서에 있는 경로는 원래 위치의 안내문을 거쳐 원문을 찾을 수 있다.

| 분류 | 내용 |
|---|---|
| design | 01~14, 옛 색인/카탈로그, 루트 규약·진행·README/CLAUDE |
| research | 15~27 연구 원문. 사전등록 기준과 결과를 변경하지 않음 |
| drafts | D22 초안, STATUS: INCOMPLETE / EXCLUDED |

## 원래 경로 → 보관 위치

| 원래 경로 | 보관 |
|---|---|
| `docs/phase0/01-requirements.md` | [원문](design/01-requirements.md) |
| `docs/phase0/02-architecture.md` | [원문](design/02-architecture.md) |
| `docs/phase0/03-data-model.md` | [원문](design/03-data-model.md) |
| `docs/phase0/04-state-machine.md` | [원문](design/04-state-machine.md) |
| `docs/phase0/05-risk-engine.md` | [원문](design/05-risk-engine.md) |
| `docs/phase0/06-scoring-engine.md` | [원문](design/06-scoring-engine.md) |
| `docs/phase0/07-backtest.md` | [원문](design/07-backtest.md) |
| `docs/phase0/08-external-apis.md` | [원문](design/08-external-apis.md) |
| `docs/phase0/09-failure-modes.md` | [원문](design/09-failure-modes.md) |
| `docs/phase0/10-roadmap.md` | [원문](design/10-roadmap.md) |
| `docs/phase0/11-testing.md` | [원문](design/11-testing.md) |
| `docs/phase0/12-critique.md` | [원문](design/12-critique.md) |
| `docs/phase0/13-decisions.md` | [원문](design/13-decisions.md) |
| `docs/phase0/14-ui-and-packaging.md` | [원문](design/14-ui-and-packaging.md) |
| `docs/phase0/15-h1-postmortem.md` | [원문](research/15-h1-postmortem.md) |
| `docs/phase0/16-h2-proposal.md` | [원문](research/16-h2-proposal.md) |
| `docs/phase0/17-h2-screen-result.md` | [원문](research/17-h2-screen-result.md) |
| `docs/phase0/18-h2-validation-result.md` | [원문](research/18-h2-validation-result.md) |
| `docs/phase0/19-maker-cost-measurement.md` | [원문](research/19-maker-cost-measurement.md) |
| `docs/phase0/20-signal-time-fills.md` | [원문](research/20-signal-time-fills.md) |
| `docs/phase0/21-closure.md` | [원문](research/21-closure.md) |
| `docs/phase0/22-multi-symbol-pivot.md` | [원문](research/22-multi-symbol-pivot.md) |
| `docs/phase0/23-h3-preregistration.md` | [원문](research/23-h3-preregistration.md) |
| `docs/phase0/24-h3-screen-result.md` | [원문](research/24-h3-screen-result.md) |
| `docs/phase0/25-research-direction-review.md` | [원문](research/25-research-direction-review.md) |
| `docs/phase0/26-r1-preregistration.md` | [원문](research/26-r1-preregistration.md) |
| `docs/phase0/27-r1-screen-result.md` | [원문](research/27-r1-screen-result.md) |
| `docs/phase0/28-btc-multitimeframe-strategy.md` | [원문](drafts/D22-btc-multitimeframe-strategy.md) |
| `docs/phase0/README.md` | [원문](design/README.md) |
| `AGENTS.md` | [원문](design/AGENTS.md) |
| `PROGRESS.md` | [원문](design/PROGRESS.md) |
| `README.md` | [원문](design/ROOT-README.md) |
| `CLAUDE.md` | [원문](design/CLAUDE.md) |
| `docs/STRATEGY-CATALOG.md` | [원문](design/STRATEGY-CATALOG.md) |

## Current 병합 매핑

01/02/03 및 유효 결정 → CURRENT_SPEC.
04/05/공통06 → TRADING_CORE.
07/09/11 및 Phase 게이트10 → BACKTEST_AND_TESTING.
08/14 및 운영 내용 → OPERATIONS.
15~27 및 역사적 결정/교훈 → RESEARCH_HISTORY 요약.
12/13의 과거 논의, D22, 구체적인 옛 전략 파라미터는 현행 명세로 복제하지 않았다.
반복된 위험 공식·검증 게이트는 Current의 담당 문서 한 곳으로 통합했다.
상충하는 손절 정책·지연 검사·진행률·보유기간/embargo는 CURRENT_SPEC의 C1~C6에 남겨 임의로 해소하지 않았다.

## 판정 주의

19의 maker 비용 초기 추정은 20의 신호 시점 역선택 분석으로 대체됨.
21 종료 결정은 철회됨. 24의 과거 H1/H2 일반화 문장은 각 원연구 결과로 범위를 제한해 읽을 것.
D22 초안은 연구 검정에서 기각된 전략이 아니라 미완성·제외된 아이디어다.
기본 개발에서는 이 폴더 전체를 읽지 말고 필요한 근거만 조회한다.
