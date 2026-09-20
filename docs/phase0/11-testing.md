# 11 — 테스트 전략 (N)

## 1. 테스트 계층

| 계층 | 대상 | 도구 | 실행 시점 |
|---|---|---|---|
| L1 단위 | feature, 정규화, 게이트, 사이징, money 반올림 | pytest | 매 커밋 |
| L2 속성 기반 | 상태머신 전이, 멱등성, 불변식 | hypothesis | 매 커밋 |
| L3 골든/스냅샷 | 백테스트 결과 재현성 | pytest + 고정 데이터셋 | 매 커밋 |
| L4 통합 | 모의 거래소 상대 전체 루프 | pytest + fake adapter | 매 커밋 |
| L5 장애 주입 | 타임아웃·429·WS단절·부분체결 | 커스텀 fake | 매 커밋 |
| L6 누출 탐지 | 1봉 지연, 신호 셔플 | 백테스트 하네스 | 백테스트마다 |
| L7 일치성 | Parity / Shadow backtest | 배치 | 라이브 일 1회 |
| L8 실환경 | Testnet 스모크 | 수동 + 스크립트 | Phase 7, 릴리스 전 |

---

## 2. L1 — 단위 테스트

### 2.1 Feature / 지표

```python
# 고정 입력 → 손으로 계산한 기대값
def test_ema_matches_reference():
    closes = [...]                      # 고정 픽스처
    assert ema(closes, 3) == pytest.approx([...], abs=1e-9)
```

- 참조 구현(TA-Lib/pandas-ta)과 **교차 검증**하되, 테스트 자체는 고정 숫자로 고정한다
  (외부 라이브러리 버전 변경에 테스트가 흔들리면 안 된다)
- 경계: 데이터 부족, 전부 동일값(분산 0), NaN 입력

### 2.2 정규화 (look-ahead 방지)

```python
def test_causal_zscore_ignores_future():
    full   = causal_zscore(series[:100], lookback=50)
    padded = causal_zscore(series[:100] + [999999.0], lookback=50)  # 미래 추가
    # 인덱스 99 기준 값은 변하지 않아야 한다
    assert full == padded_at_index_99
```

이 테스트는 **누출 방지의 1차 방어선**이다. 모든 정규화 함수에 대해 작성한다.

### 2.3 Money / 정밀도

```python
@pytest.mark.parametrize("qty,step,expected", [
    ("0.0019", "0.001", "0.001"),
    ("0.0010", "0.001", "0.001"),
    ("0.00099","0.001", "0.000"),      # -> BELOW_MIN_SIZE 로 이어져야 함
])
def test_floor_to_step(qty, step, expected): ...
```

- 항상 **내림**임을 검증 (올림이면 증거금 초과 주문이 나간다)
- Decimal 왕복(문자열 ↔ Decimal) 무손실 검증

### 2.4 Risk 게이트

게이트 하나당 최소 3케이스: **통과 / 경계 / 거부**.

```python
def test_daily_loss_gate_blocks_at_limit():
    st = RiskState(day_realized_pnl=Decimal("-200"), ...)
    pf = PortfolioState(equity=Decimal("10000"), ...)
    v = gates.daily_loss(proposal, pf, st, cfg)      # limit 2% = 200
    assert v.veto and VetoReason.DAILY_LOSS_LIMIT in v.reasons
```

전 게이트 목록에 대한 **커버리지 테이블**을 테스트로 강제한다:

```python
def test_all_gates_have_tests():
    assert set(ALL_GATE_IDS) == set(TESTED_GATE_IDS)   # 게이트 추가 시 테스트 누락 방지
```

### 2.5 Sizing

- 위험 예산 ↔ 손절 거리 ↔ 수량의 관계 검증
- 상한이 실제로 적용되는지 (명목가, 레버리지, 증거금, 거래소 최대)
- `liq_distance >= 2 × stop_distance` 위반 케이스가 거부되는지
- 손절 거리 0 / 극소 케이스에서 폭주하지 않는지

---

## 3. L2 — 속성 기반 테스트 (hypothesis)

