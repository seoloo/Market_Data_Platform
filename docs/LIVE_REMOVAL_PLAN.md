# 실주문 · 실계좌 코드 걷어 내기 — 목록 (2단계 준비, 2026-10-21)

> 절대 규칙 2(실제 주문 · 실계좌 연동은 이 저장소에서 동작하면 안 된다)를 지키기 위한 **목록**이다.
> 이 문서를 만들 때는 코드를 하나도 고치지 않았다. 사용자가 아래 '정할 것' 을 고른 뒤 묶음별로 지운다.

## 어떻게 찾았나

1. **씨앗**: 거래소 · 증권사에 **키로 접속하는** 코드를 모두 읽고 분류했다(실주문 · 실계좌 조회 · 모의 주문).
2. **import 관계 그래프**: `src` · `scripts` · `tests` · `retired` 의 파이썬 파일 1,218 개를 AST 로 읽어 누가 누구를 import 하는지
   그래프를 만들었다(함수 안 import · `importlib` 포함).
3. **지울 뿌리**: 씨앗 + 실거래 전용 실행 스크립트 + `retired/`.
4. **고아**: 지울 것만 import 하던 `src` 모듈을 반복해서 찾았다(시험 파일은 '쓰는 곳' 으로 세지 않음).
5. **고칠 곳**: 남는 파일 중 지울 것을 import 하는 곳.
6. **시험 수**: 지울 시험 파일을 `pytest --collect-only` 로 셌다.

## 한눈에

| 묶음 | 파일 | 줄 | 내용 |
|---|---:|---:|---|
| A. 거래소 · 증권사 연결 | 13 | 4,080 | 실주문 6 · 실계좌 조회 1 · 모의 주문 4 · 따라 지워지는 계약/해석 2 |
| B. 주문 실행기 | 7 | 2,848 | 실거래 실행기 · 선물 주문 관리 · 보호 스톱 · 지갑 이체 |
| C. 실거래 실행 스크립트 | 38 | 14,665 | 적립 · Deneb · Vega · 개별주 · 정리 · 테스트넷 연습 · 계좌 점검 · 텔레그램 봇 · 대시보드 |
| D. 실거래 계산 · 운영 (`ops`) | 28 | 7,768 | 위 스크립트만 쓰던 계산 · 설정 · 예약 작업 관리 |
| E. 화면 (`ui` 전체) | 24 | 8,746 | 「오르빗」 대시보드 · 설정 · 정리 버튼 화면 |
| F. 실계좌 조회 창구 (`app`) | 2 | 83 | 업비트 · 토스 계좌 조회 |
| G. 주문 기록 저장소 | 3 | 413 | `futures_orders` · `risk_events` · `wallet_transfers` |
| H. `retired/` | 13 | 3,759 | 이미 은퇴한 바이낸스 실거래 코드와 시험 |
| 시험 | 99 | 18,868 | **시험 1,009 개** (지금 4,447 개 → 약 3,438 개) |
| **합계** | **227** | **61,230** | |

파이썬이 아닌 것: `deploy/tasks/*.xml` 19 개(이 PC 의 Windows 예약 작업), `docs/supabase/schema.sql`(실거래 장부 표),
대시보드 · 바로 가기용 `scripts/*.cmd` · `*.ps1` 7 개, 화면 그림 `src/auto_trading/ui/assets/`(11 MB),
골든 기대값 `tests/golden/expected/`(464 KB).

## A. 거래소 · 증권사 연결 (`src/auto_trading/exchange/`)

