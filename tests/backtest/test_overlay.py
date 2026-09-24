"""O/v1 T/v2 선물 추가 노출 시뮬레이터 합성 데이터 테스트 (24 사전등록)."""

from __future__ import annotations

import math
from dataclasses import replace

import pytest

from auto_trading.backtest.overlay import (
    T2_ALONE,
    OverlayBar,
    OverlayConfig,
    simulate_overlay,
    split_weights,
)
from auto_trading.backtest.trend import DailyBar, TrendConfig, simulate_trend

D = 86_400_000
START = 1_583_020_800_000  # 2020-03-01
HISTORY = 130
FREE_T2 = TrendConfig(long_only=True, spot_cost=0.0, perp_cost=0.0)


def wave(n: int, drift: float, *, wobble: float = 0.01, base: float = 100.0) -> list[float]:
    return [base * math.exp(drift * k + wobble * math.sin(k)) for k in range(n)]


def to_bars(closes: list[float], *, dip: float = 0.001) -> dict[int, OverlayBar]:
    first = START - HISTORY * D
    return {first + k * D: OverlayBar(c * (1 - dip), c) for k, c in enumerate(closes)}


def run(
    spot: dict[str, list[float]],
    cfg: OverlayConfig,
    *,
    perp: dict[str, list[float]] | None = None,
    perp_dip: float = 0.001,
    funding: dict[str, list[tuple[int, float]]] | None = None,
):
    perp = perp or spot
    n = len(next(iter(spot.values())))
    return simulate_overlay(
        {s: to_bars(c) for s, c in spot.items()},
        {s: to_bars(c, dip=perp_dip) for s, c in perp.items()},
        funding or {},
        from_ms=START,
        to_ms=START + (n - HISTORY - 1) * D,
        initial_equity=10_000.0,
        cfg=cfg,
    )


def zigzag(n: int) -> list[float]:
    """상승·하락이 번갈아 목표 비중이 자주 바뀌는 경로."""
    return [100 * math.exp(0.3 * math.sin(k / 25) + 0.01 * math.sin(k)) for k in range(n)]


class TestSplit:
    def test_below_cap_keeps_all_spot(self) -> None:
        spot, fut = split_weights({"A": 0.3, "B": 0.2}, 1.5, 3.0)
        assert spot == pytest.approx({"A": 0.3, "B": 0.2})
        assert fut == pytest.approx({"A": 0.15, "B": 0.1})

    def test_full_weight_moves_spot_to_futures(self) -> None:
        spot, fut = split_weights({"A": 0.5, "B": 0.5}, 1.5, 3.0)
        assert sum(spot.values()) == pytest.approx(0.75)
        assert sum(fut.values()) == pytest.approx(0.75)
        # 선물 명목 = 3 × 선물 지갑(1 − 현물)
        assert sum(fut.values()) == pytest.approx(3 * (1 - sum(spot.values())))

    def test_exposure_one_has_no_futures(self) -> None:
        _, fut = split_weights({"A": 0.5, "B": 0.5}, 1.0, 3.0)
        assert sum(fut.values()) == 0.0

    def test_cash_is_zero_weight(self) -> None:
        spot, fut = split_weights({"A": 0.0}, 1.5, 3.0)
        assert spot == {"A": 0.0}
        assert fut == {"A": 0.0}


class TestControlMatchesT2:
    @pytest.mark.parametrize("path", ["up", "zigzag"])
    def test_same_path_as_simulate_trend(self, path: str) -> None:
        n = 500
        closes = (
            {"AUSDT": wave(n, 0.004), "BUSDT": zigzag(n)}
            if path == "zigzag"
            else {
                "AUSDT": wave(n, 0.004),
                "BUSDT": wave(n, 0.002, base=50.0),
            }
        )
        cfg_t2 = TrendConfig(long_only=True)
        got = run(closes, replace(T2_ALONE, trend=cfg_t2))
        first = START - HISTORY * D
        ref = simulate_trend(
            {
                s: {first + k * D: DailyBar(c * 1.001, c) for k, c in enumerate(v)}
                for s, v in closes.items()
            },
            {},
            from_ms=START,
            to_ms=START + (n - HISTORY - 1) * D,
            initial_equity=10_000.0,
            cfg=cfg_t2,
        )
        assert got.days == ref.days
        assert got.equity == pytest.approx(ref.equity, rel=1e-12)
        assert got.trades == ref.trades
        assert got.costs == pytest.approx(ref.costs, rel=1e-12)
        assert got.futures_pnl == 0.0


