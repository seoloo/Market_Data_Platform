from __future__ import annotations

from collections.abc import Sequence

from scripts.fetch_kq_stocks import LIQ_DAYS, candidates
from scripts.run_kq_154 import min_sector
from scripts.run_lt_kr import UNKNOWN, KrPanel


def test_candidates_take_top_by_average_value_only_while_listed() -> None:
    days = [f"2020{m:02d}{d:02d}" for m in (1, 2, 3, 4, 5) for d in range(1, 21)]
    n = len(days)
    value = {
        "BIG": dict.fromkeys(range(n), 100.0),
        "LATE": dict.fromkeys(range(70, n), 1000.0),  # 늦게 상장
        "SMALL": dict.fromkeys(range(n), 1.0),
    }
    import scripts.fetch_kq_stocks as m

    old = m.CANDIDATES
    m.CANDIDATES = 1
    try:
        chosen, span = candidates(days, value)
    finally:
        m.CANDIDATES = old
    assert LIQ_DAYS == 63
    # 4 월 말(색인 79): 상장 전 날은 0 으로 쳐서 LATE 평균 158.7 > BIG 100 → LATE 하나
    assert chosen == ["LATE"]
    assert span["LATE"] == (days[70], days[-1])


def test_min_sector_drops_small_and_unknown_sectors_before_picking() -> None:
    sector = {f"A{i}": "IT" for i in range(5)} | {"B0": "에너지", "C0": UNKNOWN}
    panel = KrPanel([], {}, {}, sector, {}, {})
    seen: list[str] = []

    def pick(p: KrPanel, t: int, pool: Sequence[str]) -> list[tuple[str, float]]:
        seen.extend(pool)
        return []

    min_sector(pick)(panel, 0, sorted(sector))
    assert sorted(seen) == [f"A{i}" for i in range(5)]


def test_panic_trade_buys_next_close_and_holds_twenty_days() -> None:
    from scripts.run_kq_154 import PANIC_FEE, PANIC_HOLD, panic_trades

    days = [f"d{i:03d}" for i in range(30)]
    index = [100.0] * 30
    index[22] = 110.0  # 1 + 1 + 20
    got = panic_trades(days, index, ["d001", "d020"])  # d020 은 보유가 안 끝남
    assert PANIC_HOLD == 20
    assert got == [110.0 / 100.0 - 1 - 2 * PANIC_FEE]
