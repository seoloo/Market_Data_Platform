# 작업 현황

최종 갱신: 2026-09-26 · 작성: Codex (ETH·SOL 스테이킹/헤지 Phase 0 무료 자료 감사).
현재: **Phase 6 — Paper T/v2·O/v1·O/v2·O/v3 관찰 중**. 신규 BTC 옵션 후보는 Phase 0 실행 안전성 게이트 미통과로 보류.

## 2026-09-26 — ETH·SOL 스테이킹 Phase 0 무료 이력 감사 (문서 90 §5.3)

- Binance 서명 GET으로 **WBETH 1,226일(2023-04-24~2026-08-31)**, **BNSOL 734일(2024-08-28~2026-08-31)** 환산비율 이력을 확보했다. 시작 이후 UTC 날짜 누락 0, 해시 검증. 이는 Binance 상품 자료이며 **업비트 실제 SOL 지급률이 아니다**. 사용자 전달 업비트 표시 4.52%는 현재 참고값으로만 유지한다.
- 두 자산 공통 기간(2024-08-28~2026-08-31)의 Binance 공개 funding/정산 mark를 각각 **2,202건** 확보했다. 로컬 DB와 정산 시각·비율 불일치 0, 공개 mark 결측 0. 운영 DB의 기존 `mark_price` 결측은 그대로 두었다.
- 정상화 이력과 수집 시각·SHA-256 manifest는 Git 제외 `data/research/staking/`에 보존. 연구용 감사 스크립트·단위 테스트만 추가했고 전략/Paper/스케줄/실계좌/사용자의 업비트 SOL에는 손대지 않았다.
- 검증: 전체 pytest **2,200건**, leakage **77건**, strict mypy **173파일**, ruff check/format **454파일**, import-linter **5계약** 통과. 스냅숏 SHA-256 재계산 일치, Git 제외 상태·변경 공백 확인.
- **Phase 0 유지·성과 미열람·사전등록 전.** 업비트 실제 보상, 상품 환매/할인, 장중 mark·증거금, KRW/USDT·프리미엄, 보상 시점/MEV·비용 자료가 남아 있다. 다음: 이 결측과 계좌별 위험·통계 단위 확인 후에만 사전등록 가능성 판단.

## 2026-09-26 — ETH·SOL 스테이킹/선물 헤지 타당성 조사 (문서 90)

- 사용자 선택: **ETH·SOL만**, 무료 자료로 스테이킹 단독 및 선물 숏 결합을 비교. [90](docs/research/90-staking-eth-sol-feasibility.md)에 공식 조건·회계·데이터 감사·후속 게이트를 정리했다. 기존 K·M의 funding 가설을 되살리지 않고 스테이킹 추가 기여를 분리한다.
- 업비트 SOL 표시율 **4.52%는 사용자 전달 현재값**. 공식 안내상 표시율은 수수료 차감 후여서 10%를 중복 차감하지 않는다. SOL 언스테이킹 예상 약 3일(지연 가능), 보상 재스테이킹, 외부 선물 증거금과 자금 분리·숏 stop 이후 잔여 롱 위험을 기록했다. 개인 보유량·계좌는 조회하지 않았다.
- DB 읽기 전용: ETH funding 7,125행/SOL 6,012행, 마지막 2026-08-31, `mark_price` 전부 결측. 스테이킹 보상 테이블 없음. 공개 funding·업비트 일봉 GET 각 자산 1건 성공; WBETH/BNSOL 과거 비율은 서명 USER_DATA 필요(무키 GET −2014). API 키를 읽거나 변경하지 않았다.
- 산술 예시: SOL 4.52%가 유지되고 코인 50/별도 현금 50이면 총자본에 대한 보상 기여는 **2.26%**(펀딩·거래비용 등 전). 실측 수익·예측·배분 권고 아님. ETH 상품별 실수령률과 양 자산 과거 보상은 미확인.
- **연구 Phase 0 유지, 사전등록·성과 판정 전**. 다음: 상품별 보상/환산비율·mark/환매/환율 자료 감사 → 계좌별 위험·통계 단위·80% 검정력 확인 → 사전등록. 일별 보상을 거래 수로 세지 않으며 기존 위험/Phase 게이트를 면제하지 않는다.
- 문서 전용 검증: 링크·보존·범위·공백·산술 검사. 코드 테스트 미실행. 운영 코드·설정·DB·Paper·사용자 스테이킹·실계좌·스케줄 변경 없음.

## 2026-09-26 — 변경된 KIS 계좌 읽기 점검 · 해외 주식 UI 분리

- 변경된 로컬 KIS 설정으로 **읽기 전용** 해외 잔고, QNDX·BTQ 시세·매수 가능 조회가 성공했다. 자동 매수 스위치는 꺼져 있다. 기존 두 적립 상태는 기준 수량 0·열린 주문 0으로 새 조회와 즉각적인 충돌이 없었다. 계좌번호·잔고·매수 가능 금액 등 개인 값은 추적 문서/커밋에 기록하지 않고 사용자에게만 보고한다. `AutoTrading_QNDX_Alert` 예약 작업은 Ready, 다음 실행 21:00 KST로 조회됐다.
- 대시보드 일반 자동매매 메뉴에서 적립 항목을 빼고 **별도 해외 주식** 구역(`/overseas`)으로 분리했다. 해당 화면은 QNDX·BTQ 각각을 고정 순서로 보여 주며 마지막 저장 보고서의 실제 기록 시각·보유 수량을 표시한다. `/accumulate`·`/qndx` 옛 링크는 같은 화면으로 연결한다. 화면은 계좌 실시간 조회·주문을 하지 않고 계좌 전환 전 보고서가 남아 있을 수 있음을 명시한다.
- 주문 경로·스케줄·`.env`·Paper·위험 규칙은 변경하지 않았다. 계좌 식별자를 적립 상태에 저장하지 않는 기존 구조는 향후 재차 계좌 변경 시 감지할 수 없는 한계로 남는다.
- 검증: 전체 pytest 2,192건, leakage 77건, UI·적립 관련 41건, strict mypy 173파일, 전체 ruff check/format, import-linter 5계약 통과. 옛 URL 호환·QNDX/BTQ 고정 표시·KST 시각·HTML 이스케이프를 테스트했고 실제 저장 보고서 렌더링, 변경 파일 공백·링크를 확인했다.

## 2026-09-26 — QNDX 적립 알림 (문서 89) · QQQ 매일 사기 vs RSI 40 참고 계산 (문서 88)

- 사용자: QQQ 매일 1만 원 vs RSI 40 까지 기다리기 → **문서 88 참고 계산(판정 아님)**: 매일 사기가 0.9% 더 남음, 5·10년 창에서 기다리기 우세 0개. 87 판정 불변.
- 사용자: QNDX 자동 1주 매수 요청 → 한국투자증권 예약 매수 코드를 더하는 단계에서 **Claude Code 자동 권한 검사가 차단**(우회 안 함) → 사용자 선택 **"3번으로 알림만 · UI 추가"**.
- 구현: `KisOverseasReadOnly.quote_detail`(현재가상세, 원화 환산·당일 환율) · `ops/qndx_alert.py`(매일 1만 원 적립·잔고로 매수 확인·월 30만 원 한도) · `scripts/qndx_alert.py`(21:00, 텔레그램 하루 1회) · 대시보드 `/qndx` · 실행 현황 한 줄. 테스트 5개.
- 첫 실행(텔레그램 없이): 적립 10,000원 / 1주 34,570원(25.22 USD × 1,367.30원) → 약 3일 뒤. 적립 시작 2026-09-26.
- **사용자 할 일:** 작업 스케줄러에 매일 21:00 `python -m scripts.qndx_alert run` 등록.
- **자동 예약 매수 추가** (사용자 "권한 바꿨어 자동 매수 만들어줘"): `KisReservationBuyer`(예약 매수 1주 전용 `TTTT3014U`, 종목 QNDX·수량 1·지정가 ≤ 40 USD, 매도 없음, 네트워크 오류 재전송 없음) · 선기록·하루 1건·다음 날 잔고로 체결/만료 · 대시보드에 자동 매수 켜짐/꺼짐·예약 매수 열. 테스트 14개 추가. 실계좌 조회 시험(살 차례 아님 → 주문 없음). **켜기는 사용자가 `.env` `KIS_TRADING_ENABLED=true`** — 지금은 꺼짐. 첫 살 차례 9/29(화) 밤.
- 예약 작업 `AutoTrading_QNDX_Alert` 등록 확인(21:00, 준비됨 — "놓치면 켤 때 실행" 꺼짐·로그인 필요는 문서 84 §3 명령으로). 매수 가능 조회 **0주·0 USD** → 통합증거금 신청 또는 달러 환전 필요(도구는 환전하지 않는다).
- **BTQ 추가** (사용자 "하루에 1달러"): 종목별 계획으로 일반화(`PLANS` — BTQ 달러 적립·월 31달러·지정가 ≤ 6 USD), 상태·보고를 `data/accumulate/`·`reports/accumulate/<종목>/` 로(첫 판 자동 이동), 대시보드 `/accumulate`. BTQ 첫 살 차례 9/28(월) 밤.

## 2026-09-26 — RD 사전 등록: SPY·QQQ RSI ≤ 35 눌림목 적립 (문서 87)

- 사용자: "rsi가 35이하일때 매수하고 계속 보유 … 장투로".
- 팔지 않으면 낙폭은 같다. 그래서 질문을 **"매달 같은 돈을 넣을 때 눌림목을 기다리는 쪽(R)이 바로 사는 쪽(D)보다 더 남는가"** 로 고정했다.
- 규칙:
  - RSI 는 원 가격으로 계산하고, 평가는 배당 조정 가격으로 한다.
  - 수수료는 두 방식 모두 0.25%. 현금 이자는 0 이고, 국채 금리를 준 경우는 참고 줄로 본다.
- 판정: R1 전체 구간 R > D, R2 10년 창 99개 중 60% 이상. SPY·QQQ 둘 다 통과해야 PASS.
- 코드 `scripts/run_rd_dip.py`, 테스트 6개. 사전 등록 커밋 `4578c8f`.
- **판정 (사용자 "이대로 판정"): REJECT.** R/D SPY 0.971 · QQQ 0.979, 10년 창 99개 중 R 우세 0개. 기다리는 동안 놓친 상승(SPY 최장 538일 대기)이 눌림목 할인보다 컸다.

## 2026-09-26 — X 판정: T/v2 추세를 금·은·SPY·QQQ 로 (문서 86 §9)

- 순서: 감사·계산 정의 먼저 고정(`6c15309`) → KIS·FRED 적재·감사 A1~A3 PASS → 시뮬레이터 V0(기존 T/v2 와 자본 차이 0.0) → 준비 커밋(`77e4517`) → **1회 판정**.
- **Q1 재현 1/4:** QQQ 만 통과(샤프 0.68). GLD 0.29 · SLV 0.08 · SPY 0.50 은 넷 다 두 반쪽 양수지만 4개 동시 검정 경계 아래.
- **Q2 REJECT:** 6자산 샤프 1.11 < 1.20, MDD 30.8% > 15.9%. 상관은 낮았으나(−0.2~0.2) 종목별 변동성 목표 때문에 평균 노출이 0.82 vs 0.19 였다.
- KIS 일반 수수료(0.25%)면 더 나빠진다 → ETF 추세를 KIS 로 거래할 근거가 없다.
- 작업 중 실수: 새 시뮬레이터를 쓰다 기존 `backtest/multi_trend.py`(TA)를 덮어썼다가 git 에서 되돌렸다. 새 파일은 `xasset_trend.py`. 커밋에는 영향 없음.
- RESEARCH_HISTORY `X`, BRIEFING 반영.

## 2026-09-26 — 한국투자증권(KIS) 실전 읽기 전용 연동 (문서 86 §3.1·§6.2)

- 사용자: Binance 말고 한국투자증권 API 로 테스트하자. 붙여 넣은 요구: 인증·토큰 캐시·잔고·해외 시세·SPY/QQQ/GLD/SLV 조회·공통 인터페이스, **주문 절대 금지**.
- 코드 (기존 Binance 코드는 그대로 둔다):
  - `config/secrets.py` `load_kis_credentials`: 빠진 이름을 모두 알려 주고, 값은 숨긴다.
  - `exchange/overseas.py`: 해외주식 계약. 일봉은 기존 `closed_daily` 신호 계약과 같은 모양이다.
  - `exchange/kis.py`: 토큰 파일 캐시, 만료 시 1회 재발급, 초당 한도·네트워크 오류는 최대 3회 재시도. 시세·일봉·잔고 조회만 하고, `place_order` 는 항상 막는다.
  - `scripts/check_kis_account.py`, 테스트 22개.
- 실측 (읽기만):
  - 네 종목 현재가·일봉 정상.
  - **일봉은 2007-08-20 부터**(약 4,805봉).
  - 해외 잔고 조회 정상(보유 0).
  - 주문 차단 확인.
- 발견한 결함을 고쳤다: 쓸 수 없는 줄만 있는 일봉 페이지에서 과거 조회가 멈추는 문제 → 원시 날짜 기준 커서로 바꿨다.
- 문서 86:
  - 자료 공급처를 KIS 로 확정했다.
  - Q1 구간을 2008-06~2026-08 로 수정했다(자료 시작일 때문이며, 결과 보기 전이다).
  - 판정 전 자료 감사 A1~A3 을 넣었다. `MODP=0` SPY 2010-01-15 가 지수의 1/10 과 약 5% 달라서다.
  - KIS 경로의 비용(수수료·환전·양도세 22%)은 참고 줄로 보고한다.

## 2026-09-26 — X: T/v2 추세를 금·은·SPY·QQQ 로 (문서 86)

- 사용자가 GPT 와 정리한 연구 요청을 붙여 넣었다. 기각 목록과 겹치지 않는다(TA 는 알트 확장, P1 은 페어).
- **사전 등록 먼저 커밋**(`252945b`, 수익 미계산).
  - 규칙: T/v2 그대로, 봉은 거래일, 연율화 252.
  - Q1: 2007~2026-08 자산별로 평균 − 2.5 SE > 0, 두 반쪽 모두 양수.
  - Q2: 2018~2026-08 6자산 비중 합 ≤ 1 로 샤프·MDD·두 반쪽 모두 BTC·ETH T/v2 보다 나은가.
- **장기 자료 공급처 미정 → 판정 대기.**
  - Stooq 는 봇 차단이라 쓰지 않는다.
  - FRED 에는 금·은이 없다.
  - Tiingo 무료 계정을 권장한다(사용자가 만들고 `TIINGO_API_KEY`).
- Binance TradFi 무기한 실행 가능성 (판정과 별개): `scripts/measure_tradfi_perp.py`.
  - 금·은은 롱 funding 이 연 13%·21% 라 **C2 FAIL**.
  - SPY·QQQ 는 C2~C4 통과, C1(정규장 호가)은 스냅숏을 모으는 중.

## 2026-09-26 — 외부 AI 대화용 연구 요약

- 사용자: "gpt llm으로 대화를 할 수 있게 … 요약" → "저장소에 두고 계속 갱신해줘".
- [docs/BRIEFING_FOR_LLM.md](docs/BRIEFING_FOR_LLM.md): 목표·원칙, 통과 전략 수치, 기각 18개 한 줄씩, 기타 트랙, 운영·일정, 대화 주제. 키·잔고·개인 정보 없음.
- 파생 요약이라 기준 문서가 아니다. AGENTS §1 에 갱신 규칙 추가 — 판정·전략 상태·단계·일정이 바뀌면 같은 커밋에서 고친다.

## 2026-09-26 — 대시보드: 실행 현황 화면 · 더블클릭으로 열기

- 사용자: "UI를 … exe나 아니면 웹 호스트 들어 가면 볼 수 있게 … 지금 연구실행중인 것들 전부 볼 수 있게".
- 새 화면 **실행 현황**(`/running`): 지금 도는 11개 작업(Paper T/v2·O/v1·O/v2·O/v3, Phase 7 훈련, V4 선물 실행기 훈련, 그림자, 데모 T/v2·O/v2·Elliott, 일일 점검) — 마지막 기록·밀림·실패·끝남, PC 상태, 긴급 정지, 다가오는 일정.
- `scripts/open_dashboard.cmd`: 창 없이 띄우고 브라우저로 연다(이미 떠 있으면 브라우저만). 끄기 `stop_dashboard.cmd`. 주소 http://127.0.0.1:8787.
- 바탕화면 바로 가기·로그인 때 자동 시작은 사용자가 명령으로 만든다. 휴대폰·외부 접속은 외부 노출이라 결정 대기.

## 2026-09-26 — 실거래 1단계 출발 준비 (문서 85)

- 사용자: "①번 실행했어, 2순위 진행해줘". 84 §3 ① 적용 확인 — 작업 11개 모두 놓치면 켤 때 실행, Demo_EW 빼고 깨워서 실행. 로그인 조건(②)은 그대로.
- [85](docs/research/85-phase8-stage1-go-live-plan.md): 출발 조건 G1~G5 · 금액 제안 5,000 USDT(설계 10,000 의 절반) · 거래 키 만들기 순서 · 첫 주문 사람 확인 · 멈출 조건 · 긴급 정지 · 매일 대조 · 장애 대응.
  - ⚠ 실행기는 현물 지갑의 USDT+BTC+ETH 전체를 전략 자본으로 본다 → 현물 지갑에는 전략 금액만.
  - ⚠ TRADING_CORE 의 누적 DD 30% 정지는 현물 실행기에 아직 없다 → L3 때 넣을지 사용자 결정.
- Phase 7 판정 자동화: `python -m scripts.review_phase7 --until 2026-10-08` (`ops/phase7_review.py`, 26 §2 P1~P4). 원인 미기록 실패는 CHECK. 원인은 `docs/research/26-phase7-causes.json`.
- 실계좌 주문 어댑터(L3)는 만들지 않았다(승인 전).

## 2026-09-26 — 지금 PC 를 상시 실행 환경으로 (문서 84)

- 사용자 결정: 수익 전에는 구매하지 않고 **지금 PC 로 운영**. 1순위 과제를 그 조건으로 했다 → [84](docs/research/84-home-pc-always-on.md).
- 결론: 1단계(T/v2 현물)는 지금 PC 로 가능하다.
  - 하루 한 번, 놓치면 거래 안 함(fail-closed), 현물이라 청산이 없다
  - 선물은 거래소 스톱이 PC 없이도 작동한다
  - 남는 위험: 며칠 꺼지면 추세가 꺾여도 못 판다 → 알아차리기와 앱으로 직접 매도
- PC 읽기 점검:
  - ✅ 데스크톱·유선·절전 안 함·업데이트 재시작 01~07시
  - ⚠ **최근 7일 비정상 종료 4회**(사용자 확인 필요)
  - ⚠ 작업 11개 모두 로그인해야 실행, 5개는 놓치면 안 돎, 절전 중 깨우기 없음
- 코드:
  - `ops/pc_health.py` + 일일 점검에 **PC 상태 한 줄**: 작업 실패·26시간 밀림·24시간 비정상 종료는 경고
  - 그림자 운영이 −2015 면 "공인 IP 가 바뀌었을 수 있다"고 알린다. 테스트 +6
- **남은 일(사용자):** 84 §3 명령 ①(놓치면 켤 때 실행·깨우기), 선택 ②(로그인 없이 실행)·③(BIOS 정전 뒤 켜기). 비정상 종료 원인 확인.

## 2026-09-26 — 선물 롱만·배율 높이기: 후보 비교(81)와 H1 손실 크기 분석(82)

- 사용자 요청: 선물은 롱만, 배율을 높이는 방향으로 연구 → [81](docs/research/81-long-only-higher-leverage-candidates.md) 후보 비교.
  - H1 한도 상향 + 2배 이상 전향 관찰, H2 낙폭 연동 배율(권고), H3 선물만 롱(비권고)
  - 사용자 선택: **"H1 다시 연구해보자"**
- [82](docs/research/82-h1-raise-limits-leverage-analysis.md) — **판정·배율 선택용 아님.** 한도 결정을 위한 손실 크기다.
  - 정지를 끈 과거 경로의 봉 안 최악 낙폭(= 필요했던 정지선): 1.5배 23.7% · 2배 **31.1%** · 2.5배 39.8% · 3배 46.0%. 청산 0
  - 샤프 1.17 → 0.99. funding 이 배율보다 빨리 는다
  - 하루 급락 스트레스(최대 보유 날, 2배): −30% 에 −42%(스톱 작동) / −52%(스톱 실패)
  - 권고: H1 이면 **2배 · 정지 40% · 중단선 35%**, O/v3 Paper 를 그 한도로 계속 관찰. 보완 1순위는 H2
  - F3 을 한도 상향으로 통과시키는 것은 기준 변경이라 쓰지 않는다
- 코드: `scripts/analyze_h1_leverage.py`.
- **사용자 결정 B ("B로 하자", 10/23 결과 보기 전):** O/v3(2배) Paper 를 계좌 정지 40% · 중단선 35%(경고 30%)로 계속 관찰한다.
  - 새 설정 `O3_2X_LIMIT40` 을 Paper 에만 쓴다. `O3_2X` 는 과거 결과 재현용으로 그대로 둔다. 기존 행 재계산 일치
  - 일일 점검·10/24 채점에 원장별 중단선을 넣었다(문서 65 §9). 실거래 계좌 한도 30% 는 그대로
