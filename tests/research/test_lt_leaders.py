"""문서 113 LT: 주도 섹터·주도주 고르기, 추세 진입, −35% 손절."""

from __future__ import annotations

from datetime import date, timedelta

from scripts.run_lt_leaders import Panel, leaders, simulate, trend_up


def _days(n: int) -> list[str]:
    out: list[str] = []
    d = date(2008, 1, 1)
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def _panel(n_days: int, crash_at: int | None = None) -> Panel:
    days = _days(n_days)
    close: dict[str, list[float | None]] = {}
    sector: dict[str, str] = {}
    for k in range(60):
        sym = f"S{k:02d}"
        sector[sym] = ("Tech", "Energy", "Utilities")[k % 3]
        drift = 1.0 + (0.0004 if sector[sym] == "Tech" else 0.0) + k * 1e-6
        xs: list[float | None] = []
        v = 100.0
        for _ in range(n_days):
            v *= drift
            xs.append(v)
        close[sym] = xs
    star: list[float | None] = []
    v = 100.0
    for t in range(n_days):
        v *= 1.004
        if crash_at is not None and t == crash_at:
            v *= 0.55
        star.append(v)
    close["STAR"] = star
    sector["STAR"] = "Tech"
    return Panel(days, close, sector)


def test_star_is_a_leader_in_leading_sector_and_in_trend() -> None:
    panel = _panel(300)
    lead = leaders(panel, 299)
    assert lead and lead[0][0] == "STAR"
    assert all(panel.sector[s] in {"Tech", "Energy", "Utilities"} for s, _ in lead)
    assert trend_up(panel.close["STAR"], 299)


def test_bought_after_month_end_and_stopped_after_crash() -> None:
    panel = _panel(500, crash_at=450)
    run = simulate(panel, [True] * len(panel.days))
    star = [tr for tr in run.trades if tr["symbol"] == "STAR"]
    assert star, "STAR 를 샀다가 팔아야 한다"
    assert star[0]["reason"] == "stop_35"


def test_leaders_tie_order_is_fixed() -> None:
    """모두 같은 수익(동점)이면 종목 이름 순으로 정해진다 — 해시 시드와 무관."""
    days = _days(200)
    close: dict[str, list[float | None]] = {
        f"S{k:02d}": [100.0 * 1.001**t for t in range(200)] for k in range(60)
    }
    sector = {s: ("Tech", "Energy", "Utilities")[int(s[1:]) % 3] for s in close}
    got = [s for s, _ in leaders(Panel(days, close, sector), 199)]
    assert got == [f"S{k:02d}" for k in range(59, 49, -1)]
