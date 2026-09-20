# 14 — 운영 UI 및 배포 형태

> **상태**: 설계 확정 ([13-decisions.md](13-decisions.md) **D10**) / 구현은 Phase 6·7
> **작성**: 2026-09-21

---

## 1. 무엇을 만드는가

### 1.1 대시보드 (읽기 전용 웹 UI)

자동매매 결과를 눈으로 확인하는 화면. **주문을 내지 않는다. 설정을 바꾸지 않는다.**

| 화면 | 내용 | 데이터 출처 |
|---|---|---|
| **요약** | 현재 자산, 오늘 손익, 누적 수익률, 최대 DD, 현재 포지션, 시스템 상태 | `account_snapshots`, `positions`, `heartbeats` |
| **자산 곡선** | equity 시계열 + 낙폭(drawdown) 차트 | `account_snapshots` |
| **달력** | 날짜별 손익 히트맵 (바이낸스 스타일) | `v_daily_pnl` |
| **거래 목록** | 진입·청산 시각, 방향, R 배수, 청산 사유, 수수료, 슬리피지 | `trades` |
| **거래 상세** ⭐ | **"왜 이 거래를 했는가"** — 점수 분해, 게이트 통과 내역, 비용 추정, 사이징 계산 | `v_trade_explain` |
| **거부 사유 분포** | Risk Engine 이 무엇 때문에 진입을 막았는지 통계 | `decisions.veto_reasons` |
| **시스템 로그** | 최근 `risk_events`, `system_events`, `errors` | 해당 테이블 |

**거래 상세 화면이 이 시스템의 차별점이다.** 재현성을 위해 만든 `decisions` 저널을
사람이 읽을 수 있게 보여주는 곳이다. 다른 대시보드에는 없는 화면이다.

### 1.2 달력 화면 (바이낸스 스타일)

```
        2026년 9월              실현손익 합계: +142.30 USDT (+1.42%)
 ┌──────┬──────┬──────┬──────┬──────┬──────┬──────┐
 │  일  │  월  │  화  │  수  │  목  │  금  │  토  │
 ├──────┼──────┼──────┼──────┼──────┼──────┼──────┤
 │      │  1   │  2   │  3   │  4   │  5   │  6   │
 │      │ ░░░  │ ▓▓▓  │ ░░░  │      │ ███  │ ▒▒▒  │
 │      │+12.4 │-31.0 │ +8.2 │  —   │+45.1 │ -9.3 │
 ├──────┼──────┼──────┼──────┼──────┼──────┼──────┤
 │  7   │  8   │ ...                               │
 └──────┴──────┴──────┴──────┴──────┴──────┴──────┘
   ███ 큰 이익   ░░░ 작은 이익   ▒▒▒ 작은 손실   ▓▓▓ 큰 손실   — 거래 없음

 날짜 클릭 → 그날의 거래 목록 → 거래 클릭 → 판단 근거
```

**표시 지표 2종** (토글)

| 지표 | 정의 | 용도 |
|---|---|---|
| **실현손익** (기본) | 그날 **청산된** 거래의 net_pnl 합 (수수료·펀딩 포함) | 바이낸스와 같은 기준 |
| 자산 변동 | 그날 equity 종가 − 전일 equity 종가 | 미실현 포함. 실제 계좌 변화 |

두 값은 포지션을 하루 넘겨 보유하면 달라진다. **둘 다 보여주고 차이를 설명한다.**

**시간대 주의**: 기본 **UTC**. Risk Engine 의 일일 손실 한도 리셋이 UTC 00:00 이므로
같은 기준을 써야 "오늘 왜 거래가 멈췄지?"를 추적할 수 있다.
KST 토글을 제공하되 화면에 시간대를 항상 표시한다. **시간대가 다르면 날짜별 손익이 달라진다.**

### 1.3 실행 파일 (.exe)

| 산출물 | 용도 | 대상 |
|---|---|---|
| `AutoTradingDashboard.exe` | 대시보드 실행 → 브라우저 자동 열림 | **권장. 주 용도** |
| `AutoTradingBot.exe` | 매매 엔진 (백테스트·Paper 전용) | 제한적 (§4.2) |

---

## 2. 설계 원칙 — 안전이 우선

### 2.1 ⭐ UI 는 별도 프로세스이며 읽기 전용이다

```
 ┌─────────────────────┐          ┌──────────────────────┐
 │  매매 프로세스       │  write   │                      │
 │  (main.py)          │ ───────▶ │   SQLite (WAL)       │
 └─────────────────────┘          │   data/trading.db    │
                                  │                      │
 ┌─────────────────────┐  read    │                      │
 │  대시보드 프로세스   │ ◀─────── │                      │
 │  (dashboard.py)     │  ONLY    └──────────────────────┘
 │  127.0.0.1:8787     │
 └─────────────────────┘
          ▲
          │ 브라우저
       사용자
```

