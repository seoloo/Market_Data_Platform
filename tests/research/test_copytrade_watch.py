"""문서 226 후속 — 새 포지션만 · 시작 전 제외 · 닫힌 손익 낙폭."""

from __future__ import annotations

from auto_trading.research.copytrade_watch import forward_summary, merge_new, position_key


def _p(pid: str, closed: int | None, pnl: float = 1.0) -> dict[str, object]:
    return {"positionId": pid, "closed": closed, "closingPnl": pnl}


def test_merge_new_dedupes_and_skips_old_and_open() -> None:
    seen: set[str] = set()
    rows = [_p("a", 200), _p("b", 50), _p("c", None), _p("a", 200), _p("d", 150)]
    got = merge_new(seen, rows, start_ms=100)
    assert [r["positionId"] for r in got] == ["d", "a"]
    assert position_key(_p("a", 200)) in seen
    assert merge_new(seen, rows, start_ms=100) == []


def test_forward_summary_drawdown_and_worst() -> None:
    rows = [_p("a", 1, 10.0), _p("b", 2, -30.0), _p("c", 3, 5.0)]
    s = forward_summary(rows, avg_win_before=10.0)
    assert s["pnl"] == -15.0
    assert s["max_drawdown_usdt"] == 30.0
    assert s["worst"] == -30.0
    assert s["worst_over_avg_win_before"] == -3.0
    assert forward_summary([], None)["positions"] == 0
