# 실거래 코드 지도 (초안 — 리팩토링 0 단계, 2026-10-20)

> 처음 보는 사람이 "무엇이 · 언제 · 무엇을 읽고 쓰는지" 찾는 지도. 리팩토링 단계마다 고쳐 쓴다
> ([REFACTOR_PLAN.md](REFACTOR_PLAN.md)). 6 단계에서 용어 사전 · 디자인 패턴 표를 붙여 완성한다.

## 1. 한눈에 — 실제 돈이 움직이는 것

| 전략 | 증권사 · 거래소 | 언제 도나 (예약 작업) | 실행 파일 | 계산만 하는 파일 | 장부 |
|---|---|---|---|---|---|
| 🌟 **Deneb** — TQQQ + SCHD 매달 적립, 하락 방어 | 토스 (미국) | 평일 21:00 `Toss_Accumulate` · 재시도 23:40 · 03:30 `Deneb_Retry` · 금액 묻기 09:00 `Deneb_Chat` | `scripts/toss_accumulate.py` | `ops/deneb_live.py` (월 계획 · 다음 날 채우기 · 배당) · `ops/deneb_guard.py` (하락 방어 · 배당 단계 · 제안) | `data/toss/DENEB.json` |
| 💰 **자동 적립 (미국)** — BTQ · WQTM · QS 매일 소액, 1 주 값이 모이면 1 주 | 토스 (미국) | 평일 21:00 `Toss_Accumulate` · 환전 알림 09 · 12 · 15 시 `Toss_FX` | `scripts/toss_accumulate.py` | `ops/qndx_alert.py` (적립 판단) · `ops/qs_live.py` | `data/toss/<기호>.json` |
| 🇰🇷 **자동 적립 (국내)** | 토스 (국내) | 평일 12:10 `Toss_KR` | `scripts/toss_kr.py` | `ops/kr_accumulate.py` | `data/toss/KR_<코드>.json` |
| 🌌 **Vega** — BTC · ETH · SOL 원화, 추세 따라 비중 | 업비트 | 매일 09:05 `Vega` · 토 09:30 `Vega_Weekly` | `scripts/vega_upbit.py` | `ops/t2_upbit.py` | `data/upbit_t2/state.json` |
| 💹 **개별주** — 비중 추천 · −3% 손절 · PEAD 소액 | 토스 (미국) | 텔레그램 봇 안에서 2 분마다 | `scripts/size_guard.py` (봇이 부름) | `ops/size_order.py` · `ops/stop_guard.py` · `ops/position_size.py` | `data/size_guard/state.json` |

대기 · 꺼짐 (이번 리팩토링 범위 밖, 제자리): 🧭 Polaris(나무 · 11 월부터 `Polaris`) · ✨ Sirius(꺼짐).
은퇴 (`retired/`, 실행하지 않음): 바이낸스 Vega(`vega_live.py` · `live_t2_binance.py`) · ☄️ Altair(`altair_live.py` · `altair_demo.py` · `ops/altair_split.py`) — 2026-10-19 Altair 정리 · Vega 업비트로 되돌림. 자세한 것은 [retired/README.md](../retired/README.md).

## 2. 함께 쓰는 부품

| 하는 일 | 파일 | 읽고 쓰는 곳 |
|---|---|---|
| 토스 주문 · 조회 | `exchange/toss.py` | 토스 Open API, 토큰 `data/toss_token.json` |
| 업비트 주문 · 조회 | `exchange/upbit.py` | 업비트 API |
| 긴급 정지 (파일이 있으면 새 주문 멈춤) | `ops/kill_switch.py` | `data/HALT`(전체) · `data/HALT_<그룹>`(deneb · vega · acc …) |
| 전부 정리 (누를 때만) | `scripts/liquidate.py` · `ops/liquidate.py` · `ui/liquidate_panel.py` | `data/liquidate/` · 보고 `reports/liquidate/` |
| 사용자 설정 (오르빗 설정 화면 · 텔레그램 '적립') | `ops/user_settings.py` · `ui/settings_page.py` | `data/settings/settings.json` · 바꾼 기록 `changes.jsonl` |
| 예약 작업 켜기 · 끄기 | `ops/task_setup.py` | Windows 작업 스케줄러 |
| 매일 안전 점검 | `ops/safety_check.py` | (판단만) |
| 입금 나누기 안내 (Deneb · Vega 비율) | `ops/deposit_split.py` | 설정 값 |

## 3. 알림 · 화면

| 하는 일 | 파일 | 언제 |
|---|---|---|
| 텔레그램 봇 — 현황 · 적립 · 정리 · 개별주 버튼 · 입금 확인 | `scripts/telegram_bot.py` | 2 분마다 `Telegram_Bot` |
| 현황을 그 순간 실제 계좌로 맞춘 사본 | `ui/live_sync.py` | 봇 · 화면이 부름 |
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

작업 브랜치(worktree)에서 돌릴 때는 그 브랜치 코드를 읽도록 `PYTHONPATH=<브랜치>/src;<브랜치>` 를 꼭 준다
(가상환경이 원래 폴더 `src` 에 연결돼 있어, 안 주면 원래 코드를 시험하게 된다).