| 파일 | 줄 | 분류 | 하는 일 |
|---|---:|---|---|
| `binance_spot_live.py` | 290 | 실주문 | 바이낸스 현물 실계좌 주문 · 지갑 이체 |
| `binance_usdm_live.py` | 147 | 실주문 | 바이낸스 선물 실계좌 주문 |
| `toss.py` | 720 | 실주문 | 토스증권 미국 · 국내 주문 · 조회 |
| `upbit.py` | 258 | 실주문 | 업비트 원화 현물 주문 · 조회 |
| `kis.py` | 573 | 실주문 | 한국투자증권 조회 + 예약 매수 |
| `namu_global.py` | 455 | 실주문 | 나무증권 해외주식 원화 주문 |
| `binance_usdm.py` | 394 | 실계좌 조회 | 선물 계좌 · 포지션 서명 조회 |
| `binance_spot_demo.py` | 181 | 모의 주문 | 바이낸스 현물 데모 계좌 주문 |
| `binance_usdm_testnet.py` | 333 | 모의 주문 | 바이낸스 선물 테스트넷 주문 |
| `binance_usdm_stream.py` | 204 | 모의 주문 | 선물 사용자 스트림(키 필요) |
| `namu.py` | 296 | 모의 주문 | 나무증권 국내 모의투자 |
| `overseas.py` · `user_stream.py` | 229 | 따라 지워짐 | 해외주식 계약 · 사용자 스트림 해석 — 위 파일만 씀 |

**남기는 것**

- `binance_usdm_public.py`: 키 없이 공개 시세만 받는다.
- `spot.py` · `futures.py` · `wallet.py`: 계약(모양)만 정의한다.
- `fake_spot.py` · `fake_futures.py` · `fake_wallet.py`: Paper 와 시험에서 쓰는 가짜 거래소다.

**쪼개는 것**: `binance_spot.py`(201 줄)는 키 없이(`BinanceSpotReadOnly(None)`) 공개 시세를 받는 데도 쓴다.
Paper · 연구 스크립트 5 개가 이 용도로 쓴다(`run_paper_o1` · `run_paper_t2` · `run_shadow_t2` · `shadow_272` · `audit_staking_rates`).
그래서 이 파일은 남기고, 키로 서명하는 계좌 조회 부분만 뺀다.

## B ~ H. 지울 파일

<details><summary>B. 주문 실행기 (7)</summary>

`execution/`: `live_t2` · `demo_limit` · `futures_orders` · `futures_guard` · `overlay_futures` · `stream_sync` · `wallet_transfers`

남김: `paper_t2` · `paper_overlay` · `shadow_t2` · `overlay_plan` · `spot_orders` (Paper 가 가짜 거래소와 함께 쓴다).
</details>

<details><summary>C. 실거래 실행 스크립트 (38)</summary>

| 무리 | 스크립트 |
|---|---|
| 토스 적립 · Deneb · 개별주 | `toss_accumulate` · `toss_kr` · `size_guard` · `qndx_alert` · `stock_reco` · `sirius_live` · `deneb_shadow` · `us_tax` |
| 업비트 Vega | `vega_upbit` · `live_t2_upbit` |
| 나무증권 | `polaris` · `namu_global` · `namu_follow` · `namu_mock` |
| 정리 · 점검 | `liquidate` · `safety_check` · `check_futures_account` · `check_kis_account` · `check_spot_account` |
| 테스트넷 · 데모 연습 | `testnet_drill` · `testnet_overlay_drill` · `testnet_stop_drill` · `testnet_stop_replace_drill` · `testnet_stream_drill` · `demo_ew_btc` · `demo_o2_futures` · `demo_t2_spot` |
| 장부 · 보고 | `ledger_sync` · `live_snapshot` · `fire_report` · `backup_state` · `risk_news` |
| 봇 · 화면 · 예약 | `telegram_bot` · `dashboard` · `desktop_app` · `desktop_dashboard` · `task_launcher` · `tasks_export` |
</details>

<details><summary>D. 실거래 계산 · 운영 (28)</summary>

`ops/`: `deneb_live` · `deneb_guard` · `deneb_shadow` · `qs_live` · `sirius_live` · `t2_upbit` · `qndx_alert` · `position_size` · `size_order` ·
`stop_guard` · `stock_journal` · `stock_reco` · `liquidate` · `live_ledger` · `safety_check` · `conn_test` · `env_keys` · `deposit_split` ·
`fire` · `kr_earnings` · `namu_follow` · `paper_summary` · `phone_link` · `risk_news` · `task_setup` · `tax_harvest` ·
`us_capital_gains` · `catchup` · (`ops/__init__` 은 남김 — 그래프상 고아로 잡혔지만 패키지 파일)
</details>

