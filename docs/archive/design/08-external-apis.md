> STATUS: HISTORICAL / NOT CURRENT
> 보관일: 2026-09-23 · 원래 경로: `docs/phase0/08-external-apis.md`
> 승인된 문서 정리 직전 작업본. D22 및 대체된 결정을 현행 사양으로 사용하지 않는다.
> 본문은 보존하고 Markdown 상대 링크만 새 위치에 맞췄다. 현재 기준: [CURRENT_SPEC](../../current/CURRENT_SPEC.md).

# 08 — 외부 API 후보 (J)

> **면책**: 아래 요금·제한·정책은 조사 시점 기준의 정리이며, 계약 전 **반드시 각 서비스의
> 현행 약관과 가격을 직접 확인**해야 한다. 특히 "개인 사용 무료, 상업적 사용 유료" 구분이
> 서비스마다 다르고 자주 바뀐다. 수수료율처럼 거래 판단에 직접 쓰이는 값은 문서가 아니라
> **거래소 API에서 런타임 조회**한다 (원 명세 §6 준수).

## 1. 거래소 (필수, Phase 1)

### Binance USD-M Futures

| 용도 | 엔드포인트 / 스트림 |
|---|---|
| 심볼 규격 | `GET /fapi/v1/exchangeInfo` (tickSize, stepSize, minNotional, 레버리지 브래킷) |
| 수수료율 | `GET /fapi/v1/commissionRate` |
| 캔들 | `GET /fapi/v1/klines` (최대 1500개/요청) |
| Funding 이력 | `GET /fapi/v1/fundingRate` |
| 계좌 | `GET /fapi/v2/account`, `GET /fapi/v2/positionRisk` |
| 주문 | `POST/GET/DELETE /fapi/v1/order`, `GET /fapi/v1/openOrders` |
| 체결 이력 | `GET /fapi/v1/userTrades` |
| 손익/펀딩 이력 | `GET /fapi/v1/income` |
| 서버 시각 | `GET /fapi/v1/time` |
| 실시간 캔들 | WS `<symbol>@kline_<interval>` |
| 마크 가격 | WS `<symbol>@markPrice@1s` |
| 호가 | WS `<symbol>@bookTicker` |
| 계정 이벤트 | WS user data stream (`listenKey`, 60분마다 `PUT` 연장) |
| Testnet | `testnet.binancefuture.com` — 별도 API 키 |

**주의 사항**

- Rate limit 은 가중치(weight) 기반. 응답 헤더 `X-MBX-USED-WEIGHT-1M` 을 읽어 **선제적으로 감속**한다.
  418/429 를 맞은 뒤 대응하면 이미 늦다 (반복 시 IP ban)