| 규칙 | 이유 |
|---|---|
| **별도 프로세스** | 대시보드가 멈추거나 죽어도 매매는 계속된다. UI 버그가 자금에 닿지 않는다 |
| **DB 를 읽기 전용으로 연다** | `sqlite3.connect("file:...?mode=ro", uri=True)`. 쓰기 자체가 **물리적으로 불가능** |
| **쓰기 엔드포인트 없음** | 주문·설정 변경 API 를 만들지 않는다. 있으면 언젠가 사고가 난다 |
| **`127.0.0.1` 만 바인딩** | 외부 노출 금지. 원격 접속이 필요하면 SSH 터널 |
| **API 키를 프로세스에 로드하지 않음** | 대시보드는 거래소 자격증명을 아예 모른다 |
| **거래소 API 직접 호출 금지** | 모든 데이터는 DB 에서만. 레이트 리밋 경합 방지 |

> **킬 스위치를 UI 에 넣지 않는다.** 버튼 하나를 위해 쓰기 경로·인증·CSRF 방어가 필요해진다.
> 킬 스위치는 이미 세 경로가 있다: 파일(`data/KILL`), Telegram `/kill`, 워치독 자동 발동
> ([05-risk-engine.md](05-risk-engine.md) §5).

### 2.2 WAL 동시 접근

SQLite WAL 모드는 **쓰기 1 + 읽기 N** 동시 접근을 허용한다. 대시보드는 읽기만 하므로 안전하다.

```python
# 대시보드 측 연결
conn = sqlite3.connect("file:data/trading.db?mode=ro", uri=True, timeout=5.0)
conn.execute("PRAGMA query_only = ON")      # 이중 방어
```

- 읽기 쿼리가 길어지면 WAL 파일이 커진다 → 집계 쿼리에 시간 범위 제한을 건다
- 대시보드는 **5초 폴링**으로 갱신한다. WebSocket 은 복잡도 대비 이득이 없다

### 2.3 백테스트 결과도 같은 화면에서 본다

`backtest_runs.run_id` 로 필터링하면 동일한 UI 로 백테스트 결과를 볼 수 있다.
별도 뷰어를 만들지 않는다. **모드 선택 드롭다운**: `live / paper / testnet / backtest:<run_id>`

이것이 우리 스키마가 처음부터 `mode` 와 `run_id` 를 모든 테이블에 넣어둔 이유다
([03-data-model.md](03-data-model.md)).

---

## 3. 기술 선택

### 3.1 스택

| 계층 | 선택 | 대안과 비교 |
|---|---|---|
| 백엔드 | **FastAPI + uvicorn** | Streamlit 은 빠르지만 exe 패키징이 무겁고 레이아웃 통제가 어렵다 |
| 템플릿 | **Jinja2 서버 렌더링** | React/Vue 는 npm 빌드 단계가 생겨 PyInstaller 와 CI 가 복잡해진다 |
| 차트 | **Chart.js (로컬 번들)** | CDN 금지 — exe 는 오프라인에서도 동작해야 한다 |
| 스타일 | 단일 CSS 파일 | 빌드 도구 없음 |

**npm 빌드 단계를 만들지 않는 것**이 핵심 제약이다. Python 만으로 빌드가 끝나야
PyInstaller 와 CI 가 단순해진다.

### 3.2 모듈 구조 추가

```
src/auto_trading/
  ui/                          # [신설] 대시보드 — 읽기 전용
    __init__.py
    app.py                     # FastAPI 앱
    db.py                      # 읽기 전용 커넥션 (query_only)
    queries.py                 # 집계 쿼리 (SQL 만. 매매 로직 없음)
    routes/
      summary.py
      equity.py
      calendar.py
      trades.py
      diagnostics.py           # veto 분포, 시스템 로그
    templates/
    static/
      css/  js/  vendor/chart.min.js
dashboard.py                   # [신설] 대시보드 엔트리포인트
```

**의존 방향**: `ui` 는 `core` 와 `storage` 만 import 한다.
`strategy`, `risk`, `exchange`, `execution` 을 import 하지 않는다 — import-linter 계약에 추가한다.

```toml
[[tool.importlinter.contracts]]
name = "UI 는 매매 로직을 import 하지 않는다"
type = "forbidden"
source_modules = ["auto_trading.ui"]
forbidden_modules = [
    "auto_trading.exchange",
    "auto_trading.execution",
    "auto_trading.strategy",
    "auto_trading.risk",
    "auto_trading.ai",
]
```

