> STATUS: HISTORICAL / NOT CURRENT
> 보관일: 2026-09-23 · 원래 경로: `docs/phase0/04-state-machine.md`
> 승인된 문서 정리 직전 작업본. D22 및 대체된 결정을 현행 사양으로 사용하지 않는다.
> 본문은 보존하고 Markdown 상대 링크만 새 위치에 맞췄다. 현재 기준: [CURRENT_SPEC](../../current/CURRENT_SPEC.md).

# 04 — 주문 / 포지션 State Machine 과 정합성 복구 (F)

## 1. 멱등성 설계 — 중복 진입 방지의 핵심

### clientOrderId 생성 규칙 (결정론적)

```
client_order_id = f"{env}{ver}-{run}-{decision_key}-{purpose}-{seq}"
```

| 구성 | 예 | 설명 |
|---|---|---|
| `env` | `L` / `T` / `P` | live / testnet / paper |
| `ver` | `1` | id 포맷 버전 |
| `run` | `a3f2` | 라이브 세션이 아니라 **전략 인스턴스 id** (재시작해도 동일) |
| `decision_key` | base36(as_of_ms/1000) | 평가 기준 봉 마감 시각 |
| `purpose` | `E`/`X`/`S`/`T` | entry / exit / stop / take-profit |
| `seq` | `0`,`1` | 동일 목적 재시도 시 증가 (명시적 재발행 시에만) |

Binance USD-M `newClientOrderId` 제약: 최대 36자, `^[\.A-Z\:/a-z0-9_-]{1,36}$` → 위 형식은 여유 있게 들어간다.

**핵심 성질**: 같은 봉(`as_of_ms`)에 대한 진입 의도는 프로세스가 몇 번 재시작되든 **같은 id** 를 만든다.
거래소는 동일 `clientOrderId` 를 거부하므로(`-4015`/duplicate), 중복 진입이 **구조적으로** 차단된다.

> 이것이 "로컬 DB에 플래그를 남긴다" 보다 강한 이유:
> DB 쓰기 직전에 죽어도 거래소 쪽에서 막힌다.

### 진입 전 2단 검사

```
1. 로컬: 동일 client_order_id 가 orders 에 있는가?  → 있으면 그 주문의 상태를 조회하고 종료
2. 원격: GET /fapi/v1/order?origClientOrderId=...   → 존재하면 로컬 동기화 후 종료
3. 둘 다 없음 → 신규 전송
```

`seq` 를 증가시켜 새 id로 재발행하는 것은 **오직** 다음 경우만 허용한다:
- 원격 조회 결과가 명확히 `CANCELED` / `EXPIRED` / `REJECTED`
- 그리고 Risk Engine이 재차 승인
- 그리고 재시도 횟수 한도 내

---

## 2. 주문 상태머신

```
                    +------------------+
                    |   PENDING_NEW    |  intent 생성, 아직 미전송
                    +---------+--------+
                              | send()
                              v
                    +------------------+
          +---------|    SUBMITTING    |  전송 중 (응답 대기)
          |         +---------+--------+
          | timeout/          | ack
          | conn error        v
          |         +------------------+
          |         |       NEW        |  거래소 접수됨 (미체결)
          |         +----+--------+----+
          |              |        |
          |    partial   |        | full fill
          |              v        v
          |     +---------------+  +-----------+
          |     |PARTIALLY_FILLED|->|  FILLED   | (terminal)
          |     +-------+-------+  +-----------+
          |             |
          |             | cancel / expire
          |             v
          |       +-----------+     +-----------+     +-----------+
          |       | CANCELED  |     | REJECTED  |     |  EXPIRED  | (terminal)
          |       +-----------+     +-----------+     +-----------+
          |
          v
   +--------------+
   |   UNKNOWN    |  <== 가장 위험한 상태. 반드시 명시적으로 모델링한다
   +------+-------+
          | reconcile(origClientOrderId) 조회
          v
   위 상태 중 하나로 해소  (조회 실패 시 UNKNOWN 유지 + 신규진입 차단)
```

### UNKNOWN 상태 규칙 (필수)

- `UNKNOWN` 인 주문이 하나라도 있으면 **해당 심볼의 신규 진입 금지**
- 해소 시도: 지수 백오프로 `origClientOrderId` 조회 (1s, 2s, 4s, 8s, ...)
- 60초 내 해소 실패 → `DEGRADED` 전환 + Telegram critical 알림
- **절대 금지**: UNKNOWN 상태에서 "아마 실패했겠지" 하고 재주문

---

## 3. 포지션 상태머신

