# 운영·외부 연동·데이터 보존

기준일: 2026-09-23. 백필/저장 기반은 구현, 주문 연동·운영 자동화·UI·Shadow 파이프라인은 계획이다.
실거래 활성화와 데이터 삭제는 이번 문서 작업의 범위가 아니다.

> **표시 이름 변경 (2026-10-15, 사용자 결정):** Glide → 🌟 **Deneb** · Cruise → 🌌 **Vega** · Falcon(옛 AG4S2) → ☄️ **Altair** (여름철 대삼각형). 같은 날 내부 이름까지 바꿨다: 파일(`scripts/vega_live.py` · `scripts/altair_demo.py` · `ops/deneb_live.py` · `ops/deneb_guard.py` · `ops/altair_split.py`), 설정 키(`deneb.*` · `alerts.vega_*` · `demo.altair`), 정지 그룹(`HALT_deneb` · `HALT_vega`), 장부(`data/toss/DENEB.json` · `data/vega/` · `data/altair_demo/`), 예약(`AutoTrading_Vega` · `AutoTrading_Deneb_Chat` · `AutoTrading_Deneb_Retry`). 옛 정지 파일 · 옛 설정 키 · 옛 명령(`m3` · `m3-chat`) · 옛 스크립트 경로는 계속 읽는다. 주문 번호 앞머리(`M3TQQQ` · `t2b-`), Paper · 연구 기록, 아래 옛 기록 속 이름은 그대로다.

## 실거래 스위치 · 예약 · 정리 (2026-10-16)

- **스위치(.env):** 토스 `TOSS_TRADING_ENABLED` · 바이낸스 현물 `BINANCE_LIVE_TRADING_ENABLED` ·
  바이낸스 선물 `BINANCE_FUTURES_LIVE_TRADING_ENABLED` · 업비트 `UPBIT_TRADING_ENABLED`(기본 꺼짐) ·
  한투 `KIS_TRADING_ENABLED`. 정확히 `true` 일 때만 주문. 환경 변수가 `.env` 보다 앞선다.
- **설정(오르빗):** `live.altair`(Altair 실거래, 기본 꺼짐) · `stock.orders`(💹 개별주, 기본 켜짐) ·
  `demo.altair`(Altair 데모).
- **예약 이름(2026-10-15 이름 변경 뒤):** `AutoTrading_Vega`(매일 09:05) · `AutoTrading_Deneb_Chat` ·
  `AutoTrading_Deneb_Retry` · `AutoTrading_Altair`(5 분마다, 꺼져 있으면 연결도 안 함).
  2026-10-16: `AutoTrading_Vega` 등록 · 옛 `Binance_Cruise` 끔. 옛 `Toss_M3_Chat` · `Toss_M3_Retry` ·
  `Upbit_T2_Live` · `Upbit_T2_Weekly` 는 관리자 권한으로 만들어져 일반 권한으로 못 꺼서, 새
  `Deneb_Chat` · `Deneb_Retry` 는 겹치지 않게 꺼 둠 — 관리자 PowerShell 에서 옛 4 개 끄고 새 2 개 켜기.
- **정지 · 정리:** 텔레그램 '정리'(범위 버튼) · '정리 NVDA'(한 종목) · '정지풀기'. 그룹: vega · deneb ·
  acc · altair · stock. `python -m scripts.liquidate preview <범위|sym:종목>` 은 조회만.
- **💹 개별주:** `/stock` → 종목 답장 → 추천 → [N주 매수] · [수량 바꾸기] · [그래도 매수] → 수량 답장 →
  [예약]. 장부 `data/size_guard/state.json`, 봇이 2 분마다 tick. 명령 `python -m scripts.size_guard
  advise|reserve|status|attach|release|cancel`.
- **Altair 실거래:** `python -m scripts.altair_live plan|status|unhalt` — plan · status 는 조회만.

## 데이터·API·복구

- Binance USD-M 시장 원자료는 출처·수집 시각·기간·심볼/TF·체크섬·적재 결과를 추적한다. 백필은 멱등/재개 가능, checksum 실패와 gap을 보고한다.
- 원천 가격, mark price, funding 간격, OI/포지셔닝 지표의 가용 기간과 시점별 유니버스를 구분한다. 폐지 종목 제외로 생존 편향을 만들지 않는다.
- REST 초기 조회/정기 대조와 WS 실시간 이벤트를 조합한다. rate-limit/backoff, 연결 복구, 누락·중복·역순 처리, 서버 시각 대조를 구현/검증해야 한다.
- API endpoint·주문 제약·listenKey·수수료·요청 제한은 구현 시 공식 문서로 재확인한다. Archive의 과거 수치를 최신 API 계약으로 가정하지 않는다.
- 기동/재연결은 TRADING_CORE의 복구 절차를 따르며 복구 완료 전 신규 진입을 차단한다.
- 상태/손익/주문 저장 실패 또는 디스크 부족은 신규 진입을 막고 위험 이벤트를 알린다. 보호/청산 경로는 계속 허용한다.

## Optional: 거시 일정·뉴스·JEV·LLM

기존 결정론적 이벤트 달력 blackout은 Risk의 별도 안전 입력이다. 실제 데이터 연동 상태를 검증해야 하며 뉴스 AI와 혼합하지 않는다.
Elliott/JEV/News AI는 초기 Shadow 전용: 방향·시나리오·중요도·신뢰도를 기록하되 실제 주문, veto, 사이징, stop/TP에 영향이 없다.
JEV의 의미/공급자/입출력 상세 계약은 미확정이며 추측해 구현하지 않는다.
충분한 전향적 기록 후 사전등록한 비교를 수행하고, 유효성 입증과 사용자 승인 뒤에만 새 전략 버전의 입력으로 승격한다.

LLM은 공급자 비종속 Protocol 뒤에 둔다. 모델명·공급자·prompt_version·schema_version·원문/입력 해시·응답·수집/공개/분석 시각을 보관한다.
temperature=0만으로 재현성이 보장되지는 않는다. 저장된 응답과 입력 해시 캐시를 재생해야 한다.
기사 수정본/발표치 revision은 당시 가용 시각으로 구분하고 실패/timeout을 정상 중립 의견으로 위장하지 않는다.
외부 기사/모델 출력은 신뢰할 수 없는 데이터이며 주문·시크릿·실행 권한을 주지 않는다.

## 배포·보안·관찰 (설계)