class TestOverlay:
    def test_exposure_is_one_and_a_half_times_t2(self) -> None:
        n = 400
        closes = {"AUSDT": wave(n, 0.004)}
        cfg = OverlayConfig(trend=FREE_T2)
        a = run(closes, replace(T2_ALONE, trend=FREE_T2))
        b = run(closes, cfg)
        # 둘 다 이미 보유 중인 마지막 날: 노출 비율 ≈ 1.5 (밴드 오차 이내)
        assert b.exposure[-1] == pytest.approx(1.5 * a.exposure[-1], abs=0.05 * 1.5 + 1e-9)
        assert b.futures_pnl > 0
        assert b.equity[-1] > a.equity[-1]

    def test_futures_leverage_never_exceeds_cap_after_trading(self) -> None:
        n = 600
        closes = {"AUSDT": wave(n, 0.006, wobble=0.002), "BUSDT": wave(n, 0.005, wobble=0.002)}
        b = run(closes, OverlayConfig(trend=FREE_T2))
        assert 0 < b.max_futures_leverage <= 3.0 + 1e-9

    def test_long_pays_positive_funding(self) -> None:
        n = 300
        closes = {"AUSDT": wave(n, 0.004)}
        times = [START + k * D + 1 for k in range(n - HISTORY)]
        b = run(
            closes, OverlayConfig(trend=FREE_T2), funding={"AUSDT": [(t, 0.001) for t in times]}
        )
        assert b.funding < 0

    def test_crash_liquidates_futures_wallet(self) -> None:
        n = 400
        up = wave(n, 0.006, wobble=0.002)
        cfg = OverlayConfig(trend=FREE_T2, max_futures_leverage=50.0, exposure=3.0)
        b = run({"AUSDT": up}, cfg, perp_dip=0.6)
        assert b.liquidations >= 1

    def test_daily_loss_blocks_increases(self) -> None:
        n = 400
        closes = wave(n, 0.004)
        drop = HISTORY + 200
        closes[drop:] = [c * 0.9 for c in closes[drop:]]  # 하루 −10%
        cfg = OverlayConfig(trend=FREE_T2)
        b = run({"AUSDT": closes}, cfg)
        assert b.loss_limit_days >= 1

    def test_halt_turns_off_futures(self) -> None:
        n = 500
        closes = wave(n, 0.004)
        cut = HISTORY + 250
        closes[cut:] = [c * 0.5 for c in closes[cut:]]
        b = run({"AUSDT": closes}, OverlayConfig(trend=FREE_T2))
        assert b.halted_day is not None
        halted_idx = b.days.index(b.halted_day)
        # 정지 뒤에는 현물만 남는다 (가격 변동에 따른 밴드 안 이탈만 허용)
        assert all(e <= 1.0 + 0.05 + 1e-9 for e in b.exposure[halted_idx:])

    def test_missing_perp_bar_raises(self) -> None:
        n = 300
        closes = {"AUSDT": wave(n, 0.004)}
        spot = {s: to_bars(c) for s, c in closes.items()}
        perp = {s: to_bars(c) for s, c in closes.items()}
        del perp["AUSDT"][START + 10 * D]
        with pytest.raises(ValueError, match="결측"):
            simulate_overlay(
                spot,
                perp,
                {},
                from_ms=START,
                to_ms=START + 50 * D,
                initial_equity=10_000.0,
                cfg=OverlayConfig(),
            )


