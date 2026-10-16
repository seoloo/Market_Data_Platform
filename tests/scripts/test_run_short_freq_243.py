"""243 — Altair 숏 빈도 점검의 구간 · 빈 기간 계산 (가짜 기록)."""

from __future__ import annotations

from typing import Any

import pytest

from scripts import run_short_freq_243 as m

H4 = 4 * 3_600_000


def test_runs_and_gaps() -> None:
    assert m.runs([True, True, False, True]) == [(0, 2), (3, 1)]
    assert m.runs([]) == []
    g = m.gaps_days([2 * m.DAY_MS], 0, 5 * m.DAY_MS)
    assert [x[2] for x in g] == pytest.approx([2.0, 3.0])


def test_analyse_splits_vetoed_signals_and_counts_shorts() -> None:
    feats: list[dict[str, Any]] = []
    for d in range(4):
        for k in range(6):
            at = d * m.DAY_MS + k * H4
            targets = None
            if k == 0:
                btc = "0" if d >= 2 else "0.1"
                targets = {"BTCUSDT": btc, "ETHUSDT": "0.1"}
            signals = ["BTCUSDT"] if k == 3 and d in (1, 2) else []
            feats.append({"at_ms": at, "targets": targets, "signals": signals})
    trades = [
        {
            "symbol": "BTCUSDT",
            "side": "SHORT",
            "entry_ms": 2 * m.DAY_MS + 4 * H4,
            "exit_ms": 2 * m.DAY_MS + 4 * H4 + 48 * 3_600_000,
            "net": "5",
            "reason": "48H",
        }
    ]
    out = m.analyse(feats, trades)
    assert out["signals"] == {"BTCUSDT:vetoed_by_long": 1, "BTCUSDT:eligible": 1}
    assert out["daily_goal"]["BTCUSDT"]["zero_share"] == pytest.approx(0.5)
    assert out["both_long_on"]["runs"]["max_days"] == 2
    assert out["shorts"]["count"] == 1 and out["shorts"]["win_rate"] == 1.0
    assert out["shorts"]["median_hold_hours"] == pytest.approx(48.0)
