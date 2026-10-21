# 실거래 코드 지도 (리팩토링 7 단계까지, 2026-10-21)

> 처음 보는 사람이 "무엇이 · 언제 · 무엇을 읽고 쓰는지" 찾는 지도 ([REFACTOR_PLAN.md](REFACTOR_PLAN.md)).
> 모르는 말은 **6. 용어 사전**, 코드 짜임새는 **7. 디자인 패턴**, 예전 이름으로 찾을 때는 **8. 바뀐 이름**.
>
> 파일을 열면 맨 위에 **언제 실행 · 읽고 쓰는 것**, 함수마다 바로 아래에 `[쓰임]`(하는 일) · `[이유]`
> (왜 이렇게 — 결정 날짜 · 사용자 말)가 있다. VS Code 에서 함수 이름에 마우스를 올리면 보인다.

## 1. 한눈에 — 실제 돈이 움직이는 것

| 전략 | 증권사 · 거래소 | 언제 도나 (예약 작업) | 실행 파일 | 계산만 하는 파일 | 장부 |
|---|---|---|---|---|---|
| 🌟 **Deneb** — TQQQ + SCHD (+ 금 IAUM) 매달 적립, 하락 방어 | 토스 (미국) | 평일 21:00 `Toss_Accumulate` · 재시도 23:40 · 03:30 `Deneb_Retry` · 금액 묻기 09:00 `Deneb_Chat` | `scripts/toss_accumulate.py` | `ops/deneb_live.py` (월 계획 `plan_monthly_rebalance` · 다음 날 채우기 `plan_next_day_fill` · 배당) · `ops/deneb_guard.py` (하락 방어 · 배당 단계 · 제안) | `data/toss/DENEB.json` |
| 💰 **자동 적립 (미국)** — 설정에서 고른 종목(지금 BTQ 하루 $3) 매일 적립, 모인 돈으로 살 수 있는 만큼 정수 주 (2026-10-21 부터, 그 전엔 하루 1 주) | 토스 (미국) | 평일 21:00 `Toss_Accumulate` · 환전 알림 09 · 12 · 15 시 `Toss_FX` | `scripts/toss_accumulate.py` | `ops/qndx_alert.py` (적립 판단) · `ops/qs_live.py` | `data/toss/<기호>.json` |
| 🇰🇷 **자동 적립 (국내)** — 미국과 같은 셈, 평일 낮 당일 지정가 | 토스 (국내) | 평일 12:10 `Toss_KR` | `scripts/toss_kr.py` | `ops/kr_accumulate.py` | `data/toss/KR_<코드>.json` |
| 🌌 **Vega** — BTC · ETH · SOL 원화, 추세 따라 비중 | 업비트 | 매일 09:05 `Vega` · 토 09:30 `Vega_Weekly` | `scripts/vega_upbit.py` | `ops/t2_upbit.py` | `data/upbit_t2/state.json` |
| 💹 **개별주** — 비중 추천 · 정규장 시작 직후 지정가 매수 · −3% 손절 · PEAD 소액 | 토스 (미국 · 국내) | 텔레그램 봇 안에서 2 분마다 `tick` | `scripts/size_guard.py` (봇이 부름 — ① 보호 매도 ② 매수 직전 다시 보기 → 매수 → 추가 매수 합치기) | `ops/position_size.py` (비중 · 수량 · 막는 이유) · `ops/size_order.py` (예약 → 매수 단계) · `ops/stop_guard.py` (손절 · PEAD 보유 끝 → 매도) | `data/size_guard/state.json` · 추천 기록 `journal.jsonl` · PEAD 알림 `pead_offers.json` |

대기 · 꺼짐 (이번 리팩토링 범위 밖, 제자리): 🧭 Polaris — 실거래 꺼짐(설정 `polaris.on`), **Paper 만** `scripts/polaris_paper.py` (예약 `Polaris` 아침 실행, 장부 `data/polaris_paper/state.json`, 2026-10-20) · ✨ Sirius(꺼짐).
은퇴 (`retired/`, 실행하지 않음): 바이낸스 Vega(`vega_live.py` · `live_t2_binance.py`) · ☄️ Altair(`altair_live.py` · `altair_demo.py` · `ops/altair_split.py`) — 2026-10-19 Altair 정리 · Vega 업비트로 되돌림 · 🗓 옛 주간 요약(`weekly_summary.py`, 2026-10-21). 자세한 것은 [retired/README.md](../retired/README.md).

