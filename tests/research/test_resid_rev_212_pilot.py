# ruff: noqa: E501 — 한국어 설명이 길다
"""문서 212 ④ 파일럿 엔진 합성 검산 — beta 명목, 72 시간 청산, 보호 우선 · 두 다리 정리, 헤지 실패 1 시간, funding 부호, 계좌 정지."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime

from scripts.run_resid_rev_212 import M5, Signal
from scripts.run_resid_rev_212_pilot import P, Sim

DAY = 86_400_000
T0 = int(datetime(2022, 1, 2, tzinfo=UTC).timestamp() * 1000)  # 진입일 00:00


class Fake:
    """price(symbol, t) 로 5분봉을 만든다 (시가 = 고가 = 저가 = 종가, 거래대금 큼)."""

    def __init__(
        self,
        price: Callable[[str, int], float],
        funding: dict[str, list[tuple[int, float]]] | None = None,
        low: Callable[[str, int], float] | None = None,
        btc_open_jump: bool = False,
    ) -> None:
        self.price, self.f, self.low, self.jump = price, funding or {}, low, btc_open_jump

    def bar(self, s: str, t: int):
        p = self.price(s, t)
        lo = self.low(s, t) if self.low else p
        o = p * 0.99 if (self.jump and s == "BTCUSDT") else p  # 숏 매도 한도(−0.3%) 밖 시가
        return (o, max(p, o), lo, p, 1e12)

    def close_before(self, s: str, t: int):
        return self.price(s, t - M5)

    def mark_at(self, s: str, t: int):
        return self.price(s, t)

    def funding_between(self, s: str, a: int, b: int):
        return [(t, r) for t, r in self.f.get(s, []) if a < t <= b], False


def _sig(beta: float = 1.5) -> Signal:
    return Signal("2022-01-01", "AAAUSDT", -2.5, beta, 0.0, 1e6, 2.0, 50.0)  # ATR 코인 2 · BTC 50


def _run(fake: Fake, p: P | None = None, sigs: list[Signal] | None = None) -> Sim:
    sim = Sim(p or P(), sigs or [_sig()], fake, {})  # type: ignore[arg-type]
    sim.run(date(2022, 1, 2), date(2022, 1, 8))
    return sim


def test_beta_notional_and_72h_exit() -> None:
    def price(s: str, t: int) -> float:
        up = t >= T0 + 72 * 3_600_000
        return (110.0 if up else 100.0) if s == "AAAUSDT" else (1020.0 if up else 1000.0)

    sim = _run(Fake(price))
    b = sim.done[0]
    assert b.reason == "72 시간"
    # 수량: 위험 0.25% = N(2·2/100 + 1.5·2·50/1000) → N = 25 / 0.19
    n = 0.0025 * 10_000 / (0.04 + 1.5 * 0.1)
    assert abs(b.coin.avg - 100.0) < 1e-9
    gross = n * 0.10 - 1.5 * n * 0.02  # 코인 +10% − beta × BTC +2%
    fees = 0.00095 * (n + 1.5 * n + n * 1.1 + 1.5 * n * 1.02)
    assert abs(b.cash - (gross - fees)) < 1e-6
    assert b.coin.first_fill == T0 and b.btc.first_fill == T0 + M5  # 헤지는 코인 다음 봉


def test_stop_closes_both_legs_with_extra() -> None:
    crash = T0 + 6 * 3_600_000

    def price(s: str, t: int) -> float:
        return (90.0 if t >= crash else 100.0) if s == "AAAUSDT" else 1000.0

    sim = _run(Fake(price))
    b = sim.done[0]
    assert b.reason == "보호 청산" and b.coin.qty == 0 and b.btc.qty == 0
    assert b.extra > 0  # 비상 정리 추가 불리폭


def test_hedge_failure_unwinds_after_one_hour() -> None:
    sim = _run(Fake(lambda s, t: 100.0 if s == "AAAUSDT" else 1000.0, btc_open_jump=True))
    b = sim.done[0]
    assert b.reason == "헤지 실패" and b.btc.first_fill < 0
    assert b.coin.qty == 0


def test_funding_signs() -> None:
    ft = T0 + 8 * 3_600_000
    sim = _run(
        Fake(
            lambda s, t: 100.0 if s == "AAAUSDT" else 1000.0,
            {"AAAUSDT": [(ft, 0.001)], "BTCUSDT": [(ft, 0.001)]},
        )
    )
    b = sim.done[0]
    coin_pay = b.coin.target * 100.0 * 0.001
    btc_recv = 1.5 * b.coin.target * 100.0 / 1000.0 * 1000.0 * 0.001
    assert abs(b.funding - (coin_pay - btc_recv)) < 1e-6  # 롱은 냄, 숏은 받음


def test_halt_blocks_new_entries() -> None:
    sim = Sim(P(), [_sig()], Fake(lambda s, t: 100.0 if s == "AAAUSDT" else 1000.0), {})  # type: ignore[arg-type]
    sim.halted = "2022-01-01"
    sim.run(date(2022, 1, 2), date(2022, 1, 4))
    assert not sim.done and sim.skips["계좌 30% 정지"] == 1