```
              +--------+
              |  FLAT  |<---------------------------+
              +---+----+                            |
                  | entry intent dispatched         |
                  v                                 |
             +---------+                            |
             | OPENING |  진입 주문 진행 중          |
             +----+----+                            |
                  | first fill                      |
                  v                                 |
            +-----------+                           |
      +---->| UNPROTECTED|  체결됐으나 스톱 미등록  | <== 최대 위험 구간
      |     +-----+-----+                           |
      |           | 보호주문 등록 성공               |
      |           v                                 |
      |      +---------+                            |
      |      |  OPEN   |<----+                      |
      |      +----+----+     | 스톱 갱신(trailing)  |
      |           |          |                      |
      |           +----------+                      |
      |           |                                 |
      |           | reduce intent                   |
      |           v                                 |
      |     +-----------+                           |
      |     | REDUCING  |---------> OPEN            |
      |     +-----------+                           |
      |           |                                 |
      |           | close intent / stop hit         |
      |           v                                 |
      |      +---------+                            |
      |      | CLOSING |                            |
      |      +----+----+                            |
      |           | fully closed                    |
      |           v                                 |
      |      +---------+                            |
      |      | CLOSED  |----------------------------+
      |      +---------+
      |
      |  보호주문 등록 실패 (N회 재시도 후)
      +---------------------------------> 즉시 FLATTEN (시장가 청산)

  임의 상태 --(기동 / 불일치 감지)--> RECONCILING --> 위 상태 중 하나
```

### UNPROTECTED 구간 — 설계상 가장 중요한 부분

Binance USD-M에는 "진입 + 스톱"을 원자적으로 거는 수단이 없다.
따라서 체결 직후 스톱 등록까지의 창(window)이 반드시 존재한다. 이 창을 최소화하고 감시한다.

규칙:

1. 진입 주문 전송 **직후**(체결 확인 전) 스톱 등록을 준비해 둔다
2. 첫 체결 이벤트 수신 → **즉시** `STOP_MARKET` + `closePosition=true` 등록
   - `closePosition=true` 를 쓰면 수량을 몰라도 전량 청산되므로 부분 체결 중에도 안전
   - `workingType=MARK_PRICE` (기본) — 순간 wick으로 인한 오발동 완화
3. 등록 실패 시 즉시 재시도 (최대 3회, 총 3초 이내)
4. 그래도 실패 → **포지션 시장가 전량 청산** 후 `HALTED`
5. `UNPROTECTED` 상태 지속 시간이 `max_unprotected_ms`(기본 5000ms)를 넘으면 위 4번 실행
6. 워치독이 독립적으로 5초마다 검사: "거래소에 포지션이 있는데 reduce-only 스톱이 없다" → critical 알림 + 킬스위치

### 불변식 (invariant) — 코드에서 assert 하고 워치독이 재검사

```
INV-1  position.state == OPEN  =>  거래소에 reduce-only stop 주문이 존재한다
INV-2  |로컬 포지션 수량 - 거래소 포지션 수량| <= step_size
INV-3  UNKNOWN 주문이 존재하면 신규 진입 intent 를 생성하지 않는다
INV-4  같은 symbol 에 대해 OPENING/OPEN/REDUCING/CLOSING 포지션은 최대 1개
INV-5  보호주문의 수량 <= 포지션 수량 (초과 시 반대 포지션이 열림)
INV-6  stop_price 는 진입가 기준 손실 방향에 있다 (방향 오류 방지)
```

---

## 4. 정합성 복구 (Reconciliation)

### 4.1 기동 시 절차

```
[0] 로컬 DB 로드 (마지막 알려진 상태)  — 신뢰하지 않음, 비교 대상으로만 사용
[1] 거래소 시간 동기화
      GET /fapi/v1/time  ->  |서버시각 - 로컬시각| > 1000ms 이면 경고
                             > 5000ms 이면 기동 중단 (서명 오류·오판 위험)
[2] 거래소 상태 조회 (모두 REST, WS 이전에)
      - GET /fapi/v2/account         : 잔고, equity
      - GET /fapi/v2/positionRisk    : 포지션 수량, 진입가, 레버리지, 청산가
      - GET /fapi/v1/openOrders      : 미체결 주문 전체
      - GET /fapi/v1/userTrades      : 마지막 처리 시각 이후 체결
      - GET /fapi/v1/income          : funding, realized pnl (마지막 처리 이후)
      - GET /fapi/v1/exchangeInfo    : tickSize/stepSize/minNotional (매 기동 갱신)
      - GET /fapi/v1/commissionRate  : 수수료율
[3] 차이 분류 및 처리 (아래 표)
[4] 로컬 DB를 거래소 상태로 정정, 모든 차이를 risk_events 에 기록
[5] 보호주문 검증 -> 없거나 부적합하면 재등록 (INV-1)
[6] 일일 손익/DD/연속손실 상태 재계산 (income + trades 기반)
[7] WS 연결, 마지막 처리 이벤트 이후 REST 갭 보정
[8] 캔들 무결성 검사 + backfill
[9] warm-up 완료 확인 (feature 별 warmup_bars 충족) -> 미충족이면 충족까지 진입 금지
[10] RUNNING 진입, Telegram "재시작 완료 + 복구 요약" 발송
```