## 2. 함께 쓰는 부품

| 하는 일 | 파일 | 읽고 쓰는 곳 |
|---|---|---|
| 토스 주문 · 조회 | `exchange/toss.py` | 토스 Open API, 토큰 `data/toss_token.json` |
| 업비트 주문 · 조회 | `exchange/upbit.py` | 업비트 API |
| 긴급 정지 (파일이 있으면 새 주문 멈춤) | `ops/kill_switch.py` | `data/HALT`(전체) · `data/HALT_<그룹>`(vega · deneb · acc · stock · sirius — 옛 `HALT_altair` 는 `data/archive/retired-2026-10-21/` 로 옮김) |
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
| 대시보드 서버 — 127.0.0.1 · Tailscale, 읽기 전용 + 예외 몇 개(설정 저장 · 정지 · 정리 띄우기 · 놓친 작업) | `ui/server.py` (`scripts/dashboard.py` 가 띄움) | 로그인하면 켜져 계속 `Dashboard` |
| 실거래 하루 평가 기록 (그래프용) | `scripts/live_snapshot.py` | 23:00 `Ledger_Sync` 가 먼저 부름 → `data/live/history.jsonl` |
| 파이어 진행 보고 | `scripts/fire_report.py` · `ops/fire.py` | 매달 1 일 09:15 `Fire_Report` |
| 보유 종목 위험 뉴스 | `scripts/risk_news.py` | 매시 17 분 `Risk_News` |
| 실거래 기록 DB 맞추기 (JSON → SQLite → Supabase) | `scripts/ledger_sync.py` · `ops/live_ledger.py` | 23:00 `Ledger_Sync`, DB `data/live_ledger.db` |

## 4. 하루 흐름 (한국 시간)

| 시각 | 일 |
|---|---|
| 07:30 | 미장 주도주 추천 확인 `Stock_Reco` |
| 09:00 | Deneb 이번 달 금액 묻기 (매달 첫 거래일 전) · 환전 알림 |
| **09:05** | **Vega 하루 판단 · 주문 (업비트)** |
| 12:10 | 국내 자동 적립 |
| **21:00** | **토스: 지난 주문 체결 확인 → ① 미국 자동 적립 → ② QS(실거래 꺼짐) → ③ Deneb 하락 방어 → 월 계획 또는 다음 날 채우기 (모두 장 마감 지정가). 알림 글은 Deneb 이 맨 위** |
| 23:40 · 03:30 | Deneb 월 계획 재시도 (달러 부족 · 장 시간 문제였을 때만) |
| 2 분마다 | 텔레그램 봇 (명령 · 버튼 → 정리 이어 하기 · 개별주 손절 감시 · 이벤트 알림 · 안전 점검(09:20 뒤 하루 한 번) 등) |
| 23:00 | 실거래 기록 DB 맞추기 `Ledger_Sync` |
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
| `stock_position_size` · `stock_size_order` · `stock_stop_guard` · `stock_size_guard` · `stock_snapshot_fire` | 💹 비중 추천 15 경우 / 예약 매수 단계 / 손절 감시 / 개별주 실행기 흐름(예약 → 매수 → 추가 매수 합치기 → 손절 → 끝, 앱 종목 손절, PEAD) / 매일 기록 · 파이어 보고 (`tests/golden/test_stock_golden.py`, 7 단계) |

작업 브랜치(worktree)에서 돌릴 때는 그 브랜치 코드를 읽도록 `PYTHONPATH=<브랜치>/src;<브랜치>` 를 꼭 준다
(가상환경이 원래 폴더 `src` 에 연결돼 있어, 안 주면 원래 코드를 시험하게 된다).

## 6. 용어 사전

