# 실거래 코드 리팩토링 계획 (2026-10-20 사용자 승인 방향 · 착수 전)

상태: **0 단계 끝 (2026-10-20, 사용자 "그 작업 확인해보고 진행해줘")** — 작업 브랜치 `refactor/live-code` (worktree `../Auto_Trading_refactor`).

## 목적 (사용자 말)

- "우리가 구현했던 코드들 싹다 리펙토링" — 이유: "내가 앞으로 코드를 알아 볼 수 있도록".
- 함수마다 **쓰임 · 구현 이유** 주석. 변수 · 함수명을 사람이 이해하기 쉽게.
- "중요한게 … 꼭 내가 즉 초보자가 알기 쉽도록".
- 디자인 패턴은 쓸 수 있으면 쓰고, 쓴 패턴을 알려 줄 것.

## 범위

| 한다 | 안 한다 |
|---|---|
| 실거래 핵심: `scripts/toss_accumulate.py` · `ops/deneb_live.py` · `ops/deneb_guard.py` · `scripts/vega_upbit.py` · `ops/t2_upbit.py` · `ops/deposit_split.py` · `ops/kr_accumulate.py` · `scripts/liquidate.py` · `ops/liquidate.py` | 연구 스크립트(`run_` · `audit_` · `fetch_` · `measure_` 등) · `src/auto_trading/research` — 사전 고정 커밋과 어긋나고 다시 돌릴 일 없음 |
| 거래소: `exchange/toss.py` · `exchange/upbit.py` | Sirius · Polaris(대기 — 제자리, 코드 지도에 '대기' 표시만) |
| 알림 · 화면: `scripts/telegram_bot.py` · `ui/live_sync.py` · `ui/live_home.py` · `ui/orbit_home.py` · `ui/settings_page.py` · `ui/liquidate_panel.py` | |
| 안전: `ops/kill_switch.py` · `ops/safety_check.py` · `ops/task_setup.py` · `ops/user_settings.py` · `ops/live_ledger.py` · `scripts/risk_news.py` · `scripts/ledger_sync.py` | |

약 1.5 만 줄 · 30 여 파일.

## 절대 규칙

- **동작 변경 0** — 이름 · 주석 · 중복 정리 · 풀어 쓰기만. 계산 · 주문 규칙 · 알림 문구 그대로.
- **파일 밖에 저장되는 이름은 그대로**: 장부 JSON 키, 설정 키(`deneb.gold_on` 등), 예약 작업 이름, 실행 명령(`python -m scripts.vega_upbit run` 등), 주문 번호 앞머리, 정지 파일 이름.
- 작업은 **worktree 별도 브랜치**. 합치기는 21:00(토스) · 09:05(Vega) 피해서 낮에, 단계마다.
- 확인: 전체 pytest · ruff · mypy + **전후 출력 비교**(같은 장부 사본 · 고정 가격으로 Deneb 미리 보기 · 월 계획 문구, Vega 계획, 적립 계획, 텔레그램 현황, 입금 안내가 글자까지 같아야 함).

## 초보자 기준 (사용자 강조)

- 주석 위치: **함수 이름 바로 아래 docstring** (2026-10-20 사용자 확정 — VS Code 마우스 올리면 보임).
- docstring 형식: `[쓰임] …` / `[이유] … (결정 날짜 · 사용자 말)`. 쉬운 말("변동성 목표" 대신 "요즘 많이 흔들리면 TQQQ 를 덜 산다").
- 한 줄에 한 가지 일. 겹친 컴프리헨션 · `:=` · 복잡한 한 줄 조건은 평범한 for / if 로.
- 한두 글자 변수 금지(`o` · `s` · `n` · `_f` · `exp`), 단위는 이름에(`_usd` · `_krw` · `_seconds`), 함수는 동사 + 대상.
  예: `LEV/DIV/GOLD` → `LEVERAGED_ETF/DIVIDEND_ETF/GOLD_ETF`, `choose/fill` → `plan_monthly_rebalance/plan_next_day_fill`, `Sync` → `LiveAccountSnapshot`, `apply_vega` → `sync_vega_ledger_to_upbit`, `apply_plan` → `sync_accumulate_ledger_to_toss`, `sent_ids` → `pending_toss_order_ids`, `_f` → `to_float`, `CACHE_S` → `CACHE_SECONDS`, `_ak/_sk` → `_access_key/_secret_key`.