- **Cruise 바이낸스 이전 (2026-10-15, 사용자 "cruise는 그냥 바이낸스로 옮기면 돼")**: 실행 `python -m scripts.live_t2_binance plan|run|status|release-dd` (예약 `AutoTrading_Binance_Cruise` 매일 09:05 KST, 사용자 등록). 상태 `data/binance_t2/state.json` · 보고 `reports/binance_t2/`. 거래 키 `BINANCE_LIVE_API_KEY/SECRET`(현물 거래 · 읽기만, 출금 금지, IP 제한) + `BINANCE_LIVE_TRADING_ENABLED=true`. 현물 지갑의 USDT · BTC · ETH · SOL 전체가 전략 돈 — 따로 둘 코인은 Funding 지갑. 업비트 Cruise(`live_t2_upbit`)는 `CRUISE_UPBIT=on` 일 때만 주문(옛 예약은 끈다).
- **데스크톱 실거래 홈 2026-10-09 후속:** `AutoTrading.exe`는 `/live`를 여는 창 실행기이며 화면은 저장소의 서버가 제공한다. exe·바로가기 재빌드 없이 실거래 홈을 자산 요약·구성 그래프·전략별 카드·확인할 사항·최근 기록으로 개편했다. 두 테마·반응형·키보드 안내 접기/카드 이동을 제공한다. UI는 장부와 기존 공개 시세(업비트·야후, 키 없음·60초 캐시)만 읽고 주문/계좌 인증/예약을 하지 않는다. `live_home`의 기존 `ops.t2_upbit` 간접 전략 import를 제거하고 저장된 체결의 수수료 포함 이동평균 원가 표시만 분리했다(기존 장부와 부분매도·전량매도 원가 일치 시험). 실행기·매매·위험 규칙은 불변.
  - 합계의 **기준 금액 = 입금 + 적립 매입원가**, **장부 기준 차액 ≠ 확정 수익**. 원래 장부 계산은 유지하고 명칭·설명만 명확히 한다. 시세/달러 환율 미확인 때 추정 평가 표시와 차액 숨김. 자산 구성은 양수 장부 평가액 비중이며 시세 누락이면 추정 비중, 역사적 수익 곡선이나 실제 계좌 인증이 아니다. Paper·데모·테스트넷을 합산하지 않는다.
  - 체결 대기는 주문 접수 기록이며 실제 완료를 뜻하지 않는다. 매수 정지·시세 결측은 경고, 장부가 없으면 빈 상태이며 잔고 0/정상 운영으로 인증하지 않는다. 정기 일정은 참고일·휴장/잔고/예약에 따른 실행 불확실성을 명시한다. `/live` 브랜드 링크는 실거래 홈으로 돌아가며 다른 메뉴·CSP·테마 쿠키·기존 catchup 예외는 유지한다.
  - 화면 서버만 다시 켜야 새 스타일이 반영된다. 앱 창을 닫아도 별도로 실행되는 매매 프로세스는 종료되지 않는다. UI 원가 표시·안내·차트는 매매 신호·위험 계산 입력이 아니다.
  - 10/09 반영 시도는 기존 8787 UI 서버 종료가 Windows 권한 거부로 실패했다. 따라서 코드·검증 완료와 실행 중 서버 반영은 구분한다. 사용자가 기존 UI 서버를 종료할 수 있는 권한으로 "대시보드 다시 켜기"를 실행해야 하며, 단순히 앱 창을 닫고 다시 열면 기존 200 응답 서버를 재사용하므로 새 화면 반영을 보장하지 않는다. 매매·예약 작업은 재시작하지 않는다.

- **대시보드 2026-10-09 개편(아래 10/04 홈 설명보다 우선)**: 주식 홈·주식·코인·연구·시스템 순서, Glide 등록 설정·토스 적립·주식 Paper 비교를 먼저 표시한다. 저장된 USD Paper 이력의 누적 수익률(첫 평가액 기준)·평가금액·낙폭 및 표시 기간을 CSS로 전환한다. 원장·통화를 합산하지 않고, 기간 필터로 기준액/누적 고점을 초기화하지 않는다. 첫 저장 평가 이전 비용은 수익률 기준에서 제외된다. QQQ 자료·환율·실거래 평가 이력이 없으면 추정하지 않는다. Glide 실거래 수익은 현재 평가 기록 대기, 주식 Paper는 1일 기록이라 2일째부터 선을 그린다. 토스 상세의 개인 계좌 평가손익 기본 숨김은 유지한다. 신규 거래 설정·수집·예약·주문 없음. 바탕화면 다시 켜기 바로가기는 현행 파비콘과 동일한 ICO 사본을 쓰며 실행 경로/인수는 보존한다. 기존 `POST /catchup` 예외와 쿠키·CSP·읽기 전용 경계는 불변.

