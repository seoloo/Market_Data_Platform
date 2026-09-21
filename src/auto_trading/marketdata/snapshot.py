"""저장된 봉에서 :class:`MarketSnapshot` 을 만드는 인과성 관문.

**이 모듈이 look-ahead 를 막는 마지막 실무 방어선이다.** Pure Core 는 스냅샷에 담긴
것만 볼 수 있으므로 (AGENTS.md §3.2), 여기서 미래 봉을 한 개라도 넣으면 백테스트
성적이 통째로 거짓이 된다. 그래서 이 모듈의 규칙은 셋뿐이다.

1. ``close_ms <= as_of_ms`` 인 봉만 넣는다 — 형성 중인 봉은 존재하지 않는 것으로 취급한다.
2. 부족한 데이터를 **채우지 않는다** — 모자라면 ``DataQuality.ok=False`` 로 표시하고
   진입은 상위에서 거부된다 (fail-closed, AGENTS.md §3.6).
3. 필요한 봉 수는 feature 가 선언한 ``warmup_bars`` 에서 **파생**한다. 손으로 적으면
   설정을 바꿨을 때 조용히 어긋난다.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from auto_trading.config.schema import RootConfig
from auto_trading.core.enums import Mode, Timeframe
from auto_trading.core.errors import DataIntegrityError
from auto_trading.core.types import DataQuality, MarketSnapshot, TimeframeSeries
from auto_trading.features.registry import build_feature_set
from auto_trading.storage.repositories.candles import CandleRepository

__all__ = [
    "SWING_LOOKBACK_BARS",
    "SnapshotBuilder",
    "TimeframeRequirement",
    "build_requirements",
]

SWING_LOOKBACK_BARS = 720
"""구조 손절용 ZigZag 스윙을 찾기 위해 확보할 손절 TF 봉 수.