| 말 | 뜻 (쉬운 말) | 어디서 |
|---|---|---|
| **장부** | 전략마다 따로 두는 JSON 파일. 그 전략이 산 몫 · 넣은 돈 · 낸 주문만 적는다(계좌 전체가 아님 — 사용자가 직접 산 같은 종목과 섞지 않으려고). | `data/toss/*.json` · `data/upbit_t2/state.json` |
| **LOC (장 마감 지정가)** | "오늘 종가가 이 값 이하면 종가에 산다" 주문. 장중 흔들림에 안 휘둘리고 종가 한 번에 체결. 토스 미국 주문의 기본(`LIMIT` + `CLS`). | Deneb · 미국 자동 적립 (`exchange/toss.py`) |
| **DAY 지정가 (당일 지정가)** | 오늘 장중에 이 값 이하면 산다. 장이 끝나면 남은 것은 저절로 취소. 토스 국내는 LOC 가 없어 이걸 쓴다. | 국내 적립 · 전부 정리의 토스 매도 |
| **IOC** | "지금 바로 체결되는 만큼만, 나머지는 즉시 취소" 지정가. 걸려 있는 주문이 남지 않는다. | Vega(업비트) · 전부 정리(업비트) |
| **시장가** | 값을 정하지 않고 지금 가격에 사고판다. 2026-10-18 '시장가 금지' 규칙이 지워졌지만 기존 실행기는 지정가 그대로. | Sirius 소수점 주문만 |
| **지정가 여유** | 지정가를 지금 가격보다 조금 높게 잡아 체결되게 하는 몫. 미국 적립 +3%, 국내 적립 +0.5%(호가 단위로 올림). 실제 체결은 시장 가격. | `ops/qndx_alert.LIMIT_BUFFER` · `ops/kr_accumulate.BUFFER` |
| **적립금** | 시작일부터 달력으로 매일 '하루 금액' 이 쌓인 돈 − 이미 산 원가. 이만큼으로 **살 수 있는 만큼(정수 주)** 산다, 남는 돈은 다음 날로(2026-10-21). 하루 금액을 바꾸면 그날부터 새 금액. 월 한도 · 한 건 50 주 안에서. | `ops/qndx_alert.advise` · `ops/kr_accumulate.advise` |
| **기준 수량** | 적립을 시작할 때 이미 갖고 있던 수량 — 적립 몫에 넣지 않는다. | 적립 장부 `baseline_qty` |
| **평균가 · 원가** | 산 값의 평균(수수료 포함). 일부 팔면 판 몫의 원가를 이동평균으로 뺀다. Vega 화면은 업비트 앱 평균가를 그대로 쓴다(현황 동기화). | `ui/live_home.py` · `ui/live_sync.py` |
| **전략 현금** | Deneb 장부의 달러 현금 — 계좌 전체 달러가 아니다. | `DENEB.json` `cash_usd` |
| **σ20 (QQQ 20 일 변동성)** | 나스닥 100(QQQ)이 최근 20 거래일 얼마나 흔들렸나. TQQQ 몫 = 60% × min(1, 0.20 ÷ σ) — 많이 흔들리면 TQQQ 를 덜 산다. | `ops/deneb_live.plan_monthly_rebalance` |
| **다음 날 채우기** | 매달 계획 다음 거래일부터, 남은 전략 현금으로 목표보다 가장 모자란 종목을 1 주씩 산다(놀고 있는 현금 줄이기, 2026-10-18). | `ops/deneb_live.plan_next_day_fill` |
| **브레이크 (T 브레이크)** | Deneb 하락 방어. TQQQ 가 자기 고점(T)보다 −15% 면 절반, −30% 면 남은 전부를 판다. 판 돈은 **보관금**으로 따로 두고, QQQ 추세가 다시 켜진 날 그 돈으로 되산다. | `ops/deneb_guard.py` |
| **보관금** | 브레이크로 판 돈. 매달 비율 계산에서 빼고 되사기에만 쓴다. | `DENEB.json` `brake.reserve_usd` |
| **금 섞기** | 설정 `deneb.gold_on` 이 켜지면 TQQQ 를 뺀 나머지를 SCHD : 금(IAUM) = 1 : 1 로 (2026-10-19). | `ops/deneb_live.py` |
| **배당 단계 · 12 월 공제 실현** | SCHD 세후 배당이 월 10 · 30 · 50 · 100 만 원을 넘을 때마다 TQQQ 목표를 10%p 낮출지 **묻기** / 12 월에 양도 공제(250 만 원) 안에서 이익 종목을 팔고 다음 날 되살지 **묻기**. | `ops/deneb_guard.py` |
| **추세 (T/v2)** | Vega 규칙. 20 · 60 · 120 일 수익률 부호 평균으로 오르는 코인을, 60 일 변동성 15% 목표로(덜 흔들릴수록 많이), 종목당 최대 50%, 목표와 5%p 넘게 차이 날 때만 사고판다. | `ops/t2_upbit.py` |
| **NAV (단위 가치) · 낙폭** | 넣은 돈을 '단위' 로 바꿔 입출금 영향 없이 수익률만 보는 값 / 고점보다 얼마나 내려왔나. Vega 는 낙폭 30% 면 새 매수 멈춤(사람이 풀 때까지). | `ops/t2_upbit.py` · `ops/deneb_guard.py` |
| **정지 파일 (HALT)** | 파일이 있으면 새 주문을 멈춘다. `data/HALT` = 전체, `data/HALT_<그룹>` = 그 전략만. 텔레그램 '정지풀기' · 설정 화면으로 지운다. | `ops/kill_switch.py` |
| **전부 정리** | 고른 그룹의 보유를 지정가로 파는 비상 버튼. 먼저 정지 파일로 새 매수를 막고 → 토스 미체결 매수 취소 → 판다. | `ops/liquidate.py` · `scripts/liquidate.py` |
| **현황 동기화** | '현황' 을 보일 때 장부를 그 순간 실제 계좌로 맞춘 **사본**을 쓴다(장부 파일은 안 고침). 체결 · 입출금이 다음 매매 실행 전에도 보이게(2026-10-20). | `ui/live_sync.py` · `app/live_accounts.py` |
| **Paper** | 가짜 돈으로 같은 규칙을 돌려 기록만 하는 모의 실행. 주문 없음. | `scripts/*_paper.py` · `ops/paper_summary.py` |
| **예약 작업** | Windows 작업 스케줄러에 등록한 정해진 시각 실행(`AutoTrading_*`). 켜고 끄기는 설정 화면 · `ops/task_setup.py`. | `ops/task_setup.py` |
| **전후 비교 기준 (골든)** | 리팩토링 전 코드로 찍어 둔 출력. 고친 뒤 글자까지 같아야 '동작이 안 바뀜'. 동작을 일부러 바꾼 커밋만 바뀐 줄을 확인하고 다시 찍는다. | `tests/golden/` |

