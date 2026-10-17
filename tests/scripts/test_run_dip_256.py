"""256 — 눌림 후보 고르기 · 추세 확인 뒤에만 켜지는 느린 나가기 · −3% 손절 · 판정 (가짜 자료)."""

from __future__ import annotations

from datetime import date, timedelta

from scripts.run_exit_242 import Market

from scripts import run_dip_256 as m


def _days(n: int) -> list[str]:
    out: list[str] = []
    d = date(2008, 1, 1)
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def test_dip_pool_newest_first_and_only_trend_off() -> None:
    up = [100.0 + t for t in range(200)]
    down = [300.0 - t for t in range(200)]
    close: dict[str, list[float | None]] = {"A": list(down), "B": list(up), "C": list(down)}
    hist = [["C", "B"], ["A"], ["A", "C"]]
    assert m.dip_pool(hist, close, 199) == ["A", "C"]
    assert m.dip_pool(hist[:1], close, 199) == ["C"]


def _market(xs: list[float | None], n: int) -> Market:
    days = _days(n)
    return Market(
        days,
        {"X": xs},
        None,
        None,
        [True] * n,
        lambda _t: ["X"],
    )


def test_dip_entry_stays_unarmed_until_trend_confirmed() -> None:
    n = 400
    # 150 일 오르다 40 일 내림(추세 꺼짐), 그 뒤 계속 내림 아주 천천히 — −3% 는 안 닿게 평평
    xs: list[float | None] = [100.0 * 1.003**t for t in range(150)]
    top = xs[-1]
    assert top is not None
    xs += [top * (1 - 0.004 * k) for k in range(1, 41)]
    low = xs[-1]
    assert low is not None
    xs += [low * (1 - 0.00001 * k) for k in range(1, n - len(xs) + 1)]
    mk = _market(xs, n)
    arm = m.simulate(mk, "D", True, start_date=mk.days[0])
    s3 = m.simulate(mk, "D", False, start_date=mk.days[0])
    # 추세가 다시 안 켜지므로 느린 나가기는 안 켜지고 두 경로가 같다
    assert arm.equity == s3.equity
    assert all(tr["reason"] != "slow" for tr in arm.trades)


def test_stop_three_percent_on_close_when_no_lows() -> None:
    n = 300
    xs: list[float | None] = [100.0 * 1.003**t for t in range(n)]
    mk = _market(xs, n)
    first_me = next(t for t in range(n - 1) if mk.days[t + 1][:7] != mk.days[t][:7] and t > 130)
    crash = first_me + 5
    for t in range(crash, n):
        v = xs[t]
        assert v is not None
        xs[t] = v * 0.8
    run = m.simulate(mk, "T", False, start_date=mk.days[0])
    assert run.trades and run.trades[0]["reason"] == "stop"
    assert run.trades[0]["exit_i"] == crash


def test_judge() -> None:
    def row(s: float, s1: float, s2: float, mdd: float) -> dict[str, dict[str, float]]:
        return {
            "full": {"sharpe": s, "mdd": mdd},
            "first": {"sharpe": s1},
            "second": {"sharpe": s2},
        }

    t = row(0.30, 0.2, 0.4, 0.40)
    assert m.judge({"T_ARM": t, "D_ARM": row(0.41, 0.3, 0.5, 0.44)}) == "지지"
    assert m.judge({"T_ARM": t, "D_ARM": row(0.41, 0.1, 0.5, 0.44)}) == "불명"
    assert m.judge({"T_ARM": t, "D_ARM": row(0.41, 0.3, 0.5, 0.50)}) == "불명"
    assert m.judge({"T_ARM": t, "D_ARM": row(0.29, 0.3, 0.5, 0.30)}) == "불리"
