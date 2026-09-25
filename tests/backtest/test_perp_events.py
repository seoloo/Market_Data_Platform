"""BTCUSDT 무기한 포지셔닝 이벤트 탐지·거래 결과 합성 테스트 (48)."""

from __future__ import annotations

import math
from dataclasses import replace

import pytest

from auto_trading.backtest.perp_events import (
    BAR_MS,
    DAY_MS,
    Bars,
    PerpEvent,
    daily_thresholds,
    detect_fb,
    detect_lq,
    prior_extremes,
    settle_trade,
)

START = 1_609_459_200_000  # 2021-01-01
DAYS = 35  # 30일 기준 창 + 여유


def make_bars(
    closes: list[float],
    *,
    oi: list[float | None] | None = None,
    funding: float = 0.0001,
    highs: list[float] | None = None,
    lows: list[float] | None = None,
) -> Bars:
    n = len(closes)
    opens = [closes[0], *closes[:-1]]
    return Bars(
        open_ms=[START + i * BAR_MS for i in range(n)],
        open=opens,
        high=highs or [max(o, c) * 1.0005 for o, c in zip(opens, closes, strict=True)],
        low=lows or [min(o, c) * 0.9995 for o, c in zip(opens, closes, strict=True)],
        close=closes,
        oi=oi if oi is not None else [1000.0 + 5 * math.sin(i / 7) for i in range(n)],
        funding=[funding] * n,
    )


def calm(n: int) -> list[float]:
    return [100 * (1 + 0.002 * math.sin(i / 11)) for i in range(n)]


def settlements(n_bars: int, rate: float = 0.0001) -> list[tuple[int, float]]:
    end = START + n_bars * BAR_MS
    return [(t, rate) for t in range(START - 100 * DAY_MS, end, 8 * 3_600_000)]


