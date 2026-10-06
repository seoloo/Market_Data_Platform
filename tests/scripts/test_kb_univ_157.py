from __future__ import annotations

import pytest
from scripts.fetch_alt_universe import eligible, monthly_top
from scripts.run_kb_univ_157 import verdict

from auto_trading.backtest.ema_box import Bars, BoxConfig, simulate_box


def test_eligible_drops_stables_leveraged_pegged_and_seen() -> None:
    syms = ["BTCUSDT", "SXPUSDT", "SXPUPUSDT", "JUPUSDT", "USDCUSDT", "PAXGUSDT", "ETHBULLUSDT",
            "LUNAUSDT", "BTCUSDC"]  # fmt: skip
    assert eligible(syms) == ["JUPUSDT", "LUNAUSDT", "SXPUSDT"]


def test_monthly_top_needs_history_and_trading_on_month_end() -> None:
    days = [f"2020-01-{d:02d}" for d in range(1, 32)] + [f"2020-02-{d:02d}" for d in range(1, 30)]
    days += [f"2020-03-{d:02d}" for d in range(1, 32)]
    vols = {
        "A": dict.fromkeys(days, 10.0),
        "B": dict.fromkeys(days, 100.0),
        "C": dict.fromkeys(days[:-3], 1000.0),  # 3 월 말 전에 거래가 끝남
    }
    got = monthly_top(vols, ["2020-02-29", "2020-03-31"])
    assert got["2020-02-29"] == ["C", "B", "A"]  # 1 월 31 + 2 월 29 = 60 일
    assert got["2020-03-31"] == ["B", "A"]


def _bars(n: int, closes: list[float]) -> Bars:
    t = [i * 4 * 3_600_000 for i in range(n)]
    return Bars(t, closes, [c * 1.001 for c in closes], [c * 0.999 for c in closes], closes,
                [1.0] * n)  # fmt: skip


def test_can_enter_false_blocks_all_entries() -> None:
    n = 400
    closes = [100.0 * 1.002**i for i in range(n)]
    b = _bars(n, closes)
    blocked = simulate_box(b, from_ms=0, to_ms=b.t[-1], initial=1000.0, cfg=BoxConfig(),
                           can_enter=lambda t: False)  # fmt: skip
    assert blocked.signals == 0 and blocked.entries == 0


def test_verdict_rules() -> None:
    rows = [("X", f"2020-01-{d:02d}", 1.0) for d in range(1, 29)] * 2
    rows += [("Y", f"2023-0{m}-{d:02d}", 0.5) for m in (1, 2) for d in range(1, 29)]
    v = verdict(rows)
    assert v["verdict"] == "PASS" and v["halves_n"] == [56, 56]
    assert verdict(rows[:50])["verdict"] == "DATA_INSUFFICIENT"
    bad = rows[:56] + [("Y", r[1], -0.5) for r in rows[56:]]
    assert verdict(bad)["verdict"] == "REJECT"
    assert v["mean_R"] == pytest.approx(0.75)


def test_close_at_end_settles_open_lot_and_records_entry_time() -> None:
    n0 = 250
    c = [100 * 1.003**i for i in range(n0)]
    base = c[-1]
    c += [base] * 10 + [base * 1.02] + [base * 1.03] * 8  # 박스 → 돌파 → 자료 끝
    n = len(c)
    v = [1.0] * n
    v[n0 + 10] = 5.0
    t = [i * 14_400_000 for i in range(n)]
    b = Bars(t, c, [x * 1.01 for x in c], [x * 0.99 for x in c], c, v)
    kw = {"from_ms": 0, "to_ms": t[-1], "initial": 1000.0, "cfg": BoxConfig()}
    keep = simulate_box(b, **kw)  # type: ignore[arg-type]
    closed = simulate_box(b, close_at_end=True, **kw)  # type: ignore[arg-type]
    assert keep.entries == closed.entries == 1
    assert keep.r_multiples == [] and keep.end_exits == 0  # 기본값: 열린 묶음은 R 에서 빠짐
    assert closed.end_exits == 1 and len(closed.r_multiples) == 1
    assert closed.r_entry_ms == [t[n0 + 11]]  # 돌파 다음 봉 시작에 진입
