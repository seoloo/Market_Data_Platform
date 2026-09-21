"""SnapshotBuilder — 인과성 관문 테스트.

여기서 검증하는 성질 하나하나가 백테스트 성적의 진위를 좌우한다.
특히 ``test_forming_bar_is_never_included`` 가 깨지면 모든 성과 수치가 무의미해진다.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from auto_trading.config.schema import RootConfig
from auto_trading.core.enums import Mode, Timeframe
from auto_trading.core.errors import DataIntegrityError
from auto_trading.core.types import Candle
from auto_trading.marketdata.snapshot import (
    SWING_LOOKBACK_BARS,
    SnapshotBuilder,
    TimeframeRequirement,
    build_requirements,
)
from auto_trading.storage.repositories.candles import CandleRepository

D = Decimal
SYMBOL = "BTCUSDT"
INGESTED = 1_700_000_000_000

pytestmark = pytest.mark.leakage


def config(**overrides: object) -> RootConfig:
    """warm-up 이 얕은 설정. 깊이 계산 자체가 검증 대상이므로 값을 명시한다."""
    payload: dict[str, object] = {
        "features": {
            "b1_trend": {
                "timeframe": "4h",
                "params": {"ema_period": 5, "atr_period": 3, "slope_bars": 2},
            },
            "b2_volregime": {
                "timeframe": "1h",
                "params": {"atr_period": 3, "lookback_days": 1},
            },
            "t1_pullback": {"timeframe": "15m", "params": {"rsi_period": 3}},
            "t2_momentum": {
                "timeframe": "5m",
                "params": {"return_bars": 2, "zscore_lookback": 6},
            },
            "t3_volume": {"timeframe": "5m", "params": {"sma_period": 3}},
        },
        "data": {"warmup_safety_bars": 0},
        "risk": {
            "stop": {"atr_timeframe": "1h", "atr_period": 3},
            "volatility_gate": {"enabled": False},
            "recent_spike_window_min": 2,
        },
    }
    payload.update(overrides)
    return RootConfig.model_validate(payload)


TEST_TIMEFRAMES = (Timeframe.M1, Timeframe.M5, Timeframe.M15, Timeframe.H1, Timeframe.H4)

SHALLOW_REQUIREMENTS = (
    TimeframeRequirement(Timeframe.M1, 60, "test"),
    TimeframeRequirement(Timeframe.M5, 40, "test"),
    TimeframeRequirement(Timeframe.M15, 30, "test"),
    TimeframeRequirement(Timeframe.H1, 20, "test"),
    TimeframeRequirement(Timeframe.H4, 10, "test"),
)
"""실제 warm-up 깊이는 ``TestRequirements`` 가 따로 검증한다.

인과성·품질 테스트까지 1년치 봉을 채우면 느리기만 하고 검증력은 같으므로,
여기서는 얕은 요구치를 주입한다.
"""

HORIZON_MS = 100 * Timeframe.H1.ms
"""모든 TF 가 **같은 벽시계 구간**을 덮게 한다.