### 3.3 DB 추가 사항

기존 스키마로 대부분 커버되지만 집계 뷰를 추가한다.

```sql
-- 날짜별 실현손익 (달력 화면)
CREATE VIEW v_daily_pnl AS
SELECT
    date(t.closed_ms / 1000, 'unixepoch')       AS trade_date,   -- UTC
    p.mode,
    p.run_id,
    COUNT(*)                                     AS trade_count,
    SUM(CAST(t.net_pnl AS REAL))                 AS realized_pnl,
    SUM(CAST(t.gross_pnl AS REAL))               AS gross_pnl,
    SUM(CAST(t.entry_fee AS REAL) + CAST(t.exit_fee AS REAL)) AS fees,
    SUM(CAST(t.funding_paid AS REAL))            AS funding,
    SUM(CASE WHEN CAST(t.net_pnl AS REAL) > 0 THEN 1 ELSE 0 END) AS wins,
    AVG(t.realized_r)                            AS avg_r
FROM trades t
JOIN positions p ON p.id = t.position_id
GROUP BY trade_date, p.mode, p.run_id;

-- 날짜별 자산 변동 (미실현 포함)
CREATE VIEW v_daily_equity AS
SELECT
    date(ts_ms / 1000, 'unixepoch')  AS trade_date,
    mode,
    MIN(ts_ms)                       AS first_ms,
    MAX(ts_ms)                       AS last_ms,
    MAX(CAST(equity AS REAL))        AS equity_high,
    MIN(CAST(equity AS REAL))        AS equity_low
FROM account_snapshots
GROUP BY trade_date, mode;
```

> `CAST(... AS REAL)` 을 쓰는 이유: 우리는 금액을 `TEXT`(Decimal) 로 저장한다.
> **집계와 표시에만 REAL 로 변환**하며, 이 값이 매매 판단에 쓰이는 일은 없다.
> 계산 경로는 여전히 `Decimal` 이다 ([03-data-model.md](03-data-model.md) §1).

`account_snapshots` 기록 주기를 **1분** 으로 정한다 (자산 곡선 해상도).
연 52.6만 행 × 작은 행 크기 → 문제 없음.

---

## 4. exe 패키징

### 4.1 방식

| 항목 | 선택 |
|---|---|
| 도구 | **PyInstaller** (`--onedir` 권장, `--onefile` 은 기동이 느리다) |
| 빌드 | GitHub Actions `windows-latest` 에서 태그 푸시 시 아티팩트 생성 |
| 버전 | `--version` 플래그가 git sha 와 DB 스키마 버전을 출력 |
| 시크릿 | **exe 에 번들하지 않는다.** exe 옆의 `.env` 를 읽는다 |
| 데이터 | `data/trading.db` 는 exe 옆 폴더. exe 안에 넣지 않는다 |

```
AutoTrading/
├── AutoTradingDashboard.exe
├── AutoTradingBot.exe
├── .env                    ← 사용자가 직접 작성. 절대 배포에 포함하지 않음
├── config/
│   └── base.yaml
└── data/
    └── trading.db
```

대시보드 exe 는 실행 시 `http://127.0.0.1:8787` 을 **기본 브라우저로 자동 열고**,
콘솔 창에는 종료 방법만 표시한다.

### 4.2 ⚠ 매매 엔진을 exe 로 24시간 돌리는 것에 대한 경고

**대시보드 exe 는 권장한다. 매매 엔진 exe 는 용도를 제한한다.**

| 모드 | Windows exe | 이유 |
|---|---|---|
| 백테스트 | ✅ 문제 없음 | 일회성 실행 |
| Paper Trading | ✅ 가능 | 자금이 없음. 중단돼도 데이터 공백만 생김 |
| Testnet | ⚠️ 가능하나 비권장 | |
| **실거래** | ❌ **권장하지 않음** | 아래 참조 |

Windows 개인 PC 를 24시간 매매 호스트로 쓰면 다음이 발생한다:

| 문제 | 결과 |
|---|---|
| **절전 모드 / 화면 잠금** | 프로세스 정지 → 봉 누락, 청산 신호 놓침 |
| **Windows 자동 업데이트 재부팅** | 예고 없이 종료. 포지션 보유 중이면 무방비 |
| 인터넷 끊김 (가정용 회선) | WS 단절, 재연결 실패 |
| 노트북 덮개 닫기 | 즉시 정지 |

우리 설계는 이 상황을 **이미 대비**하고 있다 — 거래소에 걸어둔 `STOP_MARKET` 은
프로그램이 죽어도 작동하고([05-risk-engine.md](05-risk-engine.md) §2 L2),
재시작 시 reconciliation 이 상태를 복구한다([04-state-machine.md](04-state-machine.md) §4).