- 실거래 엔진은 Linux VPS/systemd 기반, 재시작·단일 인스턴스·독립 watchdog·시각 동기화 필요. Windows exe 실거래 금지.
- **2026-09-26 사용자 결정: 수익이 확인되기 전에는 장비를 사지 않고 지금 집 PC(Windows 작업 스케줄러)로 운영한다** ([84](../research/84-home-pc-always-on.md)). 1단계(T/v2 현물, 하루 한 번·fail-closed)는 이것으로 충분하고, 선물 비상 스톱은 거래소에 걸려 PC 가 꺼져도 작동한다. 일일 점검에 **PC 상태 한 줄**(작업 실패·밀림·비정상 종료)을 추가했다. 작업 설정(놓치면 켤 때 실행·깨우기·로그인 없이 실행)은 84 §3 명령을 사용자가 실행한다. 위 VPS 기준은 수익 확인 뒤 다시 본다.
- **대시보드(2026-10-04 UI 개편)**: `python scripts/dashboard.py` → http://127.0.0.1:8787 (끄기 Ctrl+C). 사용자 선택 시안의 상단 탭 **요약·코인·해외 주식·연구·시스템**, 상단 탭을 고르면 그 탭의 화면만 바로 아래 알약 버튼으로 보인다(2026-10-05: 코인 = 전략 비교·포지션, 주식 = 해외 4·국내 2, 시스템 = 실행 현황·테스트넷·위험·데모, 요약·연구는 화면 하나라 없음). 홈은 저장된 Paper 원장 차트·토스 QNDX/BTQ/WQTM 적립 상태·확인할 일·다가오는 예정 시각·최근 기록을 표시한다. 기록 없음/손상/경고를 정상이나 수익으로 꾸미지 않는다. 예정 시각은 스케줄러 등록·성공의 보증이 아니다. 해외 주식 평가 손익은 표시하지 않는다. **라이트(기본)/다크** 선택은 `?theme=light|dark` GET + 브라우저 쿠키(`dashboard_theme`, 1년·Path=/·HttpOnly·SameSite=Strict)로 페이지 이동/새로고침 후 유지한다. 거래 데이터·설정은 쓰지 않는다. `--export <폴더>`는 각 화면의 두 테마 HTML(라이트 기본 파일·다크 `-dark.html`)과 로컬 이동 링크를 저장한다. 별도 프로세스·127.0.0.1 전용·DB mode=ro·API 키 미사용·외부 자원/스크립트 없음(CSP). 주문 버튼 없음·PAPER/TESTNET/DEMO 자산 합산 금지·엔진 중단 권한 없음. 쓰기 요청의 유일한 기존 예외 `POST /catchup`은 아래 규칙을 그대로 유지한다.
- **대시보드 실행 현황·더블클릭 실행(2026-09-26)**: 새 화면 `/running` "실행 현황" — Paper 4개·테스트넷 훈련 2개·그림자·데모 3개·일일 점검을 한 표로(시각·기간·일수·마지막 기록·상태·21:20 점검 요약), 밀림(매일 26시간·상주 5시간)·끝남·실패 표시, PC 상태 줄, 긴급 정지 배너, 다가오는 일정(D-day). `scripts/open_dashboard.cmd` 더블클릭 = 창 없이(pythonw) 띄우고 브라우저 열기, 이미 떠 있으면 브라우저만. 끄기 `scripts/stop_dashboard.cmd`. **바탕화면 바로 가기(2026-10-04)**: '대시보드 켜기'·'대시보드 끄기'·'대시보드 다시 켜기'(`scripts/restart_dashboard.cmd` = 끄기 → 켜기, 새 코드 반영). 여전히 127.0.0.1 전용 — 휴대폰·밖에서 보기는 외부 노출이라 사용자 결정 전까지 하지 않는다.
- **해외 주식 카드·브랜드 아이콘(2026-10-04)**: `/toss`는 QNDX/BTQ/WQTM 카드 + QS/환전 보조 영역 + 접을 수 있는 저장 상세 기록. 적립 계획/주문 동작은 그대로, 초기/손상 기록과 실제 진행률을 구분한다. `ui/assets/auto-trading.svg`(AT+추세선)와 다중 해상도 ICO를 웹 로고/파비콘에 사용한다. `/assets/auto-trading.svg`·`/assets/auto-trading.ico`·`/favicon.ico`만 정적으로 제공하고 HTML 내보내기에도 함께 복사한다. `scripts/build_dashboard_icon.py`는 SVG 도형의 ICO 빌드용(Pillow는 빌드 환경에만 필요). 사용자 요청한 바탕화면 `대시보드 다시 켜기.lnk`는 아이콘/설명만 변경. `scripts/style_dashboard_shortcut.ps1 [-WhatIf]`는 기존 실행 대상 검사·원본 `reports/ui/shortcut-backups/` 백업·실행 정보 보존 확인·실패 시 복구를 수행한다. CMD 실행 경로·예약 작업·매매 설정은 변경하지 않는다.
- **토스 적립에 QS(퀀텀스케이프) 추가(2026-10-04, 문서 118 §7)**: 매일 1 달러·월 31 달러, BTQ 와 같은 방식. 티커가 QS 라 **QS 전략 상태·보고는 `QS_SWITCH`** (`data/toss/QS_SWITCH.json`, `reports/toss/QS_SWITCH/`), `data/toss/QS.json`·`reports/toss/QS/` 는 퀀텀스케이프 적립이다. 대시보드 카드 4개.
- **TradFi 무기한 호가 스냅숏(2026-09-26, 문서 86 §6, 선택·임시)**: `python -m scripts.measure_tradfi_perp snapshot` 을 평일 미국 정규장 시간에 반복해 `reports/tradfi_perp/snapshots.jsonl` 에 쌓는다(공개 API 읽기만, 주문 없음). 종목마다 10개·5일 이상 모이면 `report` 로 C1 판정. 모이면 작업을 지운다.
- **예약 작업 창 없이 (2026-10-04)**: 작업 동작을 `pythonw.exe scripts	ask_launcher.py <원래 인자>` 로 바꾸면 검은 창이 뜨지 않는다. 출력은 `reports/tasklog/<날짜>_<대상>.log`, 종료 코드는 그대로(작업 스케줄러 결과·점검·다시 실행 판정 같음). 실행 중 창을 닫으면 `0xC000013A`(중단)로 끝난다.
- **놓친 작업 다시 실행(2026-10-02)**: `/running` 오른쪽 "놓친 작업 다시 실행" — PC 가 21시 무렵 꺼져 있었거나 작업이 실패했을 때. 각 작업의 가장 최근 예정 시각보다 마지막 실행이 이르면 "놓침", 결과 코드가 실패면 "실패"로 나열하고, 버튼을 누르면 등록된 예약 작업을 예정 시각 순서대로 **하나씩**(앞 작업이 끝난 뒤, 작업당 최대 20분) 시작한다. 점검(Watchdog)은 마지막. 기록 `reports/catchup/<날짜>.log`. 참고: 작업 스케줄러의 StartWhenAvailable 이 켜는 순간 놓친 작업을 자동으로 돌리지만 **한꺼번에** 시작한다 — 2026-10-01 21:50 에 점검이 Paper 기록보다 먼저 돌아 CRITICAL(종료 2), 테스트넷 훈련이 같은 계좌·DB 의 오버레이 훈련 주문에 막혀 실패(종료 1). 적립 매수는 예약 접수 10:00~22:20 KST 밖이면 주문하지 않는다(아침에 켜서 돌 때 거절로 그날 1 건을 버리지 않게, `ORDER_WINDOW`). 대시보드를 새 코드로 쓰려면 `scripts/stop_dashboard.cmd` → `open_dashboard.cmd`.
- PyInstaller는 대시보드 및 backtest/paper 한정. schema 버전 불일치는 명확히 거부한다.
- API 키는 출금 권한 없이 최소 권한·IP 제한. .env/환경변수 사용, Git/로그/보고서에 키·토큰·개인계정 응답을 노출하지 않는다.
- **Paper 일일 점검(2026-09-24 구현)**: `scripts/paper_watchdog.py` 매일 21:20 KST(2026-09-24 아침→저녁 이동; T/v2 21:00·O/v1 21:02·O/v2 21:04·O/v3 21:06·테스트넷 21:08). 두 Paper 원장의 실행 누락·DEGRADED·누락 보정·청산/정지·낙폭(경고 20%·중단 25%)·O/v1 원장 재계산을 판정, `reports/watchdog/` 기록(`<날짜>.txt`·대시보드용 `<날짜>.json`) + 텔레그램(`.env` 설정 시). 종료 코드 0/1/2로 스케줄러에 드러난다.
- **T/v2 실거래 그림자 운영(2026-09-25 구현, Phase 8 준비)**: `scripts/run_shadow_t2.py run --allocation <USDT>` 매일 21:10 KST. 주문 0건. Paper 와 같은 실행기를 실거래 예정 금액으로(mode='shadow', 원장 `t2_shadow_<금액>`), 실계좌 **읽기만**(키 권한·시계·잔고), 호가 미끄러짐 추정, Paper 목표 대조. 일일 점검에 한 줄 추가 — 키에 읽기 외 권한·IP 제한 없음·주문 기록은 CRITICAL, 나머지 WARN 까지. 기준·대응: [51](../research/51-phase8-shadow-t2-registration.md).
- **실거래 키 권한 계획(제안, Phase 8 승인 때 확정)**: 1단계(T/v2 현물) = 현물 거래만. 2단계(O/v2) = 현물·선물 거래 + "Permits Universal Transfer"(본인 지갑 사이 이체, [53](../research/53-o2-wallet-transfer-design.md)). **어느 단계든 출금 끔·IP 제한.** 키 권한을 켜는 것은 사용자가 한다. 그림자 운영 점검 기준은 단계 승인 때 바꾼다.
- **한국투자증권(KIS) 실전 읽기 전용 연동(2026-09-26)**: `.env` 의 `KIS_APP_KEY`·`KIS_APP_SECRET`·`KIS_ACCOUNT_NO`(10자리)·`KIS_TRADING_ENABLED`. 어댑터 `exchange/kis.py` = 토큰(`data/kis_token.json` 캐시, 발급 1분 1회 제한·만료 10분 전 교체·만료 오류 시 1회 재발급), 해외주식 현재가·일봉(2007-08-20~)·잔고 조회만. **주문 함수는 `KIS_TRADING_ENABLED` 와 상관없이 항상 막힌다**(실전 주문은 별도 승인 뒤). 점검: `python -m scripts.check_kis_account [--history]`. 키·시크릿·토큰은 출력·예외·repr 에 싣지 않는다.
- **긴급 정지 스위치(2026-09-25)**: `data/HALT` 파일이 있으면 정지(첫 줄 = 사유), 지우면 해제. 코드는 이 파일을 만들거나 지우지 않는다. 그림자 운영은 그날 체결하지 않고, Phase 8 실행기는 신규 주문을 멈춘다(청산은 허용). `ops/kill_switch.py`.
- **LT Paper — 주도주 추세 보유 가상 1,000 USD (2026-10-03, 문서 115)**: `scripts/lt_paper.py daily` 매일 21:10 KST 화~토(예약 작업 `AutoTrading_LT_Paper`, 사용자 등록). 한국투자증권 S&P 500 최근 일봉 이어 받기(읽기 전용) → 113 규칙 엔진(그달 첫 거래일 주도주 매수·추매, 추세 꺾임/고점 −35% 매도) → 무료 RSS 뉴스 모으기. **주문 없음.** 자료 `data/lt_paper/`, 보고 `reports/lt_paper/latest.json`.
- **NQ Paper — 나스닥 100 상위 10 교체판 가상 1,000 USD (2026-10-12, 사용자 요청)**: `python -m scripts.nq_paper daily` 를 LT Paper `daily` 안에서 함께 돈다(새 예약 작업 없음, NQ 가 실패해도 LT 는 끝남). 나스닥 100(위키백과 판 1379452901 고정, GOOG 제외 100 종목)을 LT 점수(21 · 63 · 126 거래일 수익 순위 평균, 섹터 단계 없음)로 줄 세워, 판단일(그달 첫 거래일 · 그로부터 14 일 뒤 첫 거래일)에 상위 10 밖은 팔고 새로 든 종목은 자산의 10% 씩 산다(추세 꺼진 종목은 안 삼). 손절은 T/v2 와 같은 추세 신호 — 매일 20 · 60 · 120 거래일 수익 부호 합 ≤ 0 이면 다음 거래일 종가 매도, 판 돈은 다음 판단일까지 현금. 2026-10-01(10 월 첫 판단일)부터 계산. 현재 명단이라 생존 편향 → 앞으로의 기록만 판단 근거. **주문 없음.** 자료 `data/nq_paper/`, 보고 `reports/nq_paper/latest.json`, 엔진 `ops/nq_paper.py`. 일일 점검의 '주식 Paper 마지막 기록' 에 NQ 줄.
- **SN20 Paper — S&P 500 상위 10 + 나스닥 100 상위 10 (2026-10-12, 사용자 요청)**: `python -m scripts.sn20_paper run` 을 LT Paper `daily` 안에서 NQ 다음에 돈다(새 예약 작업 · 새 시세 없음 — S&P 500 은 LT, 나스닥 100 은 NQ 일봉). 판단일마다 S&P 500 점수(모멘텀, 순위만) 상위 10 + 나스닥 100 점수 순위에서 이미 고른 종목을 건너뛴 다음 10 = 20 종목 각 5%. 판단일 · T/v2 추세 손절은 NQ 와 같다(`ops/nq_paper.combined_top`). 10/01 부터. 실행 확인용 기록 — 10 · 20 종목 모멘텀은 백테스트 · Paper 로 우위를 가릴 수 없다(225 `UNDERPOWERED`). 보고 `reports/sn20_paper/latest.json`, 일일 점검에 SN20 줄. **SN20-C 와 세금 (같은 날 추가):** 같은 실행에서 `SN20C`(판단일마다 10% 넘은 종목을 5% 로 덜고 5% 미만을 채움, `ops/nq_paper.cap_and_fill`, 보고 `reports/sn20c_paper/`)도 돈다. 두 장부 모두 양도세 추정(`ops/paper_tax.py`): 장부 1,000 USD = 실제 1,000 만 원(`SCALE_KRW`)으로 보고 연 250 만 원 공제 · 22%, 해 넘김 첫 처리일에 정산, 12 월 15 일 뒤 첫 처리일에 남은 공제만큼 이익 실현 후 재매수(D) — 보고서 `after_tax_equity_usd` · `tax`. 환율 변화는 무시(근사).
- **카피 트레이딩 리더 10 명 90 일 기록 (2026-10-12, 문서 226 · IDEAS 35, 사용자 요청)**: `python -m scripts.copytrade_watch run`(예약 `AutoTrading_Copytrade_Watch` 매일 10:23 KST, 사용자 승인 뒤 Claude 등록). 바이낸스 카피 트레이딩 공개 웹 주소에서 리더당 상태 · 최근 포지션 100 건만(하루 20 요청, 3 초 간격, 키 없음 · 주문 없음) → `data/copytrade_watch/`. 텔레그램 없음. 2027-01-11 뒤 `status` 로 판단하고 예약을 끈다. 웹 주소가 막히면 실패로 남는다(실거래와 무관).
- **Cruise 바이낸스 전환 · Falcon 자동 분배 (2026-10-15, 사용자 요청)**: 거래 키는 기존 `BINANCE_API_KEY` 에 권한을 켠 것을 `BINANCE_LIVE_API_KEY/SECRET` 로도 쓴다(확인: IP 제한 · 읽기 · 현물 거래 · 선물 켜짐, 출금 꺼짐, **내부 이체(Universal Transfer) 꺼짐 → 자동 분배 전에 켜야 함**). 예약 `AutoTrading_Binance_Cruise` 등록(매일 09:05). 업비트 Cruise 예약 2 개(`AutoTrading_Upbit_T2_Live/Weekly`)는 관리자 권한이 필요해 사용자가 지운다 — 업비트 실행기는 `CRUISE_UPBIT=on` 일 때만 주문하고 잔고도 0 이라 남아 있어도 주문은 없다. 틀(`deploy/tasks/AutoTrading_Upbit_*`)은 지웠다. 오르빗 · '현황' 은 업비트 장부를 보이지 않는다(`live_home.SHOW_UPBIT=False`). **Falcon 자동 분배**(`ops/falcon_split.py`): Cruise 실행 직전, 새 입금에서 선물 몫(Cruise:Falcon = 10:10 → 50%)이 모자라면 현물 → USDⓈ-M 선물 이체. 선물 몫 목표가 500 USDT 미만이면 안 옮김, 선물 → 현물 되돌림 없음, 문서 53 규약(선기록 · 결과 모르면 재전송 안 함, `wallet_transfers` 표 mode=live). Cruise 상태에 `falcon`(선물 지갑 USDT · 이체 기록)을 남기고 오르빗 실거래 화면이 Falcon 카드로 보인다. Falcon 실거래 실행기는 아직 없다.
- **월 입금 안내 시안 A (2026-10-15, 사용자 요청)**: Glide 매수 당일 아침 질문이 '이번 달 세 곳에 넣을 돈(합계)' 으로 바뀌었다. 답한 합계를 Glide 80 · Cruise 10 · AG4S2 10 (`ops/deposit_split.py` MIX)으로 나눠 '토스 Glide 몫 · 바이낸스 현물 Cruise 몫 · 선물 AG4S2 몫(원화 · 업비트 USDT 환산)' 을 알린다. Glide 몫이 그달 Glide 매수 금액이다(`glide.max_krw` 상한은 Glide 몫에). 답이 없으면 예전처럼 `glide.monthly_krw` 만 산다. 돈을 옮기지 않는다 — 입금은 사용자가 직접.
- **PEAD Paper — 실적 발표 갭 따라가기 (2026-10-16, 문서 241, 사용자 요청)**: `python -m scripts.pead_paper run`(예약 `AutoTrading_PEAD_Paper` 화 ~ 토 21:40 KST, LT Paper 21:10 일봉 갱신 뒤, Claude 등록). SEC 8-K 2.02 최근 공시를 종목마다 다시 받아(`SEC_CONTACT_EMAIL`, 초당 10 회 미만) 240 규칙(반응일 +5% · 거래량 2 배 → R + 1 시가 · R + 20 종가)으로 가상 10,000 USD · 500 USD × 최대 20 자리를 처음부터 다시 계산. 반응일 2026-10-16 부터. 텔레그램 없음, `status` 로 봄. 판단은 닫힌 거래 100 건(241 §4).
- **MF Paper 에 TRI3 추가 (2026-10-16, 사용자 요청)**: 장부 `TRI3` = QQQ · GLDM · IEF 각 1/3, 종목마다 T 추세(20 · 60 · 120 거래일) 꺼지면 그 몫 현금(`CASH` 가격 1 · 이자 0), 매달 첫 거래일, 2026-10-08 부터. 추세 판단용 1 년 일봉을 따로 받는다(`_prices_for`). 판정 아님(MA-TREND A 는 REJECT).
- **MF Paper 에 GBG 추가 (2026-10-12, 사용자 요청)**: `scripts/mf_paper.py`(예약 `AutoTrading_MF_Paper`) 장부 `GBG` = Glide 핵심 60%(LD_M3 와 같은 변동성 맞춤 TQQQ · SCHD, T 브레이크 없음) + IEF 20% + GLDM 20%, 매달 첫 거래일 다시 맞춤, 다른 장부처럼 2026-10-08 부터. 비교 상대 LD_M3. 근거는 참고 계산(2012 ~ 2026, Glide 60 · 채권 20 · 금 20: 낙폭 28 → 21%, 2022 −26 → −19%) — 판정 아님. 고치기 전 상태 백업 `data/backups/mf_paper_state_before_gbg_2026-10-12.json`.
- **미국 일봉 미완성 봉 결함 수정 (2026-10-12)**: 한국투자증권 일봉은 미국 장 전 · 장중에도 그날 진행 중인 봉을 주는데, LT · QS Paper(21:10 · 21:15 KST = 미국 장 전)가 이를 그날 종가로 저장하고 다시 고치지 않았다(SPY 10/06 ~ 10/09 거래량 14만 ~ 32만 주, QQQ 10/09 37만 주). `ops/us_daily_bars.py`: 뉴욕 16:30 이 지나지 않은 날의 봉은 버리고, 최근 30 일을 겹쳐 받아 이미 저장된 줄도 새 값으로 덮어쓴다(10 일보다 오래된 날이 0.5% 넘게 다르면 수정주가 변경 → 전체 다시 받기). 고친 뒤 LT 162 종목 · QQQ 줄을 바로잡고 `rebuild` 로 LT(1,006.92 → 1,023.04) · QS(QS · A 1,002.50 → 999.75, AG 1,009.81 → 1,000.82) 를 10/02 부터 다시 계산, AG 보고서 재생성. 고치기 전 상태 · 시세 백업 `data/backups/paper-before-bar-fix-2026-10-12/`. 시험 `tests/ops/test_us_daily_bars.py`. 토스 실거래 확인(같은 날): QS 신호(`qs_signal`, 뉴욕 16:15 전 봉 뺌) · Glide 비중(지난달 말까지 종가)은 안전. Glide T 브레이크는 한국 날짜로만 오늘 봉을 빼서 21:00 작업이 자정 넘어(미국 장중) 늦게 돌면 장중 TQQQ 봉을 어제 종가로 쓸 수 있었다 → `last_settled_day(day.at)` 까지만 쓰게 고침(`Day.at` = 실제 지금 시각, 03:30 재시도의 업무 날짜와 분리). QS 신호 시각도 `_utc_now()`(`_kst_now` 에서 만듦) 하나로 — 시험이 시계를 고정하던 것과 어긋나 10/12 부터 깨지던 `test_toss_accumulate` 6 개 복구(가짜 토스에 `stocks_all`). 회귀 시험 `test_m3_brake_live` 늦은 실행 2 개.
- **QS Paper — QQQ 적립 · TQQQ 전환 가상 장부 (2026-10-03, 문서 116)**: `python -m scripts.qs_paper daily` 매일 21:15 KST 화~토(예약 작업 `AutoTrading_QS_Paper`, 사용자 등록). 한국투자증권 QQQ·TQQQ 최근 일봉(읽기 전용) → QS · A(매달 QQQ) · AG-US 세 장부. **주문 없음.** 자료 `data/qs_paper/`, 보고 `reports/qs_paper/latest.json`, 대시보드 `/qs-paper`.
- **AG Paper — 소액 공격형 세 계좌 가상 1,000 USD 씩 (2026-10-04, 문서 117)**: `python -m scripts.ag_paper daily` 매일 21:17 KST(예약 작업 `AutoTrading_AG_Paper`, 사용자 등록). AG-CR 오버레이 원장 `ag_cr3` 하루 진행(공개 시세만) + QS Paper 의 AG-US 를 읽어 AG-MIX 계산, 300 USD 정리선. **주문 없음.** 보고 `reports/ag_paper/latest.json`, 대시보드 `/ag-paper`.
- **토스 적립 + QS (2026-10-04, 문서 118 §7)**: `python -m scripts.toss_accumulate run` 21:00 KST 월~금(예약 작업 `AutoTrading_Toss_Accumulate`) · `fx` 09:00·12:00·15:00 KST 월~금(`AutoTrading_Toss_FX`, 달러가 모자라면 환전 금액 알림). 장 마감 지정가, `TOSS_TRADING_ENABLED=true` 일 때만 주문. 상태 `data/toss/`, 보고 `reports/toss/`. 한국투자 적립(`AutoTrading_QNDX_Alert`)은 끈다.
- **주간 미래 주도 테마 알림 (2026-10-05, 문서 148)**: `python -m scripts.theme_alert weekly` 토요일 09:00 KST(예약 작업 `AutoTrading_Theme_Alert`, 사용자 등록). 143·115 가 모은 지난 7 일 뉴스 제목 → AI(Gemini) 가 국장·미국 미래 주도 테마 3·종목 5 · 근거 뉴스 번호 → 코드가 실제 제목·우리 자료 사실(추세·3 달 수익·주도주 목록)을 붙여 텔레그램 두 통. 근거 없는 주장·종목군 밖 코드는 뺀다. 보고 `reports/theme_alert/`. 매매 신호 아님(성적은 147 장부). `--no-telegram` 은 보내지 않고 확인만.
- **상태 백업 (2026-10-04, 사용자 요청)**: `python -m scripts.backup_state run` 매일 23:30 KST(예약 작업 `AutoTrading_Backup`, 사용자 등록). `data/`·`reports/` 는 git 밖이라 C: 디스크가 고장 나면 QS 실거래 상태·적립 진행·Paper 장부가 사라진다 → **상태 묶음** `state-YYYY-MM-DD.zip`(약 1 MB: `data/` 작은 상태 파일 · SQLite 장부 안전 복사 · `trading.db` 에서 시세 표 `candles`·`perp_metrics`·`funding_rates` 를 뺀 장부 `backup/trading_ledger.db` · `reports/`(연구·실행 기록 제외) · 파일별 SHA256 `backup/manifest.json`)을 **F:\AutoTrading_backup**(C: 와 다른 물리 하드디스크)에 저장 — OneDrive 는 용량이 가득 차 뺐다, 최근 30 개 유지. 큰 자료(`data/external`·`research`·`macro_sources`·시세 캐시 `bars`, 약 3 GB)는 F: 의 `mirror/` 에 바뀐 파일만 복사(지우지 않음). **넣지 않음:** `.env`·토큰(비밀 — 다시 발급), `trading.db` 시세 표(다시 받음). 잠긴 파일(상주 프로그램 `*.lock` 등)은 건너뛰고 기록. 끝 코드 0 성공 · 2 F: 실패 — 21:20 점검의 PC 줄에 나온다. 마지막 결과 `reports/backup/last.json` (`status` 로 보기). **되살리기:** 최신 묶음을 저장소 루트에 풀면 경로가 그대로다(먼저 다른 폴더에 풀어 확인 권장). 장부 표는 `trading.db` 를 연 뒤 `ATTACH 'backup/trading_ledger.db' AS b; INSERT INTO paper_days SELECT * FROM b.paper_days;` 처럼 표마다 옮긴다. 시세는 백필로 다시 받는다.
- **QS 실거래 (2026-10-04, 문서 118) — 코드 준비, 꺼짐**: `python -m scripts.qndx_alert qs-start` 로 켜면 21:00 적립이 QS 규칙(추세 꺼짐 QNDX 멈춤 · 회복 때 쌓인 적립금으로 TQQQ 예약 매수 · 꺾이면 QS 의 TQQQ 예약 매도)을 따른다. `qs-preview` 는 조회만. TQQQ 는 기본예탁금 1,000 만 원 필요 — 확보 전에는 켜지 않는다. 증권사 토스 이전 검토 중.
- **적립 매수 QNDX·BTQ(2026-09-26, 문서 89)**: `scripts/qndx_alert.py run` 매일 21:00 KST(예약 작업 `AutoTrading_QNDX_Alert`, 사용자 등록 확인). QNDX 매일 1만 원·BTQ 매일 1달러 가상 적립 → 1주 살 차례면 `KIS_TRADING_ENABLED=true` 일 때 **예약 매수 1주**(현재가 +3% 지정가, 당일 유효, 매수 전용), 아니면 텔레그램 안내만. 달러 예수금 또는 통합증거금 필요. 끄기: `.env` 를 `false` 로, 급할 때 `data/HALT`. 상태 `data/accumulate/`, 보고 `reports/accumulate/`. 대시보드는 일반 자동매매 메뉴와 분리한 **해외 주식** `/overseas`에서 QNDX·BTQ의 마지막 저장 보고서를 보여 준다. 옛 `/accumulate`·`/qndx` 링크도 같은 화면으로 연결되며, UI는 계좌 실시간 조회나 주문을 하지 않는다.
- **10/23·10/24 점검 보고(2026-09-25, 문서 65)**: `python -m scripts.review_phase6 --until 2026-10-22`(10/23 21:30 KST 이후, O/v3 한 달) · `--until 2026-10-23`(10/24, 4 주 점검·Phase 8 1단계 자격). 결과 `reports/review/<결정일>.md`. DB 읽기 전용, 기준은 문서 65 에 고정.
- **Phase 7 테스트넷 판정 보고(2026-09-26, 문서 26 §2·85)**: `python -m scripts.review_phase7 --until 2026-10-08`(10/08 21:30 KST 이후). 인자 없이 실행하면 중간 점검. 결과 `reports/review/phase7-<날짜>.md`. 실패한 날의 원인은 사람이 `docs/research/26-phase7-causes.json`(`external`·`code`)과 26 §6 에 적는다 — 적지 않은 실패는 CHECK. 코드 결함이면 `--first <고친 날>` 로 다시 센다.
- **실거래 1단계 출발 준비(2026-09-26, 문서 85)**: 첫 주 운영 계획·거래 키 체크리스트·멈출 조건·매일 대조·장애 대응. 금액·멈출 조건 숫자는 제안이며 L1 승인 때 사용자가 확정한다.
- 실거래 경로의 Telegram critical/위험 알림, JSONL 구조화 로그, WS 지연·마지막 봉·잔고·보호주문·디스크·DB 오류 관찰은 구현/운영 검증 대기.
- 일별 회계와 위험 초기화는 UTC. 표시 시간대가 위험 계산 기준을 바꾸면 안 된다.