- **[83 H2 사전 등록](docs/research/83-h2-drawdown-linked-leverage-preregistration.md):**
  - 규칙: 고점 2.5배, 계좌 낙폭 10% 에서 현물만(직선). 그 밖은 O/v2 규칙
  - 기준(대조 O/v2): 정지 0 · 청산 0 · MDD ≤ 25% · CAGR > 31.7% · 두 절반 모두 > O/v2
  - `OverlayConfig.dd_throttle_zero`, 끄면 기존과 비트 단위 동일, O 판정 3개 재현. 테스트 +5
  - **판정 1회 (사용자 "판정해줘"): REJECT** ([83 §6](docs/research/83-h2-drawdown-linked-leverage-preregistration.md))
    - MDD 21.0% 로 한도 안이지만 **CAGR 23.4% < O/v2 31.7%**, 두 절반 모두 미달. 샤프 0.94
    - 원인은 구조다: 전체 날의 78%(1,845일)가 역대 고점보다 10% 이상 아래라 현물만 들었다. 사실상 T/v2 가 됐다
    - 참고: 2.5배를 늘 들면 2020-09-03 정지. 30% 한도 안에서 2.5배는 맞지 않는다
    - RESEARCH_HISTORY 에는 옛 H2(포지셔닝)와 겹치지 않게 **DDL** 로 적었다
  - 배율을 높이는 길은 결정 B(O/v3 2배, 한도 40/35% 관찰)만 남았다. 10/23·10/24 에 본다

## 2026-09-26 — BTC 옵션 보호 쌍의 청산 호가·수량 및 표시 비용

- [80](docs/research/80-btc-option-exit-quote-capacity.md): 73에서 고정한 16쌍·4개 무료 월초 날짜·각 10분을 그대로 재조회했다. 진입 bid/ask뿐 아니라 **조기 청산에 필요한 숏 ask·보호 롱 bid**와 네 최우선 표시 수량 ≥현재 최소 0.01 BTC를 동시에 요구했다. 각 쌍의 1초·5초 유효 시간은 73의 양방향 양수 호가 수치와 같아, 이 소표본에서는 0.01 BTC 표시 수량이 추가 병목으로 관측되지 않았다.
- 5초 유효 시간의 가상 **네 번 호가 교차 스프레드**는 0.01 BTC 기준 시간 가중 평균 0.13–3.50 USDT/쌍. 이는 프리미엄·전략 손익·실제 체결 비용이 아니며 무료 4일×10분을 연중 성과로 확장하지 않는다. 2023년 가까운 콜의 5초 유효 시간은 37/600초. 계좌 읽기 `-2015`, 원자적 청산·거래소 측 보호 미입증은 그대로다.
- 판정 `FOUR_SIDE_DISPLAY_QTY_OBSERVED / EARLY_EXIT_EXECUTION_NOT_PROVEN / ACCOUNT_RISK_NOT_CLEARED / NO_TRADING_APPLICATION`. 같은 무료 표본의 세분화는 핵심 안전 게이트를 해결하지 못해 옵션 트랙 Phase 0 보류. 계정/주문/운영·Paper 변경 및 유료 구매 없음. 재현 스크립트·경계 테스트를 추가했다. 다음 작업은 안전한 시험 계정·청산 계약 검증의 별도 승인 또는 다른 사용자 선택 연구 트랙이며 Paper 10/23·10/24 점검 일정 불변.
- 검증: 전체 pytest 2,093건과 leakage 표식 77건, 신규 경계 테스트 3건, strict mypy(현행 src 및 신규 스크립트), 전체 ruff check/format, import-linter 5계약 통과. 새 문서 로컬 링크·공백·변경 범위 확인.

## 2026-09-26 — BTC 옵션 계정 읽기 전용 접근·공개 최소 담보 점검

- [79](docs/research/79-btc-option-account-readonly-gate.md): 기존 키가 읽기 전용·IP 제한이며 거래/출금 권한이 꺼진 것을 확인하고 공식 옵션 `marginAccount` GET을 1회 시도했다. HTTP 401 / `-2015`로 거부되어 계정 equity·가용잔고·매도 모드는 **미확인**이다. 비공개 옵션 조회를 반복하거나 키 권한을 확대하지 않았다.
- 공개 `exchangeInfo`·BTC 지수에서 당시 거래 중인 668계약의 최소 기초수량 0.01 BTC와 10% 증거금식의 **하한**을 계산했다: 센트 단위 올림 시 숏 포지션 초기증거금 ≥84.06 USDT, 현행 10%/1.5배 게이트의 최소 equity ≥840.51·available ≥126.08 USDT. 마크·보호 롱·수수료·기존 노출 제외값으로 실제 계정 적격 판정은 아니다. 판정 `ACCOUNT_READ_DENIED_-2015 / PUBLIC_MARGIN_FLOOR_ONLY / SHORT_ELIGIBILITY_UNVERIFIED / NO_TRADING_APPLICATION`. 주문·이체·설정·코드/Paper 변경 없음. 10/23·10/24 일정 불변.

## 2026-09-26 — BTC 옵션 보호 롱 오프라인 필요조건 검증

- [78](docs/research/78-btc-option-long-first-offline-check.md): 사용자 요청에 따라 보호 롱의 **검증 후 조건부 적용** 순서를 A(계약·수량) → B/C(계좌 손실·청산) → D(비용 후 경제성)로 고정했다. 연구 전용 순수 검사기와 경계·`UNKNOWN`·부분 체결·속성 테스트로 A의 필요조건을 확인했다. 운영 주문 경로에는 연결하지 않았다.
- 가상 수치 반례에서 초기 만기 손실이 equity 1% 이내여도 조기 청산 손실은 초과할 수 있음을 계산했다. 실제 계정·호가·거래 성과가 아닌 산술이다. 판정 `A_OFFLINE_INVARIANT_TESTED / ACCOUNT_PATH_UNVERIFIED / TIMED_EXIT_UNVERIFIED / ECONOMICS_UNTESTED / NO_TRADING_APPLICATION`. B/C의 공식·계정 근거와 장애 시험이 없으면 옵션 매도 적용 불가, 경제성까지 미달이면 후보 제외. 계정 인증·주문·유료 구매·기존 Paper/위험 정책 변경 없음. 10/23·10/24 일정 불변.
- 검증: 전체 pytest 2,090건, leakage 77건, 신규 연구 테스트 10건(마지막 속성 검사 강화 후 재실행), strict mypy 321파일, ruff check/format, import-linter 5계약 통과. 문서 상대 링크·공백·변경 범위 확인.

## 2026-09-26 — BTC 옵션 RFQ 자동화·다리 체결 계약 공개 감사

- [77](docs/research/77-btc-option-rfq-api-and-leg-contract.md): Binance 공식 RFQ FAQ·출시 공지·Options 개발자 명세를 채널별로 대조했다. RFQ 플랫폼의 다중 다리 **단일 주문 구성은 확인**했지만, 자동 제출용 공개 RFQ API와 두 다리 all-or-none/부분 체결·실패 계약은 확인하지 못했다. 개발자 명세의 별도 `block/order/create`는 `legs` 필드가 있어도 **최대 1다리**이고, 공개 주문장 `batchOrders`는 매칭 순서 미보장이다. 공지 본문과 말미 위험 고지에도 다중 다리/한 계약 표현의 불일치가 남아 있다.
- 판정 `RFQ_MULTI_LEG_UI_CONFIRMED / PUBLIC_MULTI_LEG_API_NOT_DOCUMENTED / ATOMIC_FILL_CONTRACT_UNVERIFIED / ACCOUNT_ELIGIBILITY_UNKNOWN / LIVE_BLOCKED`. 문서 부재를 기능의 절대 부재로 단정하지 않는다. RFQ 자동화 우회 구현·실거래·계정 인증·주문·유료 구매·코드/Paper 변경 없음. 다음 Phase 0: 보호 롱을 별도 보호 수단으로 인정할지 사용자 명시 결정이 필요하다. 승인이 있더라도 옵션 전용 청산·담보·계좌 위험 검증은 별도다. 10/23·10/24 일정 불변.

## 2026-09-26 — BTC 옵션 보호 롱 선체결의 별도 안전 방식 연구

- [76](docs/research/76-btc-option-long-first-protection-study.md): 사용자의 진행 요청을 정책 변경이 아닌 Phase 0 **연구 승인**으로 적용했다. 보호 롱의 원격 확인 수량 ≥ 보유 숏 + 모든 잠재 숏 주문량이라는 불변식과 부분 체결·UNKNOWN·취소·강제청산 반례를 명시했다. 이는 우리 주문으로 단독 숏을 만들지 않는 필요조건이지만 만기 손실 한도만으로 계좌 1%/증거금 10%/1.5배 잔고 및 선물형 거래소 보호·비상 청산 요건을 통과시키지 못한다.
- 새 근거: Binance **RFQ 다중 다리**는 한 주문으로 결합하는 별도 채널임을 공식 FAQ·출시 공지에서 확인했다. 공개 주문장 `batchOrders`의 순서 미보장과 혼동하지 않는다. RFQ의 자동매매 API·원자성 세부 계약·계정 자격은 미확인으로 남기며 무료 주문장 호가를 RFQ 체결 증거로 쓰지 않는다. 일반 강제청산은 공식 FAQ상 숏부터 단계적으로 처리하고 롱은 그 후 음수 잔고 시 처리함을 구분했다.
- 판정 `LONG_FIRST_REMOVES_INTENDED_LEGGING_GAP / EXPIRY_BOUND_ONLY / ACCOUNT_PATH_NOT_BOUNDED / RFQ_ALTERNATIVE_UNVERIFIED / LIVE_BLOCKED`. 계정 인증·주문·유료 구매·코드/운영 변경 없음. 문서 링크·공백·변경 범위만 확인했고 코드 테스트는 수행하지 않았다. 다음 Phase 0: RFQ 공식 자동화/원자성 계약의 공개 확인 및 옵션 전용 보호정책에 대한 명시적 사용자 결정. 10/23·10/24 점검 일정 불변.

## 2026-09-26 — BTC 옵션 보호·청산 기능 공식 명세 재확인

- [75](docs/research/75-btc-option-protection-api-audit.md): 공식 옵션 REST 신규/일괄 주문·취소·계정 조회와 auto-cancel kill-switch/MMP를 대조했다. 신규 주문 유형은 명세상 `LIMIT`만 열거하고, IOC/FOK·`reduceOnly`는 전량 체결/시간 보증이 아니다. kill-switch와 MMP는 **열린 주문 취소/거부**이지 체결된 숏 포지션 청산이나 손절이 아니다. 계정·포지션 조회 인터페이스는 있으나 계정 값은 미확인이다.
- 판정 `DOCUMENTED_OPTION_STOP_NOT_FOUND / ORDER_CANCEL_IS_NOT_POSITION_CLOSE / REDUCE_ONLY_LIMIT_NOT_GUARANTEED / POLICY_DECISION_REQUIRED`. 공개 명세의 미확인을 모든 Binance 기능의 부재로 단정하지 않는다. 현행 선물형 보호·5초 전량 청산 규칙을 옵션 롱 다리로 대체하는 것은 사용자 승인 전 금지이며 옵션 매도 실거래는 차단한다.
- 계정 인증·주문·유료 구매·코드/운영 변경 없음. 문서 상대 링크·공백·변경 범위를 확인했고 코드 변경이 없어 코드 테스트는 수행하지 않았다. 다음 Phase 0: 보호 롱의 별도 보호 방식 인정 여부에 대한 사용자 결정. 10/23·10/24 점검 일정 불변.

## 2026-09-26 — BTC 옵션 매도 보호 다리 실행 불가 조건

- [74](docs/research/74-btc-option-execution-safety-gate.md): 공개 Binance 옵션 주문·매도 증거금/강제청산 규칙을 [TRADING_CORE](docs/current/TRADING_CORE.md)의 거래당 1%·증거금 10%·가용잔고 1.5배·첫 체결 보호/5초 상한과 대조했다. 일괄 주문은 매칭 순서가 보장되지 않아 숏 선체결을 배제할 수 없다. 순차 보호 롱 선체결은 필요조건이지만 옵션 신규 주문 API의 `LIMIT`만으로 현행 거래소 측 조건부 보호/시장가 비상 청산을 입증할 수 없다.
- 판정 `BATCH_ENTRY_UNSAFE / LONG_FIRST_CONDITIONAL / LIVE_EXECUTION_BLOCKED / ACCOUNT_UNVERIFIED`. 일반 계정에서 옵션 스프레드/선물 간 증거금 상계는 기본 제공되지 않으며 만기 손실 한도는 중간 강제청산 위험의 상한이 아니다. 수익성 기각이나 영구적 옵션 기능 부재 판정은 아니다.
- 계정 인증·주문·코드·운영 변경·유료 구매 없음. 문서 상대 링크·공백·변경 범위를 확인했고 코드 변경이 없어 코드 테스트는 수행하지 않았다. 다음 Phase 0: 공식 옵션 보호/청산 기능 재확인과 현행 규칙 정합성 판단; 계정 읽기·장애 시험·정책 변경은 별도 승인 범위. 10/23·10/24 점검 일정 불변.

## 2026-09-26 — BTC 옵션 보호 다리 쌍의 무료 호가 동시 관측

- [73](docs/research/73-btc-option-two-leg-quote-overlap.md): [72](docs/research/72-btc-option-fixed-strata-depth-audit.md)의 48계약·4일·각 10분에서 등가격 부근 숏 콜→110% 보호 콜, 등가격 부근 숏 풋→90% 보호 풋 **16쌍**을 사전 고정했다. 마지막 두 스냅샷 나이 1초·5초 이내, 두 주문장 모두 양방향인 시간과 최종 관측 시차를 측정했다. 2023년 가까운 콜은 무제한 마지막 호가 운반 시 약 557초 함께 보이지만 5초 나이 진단에서는 37초, 1초에서는 7초였다. 2025년 연결 중단 경계는 상태를 초기화했다.
- 판정 `TWO_LEG_DISPLAY_OVERLAP_VARIABLE / ATOMIC_FILL_NOT_PROVEN / ACCOUNT_RISK_NOT_CLEARED`. 1초·5초는 주문 기준이 아닌 자료 신선도 진단값이다. 2023년 가까운 콜은 독립적인 100ms 격자 근사로 확인했다. 표시 겹침은 동시 체결·보호 롱 선체결·수익의 증거가 아니다.
- 유료 구매·원자료 저장·계정 인증·주문·운영 변경 없음. 문서 상대 링크·공백·변경 범위를 확인했고 코드 변경이 없어 코드 테스트는 수행하지 않았다. 다음 Phase 0: 공개 거래소 규칙과 TRADING_CORE의 보호/1% 계좌 위험/독립 증거금을 대조해 옵션 실행 설계의 불가 조건을 정리. 10/23·10/24 점검 일정 불변.

## 2026-09-26 — BTC 옵션 무료 depth 만기·행사가 범주 감사

- [72](docs/research/72-btc-option-fixed-strata-depth-audit.md): [67](docs/research/67-btc-option-quote-quality-sample.md)의 사전 고정 4일·48계약(가까운/먼 만기 × 90/100/110% 행사가 × 콜/풋)을 첫 관측 지수 다음 1분부터 각 10분간 조회했다. 45/48계약에 depth 프레임이 있었고 3계약은 무관측. 2023년 가까운 만기 26k 풋은 1,676프레임이 모두 같은 최우선 호가 상태, 표시 스프레드 10,000bps였다. 2025년 응답에는 연결 중단 1회가 있었다.
- 범주별 프레임 수·양방향 결손·옵션 프리미엄 대비 표시 스프레드 중위를 모두 기록했다. 같은 날짜의 등가격 부근은 일부 날개 행사가보다 좁은 표시 호가가 있었지만 이는 실제 체결·수익의 증거가 아니다. 판정 `STRATIFIED_FREE_DEPTH_OBSERVED / YEAR_ROUND_EXECUTION_NOT_INFERABLE / ACCOUNT_RISK_NOT_CLEARED`.
- 구매·원자료 저장·계정 인증·주문·운영 변경 없음. 문서 상대 링크·공백·변경 범위를 확인했고 코드 변경이 없어 코드 테스트는 수행하지 않았다. 다음 Phase 0: **동일 48계약·10분 창**에서 보호 매수/매도 다리의 동시 표시와 관측 시각 차이를 위험 필요조건으로만 감사. 10/23·10/24 점검 일정 불변.

## 2026-09-25 — BTC 옵션 무료 depth 고정 표본 품질 감사

- [71](docs/research/71-btc-option-fixed-depth-quality.md): [69](docs/research/69-btc-option-free-depth-and-leg-risk.md)의 고정 3일·6계약·각 10분을 재조회해 스냅샷별 양방향 여부, 표시 스프레드·최우선 수량, BBO 변화, 연결 내 관측 간격을 집계했다. 2024년 두 계약은 각 2프레임·끝까지 약 579초 무관측. 2025년 풋은 bid 없는 27/3,315프레임, 공급처 연결 중단 1회. 2026년 두 계약의 유효 프레임 스프레드 중위는 옵션 프리미엄 대비 약 1,818/690bps.
- 판정 `FREE_DEPTH_MEASURABLE / EXECUTION_QUALITY_INSUFFICIENT_TO_INFER / ACCOUNT_RISK_NOT_CLEARED`. 표시 호가·수량은 실제 체결·수익·독립 거래 표본이 아니다. 구매·원자료 저장·계정 인증·주문·기존 운영 변경 없음. 다음: [67](docs/research/67-btc-option-quote-quality-sample.md)의 사전 고정 만기·행사가 범주로 무료 표본의 대표성 한계를 점검하고, 옵션 보호/담보 충돌은 별도 승인 전까지 차단. 10/23·10/24 점검 일정 불변.

## 2026-09-25 — BTC 옵션 무료 depth 스냅샷 의미 정정

- [70](docs/research/70-btc-option-depth-snapshot-semantics.md): Tardis 공식 옵션 매퍼 소스는 구형 `depth100`·신형 `depth20`의 **각 원시 프레임을 `isSnapshot=true`로 처리**한다. Binance 신형 partial-depth 명세와 무료 월 1일 원시 표본(2025년 재연결 직전/직후, 2026년 첫 두 프레임)을 대조했다. 따라서 [69](docs/research/69-btc-option-free-depth-and-leg-risk.md)의 “초기 스냅샷 미증명”은 **자료 형식 의미에 관해 정정**한다. 공식 Node 클라이언트 실행이나 거래소와의 독립 교차검증은 하지 않았다.
- 재연결 뒤 첫 프레임부터 **새 상위 N레벨 목록**으로 시작 가능하나, 직전 상태를 운반하지 않는다. 2025년 재관측 풋은 bid `5`/10·ask `2370`/0.24라 실행 품질이 불량하다. 전체 주문장·체결 가능성·계좌 위험은 여전히 미검증. 판정 `TOP_N_SNAPSHOT_SEMANTICS_CONFIRMED / EXECUTION_QUALITY_NOT_PROVEN / ACCOUNT_RISK_NOT_CLEARED`.
- 구매·패키지 설치·원자료 저장·계정 인증·손익 계산·기존 운영 변경 없음. 다음: 사전 고정 무료 표본에서 스냅샷별 양방향 호가·스프레드·표시 수량·관측 간격을 계약 유형별로 집계하되, 월 1일 표본의 일반화 한계를 명시. 10/23·10/24 점검 일정 불변.

## 2026-09-25 — BTC 옵션 무료 L2 시퀀스·단독 다리 위험 게이트

- [69](docs/research/69-btc-option-free-depth-and-leg-risk.md): 무료 월 1일 세 날짜·선택 6계약의 각 10분 depth를 메모리에서 검사했다. 신형 2026년 두 계약은 첫 관측 후 `pu=직전 u`가 250/250·279/279이나, 초기 스냅샷 완전성은 미증명. 구형 2025년은 연결 중단 1회와 `pu=u` 형식이어서 같은 ID 연속성 검사를 적용하지 못한다. 풋은 bid 레벨 0인 프레임 27개.
- 공개 Binance 계약 정보(2026-09-25 13:47 UTC)의 현재 BTC 666계약은 `unit=1`, `minQty=0.01`, USDT 호가였지만 과거 계약·사용자 계정 규격으로 소급하지 않는다. 옵션 일괄 주문은 원자적 체결이 아니며, 문서화된 신규 주문 유형은 `LIMIT`뿐이다. 보호 롱 미체결·숏 단독 체결·독립 증거금·부분 청산·선물 헤지 경로를 현행 위험 규칙에 대조했으나 **거래소 측 보호와 계좌 위험의 정합성은 미해결**.
- 정규화 L2의 문서상 샘플 URL은 이 환경에서 HEAD 404(다운로드 안 함). 판정 `FREE_L2_LINK_PARTIAL / SNAPSHOT_NOT_PROVEN / ACCOUNT_RISK_NOT_CLEARED`. 유료 데이터 구매·원자료 저장·계정 인증·손익 계산·운영 변경 없음. 다음: 공식 클라이언트의 무료 raw 재생/스냅샷 생성 가능성 및 보호/담보 충돌의 별도 설계 판단. 10/23·10/24 점검 일정 불변.

## 2026-09-25 — BTC 옵션 연속 10분 호가·계좌 위험 감사

- [68](docs/research/68-btc-option-continuity-and-risk.md): 무료 월 1일 네 날짜에서 2계약씩 각 10분의 표시 호가를 연속 재생했다. 2024년 두 계약은 매초 600행이나 각 1회만 상태가 바뀌었고, 첫 약 21초 ask=0 뒤 약 579초간 같은 상태였다. 이는 신규 주문 600개나 확정 체결 유동성이 아니다.
- 2025년 구간에서 공급처 연결 중단 표식 1개(00:09:01~00:09:03 사이)를 찾아 그 사이를 `UNKNOWN`으로 분리했다. 2026년 `bookTicker`의 긴 메시지 간격도 공백 주문장과 동일시하지 않는다. L2 초기 스냅샷·시퀀스까지 검증한 완전한 주문장 재구성은 아직 아니다.
- Binance 일반 계정의 옵션·선물 증거금 비상계, 옵션 일괄 주문의 매칭 순서 미보장, 숏 옵션 부분 강제청산, 공개 수수료 공식을 확인했다. 만기 이론 손실 한도와 실제 계좌 경로 위험은 다르다. `CONTINUITY_PARTIAL / EXECUTION_RISK_UNRESOLVED`, 구매·사전등록·백테스트·기존 운영 변경 없음.
- 다음: 제한된 계약/날짜에서 초기·재시작 L2 스냅샷과 시퀀스를 검증하고, 계정/시점별 계약 단위·담보·다리 실패 경로를 위험 한도에 대조. 10/23·10/24 운영 점검 일정 불변.