class TestCatastropheStop:
    """O/v2 비상 스톱 (docs/research/27). 꺼져 있으면 O/v1 과 같다."""

    @staticmethod
    def crash(n: int, crash_day: int, low: float) -> tuple[list[float], dict[int, OverlayBar]]:
        closes = wave(n, 0.004, wobble=0.002)
        closes[crash_day:] = [c * 0.9 for c in closes[crash_day:]]  # 그날 종가 −10%
        bars = to_bars(closes)
        day = START - HISTORY * D + crash_day * D
        bars[day] = OverlayBar(closes[crash_day - 1] * low, closes[crash_day])  # 장중 저가
        return closes, bars

    def run_bars(self, spot: list[float], perp: dict[int, OverlayBar], cfg: OverlayConfig):
        n = len(spot)
        return simulate_overlay(
            {"AUSDT": to_bars(spot)},
            {"AUSDT": perp},
            {},
            from_ms=START,
            to_ms=START + (n - HISTORY - 1) * D,
            initial_equity=10_000.0,
            cfg=cfg,
        )

    def test_far_stop_that_never_triggers_is_identical_to_none(self) -> None:
        n = 400
        closes = {"AUSDT": wave(n, 0.004), "BUSDT": zigzag(n)}
        base = run(closes, OverlayConfig())
        far = run(closes, OverlayConfig(catastrophe_stop=0.99))
        assert far.stop_hits == 0
        assert far.equity == base.equity

    def test_crash_below_stop_closes_futures_at_stop_price(self) -> None:
        n = 400
        crash_day = HISTORY + 200
        spot, perp = self.crash(n, crash_day, low=0.70)  # 장중 −30%
        cfg = OverlayConfig(trend=FREE_T2, catastrophe_stop=0.20)
        with_stop = self.run_bars(spot, perp, cfg)
        without = self.run_bars(spot, perp, replace(cfg, catastrophe_stop=None))
        assert with_stop.stop_hits == 1
        # 스톱 체결가 = 전날 종가 × 0.8 × 0.99 — 종가(−10%)보다 불리하게 팔았다
        assert with_stop.futures_pnl < without.futures_pnl
        idx = with_stop.days.index(START + (crash_day - HISTORY) * D)
        # 그날은 일 손실 2% 로 재진입이 막히고, 다음 날 추세 신호대로 다시 든다
        assert with_stop.exposure[idx] < without.exposure[idx]
        assert with_stop.exposure[idx + 1] == pytest.approx(without.exposure[idx + 1], abs=0.1)

    def test_stop_fires_before_liquidation(self) -> None:
        n = 400
        crash_day = HISTORY + 200
        spot, perp = self.crash(
            n, crash_day, low=0.55
        )  # 장중 −45% (선물 약 5배면 청산 거리 약 −20%)
        cfg = OverlayConfig(
            trend=FREE_T2, exposure=6.0, max_futures_leverage=50.0, catastrophe_stop=0.20
        )
        assert self.run_bars(spot, perp, replace(cfg, catastrophe_stop=None)).liquidations >= 1
        with_stop = self.run_bars(spot, perp, cfg)
        assert with_stop.stop_hits == 1
        assert with_stop.liquidations == 0

    def test_dip_above_stop_does_not_trigger(self) -> None:
        n = 400
        crash_day = HISTORY + 200
        spot, perp = self.crash(n, crash_day, low=0.85)  # 장중 −15% < 20%
        with_stop = self.run_bars(spot, perp, OverlayConfig(trend=FREE_T2, catastrophe_stop=0.20))
        assert with_stop.stop_hits == 0


class TestVolSpike:
    """43: 종목별 σ10 ≥ K × σ60 이면 다음 체결에서 그 종목 배수 1.0 (선물 추가분 0)."""

    def test_never_triggering_ratio_is_identical_to_none(self) -> None:
        n = 400
        closes = {"AUSDT": wave(n, 0.004), "BUSDT": zigzag(n)}
        base = run(closes, OverlayConfig())
        far = run(closes, OverlayConfig(vol_spike_ratio=1e9))
        assert far.spike_days == 0
        assert far.equity == base.equity

    def test_calm_path_never_spikes(self) -> None:
        n = 400
        closes = {"AUSDT": wave(n, 0.004)}
        assert run(closes, OverlayConfig(vol_spike_ratio=1.5)).spike_days == 0

    def test_vol_spikes_flags_only_the_jumping_symbol(self) -> None:
        from auto_trading.backtest.overlay import vol_spikes

        calm = wave(200, 0.002)
        jumpy = calm[:-10] + [calm[-11] * (1.08 if k % 2 else 0.93) for k in range(10)]
        cfg = OverlayConfig(vol_spike_ratio=1.5)
        assert vol_spikes({"AUSDT": calm, "BUSDT": jumpy}, cfg) == frozenset({"BUSDT"})
        assert vol_spikes({"BUSDT": jumpy}, OverlayConfig()) == frozenset()
        assert vol_spikes({"BUSDT": jumpy[:50]}, cfg) == frozenset()  # 60 일 이력 부족

    def test_spike_removes_futures_for_that_symbol_only(self) -> None:
        n = 400
        a_path = wave(n, 0.004)
        b_path = wave(n, 0.004, base=50.0)
        # B 만 마지막 20 일 동안 크게 흔들리되 추세는 위로 유지
        for k in range(n - 20, n):
            b_path[k] = b_path[k] * (1.06 if k % 2 else 0.97)
        closes = {"AUSDT": a_path, "BUSDT": b_path}
        cfg = OverlayConfig(trend=FREE_T2)
        base = run(closes, cfg)
        capped = run(closes, replace(cfg, vol_spike_ratio=1.5))
        assert capped.spike_days > 0
        assert capped.exposure[-1] < base.exposure[-1]

    def test_split_weights_per_symbol_matches_scalar_when_equal(self) -> None:
        w = {"AUSDT": 0.4, "BUSDT": 0.5}
        assert split_weights(w, 1.5, 3.0) == split_weights(
            w, 1.5, 3.0, {"AUSDT": 1.5, "BUSDT": 1.5}
        )
        spot, fut = split_weights(w, 1.5, 3.0, {"AUSDT": 1.0, "BUSDT": 1.5})
        assert fut["AUSDT"] == pytest.approx(0.0)
        assert spot["AUSDT"] == pytest.approx(0.4)
        assert fut["BUSDT"] == pytest.approx(0.25)
