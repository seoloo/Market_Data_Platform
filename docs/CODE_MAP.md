# 실거래 코드 지도 (초안 — 리팩토링 0 단계, 2026-10-20 · 5 단계까지 반영 2026-10-21)

> 처음 보는 사람이 "무엇이 · 언제 · 무엇을 읽고 쓰는지" 찾는 지도. 리팩토링 단계마다 고쳐 쓴다
> ([REFACTOR_PLAN.md](REFACTOR_PLAN.md)). 6 단계에서 용어 사전 · 디자인 패턴 표를 붙여 완성한다.

## 1. 한눈에 — 실제 돈이 움직이는 것

| 전략 | 증권사 · 거래소 | 언제 도나 (예약 작업) | 실행 파일 | 계산만 하는 파일 | 장부 |
|---|---|---|---|---|---|
| 🌟 **Deneb** — TQQQ + SCHD (+ 금 IAUM) 매달 적립, 하락 방어 | 토스 (미국) | 평일 21:00 `Toss_Accumulate` · 재시도 23:40 · 03:30 `Deneb_Retry` · 금액 묻기 09:00 `Deneb_Chat` | `scripts/toss_accumulate.py` | `ops/deneb_live.py` (월 계획 `plan_monthly_rebalance` · 다음 날 채우기 `plan_next_day_fill` · 배당) · `ops/deneb_guard.py` (하락 방어 · 배당 단계 · 제안) | `data/toss/DENEB.json` |
| 💰 **자동 적립 (미국)** — BTQ · WQTM · QS 매일 소액, 1 주 값이 모이면 1 주 | 토스 (미국) | 평일 21:00 `Toss_Accumulate` · 환전 알림 09 · 12 · 15 시 `Toss_FX` | `scripts/toss_accumulate.py` | `ops/qndx_alert.py` (적립 판단) · `ops/qs_live.py` | `data/toss/<기호>.json` |
| 🇰🇷 **자동 적립 (국내)** | 토스 (국내) | 평일 12:10 `Toss_KR` | `scripts/toss_kr.py` | `ops/kr_accumulate.py` | `data/toss/KR_<코드>.json` |
| 🌌 **Vega** — BTC · ETH · SOL 원화, 추세 따라 비중 | 업비트 | 매일 09:05 `Vega` · 토 09:30 `Vega_Weekly` | `scripts/vega_upbit.py` | `ops/t2_upbit.py` | `data/upbit_t2/state.json` |
| 💹 **개별주** — 비중 추천 · −3% 손절 · PEAD 소액 | 토스 (미국) | 텔레그램 봇 안에서 2 분마다 | `scripts/size_guard.py` (봇이 부름) | `ops/size_order.py` · `ops/stop_guard.py` · `ops/position_size.py` | `data/size_guard/state.json` |

대기 · 꺼짐 (이번 리팩토링 범위 밖, 제자리): 🧭 Polaris — 실거래 꺼짐(설정 `polaris.on`), **Paper 만** `scripts/polaris_paper.py` (예약 `Polaris` 아침 실행, 장부 `data/polaris_paper/state.json`, 2026-10-20) · ✨ Sirius(꺼짐).
은퇴 (`retired/`, 실행하지 않음): 바이낸스 Vega(`vega_live.py` · `live_t2_binance.py`) · ☄️ Altair(`altair_live.py` · `altair_demo.py` · `ops/altair_split.py`) — 2026-10-19 Altair 정리 · Vega 업비트로 되돌림. 자세한 것은 [retired/README.md](../retired/README.md).

## 2. 함께 쓰는 부품

| 하는 일 | 파일 | 읽고 쓰는 곳 |
|---|---|---|
| 토스 주문 · 조회 | `exchange/toss.py` | 토스 Open API, 토큰 `data/toss_token.json` |
| 업비트 주문 · 조회 | `exchange/upbit.py` | 업비트 API |
| 긴급 정지 (파일이 있으면 새 주문 멈춤) | `ops/kill_switch.py` | `data/HALT`(전체) · `data/HALT_<그룹>`(vega · deneb · acc · stock · sirius — 남은 `HALT_altair` 는 읽는 곳 없음) |
| 장부 파일 읽기 · 쓰기 (임시 파일에 쓰고 바꿔 끼움, 형식 그대로) | `ops/ledger_file.py` | 부르는 쪽 장부 (설정 · 안전 점검 기록 · Deneb · Vega · 국내 적립 · 정리) |
| 전부 정리 (누를 때만 — 전부 · Vega · Deneb · 적립 · 개별주 · Sirius · 한 종목) | `scripts/liquidate.py` · `ops/liquidate.py` · `ui/liquidate_panel.py` | `data/liquidate/` · 보고 `reports/liquidate/` |
| 사용자 설정 (오르빗 설정 화면 · 텔레그램 '적립') | `ops/user_settings.py` · `ui/settings_page.py` | `data/settings/settings.json` · 바꾼 기록 `changes.jsonl` |
| 예약 작업 켜기 · 끄기 | `ops/task_setup.py` | Windows 작업 스케줄러 |
| 매일 안전 점검 (09:20 뒤 한 번, 봇이 부름) | `scripts/safety_check.py` (조회) · `ops/safety_check.py` (판단만) | 기록 `data/safety/state.json` · Vega 장부 `data/upbit_t2/state.json` |
| 입금 나누기 안내 (Deneb · Vega 비율) | `ops/deposit_split.py` | 설정 값 |