### 4.2 차이 유형별 처리

| # | 로컬 | 거래소 | 처리 |
|---|---|---|---|
| 1 | FLAT | 포지션 있음 | **거래소 채택.** 포지션을 "고아(orphan)"로 등록. 진입 근거를 모르므로 **새 진입 규칙을 적용하지 않고**, 보호주문만 즉시 부착한 뒤 `orphan_policy` (config: `hold_with_stop` \| `flatten`) 실행. 기본값 `flatten` 권장 |
| 2 | OPEN | 포지션 없음 | 이미 청산됨. `userTrades` 로 청산 체결을 찾아 trade 기록 완성. 못 찾으면 `close_reason='unknown'` 으로 마감 |
| 3 | 수량 불일치 | | 거래소 수량 채택. 차이가 `step_size` 초과면 critical. 보호주문 수량 재조정 |
| 4 | 진입가 불일치 | | 거래소 `entryPrice` 채택 (부분 체결 평균 반영) |
| 5 | 레버리지 불일치 | | 거래소 값 채택 후 config 목표값으로 재설정 시도. 포지션 보유 중이면 변경 불가할 수 있음 → 경고만 |
| 6 | 로컬에 없는 미체결 주문 | | 우리 `clientOrderId` prefix면 입양(adopt). 아니면 **수동 개입 필요** → 알림 후 `HALTED` (사람이 넣은 주문을 임의로 취소하지 않는다) |
| 7 | 로컬 OPEN 주문이 거래소에 없음 | | `origClientOrderId` 로 이력 조회 → 체결/취소 확정 |
| 8 | 미처리 체결 존재 | | `fills` 에 삽입 (`exchange_trade_id` UNIQUE 로 멱등), 포지션·손익 재계산 |
| 9 | funding 미반영 | | `income` 에서 수집하여 `trades.funding_paid` 보정 |
| 10 | position mode 불일치 | | One-way 가정인데 Hedge 모드면 **기동 중단** (수량 부호 해석이 달라져 매우 위험) |

### 4.3 주기적 reconcile

- 60초마다 경량 검사: `positionRisk` + `openOrders` 조회 → INV-1/2/4/5 검증
- 불일치 시 전체 reconcile 수행 + `risk_events` 기록
- 비용: 요청 2건/분 → rate limit 영향 미미

### 4.4 WS 재연결 시

```
WS disconnect 감지
  -> 상태 DEGRADED (신규 진입 차단, 기존 포지션 보호는 거래소 스톱이 담당)
  -> 재연결 (지수 백오프, listenKey 재발급)
  -> 재연결 후 REST 로 갭 보정:
       userTrades (since last_processed_ms)
       openOrders (전체 대조)
       positionRisk
       candles (since last_closed_open_ms)
  -> 검증 통과 후 RUNNING 복귀
```

listenKey 는 60분마다 `PUT /fapi/v1/listenKey` 로 연장한다 (미연장 시 조용히 끊긴다).

---

## 5. 시스템 상태 (ops/state.py)

```
   +-----------+   데이터/연결 이상    +------------+
   |  RUNNING  |---------------------->|  DEGRADED  |
   |           |<----------------------|            |
   +-----+-----+   검증 통과            +------+-----+
         |                                     |
         |  리스크 한도 도달 /                 |  해소 실패 지속
         |  킬스위치 / 치명적 불일치            |
         v                                     v
   +-------------------------------------------------+
   |                    HALTED                        |
   |  신규 진입 금지. 기존 포지션 보호는 계속.        |
   |  해제는 사람의 명시적 조작으로만 (자동 해제 없음)|
   +-------------------------------------------------+
```

| 상태 | 신규 진입 | 기존 포지션 청산 | 보호주문 갱신 | 데이터 수집 |
|---|---|---|---|---|
| RUNNING | 허용 | 허용 | 허용 | 허용 |
| DEGRADED | **금지** | 허용 | 허용 | 허용 |
| HALTED | **금지** | 허용 | 허용 | 허용 |

핵심: **어떤 상태에서도 "안전 방향 동작"(청산, 스톱 등록/조임)은 막히지 않는다.**
HALTED 는 진입만 막는다. Risk Engine 이 청산을 거부하는 경로는 존재하지 않는다.

### 5.1 D22 신호 역전 후 재평가

LONG 보유 중 `short_score > long_score`, SHORT 보유 중 `long_score > short_score`이면
손익과 무관하게 우선 청산한다. 청산 체결 시각부터 60분간 `REENTRY_COOLDOWN` 상태로
신규 진입을 막는다. 60분이 지난 뒤 첫 마감 15분봉에서 8개 시간대·기술지표·패턴·
Elliott·뉴스/JEV 점수를 전부 다시 계산한다. 이전 반대 점수를 그대로 재사용해 즉시
반전하지 않는다.