## 2026-09-25 — BTC 옵션 호가 대표성·용량 소표본 감사

- [67](docs/research/67-btc-option-quote-quality-sample.md): 무료 월 1일 네 날짜에서 첫 관측 BTC 지수로 만기 2종·행사가 비율 3종·콜/풋 48계약을 고정하고, **그 시점 이후** 192 계약·분을 조회했다. 초기 정오 지수로 앞선 시간을 분류한 시도는 미래 가격 사용으로 폐기하고 전부 다시 계산했다.
- 구형 티커의 반복 행은 신규 호가가 아니었다. 예: 2023년 가까운 만기 1,440행→관측 상태 77개. 2024년 가까운 만기 90%·110% 행사가의 유효 양방향 상태는 각각 4/10·11/23, 표시 스프레드는 컸다. 무갱신 분은 주문장 공백으로 단정하지 않는다.
- 12계약 정오 1분의 메모리 gzip은 날짜별 19~41KB. 같은 속도의 24시간 단순 환산은 약 27~57MiB/일이지만 **하루·연간·DB 용량 예측은 아니다**. 원자료 저장·전체 체인 적재·손익 계산·구매·운영 변경 없음.
- 판정 `SOURCE_ACCESS_CONFIRMED / EXECUTION_DATA_NOT_VALIDATED`; 유료 전 기간 자료 구매나 전략 사전등록은 아직 권하지 않는다. 다음: 무료 날짜의 연속 주문장 재생으로 실제 호가 나이·무갱신 구간 확인, 계약 규격·증거금/수수료와 안전한 다리·선물 헤지 위험 정합성 점검. 10/23·10/24 운영 점검 일정 불변.

## 2026-09-25 — 만기 BTC 옵션 무료 호가 소표본 감사

- [66](docs/research/66-btc-option-vol-premium-feasibility.md) 갱신: Tardis 월 1일 무료 **원시 HTTP API**에서 2023·2024·2025·2026년 만기 BTC 콜/풋 8계약의 고정 1분 구간 16개를 조회했다. 실제 bid/ask·수량을 확인했으며 2023·2025·2026년 호가창 레벨도 소표본 확인. CSV 샘플 링크가 열린 것은 아니므로 구분한다.
- 2024년 선택 계약은 60개 티커 행이 동일 호가·수량을 반복하고 거래량 필드가 0인 구간이 있었다. 2023~2026년의 선택 구간 스프레드도 크게 달랐다. **행 수를 신선한 호가·독립 표본·체결 가능성으로 해석하지 않는다.** 전체 커버리지·비용·검정력 미계산, 사전등록·백테스트 없음.
- 공급처 조건: Options/Solo 표시 월 $700. Solo는 CSV만, 2023~2026 전체 이력에는 4년 접근의 연 결제 조건이 필요하며 실제 견적·저장량 미확정. 결제·원자료 저장·운영 변경 없음.
- 다음 작업: 무료 월 1일 자료로 만기·행사가별 호가 신선도와 양방향 수량의 대표성 감사 및 제한된 저장량 추산. 전략 손익 열람·유료 계약은 그 후 별도 판단. 10/23·10/24 운영 점검 일정 그대로.
- 사용자 지시: 향후 완료·검증된 작업은 범위 내 파일만 자동 커밋·일반 푸시. 범위·충돌·비밀정보 안전 조건은 [AGENTS §6](AGENTS.md)을 따른다.

## 2026-09-25 — BTC 옵션 과거 호가 공급처 감사

- [66](docs/research/66-btc-option-vol-premium-feasibility.md)에 Tardis Binance European Options 이력 감사를 추가했다. 공급처는 2023-06-15부터의 만기 계약 목록과 `quotes`·L2·`options_chain` 등 양방향 호가·수량을 포함하는 데이터 유형을 공개한다. 만기 BTC 계약의 메타데이터도 직접 확인했다. 따라서 이전의 **별도 과거 호가 공급처 미확인**은 정정한다.
- 공개 샘플 파일은 이 환경에서 HEAD 404·제한 GET 403으로 열리지 않았다. 호가 완전성·스프레드·결측·실제 체결 가능성은 아직 미검증. Options/Solo 표시 가격은 월 $700이며 단일 BTC 범위의 가격·라이선스는 미확정이다. 결제·대량 다운로드·백테스트·운영 변경 없음.
- 다음 작업: 소수 만기 BTC 계약의 샘플 접근 조건과 파일 품질 확인 → 기간·용량·구매 비용 추산. 사용자 결정 전 유료 계약·전략 사전등록 없음. 기존 10/23·10/24 점검 일정 그대로.

## 2026-09-25 — 신규 BTC 옵션 연구 Phase 0 점검

- 사용자 후속 요청으로 기존의 새 연구 보류 결정 중 **Phase 0 타당성 점검만** 재개해 [66 BTC 옵션 변동성 보험료·선물 헤지 타당성](docs/research/66-btc-option-vol-premium-feasibility.md)을 점검했다. 상태 **자료·실행 위험 게이트 미충족, 사전등록 전 보류**.
- 옵션 원자료가 로컬에 없고, 확인된 Binance API/공개 아카이브에는 다년간의 당시 bid/ask·수량 확보 근거가 없다. **실측:** 현재 BTC 옵션 일봉 8개 반환, 지난주 만기 옵션의 과거 일봉은 API `-1121`. Binance 일반 계정은 옵션·선물 간 증거금 상계를 제공하지 않는다. 따라서 수익 백테스트·검정력 계산·전략 채택은 하지 않았다.
- 다음 작업: 만기 BTC 옵션의 당시 양방향 호가·수량을 제공하는 공식/라이선스 이력 감사. 기존 Paper·데모·실거래 준비 기준 및 10/23·10/24 점검 일정은 그대로.

이전 갱신: 2026-09-25 밤 · 작성: Claude (인계 정리 — 문서 55~65 반영, 타입 검사 복구, 데모 계좌 ETH 충돌 수정).
당시 상태: **Phase 6 — Paper T/v2(09-22~)·O/v1·O/v2·O/v3(09-23~)** · Phase 8 준비(그림자·데모·O/v2 안전성 검증) · **10/23 O/v3 보고 · 10/24 4주 점검(기준 고정, 문서 65)**.
D22는 중단·제외. 승인된 수익 전략 없음. A1 종료(DATA_INSUFFICIENT), WA·K·M·TA·P1·F1b·I3·FB·LQ·MD·B′·F2·F3·DDL 기각. Elliott→수익 모델은 이 자료로 판정 불가(63).

## 이번 작업 (Claude — 인계 정리: 문서 55~65 반영 · 타입 검사 복구 · 데모 계좌 충돌 수정)

- **인계:** 09-25 오후~밤 다른 세션 작업(문서 55~65, 커밋 29개)이 PROGRESS 에 없어서 아래에 요약했다.
- **타입 검사 복구:** mypy strict 오류 30개(파일 10개)를 고쳤다. 동작 변경은 없다.
  - 대상: 오늘 추가된 데모·실거래 실행기·Elliott·점검 테스트, 데모 스크립트 2개
  - 고친 것: 테스트 가짜 객체의 타입 표기, lambda → def
- **데모 계좌 ETH 충돌 수정 ([59 §3 후속](docs/research/59-demo-o2-futures-leg.md)):**
  - 수동 스톱 훈련 2개(스톱 발동·스톱 교체)가 데모 O/v2 와 같은 ETHUSDT 를 썼다. 손으로 돌리면 O/v2 의 ETH 롱과 비상 스톱을 닫을 수 있었다
  - → XRPUSDT 로 옮기고, 시작 때 우리 기록 밖 포지션·스톱이 있으면 멈추게 했다. 실패 정리도 그 확인 뒤에만 한다
  - 테스트 4건. 실계좌 데모에서 읽기로 확인: ETH 에서는 멈추고 XRP 에서는 통과
- 오늘 밤 자동 실행(09-25): 11개 작업 모두 정상.
  - 선물 실행기 훈련 7/7
  - **그림자 운영 첫 실행: Paper 와 목표 차이 0**, 주문 0건
  - 데모 O/v2 ETH 0.236 유지

## 다른 세션 작업 요약 (09-25 오후~밤, 문서 55~65)

- **데모 트랙 3개 (가짜 돈, Binance 데모):**
  - [55](docs/research/55-demo-t2-limit-execution.md) 데모 T/v2 현물 — 지정가 L240 체결 비용 실측. 21:12
  - [56](docs/research/56-demo-elliott-fib-btc.md) Elliott+피보 2·4파 되돌림 BTCUSDC 5x, 위험 1%. 상주 실행기. **검증이 아닌 관찰용**
  - [59](docs/research/59-demo-o2-futures-leg.md) O/v2 선물 추가분을 실제 T/v2 목표로 ETHUSDT(BTC 는 10/08 뒤). 21:14
  - 공유 데모 계좌 감독자 결함 수정(`shared_account`)
- **[57](docs/research/57-phase8-t2-live-executor.md) Phase 8 1단계 실행기:** `execution/live_t2.py`.
  - 실제 잔고, 지정가 L240, 선기록, 재시작 이어받기, fail-closed, 긴급 정지. 장애 시험 9건
  - 데모로 예행연습 중. **실계좌 주문 어댑터는 승인 전까지 없다**
- **Elliott 판독기:**
  - [58](docs/research/58-ew3e-candidate-generator.md) EW-3e 채택
  - [60](docs/research/60-ew4-flat-triangle.md)·[61](docs/research/61-ew4-nested-triangle-flat.md) EW-4·EW-4n (플랫·삼각형·안 삼각형)
  - [62](docs/research/62-ew4n-daily-degree-null.md) 일봉 대조: BTC 에서 모양 빈도가 뒤섞기 대조보다 높지 않음 → 모양의 근거로 쓰지 않음
  - [63](docs/research/63-elliott-to-model-feasibility.md) **Elliott 을 필터·손절로 쓰는 길도 이 자료로는 판정 불가 → 중단**
- **[64](docs/research/64-o-futures-first-derisk.md) 선물부터 줄이기 F2·F3 REJECT.**
  - **O/v3(2배)는 과거 경로에서 2022-11-09 계좌 정지.** 이전 "O/v3 CAGR 30.1%" 는 정지 경로의 숫자다 → 10/23 보고에 적는다
- **[65](docs/research/65-1023-1024-review-criteria.md) 10/23·10/24 점검 기준 고정 (결과 보기 전):**
  - 기준: Paper P1~P6 · 그림자 S1~S5 · 데모 K1~K4
  - 결정 메뉴 D1~D5: 1단계 자격, O/v2 만 2단계 후보, O/v3 관찰 종료 권고
  - 보고서 `python -m scripts.review_phase6 --until <결정일>`
- 대시보드 '데모·실거래 준비' 화면. 일일 점검에 데모 T/v2·Elliott·O/v2 줄 추가.

## 이번 작업 (Claude — O/v2 이어가기: 현물 대비 정리 · 남은 안전성 검증)

- 사용자 요청: 추세 전략의 BTC·ETH 선물 확장은 O/v2 방향. 처음부터 다시 연구하거나 운영 전략을 바꾸지 말고, 기존 결과와 남은 안전성 검증을 잇는다 → [52](docs/research/52-o2-continuation-safety.md).
- 현물 대비 (이미 있는 판정): O/v2 CAGR 31.7% vs T/v2 23.4% (+8.3%p, funding·수수료 뒤), MDD 23.2% vs 15.9%, 샤프 1.17 vs 1.27. 위험을 더 지고 더 버는 구조. 표본 밖은 Paper 전향 관찰뿐이다.
- **지적:** O/v2 는 추세가 약해질 때 현물·선물을 같은 비율로 줄인다. "선물부터 줄이기"는 새 변형이라 지금 만들지 않는다(관찰 뒤 후보).
- **V1 매일 스톱 교체 (테스트넷 측정):**
  - 수량 지정 reduce-only 스톱은 closePosition 스톱과 함께 걸린다. 두 개도 함께 걸린다.
  - → 다리 스톱 방식으로 **공백 없이 교체 가능**. 취소 → 재등록 공백은 약 0.1초.
  - `scripts/testnet_stop_replace_drill.py`, 어댑터 `place_stop_market(quantity=…)`
- **V2 완료 (52 §6):** `execution/overlay_futures.py`.
  - 하루 순서: 복구 → 증가 허용 판단(정지 스위치·그날 손실 2%) → 수량 맞추기 → 다리 방식 스톱 교체 → 마지막 확인
  - 가짜 거래소 시험 15건: 발동 뒤 재진입·정지 스위치·다리 실패·취소 실패·스톱 불가 비상 청산 포함
  - hypothesis 속성 시험: 어떤 경로에서도 롱 포지션에 거래소 스톱이 있다
  - 마이그레이션 008 (BRIDGE). 실계좌 주문 경로 없음
- **V3 완료 (53):** 실계좌 이체는 하지 않았고 어댑터도 만들지 않았다.
  - 공식 문서 확인: `POST /sapi/v1/asset/transfer`, "Permits Universal Transfer" 권한, **클라이언트 멱등 ID 없음**(tranId 만)
  - 이체 관리자: 선기록 → 결과 모르면 이력에서 같은 금액·미사용 tranId 로만 해소, 여러 건이면 사람 확인, 10분 뒤 NOT_FOUND. 모르는 이체가 있으면 새 이체와 그 돈에 기대는 주문을 막는다
  - 하루 순서 계획기: 선물 줄이기 → 현물 팔기 → (모자라면) 선물→현물 이체 → 현물 사기 → 남는 돈 선물로 → 선물 늘리기
  - 선물 지갑 최소 금액 = max(명목/3, 1.5 × 명목/거래소 레버리지)
  - 테스트 18건(속성 시험 150개 포함), 마이그레이션 009
- **V4 등록 (54):** `scripts/testnet_overlay_drill.py`. SOL·BNB 로 O 의 BTC·ETH 를 대신하고, 며칠 들고 가며 목표 순서를 돈다.
  - 매일 확인: 포지션 = 목표 · 스톱 하나(오늘 가격) · 재실행 주문 0건 · 복구 RUNNING
  - **테스트넷에서만 드러난 차이 2건을 고쳤다:**
    - 스톱 취소·조회가 늦게 맞는다 → 로컬 ∩ 거래소 목록으로 판단 (지연 흉내 시험 추가)
    - 걸린 주문이 있으면 증거금 방식 변경 −4067 → 첫날만 설정
  - 수정 뒤 7/7. 일일 점검에 한 줄 추가
  - 운영 09-26~10-02 (K1~K4). 스케줄 `AutoTrading_Testnet_Overlay` 20:45 등록 확인(사용자, 09-25). 20:45:45 실행이 겹침 방지에 걸리던 문제를 등록 확인 때 고쳤다(가정 실행 시간 5 → 2분)
- 남은 검증: V4 운영 · V5 선물 조항 승인 · V6 Paper 관찰. O 전체 오케스트레이터는 2단계 직전.

## 이전 작업 (Claude — Phase 8 준비: 보호주문 충돌 정리 · T/v2 그림자 운영)

- [50 결정 자료](docs/research/50-overlay-protective-order-decision.md): O 오버레이는 TRADING_CORE 네 규칙에 걸린다.
  - 보호주문 필수, 거래 위험 1%, 증거금 10%, 청산거리 비율 2.0
  - O/v2 는 보호주문을 채운다. 스톱이 발동하면 계좌의 3~9% 를 잃는다. 레버리지 2.4배 초과(과거 0.2% 날)에서 비율 규칙을 어긴다
  - 권고 A: Phase 8 1단계는 T/v2 현물만, O/v2 계열은 2단계. **사용자 결정 대기**
- [51 그림자 운영](docs/research/51-phase8-shadow-t2-registration.md): `scripts/run_shadow_t2.py` — 주문 0건.
  - Paper 실행기를 실거래 예정 금액으로 돌린다 (mode='shadow')
  - 실계좌 읽기 점검 (키 권한·시계·잔고), 호가 미끄러짐, Paper 목표 대조
  - 긴급 정지 스위치 `data/HALT`, 일일 점검에 한 줄 추가
- 첫 시험(임시 DB): 키 읽기 전용·IP 제한 ✓, 시계 ✓, 호가 미끄러짐 BTC 0.00 · ETH 0.02 bps (10,000 USDT 까지), 주문 0건.
- 코드: `execution/shadow_t2.py`(순수 점검), `ops/kill_switch.py`, `PaperT2Runner(mode=…)`, `BinanceSpotReadOnly.order_book`, `check_shadow`. 테스트 +20건 (전체 1,960).
- **사용자 결정·등록 (2026-09-25):**
  - 50 → **A**: Phase 8 1단계는 T/v2 현물만, O/v2 계열은 2단계
  - 그림자 운영 `AutoTrading_Shadow_T2` 등록 (`--allocation 10000`, 매일 21:10, 첫 실행 09-25 21:10)
- 다음: 과제 3(상시 가동 환경 비교). 10/24 에 Phase 6 점검과 그림자 기준 S1~S5(51 §3)를 함께 본다.

## 이전 작업 (Claude — 선물 트랙: 거시 지표 발표 첫 반응 MD 사전 등록)

- 사용자 선택 "A로 가자" → [49](docs/research/49-macro-release-drift.md).
  - 가설: CPI·NFP·PCE·FOMC 발표 첫 15분 BTC 반응이 4시간 동안 지속되거나 반전한다
  - 방향은 Train 에서 한 번 정하고 양측 검정한다
  - 컨센서스는 라이선스 자료라, 시장의 첫 반응을 서프라이즈 대리로 쓴다
- Train 2021~2022 88건(`macro_events` 감사 시각). Validation·OOS 는 발표 시각 자료가 없어 Train 통과 시에만 모은다(BLS 자동 수집 금지).
- 규칙: 진입 t+15분, 스톱 첫 15분 극값, 청산 4시간, 편도 0.095% + funding. 기준: 거래 ≥ 30, 순수익 > 0 + 양측 p < 0.05, 두 해 모두 양수.
- 검출 가능 효과: 총 92 bps/거래 (4시간 표준편차 245 bps).
- **Train 1회 판정: REJECT** (49 §7).
  - 지속은 비용 전부터 음수라 반전이 정해졌고, 반전 순수익 −13.6 bps(비용 전 +5.3), 두 해 모두 음수, 스톱 72/88
  - 스톱 없음 참고 줄 +28 bps(t 1.08)는 규칙 변경으로 쓰지 않는다
- **사용자 결정 (2026-09-25, "A로 하자"): 몇 시간 단위 BTCUSDT 방향 탐색을 멈춘다.**
  - 근거: H2·FB·LQ·MD 모두 비용 전 +4~7 bps(taker 왕복 19 bps 미만)였다. 정보원을 포지셔닝·가격 구조·외부 충격으로 바꿔도 같았다.
  - 다음 확인: 10/23 O/v3 보고, 10/24 Phase 6 4주 점검.
  - 선물 연구를 다시 열 때는 기간(며칠 이상)이나 시장을 바꾼 새 구조부터 후보 비교로 시작한다. 48·49 의 Validation·OOS 는 봉인된 채 남아 있다.

## 이전 작업 (Claude — 선물 수익 전략 독립 트랙: FB·LQ 사전 등록)

- 사용자 요청: BTCUSDT 무기한에서 비용 차감 후 재현 가능한 edge. 후보 4개 중 [48](docs/research/48-perp-positioning-events.md)에서 둘을 골랐다.
  - FB: 실패한 돌파 + OI 증가 + funding 과열
  - LQ: 청산 추정 = 1시간 OI 급감 + 가격 급변
  - Price/OI 괴리와 funding 극단은 H2 F3·F1(기각)과 같은 신호라 제외했다.
- 규칙(Entry 다음 봉 시가 · Stop 구조 극값 · Exit 12시간)과 기각 기준을 고정했다.
  - Train 2021+2023: 거래 ≥ 30, 순수익 > 0 + Holm, 두 해 모두 양수
  - Validation 2024: 순수익 > 0, p < 0.05
  - OOS 2025~2026-08: 순수익 > 0, MDD ≤ 30%
- 이벤트 빈도: FB Train 118 · Val 67 · OOS 85, LQ 91 · 43 · 65. 검출 가능 효과는 거래당 총 80~90 bps 수준.
- **Train 1회 판정: FB·LQ 모두 REJECT** (48 §7). Validation·OOS 는 봉인된 채 남았다.
  - FB: 순수익 −10.7 bps/거래(비용 전 +7.4), 두 해 모두 음수, 95% 상한 약 +18 bps
  - LQ: 지속 방향 −15.4 bps(비용 전 +4.0), 2021 −69 / 2023 +17, 최악 거래 −12.3%
- 다음: 사용자 결정 대기. 비슷한 OI·funding 조합은 반복하지 않는다. 다른 구조적 가설은 후보 비교부터 한다.

## 이전 작업 (Claude — I3 MACD 히스토그램 롱숏 판정)

- 사용자 승인("판정 진행해줘") 뒤 1회 판정 → **REJECT** ([47 §6](docs/research/47-i3-macd-preregistration.md)). M1·M2·M3·안전 모두 실패.
  **2022-03-08 계좌 정지**(MDD 30.0%), 숏 순손익 **−4,621**(F1b −2,188 의 두 배), 롱 +4,201(funding −1,586), CAGR −0.7%, 샤프 0.05.
  T/v2 와 상관 0.09 이지만 반반 샤프 0.91 < T/v2 1.27. 비용 참고 줄에서도 숏 음수.
