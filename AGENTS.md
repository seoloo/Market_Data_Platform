# AGENTS.md — AI 에이전트 공통 작업 규약

## 0. 작업 시작 순서

1. [PROGRESS.md](PROGRESS.md): 현재 위치와 다음 작업.
2. [CURRENT_SPEC.md](docs/current/CURRENT_SPEC.md): 현행 결정·구현 상태·미해결 등록부.
3. [BACKTEST_AND_TESTING.md](docs/current/BACKTEST_AND_TESTING.md): 해당 Phase 게이트.
4. 작업 관련 Current 문서만 추가로 읽는다. Archive 전체를 자동으로 읽지 않는다.

현재 기준은 docs/current/의 5개 문서다. docs/phase0/와 STRATEGY-CATALOG는 호환 안내문이다.
Archive는 역사적 증거이며 그 안의 “확정/다음 작업” 문구를 실행 지시로 삼지 않는다.
D22는 INCOMPLETE / EXCLUDED. 옛 H1 코드가 남아 있다는 이유로 현행 전략이라고 부르지 않는다.
새 전략 후보는 최대 3개를 비교하고 사용자가 고른 하나만 구현·검증한다.

## 1. Phase 보고와 인계

작업 응답 시작: “📍 현재: Phase N — 항목”, “🎯 이번 작업: 구체적인 범위”.
종료: “✅ 완료”, “📍 현재 Phase”, “➡️ 다음 작업: Phase와 항목”, “⚠️ 미해결”, “📦 커밋 제안”.
Phase 번호/게이트는 BACKTEST_AND_TESTING을 따른다. 임의 진행률이나 미검증 통과를 만들지 않는다.
게이트 미통과 상태에서 다음 Phase로 넘어가지 않는다. 예외 이동은 사용자 승인 필요.
매 작업 종료 시 PROGRESS를 갱신하되, 사용자가 읽기 전용을 요구하면 그 지시가 우선한다.
연구 판정·전략 상태·운영 단계·일정이 바뀌면 [BRIEFING_FOR_LLM](docs/BRIEFING_FOR_LLM.md)(다른 AI 에 붙여 넣는 요약)도 같은 커밋에서 고친다.

## 2. 변경할 수 없는 핵심 원칙

규칙의 상세 기준과 한도는 [TRADING_CORE](docs/current/TRADING_CORE.md), 데이터 경계는 CURRENT_SPEC, 검증 기준은 BACKTEST_AND_TESTING이 단일 원천이다.

- Pure Core: core/features/strategy/risk에서 I/O·실시간 시계·시드 없는 난수 금지. exchange/storage/marketdata/notify/ai import 금지. snapshot.as_of_ms 사용.
- 미래 데이터 금지: causal 정규화, 마감봉, confirmed_at_ms 기준 스윙만 사용. 전체 표본 fit 금지.
- 거래소가 진실의 원천: 로컬 차이는 위험 이벤트로 기록하고 reconcile.
- 거래소 측 보호주문 필수: 로컬 stop만으로 운용 금지. 보호 실패 시 TRADING_CORE의 청산 규칙.
- 멱등 주문: 동일 결정의 주문 ID는 재시작 후에도 동일, UNKNOWN에서 추정 재주문 금지.
- **시장가 금지(2026-10-05 사용자 지시):** 모든 매매는 가격 한도가 있는 지정가만. 시장가 주문을 보내거나 시장가 체결을 가정하는 실행·사전 등록을 만들지 않는다. **유일한 예외: 거래소에 걸어 둔 비상 스톱(STOP_MARKET) 발동**(사용자 2026-10-05). 충돌 목록은 TRADING_CORE §주문 유형.
- Fail-closed: 결측/stale/불명은 신규 진입 거부. DEGRADED/HALTED에도 청산·위험 축소 허용.
- 모든 평가 기록: config/code 버전·snapshot_digest·구성 신호·veto를 주문 유무와 무관하게 남긴다.

원칙·위험 한도를 임의로 완화하지 않는다. 변경이 필요하면 근거와 대안을 보고하고 승인받는다.

## 3. 코드 규약

Python 3.11+, 모든 함수 type hints. core/features/strategy/risk/execution은 mypy strict.
금액·수량 Decimal, DB 문자열 저장. UTC epoch milliseconds(int), 시각 변수 _ms, 비율/단위 _pct/_bps/_min 명시.
수량은 floor_to_step으로 내림. 매직넘버 대신 config.
core/errors.py 도메인 예외 사용, bare except 금지. 구조화 JSONL 로깅, print 금지.
ruff format / ruff check 준수. 기존 사용자 변경 보존. 범위 밖 코드 수정 금지.
외부 backtest 프레임워크로 별도 전략 경로를 만들지 않는다.