1시간봉 실측에서 임계 2% 스윙의 확정 지연은 평균 6.3h · 최대 70h 였다
(05-risk-engine.md §3.1). 720봉(30일)이면 최악의 확정 지연보다 10배 이상
여유가 있어, 확정 스윙이 하나도 없어 손절이 ATR 로만 잡히는 상황을 피한다.
"""


@dataclass(frozen=True, slots=True)
class TimeframeRequirement:
    """한 타임프레임에서 확보해야 할 마감봉 수."""

    timeframe: Timeframe
    bars: int
    reason: str

    def __post_init__(self) -> None:
        if self.bars <= 0:
            raise ValueError(f"bars 는 1 이상이어야 한다: {self.bars}")


def build_requirements(cfg: RootConfig) -> tuple[TimeframeRequirement, ...]:
    """설정에서 타임프레임별 필요 봉 수를 계산한다.

    같은 TF 를 여러 소비자가 쓰면 가장 깊은 요구치를 택한다. 각 소비자가 자기
    warm-up 을 선언하므로, feature 파라미터를 바꾸면 스냅샷 깊이도 따라 바뀐다.
    """
    safety = cfg.data.warmup_safety_bars
    needs: dict[Timeframe, tuple[int, str]] = {}

    def require(timeframe: Timeframe, bars: int, reason: str) -> None:
        current = needs.get(timeframe)
        total = bars + safety
        if current is None or total > current[0]:
            needs[timeframe] = (total, reason)

    for feature in build_feature_set(cfg.features).as_tuple():
        require(feature.timeframe, feature.warmup_bars, feature.name)

    stop = cfg.risk.stop
    # Wilder ATR 은 시드 영향이 남으므로 period 의 4배를 확보한다 (indicators.py 참조).
    require(stop.atr_timeframe, stop.atr_period * 4, "risk.stop.atr")
    require(stop.atr_timeframe, SWING_LOOKBACK_BARS, "risk.stop.swing")

    gate = cfg.risk.volatility_gate
    if gate.enabled:
        require(
            gate.timeframe,
            gate.atr_period + gate.lookback_days * (Timeframe.D1.ms // gate.timeframe.ms),
            "risk.volatility_gate",
        )

    # G5.3 급등락 게이트는 1m 종가 차이를 본다 (decision._recent_price_change).
    require(Timeframe.M1, cfg.risk.recent_spike_window_min + 1, "risk.recent_spike")

    return tuple(
        TimeframeRequirement(timeframe=tf, bars=bars, reason=reason)
        for tf, (bars, reason) in sorted(needs.items(), key=lambda item: item[0].ms)
    )


class SnapshotBuilder:
    """저장소에서 인과적으로 안전한 스냅샷을 조립한다.

    백테스트와 실거래가 **같은** 빌더를 쓴다. 다르면 parity test(07 §6.1)가
    검증하는 대상 자체가 달라진다.
    """

    def __init__(
        self,
        repo: CandleRepository,
        cfg: RootConfig,
        *,
        requirements: tuple[TimeframeRequirement, ...] | None = None,
    ) -> None:
        self._repo = repo
        self._cfg = cfg
        self._requirements = build_requirements(cfg) if requirements is None else requirements

    @property
    def requirements(self) -> tuple[TimeframeRequirement, ...]:
        return self._requirements

    def build(
        self,
        as_of_ms: int,
        *,
        mode: Mode,
        symbol: str | None = None,
        mark_price: Decimal | None = None,
        last_price: Decimal | None = None,
        funding_rate: Decimal | None = None,
        next_funding_ms: int | None = None,
        spread_bps: Decimal | None = None,
    ) -> MarketSnapshot:
        """``as_of_ms`` 시점에 알 수 있었던 시장 상태를 만든다.

        ``mark_price`` 를 주지 않으면 가장 짧은 TF 의 마지막 **마감** 종가를 쓴다.
        이것이 백테스트 관례이며, 그 사실을 ``data_quality.notes`` 에 남긴다 —
        실거래(거래소 mark price)와 값의 출처가 다르다는 점이 기록에 보여야 한다.
        """
        target = self._cfg.symbol if symbol is None else symbol
        series: list[TimeframeSeries] = []
        missing: list[Timeframe] = []
        stale: list[Timeframe] = []
        gapped: list[Timeframe] = []
        notes: list[str] = []

        for requirement in self._requirements:
            loaded = self._repo.get_closed_series(
                target, requirement.timeframe, as_of_ms, limit=requirement.bars
            )
            series.append(loaded)
            if len(loaded) < requirement.bars:
                missing.append(requirement.timeframe)
                notes.append(
                    f"{requirement.timeframe.value} 봉 부족: "
                    f"{len(loaded)}/{requirement.bars} ({requirement.reason})"
                )
            if loaded.has_gaps():
                gapped.append(requirement.timeframe)
            if self._is_stale(loaded, as_of_ms):
                stale.append(requirement.timeframe)

        resolved_mark = self._resolve_mark_price(series, mark_price, notes)
        quality = DataQuality(
            ok=not missing and not stale and not gapped,
            warmup_ok=not missing,
            stale_timeframes=tuple(stale),
            missing_timeframes=tuple(missing),
            gapped_timeframes=tuple(gapped),
            notes=tuple(notes),
        )
        return MarketSnapshot(
            symbol=target,
            as_of_ms=as_of_ms,
            mode=mode,
            series=tuple(series),
            mark_price=resolved_mark,
            last_price=last_price,
            funding_rate=funding_rate,
            next_funding_ms=next_funding_ms,
            spread_bps=spread_bps,
            data_quality=quality,
        )

    def _is_stale(self, series: TimeframeSeries, as_of_ms: int) -> bool:
        """마지막 마감봉이 너무 오래됐는가.

        정상이라면 마지막 마감봉의 나이는 봉 하나 길이 미만이다. 그보다
        ``staleness_multiple`` 배 넘게 벌어지면 피드가 끊긴 것으로 본다.
        """
        last = series.last
        if last is None:
            return True
        limit = series.timeframe.ms * self._cfg.data.staleness_multiple
        return (as_of_ms - last.close_ms) > limit

    def _resolve_mark_price(
        self,
        series: list[TimeframeSeries],
        mark_price: Decimal | None,
        notes: list[str],
    ) -> Decimal:
        if mark_price is not None:
            return mark_price
        for candidate in series:  # build_requirements 가 TF 오름차순을 보장한다
            last = candidate.last
            if last is not None:
                notes.append(f"mark_price 출처: {candidate.timeframe.value} 마감 종가")
                return last.close
        raise DataIntegrityError("mark_price 를 정할 수 없다 — 어떤 타임프레임에도 마감봉이 없다")