- 지표 트랙 결론: 요청 지표는 T·F1b 와 같은 신호(I1·I5·I6), 표본 부족(I4), 치명적 숏 꼬리(I2), 판정 기각(I3).
  일봉 가격 신호로 든 BTC·ETH 선물 숏은 T/v1·F1b·I3·TA 모두 수익원이 아니었다. RESEARCH_HISTORY 에 I3 추가.
- 다음: 사용자 결정 대기. 46 의 B(RSI 과매도 롱만, 표본 약 25회)는 남아 있지만 권하지 않는다. O/T 는 10/23·10/24 결과를 기다린다.

## 이전 작업 (Claude — I3 MACD 히스토그램 롱숏 사전 등록)

- 사용자 선택 "A로 진행" → [47 사전 등록](docs/research/47-i3-macd-preregistration.md). **수익 미계산, 판정 전.**
  45 F1b 와 같은 자료·시뮬레이터·판정 기준(M1 평균−2SE>0 · M2 두 절반 > 0 · M3 숏 순손익 > 0 · 청산·정지 0)이고 방향만 MACD(12,26,9) 히스토그램.
- `fast_trend.py` 에 방향 규칙(`rule`)과 `I3` 추가. 변경 뒤 F1b 재실행 결과가 45 판정 파일과 같음(최종 자본 12,008.67) 확인. `scripts/run_i3.py`(check·run, 1회·깨끗한 트리만).
- 판정은 위 '이번 작업' 참조.

## 이전 작업 (Claude — 지표로 선물 방향 정하기: 후보 비교 측정)

- 사용자 요청(44 §4) RSI·MACD·MA·EMA·일목을 [46 후보 비교](docs/research/46-indicator-candidates.md)로 측정. **수익·샤프·낙폭 미계산.**
  후보는 가족마다 교과서 기본값 하나로 먼저 고정(I1 RSI 50 · I2 RSI 30/70 역추세 · I3 MACD 히스토그램 · I4 SMA 50/200 · I5 EMA 20/50 · I6 일목 구름).
- 결과: I1 ≈ F1b(같은 방향 91%, 기각 전략 재시험), I5·I6 ≈ T(89·92%, 숏 0 근처로 이미 판정). I2 는 유일하게 새 정보지만
  강세장 숏이라 숏 41번 중 9번이 +50% 이상 역행(최대 +225%). I4 는 숏 14번 — 판정 불가. **I3 만 T 와 거의 무관(상관 0.09)하고 꼬리가 F1b 수준.**
- 6개 동시 시험(Holm)은 최소 검출 샤프 1.27 — 하나로 좁혀야 판정 가능. 권고 A: I3 MACD 롱숏 사전 등록(45 틀 재사용, 기각 가능성 큼).
  B: I2 롱만(표본 약 25번), C: 트랙 마무리. → 사용자 A 선택.
- 코드: `backtest/indicator_signals.py`(순수·causal) + 테스트 22건(고정값·Decimal 지표와 일치·미래 무관성), `scripts/measure_indicator_candidates.py`(DB 읽기 전용).
  T·F1b 회전율·전환 수가 44 측정과 같음을 확인.

## 이전 작업 (Opus — F1 빠른 추세 롱숏, 문서 44·45)

- [44 후보 비교](docs/research/44-f1-fast-trend-candidates.md) → [45 F1b 사전 등록·판정](docs/research/45-f1b-preregistration.md) **REJECT**:
  숏 순손익 −2,188, 2023-05-28 계좌 정지(MDD 31%), 롱 funding −2,371. 추세를 따르는 BTC·ETH 숏은 두 속도 모두 수익원이 아니다.

## 이전 작업 (Codex — Elliott 독립 연구 Phase 0 시장 창 가용성)

- [33 §12~13](docs/research/33-elliott-phase0-data-and-profile.md)에 **WA 결과 열람 전** 4h 후보 창 가용성 집계 단위·차단 규칙을 고정한 뒤, [읽기 전용 감사](scripts/audit_ew0_candidate_coverage.py)를 2020~2021 Binance USD-M BTCUSDT 대용 4h/1m에 실행. 기존 `mode=ro`·`query_only=ON` 시장 로더만 사용했고 WA 체결·PnL/운영 DB·Paper·전략을 건드리지 않음.
- 4,386개 4h 마감 중 후보 ≥2/≥4/≥6 창은 **882/369/147개 마감**. `UNKNOWN` 1,229개, 비-UNKNOWN 구간 542개, 4/6 후보에 도달한 구간 29/11개. 같은 창의 반복 마감이며 독립 파동·검정 표본 수 아님.
- `UNKNOWN` 이전 극값을 참조하는 뒤쪽 후보 **147개**, 이후 다음 `UNKNOWN`까지 **714개 마감 차단**. 기존 후보 매핑·2% 임계·창 길이를 결과에 맞춰 바꾸지 않음. 실측 하위 degree parser도 없어 시장 문법 `PASS`·WA 결합 불가. `NOT_READY_FOR_WA_TEST` 유지.
- [coverage.py](src/auto_trading/research/elliott/coverage.py) 순수 집계와 [합성·읽기 전용 테스트](tests/research/test_elliott_coverage.py) 추가. 동일 감사 재실행 수치·매핑 증거 SHA-256 일치. 검증: 전체 pytest **1,811건**·leakage **65건**·strict mypy **260개 소스**(새 스크립트 포함 별도 135개)·ruff check/format **319파일**·import-linter **5계약** 통과; `git diff --check`.
- 다음: 교차-UNKNOWN 147건의 원인을 **시장 결과·WA 비열람**으로 분류하고, 4h 이상 자식 degree의 출처/확정 시각 계약을 별도 사전 명세. 기존 규칙 변경은 설계·회귀 검증 뒤 결정.

## 이전 작업 (Codex — Elliott 독립 연구 Phase 0 후보·출처 계약)

- [grammar.py](src/auto_trading/research/elliott/grammar.py): 하위파 표식을 출처·4h 다리 양 끝·확인 가용 시각이 붙은 `LegSubdivisionProof`로 교체. 현재 **합성 fixture만** 인증하고 4h 미만·미래 시각·잘못된 다리는 거부. 하위파 미제공이면 `PENDING` 유지.
- [candidates.py](src/auto_trading/research/elliott/candidates.py): 마지막 `UNKNOWN` 뒤 마지막 6개/4개 후보의 고정 rolling 창으로 impulse·일반 zigzag·ending diagonal 각 하나, 최대 세 가설만 생성. 미보정 confidence·확률 없음; `UNKNOWN` 이전 극값을 새 구간에 연결하지 않음. 마지막 꼭짓점 이후 재연장 시 종료 가설 무효화 시각·가격을 **분석 전용**으로 기록.
- [합성 테스트](tests/research/test_elliott_grammar.py)와 [33 §11](docs/research/33-elliott-phase0-data-and-profile.md): 출처·시각 경계, 제한된 후보, `UNKNOWN` 재시작, 미래 prefix 불변, 분석용 무효화. 시장 681 후보·WA 방향/PnL 미입력, 기존 전략·Paper·DB·스케줄 불변. `NOT_READY_FOR_WA_TEST` 유지.
- 검증: 전체 pytest·leakage, strict mypy **258개 소스**, ruff check/format **316파일**, import-linter **5계약** 통과. `git diff --check` 확인.
- 다음: 고정 규칙의 **WA 결과 비열람** 시장 품질/coverage 감사와 4h 하위 degree 파서의 출처 계약 구현 가능성 검토. BitMEX 원시장 결측·실측 하위파 미검증이므로 WA 결합 금지.

## 이전 작업 (Codex — Elliott 독립 연구 Phase 0 엄격 문법)

- [grammar.py](src/auto_trading/research/elliott/grammar.py): 최소 마감 4h 후보 증거의 **연속 구간**에서 strict impulse·일반 zigzag·수렴 ending diagonal의 기하학/하위파 문법만 순수 판정. `PASS/FAIL/PENDING/UNKNOWN`과 위반·미검증 사유를 분리하고 복수 시나리오를 선택·확률화 없이 모두 반환. 추가 피벗 건너뛰기 금지, 4h wick·부모 위치·시간-가격 수렴 검사.
- [합성 테스트](tests/research/test_elliott_grammar.py): 상승/하락 대칭, 2파 경계·3파 최단·4파 wick/봉 내부 불명, 3파 generic motive 거부, zigzag B 경계, ending 부모/수렴, UNKNOWN·미완성·미검증 하위파, 복수 시나리오·미래 prefix 불변. 문서 [33 §10](docs/research/33-elliott-phase0-data-and-profile.md)에 조건부 `PASS`의 한계 기록.
- **681개 시장 후보를 문법 판정기에 넣지 않았다.** 하위 degree parser/provenance·고정 탐색 창·invalidation 원장 미구현. 시장 파동 라벨, WA 방향/PnL·거래 신호 없음. `NOT_READY_FOR_WA_TEST`, 운영 Phase 6 유지.
- 검증: 전체 pytest **1,803건**·leakage **63건**·strict mypy **257개 소스**·ruff check/format **315파일**·import-linter **5계약** 통과. `git diff --check` 통과.
- 다음: 하위파 검증 자료 계약과 bounded candidate generator·시나리오 snapshot/invalidation을 합성 자료로 설계. 이후 블라인드 coverage, 그 뒤 WA 결합 게이트.

## 이전 작업 (Codex — Elliott 독립 연구 Phase 0 4h 후보 coverage)

- §8의 사전 고정 규칙을 바꾸지 않고 [읽기 전용 감사](scripts/audit_ew0_h4_evidence.py)를 2020~2021 Binance USD-M BTCUSDT 대용 4h/1m에 적용. 시장 봉 이외 WA 거래·성과 자료는 열지 않았고 DB·보고서 파일은 쓰지 않았다. 기존 1m 감사의 `load_market_replay`를 재사용, 가짜 DB 해시 불변·부재 시 미생성 테스트 추가.
- 4h 4,386개: `CANDIDATE` **681(15.5%)**(high 358·low 323), `NO_EVENT` **2,476(56.5%)**, `UNKNOWN` **1,229(28.0%)**. UNKNOWN 첫 사유: 복수 553·동일 4h 480·모호 1m 124·국소 극값 44·단절 20·품질 차단 8. 2020/2021 연도별·확정 지연은 [33 §9](docs/research/33-elliott-phase0-data-and-profile.md)에 기록.
- **681은 Elliott 파동 수나 독립 WA 표본 수가 아니다.** 후보 연속 구간·파동 문법·WA 방향/PnL·BitMEX 원시장 확인 미실시. 수치에 맞춰 임계/매핑 규칙 조정 없음. `NOT_READY_FOR_WA_TEST`, 운영 Phase 6 유지.
- 검증: 전체 pytest **1,790건**·leakage **62건**·strict mypy (`src tests` 255개 소스 및 감사 스크립트 포함 별도 132개 소스)·ruff check/format **313파일**·import-linter **5계약** 통과. 가짜 DB 무변경·부재 시 미생성, 동일 시장 감사 재현 확인.
- 다음: 최소 마감 4h 봉에서 impulse·zigzag·ending diagonal의 hard rule과 `UNKNOWN`/복수 시나리오를 보존하는 **순수 문법 validator 설계·합성 테스트**. 시장 후보를 바로 파동 라벨로 부르지 않음.

## 이전 작업 (Codex — Elliott 독립 연구 Phase 0 4h 증거 매핑)

- 결과·WA 거래를 보지 않고 [33 §8](docs/research/33-elliott-phase0-data-and-profile.md)에 1m 경로 사건→마감 4h 피벗 *후보 증거*의 보수적 규칙 고정. Elliott 파동/진입 신호는 생성하지 않는다.
- `h4_evidence.py`: 4h당 확인 사건 정확히 1개, 극값은 이전 완성 4h의 실제 high/low, 극값~확정 경로 연속, 후보 극값 시각 증가·종류 교대일 때만 `CANDIDATE`. 정상 무사건은 `NO_EVENT`; 복수 사건·동일 4h·국소 극값·모호/차단·비단조는 사유별 `UNKNOWN`, 데이터 오류는 거부. 과거 후보 소급 변경 금지.
- 합성 테스트: 4h 마감 전 미노출·이전 봉 극값·복수 사건·동일 봉·국소 극값·단절·비단조 충돌·미래 prefix 불변. **이 규칙의 시장자료 적용 비율과 WA 효과는 아직 보지 않았다.** 기존 운영·DB·Paper·스케줄 불변, `NOT_READY_FOR_WA_TEST` 유지.
- 검증: 전체 pytest **1,788건**·leakage **62건**·strict mypy **254개 소스**·ruff check/format **311파일**·import-linter **5계약** 통과. `git diff --check` 통과.
- 다음: WA 결과를 가린 채 2020~2021 Binance 대용 시장자료에서 4h 후보/무사건/UNKNOWN 사유별 coverage를 읽기 전용 측정. 이후 Elliott 문법 validator 설계. BitMEX 자료 결측 전 WA 결합 금지.

## 이전 작업 (Codex — Elliott 독립 연구 Phase 0 스트리밍 시장 감사)

- 사용자 결정 반영: **Elliott 파동 분석·판정의 최소 시간대는 마감 4h**. 1m은 4h 내부 사건 순서/품질 확인에만 사용하고 1m 파동·진입 신호로 쓰지 않는다. [CURRENT_SPEC](docs/current/CURRENT_SPEC.md), [Elliott 설계](docs/research/ELLIOTT_WAVE_ANALYSIS.md), [33 감사](docs/research/33-elliott-phase0-data-and-profile.md)에 명시. 실거래 연결은 별도 승인 전 금지.
- `GuardedExtremaState`로 기존 2% 재생기를 한 봉씩 먹이는 동일 상태 기계로 분리하고, `replay_minute_groups`가 4h당 240개 1m만 메모리에 두고 경계 간 후보 상태를 유지한다. 배치·스트리밍 결과 일치, 품질 차단·1m 모호함 경계 테스트 추가.
- `scripts/audit_ew0_minute_path.py`: 시장 DB `mode=ro`·`query_only=ON`, 2020~2021 Binance 대용 4h/1m만 읽음. 4h 4,386개 중 품질·전역 고저 순서 통과 4,378개(99.82%), 차단 8개(겹침 6·거래량 0 포함 2). 1m 모호함 565개에서 절단, 분봉 경로 반전 사건 3,500개, 사건 노출 4h 1,894개, 분봉 확인→4h 마감 중앙 대기 125분. **이는 4h Elliott 피벗/파동 coverage 또는 수익 검증이 아니다.**
- WA 거래·방향·PnL, 전략·Paper·DB·스케줄은 변경하지 않았다. `NOT_READY_FOR_WA_TEST` 유지.
- 검증: 전체 pytest **1,780건**·leakage **60건**·strict mypy (`src tests` 252개 소스, 새 감사 스크립트 포함 별도 검사 131개 소스)·ruff check/format **309파일**·import-linter **5계약** 통과. 가짜 DB 해시 불변·부재 시 미생성 테스트 통과.
- 다음: WA 결과 가림 유지, 1m 사건을 4h 이상 후보에 연결할 수 있는 다대일·충돌·UNKNOWN 매핑 규칙을 사전 고정하고 테스트. BitMEX 원시장 자료 결측 해결 전 WA 진입 분석 금지.

## 이전 작업 (Codex — Elliott 독립 연구 Phase 0 1m 경로 재생)

- `src/auto_trading/research/elliott/pivots.py`의 기존 2% 가드 상태 기계를 시간순 검증 봉 재생 함수로 분리하고, `minute_path.py`에서 완성 4h마다 240개 1m 격자·양수 거래량·OHLC·전역 고저 순서가 확인될 때만 연속 경로를 재생한다.
- 1m 안의 새 극값·반전 선후가 모호하면 해당 분에서 구간을 절단한다. 품질 실패·전역 고저 겹침 4h는 통째로 차단하며, 서로 다른 구간을 잇지 않는다. 분봉 반전 확인은 **해당 4h 마감 후에만** 연구 결과로 노출하고 4h Elliott 파동 라벨로 승격하지 않는다.
- 합성 테스트 8건: 복수 분봉 반전의 마감 전 미노출, 연속 4h 후보 유지, 1m 모호함, 결측·거래량 0·OHLC 불일치·전역 고저 겹침 차단, 미래 자료 무관성. [33 감사](docs/research/33-elliott-phase0-data-and-profile.md)에 경계 기록.
- **시장자료 전체 경로 coverage·확정 지연은 아직 측정하지 않았다.** 기존 1,931/1,933개 수치는 전역 고저 선후만 확인된 봉 수다. WA 거래·방향·PnL, 전략·Paper·DB·스케줄은 변경하지 않았다. `NOT_READY_FOR_WA_TEST` 유지.
- 검증: 전체 pytest 1,776건, leakage 59건, strict mypy 251개 소스, ruff check/format 307파일, import-linter 5계약 모두 통과.
- 다음: WA 결과를 가린 채 Binance 대용 자료에서 1m 경로의 블라인드 coverage·연속 구간 길이·확정 지연을 읽기 전용 감사한다. 이후 4h 파동 입력 매핑 규칙과 원시장 BitMEX 자료 경계를 고정한다.

## 이전 작업 (Codex — Elliott 독립 연구 Phase 0 1m 순서 감사)

- `src/auto_trading/research/elliott/intrabar.py`: 닫힌 4h 봉의 240개 1m 시각 격자·양수 거래량·OHLC 정확 일치 시에만 **전역 최고·최저 발생 분의 선후** 반환. 같은 분/교차 반복·품질 실패는 UNKNOWN, 4h 마감 전에는 결과 미노출.
- `scripts/audit_ew0_intrabar.py`: DB `mode=ro`·`query_only=ON` 스트리밍 집계, 파일 출력 없음. 테스트는 가짜 DB 해시 불변·DB 부재 시 미생성까지 검사.
- 2020~2021 Binance USD-M BTCUSDT 4h 4,386봉: 전역 고점 먼저 2,135·저점 먼저 2,243·겹침 6·거래량 0 포함 2. 4h/1m 격자·OHLC 불일치 0. 이전 가드 검출의 모호한 봉 1,933개 중 **전역 고저 순서만** 1,931개 확인(고점 먼저 942·저점 먼저 989), 나머지 겹침 1·거래량 0 포함 1.
- 이 순서만으로 2% 반전 경로·피벗이 복원됐다고 간주하지 않음. 임계·가드 검출기·다이버전스·WA 거래·기존 Paper/전략/DB/스케줄 변경 없음. [33 감사](docs/research/33-elliott-phase0-data-and-profile.md), [Elliott 설계](docs/research/ELLIOTT_WAVE_ANALYSIS.md) 갱신.
- `NOT_READY_FOR_WA_TEST` 유지. 다음: 1m 내부 순서 불명을 보존하는 시간순 경로 상태 기계와 4h 마감 전 미노출 테스트. BitMEX 원시장 봉은 여전히 없음.
- 검증: 전체 pytest·leakage·strict mypy·ruff check/format·import-linter 통과.

## 이전 작업 (Codex — Elliott 독립 연구 Phase 0 가드 피벗)

- `src/auto_trading/research/elliott/pivots.py`: 별도 순수 4h·2% 피벗 검출기. 새 극값과 반전이 같은 봉에 담기면 장중 순서를 가정하지 않고 모호한 봉을 절단, 구간별 확정 피벗·모호한 시각을 반환. 기존 운영 `detect_swings` 미변경.
- `divergence.py`는 가드 검출 결과를 구간별로만 다이버전스 판정하도록 결합. 모호함 양쪽 피벗을 한 쌍으로 만들지 않음.
- 합성·무작위 경로 테스트: 확정 시각·경계 2%·고저 대칭·동률·같은 봉 모호함·구간 분리·미래 prefix 불변·결측/역순/거래량 0 거부.
- **시장자료만 읽기 전용 감사**: 2020~2021 Binance USD-M BTCUSDT 4h 4,386봉 중 모호한 봉 1,933개(44.1%), 확정 피벗 496개, 피벗 ≥6개 연속 구간 10개. WA 거래·방향·손익 미조회. 수치 때문에 규칙을 조정하지 않음.
- [33 Phase 0 감사](docs/research/33-elliott-phase0-data-and-profile.md)와 [Elliott 설계](docs/research/ELLIOTT_WAVE_ANALYSIS.md)에 방법·결과·한계 기록. `NOT_READY_FOR_WA_TEST` 유지. 다음: 1m 봉으로 4h 내부 사건 순서만 확인하는 방법·품질 경계를 검증.
- 검증: 전체 pytest·leakage·strict mypy·ruff check/format·import-linter 통과. 기존 Paper/전략/DB/스케줄 변경 없음.

## 이전 작업 (Codex — Elliott 독립 연구 Phase 0 순수 판정 경계)

- `src/auto_trading/research/elliott/divergence.py`: EW-0 확정 가격 피벗·4h 종가 RSI(14) 일반 상승/하락 다이버전스 사건 판정. 피벗 가격/확정 봉·연속성·격자·거래량 0 검증, RSI warmup/같은 봉 발생·확정은 UNKNOWN, 미래 봉·미확정 피벗 비노출. Decimal 계산 context 고정.
- `tests/research/test_elliott_divergence.py`: 합성 방향 대칭·확정 지연·미래 변형·prefix 일치·warmup·동일 봉 모호함·결측/역순/잘못된 가격/임계 거부·Decimal context 검증.
- `pyproject.toml` import 계약으로 Elliott 연구 모듈을 기존 매매 경로에서 참조하지 못하도록 제한. Paper/전략/DB/스케줄 변경 없음.
- 이 adapter는 **피벗 자체를 생성하지 않으며**, 기존 ZigZag의 동일 봉 고저 경로를 완전히 검증하지 않는다. 파동 시나리오·WA 실제 거래 결합·성과 판정 미실행. `NOT_READY_FOR_WA_TEST` 유지.
- 검증: 전체 pytest·leakage·strict mypy·ruff check/format·import-linter 통과. 다음: 원천 피벗 검출의 장중 순서 모호함을 해소하는 독립 연구 detector와 인과성 테스트.

