# 국내 주식 실시간 — 체결 웹소켓 → Kafka → 스트림 1분봉 → 장 마감 뒤 대사 (K-4)

> 바이낸스 실시간([STREAM](STREAM.md) · [RECONCILE](RECONCILE.md))과 **같은 틀**을 한국투자증권 실시간 체결에 쓴다.
> 다른 점은 둘 — ① 체결 번호가 없어 **누적 거래량을 번호처럼** 쓰고, ② 원천에 체결 이력이 없어 대사의 정답이
> 체결 파일이 아니라 **장 마감 뒤 분봉 조회**다([ADR 0012](adr/0012-domestic-stocks-kis-quotes-only.md)).

| 단계 | 코드 | 하는 일 |
|---|---|---|
| 글자 읽기 · 구독 | [`mdp.kis.realtime`](../src/mdp/kis/realtime.py) | 47 칸 체결 글자 끊기, 구독 메시지(실시간 체결 `H0STCNT0` 만 허용) |
| 받기 | [`mdp.stream.kis_produce`](../src/mdp/stream/kis_produce.py) | 정한 시각에 붙어 구독 → 받은 글자 그대로 Kafka `raw.kis.domestic.ccnl` |
| 스트림 1분봉 | [`mdp.stream.kis_bars`](../src/mdp/stream/kis_bars.py) | 워터마크 · 누적 거래량으로 중복 제거 · 1 분 창 · MERGE → `gold/kr_stream_bars_1m` |
| 대사 | [`mdp.reconcile.kis_daily`](../src/mdp/reconcile/kis_daily.py) | 스트림 봉 ↔ Silver `kr_minute_bars`(정규장 · 마감 봉), 분마다 원인 |

## 실시간 체결 메시지 (공식 예제 저장소, 2026-10-26 확인)

- 주소: `ws://ops.koreainvestment.com:21000/tryitout` (모의투자 `:31000`). 접속키는 `POST /oauth2/Approval`.
- 구독: `{"header": {"approval_key", "custtype": "P", "tr_type": "1", "content-type": "utf-8"},
  "body": {"input": {"tr_id": "H0STCNT0", "tr_key": "005930"}}}`.
- 체결: `0|H0STCNT0|003|<47 칸>^<47 칸>^<47 칸>` — 첫 칸 0 평문 · 1 암호문(체결통보에만, 구독하지 않음), 셋째 칸이 건수.
- 제어: 구독 응답(`SUBSCRIBE SUCCESS`) · `PINGPONG` — 받은 그대로 pong 으로 돌려준다(공식 예제 방식).

## 누적 거래량을 체결 번호처럼

체결 한 건마다 누적 거래량(`ACML_VOL`)이 그 체결량(`CNTG_VOL`)만큼 는다. 그래서:

- **중복 제거**: 같은 (종목, 누적 거래량)은 같은 체결이다 — 재연결 때 두 번 받아도 한 번만 센다.
- **놓친 체결 찾기**: 한 분 안에서 '첫 누적 − 첫 체결량 + 체결량 합 = 마지막 누적' 이 아니면 그 분 안에서
  놓쳤다(`acml_contiguous = false`). 분과 분 사이는 대사가 '앞 분 마지막 누적 = 이번 분 첫 누적 − 첫 체결량' 으로 본다.

## 대사 결과 (분마다)

| 결과 | 뜻 |
|---|---|
| `matched` | 시가 · 고가 · 저가 · 종가 · 거래량이 모두 같다 |
| `stream_missing` | 분봉 조회에만 있다 — 그 분에 체결을 하나도 못 받았다 |
| `only_stream` | 스트림에만 있다 |
| `missed_trades` | 값이 다르고 누적 거래량이 분 안 · 앞 분과 이어지지 않는다 — 체결을 놓쳤다 |
| `values_differ` | 값이 다른데 누적은 이어진다 — 분 이름 붙이는 방식 · 가격 기준 등(확인 필요) |

- 맞대는 범위: 정규장 + 마감 동시호가 봉. 장 후 봉(16:00 ~)은 시간외 체결이 다른 TR(공식 예제
  `overtime_ccnl_krx`)이라 맞대지 않는다.
- 결과: `gold/kr_reconcile_minutes`(그 날 칸만 바꿔 끼움) · `gold/kr_reconcile_daily`(돌릴 때마다 한 줄 더함).
- KODEX 200 은 분봉 조회가 '받은 날 기준 수정 가격' 이라([K-3](KR_SILVER_GOLD.md)) 다음 분배락 전까지는 원주가 체결과
  같아야 한다 — 2026-07-30 뒤로 분배락이 없었으므로 지금은 맞대도 된다.

## 확인한 것 (2026-10-26)

- **접속 · 구독**: 실전 웹소켓에 붙어 세 종목(005930 · 000660 · 069500) 모두 `SUBSCRIBE SUCCESS`. 150 초 동안
  PINGPONG 7 번에 답했고 끊기지 않았다. 장이 닫힌 밤이라 체결은 0 건(정상).
- 시험: 호스트 14 개(글자 읽기 · 구독 허용 목록 · 받기 — 기다림 · 그대로 보내기 · PINGPONG · 다시 붙기 · 구독 실패에
  멈춤 · 40 개 한도), Spark 3 개(깨진 메시지 버림 · 누적 거래량 중복 제거 · 이음 · 대사 원인 다섯 가지).

## 장 날 돌리는 순서

```bash
# 1) 아침 — 받기를 띄워 둔다(정한 시각에 붙고 멈춤). 키는 이 서비스에만 넘어간다(docker-compose.yml).
MDP_KIS_ENV=real docker compose --profile stream run -d --name mdp-kis-capture kis-capture \
    python -m mdp.stream.kis_produce --symbols 005930,000660,069500 \
    --start-at 2026-10-27T08:40 --stop-at 2026-10-27T15:45

# 2) 20:30 뒤 — 그 날 분봉 받기 → Silver 다시 → 스트림 봉 확정 → 대사
MDP_KIS_ENV=real python -m mdp.bronze.kis --kind minute --symbols 005930,000660,069500 \
    --start 2026-10-27 --end 2026-10-27
docker compose --profile spark run --rm spark python -m mdp.silver.kr_stocks
docker compose --profile spark run --rm spark python -m mdp.stream.kis_bars --trigger available-now
docker compose --profile spark run --rm spark python -m mdp.reconcile.kis_daily --date 2026-10-27
```

## 확인 필요

- 분봉 조회의 분 이름이 '분의 시작' 인지(스트림은 시작으로 붙인다) — 첫 장 날 대사로 확인.
- 마감 동시호가 체결이 실시간으로 몇 시 몇 초 체결로 오는지, 장 시작 동시호가(09:00) 체결도 같은지.
- 접속키 유효 시간, 실시간 구독 한도의 정확한 숫자, `ws://`(암호화 없음)로 접속키가 가는 문제.
- `CCLD_DVSN`(체결 구분) · `NEW_MKOP_CLS_CODE`(장 운영 구분) · `HOUR_CLS_CODE`(시간 구분) 칸의 값 뜻.
