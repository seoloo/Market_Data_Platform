# 실거래 코드 리팩토링 계획 (2026-10-20 사용자 승인 방향 · 착수 전)

상태: **0 ~ 5 단계 끝 (2026-10-21, 사용자 "다음 작업 진행해줘")** — 남은 것 6 단계(문서) — main 합치기는 낮에(계획 규칙) — 작업 브랜치 `refactor/live-code` (worktree `../Auto_Trading_refactor`).

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
- 2026-10-20 1 단계: 은퇴 5 파일(`vega_live` · `live_t2_binance` · `altair_live` · `altair_demo` · `ops/altair_split`)과 시험 5 개를 `retired/` 로, `retired/README.md`, 파일마다 '⚠️ 은퇴' 표시. 먼저 뗀 것: 텔레그램 봇의 죽은 바이낸스 입금 확인 · 꺼진 Altair 데모 호출(지움), 주간 요약의 Altair 접속 함수(자체로), 그림자 272 의 롱 목표 함수(그대로 옮김), Deneb 금 시험의 altair_split 시험 1 개. ruff 가 `retired/` 제외. 전후 비교 4 묶음 같음 · 전체 pytest(worktree 환경 차이 desktop_app 3 개만 실패, 원래와 같음) · leakage · mypy(기존 10 건만) · ruff(연구 스크립트 기존 건만).
- 2026-10-20 2 단계 (기반): 거래소 `exchange/toss.py` · `exchange/upbit.py`, 정지 `ops/kill_switch.py`, 설정 `ops/user_settings.py`, 기록 DB `ops/live_ledger.py`, 안전 점검 `ops/safety_check.py` · `scripts/safety_check.py`, 예약 `ops/task_setup.py`. 파일 맨 위 '언제 · 읽고 쓰는 것', 함수마다 `[쓰임]` / `[이유]`, 한두 글자 변수 풀기, 겹친 컴프리헨션 · 한 줄 조건 풀기, 긴 함수 단계 주석. **공개 함수 이름은 그대로**(범위 밖 연구 · 데모 스크립트도 import) — 파일 안 이름만 바꿈. 새 `ops/ledger_file.py`(Repository — `read_json_or_none` · `write_json_atomic`, 형식 그대로)를 설정 저장 · 안전 점검 기록에 씀(안전 점검 기록은 이제 임시 파일에 쓰고 바꿔 끼움 — 내용 같음). 전후 비교 기준에 `user_settings`(폼 검사 · 저장 · 기록 · 종목 추가/빼기 · 정지, CR 까지) · `live_ledger`(거래 줄 · 평가 줄 · SQLite · Supabase 보낼 것) 2 묶음 더함 — 둘 다 바꾸기 전 코드로 기준을 찍고 새 코드와 같음. 실제 계좌로 안전 점검 `check()` 를 바꾸기 전 · 뒤 코드로 돌려 결과 같음(토스 · 업비트 · 바이낸스 조회 · 설정 · 정지 파일). 따로 커밋: 은퇴 흔적 정리(`1f15507` — 설정 `live.altair` · `demo.altair` · `vega.earn`, 자동 실행 Altair 예약 · 묶음, Vega 묶음 설명 업비트로, 안전 점검 선물은 포지션만) · main 에 먼저 넣은 안전 점검 수정(`0ae57a0` — Vega 장부 비교를 업비트로, 10-20 09:20 거짓 경고).
- 2026-10-20 3 단계 (Deneb): `ops/deneb_live.py` · `ops/deneb_guard.py` · `scripts/toss_accumulate.py`(2 천 줄). 앞서 전후 비교 기준 5 묶음 더함(금액 묻기 · 답 · 밤 매수, 재시도, 12 월 공제 실현, 배당 단계 제안, 금 섞기 · 배당 둘째 달 — 바꾸기 전 코드로 찍고 세 번 같음 확인) → 모두 11 묶음. 바뀐 이름: `LEV/DIV/GOLD` → `LEVERAGED_ETF/DIVIDEND_ETF/GOLD_ETF`, `choose` → `plan_monthly_rebalance`, `fill` → `plan_next_day_fill` (부르는 곳 `ops/deneb_shadow` · `scripts/deneb_shadow` · 시험 같이 고침, 장부 주문 종류 값 `"fill"` 은 그대로), `toss_accumulate` 안 `_pref` → `_setting` · `_since` → `_days_between` · `_brake_of` → `_load_brake` · `_props/_put` → `_load_proposals/_store_proposals`, 새 도움 함수 `_first_tqqq_buy_day` · `_find_typed_proposal` · `_load_plan_states`. **시험이 바꿔 끼우는 함수 이름(`_kst_now` · `_notify` · `_trader` · `_deneb_poll` 등)은 그대로.** `run` · `_deneb_run` · `_deneb_brake` · `_deneb_poll` · `_deneb_actions` 등 긴 함수에 단계 주석, 한 글자 변수 · `:=` · 겹친 컴프리헨션 풀기. 전체 시험 4,446 (worktree 환경 차이 desktop_app 3 개만 실패) · 누수 · mypy(기존 10) · ruff(연구 스크립트 기존 건만) · lint-imports(기존 1 건). 같은 날 따로: main 에 🧭 Polaris Paper(`7519ef7`, 사용자 "polaris 같은 경우 paper만") · 옛 주간 요약 예약 꺼짐.
- 2026-10-21 4 단계 (Vega · 자동 적립 · 입금 나누기 · 정리): `ops/t2_upbit.py` · `scripts/vega_upbit.py` · `ops/kr_accumulate.py` · `scripts/toss_kr.py` · `ops/qndx_alert.py` · `ops/qs_live.py`(머리말만 — 실거래 꺼짐) · `ops/deposit_split.py` · `ops/liquidate.py` · `scripts/liquidate.py`. 앞서 전후 비교 기준 4 묶음 더함(Vega 정지 30 일 · 정지 풀기 · 주간 요약 · 현황 / 국내 적립 / 입금 나누기 글 / 전부 정리 — 바꾸기 전 코드로 찍음) → 모두 15 묶음 같음. 공개 이름 그대로, 파일 안: vega `_fmt_w` → `_weights_text` · `_sig_row` → `_signal_record` · `_prev_score` → `_previous_score`, 도움 함수 `_record_flows` · `_update_stop` · `_record_order` · `_trades_since`; toss_kr `_settle_past_orders`; liquidate `_order_from_dict` · `_item_from_dict` · `_kept_text` · `_upbit_balances_prices` · `_binance_balances` · `_open_items` · `_step_upbit` · `_step_binance` · `_step_toss`; kr_accumulate `_norm` → `_normalize` · `_prev` → `_previous_day`. 장부 쓰기는 `ledger_file` (vega · toss_kr · 정리의 Deneb 장부 · 옛 Altair 장부 — 형식 같음). 입금 나누기의 은퇴한 Altair 안내 칸 뺌(쓰는 곳 없음). 전체 시험 4,450 (worktree desktop_app 3 개만) · 누수 · mypy(기존 10) · ruff(연구 스크립트 기존 건만) · lint-imports(기존 1 건). **발견(기존 버그, 고치지 않음)**: 정리 작업 번호가 '시작한 초' 라 같은 초에 두 범위를 시작하면 `Store.put` 이 앞 작업 기록을 덮어씀 — 사람이 누를 때는 드묾, 고칠지 사용자에게 물음. 비교 기준은 시계를 고정해 피함.
- 남은 Altair 흔적(정리 그룹 · 정지 그룹 `kill_switch.GROUPS['altair']` · 실거래 홈 `altair_book` · 주간 요약 Altair 칸 · paper 요약)은 해당 파일 단계(4 · 5)에서 정리. (설정 · 예약 정의 · 안전 점검 선물은 2 단계에서 정리함)
- 발견(기존, 다른 세션 10-20 커밋 `ee617f7` · `30f56b9`): lint-imports 'UI 는 매매 로직을 import 하지 않는다' 깨짐 — `ui/live_sync` 가 `exchange.toss` · `exchange.upbit` 를 직접 씀. 5 단계(현황 동기화 Facade)에서 고침.
- 2026-10-21 정리 작업 번호 겹침 고침(`be5bb44`, 사용자 '고쳐줘'): 같은 번호가 있으면 `-2` · `-3` 을 붙임 + 시험.
- 2026-10-21 5 단계 (텔레그램 · 현황 · 화면): 앞서 전후 비교 기준 4 묶음 더함(`test_bot_ui_golden.py` — 텔레그램 적립 · 정리 · 정지풀기 대화 / 현황 글 · 실거래 홈 HTML / 설정 · 정리 칸 HTML, 바꾸기 전 코드로 찍음) → 19 묶음. ① 현황 동기화: 거래소 조회를 `app/live_accounts.py` 로 옮기고 실행기 4 곳(텔레그램 봇 · 대시보드 · 매일 기록 · 파이어 보고)이 `install()` 로 넣어 줌(의존성 주입) → **import 계약 5 개 모두 지킴**. 이름 `Sync` → `LiveAccountSnapshot` · `apply_vega/deneb/plan` → `sync_vega_ledger_to_upbit` · `sync_deneb_ledger_to_toss` · `sync_accumulate_ledger_to_toss` · `sent_ids` → `pending_toss_order_ids` · `CACHE_S` → `CACHE_SECONDS`. 실계좌 현황 글 전후 같음(시세 움직임만 다름). ② 텔레그램 봇: 받은 명령 나누기를 표(`COMMAND_KINDS` · `_sort_commands`), 매 실행 부가 작업을 표(`BACKGROUND_JOBS`)로(Command + 목록표), 별칭 `sg` · `liq` 풀기. ③ 화면 · 위험 뉴스 · 기록 맞추기: `[쓰임]` · `[이유]` 56 개, 한 글자 변수 풀기(함수 범위 토큰만 바꾸는 도구로 — `d`→`row` · `o`→`order` · `q`→`quotes` · `b`→`book` · `h`→`holding`, `_f` → `_to_float`). ④ 은퇴 흔적 정리(따로 커밋): 정리 범위 · 정지 그룹 Altair, 정리의 바이낸스 선물 코드(`run_futures_item` · `_step_futures` · `_altair_*`), 텔레그램 · 설정 화면 'Altair 만' 버튼, 실거래 홈 바이낸스 Vega · Altair 카드(`vega_binance_book` · `altair_book` · `VEGA_ON_UPBIT` · `BINANCE_VEGA`), Paper 요약의 멈춘 Altair 데모 줄. 옛 작업 기록(`data/liquidate/jobs.json` 에 'altair' 범위 · `binance_fut` 2 건)은 `RETIRED_SCOPES` · `VENUE_NAMES` 로 그대로 읽음(새 정리는 못 시작) + 시험. 기준 3 묶음(텔레그램 문구 · 정리 대화 · 설정 칸)은 Altair 버튼만 빠진 것을 글자 단위로 확인하고 다시 찍음. 시험 파일 `tests/ui/test_live_altair.py` → `test_live_sirius.py`(Sirius 시험만 남김). **남김(사용자 결정 필요)**: `scripts/weekly_summary.py` · `ops/weekly_summary.py` 는 통째로 옛 바이낸스 Vega · Altair · 실거래 Polaris 기준(예약 꺼짐, 업비트 Vega 는 자기 주간 요약이 따로 있음) — Altair 칸만 빼는 대신 통째 은퇴(`retired/`) 여부를 물음. 안전 점검의 '바이낸스 선물 포지션 있으면 경고' 는 안전망이라 둠. 파이어 보고의 옛 Altair 이체 셈은 지난 돈 기록이라 둠.