## 이전 작업 (Codex — Elliott 독립 연구 Phase 0 후속)

- SQLite 시장 DB를 `mode=ro`로 감사. 2020~2021 Binance BTCUSDT 1m 1,052,640·1h 17,544·4h 4,386·1d 731봉은 시각 격자가 채워져 있음. 12h·1w 미저장, 1m 거래량 0은 61봉. BitMEX XBTUSD 시장 봉은 없음.
- [33 데이터·규칙 감사](docs/research/33-elliott-phase0-data-and-profile.md)에 개발용 `EW-0`(4h·기존 2% 확정 피벗·RSI14·일반 다이버전스 엄격 비교)과 Binance 대용 자료 경계 기록.
- 기존 WA 대용 결합 390건 기준 단순 독립 표본 예시의 검출 한계 약 14.5%p/방향을 계산했으나, 군집·다중검정·BitMEX 결측 전의 **낙관적 예시**로만 표시. WA 결과·파동 빈도·PnL은 열람하거나 계산하지 않음.
- 현재 판정 `NOT_READY_FOR_WA_TEST`. 다음: Phase 0 잔여 문법·노출창·자료 경계 확정 후 순수 피벗/RSI adapter와 인과성 테스트. 기존 Paper/전략/DB/스케줄 변경 없음.
- 검증: 문서 링크·내용·diff 범위 검사. 문서 전용이라 코드 테스트는 미실행.

## 이전 작업 (Codex — Elliott 독립 연구 Phase 0)

- 추가 요청 반영: 연구 문서 §7.1~7.3에 일반 상승·하락 다이버전스, 가격 피벗 기준 지표 대응·확정 시각·분석 무효화/TTL·DV1~DV3 대조 분석·테스트 계획 추가. 히든 다이버전스는 초기 범위 제외. 코드·실제 결합 분석 없음.
- 사용자 선택: Elliott 분석 체계를 먼저 정리하고 이후 실제 워뇨띠 체결·D22 맥락과 결합하는 **독립 연구**. 기존 전략·운영 변경 없음.
- [ELLIOTT_WAVE_ANALYSIS](docs/research/ELLIOTT_WAVE_ANALYSIS.md) 작성: PDF 21쪽 본문·주요 도해, Aurra 공식 영문판(한국어 URL 접근 오류), EWI 직접 해설 대조.
- hard / guideline / subjective / 구현 약속 분리. PDF 삼각형 5-5-5 표기·105% 상한·diagonal 4파 경계 등 충돌을 기록하고 무비판적 코드화 방지.
- confirmed_at 기반 복수 scenario + 미보정 confidence + invalidation + UNKNOWN, 과거 snapshot 불변, 4h 이상 Elliott·D22 맥락 분리 설계.
- `WA_trades`(실제 BitMEX 체결)와 기각된 `WA_strategy` 분리. D22는 **이 연구의 설명 변수 참조만** 허용하며 운영 EXCLUDED 유지.
- 후속 설계: 진입 집중도 H1~H4, flat risk set·시장 맥락 matched baseline, 군집·다중검정·검정력, 사후 size/holding/PnL/MFE/MAE 분리.
- Opus 최신 기록: UI·WS 검증·O/v2·O/v3·저녁 시각 변경·32 후보 비교 확인. 실제 스케줄/거래소/기존 테스트 재실행 없음.
- **다음: Phase 0 — 규칙 프로파일·미확정 수치와 데이터 가용성/검정력 설계 확정**. 이후 별도 순수 판정기 검증, 그다음 사용자 요청 순서대로 실제 체결 결합.
- 검증: 문서 링크·내용·diff 범위 검사만. 코드/config/원장/DB/스케줄 변경 없음. WA CSV 재분석·파동 실험·통계 판정 미실행.

## 이번 작업 (Claude — 노출 검토 · O/v3 한 달 관찰 · 저녁 실행)

- [30 노출 검토](docs/research/30-exposure-review.md): 새 백테스트 없이 1배·1.5배 결과로 산술 추정(2배 ≈ 연 42%·MDD 29~32%) — 계좌 정지 30% 에 걸린다.
  사용자 요청 참고 계산(최근 12개월, 이미 본 구간): 1배 −4.2% · 1.5배 −6.7% · 2배 −9.1% — 판정 아님. 금(C3) 제외, 주식 선물은 추후 후보.
- 사용자 결정: 2배도 한 달 관찰 → [31 등록](docs/research/31-o3-2x-paper-observation.md) `O3_2X`(2배 + 비상 스톱), `--ledger o3`, 점검·대시보드 추가,
  비트 단위 재현 테스트. 첫 실행 결정일 2026-09-23.
- 사용자 결정: 출근 때문에 **저녁 실행** — T/v2 21:00 · O/v1 21:02 · O/v2 21:04 · O/v3 21:06 · 테스트넷 21:08 · 점검 21:20 (놓치면 켤 때 실행).
  수동 테스트넷 훈련의 회피 구간을 [11:55, 12:30) UTC 로 옮김. 문서 16·25·26·28·OPERATIONS 시각 갱신.
- **남은 일(사용자)**: 스케줄 등록·시각 변경 PowerShell 명령 실행(응답 참조).

## 이전 작업 (Claude — 새 수익 후보 비교 · 유휴 현금 이자)

- [29 후보 비교](docs/research/29-next-return-candidates.md): C1 유휴 현금 이자(T/v2 평균 유휴 79%) · C2 체결 비용 절감(비용 부담 연 2.4~3.5%) · C3 금 추세(검정력 빠듯).
  큰 수익을 약속하는 새 방향 예측 후보는 없다고 정직하게 적었다. 권고 C1.
- 사용자 확인 이자율(7.08%≤1,000 · 3.08%)로 대략 계산: T/v2 23.4→26.4%, O/v1 33.3→36.3%, 최근 2025~2026 3.0→5.7%.
- 사용자 선택 "Paper 에 이자 반영 줄": `core/cash_yield.py`(구간 이자·날짜별 이자율·유휴 비율 경로, 테스트 6건), `config/cash_yield.yaml`,
  대시보드 카드·전략 비교 열(원장 위 가정 계산, 선물은 명목 절반 증거금 유지). 원장·Paper 실행기 불변. 대시보드 테스트 +2건.
- 다음(사용자 결정 대기): 노출 확대(수익↑·낙폭↑) 검토 여부, C2·C3 여부.

## 이전 작업 (Claude — 읽기 전용 운영 대시보드)

- 사용자 결정: 대시보드 먼저, 그다음 새 수익 전략 연구. 구성은 사용자가 고른 초안(Astra 목업)을 따른다.
- `src/auto_trading/ui/`: `data.py`(DB **mode=ro** + 보고서 파일 읽기, 실패는 화면에 표시하고 계속) · `charts.py`(서버 SVG, 외부 자원 없음)
  · `render.py`(화면 6개: 운영 개요·전략 비교·포지션·테스트넷 검증·위험·연구 기록, 모든 동적 문자열 이스케이프) · `server.py`(표준 라이브러리,
  **127.0.0.1 전용·GET 만**·CSP·no-store) · `dashboard.css`. `scripts/dashboard.py`(서버 / `--export`). 새 의존성 없음.
- import 계약 유지: ui 는 core·storage 만(판정은 일일 점검 JSON·훈련 보고서·RESEARCH_HISTORY 를 읽는다). 점검 스크립트가 `reports/watchdog/<날짜>.json` 도 남긴다.
- 테스트 19건: 읽어도 DB 해시 불변, DB 없으면 만들지 않음, 손상 파일 건너뜀, 화면 6개·이스케이프·폼/스크립트 없음, 0.0.0.0 거부, 쓰기 메서드 501, 404.
- 실행: `python scripts/dashboard.py` → http://127.0.0.1:8787 (Ctrl+C 로 끔). 다음: 새 수익 전략 연구(사용자 결정 순서).

## 이전 작업 (Claude — O/v2 비상 스톱 사전등록)

- 사용자 선택: 다음 작업 1번 **비상 손절**, 거리 **−20%** (급락 일수 측정 뒤: 보유 중 −20% 이상 BTC 3일·ETH 12일, 연 약 2회).
- `OverlayConfig.catastrophe_stop`(기본 None = O/v1 과 비트 단위 동일)·`stop_slippage` 1%, `OverlayResult.stop_hits`.
  하루 순서: 손익·funding → **비상 스톱**(선물만, 전날 무기한 종가 × 0.8 이하 저가면 그 가격 × 0.99 체결) → 청산 → 정지 → 일 손실 → 체결.
  O/v1 Paper 는 기본 설정이라 변화 없음(Paper 재현 테스트 통과). 테스트 4건(먼 스톱 = 없음, 발동·재진입, 청산보다 먼저, 미발동).
- [27 사전등록](docs/research/27-o2-catastrophe-stop-preregistration.md): S1 O/v1 기준(O1~O5) 유지 · S2 보험 비용 ≤ CAGR 2%p. `scripts/run_o2_stop.py`(1회 가드, 테스트 9건).
- 사용자 승인 뒤 1회 실행 → **PASS**: CAGR 31.7%(O/v1 33.3%, 보험 비용 1.56%p ≤ 2%p), MDD 23.2%, 발동 11회, 정지·청산 0.
  과거에는 낙폭을 줄이지 못하고 비용만 들었다 — 근거는 표본 밖 위험과 보호 주문 규칙. 27 §7, RESEARCH_HISTORY.
- 사용자 결정: "O/v1 은 그대로 두고 **O/v2 를 하나 더**" → [28 전향 등록](docs/research/28-o2-paper-forward-registration.md)(첫 실행 전 고정, 기준 25 §3 과 같음).
  `O2_STOP` 설정 한 곳, `run_paper_o1.py --ledger o2`(인자 없으면 O/v1 — 기존 09:10 스케줄 그대로), 원장 `o2_stop`·`reports/paper/o2/`,
  O/v2 만 `stop_hits` 기록, 일일 점검에 O/v2 줄(기록 없으면 '등록 전' 정보, 스톱 발동일 경고). 테스트: 스톱 발동 포함 비트 단위 재현·규칙별 재계산·원장 분리·O/v1 형식 불변.
- 스케줄 `AutoTrading_Paper_O2` 09:12 등록 — 사용자 완료, Ready 확인. 첫 실행(수동) 결정일 2026-09-23, O/v1 과 같은 목표.

## 이전 작업 (Claude — Phase 7 WS 사용자 스트림, 커밋 `5d794df`)

- 공식 문서 재확인: **옛 WS 주소(`/ws/<listenKey>`)는 2026-04-23 폐지**, 사용자 데이터는 `wss://fstream.binance.com/private/ws?listenKey=…&events=…`.
  테스트넷 주소는 문서에 없어 실측: `wss://fstream.binancefuture.com/private/ws`(listenKey 는 `demo-fapi`).
- 실측 조사(ETH): 발동한 스톱의 실제 주문은 **스톱과 같은 client ID**, 이벤트 **순서 역전**(체결이 TRIGGERING 보다 먼저) → 이벤트는 "그 주문 다시 조회" 신호로만 쓴다.
- 구현: `exchange/user_stream.py`(순수 해석, 실측 원문 테스트) · `exchange/binance_usdm_stream.py`(listenKey 발급·30분 연장·23시간 교체·끊김 재연결·키 가림)
  · `execution/stream_sync.py`(우리 주문만 `refresh` 조회, LAGGING 재시도, UNTRACKED·DUPLICATE, 연결마다 전체 재조회) · `FuturesOrderManager.refresh`
  · 어댑터 listenKey 3종(키 헤더만, 서명 없음). 의존성 `websockets>=13`. 테스트 25건.
- 선택형 훈련 `scripts/testnet_stream_drill.py`: 1차 8/9(훈련 판정 결함 수정) → 2차 8/8 미발동 → **3차 9/9** — 스톱 발동을 조회 루프 없이 이벤트로 확정, 재연결 6.3초, 수신 지연 ≤ 73ms. 26 §6.
- 남은 것: 운영 실행기(Paper·실거래)에 스트림 연결은 아직 안 함 — 지금 Paper 는 하루 1회 종가 체결이라 필요 없고, 실거래(Phase 8) 실행기 설계 때 붙인다.

## 이전 작업 (Claude — Phase 7 실제 스톱 발동 확인)

- 스케줄 4개 등록 확인(읽기 조회): T2 09:05 · O1 09:10 · **Testnet_Drill 09:14** · Watchdog 09:20 모두 Ready. 훈련·점검은 09-25 첫 실행.
- `scripts/testnet_stop_drill.py`(선택형, 스케줄 없음): ETHUSDT 로 매일 훈련(BTC)과 분리, 매일 훈련 구간 [00:05, 00:35) UTC 와 겹치면 시작 거부,
  mark 15bp 아래 closePosition 스톱 → 발동까지 대기(최대 60분, 스톱 이동 없음) → 미발동이면 정리 후 `NOT_TRIGGERED`(판정 불가, 종료 2).
  보고서 `reports/testnet/stop-*.json`(점검 보고의 `drill-*` 통계와 섞이지 않음). 순수 규칙 테스트 8건.
- **실행 결과 9/9 TRIGGERED**: 2,683.90 롱 → 스톱 2,679.49 → 235초 뒤 발동, 체결 2,679.33(**0.60bp 불리**). 스톱 기록 FILLED·수량 일치·평탄·
  재시작 복구 RUNNING — Algo 스톱 발동 체결이 `actualOrderId` 조회로 로컬 기록에 들어오는 경로를 실측으로 확인. 26 §3·§6.
- Phase 7 남은 것: **WS 사용자 스트림**(`ORDER_TRADE_UPDATE`·`ALGO_UPDATE`) — 지금은 5초 조회로 발동을 안다. 부분 체결은 가짜 거래소 테스트로 대체(26 §3).

## 이전 작업 (Claude — Phase 7 매일 훈련 운영 등록)

- 사용자 선택: 테스트넷 훈련 **매일 1회 자동 실행**.
- [26 운영 등록](docs/research/26-phase7-testnet-drill-registration.md) — **시작 전** 기준 고정: 2026-09-25 ~ 10-08 (14일),
  P1 밀린 날 0 · P2 실패 원인 전부 설명 · P3 코드 결함 실패 0(나오면 수정 후 14일 재시작) · P4 매일 평탄 마무리.
  매일 훈련이 못 채우는 항목(실제 스톱 발동·부분 체결·WS 수신)은 별도 작업으로 분리해 적었다.
- 점검 보고에 테스트넷 한 줄 추가(`check_testnet_drill`): 최근 결과·14일 중 실행/통과 일수, 실패·밀림은 **WARN 까지만**
  (Paper 안전과 별개). 등록 전 개발 중 실행(오늘 1·2차 실패)은 통계에서 제외. 테스트 10건.
- 보고 머리글을 `[일일 점검 ...]` 으로 바꿨다(Paper 뿐 아니라 테스트넷도 담는다).
- 스케줄 `AutoTrading_Testnet_Drill` 등록(09:14) — 사용자 완료, Ready 확인.

## 이전 작업 (Claude — Phase 7 테스트넷 훈련)

- 테스트넷 키 확인(값 가림, 실계좌 키와 다름). 공식 문서의 테스트넷 주소 `demo-fapi.binance.com` 과 예전 `testnet.binancefuture.com`
  둘 다 같은 계좌(가짜 5,000 USDT)에 닿음을 읽기 조회로 확인.
- `exchange/binance_usdm_testnet.py`: **테스트넷 주소가 아니면 생성 자체를 거부**하는 주문 어댑터. 전송 결과 분류:
  거래소 코드 거절 → 확실한 거절, 네트워크·5xx·-1007·레이트 리밋 → 결과 모름(조회로만 해소). 실계좌 주문 경로는 여전히 없다.
- `scripts/testnet_drill.py`: 별도 DB `data/testnet.db`. 3차 실행에서 **20/20 통과** — 같은 의도 3회 → 1건, Algo 보호 스톱,
  재시작 복구, 거래소 거절 5종, 전송 직후 강제 종료 → 조회로 FILLED 복구, 스톱 취소 → 감시자 재보호, 원인 불명 포지션 정리.
- **테스트넷이 드러낸 것 (문서에 없던 것)**:
  - closePosition 스톱은 닫을 포지션이 있어야(-4509), 같은 방향 하나만(-4130), 검사 순서 -4509 → -4130 → -2021.
  - 시장가 `RESULT` 응답의 평균가가 0 → 체결 명목/수량으로 계산, 없으면 재조회.
  - 스톱 취소 직후 조회가 아직 NEW → 취소 성공 응답을 기준으로.
  - **체결된 client_order_id 재사용을 거래소가 받는다** → 로컬 선기록이 필수라는 TRADING_CORE 원칙의 실증.
  - 청산 뒤 closePosition 스톱은 거래소가 자동 정리. 테스트넷 수수료 0.040%(연구 가정 0.045%, 실계좌 요율은 별도 확인 필요).
- 감시자 수정: **우리가 걸지 않은 주문이 있는 심볼은 자동 조치하지 않는다.** 모르는 스톱이 이미 보호 중이면 우리 스톱이
  -4130 으로 막혀 멀쩡한 포지션을 비상 청산하는 경로가 있었다.
- 전체 pytest **1,637건**·leakage 50건, mypy 223파일, ruff, import 계약 4/4 통과.

## 이전 작업 (Claude — Phase 5 선물 실행 인프라, 4주 대기 중)

- 확인: 스케줄 3개(T2 09:05·O1 09:10·Watchdog 09:20) 등록, 텔레그램 인증 로드(값 가림), `.env` git 무시.
- 사용자 결정: 4주 점검까지 기다리는 동안 **실거래 전 필요한 선물 인프라**를 만든다. 주문 전송 경로는 만들지 않는다.
- 1단계 `47dbcf2`: `exchange/futures.py` 계약, `fake_futures.py`(포지션 회계·증거금·reduce-only·보호 스톱 갭 체결·funding·청산·장애 주입 5종),
  `futures_orders`(007) 선기록·결정론적 ID(목적·재발행 번호)·UNKNOWN 조회 해소. **결과를 모르는 주문은 신규 진입만 막고 축소·청산·보호는 막지 않는다.**
  게이트 테스트: 같은 의도 100회 → 1건, 강제 종료/재시작 10회, 무작위 장애 성질.
- 2단계 `57894cd`: `futures_guard` 재시작 정합성 복구 — 조회 실패 시 주문 0, 자동 조치 셋(보호 스톱 3회·3초 실패 시 비상 청산+HALTED,
  원인 불명 포지션 정리, 할 일 없는 스톱 취소), 나머지 차이는 DEGRADED + `risk_events`. 007 에 확인된 외부 변동 표(실DB 미적용 상태에서 추가).
- 3단계: **공식 문서 재확인에서 결정적 차이 발견** — Binance 는 2025-12-09 부터 STOP_MARKET 을 **Algo API** 로 옮겼고
  일반 주문 경로는 `-4120` 거부. 기억대로 만들었다면 실거래 첫날 보호 스톱이 전부 거부됐다. 계약을 일반/스톱 경로로 분리.
  `binance_usdm.py` 서명 읽기 전용 어댑터(주문·취소 항상 거부 = 확실한 거절로 기록), `scripts/check_futures_account.py`.
- **실계좌 읽기 점검(조회만)**: 읽기 전용·IP 제한 키로 선물 계좌 조회 성공(지갑 0·포지션 0), 시계 차이 167ms, 최소 명목 BTC 50·ETH 20.
  실측이 문서를 판정: 걸린 스톱 목록은 `openAlgoOrders`(문서 페이지의 `algoOpenOrders` 는 404), 없는 Algo 주문도 `-2013`.
  JSON 아닌 응답에서 파싱 오류로 죽던 결함 수정(계약대로 ExchangeError).
- 전체 pytest **1,609건**·leakage 50건, mypy 221파일, ruff, import 계약 4/4 통과.

## 이전 작업 (Claude — Paper 일일 점검·알림, Phase 6 게이트)

- 스케줄러 `AutoTrading_Paper_O1` 등록 확인(사용자): Ready, 다음 09-25 09:10. 수동 1회 실행 결과 0 — 작업 폴더 없이도 정상, 오늘 행 중복 없음.
- 발견: Phase 6 게이트의 **"알림·일일보고·watchdog 정상"** 이 없었다(`notify/` 비어 있음). 스케줄 실행이 실패해도 `status`를 치기 전엔 알 수 없었다.
- `ops/paper_watch.py`(순수 판정): 두 원장의 실행 누락·DEGRADED·기록 빈 날·누락 보정·O/v1 청산/정지·**원장 재계산(verify)**·낙폭(경고 20%·중단 25% — 16 §3·25 §3).
  기준은 전향 등록에서 가져왔고 새로 만들지 않았다. 20% 는 조기 경보 운영값.
- `notify/channels.py`: 로컬 기록(항상) + 텔레그램(`.env` 설정 시). 토큰은 repr·예외·traceback 어디에도 싣지 않는다(`from None`, 설명문 치환).
- `config/secrets.py`: `load_telegram_credentials` — 둘 중 하나라도 비면 None(텔레그램 끔). 현재 `.env` 텔레그램 값은 **비어 있다**.
- `scripts/paper_watchdog.py`: DB **읽기 전용**, `reports/watchdog/<날짜>.txt`·`outbox.log`, 종료 코드 0/1/2, `--no-send`·`--test-telegram`.
  실데이터 첫 점검: 이상 없음 (T/v2 9,990.87 · 2일 · 오늘 2건 체결 / O/v1 10,000 · 1일).