## 7. 디자인 패턴

> 패턴 = 자주 쓰는 코드 짜임새에 붙인 이름. 이 저장소는 초보자가 따라가기 쉬운 것만 썼다.

| 패턴 | 쉬운 말 | 쓴 곳 | 왜 |
|---|---|---|---|
| **Repository** | 저장 방법을 한 곳에 모은다 — 다른 코드는 "읽어 줘 · 써 줘" 만 부른다. | `ops/ledger_file.py` (`read_json_or_none` · `write_json_atomic`: 임시 파일에 쓰고 바꿔 끼움) · `ops/liquidate.Store` (정리 작업 기록) | 쓰다 끊겨도 장부가 반쯤 깨지지 않게, 형식을 한 곳에서 지키려고 |
| **Facade** | 여러 단계를 함수 하나 뒤에 숨긴다. | `ui/live_sync.load` (업비트 · 토스 조회 → 장부 사본 맞추기 → 60 초 저장) | 봇 · 화면 · 매일 기록이 같은 방법으로 '지금 현황' 을 얻게 |
| **의존성 주입** | 필요한 기능을 안에서 직접 만들지 않고 밖에서 넣어 받는다. | `app/live_accounts.install()` → `ui/live_sync` · `scripts/liquidate.py` → `ops/liquidate.py` (`UpbitLike` · `TossLike` 같은 '창구 모양' `Protocol`) | 화면 · 계산 코드가 거래소를 직접 부르지 않게(import 계약), 시험에서 가짜 거래소를 넣으려고 |
| **Adapter** | 바깥 API 의 JSON 을 우리 코드가 쓰는 모양으로 바꿔 준다. | `exchange/toss.py` (`TossPosition` 등) · `exchange/upbit.py` (`UpbitOrder` · `Candle`) | 증권사 응답이 바뀌어도 고칠 곳을 한 파일로 |
| **Command + 목록표** | "이런 명령이면 이 함수" 를 표 하나로. | `scripts/telegram_bot.py` (`COMMAND_KINDS` · `_sort_commands` · `BACKGROUND_JOBS`) | 새 명령 · 부가 작업을 넣을 때 표에 한 줄만 더하면 되게 |
| **Golden Master 시험** | 바꾸기 전 출력을 찍어 두고 바꾼 뒤와 글자까지 비교. | `tests/golden/` (24 묶음) | 리팩토링이 동작을 안 바꿨다는 증거 |
| ~~Template Method~~ | (안 씀) 부모 클래스가 순서를 정하고 자식이 칸을 채우는 상속 구조. | — | 상속은 흐름 따라가기 어려움 → 긴 함수는 `# 1 단계: …` 주석으로 대신 |