## 오비 아이콘·우주 테마 (2026-10-09)

사용자 승인 우주 고양이를 오비로 사용한다. 앱 이름 변경 미확정 → Auto Trading 유지.
승인 원본과 축소 PNG/ICO를 저장소에 보관하며 배경 제거/크롭/새 생성형 편집은 없다.
원본 검은 배경은 CSS 둥근 배지로 표현한다. 15페이지 공통 별/성운 배경은 정적 CSS,
두 테마의 양수/음수/위험 표시 의미와 원래 금액/장부 계산은 그대로다.
모바일 실행 현황의 표는 페이지 전체 대신 패널 내부에서 스크롤한다.

정적 제공은 기존 ICO/SVG 외 `/assets/obi.png`만 추가한다. 원본은 제공하지 않는다.
PNG 요청은 loader/DB/계좌를 호출하지 않으며 MIME/CSP/화이트리스트·HTML 내보내기를 검증한다.
빌드 도구 Pillow/PyInstaller는 빌드 환경에만 있고 새 설치·API·유료 이미지 요청은 없다.
dist/AutoTrading.exe를 새 캐시로 재빌드하고 PE RT_ICON의 PNG 7프레임을 승인 ICO와 대조했다.
바탕화면 Auto Trading·대시보드 다시 켜기 원본 LNK 백업은 reports/ui/shortcut-backups,
이전 exe는 build/obi-backup에 보관. 실행 대상/인수/작업 폴더/창 스타일을 보존한다.
기존 make_desktop_shortcut은 이미 있는 LNK에 대해 외형 전용 보존 helper를 사용한다.