- 테스트 26건(판정 규칙 16 · 채널 10: 토큰 비노출·잘림·ok=false 실패 포함). 전체 pytest **1,519건**, mypy 210파일, ruff, import 계약 4/4 통과.
- **남은 일(사용자)**: ① watchdog 스케줄 등록(09:20) ② 텔레그램 봇 만들기(선택) — 명령은 응답 참조.

## 이전 작업 (Claude — O/v1 별도 원장 Paper 착수)

- 사용자 승인: 24 §9 뒤 제안한 **O/v1 전용 별도 Paper 원장** 진행("그대로 이어서 작업"). T/v2 Paper·신호·설정·스케줄 변경 없음.
- **하루 규칙 공용화**: `backtest/overlay.py`의 루프 본문을 `step_overlay_day`로 분리 — 연구 재생과 Paper 가 같은 함수를 부른다.
  분리 전후 실데이터 2,375일 경로 A·B **비트 단위 동일**(sha256 지문 비교), 기존 overlay 테스트 통과.
- `exchange/binance_usdm_public.py`: USD-M 공개 시세(일봉·funding·현재가). **키 없음·주문/계좌 경로 없음**, 1,000건 잘림 거부. 테스트 10건.
- `storage/sql/006_paper_overlay_days.sql` + `repositories/paper_overlay_days.py`: 하루 한 행에 전체 상태·시장 입력 저장, 한 실행 = 한 트랜잭션.
- `execution/paper_overlay.py`: 종가 체결(연구와 동일), 현재가는 `observed`로만. **누락일은 보유만 계산(`MISSED`, funding·청산 반영, 거래 없음)**,
  30일 초과는 수동 확인. 데이터 부족(일봉·funding 3회 미만·신호 이력 121일/빈 날) 시 아무것도 쓰지 않음. `verify_ledger` 자체 감사.
  테스트 19건: 매일 실행 = 연구 1회 재생 **비트 단위 일치**(일반·레버리지 상한 경로), 멱등, 누락 보정, fail-closed, 변조·목표 교체·날짜 삭제 검출, T/v2 테이블 무접촉.
- `scripts/run_paper_o1.py run·status·verify`. [25 전향 등록](docs/research/25-o1-paper-forward-registration.md) — **첫 실행 전** 기준 고정:
  청산·정지 즉시 중단, 매달 MDD 25% 중단, 12개월 뒤 같은 날 T2_ALONE 재생보다 순수익 큼 → 소액 실거래 검토 자격(승인 필요).
- **첫 실행(2026-09-24 01:20 UTC)**: 결정일 2026-09-23, `NO_PRIOR_DECISION`, 자본 10,000, 내일 목표 BTC 0.3487·ETH 0.2538.
  같은 날 T/v2 Paper 목표(Decimal 경로)와 차이 **1.1e-16** — O/v1 이 T/v2 신호를 그대로 쓴다. 재실행 멱등·`verify` 일치.
- 전체 pytest **1,493건**·leakage **50건**, mypy strict 204파일, ruff check/format, import 계약 4/4 통과.
- **남은 일(사용자)**: 작업 스케줄러 등록 — 시스템 설정 변경이라 직접 실행하지 않았다. 명령은 25 §6·응답 참조.

## 이전 작업 (Claude — O/v1 사전등록)

- 베이시스 검토(보고만, 문서 없음): 분기물은 공개 아카이브에 있음(COIN-M 2020-06~, USDT-M 2021-02~). B1 현물+분기물 캐리는 K와 같은 수익원·최근 연 0~3% 추정,
  B2 캘린더 스프레드는 사이클 24개 < 필요 73~1,966개로 검정력 부족 → 사용자와 보류.
- 사용자 결정: "노출 2배, 손절은 추세 신호" → 한도 충돌 설명 뒤 **노출 1.5배 · 선물 최대 3배 · 일 손실 2% 날 노출 증가 금지**.
  3배 vs 5배 질문: T/v2 목표 비중 합이 3배 상한을 넘는 날 6.5년에 4일, 5배는 0일 → 사실상 같음, 3배 유지(24 §6).
- [24 사전등록](docs/research/24-o-overlay-preregistration.md): 2020-03~2026-08 1회. O1 정지 0 · O2 청산 0 · O3 MDD ≤ 25% · O4 B>A · O5 두 절반 B>A.
  증분 샤프 검정력 32~71% → 통계 증명 아님, 비용·위험 판정(§4). 노출: 가격·T/v2 결과 기열람(§5).
- `backtest/overlay.py`(테스트 13건, 노출 1배 = `simulate_trend` 경로 1e-12 일치), `scripts/run_o_overlay.py`(1회 실행 가드, 테스트 9건).
  T/v2 코드·설정·Paper·스케줄 변경 없음. 전체 테스트·mypy·ruff·import 계약 통과.
- 사용자 승인 뒤 1회 실행 → **PASS**: CAGR 33.3%(T/v2 23.4%), MDD 23.1%(15.9%), 샤프 1.22(1.27), funding −4,749, 정지·청산 0. 기록: 24 §9, RESEARCH_HISTORY.
  수익은 늘었으나 위험 대비 효율은 같거나 약간 낮음. 2025~2026은 둘 다 연 2~4%. 이미 본 구간이라 OOS 아님.
- 다음: 사용자 결정 — O/v1 별도 원장 Paper(전향) 여부. 실행 명령은 `PYTHONPATH="src;."` 필요(Git Bash).

## 이번 작업 (Codex — T/v2 선물 추가 노출 검토, 커밋 `4ea23bc`)

- 사용자 요청: 우선 기존 방향의 타당성을 검토하고 독립 선물 전략 연구는 추후 재개.
- [23 검토](docs/research/23-t2-futures-overlay-review.md): 기존 T/v2 Paper를 동결한 채, 별도 원장에서 여유 현금으로만 선물 롱을 추가하는 단일 실험을 권고. 수익·위험 확대와 위험 조정 성과 개선을 구분.
- 확인: 현물/선물 자본 중복 계산 금지, 현물 무손절 예외는 선물에 적용 불가. 보호 손절·재진입·합산 위험예산·현금 확보 우선순위·계좌 정지 적용 범위 미확정. 기존 한도·게이트 완화 없음.
- 비교 기준 점검: `run_t_trend.py`의 50/50 보유는 실제로 일별 재조정(재조정 비용 미차감). 새 비교에서 고정 수량 보유와 구분하고 과거 결과는 보존.
- 코드/config/DB/Paper/스케줄 변경 없음. 성과·데이터 적재율·검정력 미측정. 문서 링크와 변경 범위만 검사, 코드 테스트 미실행.
- 다음: **Phase 0 — 데이터 가용성 감사 + 보호 손절·합산 위험예산 설계 초안**, 필요 결정 승인·검정력 검토 후 사전등록. Phase 이동·실거래 승인으로 취급하지 않음.
- 미해결: 위 설계 항목, C1, Current의 일부 오래된 현물 구현 상태 문구. 이번 검토에서는 운영 상태를 직접 검증하지 않음.

## 이번 작업 (Claude — 2번: 새 선물 수익원)

- 작업 스케줄러 `AutoTrading_Paper_T2` 등록 확인(매일 09:05 KST). PowerShell에서는 `.\.venv\Scripts\python.exe` 문법.
- [19 후보 비교](docs/research/19-futures-next-candidates.md): A 다종목 선물 추세 · B 분기물 베이시스(BTC 23계약) · C 마진 간 funding(제외). 사용자 선택 A 기본안.
- [20 TA 사전등록](docs/research/20-ta-preregistration.md): 상위 20 무기한(현물 조건 없음), 종목별 변동성 목표 1.5%, 총 명목 ≤ 2배,
  밴드 0.5%p, 알트 비용 0.145%, funding 반영, 2020-06~2026-08 1회. A1 평균−2SE>0 · A2 두 절반 양수 · A3 MDD ≤ 30% · 청산 0.
  `scripts/ta_data.py`, `backtest/multi_trend.py`(테스트 8건), `scripts/run_ta.py`(테스트 9건). 적재율 일봉 99.8%·funding 99.6%. 커밋 `ecebeb9`.
- **TA 결과: REJECT.** 샤프 0.52, 하한 −0.010%/일(A1 실패), MDD 18.9%. 같은 규칙 BTC·ETH 2종목(보고) 샤프 0.77 — 분산이 오히려 해.
  기록: 20 §7, RESEARCH_HISTORY TA 항목.
- 사용자 결정(정정): 현물 T/v2는 그대로 두고 Paper 4주 동안 **선물 연구 계속**.
- [21 후보 비교](docs/research/21-futures-candidates-3.md): P1 ETH/BTC 페어(BTC와 상관 0.04) · P2 현금 구간 베이시스 · P3 약세장 숏(비권장). 사용자 선택 P1.
- [22 P1 사전등록](docs/research/22-p1-pair-preregistration.md): 비율 20·60·120 부호 평균 × min(15%/σ60, 0.5), 같은 명목 롱숏,
  1일 지연·5%p 밴드·다리별 0.095%·funding 실측, 2018~2026-08 1회. `backtest/pair_trend.py`(테스트 7건), `scripts/run_p1.py`(테스트 8건). 커밋 `5b85553`.
- **P1 결과: REJECT.** 샤프 0.32, 하한 음수, 2022~2026 음수, MDD 34.6%. 비용이 가격 수익의 절반. BTC와 상관 0.02(시장 중립은 확인).
  T/v2+P1 50/50 샤프 1.12 < T/v2 단독 1.20. 기록: 22 §5, RESEARCH_HISTORY P1 항목.
- (앞선 결정) 새 연구는 잠시 멈추고 Paper 4주 운영 뒤 점검. 2026-10-24 10:00 KST 알림 예약(Claude 앱 예약 작업).
  점검 항목: Paper status·누락·DEGRADED, 연구 시뮬레이터와 일치, T/v1·T/v2·F 전향 월간 갱신, Phase 6 게이트 판정.

## 이전 작업 (Claude — T/v2 실행 인프라 착수)

- 사용자 결정: T/v2 전향 관찰 + 현물 주문 기능 준비(1번) 먼저, 끝나면 새 선물 연구(2번).
- 발견: `exchange/`·`execution/` 패키지가 비어 있다 — 선물·현물 모두 주문 인프라가 없다. 1번 = 실행 인프라 신규 구축.
- 사용자 승인: T/v2는 Phase 4 완전 통과 전 **Phase 5·6(Paper, 실제 돈 없음)** 진행 예외. 실거래는 별도 승인.
- Phase 2 완료: `SpotTrendConfig`, `features/tsmom.py`, `strategy/spot_trend.py`(테스트 16건: 연구 일치·재현·누출·fail-closed·주문 계획).
- Phase 5 1~3 완료: `exchange/spot.py`(계약)·`fake_spot.py`(장애 주입), `execution/spot_orders.py`(결정론적 ID·선기록·
  UNKNOWN은 조회로만 해소·거래소에 없으면 NOT_PLACED로 닫고 재전송 안 함·미해소 시 신규 주문 금지),
  `storage/sql/004_spot_orders.sql`·`repositories/spot_orders.py`. 테스트 16건: 100회 → 1건, 재시작 10회 복구, hypothesis 무작위 장애.
- Phase 5-4: `config/secrets.py`(.env 로더, 값 가림), `exchange/binance_spot.py`(읽기 전용: 서명·시계 보정·잔고·필터·가격·
  주문 조회·키 권한, 주문 전송 항상 거부), `scripts/check_spot_account.py`(읽기 외 권한·IP 미제한이면 중단). 테스트 12건.
- 실계좌 점검 결과: 사용자 키에 **현물 거래·선물 권한이 켜져 있어** 스크립트가 중단(잔고 조회 안 함). 키·시크릿이 대화에
  노출됨 → 사용자에게 키 삭제 후 읽기 전용 새 키 발급 요청. `.env.example`에 들어간 키는 `.env`로 옮기고 틀 복원(커밋 없음).
- 새 키(읽기 전용·IP 제한) 점검 통과: 실계좌 잔고·BTCUSDT/ETHUSDT 규칙(step·최소 수량·최소 주문 5 USDT) 조회 성공,
  PC 시계가 서버보다 약 1.5초 차이 → 어댑터가 보정. **Phase 5 게이트 항목 전부 충족(현물).**
- Phase 6 Paper 실행기: `execution/paper_t2.py`, `storage/sql/005_paper_days.sql`·`repositories/paper_days.py`,
  `scripts/run_paper_t2.py run·status`, `BinanceSpotReadOnly.closed_daily`(공개 시세, 키 불필요). 테스트 6건(연구 일치 포함).
  첫 실행: 결정일 2026-09-22, NO_PRIOR_DECISION, 다음 목표 BTC 0.353·ETH 0.256, 가상 자본 10,000 USDT.
- 다음: 사용자가 작업 스케줄러 등록 → 매일 실행·주 1회 `status` 점검 4주. 그 뒤 1번 마무리 → 2번(새 선물 아이디어) 연구.
  그 다음 Phase 6 Paper 실행기(일 1회 결정·가상 체결·일일 보고).

## 이전 작업 (Claude — 선물 롱숏 후보·V 사전등록)

- [17 후보 비교](docs/research/17-futures-long-short-candidates.md): V 변동성 돌파 거래 4,104건·증명 가능 38.6bps(통과), 돈치안 111건(탈락).
  레버리지는 우위를 만들지 않음. 사용자 선택 V.
- [18 V 사전등록](docs/research/18-v-breakout-preregistration.md): k=0.5, 당일 종가 청산, 동시 돌파일 제외, 비용 24bps,
  V1 평균−2SE>0 · V2 두 절반 양수. `backtest/vbreak.py`(테스트 6건), `scripts/run_v_breakout.py`(테스트 3건). 커밋 `d3de043`.
- V/v1 표면 PASS(+56.5bps)는 **무효**: 양방향 돌파일 제외가 그날 전체 고저를 보는 미래 정보였다(18 §5).
  V/v1.1: 1시간봉으로 먼저 닿은 쪽 진입, 판별 불가 시 나쁜 쪽 결과. 현물 1h 2017-08~ 적재, 테스트 추가(18 §6). 커밋 `e46f31d`.
- **V/v1.1 결과: REJECT.** 순손익 −0.5bps(하한 −10.9), 뒤 절반 −10.4, 비용 전 +23.5 ≈ 비용. 기록: 18 §7, RESEARCH_HISTORY V 항목.

## 이전 작업 (Claude — T/v2 현물 롱 등록)

- 사용자 결정: 현물 롱 v2 진행, 이후 선물 롱숏 연구 계속. A1은 그대로 둔다(정정 재실행 안 함).
- [16 T/v2](docs/research/16-t2-spot-long-registration.md): `TrendConfig(long_only=True)` 고정, 전향 2026-09~2027-08만 판단.
  매달 MDD 25%면 중단·재검토, 12개월 뒤 MDD ≤ 25%·50/50 보유보다 작은 MDD·재현 오류 없음 → 소액 실거래 검토 자격(승인 필요).
- `run_t_trend.py forward`가 v1·v2·50/50 보유를 함께 보고.

## 이전 작업 (Claude — 위험 한도 정정)

- 사용자 정정(2026-09-24): "10% 손실"은 **포지션당**(증거금 10%, 손절 −10% → 계좌 1%) 규칙이었다.
  원문(archive 13-decisions D4)에 없던 계좌 누적 DD 10% 영구 정지가 `[확정]`으로 붙어 있었다.
- 사용자 선택: 계좌 누적 DD 정지 **10% → 30%**(도달 시 HALTED, 사용자 확인 후 재개), 일 손실 2%·연패 4회 **유지**(출처 명시),
  현물은 **변동성 목표**(종목당 15%, 비중 ≤ 50%, 손절 없음).
- 스키마 기본값·base.yaml·TRADING_CORE·CURRENT_SPEC·BACKTEST_AND_TESTING·15 §7·RESEARCH_HISTORY 정정, 게이트 테스트 갱신.
  A1은 재실행하지 않는다. T/v1 MDD 25.4%는 30% 안.

## 이전 작업 (Claude — 추세 추종 검정력·T 사전등록)

- [14 검정력 검토](docs/research/14-trend-power-review.md): 표본은 기간. 2년 → 최소 샤프 2.0, 현물 9년 → 0.95.
  상위 20 선물 상관 0.38~0.63 → 유효 독립 약 2종목. 사용자 선택: 진행·롱숏·표준형.
- 현물 BTC·ETH 1d 2017-08~2026-08, ETH funding 적재. `backtest/trend.py`(테스트 11건), `scripts/run_t_trend.py`(테스트 7건).
- [15 T 사전등록](docs/research/15-t-trend-preregistration.md): 20·60·120 부호 평균 × min(15%/σ60, 0.5), 1일 지연, 밴드 5%p,
  롱 현물·숏 선물 1x, 2018~2026-08 1회. T1 평균−2SE>0, T2 절반별 양수, 청산 0. 커밋 `4ef4458`.
- **T 결과: PASS.** 샤프 0.95·CAGR 21.2%·MDD 25.4%, 2018~21 샤프 1.50 → 2022~26 0.46. 수익은 거의 롱(현물).
  보고: 롱 전용 샤프 1.20·MDD 15.9%, 50/50 보유 샤프 0.64·MDD 87.9%. 기록: 15 §7, RESEARCH_HISTORY T 항목.
- 다음(사용자 결정): 전향 관찰 운영, 롱 전용 v2 사전등록 여부, DD 10% 규칙과 변동성 목표 정합, 현물 주문 기능.

## 이전 작업 (Claude — 레버리지 범위·M·F)

- 레버리지 허용 범위 **1x~10x** 명시, 스키마 상한 20→10 (`cfc2b91`). 실거래 단계(3x·live 5x)는 유지.
- [11 후보 비교](docs/research/11-post-k-candidate-comparison.md) → 사용자 선택 **M + F** (L 제외).
- F: [12](docs/research/12-f-k-forward-registration.md), `scripts/run_k_forward.py` — K/v1 동결, 2026-09~2027-08, 12개월 뒤 판정 (`0e285f9`).
- M: `scripts/m_data.py`(선물 1d 2019~2022 보강, 시점별 유니버스 상위 20·현물 필수, 필요한 달만 1h·funding 적재),
  `backtest/multi_carry.py`(테스트 10건), `scripts/run_m_carry.py`(테스트 6건), [13 사전등록](docs/research/13-m-multi-carry-preregistration.md).
  적재율 99.3~100%, 종목-달 funding SD 약 300~360bps → 검정력 여유 적음(결과 전 기록). 커밋 `2197fc2`.
- **M 결과:** TRAIN 연 **3.65%** < 5% → **REJECT**(월간 하한·연도별·MDD 1.25%·청산 0은 통과). funding 1,225 − 비용 505(41%).
  거래대금 순 대조군 3.08%. VALIDATION·STRESS 미실행. 기록: 13 §10, RESEARCH_HISTORY M 항목.
- 캐리 계열 정리: 실재하지만 안전한 레버리지에서 자본 대비 연 2~5%. F는 매달 `scripts/run_k_forward.py`로 갱신.

## 이전 작업 (Claude — K 후보 선정·사전등록·시뮬레이터)

- 원격 WA 커밋 3개에서 Claude 공동 작성자 줄 제거(트리 동일, `--force-with-lease`): `157082e`·`8b45853`·`95ab7e8`.
- [09 후보 비교](docs/research/09-next-candidate-comparison.md): K 캐리 · L 지연 캐스케이드(60분, 비용 21bps, 2h 33bps 필요) · P 중단. 사용자 K 선택.
- 현물 BTCUSDT 1h 58,401봉 적재(`scripts/backfill_spot.py`, `binance_spot`, 점검으로 잘린 봉 제외 → 16개 짧은 결측).
  `ArchiveFile.market`(기본 선물) 추가.
- [10 K 사전등록](docs/research/10-k-carry-preregistration.md) 고정: 2배·현물 2/3, 21회 평균 ≥0.005% 진입·<0 해제,
  T+1h 체결, ±20% 재조정, K1 연 5%·K2 월간·K3 연도별·K4 MDD 5%·청산 0. funding 평균 미열람(SD만).
- `backtest/carry.py` 순수 시뮬레이터(테스트 14건), `scripts/run_k_carry.py`(1회·순서 안전장치, 테스트 5건). 커밋 `d8ec352`.
- **결과:** TRAIN 연 6.28%·MDD 1.4% → PASS. VALIDATION 연 **1.97%** < 5% → **REJECT**(K2~K4·안전 통과).
  STRESS 2020~2022 연 11.1%·MDD 2.3%·청산 0. 수익은 funding 국면에 비례(2021 23% → 2025~26 2~3%).
- 안전 발견: 상시 보유 대조군이 2021-07-26 선물 1h +31% 급등에 2배 숏 청산. 실거래라면 1배/포트폴리오 마진 필요.
- 기록: 10 §10, RESEARCH_HISTORY K 항목, CURRENT_SPEC. 원자료 `reports/k/`(미추적).
- 다음: 사용자 결정. 임계·레버리지 조정으로 K 재판정은 금지(10 §4).

## 이전 작업 (Claude — WA TRAIN 1회 실행·판정 기록)

- 커밋 `8b45853`(당시 `f7f5c6b`, 트리 동일)에서 `scripts/run_wa_train.py run` 1회 실행(우위 측정 모드, 2023~2024, 재생 4회).
- 거래 롱 210·숏 240(관문 176 통과). 롱 비용 전 −4.05bps → 방향 숏.
  숏 순평균 −23.23bps(하한 −39.74), 비용 전 −4.18bps, 대조군 대비 −1.78bps, 2023·2024 모두 음수.
  **T1~T4 실패, 강제청산 0 → `REJECT`.** 계좌 재생 NOT_RUN. VALIDATION 미실행(규칙상 불필요).