<details><summary>E. 화면 — ui 전체 (24)</summary>

`server` · `render` · `live_home` · `orbit_home` · `live_sync` · `data` · `settings_page` · `settings_extra` · `liquidate_panel` · `stock_home` ·
`running` · `overview` · `charts` · `catchup` · `demo_data` · `demo_render` · `ag_render` · `kr_shadow_render` · `leader_alert_render` ·
`lt_render` · `qndx_render` · `qs_render` · `orbit_icons` · `__init__` + `assets/` · `*.css`
</details>

<details><summary>F · G · H</summary>

- F: `app/live_accounts.py` · `app/__init__.py`
- G: `storage/repositories/futures_orders.py` · `risk_events.py` · `wallet_transfers.py` (표 정의 SQL 은 그대로 — 이미 적용된 마이그레이션은 바꾸지 않는다)
- H: `retired/` 폴더 전체
</details>

## 고칠 곳 (지운 것을 부르던 남는 파일)

| 파일 | 부르던 것 | 고치는 법 |
|---|---|---|
| `scripts/run_qs_switch.py` · `fetch_kr_stocks.py` · `fetch_sb_universe.py` · `run_gem.py` · `audit_x_data.py` | `kis` (실계좌 키로 시세 받기) | 받기 함수만 빼고, 저장된 자료 읽기는 그대로. 다시 받기는 3 단계 공개 원천으로 |
| `scripts/lt_paper.py` · `nq_paper.py` · `qs_paper.py` | `kis` | 위와 같음 — Paper 기록은 저장된 시세까지만 |
| `scripts/polaris_paper.py` · `event_alert.py` | `toss` (시세 · 일정 조회) | 토스 조회 부분만 뺀다 |
| `scripts/pead_paper.py` | `size_guard.offer_pead` (실거래로 넘기기) | 넘기는 부분만 뺀다 |
| `scripts/ew_indicator_1h.py` | `demo_ew_btc` 의 계산 함수 | 쓰는 계산 함수만 이 파일로 옮김 (연구 숫자 그대로) |
| `config/secrets.py` | 키 읽기 `load_kis/toss/namu/binance_credentials` | 지운다 (텔레그램 · AI 키 읽기는 남김) |
| `.env.example` | `BINANCE_API_KEY` · `BINANCE_TESTNET_*` | 줄을 지운다 |
| `pyproject.toml` | lint-imports 'UI 는 매매 로직을 import 하지 않는다' 계약 | `ui` 가 없어지므로 계약을 지운다 (나머지 4 개 그대로) |

## 정할 것

| # | 질문 | 추천 | 이유 |
|---|---|---|---|
| 1 | 모의 · 테스트넷 주문 코드(가짜 돈이지만 거래소 키로 접속)도 지울까? | **지움** | DE 와 무관하고, 공개 저장소에 거래소 키 설정이 남지 않게 |
| 2 | KIS · 토스를 **실계좌 키로 시세만** 받는 곳도 지울까? (Paper 3 개 · 연구 5 개가 새 자료를 못 받게 됨) | **지움** | 키로 실계좌에 접속하는 것 자체가 규칙 2 의 '실계좌 연동'. 저장된 자료로는 그대로 돈다 |
| 3 | 실거래 계산이지만 **연구 시험이 쓰는 것** 2 곳: `ops/t2_upbit`(연구 197 시험 7 개) · `execution/futures_orders` + 저장소(연구 2026-10-13 시험 39 개) | **남김** | 주문을 보낼 거래소 연결이 없어 동작할 수 없다. 연구 결론의 검증 시험을 지키려고(규칙 6 의 취지) |
| 4 | 화면(`ui`) 전체 — 연구 페이지 포함 「오르빗」 대시보드 | **지움** | 실거래 홈 · 설정 · 정리 버튼과 얽혀 있다. DE 대시보드는 나중에 새로 |
| 5 | 매매가 아닌 텔레그램 정보 도구(`info_bot` · `market_brief` · `theme_alert` · `leader_alert` · `event_alert`)와 `ops/user_settings` | **이번엔 남김** | 실주문과 무관. DE 와도 무관하니 나중에 따로 정리 후보 |