## 4. 테스트

- feature: 고정 입력 기대값 + 미래 무관성.
- Risk gate: 통과/경계/거부.
- 상태 전이: hypothesis property-based.
- Backtest: 골든 기대값과 변경 이유 기록.
- 거래소: FakeExchange 통합 + 장애 주입.

프로젝트 가상환경에서 pytest 전체, pytest -m leakage -v, strict mypy, ruff check/format --check, lint-imports를 수행한다.
테스트를 “나중에”로 미루지 않는다. 문서 전용 변경은 링크/보존/범위 검사와 코드 테스트 여부를 명확히 구분한다.

## 5. 금지 사항과 충돌 처리

실거래 무승인 활성화, 위험 한도 완화, 성과 기반 파라미터 낚시, OOS 재튜닝, 기각 전략 이름 바꾸기, 무검증 수익 주장 금지.
Elliott/JEV/News AI는 초기 Shadow 전용이며 주문·veto·사이징에 영향 금지.
아카이브와 현행 문서 충돌 시 Current를 따른다. Current 내부 충돌은 미해결 등록부에 남기고 사용자에게 확인한다.
문서와 코드가 다르면 구현 차이를 보고한다. 진단/문서 정리 요청을 코드 변경 권한으로 확대하지 않는다.
C1 손절 WIDEN 같은 명시적 미해결 항목은 설계·코드·테스트를 별도 승인 범위에서 함께 처리한다.

## 6. 커밋과 푸시

2026-09-25 사용자 지속 지시: 이후 작업은 범위·검증이 완료되면 별도 재요청 없이 **이번 작업에서 변경한 파일만** 커밋하고 현재 작업 브랜치를 일반 푸시한다. 읽기 전용·커밋/푸시 금지 등 후속 지시가 있으면 그 지시가 우선한다.
기존 사용자 변경이나 범위 밖 파일은 포함하지 않는다. 원격 선행·충돌·푸시 실패 시 강제 푸시나 임의 병합을 하지 말고 상태를 보고한다.
커밋은 GitHub Desktop용 한글 제목(50자 이내)을 사용하고 실제 포함 파일을 보고한다.
제목은 P0~P9 또는 docs:/chore:. 기술 식별자는 원문 유지. 본문은 변경 이유를 한글로.
여러 논리 단위면 제목/파일을 나누고 한 번에 할 대안도 제시한다. 파일이 많으면 개수로 요약한다.
골든 결과가 바뀌면 이유 필수. 테스트가 깨진 상태의 커밋 제안 금지.
.env/키/토큰이 포함될 수 있는 커밋은 금지. .env/.venv가 변경 목록에 보이면 경고한다.

## 7. 에이전트·LLM 이식성

CLAUDE.md 및 다른 도구 진입 파일은 이 파일을 가리키는 포인터만 둔다.
도구별 규칙 복제 금지. 코드/사양 인계는 PROGRESS와 Current로 수행한다.
시스템 LLM은 공급자 비종속 인터페이스, 모델/프롬프트/입력 해시/응답 저장을 요구한다.
temperature=0만으로 결정론을 보장하지 않는다. 저장 응답 재생/캐시와 Shadow 경계는 [OPERATIONS](docs/current/OPERATIONS.md)를 따른다.

## 8. 주요 위치

- [CURRENT_SPEC](docs/current/CURRENT_SPEC.md): 시스템 기준과 문서 라우팅.
- [TRADING_CORE](docs/current/TRADING_CORE.md): 리스크·실행 안전.
- [BACKTEST_AND_TESTING](docs/current/BACKTEST_AND_TESTING.md): 연구와 Phase 게이트.
- [OPERATIONS](docs/current/OPERATIONS.md): 운영·보안·보존 정책.
- [RESEARCH_HISTORY](docs/current/RESEARCH_HISTORY.md): 연구 판정 요약.
- [BRIEFING_FOR_LLM](docs/BRIEFING_FOR_LLM.md): 외부 AI 대화용 파생 요약(기준 문서 아님).
- config/: 실행 설정, src/auto_trading/: 구현, tests/: 검증.
- [Archive](docs/archive/README.md): 필요할 때만 과거 근거 조회.