- 파일 맨 위 3 줄: 무엇 · 언제 실행(예약 시각) · 무엇을 읽고 쓰나(장부 경로).
- 긴 함수는 `# 1 단계: …` 단계 주석. 타입 표시는 유지.

## 디자인 패턴 (초보자 기준으로 줄임)

| 패턴 | 결정 | 곳 |
|---|---|---|
| Command + 목록표 | 쓴다 | 텔레그램 메뉴 · 명령 (버튼 이름 → 처리 함수 표 하나) |
| Facade | 쓴다 | `live_sync` (실제 계좌 현황 함수 하나) |
| Repository | 쓴다 | 장부 읽기 · 쓰기 (형식 그대로) |
| Adapter | 가볍게 | 토스 · 업비트 조회를 같은 모양으로 (상속 없이) |
| Template Method | **안 씀** | 상속은 초보자가 흐름 따라가기 어려움 → 단계 주석으로 대신 |

끝나면 패턴 · 쓴 파일 표를 사용자에게 보고.

## 은퇴 코드 → `retired/` 폴더 (사용자 안: 삭제 대신 '안 쓰는 코드' 명시 + 따로 빼기)

- 대상: 바이낸스 Vega(`scripts/vega_live.py` · `scripts/live_t2_binance.py`), Altair(`scripts/altair_live.py` · `scripts/altair_demo.py` · `ops/altair_split.py`) — 관련 시험 · 기타 의존 파일은 시작 때 다시 조사.
- **먼저 같이 쓰는 부분을 떼어 낼 것** (2026-10-20 조사): `vega_upbit.py` 가 `vega_live` 를, `telegram_bot.py` 가 `vega_live` · `altair_demo` 를, `ui/live_home.py` · `scripts/shadow_272.py` 가 `altair_demo` 를, `scripts/weekly_summary.py` 가 `altair_live` 를, `vega_live.py` 가 `altair_split` 을 import. 그냥 옮기면 지금 Vega · 텔레그램이 깨진다.
- `retired/README.md` 표: 무엇 · 언제 · 왜 은퇴 · 대체한 것 · 마지막 사용 커밋 · 남은 장부 위치(`data/vega/` · `data/altair_demo/`). 각 파일 맨 위 '⚠️ 은퇴 — 실행하지 않음, 2026-10-19'.
- 시험 · ruff · mypy 에서 `retired/` 제외. README 에 '되살릴 때는 다시 검증부터'.
- 효과: 꺼 둔 `AutoTrading_Altair` 예약이 켜져도 파일 없음으로 멈춤.

## 단계 (하루 1 ~ 2 단계, 단계마다 커밋 · 보고: 바꾼 것 · 시험 수 · 출력 같음 · 바뀐 이름 표)

| 단계 | 내용 | 예상 |
|---|---|---|
| 0 | 전후 비교 기준 출력 · `docs/CODE_MAP.md` 초안 | 1 h |
| 1 | 은퇴 코드 분리 (공통 부분 먼저 떼기) | 1 ~ 1.5 h |
| 2 | 기반: 거래소 · 장부 읽기쓰기 · 정지 스위치 · 설정 | 1.5 h |
| 3 | Deneb (`toss_accumulate` 2 천 줄 최대) | 2 ~ 3 h |
| 4 | Vega · 자동 적립 · 입금 비율 · 정리 | 2 h |
| 5 | 텔레그램 봇 · 현황 동기화 · 화면 | 2 h |
| 6 | 코드 지도 완성 · 용어 사전(LOC · IOC · 평균가 · 브레이크 · σ20 …) · 패턴 표 · CURRENT_SPEC 갱신 | 1 h |

## 진행 기록

- 2026-10-20: 계획 · 방향 확정(위). 코드 변경 없음.
- 2026-10-20 0 단계: 전후 비교 기준 `tests/golden/test_live_golden.py` (4 묶음 — Deneb 첫 달 · 채우기 · 미리 보기 · 상태 / 하락 방어 주기 / Vega 업비트 / 텔레그램 문구, 기준 `tests/golden/expected/`, 같은 코드로 두 번 돌려 같음 확인 — 버튼 무작위 번호 · 보고 시각 · 임시 경로만 가림) · `docs/CODE_MAP.md` 초안. 주의: worktree 에서 시험할 때 `PYTHONPATH=<브랜치>/src;<브랜치>` 필수(가상환경이 원래 폴더 src 에 연결). worktree 에서는 `test_desktop_app.py` 3 개가 환경 차이로 실패(원래 폴더에서는 통과) — 합칠 때 원래 폴더에서 확인.
