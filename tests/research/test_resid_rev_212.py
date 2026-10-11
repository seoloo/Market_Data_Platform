# ruff: noqa: E501 — 한국어 설명이 길다
"""문서 212 합성 검산 — 회귀 · 신호 미래 정보 방지, 종목군 그 시점 자료, 제외 규칙, beta 명목, funding 부호, 사건 회계."""

from __future__ import annotations

import random
from datetime import UTC, date, datetime

from scripts.run_resid_rev_212 import DAY, DayBar, _ms, atr20, build_universe, excluded, residual_z
from scripts.run_resid_rev_212_threshold import COST_RT, HOLD, Intraday, build_events


def _series(seed: int = 1) -> tuple[list[float], list[float]]:
    rng = random.Random(seed)
    rb = [rng.gauss(0, 0.03) for _ in range(63)]
    rc = [0.001 + 1.3 * b + rng.gauss(0, 0.02) for b in rb]
    return rc, rb


def test_regression_excludes_last_three_days() -> None:
    rc, rb = _series()
    z0, beta0, alpha0 = residual_z(rc, rb)  # type: ignore[misc]
    rc2 = rc[:60] + [x - 0.15 for x in rc[60:]]  # 최근 3 일만 크게 하락
    z1, beta1, alpha1 = residual_z(rc2, rb)  # type: ignore[misc]
    assert beta1 == beta0 and alpha1 == alpha0  # 계수는 앞 60 일로만
    assert z1 < z0 - 2 and z1 <= -2
    assert 1.0 < beta0 < 1.6
    assert residual_z(rc[:62], rb[:62]) is None  # 자료 부족 → 신호 없음


def test_universe_point_in_time_and_history_rule() -> None:
    t0 = _ms(date(2021, 1, 1))
    daily: dict[str, dict[int, DayBar]] = {}
    for k, s in enumerate(["AAAUSDT", "BBBUSDT", "NEWUSDT", "BTCUSDT", "USDCUSDT"]):
        start = t0 - (100 if s == "NEWUSDT" else 400) * DAY
        daily[s] = {start + i * DAY: DayBar(1, 1, 1, 1, 1000.0 * (k + 1)) for i in range(800)}
    # 미래(t0 이후) 거래대금이 커도 1 월 순위에 영향 없음
    for i in range(30):
        daily["AAAUSDT"][t0 + i * DAY] = DayBar(1, 1, 1, 1, 1e12)
    mem = build_universe(
        daily, {"USDCUSDT": {"underlyingType": "COIN", "contractType": "PERPETUAL"}}
    )
    jan = mem["2021-01"]
    assert jan == [
        "BBBUSDT",
        "AAAUSDT",
    ]  # NEW(180 일 미만) · BTC · 스테이블 제외, 직전 30 일 거래대금 순


def test_excluded_rules() -> None:
    ex = {
        "INTCUSDT": {"underlyingType": "EQUITY", "contractType": "TRADIFI_PERPETUAL"},
        "ETHUSDT": {"underlyingType": "COIN", "contractType": "PERPETUAL"},
    }
    assert excluded("INTCUSDT", ex) and excluded("BTCUSDT", ex) and excluded("PAXGUSDT", ex)
    assert (
        excluded("ETHUSDT", ex) is None and excluded("LUNAUSDT", ex) is None
    )  # 폐지(정보 없음)는 코인으로


def test_atr20() -> None:
    bars = [DayBar(100, 101, 99, 100, 1) for _ in range(21)]
    assert atr20(bars) == 2.0


class FakeIntraday(Intraday):
    """고정 가격 · 정산으로 사건 회계 검산."""

    def __init__(
        self,
        prices: dict[str, tuple[float, float]],
        funding: dict[str, list[tuple[int, float]]],
        t_entry: int,
    ) -> None:
        super().__init__()
        self.p = prices
        self.f = funding
        self.t = t_entry

    def first_open(self, s: str, t: int):
        p0, p1 = self.p[s]
        return (t, p0 if t < self.t + HOLD else p1)

    def mark_at(self, s: str, t: int):
        return 200.0 if s == "BTCUSDT" else 12.0

    def funding_between(self, s: str, a: int, b: int):
        return [(t, r) for t, r in self.f.get(s, []) if a < t <= b], False


def test_event_gross_cost_and_funding_signs() -> None:
    from scripts.run_resid_rev_212 import Signal

    day = "2022-05-01"
    t_entry = int(datetime(2022, 5, 2, tzinfo=UTC).timestamp() * 1000)
    sig = Signal(day, "AAAUSDT", -2.5, 1.5, 0.0, 1e6, 1.0, 1000.0)
    it = FakeIntraday(
        {"AAAUSDT": (10.0, 11.0), "BTCUSDT": (100.0, 102.0)},
        {
            "AAAUSDT": [(t_entry + 8 * 3_600_000, 0.001)],
            "BTCUSDT": [(t_entry + 8 * 3_600_000, 0.002)],
        },
        t_entry,
    )
    evs, skip = build_events(
        [sig, Signal("2022-05-02", "AAAUSDT", -3.0, 1.5, 0.0, 1e6, 1.0, 1000.0)], it
    )
    assert len(evs) == 1 and skip == {"같은 코인 보유 중": 1}  # 보유 중 새 신호 무시
    e = evs[0]
    assert abs(e.gross - (0.10 - 1.5 * 0.02)) < 1e-12  # 코인 +10% − beta × BTC +2%
    assert abs(e.cost - COST_RT * 2.5) < 1e-12  # 0.19% × (1 + beta)
    assert abs(e.fund_coin - 0.001 * 12.0 / 10.0) < 1e-12  # 롱: 양수 rate 면 냄 (mark / 진입가)
    assert abs(e.fund_btc - (-1.5 * 0.002 * 200.0 / 100.0)) < 1e-12  # 숏: 받음 → 비용 음수
    assert abs(e.net - (e.gross - e.cost - e.fund_coin - e.fund_btc)) < 1e-15