TF 마다 같은 '봉 개수'를 넣으면 1m 은 100분, 1h 는 100시간이 되어 짧은 TF 가
인위적으로 stale 이 된다 — 데이터가 아니라 픽스처의 결함이다.
"""


def candle_at(timeframe: Timeframe, index: int) -> Candle:
    price = D(100 + index % 7)
    open_ms = index * timeframe.ms
    return Candle(
        open_ms=open_ms,
        close_ms=open_ms + timeframe.ms - 1,
        open=price,
        high=price + D(1),
        low=price - D(1),
        close=price,
        volume=D(10 + index % 3),
    )


def fill_all(repo: CandleRepository, *, horizon_ms: int = HORIZON_MS) -> None:
    """모든 TF 를 0 부터 ``horizon_ms`` 까지 빈틈없이 채운다."""
    for timeframe in TEST_TIMEFRAMES:
        made = [candle_at(timeframe, i) for i in range(horizon_ms // timeframe.ms)]
        repo.upsert_many(SYMBOL, timeframe, made, source="bulk", ingested_ms=INGESTED)


def builder(repo: CandleRepository) -> SnapshotBuilder:
    return SnapshotBuilder(repo, config(), requirements=SHALLOW_REQUIREMENTS)


def as_of_of(timeframe: Timeframe, index: int) -> int:
    """``index`` 번째 봉이 막 마감된 시각."""
    return index * timeframe.ms + timeframe.ms - 1


# ----------------------------------------------------------------- 요구치 계산


class TestRequirements:
    def test_depth_is_derived_from_feature_warmup_not_hardcoded(self) -> None:
        shallow = {r.timeframe: r.bars for r in build_requirements(config())}
        deep_cfg = config(
            features={
                **config().features.model_dump(mode="json"),
                "t1_pullback": {"timeframe": "15m", "params": {"rsi_period": 30}},
            }
        )
        deep = {r.timeframe: r.bars for r in build_requirements(deep_cfg)}
        assert deep[Timeframe.M15] > shallow[Timeframe.M15]

    def test_deepest_consumer_wins_when_timeframe_is_shared(self) -> None:
        # 5m 을 T2(return 2 + lookback 6 = 8)와 T3(sma 3)가 함께 쓴다.
        required = {r.timeframe: r.bars for r in build_requirements(config())}
        assert required[Timeframe.M5] == 8

    def test_safety_bars_are_added_to_every_timeframe(self) -> None:
        without = {r.timeframe: r.bars for r in build_requirements(config())}
        with_safety = {
            r.timeframe: r.bars for r in build_requirements(config(data={"warmup_safety_bars": 25}))
        }
        assert all(with_safety[tf] == without[tf] + 25 for tf in without)

    def test_stop_timeframe_reserves_room_for_a_confirmed_swing(self) -> None:
        required = {r.timeframe: r.bars for r in build_requirements(config())}
        assert required[Timeframe.H1] >= SWING_LOOKBACK_BARS

    def test_volatility_gate_dominates_when_enabled(self) -> None:
        cfg = config(
            risk={
                "stop": {"atr_timeframe": "1h", "atr_period": 3},
                "volatility_gate": {"enabled": True, "timeframe": "1h", "lookback_days": 365},
            }
        )
        required = {r.timeframe: r.bars for r in build_requirements(cfg)}
        assert required[Timeframe.H1] > 365 * 24

    def test_requirements_are_sorted_shortest_timeframe_first(self) -> None:
        bars = [r.timeframe.ms for r in build_requirements(config())]
        assert bars == sorted(bars)

    def test_zero_bars_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="1 이상"):
            TimeframeRequirement(timeframe=Timeframe.H1, bars=0, reason="x")


# ------------------------------------------------------------------- 인과성


class TestCausality:
    def test_forming_bar_is_never_included(self, repo: CandleRepository) -> None:
        """형성 중인 봉이 한 개라도 들어가면 미래를 보는 것이다."""
        fill_all(repo)
        build = builder(repo)
        # 90번 봉의 **중간** 시점 — 90번 봉은 아직 마감되지 않았다.
        as_of = 90 * Timeframe.H1.ms + Timeframe.H1.ms // 2
        snapshot = build.build(as_of, mode=Mode.BACKTEST)
        last = snapshot.require(Timeframe.H1).last
        assert last is not None
        assert last.open_ms == 89 * Timeframe.H1.ms

    def test_bar_closing_exactly_at_as_of_is_included(self, repo: CandleRepository) -> None:
        fill_all(repo)
        build = builder(repo)
        as_of = as_of_of(Timeframe.H1, 99)
        last = build.build(as_of, mode=Mode.BACKTEST).require(Timeframe.H1).last
        assert last is not None
        assert last.open_ms == 99 * Timeframe.H1.ms

    def test_every_series_passes_snapshot_causality_validation(
        self, repo: CandleRepository
    ) -> None:
        fill_all(repo)
        build = builder(repo)
        for index in (300, 700, 1100):
            snapshot = build.build(as_of_of(Timeframe.M5, index), mode=Mode.BACKTEST)
            snapshot.validate_causal()  # 위반 시 CausalityError

    def test_later_as_of_never_removes_bars_already_visible(self, repo: CandleRepository) -> None:
        """창이 밀려도 **겹치는 구간의 봉은 한 글자도 달라지지 않아야** 한다.

        과거 봉이 나중에 바뀌면 그게 repainting 이고, 백테스트가 실거래에서
        재현되지 않는다. 창에서 밀려나 사라지는 것은 정상이다.
        """
        fill_all(repo)
        build = builder(repo)
        early = build.build(as_of_of(Timeframe.H1, 40), mode=Mode.BACKTEST)
        late = build.build(as_of_of(Timeframe.H1, 41), mode=Mode.BACKTEST)
        early_bars = early.require(Timeframe.M15).candles
        late_bars = late.require(Timeframe.M15).candles
        overlap = {c.open_ms for c in early_bars} & {c.open_ms for c in late_bars}
        assert overlap, "두 창이 전혀 겹치지 않으면 검증이 성립하지 않는다"
        by_open = {c.open_ms: c for c in late_bars}
        assert all(by_open[c.open_ms] == c for c in early_bars if c.open_ms in overlap)


# --------------------------------------------------------------- 데이터 품질


class TestDataQuality:
    def test_missing_bars_fail_closed_instead_of_being_filled(self, repo: CandleRepository) -> None:
        fill_all(repo, horizon_ms=5 * Timeframe.H1.ms)  # 어느 TF 도 요구치를 못 채운다
        snapshot = builder(repo).build(as_of_of(Timeframe.H1, 3), mode=Mode.BACKTEST)
        assert not snapshot.data_quality.ok
        assert not snapshot.data_quality.warmup_ok
        assert Timeframe.H1 in snapshot.data_quality.missing_timeframes
        assert any("봉 부족" in note for note in snapshot.data_quality.notes)

    def test_stale_feed_is_flagged(self, repo: CandleRepository) -> None:
        fill_all(repo)
        build = builder(repo)
        # 마지막 봉보다 한참 뒤 시점 — 피드가 끊긴 상태.
        as_of = as_of_of(Timeframe.H1, 99) + 10 * Timeframe.H1.ms
        quality = build.build(as_of, mode=Mode.BACKTEST).data_quality
        assert not quality.ok
        assert Timeframe.H1 in quality.stale_timeframes

    def test_fresh_feed_is_not_flagged_stale(self, repo: CandleRepository) -> None:
        fill_all(repo)
        build = builder(repo)
        quality = build.build(as_of_of(Timeframe.H1, 99), mode=Mode.BACKTEST).data_quality
        assert quality.stale_timeframes == ()

    def test_gap_in_history_is_flagged(self, repo: CandleRepository) -> None:
        fill_all(repo)
        # 5m 시계열 중간의 봉 하나를 지워 결측을 만든다.
        repo._conn.execute(  # 결측 상황을 만들기 위한 테스트 전용 조작
            "DELETE FROM candles WHERE symbol = ? AND timeframe = ? AND open_ms = ?",
            (SYMBOL, Timeframe.M5.value, 1195 * Timeframe.M5.ms),
        )
        quality = (
            SnapshotBuilder(repo, config())
            .build(as_of_of(Timeframe.M5, 1199), mode=Mode.BACKTEST)
            .data_quality
        )
        assert Timeframe.M5 in quality.gapped_timeframes
        assert not quality.ok

    def test_healthy_snapshot_is_ok(self, repo: CandleRepository) -> None:
        fill_all(repo)
        quality = builder(repo).build(as_of_of(Timeframe.H1, 99), mode=Mode.BACKTEST).data_quality
        assert quality.ok
        assert quality.warmup_ok


# -------------------------------------------------------------- mark price


class TestMarkPrice:
    def test_explicit_mark_price_wins(self, repo: CandleRepository) -> None:
        fill_all(repo)
        snapshot = builder(repo).build(
            as_of_of(Timeframe.H1, 99), mode=Mode.BACKTEST, mark_price=D("12345.6")
        )
        assert snapshot.mark_price == D("12345.6")
        assert not any("mark_price 출처" in note for note in snapshot.data_quality.notes)

    def test_derived_mark_price_records_its_source(self, repo: CandleRepository) -> None:
        """출처가 기록에 남아야 실거래 mark price 와 다르다는 걸 나중에 알 수 있다."""
        fill_all(repo)
        snapshot = builder(repo).build(as_of_of(Timeframe.H1, 99), mode=Mode.BACKTEST)
        m1_last = snapshot.require(Timeframe.M1).last
        assert m1_last is not None
        assert snapshot.mark_price == m1_last.close
        assert any("mark_price 출처: 1m" in note for note in snapshot.data_quality.notes)

    def test_no_bars_at_all_raises_instead_of_guessing(self, repo: CandleRepository) -> None:
        with pytest.raises(DataIntegrityError, match="mark_price"):
            builder(repo).build(as_of_of(Timeframe.H1, 5), mode=Mode.BACKTEST)


# ------------------------------------------------------------------- 기타


def test_symbol_defaults_to_config(repo: CandleRepository) -> None:
    fill_all(repo)
    snapshot = builder(repo).build(as_of_of(Timeframe.H1, 99), mode=Mode.BACKTEST)
    assert snapshot.symbol == SYMBOL


def test_same_as_of_produces_identical_snapshots(repo: CandleRepository) -> None:
    fill_all(repo)
    build = builder(repo)
    as_of = as_of_of(Timeframe.H1, 99)
    first = build.build(as_of, mode=Mode.BACKTEST)
    second = build.build(as_of, mode=Mode.BACKTEST)
    assert first.digest() == second.digest()
