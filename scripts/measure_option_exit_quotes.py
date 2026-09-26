"""Audit displayed option entry/exit quote capacity on the fixed free sample.

Research only. No account credentials, orders, or trading signal are used.
"""

from __future__ import annotations

import gzip
import json
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from urllib.parse import urlencode
from urllib.request import Request, urlopen

MIN_DISPLAY_QTY = Decimal("0.01")  # Current public minimum; not a historical rule.
WINDOW_SECONDS = 600.0
API_URL = "https://api.tardis.dev/v1/data-feeds/binance-european-options"
SAMPLE = (
    (
        "2023-08-01",
        1,
        "depth100",
        (("230804", "29000", "32000", "26000"), ("230825", "30000", "32000", "26000")),
    ),
    (
        "2024-08-01",
        361,
        "depth100",
        (("240802", "64500", "70000", "58000"), ("240830", "64000", "71000", "58000")),
    ),
    (
        "2025-12-01",
        1,
        "depth100",
        (("251205", "90000", "99000", "81000"), ("251226", "90000", "100000", "80000")),
    ),
    (
        "2026-09-01",
        1,
        "depth20",
        (("260904", "78500", "85000", "71000"), ("260918", "79000", "86000", "70000")),
    ),
)


@dataclass(frozen=True)
class Quote:
    bid_qty: Decimal
    ask_qty: Decimal
    bid_price: Decimal
    ask_price: Decimal
    observed_at: float

    def valid(self) -> bool:
        return self.bid_price > 0 and self.ask_price > self.bid_price


def best(levels: list[list[str]], *, bid: bool) -> tuple[Decimal, Decimal]:
    positive = [
        (Decimal(price), Decimal(qty))
        for price, qty in levels
        if Decimal(price) > 0 and Decimal(qty) > 0
    ]
    if not positive:
        return Decimal(0), Decimal(0)
    return (max if bid else min)(positive, key=lambda row: row[0])


def quote_from_message(message: dict[str, object], at: float) -> Quote:
    bids = message["b"]
    asks = message["a"]
    assert isinstance(bids, list) and isinstance(asks, list)
    bid_price, bid_qty = best(bids, bid=True)
    ask_price, ask_qty = best(asks, bid=False)
    return Quote(bid_qty, ask_qty, bid_price, ask_price, at)


def pair_symbols(
    expiry: str, atm: str, call_wing: str, put_wing: str
) -> tuple[tuple[str, str], tuple[str, str]]:
    return (
        (f"BTC-{expiry}-{atm}-C", f"BTC-{expiry}-{call_wing}-C"),
        (f"BTC-{expiry}-{atm}-P", f"BTC-{expiry}-{put_wing}-P"),
    )


def epoch(timestamp: str) -> float:
    # Tardis may supply seven fractional digits; Python retains microseconds.
    return datetime.fromisoformat(timestamp.replace("Z", "+00:00")).timestamp()


def integrate(
    totals: dict[str, list[float]],
    spread_cost: dict[str, float],
    quotes: dict[str, Quote],
    pairs: tuple[tuple[str, str], ...],
    begin: float,
    end: float,
) -> None:
    if end <= begin:
        return
    for short, long in pairs:
        short_quote = quotes.get(short)
        long_quote = quotes.get(long)
        if short_quote is None or long_quote is None:
            continue
        if not short_quote.valid() or not long_quote.valid():
            continue
        if (
            min(short_quote.bid_qty, short_quote.ask_qty, long_quote.bid_qty, long_quote.ask_qty)
            < MIN_DISPLAY_QTY
        ):
            continue
        last_observed = min(short_quote.observed_at, long_quote.observed_at)
        for index, limit in enumerate((1.0, 5.0)):
            seconds = max(0.0, min(end, last_observed + limit) - begin)
            totals[f"{short}/{long}"][index] += seconds
            if index == 1:
                # Hypothetical same-quote, four-cross displayed spread for 0.01 BTC.
                width = (
                    short_quote.ask_price
                    - short_quote.bid_price
                    + long_quote.ask_price
                    - long_quote.bid_price
                )
                spread_cost[f"{short}/{long}"] += float(width * MIN_DISPLAY_QTY) * seconds


def audit_lines(
    lines: Iterable[bytes],
    *,
    start: float,
    pairs: tuple[tuple[str, str], ...],
) -> tuple[dict[str, list[float]], dict[str, float], int, int]:
    totals = {f"{short}/{long}": [0.0, 0.0] for short, long in pairs}
    spread_cost = dict.fromkeys(totals, 0.0)
    quotes: dict[str, Quote] = {}
    previous = start
    frames = 0
    disconnects = 0
    end = start + WINDOW_SECONDS
    for raw_line in lines:
        if not raw_line.strip():
            # Disconnect time is unknown: do not carry quotes beyond last frame.
            quotes.clear()
            disconnects += 1
            continue
        timestamp, payload = raw_line.decode("utf-8").split(" ", 1)
        now = epoch(timestamp)
        if now < start or now >= end:
            continue
        if now < previous:
            raise ValueError("Tardis localTimestamp moved backwards")
        integrate(totals, spread_cost, quotes, pairs, previous, now)
        message = json.loads(payload)["data"]
        symbol = message["s"]
        quotes[symbol] = quote_from_message(message, now)
        previous = now
        frames += 1
    integrate(totals, spread_cost, quotes, pairs, previous, end)
    return totals, spread_cost, frames, disconnects


def main() -> None:
    for date, offset, channel, expiries in SAMPLE:
        pairs = tuple(pair for expiry in expiries for pair in pair_symbols(*expiry))
        symbols = sorted({symbol for pair in pairs for symbol in pair})
        if channel == "depth20":
            symbols = [symbol.lower() for symbol in symbols]
        params = urlencode(
            {
                "from": date,
                "offset": offset,
                "sliceSize": 10,
                "filters": json.dumps(
                    [{"channel": channel, "symbols": symbols}], separators=(",", ":")
                ),
            }
        )
        request = Request(f"{API_URL}?{params}", headers={"Accept-Encoding": "gzip"})
        start = datetime.fromisoformat(date).replace(tzinfo=UTC).timestamp() + offset * 60
        with urlopen(request, timeout=60) as response, gzip.GzipFile(fileobj=response) as lines:
            totals, spread_cost, frames, disconnects = audit_lines(lines, start=start, pairs=pairs)
        sys.stdout.write(
            json.dumps(
                {
                    "date": date,
                    "frames": frames,
                    "disconnects": disconnects,
                    "seconds_1s_5s": {
                        key: [round(value, 3) for value in values] for key, values in totals.items()
                    },
                    "mean_four_cross_spread_usdt_at_0_01_btc": {
                        key: round(spread_cost[key] / values[1], 4) if values[1] else None
                        for key, values in totals.items()
                    },
                },
                ensure_ascii=False,
            )
            + "\n"
        )


if __name__ == "__main__":
    main()
