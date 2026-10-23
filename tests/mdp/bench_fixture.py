"""벤치마크 시험용 작은 체결 파일과 손으로 계산한 정답 (호스트 · Spark 컨테이너 시험이 함께 쓴다).

t0 = 2024-03-01 00:00:00 UTC (매시 00 분이자 5 분 경계).

| 번호 | 수량 | 시각 | is_buyer_maker | 넣은 것 |
|---|---|---|---|---|
| 1 | 1.0 | t0+1s | false | |
| 2 | 2.0 | t0+2s | true | |
| 4 | 0.5 | t0+3s | false | 3 빠짐 (빈 곳) |
| 4 | 0.5 | t0+3s | false | 같은 번호 한 번 더 (중복) |
| 5 | 1.5 | t0+2.5s | false | 앞보다 이른 시각 (역행) |
| 6 | 3.0 | t0+20s | false | 00 분 안, 10 초 창 밖 |
| 7 | 1.0 | t0+5m5s | true | 00:05 창 |
| 8 | 2.0 | t0+1h1s | false | 01:00 점검 분 · 창 |
"""

from __future__ import annotations

from typing import Any, Final

T0: Final = 1_709_251_200_000
HEADER: Final = (
    "agg_trade_id,price,quantity,first_trade_id,last_trade_id,transact_time,is_buyer_maker"
)
_ROWS: Final = [
    (1, "1.0", 1_000, "false"),
    (2, "2.0", 2_000, "true"),
    (4, "0.5", 3_000, "false"),
    (4, "0.5", 3_000, "false"),
    (5, "1.5", 2_500, "false"),
    (6, "3.0", 20_000, "false"),
    (7, "1.0", 305_000, "true"),
    (8, "2.0", 3_601_000, "false"),
]
CSV_TEXT: Final = (
    "\n".join([HEADER] + [f"{i},100.0,{q},{i},{i},{T0 + dt},{m}" for i, q, dt, m in _ROWS]) + "\n"
)

EXPECTED: Final[dict[str, Any]] = {
    "rows": 8,
    "first_id": 1,
    "last_id": 8,
    "first_ms": T0 + 1_000,
    "last_ms": T0 + 3_601_000,
    "gaps": [[T0 + 2_000, T0 + 3_000, 1]],
    "duplicate_rows": 1,
    "time_reversed": 1,
    "check_minutes": {
        str(T0): ["8.5", "6.5", 6],
        str(T0 + 3_600_000): ["2.0", "2.0", 1],
    },
    "windows": {
        str(T0): ["3.5", "2.0", 5],
        str(T0 + 300_000): ["0", "1.0", 1],
        str(T0 + 3_600_000): ["2.0", "0", 1],
    },
}