운영 UI의 HTTP 식별/포트/PID/pythonw 확인 후 UI 서버만 종료 시도했으나 Windows 액세스 거부.
관리자 UAC/권한 우회/예약 변경 없음. 소스·아이콘/바로가기 적용과 운영 화면 반영은 구분한다.
사용자가 권한 있는 '대시보드 다시 켜기'를 실행해야 새 HTML/CSS가 반영된다.
검산용 8788은 별도 프로세스이며 작업 종료 때 정리한다. 매매/Paper/예약은 변경하지 않는다.

## 오르빗 승인 실거래 화면 (2026-10-10)

10/09 우주 테마 이력보다 최신인 UI 승인이다. 앱의 표시 이름은 오르빗,
exe 내부 파일명(`AutoTrading.exe`)과 저장소/예약/브라우저 저장 경로는 호환을 위해 유지한다.
실거래 전용 `/live`에는 승인 PNG의 5메뉴·자산 요약·오비 삽화·확인 줄·3투자 카드·
자산 변화와 최근 거래만 배치한다. Paper/연구/테스트넷은 이 화면의 탐색에서 제외하고
기존 별도 운영 화면은 보존한다. 솔라나는 기존 Cruise 장부/공개 시세만 표시하며 새 매매 없음.
자동 적립의 두 제목을 동일한 자동 적립으로 표시한다. 실제 보유는 실제 장부 종목이므로
승인 PNG의 예시 종목/금액과 같도록 가짜 보유를 만들지 않는다.

