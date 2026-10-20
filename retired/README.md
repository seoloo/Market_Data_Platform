# 은퇴한 코드 (실행하지 않음)

> 지금은 쓰지 않지만 기록으로 남긴 코드. **지우지 않고 따로 뺐다** (사용자 2026-10-20 리팩토링 계획 —
> "삭제 대신 안 쓰는 코드 명시 + 따로 빼기"). 시험 · ruff · mypy 는 이 폴더를 보지 않는다.
>
> **되살릴 때는 다시 검증부터** — 은퇴 뒤 다른 코드가 바뀌었으니 그대로 옮겨 놓으면 안 돌 수 있다.

## 무엇이 · 왜

| 파일 | 무엇이었나 | 은퇴 | 왜 | 대신하는 것 | 마지막으로 바뀐 커밋 | 남은 장부 |
|---|---|---|---|---|---|---|
| `scripts/vega_live.py` | 🌌 Vega 바이낸스판 — BTC · ETH · SOL (USDT), 하루 1 회, 유동형 이자 · Altair 로 입금 나누기 | 2026-10-19 | 사용자 "altair는 정리하고 vega를 업비트로 옮기도록 하자" | `scripts/vega_upbit.py` (업비트 원화) | `8b9a1e1` (10-18) | `data/vega/` |
| `scripts/live_t2_binance.py` | 위 파일의 옛 이름 껍데기 (2026-10-15 이름 변경 전 명령 호환) | 2026-10-19 | 위와 같음 | — | `d7a2a34` (10-15) | — |
| `scripts/altair_live.py` | ☄️ Altair 실거래 — 바이낸스 USDⓈ-M 선물(USDC), 롱 T/v2 × 4 + 숏 | 2026-10-19 | 사용자가 직접 정리(10-18 21:00 텔레그램 '정리 Altair 만') 후 "altair는 정리" | 없음 (입금은 Deneb 90 : Vega 10) | `d04e316` (10-18) | `data/altair_live/` |
| `scripts/altair_demo.py` | Altair 선물 데모(가짜 돈) — 실거래와 같은 주문 코드 | 2026-10-19 | 위와 같음 (설정 `demo.altair` 꺼져 있었음) | 없음 | `d04e316` (10-18) | `data/altair_demo/` |
| `ops/altair_split.py` | 바이낸스 돈을 Vega(현물) · Altair(선물) 목표 비율로 나누는 계산 | 2026-10-19 | Altair 정리로 나눌 곳이 없어짐 | `ops/deposit_split.py` (Deneb · Vega 안내) | `1578d57` (10-20) | — |
| `scripts/weekly_summary.py` · `ops/weekly_summary.py` · `tests/test_weekly_summary.py` | 🗓 토요일 주간 요약 — 바이낸스 Vega(`data/vega/`) · ☄️ Altair 선물 · 🧭 Polaris 실거래 | 2026-10-21 | 세 곳 모두 은퇴 · 꺼짐이라 통째로 쓸모없음 (예약 `AutoTrading_Weekly_Summary` 10-20 꺼 둠), 사용자 "통째로 옮겨줘" | `scripts/vega_upbit.py` 의 Vega 주간 요약 (예약 `AutoTrading_Vega_Weekly`) | `413bb0e` (10-20) · ops `89ab3be` (10-17) | 보낸 기록 `reports/weekly_summary/` |
| `tests/test_*.py` | 위 파일들의 시험 (`test_vega_live` · `test_altair_demo` · `test_altair_live` · `test_altair_maker` · `test_altair_split`) | 2026-10-20 | 같이 옮김 | — | — | — |

## 옮기면서 떼어 낸 것 (지금 코드가 깨지지 않게)

- `scripts/telegram_bot.py` — 바이낸스 입금 확인(`_deposit_watch`, 10-19 부터 호출 꺼짐)과 Altair 데모 호출
  (`_altair_demo`, 설정 꺼짐)을 지움. 둘 다 이미 아무 일도 하지 않던 코드.
- `scripts/weekly_summary.py` — `altair_live._client` 대신 같은 일을 하는 접속 코드를 자체로.
- `scripts/shadow_272.py` — `altair_demo.long_targets` 를 `_long_targets` 로 그대로 옮김 (계산 같음).
- `tests/ops/test_deneb_gold.py` — `altair_split` 를 쓰던 시험 하나 뺌.

예약 작업 `AutoTrading_Altair` · `AutoTrading_Binance_Cruise` · `AutoTrading_Weekly_Summary` 는 꺼져 있다. 누가 다시 켜도 파일이 없어 바로 멈춘다.
