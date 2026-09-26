"""Fixed option quote audit: freshness, quantity and disconnect boundaries."""

from __future__ import annotations

import io
import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from scripts.measure_option_exit_quotes import Quote, audit_lines, integrate


def test_four_side_quantity_and_freshness_and_spread_cost() -> None:
    pairs = (("SHORT", "LONG"),)
    totals = {"SHORT/LONG": [0.0, 0.0]}
    costs = {"SHORT/LONG": 0.0}
    quotes = {
        "SHORT": Quote(Decimal("0.02"), Decimal("0.02"), Decimal(100), Decimal(102), 10.0),
        "LONG": Quote(Decimal("0.02"), Decimal("0.02"), Decimal(50), Decimal(53), 10.0),
    }
    integrate(totals, costs, quotes, pairs, 10.0, 20.0)
    assert totals["SHORT/LONG"] == [1.0, 5.0]
    assert costs["SHORT/LONG"] == pytest.approx(0.25)  # 0.01 × (2+3) × 5s

    quotes["LONG"] = Quote(Decimal("0.009"), Decimal("0.02"), Decimal(50), Decimal(53), 10.0)
    integrate(totals, costs, quotes, pairs, 10.0, 20.0)
    assert totals["SHORT/LONG"] == [1.0, 5.0]


def test_disconnect_discards_stale_quote_until_both_legs_reappear() -> None:
    start = datetime(2026, 9, 1, tzinfo=UTC).timestamp() + 60

    def row(seconds: int, symbol: str) -> bytes:
        timestamp = datetime.fromtimestamp(start + seconds, UTC).isoformat().replace("+00:00", "Z")
        message = {"data": {"s": symbol, "b": [["100", "0.02"]], "a": [["101", "0.02"]]}}
        return f"{timestamp} {json.dumps(message)}\n".encode()

    lines = io.BytesIO(row(1, "SHORT") + row(2, "LONG") + b"\n" + row(3, "SHORT") + row(10, "LONG"))
    totals, costs, frames, disconnects = audit_lines(lines, start=start, pairs=(("SHORT", "LONG"),))
    assert frames == 4
    assert disconnects == 1
    assert totals["SHORT/LONG"] == pytest.approx([0.0, 0.0])
    assert costs["SHORT/LONG"] == 0.0


def test_backward_local_timestamp_rejected() -> None:
    start = datetime(2026, 9, 1, tzinfo=UTC).timestamp() + 60
    payload = b'{"data":{"s":"SHORT","b":[["1","1"]],"a":[["2","1"]]}}\n'
    lines = io.BytesIO(b"2026-09-01T00:01:02Z " + payload + b"2026-09-01T00:01:01Z " + payload)
    with pytest.raises(ValueError, match="backwards"):
        audit_lines(lines, start=start, pairs=(("SHORT", "LONG"),))
