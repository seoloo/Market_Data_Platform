"""M/v1 다중 종목 캐리 시뮬레이터 합성 데이터 테스트 (13 사전등록)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal

import pytest

from auto_trading.backtest.multi_carry import (
    MultiBar,
    MultiCarryConfig,
    MultiCarryResult,
    simulate_multi_carry,
)

H = 3_600_000
START = 1_672_531_200_000  # 2023-01-01 00:00 UTC
EQUITY = Decimal(10_000)
FREE = MultiCarryConfig(spot_fee=Decimal(0), perp_fee=Decimal(0), slippage=Decimal(0))
P = Decimal(100)


def flat(n: int, price: Decimal = P) -> dict[int, MultiBar]:
    return {START + i * H: MultiBar(price, price, price, price, price) for i in range(n)}


def rates(
    n_hours: int, rate: Decimal, *, every: int = 8, history_days: int = 8
) -> list[tuple[int, Decimal]]:
    first = START - history_days * 24 * H
    return [(ms + 5, rate) for ms in range(first, START + n_hours * H, every * H)]


def run(
    bars: Mapping[str, Mapping[int, MultiBar]],
    funding: Mapping[str, Sequence[tuple[int, Decimal]]],
    universe: Sequence[str],
    n_hours: int,
    cfg: MultiCarryConfig = FREE,
) -> MultiCarryResult:
    return simulate_multi_carry(
        bars,
        funding,
        {START: list(universe)},
        from_ms=START,
        to_ms=START + (n_hours - 1) * H,
        initial_equity=EQUITY,
        cfg=cfg,
    )


class TestSelection:
    def test_picks_highest_funding_up_to_slots(self) -> None:
        n = 48
        symbols = [f"S{k}USDT" for k in range(6)]
        bars = {s: flat(n) for s in symbols}
        funding = {s: rates(n, Decimal("0.0001") * (k + 1)) for k, s in enumerate(symbols)}
        result = run(bars, funding, symbols, n)
        assert result.symbols_held == {"S5USDT", "S4USDT", "S3USDT", "S2USDT"}
        assert result.entries == 4

    def test_volume_control_keeps_universe_order(self) -> None:
        n = 24
        symbols = [f"S{k}USDT" for k in range(6)]
        bars = {s: flat(n) for s in symbols}
        funding = {s: rates(n, Decimal("0.0001") * (k + 1)) for k, s in enumerate(symbols)}
        cfg = MultiCarryConfig(
            spot_fee=Decimal(0), perp_fee=Decimal(0), slippage=Decimal(0), rank_by_volume=True
        )
        result = run(bars, funding, symbols, n, cfg)
        assert result.symbols_held == {"S0USDT", "S1USDT", "S2USDT", "S3USDT"}

    def test_below_threshold_or_short_history_is_skipped(self) -> None:
        n = 24
        bars = {"AUSDT": flat(n), "BUSDT": flat(n)}
        funding = {
            "AUSDT": rates(n, Decimal("0.00009")),
            "BUSDT": rates(n, Decimal("0.001"), history_days=3),  # 이력 3일 → 첫 판단 불가
        }
        result = run(bars, funding, ["AUSDT", "BUSDT"], n)
        assert result.entries == 0  # A는 문턱 아래, B는 24시간 안에 이력 6일을 채우지 못한다

    def test_four_hour_interval_is_normalised_to_eight_hours(self) -> None:
        n = 24
        bars = {"AUSDT": flat(n)}
        # 4시간마다 0.00006 → 8시간 환산 0.00012 ≥ 0.0001
        result = run(bars, {"AUSDT": rates(n, Decimal("0.00006"), every=4)}, ["AUSDT"], n)
        assert result.entries == 1

    def test_leaving_universe_closes_position(self) -> None:
        n = 24 * 40  # 2월로 넘어감
        bars = {"AUSDT": flat(n), "BUSDT": flat(n)}
        funding = {s: rates(n, Decimal("0.0002")) for s in bars}
        feb = 1_675_209_600_000
        result = simulate_multi_carry(
            bars,
            funding,
            {START: ["AUSDT"], feb: ["BUSDT"]},
            from_ms=START,
            to_ms=START + (n - 1) * H,
            initial_equity=EQUITY,
            cfg=FREE,
        )
        assert result.symbols_held == {"AUSDT", "BUSDT"}
        assert result.exits == 2  # 2월 A 해제 + 구간 끝 B 정리


class TestAccounting:
    def test_funding_income_on_one_x_hedge(self) -> None:
        n = 24 * 5
        result = run({"AUSDT": flat(n)}, {"AUSDT": rates(n, Decimal("0.0002"))}, ["AUSDT"], n)
        # 슬롯 = 10000/4, 현물 절반 → 수량 12.5. 00:00 판단 → 01:00 체결 → 정산 14회
        expected = Decimal("12.5") * P * Decimal("0.0002") * 14
        assert result.funding_income == pytest.approx(expected)
        assert result.final_equity == pytest.approx(EQUITY + expected)

    def test_price_moves_are_hedged_and_rebalanced(self) -> None:
        n = 60
        prices = [P * Decimal("1.02") ** k for k in range(n)]
        bars = {"AUSDT": {START + i * H: MultiBar(p, p, p, p, p) for i, p in enumerate(prices)}}
        result = run(bars, {"AUSDT": rates(n, Decimal(0))}, ["AUSDT"], n, FREE)
        assert result.entries == 0  # funding 0은 문턱 아래
        cfg = MultiCarryConfig(
            spot_fee=Decimal(0),
            perp_fee=Decimal(0),
            slippage=Decimal(0),
            enter_rate=Decimal(0),
            exit_rate=Decimal(-1),
        )
        result = run(bars, {"AUSDT": rates(n, Decimal(0))}, ["AUSDT"], n, cfg)
        assert result.rebalances >= 2
        assert result.liquidations == 0
        assert result.final_equity == pytest.approx(EQUITY)

    def test_costs_are_charged_on_both_legs(self) -> None:
        n = 24
        cfg = MultiCarryConfig(enter_rate=Decimal(0), exit_rate=Decimal(-1))
        result = run({"AUSDT": flat(n)}, {"AUSDT": rates(n, Decimal(0))}, ["AUSDT"], n, cfg)
        notional = EQUITY / 4 / 2
        # 현물 0.10% + 선물 0.045% + 다리별 0.05% 슬리피지, 진입·청산
        expected = float(notional) * (2 * 0.001 + 2 * 0.00045 + 4 * 0.0005)
        assert float(EQUITY - result.final_equity) == pytest.approx(expected, rel=0.01)

    def test_stale_prices_force_close_with_penalty(self) -> None:
        n = 60
        bars = flat(10)  # 10시간 뒤 데이터 끊김 (상장폐지)
        result = run({"AUSDT": bars}, {"AUSDT": rates(n, Decimal("0.0002"))}, ["AUSDT"], n)
        assert result.forced_closes == 1
        assert result.final_equity < EQUITY + result.funding_income

    def test_intrahour_spike_liquidates_one_x_only_near_double(self) -> None:
        n = 6
        cfg = MultiCarryConfig(enter_rate=Decimal(0))
        safe = flat(n)
        safe[START + 5 * H] = MultiBar(P, P * Decimal("1.8"), P, P, P)
        result = run({"AUSDT": safe}, {"AUSDT": rates(n, Decimal(0))}, ["AUSDT"], n, cfg)
        assert result.liquidations == 0
        wild = flat(n)
        wild[START + 5 * H] = MultiBar(P, P * Decimal("2.1"), P, P, P)
        result = run({"AUSDT": wild}, {"AUSDT": rates(n, Decimal(0))}, ["AUSDT"], n, cfg)
        assert result.liquidations == 1