class TestHelpers:
    def test_prior_extremes(self) -> None:
        xs = [3.0, 1.0, 4.0, 1.0, 5.0, 9.0, 2.0]
        assert prior_extremes(xs, 3, high=True) == [None, None, None, 4.0, 4.0, 5.0, 9.0]
        assert prior_extremes(xs, 3, high=False) == [None, None, None, 1.0, 1.0, 1.0, 1.0]

    def test_daily_thresholds_use_only_prior_days(self) -> None:
        times = [START + i * 3_600_000 for i in range(24 * 5)]
        values: list[float | None] = [
            float(i // 24) for i in range(24 * 5)
        ]  # 날마다 값 = 날짜 번호
        thr = daily_thresholds(
            times, values, window_days=2, q=1.0, min_coverage=0.9, expected_per_day=24
        )
        assert START not in thr and START + DAY_MS not in thr  # 이전 이틀이 없다
        assert thr[START + 2 * DAY_MS] == 1.0  # 그날(2) 값은 쓰지 않는다
        assert thr[START + 4 * DAY_MS] == 3.0

    def test_bars_reject_gap(self) -> None:
        b = make_bars(calm(10))
        with pytest.raises(ValueError):
            replace(b, open_ms=[*b.open_ms[:5], b.open_ms[5] + BAR_MS, *b.open_ms[6:]])


def lq_scenario() -> tuple[list[float], list[float | None], int]:
    n = DAYS * 288
    closes = calm(n)
    oi: list[float | None] = [1000.0 + 5 * math.sin(i / 7) for i in range(n)]
    crash = 32 * 288
    for k in range(12):  # 1시간 동안 −8%, OI −10%
        closes[crash - 11 + k] = closes[crash - 12] * (1 - 0.08 * (k + 1) / 12)
        oi[crash - 11 + k] = 1000.0 * (1 - 0.10 * (k + 1) / 12)
    for i in range(crash + 1, n):
        closes[i] = closes[crash] * (1 + 0.002 * math.sin(i / 11))
        oi[i] = 900.0 + 5 * math.sin(i / 7)
    return closes, oi, crash


class TestLq:
    def test_detects_crash_with_oi_drop(self) -> None:
        closes, oi, crash = lq_scenario()
        evs = detect_lq(make_bars(closes, oi=oi))
        hits = [e for e in evs if crash - 12 <= e.signal_index <= crash]
        assert len(hits) == 1
        e = hits[0]
        assert e.side == -1 and e.entry_index == e.signal_index + 1
        assert e.extreme_low <= closes[e.signal_index]  # 그 1시간의 저점 (롱 반전의 스톱)

    def test_price_move_without_oi_drop_is_not_an_event(self) -> None:
        closes, _, crash = lq_scenario()
        flat_oi: list[float | None] = [1000.0 + 5 * math.sin(i / 7) for i in range(len(closes))]
        evs = detect_lq(make_bars(closes, oi=flat_oi))
        assert not [e for e in evs if crash - 12 <= e.signal_index <= crash]


def fb_scenario(*, fail: bool) -> tuple[list[float], list[float | None], int]:
    n = DAYS * 288
    closes = calm(n)
    oi: list[float | None] = [1000.0 + 5 * math.sin(i / 7) for i in range(n)]
    b = 32 * 288
    top = max(closes[b - 300 : b])
    for k in range(12):  # 1시간 동안 OI +8%, 마지막 봉 종가가 24시간 고점을 넘는다
        oi[b - 11 + k] = 1000.0 * (1 + 0.08 * (k + 1) / 12)
    closes[b] = top * 1.004
    for i in range(b + 1, n):
        oi[i] = 1080.0
        closes[i] = top * (0.995 if fail and i >= b + 6 else 1.004)
    return closes, oi, b


class TestFb:
    def test_failed_breakout_with_crowding_goes_short(self) -> None:
        closes, oi, b = fb_scenario(fail=True)
        bars = make_bars(closes, oi=oi, funding=0.0003)
        sett = settlements(len(closes)) + [(START + 31 * DAY_MS + k, 0.0003) for k in range(3)]
        sett.sort()
        evs = detect_fb(bars, sett)
        hits = [e for e in evs if b - 1 <= e.signal_index <= b + 72]
        assert len(hits) == 1
        e = hits[0]
        assert e.side == -1
        assert e.signal_index == b + 6  # 처음으로 돌파 기준선 아래 종가
        assert closes[e.signal_index] < max(bars.high[b - 288 : b])
        assert e.extreme_high >= closes[b]

    def test_breakout_that_holds_is_not_an_event(self) -> None:
        closes, oi, b = fb_scenario(fail=False)
        bars = make_bars(closes, oi=oi, funding=0.0003)
        sett = sorted([*settlements(len(closes)), (START + 31 * DAY_MS + 1, 0.0003)])
        assert not [e for e in detect_fb(bars, sett) if e.signal_index >= b - 1]

    def test_no_event_without_hot_funding(self) -> None:
        closes, oi, b = fb_scenario(fail=True)
        bars = make_bars(closes, oi=oi, funding=0.0001)
        sett = settlements(len(closes), rate=0.0001)
        # 과열 = funding ≥ 90일 80% 백분위 **그리고 양수**. 늘 0.0001 이면 통과, 음수면 통과 못 한다
        neg = replace(bars, funding=[-0.0001] * len(closes))
        assert not [e for e in detect_fb(neg, sett) if e.signal_index >= b - 1]
        assert [e for e in detect_fb(bars, sett) if e.signal_index >= b - 1]


class TestSettle:
    def _bars(self) -> Bars:
        closes = [100.0, 100.0, 101.0, 99.0, 98.0, 97.0]
        return make_bars(
            closes,
            highs=[100.0, 100.5, 101.5, 101.0, 99.0, 98.0],
            lows=[99.5, 99.5, 100.0, 98.5, 97.5, 96.5],
        )

    def test_time_exit_with_cost_and_funding(self) -> None:
        bars = self._bars()
        e = PerpEvent("X", 0, 1, -1, 200.0, 0.0)
        sett = [(bars.open_ms[2] + 1, 0.001)]
        t = settle_trade(
            bars, e, direction=-1, hold_bars=4, cost_per_side=0.001, settlements=sett, use_stop=True
        )
        assert t is not None and not t.stopped
        assert t.entry == 100.0 and t.exit == 98.0  # 진입 봉 1 시가 → 봉 4 종가
        assert t.gross == pytest.approx(0.02)
        assert t.funding == pytest.approx(0.001)  # 숏은 양수 funding 을 받는다
        assert t.net == pytest.approx(0.02 + 0.001 - 0.002)

    def test_stop_hit_inside_bar(self) -> None:
        bars = self._bars()
        e = PerpEvent("X", 0, 1, -1, 101.2, 0.0)
        t = settle_trade(
            bars, e, direction=-1, hold_bars=4, cost_per_side=0.0, settlements=[], use_stop=True
        )
        assert t is not None and t.stopped and t.exit == 101.2
        assert t.gross == pytest.approx(-0.012)

    def test_stop_on_wrong_side_is_ignored(self) -> None:
        bars = self._bars()
        e = PerpEvent("X", 0, 1, -1, 99.0, 0.0)  # 숏인데 스톱이 진입가 아래
        t = settle_trade(
            bars, e, direction=-1, hold_bars=4, cost_per_side=0.0, settlements=[], use_stop=True
        )
        assert t is not None and not t.stopped

    def test_not_enough_bars(self) -> None:
        bars = self._bars()
        e = PerpEvent("X", 0, 3, 1, 0.0, 0.0)
        t = settle_trade(
            bars, e, direction=1, hold_bars=10, cost_per_side=0.0, settlements=[], use_stop=False
        )
        assert t is None


@pytest.mark.leakage
def test_future_bars_do_not_change_past_events() -> None:
    closes, oi, _ = lq_scenario()
    fb_closes, fb_oi, _ = fb_scenario(fail=True)
    for cl, o in ((closes, oi), (fb_closes, fb_oi)):
        cut = len(cl) - 288
        full = make_bars(cl, oi=o, funding=0.0003)
        part = make_bars(cl[:cut], oi=o[:cut], funding=0.0003)
        sett = settlements(len(cl))
        pairs = (
            (detect_lq(full), detect_lq(part)),
            (detect_fb(full, sett), detect_fb(part, sett)),
        )
        for a, b in pairs:
            early = [e for e in a if e.signal_index < cut - 80]
            assert early == [e for e in b if e.signal_index < cut - 80]
    assert detect_lq(make_bars(closes, oi=oi))  # 이벤트가 실제로 있는 경로에서 확인했다