import 계약(`lint-imports`, `pyproject.toml`, 5 개 모두 지킴): ① 순수 계산(core)은 I/O 계층을 import 하지 않는다
② **화면(ui)은 매매 로직을 import 하지 않는다** ③ 순수 계산은 설정 로더를 import 하지 않는다 ④ core 는 최하위 계층이다
⑤ 독립 연구는 매매 경로에서 import 하지 않는다.

## 8. 리팩토링으로 바뀐 이름

파일 밖에 저장되는 이름(장부 키 · 설정 키 · 예약 작업 · 실행 명령 · 주문 번호 앞머리 · 정지 파일)은 **하나도 안 바꿨다**.

| 파일 | 예전 | 지금 |
|---|---|---|
| `ops/deneb_live.py` | `LEV` · `DIV` · `GOLD` | `LEVERAGED_ETF` · `DIVIDEND_ETF` · `GOLD_ETF` |
| `ops/deneb_live.py` | `choose` · `fill` | `plan_monthly_rebalance` · `plan_next_day_fill` |
| `scripts/toss_accumulate.py` | `_pref` · `_since` · `_brake_of` · `_props` / `_put` | `_setting` · `_days_between` · `_load_brake` · `_load_proposals` / `_store_proposals` |
| `scripts/vega_upbit.py` | `_fmt_w` · `_sig_row` · `_prev_score` | `_weights_text` · `_signal_record` · `_previous_score` |
| `ops/kr_accumulate.py` | `_norm` · `_prev` | `_normalize` · `_previous_day` |
| `exchange/upbit.py` | `_ak` · `_sk` | `_access_key` · `_secret_key` |
| `ui/live_sync.py` | `Sync` · `apply_vega` · `apply_deneb` · `apply_plan` · `sent_ids` · `CACHE_S` · `_f` | `LiveAccountSnapshot` · `sync_vega_ledger_to_upbit` · `sync_deneb_ledger_to_toss` · `sync_accumulate_ledger_to_toss` · `pending_toss_order_ids` · `CACHE_SECONDS` · `to_float` |
| `ui/live_home.py` | `_f` · 함수 안 `d` · `o` · `q` · `b` · `h` | `_to_float` · `row` · `order` · `quotes` · `book` · `holding` |
| `scripts/telegram_bot.py` | `import … as sg` · `as liq` | `size_guard` · `liquidate` (별칭 없음) |
| `ops/size_order.py` · `ops/stop_guard.py` · `ops/position_size.py` | `_f` · 함수 안 `t` · `a` · `o` · `inp` | `_to_float` · `ticket` · `advice` · `order` · `inputs` |
| `scripts/size_guard.py` | `tick` 한 덩어리 (들여쓰기 7 단) | 3 단계 주석 + `_recheck_before_buy`(매수 직전 다시 보기) · `_opening_price`(기준 시가) |
| 새 파일 | — | `ops/ledger_file.py` (2 단계) · `app/live_accounts.py` (5 단계) |