**하지만 "손절은 되지만 익절·신호청산은 안 되는" 상태가 몇 시간 이어질 수 있다.**
우리 전략의 주 탈출 경로는 신호 청산이므로 ([13-decisions.md](13-decisions.md) D9 §0),
이건 전략을 반쯤 무력화하는 것이다.

→ **실거래(Phase 8~9)는 Linux VPS + systemd 를 유지한다.**
→ Windows 에서 꼭 돌려야 한다면: 절전 비활성화 + Windows 서비스 등록 + 자동 재시작 + 자동 업데이트 지연.
   이 경우에도 **대시보드로 하트비트를 확인하는 습관**이 필요하다.

### 4.3 exe 의 기술적 제약

| 제약 | 대응 |
|---|---|
| **백신 오탐** | 서명되지 않은 PyInstaller exe 는 Windows Defender 가 격리할 수 있다. 코드 서명 인증서는 연 10만원대. 초기엔 예외 등록으로 대응 |
| **용량 50~120MB** | `--onedir` + 불필요 패키지 제외(`--exclude-module`)로 축소 |
| **자동 업데이트 없음** | 새 버전은 수동 교체 |
| **DB 스키마 불일치** | exe 가 DB 보다 오래된 경우 → **기동 시 `schema_version` 검사 후 명확한 메시지로 중단**. 조용히 잘못 읽는 것이 최악이다 |
| 기동 속도 | `--onedir` 로 2~4초. `--onefile` 은 압축 해제 때문에 10초+ |

```python
# 대시보드 기동 시 필수 검사
EXPECTED_SCHEMA = 7
actual = read_schema_version(db_path)
if actual != EXPECTED_SCHEMA:
    fail(f"DB 스키마 v{actual} / 대시보드 v{EXPECTED_SCHEMA} 불일치. "
         f"최신 버전을 내려받으세요.")
```

---

## 5. 구현 시점

| Phase | 항목 | 이유 |
|---|---|---|
| **Phase 1** | `account_snapshots` 1분 기록, `v_daily_pnl` / `v_daily_equity` 뷰 | **지금 스키마에 넣어둬야 나중에 데이터가 있다.** 뒤늦게 추가하면 과거 구간이 비어 있다 |
| Phase 3 | 백테스트 리포트 (정적 파일) | 이미 계획됨 |
| **Phase 6** | **대시보드 MVP** (요약·자산곡선·달력·거래목록·거래상세) | Paper Trading 4주를 지켜봐야 하므로 이때 실제로 필요해진다 |
| Phase 6 말 | `AutoTradingDashboard.exe` | 대시보드가 안정된 뒤 |
| Phase 9+ | 진단 화면 확장 (veto 분포, regime 분해) | 실거래 데이터가 쌓인 뒤 |
| 보류 | 모바일 레이아웃, 다중 계정, 원격 접속 | 필요해지면 |

**Phase 1 에 스키마만 미리 넣는 것이 핵심이다.** UI 구현은 미뤄도 되지만,
기록하지 않은 데이터는 나중에 복구할 수 없다.

---

## 6. MVP 대시보드에서 제외할 것

| 제외 | 이유 |
|---|---|
| 주문 실행 / 설정 변경 | §2.1 — 쓰기 경로를 만들지 않는다 |
| 킬 스위치 버튼 | Telegram·파일·워치독으로 충분 |
| 로그인 / 사용자 관리 | localhost 전용이므로 불필요 |
| 실시간 차트(캔들) | TradingView 를 쓰면 된다. 우리가 만들 이유가 없다 |
| WebSocket 실시간 갱신 | 5초 폴링으로 충분 |
| 모바일 앱 | Telegram 알림이 그 역할을 한다 |
| 전략 파라미터 편집 UI | config 파일 + git 이력이 더 안전하다 |

---

## 7. 테스트

| 항목 | 방법 |
|---|---|
| **읽기 전용 보장** | 대시보드 연결로 `INSERT` 시도 → 반드시 예외 발생 (테스트로 고정) |
| 쓰기 엔드포인트 부재 | FastAPI 라우트를 순회해 `POST/PUT/DELETE/PATCH` 가 0개인지 검사 |
| 바인딩 주소 | 기본값이 `127.0.0.1` 인지 검사 |
| import 경계 | import-linter 계약 (§3.2) |
| 집계 정확성 | 고정 DB 픽스처 → `v_daily_pnl` 결과가 수기 계산과 일치 |
| 스키마 버전 검사 | 불일치 DB 를 주면 명확한 메시지로 기동 실패 |
| 시간대 | UTC/KST 토글 시 날짜 경계가 올바르게 이동하는지 |
