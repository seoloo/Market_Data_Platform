# Gold 1분봉 (README 4-4)

> Silver 깨끗한 체결을 1분 단위로 묶어 Gold Delta 표로 쓰고, 바이낸스 공식 1분봉과 맞대 본 뒤,
> 조회용으로 로컬 PostgreSQL에 복사해 양쪽 체크섬이 같은지 증명한다.
> 코드: [`src/mdp/gold/bars_1m.py`](../src/mdp/gold/bars_1m.py) · [`src/mdp/serve/bars_1m.py`](../src/mdp/serve/bars_1m.py)

```text
silver/agg_trades ──Spark──▶ gold/bars_1m ──DuckDB(한 트랜잭션)──▶ PostgreSQL gold_bars_1m
  (6,651만 행)                (44,640행)          │                     (조회용 복사본)
                                  │               └─ 양쪽 SHA-256 비교 ─┘
        Bronze 공식 1분봉 ──Spark──▶ gold/bars_1m_kline_check (날마다 얼마나 같은가)
```

실행:

```bash
docker compose run --rm spark python -m mdp.gold.bars_1m --symbol BTCUSDT --month 2024-03
python -m mdp.serve.bars_1m --symbol BTCUSDT --month 2024-03      # MDP_PG_DSN 필요
```

## 봉 만드는 규칙

| 칸 | 규칙 |
|---|---|
| 시가 · 종가 | 그 분의 첫 · 마지막 체결 가격. 순서는 체결 시각, 같은 밀리초면 체결 번호 — 다시 만들어도 같은 봉 |
| 고가 · 저가 | 최고 · 최저 가격 |
| 거래량 · 거래대금 · 공격 매수량 · 공격 매수 대금 | decimal 그대로 더함 |
| `trade_count` | 개별 체결 수 = 묶음마다 (끝 번호 − 첫 번호 + 1)의 합 — 공식 봉의 `count`와 같은 뜻 |
| `agg_trade_count` | 묶음(aggTrade) 수 |
| 빈 분 | 체결이 없는 분은 줄을 만들지 않는다(없는 것을 0으로 꾸미지 않음) |
| 계보 | 줄마다 `silver_version`(읽은 Silver Delta 버전) · `run_id` |

## 결과 — BTCUSDT 2024-03

| 항목 | 값 |
|---|---|
| Gold 봉 | **44,640행** = 31일 × 1,440분 (빈 분 없음), Spark 66초 |
| 공식 1분봉과 칸마다 모두 같은 분 | 33,280분 (74.6%) |
| 시가 · 고가 · 저가 · 종가가 같은 분 | 40,283분 (90.2%) |
| 거래량이 같은 분 | 2024-03-01 기준 **1,110분 / 1,440분** |
| 하루 거래량 차이 | 2024-03-01 −1.04 BTC (상대 **−3.5e-6**), 31일 중 10일은 정확히 같음 |
| PostgreSQL 복사 | 44,640행 2.4초, **Delta 쪽 · PostgreSQL 쪽 SHA-256 같음**, 다시 복사해도 한 벌(멱등) |

**연구 216과의 일치** — 연구 문서 §4에 2024-03-01을 "분 단위로는 1,440분 중 1,110분 정확 일치, 하루 거래량 상대 차 −3.5e-6"으로 적었다.
새 파이프라인의 Gold가 같은 날 **같은 두 숫자**를 독립적으로 다시 냈다.

**왜 모든 분이 같지 않나** — 공식 봉과 체결로 만든 봉은 분 경계에 걸린 체결을 어느 분에 넣느냐가 다르다.
연구 216은 앞뒤 분을 합치면 같다고 확인했다. 그래서 이 대조는 '같아야 한다'가 아니라 '얼마나 같은가'를
기록하는 표(`bars_1m_kline_check`)로 남기고, 합격 판정에는 쓰지 않는다. 월 전체 개별 체결 수는 Gold가 11,366건
많은데, 같은 경계 차이 때문인지는 **확인 필요**.

## PostgreSQL 복사가 안전한 이유

1. **원본은 Delta 하나** — PostgreSQL 표는 복사본이고, 복사할 때마다 그 달을 지우고 다시 넣는다.
2. **한 트랜잭션** — DuckDB가 PostgreSQL을 붙여(ATTACH) 지우기 · 넣기를 한 트랜잭션으로 한다. 중간에 실패하면 지우기까지 되돌아간다.
3. **체크섬 증명** — 같은 칸을 같은 순서 · 같은 글자 모양으로 이어 붙인 SHA-256을 Delta 쪽(DuckDB)과 PostgreSQL 쪽(PostgreSQL 자체 SQL)에서 따로 계산해 비교한다.
   두 DB가 소수를 글자로 바꾸는 모양이 같은 것(`61203.40000000`)을 먼저 확인했다.
4. **계보** — 줄마다 복사한 Delta 버전(`gold_version`)과 작업(`loaded_run_id`)을 남긴다. 표 정의는 마이그레이션 `003_gold_bars_1m.sql`(고가 ≥ 시가 · 종가 ≥ 저가 CHECK 포함).