3 번을 '남김' 으로 하면 위 표에서 파일 5 개(코드 3 · 시험 2)와 시험 46 개가 빠진다(지우는 시험 963 개).

**정함 (2026-10-21 사용자 "정할 것 5가지 추천대로 하고")**: 1 지움 · 2 지움 · 3 남김 · 4 지움 · 5 이번엔 남김.

**미룸 (2026-10-21 사용자 "UI는 나중에 하고 전반적인 파이프라인을 먼저 구축")**: 묶음 1 뒤 멈추고 파이프라인(README 3-1 ~ 6-2)을
먼저 만든다. 묶음 2 ~ 6 은 README 6-3 — **저장소 공개 전에는 반드시 끝낸다.** 그동안 이 폴더에는 거래소 키 파일(`.env`)이 없어
남은 실주문 코드는 거래소에 접속할 수 없다(2026-10-21 확인).

## 이번 범위 밖 (기록만)

- **개인 정보가 남은 문서**:
  - 대상: `PROGRESS.md` · `docs/BRIEFING_FOR_LLM.md` · `docs/current/OPERATIONS.md` · `docs/current/RESEARCH_HISTORY.md` ·
    `docs/research/` 4 개 · `reports/deneb_polaris_report_20261017/` 2 개 (`C:\Users\seolh` 경로 · 봇 이름).
  - 연구 문서라 숫자 · 결론은 건드리지 않고 경로 · 이름만 가리는 작업을 3 단계 문서 정리 때 따로 한다.
- **실거래를 설명하는 문서**(`docs/CODE_MAP.md` · `docs/current/OPERATIONS.md` · `docs/REFACTOR_PLAN.md` 등)와 AGENTS.md §0 · §5: 코드를 지운 뒤 함께 고친다.
- `config/live.yaml` · `testnet.yaml`: 설정 스키마 시험이 읽는다. 지우면 그 시험도 고쳐야 해서 코드 정리 뒤에 판단.
- git 히스토리에는 지운 코드가 남는다. 공개할 때 히스토리를 어떻게 할지는 그때 따로 정한다.

## 지우는 순서 (정한 뒤)

각 묶음마다 전체 pytest · `pytest -m leakage` · mypy · ruff · lint-imports 를 돌리고 커밋한다.

1. `retired/` · `docs/supabase/` (아무도 import 하지 않음)
2. 화면(E) · 봇 · 대시보드 스크립트
3. 실거래 실행 스크립트(C) · 계산(D) · 창구(F), `deploy/tasks/` — 예약 틀은 `ops/task_setup` 과 그 시험
   (`test_templates_are_clean` 이 실제 XML 을 읽는다)이 쓰므로 함께 지운다
4. 주문 실행기(B) · 주문 기록 저장소(G)
5. 거래소 연결(A), `binance_spot` 쪼개기, `secrets.py` · `.env.example` · `pyproject.toml`
6. 고칠 곳(위 표)과 남은 시험 정리

## 진행 기록