```python
@given(events=st.lists(order_events()))
def test_order_state_machine_never_enters_invalid_state(events):
    sm = OrderStateMachine()
    for e in events:
        sm.apply(e)                    # 잘못된 전이는 예외를 던져야 함
        assert sm.state in VALID_STATES
        assert sm.filled_qty <= sm.qty
```

검증할 속성:

| 속성 | 내용 |
|---|---|
| P-1 | 주문 상태는 정의된 전이로만 이동한다 |
| P-2 | `filled_qty` 는 단조 증가하고 `qty` 를 넘지 않는다 |
| P-3 | 같은 이벤트를 두 번 적용해도 상태가 같다 (멱등) |
| P-4 | 포지션 수량 = fills 합계 (부호 포함) |
| P-5 | `decide()` 는 동일 입력에 동일 출력 (결정론) |
| P-6 | `decide()` 결과가 `OPEN` 이면 반드시 stop intent 를 포함한다 |
| P-7 | 어떤 입력에서도 `qty > 0` 이면 리스크 한도를 넘지 않는다 |
| P-8 | `clientOrderId` 는 같은 `(as_of_ms, purpose)` 에 대해 항상 같다 |

---

## 4. L3 — 골든 테스트

```
tests/golden/
  dataset_2021Q1_1m.parquet     # 고정 데이터 (커밋)
  expected_summary.json         # 기대 지표
  expected_trades.csv
```

```python
def test_backtest_golden():
    result = run_backtest(GOLDEN_DATASET, GOLDEN_CONFIG)
    assert result.metrics_hash == EXPECTED_HASH
```

- 코어 로직 변경 시 이 테스트가 깨진다 → **의도적 변경임을 확인하고 기대값을 갱신**
- 커밋 메시지에 "왜 결과가 바뀌었는가"를 기록하도록 규약화
- 이것이 없으면 리팩터링이 조용히 전략을 바꾼다

---

## 5. L4 — 통합 테스트 (FakeExchange)

`tests/fakes/fake_exchange.py` — `ExchangeAdapter` 를 구현하는 인메모리 거래소.

기능:
- 주문 접수/체결/부분체결/거부
- 포지션·잔고 추적, 강제청산
- 상태 주입 API: "포지션은 있는데 주문은 없는 상태로 만들어라"

시나리오 테스트:

| # | 시나리오 | 기대 |
|---|---|---|
| I-1 | 정상 진입 → 스톱 발동 → 청산 | trades 1건, close_reason='stop' |
| I-2 | 진입 후 프로세스 재시작 | 포지션 정확 복구, 중복 진입 없음 |
| I-3 | 진입 체결 후 스톱 등록 실패 3회 | 포지션 즉시 청산 + HALTED |
| I-4 | 로컬 FLAT / 거래소 포지션 있음 | orphan 처리 정책대로 동작 + risk_event |
| I-5 | 부분 체결 후 나머지 취소 | 포지션 수량 정확, 보호주문 유효 |
| I-6 | 일일 손실 한도 도달 | 이후 진입 전부 거부, 청산은 가능 |
| I-7 | 킬스위치 | 신규 차단, 기존 포지션 보호 유지 |
| I-8 | 외부(수동) 주문 감지 | 취소하지 않고 HALTED + 알림 |

---

## 6. L5 — 장애 주입 테스트

`FakeExchange` 에 결함 모드를 주입한다.

| 결함 | 검증 항목 |
|---|---|
| POST 응답 유실 (타임아웃) | 주문이 UNKNOWN 으로 가고, 재시도 전 조회하며, 중복 주문 없음 |
| 429 / 418 | 백오프 동작, DEGRADED 전환, 복구 |
| WS 무통보 단절 | 하트비트 감지, 재연결, REST 갭 보정으로 유실 이벤트 복원 |
| 부분 체결 후 침묵 | 타임아웃 후 잔량 취소, 포지션 정합 |
| 이상 가격 틱 (0, 음수, 50% 점프) | 게이트 차단, quarantine, 주문 미발생 |
| DB 잠금 | 재시도 후 DEGRADED, 거래소 스톱은 유효 유지 |
| LLM/뉴스 타임아웃 | 매매 루프 지연 없음 (NFR-1 유지), 해당 요소 비활성 |
| 시각 drift 10초 | 거래 중단 |