- 롱·숏 모두 비용 전 ±4bps: 상태 조건에 6h 방향 정보 없음. 실제 비용 약 20bps(대리치 15.8보다 큼).
- 기록: [08 §10](docs/research/08-wa-preregistration.md), [RESEARCH_HISTORY](docs/current/RESEARCH_HISTORY.md) WA 항목,
  CURRENT_SPEC 상태. 원자료 `reports/wa/train/`(미추적). 푸시하지 않음.
- 다음: 새 수익원 후보 탐색은 사용자와 방향을 정한 뒤 진행. WA 파라미터 조정·WA2는 금지(08 §7).

## 이전 작업 (Claude — WA 엔진 인계·우위 측정 모드·판정·실행기)

- 사용자 위임(2026-09-24): WA는 승인 없이 커밋·진행, 결정은 최선/차선 선택. 푸시는 하지 않는다.
- astra의 미커밋 엔진 연결(1h 평가·다음 1분 체결·6h 시가 만기·시가 갭 손절 우선·WAState)을 점검해 이어받음.
- BACKTEST 전용 **우위 측정 모드**: 계좌 정지(DD·일 손실·연패·쿨다운)와 G6만 생략, 나머지 게이트 유지.
  다른 모드에서는 무시, 엔진은 BACKTEST가 아니면 거부. 커밋 `157082e`.
- `backtest/wa_verdict.py`(관문→방향→T1~T4·안전, 테스트 5건), `scripts/run_wa_train.py`(재생 4회, 테스트 3건).
- [08](docs/research/08-wa-preregistration.md) 고정 + §9 위임 결정 기록. 스모크: 90일 신호 33건(전부 달력 부재 G6 차단).

## 이전 작업 (Codex — WA 순수 전략 코어)

- 시작 HEAD `6b15864`, 작업 트리 깨끗함. 이전 횟수 제한 제거 커밋을 보존했다.
- `WAConfig`: 고정 창/임계/보유/잠금/손절 배수, 명시 방향, 대조군 옵션.
  BACKTEST 전용·REDUCE_POSITION·목표가 없음 검증. 기본 H1 설정은 변경하지 않았다.
- `features/wa.py`: 24h 하락·168봉 범위 하위 20%, 24개 단순 수익률 표본 SD(ddof=1),
  명시적 `WAState`로 12h 잠금. 마감봉·연속 창 검사, 중복/역순 평가 무효화.
  어댑터는 진입 거부/보유 여부와 무관하게 매 시간 evaluate_wa 반환 상태를 저장·복구해야 한다.
- `decide()`에 WA 연결. 방향별 실제 stop과 기존 사이징/비용/Risk/보호주문을 사용한다.
  6h 보유 청산은 데이터 오류·HALTED에도 가능. A1의 진입 경로는 공통 helper로 추출했고
  원래 계산식과 골든 기대값을 바꾸지 않았다. 방향 선택/판정은 아직 구현 전이다.
- **엔진 연결은 다음 작업:** 1h 평가·WAState 관리·6h 정확한 체결·요구 데이터 창이 미연결이다.
  잘못된 기존 주기로 실행되지 않도록 BacktestEngine은 WA를 명시적으로 거부한다.
- G6/계좌 정지 분리 모드는 아직 미구현. 실데이터/DB/성과 열람·TRAIN 실행 없음.
- 검증: 전체 pytest 1,258건 통과. 이후 추가한 측정식 대조/롤링 창 테스트까지 포함해
  WA 36건 재확인, leakage 46건 통과. mypy strict 157파일, ruff 187파일,
  import-linter 4/4 통과. 기존 골든 기대값 변경 없음.

## 이전 작업 (Codex — 횟수 제한 정정·WA G6 선택 b 반영)

- 인계 시 HEAD `c3f41b6`, 작업 트리 깨끗함. G6 101건 적재·A1 1회 실행/종료,
  워뇨띠 v1~v3 탐색, WA/WB 비교, WA 초안 및 UTC 일 초기화 커밋을 확인했다.
- 두 2회 제한은 초기 `688700a`의 config부터 존재. 저장소의 `[확정]` 표기만으로
  사용자 승인이라고 판단할 수 없다. **2026-09-24 사용자 명시 정정이 우선**한다.
- `daily_trade_limit`·`same_direction_limit`의 schema 기본값과 base.yaml을 **null(제한 없음)**로
  변경했다. Risk는 null일 때 해당 두 거부만 생략한다. 양수 설정 지원·카운터는
  과거 재현용으로 유지하며, 명시적으로 켜지 않는 한 모든 운용 모드에서 비활성이다.
- [08](docs/research/08-wa-preregistration.md)에 **G6 선택 (b)** 승인 기록:
  연구 측정에서 G6 분리, 전체 규칙 계좌 재생·실거래에는 적용. WA 측정 모드 구현은 다음 작업이며
  현재 라이브 G6는 변경하지 않았다. 계좌 재생 미실행을 안전성 통과로 표시하지 않는다.
- WA 숏 기록을 롱 손절 결과의 단순 부호 반전으로 계산하는 문구를 정정했다.
  방향 선택 후 숏의 손절·체결·비용 경로로 재생해야 한다. 수익 통계는 열람하지 않았다.
- A1은 기존 커밋/설정에서 종료한 연구로 유지. 변경된 설정으로 재실행하지 않았다.
- 검증: 전체 pytest **1,221건 통과**, leakage **44건 통과**. 최종 요약 출력/설정 테스트
  수정 후 config·risk **151건 재확인**. mypy strict 154파일, ruff check 및 format 184파일,
  import-linter 4/4 통과. 기존 골든 기대값은 변경하지 않았다.

## 이전 작업 (Claude — 결정 반영·WA 사전등록 초안)

- 사용자 결정: 후보 **WA**, 같은 방향 제한 **UTC 일 단위 초기화**, 우위 측정과 계좌 정지 **분리**.
- `BacktestRiskTracker.advance`가 UTC 일자 변경 시 `same_direction_entries`·`last_entry_side` 초기화(테스트 추가),
  [TRADING_CORE](docs/current/TRADING_CORE.md) 빈도 행에 정정 사유 기록. 종료된 A1은 재실행하지 않음.
- 손절 적용 SD(2σ·√6, A1 배수 재사용): 롱 206.1·숏 200.7bps, 손절 체결률 롱 15.3%·숏 8.1% —
  **비대칭이 방향 정보를 약하게 노출**했음을 08에 기록. 최소 효과 TRAIN 54.4·VALIDATION 53.9bps.
- [08 WA 사전등록 초안](docs/research/08-wa-preregistration.md): 거래 < 176 → DATA_INSUFFICIENT, 방향은 TRAIN 부호,
  T1·T2(비용 전 ≥60bps)·T3(무조건 같은 방향 대조군)·T4(2023·2024)·강제청산 0. 연구 모드는 계좌 정지 비적용.
- **사용자 확인 대기:** 08 고정 여부, G6 2023~2026 데이터 처리 (a) 원천 감사 반복 / (b) 연구 모드에서 분리.

## 이전 작업 (Claude — 07 워뇨띠 후보 비교)

- [07 후보 비교](docs/research/07-w-candidate-comparison.md), `scripts/measure_w_candidates.py`(테스트 4건,
  수익 평균 미열람). 상태 고정·방향은 TRAIN에서 읽는 설계: **WA 하락 뒤 7일 저점 부근**, **WB 큰 움직임 직후**.
- 측정: 트리거 WA 949·WB 901(2020~2026), TRAIN 2023~24 각 256·271, 비용 대리치 15.8·18.4bps(1분 움직임 1.36·1.88배),
  6h SD 212·239bps, G5 변동성 거부 10·8%.
- 필요 효과(6h, 검정력 80%): WA TRAIN 53.4 / VALIDATION 53.0 / OOS100 76bps. WB 59.7 / 62.1 / 86bps.
- **발견:** `same_direction_limit=2`는 반대 진입 전까지 초기화되지 않아 **한 방향 전략은 전 기간 2번만 진입 가능**.
  원 설계에 초기화 기준·[확정] 표시 없음. 계좌 DD 정지와 우위 측정 분리도 사전 결정 필요(A1 교훈).
- 권고 WA·6h(비용의 3.4배 문턱), **사용자 결정 대기**: 후보 선택·same_direction 의미·계좌 정지 분리 방식.

## 이전 작업 (Claude — 워뇨띠 v3 시장 상태)

- `explore_wonyotti_trips.py`에 진입 명목가·거래당 bps 추가(테스트 보강). `scripts/explore_wonyotti_context.py`
  (Binance 1분봉·funding 읽기 전용, 진입 이전 값만, 테스트 3건).
- 거래당 순수익 평균 21 → 31 → 43 → **103bps**(2018→2021), 승률 65~78% — 그 계좌의 테이커 비용 포함.
- 2020~2021 진입 390건: 숏·롱 모두 **직전 24h 하락 뒤**(상승 뒤 비율 41·40% vs 기준 55%).
  "과열 끝 역추세 숏"은 **보이지 않음**(숏 7일 고점 부근 21% < 기준 28%). 롱 7일 저점 부근 111bps(41건, 사후 구분).
- 손실 거래 역행 중앙값 4.0%, 0.3% 미만 4.7%, 1시간 안 정리 46% → 요약의 손절 수치는 2020~2021에 **불성립**.
- 가설 초안 W1 하락 흡수 롱 · W2 하락 지속 숏 · W3 크기·보유 규율([06 §6](docs/research/06-wonyotti-exploration.md)).
- 다음: 07 정식 후보 비교(빈도·트리거 시점 SD·비용·검정력·계좌 정지·노출 구간, 수익 평균 미열람).

## 이전 작업 (Claude — 워뇨띠 v2 왕복 재구성)

- `scripts/explore_wonyotti_trips.py`(테스트 5건): XBTUSD 체결 941,007건 → 왕복 2,589건.
  재구성 순손익 2,007.99 BTC vs 지갑 XBTUSD 실현손익 2,007.08 BTC(차이 0.05%)로 재구성 검증.
- 연도별: 보유 중앙값 12분(2018) → 15.5시간(2021), 잔고 대비 배율 중앙값 3.8 → 0.72배, 2배 초과 64% → 5.6%.
  XBTUSD 숏 1,263 · 롱 745 BTC(2021 숏 663 vs 롱 114). 4배 초과 구간 누적 −368 BTC.
- 요약 주장 대부분 재현. 불일치: 최다 수익 배율 구간(요약 1~2배, 실제 2~4배), 승·패 보유 수치(방향만 일치).
  2021-05-19 −44%는 일일 정산 때문에 5/20 행에 기록됨. 2018 청산은 (날짜·상품) 24건·3월 9건.
- 지갑 잔고 열 18%가 스프레드시트 지수 표기로 정밀도 손실(배율은 근사). [06 §4](docs/research/06-wonyotti-exploration.md).
- 다음: v3 — 진입 직전 시장 상태(움직임·변동성·funding)와 거래당 bps 수익, 2020~2021 역행폭(Binance 1분봉).

## 이전 작업 (Claude — A1 종료·워뇨띠 탐색 시작)

- **A1 종료 (사용자 결정 1):** [RESEARCH_HISTORY](docs/current/RESEARCH_HISTORY.md)에 A1 `DATA_INSUFFICIENT` 기록
  (계좌 DD 10% 정지로 표본 절단, 손익 통계 미열람, 재시도 금지 사항). [BACKTEST_AND_TESTING](docs/current/BACKTEST_AND_TESTING.md)에
  "예상 표본에 계좌 정지·G5 상호작용 포함" 교훈 절 추가. CURRENT_SPEC 상태 갱신. 02 상태 `종료`.
- **워뇨띠 탐색 시작:** [06 탐색 문서](docs/research/06-wonyotti-exploration.md) — 목적(가설 생성, 복제 금지),
  파일 SHA-256, 노출 기록(2018-03~2021-12), `90일 서한` 연구 제외.
- `scripts/explore_wonyotti.py`(표준 라이브러리, 11초, 테스트 2건): 유튜브 요약 주장 재현 —
  실현손익 3,537.32 BTC·37/46개월·상위 20일 62.4%·상위 50일 제외 −245 BTC·지정가 97.2%·시장가 5회·스톱 43·
  출금 2,814.5 BTC **일치**. "2019 이후 청산 0회"는 **부정확**(XBTUSD 소액 청산 2019-09-24·2020-03-12).
  요약에 없던 사실: 2018~2020 체결의 절반 이상이 테이커(편도 7.5bps), 수수료 순지출 35.6·24.1·40.2 BTC,
  2021만 리베이트 우위 −22 BTC.
- 다음: v2 XBTUSD 포지션 재구성(보유시간·배율·롱숏 손익·손절 행동) → v3 시장 상태 결합 → 가설 후보 최대 3개 비교.

## 이전 작업 (Claude — A1 TRAIN 1회 실행)

- 사용자 승인으로 커밋 `916bbf2`의 깨끗한 트리에서 `run_a1_train.py run` 1회 실행(주 arm 34분·대조군 39분).
- **결과: `DATA_INSUFFICIENT`** — 주 arm 실제 거래 **102건** < 146(§11), 대조군 45건. T1~T5 미계산·미열람.
- 거부 사유 집계(판정 파일)만 확인: 주 arm `SAME_DIRECTION_LIMIT` 194·`VOL_TOO_LOW` 153·**`MAX_DRAWDOWN` 125**,
  대조군 **`MAX_DRAWDOWN` 1,014**·`SAME_DIRECTION_LIMIT` 637. → **두 arm 모두 누적 DD 10% 정지로 표본이 잘렸다.**
  이 집계가 성과 방향을 간접 노출했음을 [02 §12](docs/research/02-a1-preregistration.md)에 기록.
- 원인: 예상 표본(162)이 계좌 수준 정지를 반영하지 않은 설계 결함. `reports/a1/train/`(gitignore)에 판정·원거래 보관.
- **A1 후속 방향은 사용자 결정 대기.**

## 이전 작업 (Claude — A1 TRAIN 판정·실행 스크립트)

- `src/auto_trading/backtest/a1_verdict.py`: 02 §7·§11 판정을 상수로 고정. **주 arm 거래 146건 미만이면
  통계 함수를 호출하지 않고 `DATA_INSUFFICIENT`**. 이상이면 T1(순손익 평균−2SE>0)·T2(비용 전 평균≥42.44)·
  T3(대조군 대비 차이−2SE>0, Welch)·T4(2021·2022 각 평균>0)·T5(LONG·SHORT 각 평균>0 = 원문
  "각각 부호 동일"을 T1과 결합한 동치)·강제청산 0 → `PASS`/`REJECT`. 테스트 7건.
- `scripts/run_a1_train.py`: `run`은 dirty 트리·기존 판정 파일이 있으면 거부(TRAIN 1회),
  DB `macro_events`가 매니페스트와 정확히 같을 때만 달력 주입, 주 arm·대조군 같은 엔진·데이터로 재생,
  판정·메타데이터(git·config·매니페스트 digest·필터·veto)·원거래를 `reports/a1/train/`에 기록. 테스트 3건.
  `smoke`는 달력 미주입(G6 fail-closed)으로 거래 0건을 확인하며 속도만 잰다.
- smoke 7일: 판단 2,017회·16초(≈8ms/회) → **TRAIN arm당 약 28분, 두 arm 약 1시간** 예상. 거래 0건 확인.
- **TRAIN은 아직 실행하지 않았다.** 사용자 확인 후 커밋된 깨끗한 트리에서 `run` 1회.

## 이전 작업 (Claude — G6 일정 DB 적재·외부 자료 접수)

- 사용자 승인으로 `build_g6_calendar.py load --apply`: `data/trading.db`의 `macro_events`에 **101건 적재**
  (한 트랜잭션, 적재 전 0건·프로세스 없음·WAL 0바이트 확인). 재 dry-run 동일 101·충돌 0,
  저장소 읽기 결과 = 매니페스트 일정, 표본 달력 판정 9건 기대대로. 7.6GB DB 전체 백업은 하지 않았다.
- **외부 자료 접수(분석 전):** 사용자가 워뇨띠 BitMEX 내보내기를 `data/external/wonyotti/`(gitignore)에 저장.
  체결 CSV 4개(2018-03~2021-12, 약 149만 행, `transacttime` μs, XBTUSD·ETHUSD·선물·funding 행 혼재),
  지갑 CSV(4,388행, 시각 열은 엑셀 손상·날짜 정상), 유튜브 요약·서한 txt(2차 자료). **형식만 확인, 손익 미열람.**
  사용자 지시: A1 판정 후 연구. 분석 시 2018~2021은 해당 파생 가설의 **노출 구간**이 되므로
  검증은 2023 이후·전향 구간에서 한다.

## 이전 작업 (Claude — G6 달력 매니페스트·적재 dry-run)

- 감사 CSV 4개를 `data/macro_sources/audits/`(gitignore)에 모으고, BEA 감사기에 원문 SHA-256 열 추가
  (재실행 23 MATCH·1 RESOLVED 동일).
- `src/auto_trading/backtest/calendar_manifest.py`: 항목·범위·공백을 고정하는 `CalendarManifest`,
  digest = `CalendarCoverage.source_digest`, 파일 digest 불일치 시 거부. 테스트 5건.
- `scripts/build_g6_calendar.py`: `build`(감사 규칙 검증 → [매니페스트](config/macro_calendar/a1_train_g6_2021_2022.json))
  · `load`(기본 dry-run, `--apply`만 DB 쓰기). 테스트 5건.
- 매니페스트: TRAIN 안 FOMC 16·CPI 24·PCE 24·NFP 24·POWELL 13 + 공백 3일,
  digest `aaa3de73…6e97`. **dry-run: `data/trading.db`에 추가 예정 101건, 충돌 0, DB 변경 없음.**
- **다음: 사용자 승인 후 `load --apply` → 실행 스크립트에서 DB 읽기 + 매니페스트 digest 대조 → 달력 주입.**

## 이전 작업 (Claude — A1 최소 거래 수 고정·BLS 로컬 감사기)

- 사용자 승인으로 [02 §11](docs/research/02-a1-preregistration.md)에 **TRAIN 주 arm 실제 거래
  146건 미만 → `DATA_INSUFFICIENT`**를 결과 열람 전에 고정. 거래 수를 먼저 확인하고 미달이면
  손익 통계를 계산·열람하지 않는다. 표본을 늘리려고 파라미터·게이트·기간을 바꾸지 않는다.
- BLS: 사용자 승인으로 신원을 밝힌 User-Agent를 1회 시험 → 403, 차단 본문에 "이용 정책 외 bot 금지".
  우회하지 않고 사용자 수동 저장 방식으로 전환. `scripts/audit_bls_schedule.py`(네트워크 없음,
  파일별 SHA-256) + 테스트 8건. 저장 폴더 `data/macro_sources/bls/`(gitignore)를 만들었다.
- **CPI·NFP 완료:** 사용자가 저장한 BLS HTML 50개를 로컬 감사. 파일명을 내용의 엠바고 날짜로
  `cpi_/empsit_MMDDYYYY.htm`로 정리(SHA-256 불변, 안내 목록 50개와 일치). 고용보고서 엠바고 문장의
  보도자료 번호(`USDL-21-0582`)를 파서가 허용하도록 보정·테스트. **CPI 24/24, NFP 24/24 MATCH**.
- **FOMC 완료:** `scripts/audit_fomc_schedule.py` — 회의 전 Internet Archive 스냅샷의 연준 월별
  캘린더 시각(판본)과 성명 보도자료 `For release at`(실제)을 대조, **16/16 MATCH**. 2021·2022 임시회의 없음.
  G6 FOMC = 정책 성명(원 설계), 의사록 제외. 테스트 8건.
- **POWELL (사용자 결정 A: FOMC 기자회견만):** `scripts/audit_powell_press_schedule.py` — 연준 홈페이지
  배너 스냅샷이 블랙아웃 시작 전 시각을 알렸는지 확인. **13/16 ANNOUNCED(모두 14:30 ET)**,
  **3건 판본 없음**(2021-01-27, 2021-06-16, 2022-06-15) → 사용자 결정 (1): 그날 동부시각 하루 전체
  fail-closed. `CoverageGap`/`CalendarCoverage.gaps` 추가(달력 사용 불가 → `EVENT_BLACKOUT`),
  감사기는 승인된 3일만 `GAP`과 공백 구간 출력, 그 밖의 미발견은 실패. 테스트: 달력 3·엔진 1·감사기 8건.

## 이전 작업 (Claude — sol 인계, BEA PCE 일정 감사 완료)

- sol이 토큰 한도로 멈춘 미커밋 작업을 이어받았다: `scripts/audit_bea_pce_schedule.py`,
  `tests/scripts/`, 05 문서 추가분, `tzdata`(Windows) 의존성.
- 2021-11-24 PCE 불일치를 독립 확인했다. BEA 2021 연간 일정표(2021-02-09 수정본)·
  직전 보도자료 예고·공식 PDF가 모두 **10:00 EST**이고 현재 웹 머리말만 08:30이다.
  감사기에 근거 URL과 함께 `RESOLVED`로 기록했다(해결 기록과 시각이 다르면 계속 실패).
  재실행: **23 MATCH / 1 RESOLVED / 0 DISCREPANCY**. TRAIN 내 PCE 발표 24건 전부 대조 완료.
- ruff 규약 정리(로캘 의존 `strptime` 제거, `pairwise`), 테스트 5 → 8건.
- 직전 커밋 `de957ad`의 `tests/storage/test_macro_events.py` fixture 반환 타입을
  `Iterator`로 정정(mypy strict 실패 해소, 동작 변경 없음).
- 시장 DB 삽입·coverage 인증·TRAIN 실행 없음.

## 이전 작업 (Codex — G6 공식 원천 1차 감사·DB 읽기 경로)

- [05 원천 감사](docs/research/05-a1-macro-pit-source-audit.md)에 2021~2022
  FOMC/CPI/NFP/PCE/POWELL 공식 일정의 **행사 시각 대 사전 공개 시각** 차이와
  아직 입증되지 않은 판본·누락 범위를 기록했다. 연준 Powell 사전 공지의
  행사 날짜 수정 사례도 확인했다. 어떤 원천도 아직 전체 기간 coverage로 인증하지 않았다.