- 주문 관련은 별도의 초당/10초당 주문 수 제한이 있다
- `recvWindow` 는 5000ms 이하 권장 + 시각 동기화 필수
- **Position mode**: One-way 를 전제로 설계. Hedge 모드면 기동 중단 (→ 04 §4.2 #10)
- **Margin mode**: Isolated 권장 (계좌 전체 연쇄 청산 방지)

**클라이언트 라이브러리 선택**

| 후보 | 장단점 | 권장 |
|---|---|---|
| 직접 구현 (`httpx` + `websockets`) | 거래소별 세부 동작을 완전히 통제. `closePosition`, `workingType`, `newClientOrderId` 등 안전에 직결된 파라미터를 정확히 다룰 수 있음. 구현량 증가 | **채택** |
| `ccxt` | 다중 거래소 편의. 다만 추상화가 거래소별 특수 파라미터를 가리고, 버전업으로 동작이 바뀔 수 있음 | 백테스트용 과거 데이터 수집 등 **비-주문 경로에 한해** 보조 사용 가능 |
| `python-binance` | Binance 특화, 편리 | 유지보수 상태 확인 후 결정 |

> 이식성은 라이브러리가 아니라 **우리의 `ExchangeAdapter` 인터페이스**가 제공한다.
> 주문 경로만큼은 추상화 계층을 하나라도 줄이는 편이 안전하다.

## 2. 과거 데이터 (필수, Phase 1)

| 소스 | 내용 | 비고 |
|---|---|---|
| **Binance Public Data** (`data.binance.vision`) | 월별/일별 klines, aggTrades, funding CSV(zip) | 무료. 대량 백필에 최적. REST 로 수년치 받으면 rate limit 소모가 크다 |

> **funding 아카이브 실측 (2026-09-21)**
>
> 경로: `/data/futures/um/monthly/fundingRate/<SYMBOL>/<SYMBOL>-fundingRate-<YYYY-MM>.zip`
> 컬럼: `calc_time, funding_interval_hours, last_funding_rate` — **월 단위만** 제공된다
> (klines 와 달리 일별 파일이 없다). 따라서 진행 중인 달은 REST 로 보충해야 한다.
>
> BTCUSDT 2020-01 ~ 2026-08: **7,305건, 결측 0건**, 전 구간 8시간 주기.
> 2019-09~12 는 아카이브 자체가 없다 (캔들도 2020-01 부터라 영향 없음).
>
> ⚠ **`calc_time` 은 정각에서 수 밀리초 흔들린다.** 7,305건 중 **2,256건**이 8시간을
> 1~4ms 초과했다. 간격을 정확히 비교하면 결측 0건인 데이터에서 2,256건을 오탐하므로,
> 결측 판정에 **1분 허용치**를 둔다 (`storage/repositories/funding.JITTER_TOLERANCE_MS`).
>
> `funding_interval_hours` 를 **반드시 함께 저장한다.** 심볼·시점마다 8h/4h/1h 로
> 다르고, 연속한 두 행의 간격으로 역산하면 결측이 있을 때 조용히 틀린다.
| `GET /fapi/v1/klines` | 최근 데이터 보충 | 갭 메우기용 |
| `GET /fapi/v1/fundingRate` | funding 이력 | 페이지네이션 |

**검증 절차** (필수): 벌크 다운로드 데이터와 REST 데이터를 무작위 구간에서 대조하여 일치 확인.
백테스트 신뢰도는 데이터 신뢰도를 넘을 수 없다.

## 3. 거시경제 (D22 전략 입력)

| 소스 | 내용 | 비용 | 평가 |
|---|---|---|---|
| **FRED API** (St. Louis Fed) | CPI, PCE, 실업률, 국채금리, DXY 등 시계열 | 무료 (API 키) | 공식·안정적. **다만 최신 수정치를 준다** |
| **ALFRED** (FRED vintage) | 위 데이터의 **발표 시점별 값** | 무료 | **백테스트에는 ALFRED 를 써야 한다** (→ 03 §3.6 `vintage_ms`) |
| **BLS API** | CPI, NFP 원자료 | 무료 (등록 시 일 500회) | 공식 |
| **BEA API** | PCE | 무료 | 공식 |
| **Federal Reserve** | FOMC 일정·성명·의사록 | 무료 (웹/RSS) | 일정은 연간 사전 공표 → **캐싱 가능** |
| **US Treasury** | 국채 수익률 | 무료 | |

### 확보가 어려운 데이터 — 명시적 공백

| 항목 | 문제 |
|---|---|
| **Consensus (시장 예상치)** | 공식 무료 소스가 없다. 주요 공급자는 상용 (Econoday, Trading Economics, FactSet 등) |
| **과거 시점의 consensus** | 실시간으로 수집해 두지 않으면 사후 취득이 매우 어렵다 |
| **Revision 이력** | ALFRED 로 일부 가능하나 지표마다 다름 |

→ **결론**: 원 명세 §8의 `surprise = actual - consensus` 기반 스코어링은
**데이터 확보 문제로 MVP에서 구현 불가**하다. 다음 순서로 접근한다:

1. **지금부터** 발표 예정 지표의 consensus 를 매일 수집해 DB에 적재 (미래를 위한 자산 축적)
2. MVP 에서는 **캘린더 블랙아웃만** 사용 (일정만 있으면 되므로 무료 소스로 충분)
3. 1~2년 후 자체 수집 데이터가 쌓이면 surprise 스코어링 검증 시도

| 상용 캘린더 후보 | 비고 |
|---|---|
| Trading Economics | 유료, 과거 consensus 포함 |
| Finnhub | freemium, economic calendar 제공. **상업적 사용 조건 확인 필요** |
| Financial Modeling Prep | freemium |

## 4. 뉴스 (D22 전략 입력)

> **원 명세 §9의 "라이선스/약관 준수, 무단 크롤링 금지" 원칙을 그대로 유지한다.**
> 언론사 웹사이트를 스크래핑하는 구조는 만들지 않는다.

| 소스 | 형태 | 라이선스 | 평가 |
|---|---|---|---|
| **GDELT Project** | 전 세계 뉴스 메타데이터, 15분 갱신 | 개방 | 무료·대량. 본문은 없고 메타데이터/URL 중심. **과거 데이터가 있어 유일하게 백테스트 가능성이 있는 소스** |
| **RSS (발행사 공식 피드)** | 제목·요약 | 피드별 약관 | 무료. 본문 재배포는 대개 불가 → **제목/요약만 저장** |
| **Finnhub / Marketaux / NewsAPI** | 집계 API | freemium, 상업적 사용 유료 | 편리하나 **과거 데이터 접근이 제한적** → 백테스트 불가 |
| Reuters / Bloomberg / Dow Jones | 엔터프라이즈 | 고가 계약 | 개인 프로젝트 범위 밖 |
| CryptoPanic 등 크립토 특화 | API | 약관 확인 필요 | 커버리지는 좋으나 품질 편차 |

**저장 정책**: 본문 전체를 DB에 보관하지 않는다. 제목 + 요약 + URL + 해시만 저장한다.
LLM 분석은 수집 시점에 수행하고 **결과(JSON)만** 보관한다.

**비용 개념 계산** (하루 100건 후보 → 상위 8건만 고급 모델):
- 1차 필터: 규칙 기반 (무료)
- 2차: 소형 모델 또는 임베딩 유사도
- 3차: 고급 LLM 8건/일 × 약 2~4K 토큰 → 월 비용은 수 달러 수준. **비용은 문제가 아니다.**
  문제는 **검증 불가능성**이다 (→ 12-critique §5)

## 5. LLM / JEV (D22 보조 분석)

| 항목 | 설계 |
|---|---|
| 제공자 | `LLMProvider`/JEV 프로토콜 뒤에 격리. 특정 공급자로 고정하지 않음 |
| 출력 강제 | tool use / JSON schema 로 구조화 출력. 자유 텍스트 파싱 금지 |
| 검증 | pydantic 으로 스키마 검증, 실패 시 1회 재시도 후 **해당 뉴스 분석 포기** (기본값 사용 금지) |
| 결정론 | `temperature=0` + `input_digest` 기반 캐시. 같은 입력 → 같은 출력을 **캐시로 보장** |
| 격리 | LLM 호출은 **별도 비동기 작업**. 매매 루프를 절대 블로킹하지 않음 |
| 장애 | 타임아웃 시 뉴스 기여도 = 0 (중립이 아니라 **비활성**). 손절·리스크 관리는 영향 없음 |
| 비용 통제 | 일일 호출 상한, 토큰 상한을 config 로 강제 |
| 기록 | `raw_response_json`, `model`, `prompt_version` 저장 → 모델 교체 시 비교 가능 |

`prompt_version` 을 반드시 기록한다. 프롬프트를 바꾸면 그것은 **다른 전략**이다.

## 6. 알림

| 항목 | 설계 |
|---|---|
| Telegram Bot API | `sendMessage`, long polling 으로 명령 수신 (`/status`, `/kill`, `/resume`, `/report`) |
| 인증 | 허용 `chat_id` 화이트리스트. 그 외 발신자 명령 무시 |
| 신뢰 경계 | **Telegram 명령은 "요청"이지 "지시"가 아니다.** `/kill` 같은 안전 방향 명령만 즉시 실행하고, 진입/사이즈 변경 명령은 제공하지 않는다 |
| 장애 | 전송 실패가 매매를 막지 않는다. 큐에 쌓아 재시도, 로컬 로그에는 항상 기록 |
| 레이트 | 봇당 제한이 있으므로 알림 집계·중복 억제 필요 |

## 7. 인프라

| 항목 | 선택 |
|---|---|
| 서버 | Linux VPS, **Binance 엔드포인트와 지연이 낮은 리전** (도쿄/싱가포르 권장) |
| 프로세스 관리 | `systemd` (Restart=always, RestartSec=10) — 메인 + 워치독 각각 유닛 |
| 시각 동기화 | `chrony` 또는 `systemd-timesyncd` 필수 |
| 시크릿 | 환경변수 + `systemd` `EnvironmentFile` (권한 600). 또는 `age`/`sops` 암호화 |
| 백업 | SQLite 파일 일일 스냅샷 + 오프사이트 복사 |
| 모니터링 | 로컬 구조화 로그 + Telegram. (선택) Prometheus textfile exporter |

**API 키 정책 (원 명세 §17)**
- 출금 권한 **비활성** (필수)
- IP 화이트리스트 활성 (서버 고정 IP 필요)
- Futures 거래 권한만 부여
- 키는 `.env` (gitignore) 또는 시크릿 매니저. `.env.example` 만 커밋
- **pre-commit 훅으로 시크릿 스캔** (`gitleaks` 또는 `detect-secrets`) — 사고를 막는 유일한 실용적 수단

## 8. 의존성 요약

| Phase | 반드시 필요 | 비용 |
|---|---|---|
| 1~4 (데이터·백테스트) | Binance public data, Binance REST | 무료 |
| 5~7 (paper/testnet) | + Binance testnet, Telegram | 무료 |
| 8~9 (실거래) | + Binance 실계정, VPS | VPS 월 $10~30 |
| 10 (거시) | + FRED/ALFRED/BLS, (선택) 상용 캘린더 | 무료 ~ 월 $50 |
| 11 (뉴스/LLM) | + 뉴스 소스, Anthropic API | 월 $10~100 |