```python
def test_lost_response_does_not_duplicate_order():
    ex = FakeExchange(fault=LoseResponse(on="POST /order", times=1))
    om = OrderManager(ex, db)
    om.execute(intent)          # 타임아웃
    om.execute(intent)          # 재시도 (같은 clientOrderId)
    assert ex.order_count() == 1
```

---

## 7. L6 — 누출 탐지 테스트

백테스트 하네스에 내장하여 **모든 백테스트 실행 시 자동 수행**한다.

| 테스트 | 방법 | 통과 기준 |
|---|---|---|
| 1봉 지연 | 모든 Intent 실행을 1봉 늦춤 | 성과가 감소는 하되 **부호가 유지**. 급락하면 신호가 봉 마감 정보에 과도 의존 (누출 의심) |
| 신호 셔플 | direction 계열을 무작위 순열로 치환 | 성과가 **랜덤 수준으로 소멸**해야 함. 남아 있으면 성과가 신호가 아닌 다른 것(예: 사이징 편향)에서 옴 |
| 미래 마스킹 | `as_of` 이후 데이터를 NaN 으로 채움 | 결과가 **완전히 동일**해야 함. 다르면 어딘가에서 미래를 읽고 있음 |

**미래 마스킹 테스트가 가장 강력하다.** 구현:

```python
def test_no_future_access():
    store_full   = HistoricalStore(data)
    store_masked = MaskedStore(data, mask_after=as_of)    # as_of 이후 접근 시 예외
    d1 = decide(SnapshotBuilder(store_full).build(as_of), pf, cfg)
    d2 = decide(SnapshotBuilder(store_masked).build(as_of), pf, cfg)
    assert d1.digest == d2.digest
```

`MaskedStore` 가 미래 데이터 접근 시 **예외를 던지게** 하면 누출 위치를 스택트레이스로 특정할 수 있다.

---

## 8. L7 — 일치성 테스트 (라이브 운영 중)

### Parity Test (일 1회 배치)

```
어제의 모든 라이브 Decision 에 대해:
  저장된 snapshot_digest 로 스냅샷 재구성
  -> decide() 재실행
  -> Decision 해시 비교
불일치 1건이라도 → critical 알림 + 조사
```

### Shadow Backtest (일 1회)

라이브와 동일 구간 백테스트 → 진입/청산 시점 비교 (→ 07 §6.2).
체결가 차이는 슬리피지 모델 보정 입력으로 축적한다.

---

## 9. CI 구성

```yaml
# 개념
jobs:
  quality:
    - ruff check / ruff format --check
    - mypy --strict src/auto_trading/{core,features,strategy,risk,execution}
    - import-linter          # 의존 방향 강제
    - gitleaks detect        # 시크릿 스캔
  test:
    - pytest -m "not slow" --cov
    - coverage 임계: core/strategy/risk/execution >= 85%
  golden:
    - pytest tests/golden
  leakage:
    - pytest tests/leakage
```

**머지 차단 조건**: 위 전부 통과. 특히 `leakage` 와 `golden` 실패는 예외 없이 차단.

---

## 10. 테스트하지 *않는* 것 (명시)

| 항목 | 이유 |
|---|---|
| 전략의 수익성 | 테스트가 아니라 **검증 프로토콜**(07 문서)의 영역. CI에 수익 기준을 넣으면 그것 자체가 과최적화 압력이 된다 |
| 외부 API 실호출 | CI에서는 전부 fake. 실호출은 Phase 7 수동 스모크 |
| LLM 출력 내용 | 스키마 준수와 실패 처리만 테스트. 출력 품질은 테스트 대상이 아님 |

---

## 11. 수동 검증 체크리스트 (릴리스 전)

- [ ] `config diff` 검토 — 리스크 한도가 의도치 않게 완화되지 않았는가
- [ ] `git status` clean, 태그 부여
- [ ] Testnet 스모크: 진입 → 스톱 → 청산 1회
- [ ] 워치독 동작 확인 (인위적 INV-1 위반 주입)
- [ ] 킬스위치 동작 확인
- [ ] 재시작 복구 확인 (`kill -9` 후 상태 일치)
- [ ] Telegram 알림 수신 확인
- [ ] DB 백업 최신