| 묶음 | 지운 것 | 확인 |
|---|---|---|
| 1 | `retired/` (13 개 · 3,759 줄) · `docs/supabase/schema.sql`, `pyproject.toml` ruff 제외 목록에서 `retired` | pytest 4,447 통과 · 10 건너뜀(그대로 — `retired/tests` 는 원래 시험 경로 밖) · leakage 157 통과 · mypy 15 건(그대로) · lint-imports 5 개 지킴 · ruff 16 건 · 서식 3 파일(그대로 — 연구 스크립트 `run_deneb1x_262` · `run_qqq_combo_263` · `run_split_264` 에 원래 있던 것) |
| 2 · 3 (함께) | 화면 `ui` 전체(이미지 포함) · `app` · 실거래 실행 스크립트 38 · 운영 계산 `ops` 27(`t2_upbit` 은 정할 것 3 대로 남김) · 대시보드 바로 가기 7 · `deploy/tasks` · 골든 시험 전체(`tests/golden`) 와 그것들만 쓰던 시험 — 265 개. 묶음 2 와 3 은 골든 시험이 서로 얽혀 있어 한 번에. 고친 곳: `ew_indicator_1h` 가 쓰던 계산 · 문구 · 상태를 `demo_ew_btc` 에서 글자 그대로 옮김, `pead_paper` 의 실거래 넘기기만 뺌, `test_laya` 는 남는 Laya 시험만, `test_event_entry_integration` 은 남는 `event_alert` 시험 3 개만, import-linter 의 UI 계약 · `app` 이름 지움. 그대로 둔 것: `reports/deneb_fraction_20261018/calc.py` (연구 보고 — 규칙 6, 지운 `ops/deneb_*` 를 불러 더는 돌지 않음) | pytest 3,870 통과 · 19 건너뜀 (4,528 → 지운 시험만큼 줄어듦) · leakage 156 (지운 `size_guard` 의 다음 날 추천 누출 시험 1 개가 코드와 함께 빠짐) · mypy 4 건(15 → 지운 시험에 있던 것) · ruff 16 건 · 서식 3 파일 (그대로) · lint-imports 4 개 지킴 (UI 계약 지움) |
| 4 | 주문 실행기 `execution/` 6 (`live_t2` · `demo_limit` · `futures_guard` · `overlay_futures` · `stream_sync` · `wallet_transfers`) · 주문 기록 저장소 2 (`risk_events` · `wallet_transfers`) · 그 시험 6 — 14 개. 정할 것 3 대로 `execution/futures_orders` 와 그 저장소는 남김 (연구 2026-10-13 시험이 씀, 주문을 보낼 거래소 연결이 없어 동작할 수 없다). 표 정의 SQL 은 그대로 (이미 적용된 마이그레이션) | pytest 3,774 통과 · 19 건너뜀 · leakage 156 · mypy 4 · ruff 16 · lint-imports 4 개 지킴 |
| 5 · 6 (함께) | 거래소 · 증권사 연결 13 (실주문 6 · 실계좌 조회 1 · 모의 주문 4 · 따라 지워지는 2)과 그 시험 12 — 25 개. `binance_spot` 은 키 없는 공개 시세만 남겨 `BinanceSpotPublic` 으로 (서명 · 잔고 · 주문 조회 · 키 권한 뺌, 쓰는 스크립트 4 개 이름만 바꿈). `config/secrets.py` 에서 증권사 · 거래소 자격 증명 4 종 뺌(텔레그램 · AI 키는 남김), `.env.example` 의 바이낸스 줄 뺌. 고칠 곳(묶음 6): 실계좌 키로 시세 · 일정 · 보유를 받던 함수 10 곳(KIS 8 · 토스 2)의 몸통만 바꿈 — Paper 갱신은 '받지 않음' 으로, 받기 명령은 이유를 말하고 멈춤, 저장된 자료 읽기는 그대로. 목록에 없던 것 2 곳도 같은 이유로: `run_shadow_t2` 의 실계좌 읽기(키 권한 · 시계 · 잔고), `audit_staking_rates` 의 키 서명 받기 | pytest 3,597 통과 · 19 건너뜀 · leakage 156 (지운 토스 일봉 누출 시험 대신, 남는 바이낸스 공개 어댑터의 같은 성질 시험에 표시) · mypy 4 · ruff 16 · 서식 3 파일 (그대로) · lint-imports 4 개 지킴. 남은 코드에 서명 · 키 헤더 · 실거래소 주문 구현 없음(가짜 거래소만) |