## 3. 알림 · 화면

| 하는 일 | 파일 | 언제 |
|---|---|---|
| 텔레그램 봇 — 받은 명령을 종류별로 나눠(`COMMAND_KINDS`) 처리 → 매번 부가 작업 목록(`BACKGROUND_JOBS`: 정리 이어 하기 · 개별주 감시 · 이벤트 알림 · 나무 따라가기 · 그림자 272 · Paper 요약 · 안전 점검) | `scripts/telegram_bot.py` | 2 분마다 `Telegram_Bot` |
| 현황을 그 순간 실제 계좌로 맞춘 사본 (Facade) | `ui/live_sync.py` (계산 · 60 초 저장) + `app/live_accounts.py` (업비트 · 토스 조회 창구 — 실행기가 시작 때 `install()`) | 봇 · 화면 · 매일 기록 · 파이어 보고가 부름 |
| 실거래 홈 · 오르빗 화면 | `ui/live_home.py` · `ui/orbit_home.py` | 대시보드(`Dashboard`, 계속 실행) |
| 보유 종목 위험 뉴스 | `scripts/risk_news.py` | 매시 17 분 `Risk_News` |
| 실거래 기록 DB 맞추기 (JSON → SQLite → Supabase) | `scripts/ledger_sync.py` · `ops/live_ledger.py` | 23:00 `Ledger_Sync`, DB `data/live_ledger.db` |

## 4. 하루 흐름 (한국 시간)

| 시각 | 일 |
|---|---|
| 07:30 | 미장 주도주 추천 확인 `Stock_Reco` |
| 09:00 | Deneb 이번 달 금액 묻기 (매달 첫 거래일 전) · 환전 알림 |
| **09:05** | **Vega 하루 판단 · 주문 (업비트)** |
| 12:10 | 국내 자동 적립 |
| **21:00** | **토스: 지난 주문 체결 확인 → Deneb 하락 방어 → 월 계획 또는 다음 날 채우기 → 미국 자동 적립 (장 마감 지정가)** |
| 23:40 · 03:30 | Deneb 월 계획 재시도 (달러 부족 · 장 시간 문제였을 때만) |
| 2 분마다 | 텔레그램 봇 (명령 · 버튼 · 개별주 손절 감시 · 입금 확인) |
| 매시 17 분 | 위험 뉴스 |

## 5. 전후 비교 기준 (리팩토링이 동작을 바꾸지 않았는지)

`tests/golden/test_live_golden.py` — 가짜 거래소 · 고정 날짜로 아래를 돌려 `tests/golden/expected/*.txt` 와 **글자까지** 비교.

| 기준 | 무엇 |
|---|---|
| `deneb_first_month` | Deneb 첫 달 계획 · 다음 날 채우기 · 미리 보기 · 상태 출력 · 알림 · 낸 주문 · 장부 |
| `deneb_brake_cycle` | 하락 방어 매도 → 보관금 → 추세 꺼짐 · 켜짐 → 되사기 · 보고 파일 |
| `vega_upbit` | Vega 계획 · 첫 매수 · 입금 감지 · 상태 · 알림 · 장부 |
| `telegram_texts` | 텔레그램 도움말 · 메뉴 · 적립 메뉴 버튼 |
| `user_settings` | 설정 폼 검사 · 바뀐 칸 · 저장 파일(줄바꿈까지) · 바꾼 기록 · 종목 추가/빼기 · 비상 정지 (2 단계) |
| `live_ledger` | 장부 → 거래 줄 · 하루 평가 줄 · SQLite 두 번 쓰기 · Supabase 로 보낼 것 (2 단계) |
| `deneb_chat` · `deneb_retry` · `deneb_harvest` · `deneb_dividend_step` · `deneb_gold_dividend` | Deneb 금액 묻기 · 답 · 밤 매수 / 재시도 / 12 월 공제 실현 / 배당 단계 제안 / 금 섞기 · 배당 둘째 달 (`tests/golden/test_deneb_golden.py`, 3 단계) |
| `vega_more` · `toss_kr` · `deposit_split` · `liquidate` | Vega 정지 30 일 · 정지 풀기 · 주간 요약 · 현황 / 국내 적립 실행기 / 입금 나누기 글 / 전부 정리 (`tests/golden/test_vega_kr_golden.py` · `test_liquidate_golden.py`, 4 단계) |
| `telegram_acc` · `telegram_liquidation` · `live_home` · `settings_page` | 텔레그램 적립 대화 · 정리 · 정지풀기 대화 / 현황 글 · 실거래 홈 HTML / 설정 · 정리 칸 HTML (`tests/golden/test_bot_ui_golden.py`, 5 단계) |

작업 브랜치(worktree)에서 돌릴 때는 그 브랜치 코드를 읽도록 `PYTHONPATH=<브랜치>/src;<브랜치>` 를 꼭 준다
(가상환경이 원래 폴더 `src` 에 연결돼 있어, 안 주면 원래 코드를 시험하게 된다).