내 자산에는 현금을 포함하고 현금/수량/장부 메모는 카드의 보유 상세에서 확인한다.
입금+적립 원가·세금 전 평가 차액과 확정 수익을 구분하고 결측 시 차액은 미확인으로 표시한다.
검증된 전체 평가 이력·입출금 이력이 없어 자산 변화/카드 작은 그래프는 평가 기록 대기다.
기간 선택 모양과 위치는 유지하지만 없는 선을 만들지 않는다. 최근 거래 전체 보기는
저장된 체결·입금 목록만 펼친다. 접수≠체결·장부≠가동·계좌 연결 미확인 경계 유지.
계좌/설정/화면 읽는 법은 native details, 기간 선택은 radio, 테마는 기존 GET/쿠키.
새 JS/POST/주문/키 조회/권한/예약/전략 변경 없음. 이미지 3개는 로컬 자산이다.

기존 exe를 `build/orbit-backup/AutoTrading-before-orbit.exe`로 보관한 뒤 설치된 빌드 도구로
오르빗 창 제목/1536×1080 창을 재빌드했다. 앱 바로가기는 정확한 실행 대상을 검사한 뒤
원본 LNK를 `reports/ui/shortcut-backups/`에 백업하고 이름만 오르빗으로 이동한다.
아이콘/설명 helper는 실행 대상·인수·작업 경로·창 스타일 동일성을 확인한다.
소스 빌드·검산과 운영 반영은 구분: 8787 HTTP 식별과 PID35888 일치 뒤 UI 서버만
종료 시도했으나 Windows 액세스 거부. 관리자 UAC/권한 우회 없음. 사용자가 기존 서버를
종료할 수 있는 권한으로 대시보드 다시 켜기를 실행해야 새 화면을 얻는다.
검산용 8788 서버는 종료하며 매매/Paper 프로세스는 변경하지 않는다.

