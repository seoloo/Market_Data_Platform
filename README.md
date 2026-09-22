# Auto_Trading

BTC/USDT 무기한 선물 자동매매 시스템 (Binance USD-M)

> # ⛔ 종료된 프로젝트 (2026-09-22)
>
> **방향성 예측 접근을 종료했습니다.** 검증 결과 거래 비용을 넘는 통계적 우위를
> 가용 데이터로 **증명할 수 없다**는 결론입니다.
>
> **실거래는 한 번도 하지 않았고, 실거래 코드 자체가 없습니다. 손실은 없습니다.**
>
> 전문: [docs/phase0/21-closure.md](docs/phase0/21-closure.md) · [PROGRESS.md](PROGRESS.md)

## 결론 요약

| 검증 대상 | 결과 |
|---|---|
| **H1** 4h 추세 + 15m 되돌림 + 5m 모멘텀 재개 | 기각 — 점수가 결과와 무관 (양수 비율 44~54%) |
| **H2** 포지셔닝 쏠림 해소 (OI·funding·롱숏비) | 기각 — 5개 중 18bps 통과 0개, 재현 실패 |
| **비용 절감** post-only 진입 | 닫힘 — 신호 시점 역선택이 수수료 절감을 압도 |

종료 근거는 가설이 아니라 산수다.

```
효과 ~20bps · 비용 18bps · 6h 수익률 SD 123bps · 연간 독립표본 190개
→ 2σ 로 분해하려면 1,814표본 = 9.5년.  가용 데이터 4.7년.
```

기준을 낮춘 적도, TEST 구간을 열어 전략을 고친 적도 없다. 게이트를 통과하지 못한 것이
[설계 문서가 처음부터 정상이라고 적어둔 결과](docs/phase0/07-backtest.md)다.

### 남은 자산

- **데이터** `data/trading.db` — 캔들 414만봉 · funding 7,305건 · 포지셔닝 327,958건
  (전량 SHA-256 검증 + 재집계 교차 검증 통과)
- **코드** 995 테스트 · mypy strict · Pure Core 경계 강제 · 이벤트 기반 백테스트 엔진
- **방법론** 사전 등록 선별 · 검정력 우선 계산 · embargo=horizon · 신호 시점 비용 측정

---

## 목적 (당시)

이 시스템의 목적은 가격 예측이 아니다.

> 여러 독립적인 시장 신호를 수집·수치화하고, **비용과 위험을 고려한 일관된 규칙**에 따라 거래하며,
> 모든 결과를 기록하여 **전략의 통계적 우위가 실제로 존재하는지 지속적으로 검증**하는 것.

따라서 우선순위는 이렇다: **기록·재현성 > 손실 통제 > 검증 가능성 > 수익**.

## 문서

| 문서 | 내용 |
|---|---|
| [PROGRESS.md](PROGRESS.md) | **현재 작업 현황** — 여기부터 읽는다 |
| [AGENTS.md](AGENTS.md) | AI 에이전트 작업 규약 (Claude / Codex 공통) |
| [docs/STRATEGY-CATALOG.md](docs/STRATEGY-CATALOG.md) | **매매 개념** — 어떤 지표로 언제 사고파는가 (사람용) |
| [docs/phase0/21-closure.md](docs/phase0/21-closure.md) | **종료 기록** — 무엇을 검증했고 왜 멈췄는가 |
| [docs/phase0/15-h1-postmortem.md](docs/phase0/15-h1-postmortem.md) | H1 사후 분석 |
| [docs/phase0/20-signal-time-fills.md](docs/phase0/20-signal-time-fills.md) | 비용 절감 경로가 닫힌 이유 |
| [docs/phase0/](docs/phase0/) | 전체 설계 문서 21종 |
| [docs/phase0/13-decisions.md](docs/phase0/13-decisions.md) | 확정 설정값 (최우선) |
| [docs/phase0/12-critique.md](docs/phase0/12-critique.md) | 설계 판단 근거 |
| [docs/phase0/14-ui-and-packaging.md](docs/phase0/14-ui-and-packaging.md) | 대시보드 · exe 배포 설계 |

## 검증했던 전략 (기각됨)

> 4시간 추세 방향으로만, 15분 눌림목이 끝나는 자리에서, 거래량이 받쳐줄 때 진입한다.
> 정상 변동성 구간에서만 거래하고, **한 번에 총액의 1%까지만 잃도록** 포지션 크기를 정한다.

**이 전략은 2026-09-22 에 기각되었다.** 아래 표는 당시 확정값 기록이다.

| 확정 사항 | 값 |
|---|---|
| 심볼 | BTCUSDT (One-way, Isolated) |
| 지표 | 5개 고정, 등가중 |
| 구조 | Bias(4h/1h) 게이트 × Trigger(15m/5m) 타이밍 |
| 보유기간 | 목표 2~24시간, 최대 제한 없음 (반대 점수 역전 시 신호 청산) |
| **거래당 최대 손실** | **총액의 1%** |
| 사이징 | 손절 위치에서 역산. 레버리지는 결과값 (최대 10x) |
| 하루 거래 | 최대 2회 |
| 결과 확인 | 로컬 대시보드 (읽기 전용) — Phase 6 |

## 개발 환경

```bash
python -m venv .venv
.venv/Scripts/activate          # Windows
# source .venv/bin/activate     # Linux / macOS
pip install -e ".[dev]"
pre-commit install
```

### 검사

```bash
pytest                   # 전체 테스트
pytest -m leakage        # look-ahead 누출 탐지 (스킵 금지)
mypy                     # 타입 검사 (strict)
ruff check . && ruff format --check .
lint-imports             # Pure Core 경계 검사
```

## 설계 원칙 (요약)

1. **Pure Core** — 전략·리스크 코드는 I/O 없는 순수 함수. 백테스트와 라이브가 같은 함수를 호출한다
2. **미래 데이터 접근 금지** — 정규화는 causal 창만, 형성 중인 봉은 쓰지 않는다
3. **거래소가 진실의 원천** — 로컬 DB와 다르면 거래소를 채택한다
4. **보호주문은 거래소에** — 프로세스가 죽어도 손절이 작동해야 한다
5. **멱등 주문** — 결정론적 `clientOrderId` 로 재시작 중복 진입을 차단한다
6. **Fail-closed / 비대칭** — 모호하면 진입하지 않는다. 다만 청산은 항상 허용한다
7. **모든 판단을 기록** — 주문 여부와 무관하게 매 평가마다 기록한다

전문: [AGENTS.md §3](AGENTS.md)

## 보안

- API 키는 **출금 권한 없이** 발급하고 IP 화이트리스트를 적용한다
- 시크릿은 `.env` (gitignore). `.env.example` 만 커밋한다
- `pre-commit` 의 gitleaks 가 시크릿 커밋을 차단한다

## 면책

이 소프트웨어는 수익을 보장하지 않는다. 검증되지 않은 전략으로 실거래를 하지 않는다.
암호화폐 선물 거래는 원금 전액을 잃을 수 있다.

## 라이선스

Proprietary. 무단 사용·배포 금지.