- `MacroEventRepository.schedule_revisions()` 추가: `vintage_ms` 상한을 SQL에서
  적용하고 이벤트 키·원천 결측을 거부한다. 읽은 행만으로 `CalendarCoverage`를
  만들지 않는다. 자료형은 Pure Core로 분리했다.
- 시장 DB 삽입·삭제 없음, 실제 A1 TRAIN/VALIDATION/TEST 성과 미열람.

## 이전 작업 (Codex — 사전등록 개정 승인·G6 입력 감사)

- 사용자 승인으로 [02 §11](docs/research/02-a1-preregistration.md)에 **T2 비용 전
  42.44bps**, TRAIN `2021-01-03~2022-12-31`, T4 `2021·2022`를 결과 열람 전에
  고정. T1·G5 등 기존 게이트는 유지. 원문과 [03 감사](docs/research/03-a1-pretrain-audit.md)는
  변경 이력을 위해 보존했다.
- 체결 시뮬레이터가 다음 1분 기준가를 거래에 기록하도록 확장했다.
  슬리피지·수수료·funding 전 `pre_cost_pnl/bps`와 실제 비용 후 `net_bps`를
  같은 진입 기준 명목가로 재현할 수 있다. 체결 가격·손익 계산 자체는 유지.
- [04 G6 준비 상태](docs/research/04-a1-g6-readiness.md): 승인 TRAIN의 가격봉
  1m/5m/1h은 기대 그리드 개수와 일치, 2021~2022 funding 2,190건이며
  9시간 초과 공백 없음. 그러나 `macro_events` **0건**이고 검증된 coverage도 없어,
  G6를 우회하지 않고 TRAIN을 **DATA_INSUFFICIENT**로 보류. 이후 순수 연결은 추가됨.
- 합성 A1 통합 테스트에서 다음 1분 체결·2시간 청산·funding 결측·
  이벤트 일정 결측 fail-closed를 확인했다. 실제 TRAIN 손익은 실행하지 않았다.

## 이전 작업 (Codex — 2020 데이터 감사와 A1 엔진 경계)

- DB 읽기 전용 확인: Binance USD-M BTCUSDT 1h·5m 첫 봉은
  `2020-01-01 00:00 UTC`, 2019년 1h 데이터 0개. A1 G5 스냅샷 요구
  8,824개 1h 봉의 최초 충족은 `2021-01-02 15:59:59 UTC`.
  공식 Binance 공개 데이터 도구도 2020-01-01 이후 아카이브를 안내한다.
- 수익 평균 없이 예상 표본만 추가: 2h 주 arm 2021년 75개, 2022년 87개;
  대조군 155개, 158개. 제안 TRAIN `2021-01-03~2022-12-31`에서는
  **주 arm 162 / 대조군 313 예상**. 실제 G1~G7/체결 후 더 적을 수 있다.
- A1 합성 백테스트 통합 테스트 추가: 동일 `decide()` 경로의 다음 1분 체결,
  2시간 청산, funding 결측 시 진입 거부를 검증. 운영 G5 설정은 변경하지 않았다.
- [03 감사](docs/research/03-a1-pretrain-audit.md)에 데이터 출처·정확한
  warm-up 시각·재등록 제안을 추가. **T2와 TRAIN/T4 변경은 미승인**으로 유지.

## 이전 작업 (Codex — A1 구현 전 감사와 공통 경로)

- [03 A1 TRAIN 전 감사](docs/research/03-a1-pretrain-audit.md) 작성. 02의
  `직전 6h 고가/저가`와 과거 코드의 `직전 6h 종가 극값` 불일치를 찾아
  feature/측정 스크립트를 고가·저가 기준으로 정정. 평균 수익은 보지 않았다.
- 손절을 1%까지 WIDEN하는 기존 A1 설계는 사용자 요구와 충돌. A1에만
  `REDUCE_POSITION` 적용: 2σ 전략 손절은 유지하고 1%는 사이징 분모 하한으로 사용.
  H1 기본 설정은 변경하지 않았다.
- 정정 설계 입력: 원 트리거 1,510개, 2h 거래 가능 **예상** 526개,
  TRAIN/VAL/TEST 예상 227/160/139, 비용 대리치 19.91bps,
  OOS 100거래 총효과 문턱 42.44bps. G1~G7/실제 체결을 포함한 수치는 아님.
- A1 사전 진입 비용 추정은 H1 기본 13bps 대신 20bps로 상향했다.
  성과 미열람 트리거 비용 대리치 19.91bps 이상으로, 사이징에서 보수적으로 사용한다.
- A1을 `features/a1.py` + 공통 `decide()`에 연결. 목표가 없이 2시간 청산,
  H1 기본 경로 유지. 백테스트는 같은 함수/인과적 사전계산 신호를 사용한다.
  미검증 edge 우회는 A1 **BACKTEST** 연구에만 허용한다.
- 추가 테스트: 손절 유지/목표가 없음, 데이터 불량 중 청산,
  신호 미래 무관성, 연구용 edge 우회의 라이브 금지.
- **TRAIN 보류:** 원문 T2의 총/순 효과 단위 충돌 및 G5 365일 warm-up으로
  2020년 실제 평가 불가. 기준을 소급 변경하지 않고 사용자 결정 필요.

## 이전 작업 (Claude — A1 원 사전등록)

- **A1 사전등록 완료** — [02-a1-preregistration](docs/research/02-a1-preregistration.md).
  사용자 결정: 후보 **A** · C8 **선택지 1**. 전략 코드는 아직 만들지 않았다.
- 실행 전 실측 두 가지가 설계를 바꿨다.
  - `min_stop_distance=1%` 가 2h 손절의 **100%** 를 WIDEN → 35.1bps 는 실행 불가능한
    전략의 수치였다. 하한 적용 후 필요 효과 **40.9bps** (비용의 2.14배).
  - `same_direction_limit=2` 가 트리거의 **65%** 를 차단 (H1 을 막았던 그 한도).
    연 279 → **92회**. 표본·검정력을 전부 한도 적용 후 값으로 다시 계산했다.
- 대조군(수축 조건 제거) 정의·판정 고정: 주 arm 이 대조군을 **12.8bps 이상** 앞서야 한다.
  못 넘으면 A1 은 H1 변형이므로 기각.
- C1·C2·C4·C8 을 사전등록 안에서 처리. C2 는 1봉(5분) 지연과 셔플 null 로 분리 확정.

## 이전 작업 (OOS 구속조건 발견)

- **OOS 게이트가 진짜 구속조건임을 발견** ([§6](docs/research/01-candidate-comparison.md)).
  Phase 4 의 `실제 거래 n≥100` 으로 확인 가능한 최소 효과는 A 35.1bps · B 53.0bps 인데,
  TRAIN 은 23.9bps 도 잡아낸다. **23~35bps 구간은 TRAIN 통과 후 OOS 확인 불가.** → C8
- 손절 적용 거래당 손익 SD 실측: A 2h·0.5σ 에서 73.6 → 56.7 (−23%). 도움은 되나 부족.
- 검정력 게이트에 **"TRAIN 과 OOS 중 큰 쪽이 기준"** 규칙 추가. 봉 SD 가 아니라
  거래당 손익 SD 와 Phase 4 거래 수 요건을 쓰도록 명시.
- C7·C8 을 CURRENT_SPEC 미해결 등록부에 정식 등록.

## 이전 작업 (트리거 시점 재측정)

- **트리거 시점 재측정으로 추천이 뒤집혔다** ([§5](docs/research/01-candidate-comparison.md)).
  B(청산 캐스케이드)는 진입 직후 1분 움직임이 전체의 **3.06배**, 2h SD 가 92.9 → 139.9 로
  둘 다 악화. 실측 비용 28.3bps 적용 시 문턱 27.3 → **39.9bps**. **추천을 B 에서 A 로 바꾼다.**
- 19 → 20 (maker 비용 낙관 → 신호 시점 재측정으로 번복)과 같은 패턴이며,
  이번에는 **사전등록 전에** 잡았다.
- 트리거 정의를 `src/auto_trading/backtest/triggers.py` 로 분리 (테스트 21건).
  빈도를 세는 쪽과 SD 를 재는 쪽이 갈라지면 게이트가 조용히 무효가 되기 때문.
- 그 테스트가 결함을 잡았다 — 돌파 봉 자신이 HTF 추세 판정에 들어가 역방향 급변이
  추세를 뒤집었다. 추세를 돌파 직전 기준으로 수정 (A 빈도 282 → 279회, 영향 미미).

## 이전 작업 (후보 비교)

- **후보 3개 비교 완료** — [01-candidate-comparison](docs/research/01-candidate-comparison.md).
  게이트로 C(다일 추세 추종) 탈락, A(변동성 수축 돌파)·B(청산 캐스케이드 반전) 통과.
- BTC 잡음 바닥 실측: 증명 가능한 최소 효과 = `18 + 0.77 × 보유시간(h)` bps. 국면에 강건.
- **변동성 수축은 검정력을 개선하지 않음을 실측** (SD −21%, 표본 −63% → 순손해).
  후보 A 의 근거를 "잡음이 작아서" 에서 "효과가 커야" 로 정정했다.
- 트리거 빈도 실측: A 연 282회 · B 연 269회 · C 연 26회.
- 사용자 선택 대기. 선택 후 **트리거 시점** SD·비용 재측정이 사전등록의 선행 조건.

## 이전 작업 (검정력 게이트)

- 사전등록 **검정력 게이트** 도입: 가용 표본으로 판정할 수 없는 가설은 사전등록하지 않는다.
  [BACKTEST_AND_TESTING](docs/current/BACKTEST_AND_TESTING.md) 의 새 절, 계산은 `src/auto_trading/backtest/power.py`.
- 옛 식 `(2·SD/Δ)²` 가 **검정력 50%** 였음을 확인하고 기본값을 0.80 으로 올렸다 (표본 2.02배).
  H2 재현 검정은 443개가 필요한데 189개로 등록해 검정력 44% 였다 — 설계 단계의 실패였다.
- 후보 비교표에 검정력 판정과 `필요 거래 수 → 도달 기간` 을 필수 항목으로 추가.
- 문서 재편 `2500ac2` 독립 검증: 코드 변경 0, 링크 0건 깨짐, 보관본 본문 일치, C1 실재 확인.

## 이전 작업 (문서 재편)

- 승인된 계획에 따라 Current 5개 문서 작성, 역할별 단일 기준으로 정리.
- 정리 직전 기존 34개 문서의 본문 보관(보관 안내 및 상대 링크만 정리).
- D22 초안과 여러 문서에 분산된 변경을 Archive에 보존하고 현행 전략/로드맵에서 제외.
- 기존 경로는 짧은 안내문으로 유지, AGENTS/CLAUDE/README의 기본 읽기 경로 갱신.
- 위험·검증 기준·연구 결과·storage 정책을 분리하고 구현과 요구사항의 차이를 명시.
- 삭제/DB 변경/코드 변경/커밋/푸시 없음.

## 다음 작업 — Phase 6 운영

1. 매일 09:14 테스트넷 훈련(09-25 ~ 10-08, 26) — 점검 보고에서 실행·통과 일수 확인.
2. 매주 `run_paper_t2.py status`·`run_paper_o1.py status`, 매달 `run_paper_o1.py verify`.
3. **2026-10-24 4주 점검**: 두 Paper 누락·DEGRADED·MISSED, 연구 일치, T/v1·T/v2·F 월간 갱신, Phase 6 게이트 판정.
4. 실거래 전 남은 것: 체결 수수료, 운영 프로세스에 스트림 연결, 주문 전송 경로(Phase 8 승인 뒤), '거래소 보호주문 필수' vs O/v1 '가격 손절 없음' 결정, CURRENT_SPEC 범위 승인.

새 연구는 4주 점검까지 쉰다(앞선 사용자 결정). A1·WA 재실행은 다음 작업이 아니다.

## 단계 상태

| Phase | 상태 |
|---|---|
| 0 | 후보 비교·사전등록 절차 운영 중. 새 연구는 4주 점검까지 보류 |
| 1~3 | 데이터·코어·백테스트 기반 구현. 전략별 게이트는 각 사전등록 기준 |
| 4 | **T PASS**(T/v1, 2018~2026 1회) · **O/v1 PASS**(비용·위험 판정, 이미 본 구간). H1~H3·R1·WA·K·M·V·TA·P1 기각, A1 DATA_INSUFFICIENT |
| 5 | 현물(T/v2) **완료**. **선물**: 가짜 거래소·멱등 주문·재시작 정합성 복구·서명 읽기 전용 어댑터·실계좌 읽기 **완료**. **WS 사용자 스트림 구현·테스트넷 확인**. 주문 전송 경로 미구현(Phase 8 전) |
| 6 | **T/v2 Paper 운영**(2026-09-22~, 현재가 체결) · **O/v1 Paper 운영**(2026-09-23~, 종가 체결·별도 원장). 최소 4주 |
| 7 | **운영 등록**: 매일 훈련 2026-09-25 ~ 10-08 (26). 개발 중 20/20. **실제 스톱 발동 9/9 확인**. 남은 것: WS 수신(부분 체결은 가짜 거래소로 대체) |
| 8~9 | 미착수. 실거래 승인 없음 |

## 미해결

- O/v1-L: 실거래 시 TRADING_CORE '거래소 측 보호주문 필수'와 O/v1 '가격 손절 없음(추세 신호 청산)' 충돌 — 24 §7 은 연구 한정 예외.
- O/v1-I: **대부분 해소** — 선물 인프라·계좌 읽기 완료. 남은 것: 주문 전송 경로(Phase 8 승인 뒤), 체결 수수료 조회, 운영 프로세스에 스트림 연결.
- O/v1-S: **해소** — `AutoTrading_Paper_O1` 등록·수동 실행 0 확인.
- W-S: watchdog 스케줄 등록 **완료**(09:20 Ready). 텔레그램은 `.env` 설정 여부에 따름.
- C1: A1에는 손절 유지/위험 축소 적용. H1 기본 WIDEN은 그대로이며 전략별 정합성 검토 필요.
- C2: 지연 검사 1일/1봉과 유지/소멸 판정 충돌. 게이트 완화 없이 사전 확정 필요.
- C3: 과거 기반 완료 표시와 데이터 시작점/계좌 snapshot 게이트 불일치 감사.
- C4: 무제한 보유의 split 경계/purge/embargo 설계.
- C5: 새 전략·운용 자본 미정.
- C6: 실제 거래소 API/보호주문·복구 구현 및 운영 검증 대기.
- C7: D22 제외가 사용자 의도인지 미확인. 2026-09-22 `요구사항 확정` → 다음 날 `EXCLUDED`.
- C8: **해소** — 사용자 승인으로 T2를 비용 전 42.44bps로 개정.
- A1-G5: **해소** — 2020년 T4 표본 0 확인 후, 사용자 승인으로 2021~2022 TRAIN/T4 재등록. G5 완화 없음.
- A1-G6: **해소** — `macro_events` 101건, 실행 시 매니페스트 digest 대조 후 주입.
  PCE 24·CPI 24·NFP 24·FOMC 16·POWELL 13 대조 완료, POWELL 3일은 승인된 공백(fail-closed).
  POWELL 설정 창(±15분)은 원 설계 "시작~종료+15분"보다 짧다(기록만, 한도 변경 없음). coverage 인증 없이는 TRAIN 불가.
- A1-N: **해소** — 사용자 승인으로 주 arm 실제 거래 146건 미만이면 DATA_INSUFFICIENT (02 §11). 원 분석:
  개정 입력(TRAIN SD 95.64·비용 19.91·효과 42.44bps·검정력 80%)으로 재계산하면 필요 ≈146건,
  예상 162건은 G1~G7 적용 전이다(G5 VOL_TOO_LOW가 수축 국면과 겹침).
- A1-P (2026-09-23 Claude 분석): 트리거 6시간 잠금은 과거 트리거에 의존한다. 백테스트는 전체 구간
  사전 스캔, 라이브 경로는 9,123봉 창 재스캔이라 창 시작점에 따라 달라질 수 있다. 현 테스트는 미래 봉
  추가만 검사. A1은 backtest 전용이라 TRAIN 영향은 없으나 Phase 6 parity 전에 해결 필요.
- C1: **A1 에 직접 영향** — 2h 손절의 100% 가 WIDEN 된다. 현재 설정 그대로 사전등록했다.
- C2·C4: **A1 범위에서 해소** (사전등록 §8). 다른 전략에는 재확정 필요.

## 검증 기록

이번 작업 (Claude — A1 TRAIN 판정·실행 스크립트, 2026-09-23):

- smoke 7일: 거래 0건(G6 fail-closed) 확인, 16초. TRAIN 손익 미실행·미열람.
- 전체 pytest **1,196건**, leakage **44건**, mypy strict **150개** 파일,
  ruff check·format --check **176개** 파일, import-linter 4/4, `git diff --check` 통과.

이번 작업 (Claude — G6 달력 매니페스트·적재 dry-run, 2026-09-23):

- 매니페스트 빌드 2회 digest 동일. `load` dry-run 후 `macro_events` 0건 그대로(읽기 전용 확인).
- 전체 pytest **1,186건**, leakage **44건**, mypy strict **147개** 파일,
  ruff check·format --check **172개** 파일, import-linter 4/4, `git diff --check` 통과.

이전 작업 (Claude — A1 최소 거래 수·BLS 로컬 감사기, 2026-09-23):

- BLS 요청은 사용자 승인 1회만(403). 이후 네트워크 접근 없음.
- 실데이터: FOMC 16/16 MATCH, BLS 로컬 CPI·NFP 48/48 MATCH, POWELL 13 ANNOUNCED·3 GAP(승인된 공백, 종료 0).
- 전체 pytest **1,176건**(스크립트 33·달력/엔진 공백 4건 포함), leakage **44건**, mypy strict **144개** 파일,
  ruff check·format --check **168개** 파일, import-linter 4/4, `git diff --check` 통과.

이전 작업 (Claude — BEA PCE 감사 인계·완료, 2026-09-23):

- 감사기 실데이터 재실행 2회: 수정 전 23 MATCH / 1 DISCREPANCY(sol 결과 재현),
  해결 기록 후 23 MATCH / 1 RESOLVED / 0 DISCREPANCY, 종료 코드 0. 출력은 표준 출력만 사용.
- 전체 pytest **1,147건** 통과, leakage 마커 **44건** 통과, mypy strict **141개** 파일 통과,
  ruff check·format --check **162개** 파일, import-linter 4/4, `git diff --check` 통과.

이전 작업 (G6 공식 원천 1차 감사·DB 읽기, 2026-09-23):

- 원천 감사는 Fed/BLS/BEA 공식 자료로만 수행. TRAIN 손익·시장 DB 변경 없음.
- 거시 일정 저장소 단위·인과성 테스트 5건 추가. 전체 pytest 통과,
  leakage 마커 **44건** 통과, mypy strict **34개** 파일 통과,
  ruff check·format --check **160개** 파일·import-linter 4/4 통과.

이번 작업 (A1 사전등록 개정 및 G6 순수 연결, 2026-09-23):

- 사용자 승인으로 T2를 비용 전 42.44bps로, TRAIN을 2021-01-03~2022-12-31로,
  T4를 2021·2022년 부호 검사로 사전 고정했다. 성과 결과는 열람하지 않았다.
- A1 체결·거래 기록에 슬리피지 전 기준가와 비용 전/후 bps를 분리했다.
  기존 체결·손익 경로는 유지했다.
- G6의 감사 범위·이벤트 공개 시각·일정 수정 판본을 반영하는 순수 달력과
  A1 백테스트 연결을 추가했다. 달력 미주입은 fail-closed. 실제 DB는
  `macro_events` 0건이므로 TRAIN은 미실행이다.
- 전체 pytest 통과, leakage 마커 43건 통과, mypy strict 38개 파일 통과,
  ruff check 통과, import-linter 4/4 통과. 포맷 수정 후
  `ruff format --check .` 157개 파일 통과, `git diff --check` 통과.

이번 작업 (검정력 게이트):

- pytest 1,109건 통과 (power 28건 + triggers 21건 신규), leakage 마커 43건 통과.
- mypy 93개 파일, ruff check/format --check 146개, import-linter 4/4 통과.
- 옛 50% 표(archive 22 §6)의 1,279·370·180·36을 `power=0.50`으로 재현하는 테스트 고정.
- 문서 링크 100개 파일 검사, 깨짐 0. config·전략 코드·시장 DB 변경 없음.

이전 작업 (문서 재편)에서 검증 완료:

- 원문 보관 34개: 작업 전 내용과 대조 일치(상단 안내/상대 링크 변환만 허용).
- Markdown 74개: 로컬 파일 링크 누락 0. git diff --check 통과.
- src/config/tests/scripts 변경 0. 시장 DB를 열거나 변경하지 않음.
- 전체 pytest 1,060건 및 leakage 마커 43건 통과.
- mypy 설정 전체 128개 파일 통과, core/strategy/risk strict 18개 파일 별도 통과.
- ruff check 통과, format --check 144개 파일 통과, import-linter 4/4 통과.
- 초기 Python 실행 접근 거부는 권한 검토 후 기존 환경으로 실행해 해결. 환경 재설치 없음.
- 옛 tests/leakage 명령은 존재하지 않는 경로였음. pytest -m leakage로 재실행하여 통과, 현행 안내 정정.

역사적 개발 이력은 [이전 PROGRESS 원문](docs/archive/design/PROGRESS.md)에 보존했다.
현재 기준과 구현 차이는 [CURRENT_SPEC](docs/current/CURRENT_SPEC.md), 과거 연구는 [RESEARCH_HISTORY](docs/current/RESEARCH_HISTORY.md)를 읽는다.