## 오르빗 자동 시작·투명 아이콘 (2026-10-10 후속)

앞선 8787 재시작 권한 문제를 앱에서 해소한다. exe를 열 때마다 현재 저장소의 최신 UI
소스를 별도 pythonw 서버로 실행하고 OS 할당 빈 포트의 `/live`를 연다.
관리자 권한 요청/UAC/권한 우회/전체 pythonw 검색·종료/작업 스케줄러 변경은 없다.
기존 8787 서버는 보존하며 기존 브라우저 북마크는 새 앱 서버로 자동 이동하지 않는다.
새 앱 창이 최신 UI를 사용한다. 이미 열린 구형 앱은 닫고 오르빗을 다시 연다.

앱 전용 임시 `orbit-ui-*` 폴더에 ready JSON(포트·프로세스 PID·이번 nonce)과 종료 신호만
저장한다. 계좌/키/장부를 저장하지 않는다. Windows venv redirector와 실제 interpreter의
PID 차이가 있어 Popen PID를 단정하지 않고 nonce+실제 로컬 자산 응답으로 확인한다.
창 정상 종료는 자기 신호 파일, 강제 종료는 SYNCHRONIZE 전용 부모 프로세스 핸들로
감지한다. PID 재사용/다른 창/매매 서버를 종료하지 않는다. 앱 종료 후에도 매매는 계속된다.
시작 실패/20초 시간 초과는 오류 화면이며 기존 서버를 정상 최신 화면으로 위장하지 않는다.

`make_desktop_shortcut.ps1`은 삭제된 다시 켜기 LNK를 복구한다. 대상은 같은 exe,
인수 `--restart-dashboard`(동일한 최신 시작 경로). CMD도 더 이상 기존 서버 종료를 시도하지
않고 같은 앱을 연다. 기존 다른 대상의 LNK는 거부, 기존 관련 LNK는 백업한다.
새 투명 아이콘 경로는 Windows 캐시 구분용 `orbit-transparent.ico`다.
첨부 PNG는 이미 alpha=0 배경이므로 이미지 재생성 없이 RGBA를 유지한다.
이전 exe는 `build/orbit-backup/AutoTrading-before-autostart.exe`로 보관한다.
새 주문/위험/전략/예약/데이터 수집을 추가하지 않는다. 실제 창·바탕화면 캐시 시각 확인은
사용자 실행 후 확인하며 소스 시험/PE 아이콘 검사와 구분한다.

## 오르빗 UI 사용성 보완 (2026-10-10 후속)

- 계좌/설정/화면 읽는 법/알림/전체 거래의 native details 팝업을 native Popover로 교체.
  X는 44px 버튼/접근성 이름/자동 초점, 헤더 고정·본문 독립 스크롤이다.
  Esc·바깥 클릭·닫은 뒤 트리거 초점 복귀는 브라우저 기본 동작이다. JS/외부 스크립트 없음.
- 창 너비에 맞춰 카드 3/2/1열·금액/간격을 조절한다. 두 테마 동일 오비 그림/위치,
  라이트 밝은 우주 배경·다크 기존 우주 배경, 해/달 테마 전환. 승인 화면의 데이터 경계 유지.
- 먼저 확인 배너는 **저장 pending > 0**일 때만 표시한다. 대기가 없더라도 시세 실패/
  환율 미확인/매수 정지/장부 부재는 작은 안내와 알림 팝업에 유지해 정상 상태로 위장하지 않는다.
- Lucide/CC0 코인 아이콘·공식 기업 식별 그림은 로컬 고정 파일이다.
  [출처·라이선스](../../src/auto_trading/ui/assets/icons/SOURCES.md)를 보관하고 HTML export에도
  포함한다. 동적 CDN/임의 경로/새 계좌 loader 없음, 기존 CSP 유지. 기업 상표는 공개
  라이브러리 라이선스와 다르며 타인 배포 전 사용 조건을 확인해야 한다.
- 소스 UI만 변경해 exe 재빌드는 불필요하다. **앱을 닫고 다시 열면** 최신 UI가 적용된다.
  기존 8787/Paper/매매/예약 프로세스는 변경하지 않는다. Chrome 합성 화면 검산은 네이티브
  WebView2 실제 창 조작과 구분한다(설치 WebView2 154.0.4258.62 확인).

## 오르빗 승인 원본 테마 보정 (2026-10-10 최종 후속)

위 UI 사용성 보완의 배경/그림 설명 보정(2026-10-10 최종 후속): 라이트 상단은 사용자
02_31_57 첨부와 동일한 `orbit-hero-light.png` 원본이다. 후속 승인으로 다크 상단만
`orbit-hero-dark-sleep.png`로 교체: 같은 오비의 눈을 감고 주변을 남색/보라색 밤우주로
imagegen 편집, canvas/자세/배치/궤도 유지. CSS mask는 배경 경계를 연결하며 낮 그림에
필터만 씌우는 방식이 아니다. 라이트 상단/전체 배경은 그대로다. 라이트 전체 배경에는
별도 밝은 구름/별/행성 PNG를 쓰고 다크 전체 배경은 그대로 유지한다. 주식 종목 그림은
확대해도 선명한 Lucide SVG 배지로 교체(공식 로고 아님), 기존 기업 그림/URL은 보존한다.
로컬 자산/새 배경 경로만 제공·export, 계좌 loader 없음. 원본 해시/생성 배경의 프롬프트는
[에셋 기록](../../src/auto_trading/ui/assets/README.md)에 보관한다. 실제 창은 앱 재실행으로 반영.

추가 화면 정리(2026-10-10): 미국 주식/코인/자동 적립 카드 헤더 오른쪽의 설명과 작은
종목 배지를 제거했다. 실제 보유 목록·수량 상세·평가액은 유지한다. 내 자산 카드만 추가
들여쓰기를 상쇄해 미국 주식 카드와 왼쪽 테두리를 맞춘다. 두 테마/3·2·1열에 적용,
인사말/오비/안내 카드 위치·팝업/배너 조건·장부/매매 불변. 기존 앱을 닫고 다시 열어 반영한다.

## 저장 공간 진단 기준점

앞선 읽기 전용 진단값(이번 문서 병합에서 DB 재측정하지 않음):
전체 약 7.77GB(7.23GiB), DB 약 7,248.62MiB로 97.85%.
candles 약 2,667MiB/2,183만 행, perp metrics 약 2,103MiB/1,525만 행;
관련 인덱스 약 1,488+966MiB, funding 약 25MiB.
WAL 0, SHM 32KiB, freelist 0, 로그 약 31KB, 보고서 약 0.83MiB, Git 약 5.68MiB였다.
주된 원인은 다중 심볼 시장 데이터와 인덱스 축적이며 당시 WAL/로그/Git 폭증 증거는 없었다.
정상 축적이라는 판정이 무제한 수집을 허용한다는 뜻은 아니다. 최근 시점의 성장 속도와 필요 범위를 별도 측정한다.

## 장기 storage/retention 정책 — 제안, 자동 삭제 미구현

| 분류 | 보존 / 관리 제안 |
|---|---|
| 주문·체결·계좌·위험·결정·버전 | 감사 핵심. 자동 만료 금지, 영구 보관 기본. 압축/장기 보관 후에도 복구·판단 재현 가능해야 함 |
| 원천 시장 데이터 | 승인된 연구 범위/필요 warmup만 hot DB. 과거 심볼·월 단위 cold archive 후보, 재다운로드 불가능한 데이터 우선 보존 |
| 파생 봉·캐시 | 원천/생성 코드·config·해시로 재생 가능한 것만 정리 후보. 원천을 대신하는 유일 사본이면 삭제 금지 |
| 백테스트 결과 | 사전등록·최종/기각 결과·노출 이력·data/code/config digest는 장기 보존. 중간 대용량 산출물은 재현성 검증 뒤 별도 승인 정리 |
| 운영 로그 | 오류·위험·주문 관련은 감사 기록으로 승격. 일반 진단 로그 hot 30일/압축 180일은 제안값, 승인 전 삭제/rotation 미적용 |
| Git | 코드·설계·사전등록·작은 요약만. DB/원자료/venv/시크릿 제외. history rewrite나 GC를 용량 이유로 자동 실행하지 않음 |
| DB WAL/인덱스 | WAL 크기·장기 reader·checkpoint 지연을 관찰. WAL/SHM 수동 삭제 금지. 인덱스 제거/VACUUM은 별도 벤치마크·백업·공간 검토 후 승인 |

보관 절차: manifest(원본 경로·테이블/schema·심볼/기간·행수·min/max timestamp·checksum·수집/변환 버전) 작성 → 일관된 backup/export → checksum/행수/구간/표본 질의 대조 → 격리 위치 복원 시험 → 사용자 승인 → 원본 정리.
live SQLite DB 파일만 단순 복사하지 않는다. SQLite backup API 또는 일관된 중지/체크포인트 절차를 사용하고 복구 검증한다.
백업은 작업 디스크와 별도 매체에 둔다. 외부 업로드는 별도 승인·민감정보 검토가 필요하다.
데이터 이관은 WAL·임시 파일을 포함한 여유공간을 먼저 산정하며 인플레이스 압축/교체를 하지 않는다.
현재는 삭제·인덱스 제거·VACUUM·수집 범위 변경·스케줄러 추가를 모두 수행하지 않았다.

장기 운영 전 디렉터리/DB 테이블·인덱스·WAL별 크기와 일별 증가량을 관찰하고 백업 복원 점검 주기를 확정해야 한다.

## 오르빗 소유자 전용 웹 조회 (2026-10-16)

- 주소 https://orbit-investment-seolh.workspace-655977.chatgpt.site / Sites project `appgprj_6ad219930e5c8191bbcc496e75c6850b`.
  ChatGPT 공식 로그인 후 소유자 1명만 허용하며 외부 방문자는 없다. 익명 홈페이지·API 401 확인.
  사용자는 이 Site를 만든 것과 같은 ChatGPT 계정으로 로그인한다. 별도 Supabase·Google OAuth 스택은 만들지 않았다.
- 소스/수동 동기화 도구: `C:/Users/seolh/.codex/visualizations/2026/09/20/01a0bf5b-5cc1-7e93-a3a2-5535af4738ba/orbit-web-site/`.
  `README-ORBIT.md`의 두 수동 명령(기존 Python으로 `tools/orbit_export.py` → `tools/orbit_sync.py upload`)을 따른다.
  PC 원본 장부는 읽기만 하며 키 없는 공개 시세 외에 거래소 인증·주문 API를 부르지 않는다. `.env`는 읽지 않는다.
- `ORBIT_INGEST_SECRET`은 Sites 런타임 secret. 서비스 접근/쓰기 자격은 그 PC의 `local-data/service-credentials.dpapi`에
  Windows CurrentUser DPAPI로 암호화하며 평문 파일·Git·브라우저·보고서에 넣지 않는다. 다른 PC/Windows 사용자에게 파일을 복사해도 그대로 쓸 수 없다.
  토큰은 해당 Site HTTPS origin에만 전송하고 redirect는 따르지 않는다. 공유 대상 확대 전 별도 권한 감사를 한다.
- D1 `orbit_snapshots`는 최신 허용 필드 JSON 1개를 보관한다. 시각 역전·같은 시각 다른 내용은 409, 동일 재전송은 멱등 허용한다.
  실제 원본 장부·계좌번호·거래소 키·원본 주문 ID·자유 메모는 보관하지 않는다. 입력은 512KiB·strict schema, 응답 private/no-store다.
- 최초 전송/서버 digest 재조회 성공. 화면 60초 폴링은 저장된 스냅샷만 읽는다. **PC 자동 업로드·예약은 미등록**이다.
  전송 후 10분이 지나면 지연 표시, 시세 미확보는 추정 표시. PC가 꺼져도 사이트의 마지막 기록은 조회할 수 있지만 PC 매매를 대신 실행하지 않는다.
- 기준 금액·평가 차액은 장부 모형이며 입출금 보정 수익률·실현손익이 아니다. Binance USDT≈USD/현재 환율 근사가 있다.
  Altair는 선물 지갑 자금 장부만 연결됐으며 실제 포지션/미실현손익은 미연결이다. 보유 0/포지션 없음으로 인증하지 않는다.
  그래프는 가격 확인·현재 장부 구성 일치 과거 기록만 쓴다(최초 기존 6개 제외/유효 0개). 과거 숫자 합성이나 소급 수익 계산 없음.
- 배포 소스 `ce9bbee4dbe9bfb2a01e99a65679a1ec5b6785ae`. 초기 검증과 Windows 도구 경로 보정은 웹 소스 전용이며,
  기존 exe·실거래·Paper·Telegram·예약·208 연구 폴더는 수정/재시작하지 않았다. 운영 Phase/전략 판정 불변.
