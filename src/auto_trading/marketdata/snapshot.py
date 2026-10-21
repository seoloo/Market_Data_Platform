"""저장된 봉에서 :class:`MarketSnapshot` 을 만드는 인과성 관문.

언제 쓰나: 백테스트(``backtest/feed.py``)가 판단 시각마다 "그때 알 수 있던 시장 상태" 를 만들 때.
읽고 쓰는 것: ``candles`` · ``funding_rates`` 표 (읽기만, 저장소를 거쳐서).

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
from auto_trading.features.a1 import RANGE_BARS, RANK_WINDOW_BARS, VOL_WINDOW_BARS
from auto_trading.features.registry import build_feature_set
from auto_trading.storage.repositories.candles import CandleRepository
from auto_trading.storage.repositories.funding import FundingRateRepository

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
    """[쓰임] 설정에서 타임프레임별로 확보해야 할 마감봉 수를 계산한다 (짧은 타임프레임부터).
    [이유] 같은 TF 를 여러 소비자가 쓰면 가장 깊은 요구치를 택한다. 각 소비자가 자기 warm-up 을
           선언하므로, feature 파라미터를 바꾸면 스냅샷 깊이도 따라 바뀐다 (손으로 적지 않는다)."""
    safety = cfg.data.warmup_safety_bars
    needs: dict[Timeframe, tuple[int, str]] = {}

    def require(timeframe: Timeframe, bars: int, reason: str) -> None:
        """[쓰임] 요구치 하나를 등록한다 — 이미 더 깊은 요구가 있으면 그것을 둔다 (여유 봉 포함)."""
        current = needs.get(timeframe)
        total = bars + safety
        if current is None or total > current[0]:
            needs[timeframe] = (total, reason)

    if cfg.strategy_id == "a1_v1":
        # 직전 30일 rank + 24시간 σ + 중복 차단에 필요한 2×6시간 선행 이력.
        require(
            Timeframe.M5,
            VOL_WINDOW_BARS + RANK_WINDOW_BARS + 2 * RANGE_BARS + 1,
            "a1.causal_breakout",
        )
    elif cfg.strategy_id == "wa_v1" and cfg.wa is not None:
        require(Timeframe.H1, cfg.wa.range_hours, "wa.range_and_volatility")
    else:
        for feature in build_feature_set(cfg.features).as_tuple():
            require(feature.timeframe, feature.warmup_bars, feature.name)

        stop = cfg.risk.stop
        # Wilder ATR 은 시드 영향이 남으므로 period 의 4배를 확보한다.
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
        TimeframeRequirement(timeframe=timeframe, bars=bars, reason=reason)
        for timeframe, (bars, reason) in sorted(needs.items(), key=lambda item: item[0].ms)
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
        funding: FundingRateRepository | None = None,
        requirements: tuple[TimeframeRequirement, ...] | None = None,
    ) -> None:
        self._repo = repo
        self._cfg = cfg
        self._funding = funding
        self._requirements = build_requirements(cfg) if requirements is None else requirements

    @property
    def requirements(self) -> tuple[TimeframeRequirement, ...]:
        """[쓰임] 이 빌더가 타임프레임마다 확보하려는 봉 수 목록."""
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
        """[쓰임] ``as_of_ms`` 시점에 알 수 있었던 시장 상태(마감봉 · funding · 기준 가격 · 품질
                  표시)를 만든다.
        [이유] 봉이 모자라거나 · 오래됐거나 · 빠진 곳이 있으면 채우지 않고 ``data_quality`` 에
               표시한다 — 진입은 상위에서 거부된다(fail-closed). ``mark_price`` 를 주지 않으면 가장
               짧은 TF 의 마지막 **마감** 종가를 쓰고, 그 사실을 ``data_quality.notes`` 에 남긴다 —
               실거래(거래소 mark price)와 값의 출처가 다르다는 점이 기록에 보여야 한다."""
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

        resolved_funding, resolved_next = self._resolve_funding(
            target, as_of_ms, funding_rate, next_funding_ms, notes
        )
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
            funding_rate=resolved_funding,
            next_funding_ms=resolved_next,
            spread_bps=spread_bps,
            data_quality=quality,
        )

    def _resolve_funding(
        self,
        symbol: str,
        as_of_ms: int,
        funding_rate: Decimal | None,
        next_funding_ms: int | None,
        notes: list[str],
    ) -> tuple[Decimal | None, int | None]:
        """[쓰임] 저장된 정산 내역에서 ``as_of_ms`` 의 funding 비율과 다음 정산 시각을 찾는다.
        [이유] 호출자가 값을 주면 그대로 쓴다 (실거래에서는 거래소가 실시간 값을 준다). 저장소가
               없거나 해당 시점 이전 정산이 없으면 **추정하지 않고 ``None``** 을 돌려준다 — 리스크
               게이트가 ``DATA_QUALITY`` 로 진입을 거부하도록 두는 것이 옳다 (AGENTS.md §3.6
               fail-closed). ``funding_ms <= as_of_ms`` 만 보므로 미래 정산이 새어들 수 없다."""
        if funding_rate is not None and next_funding_ms is not None:
            return funding_rate, next_funding_ms
        if self._funding is None:
            notes.append("funding 저장소 미연결 — 진입은 게이트에서 거부된다")
            return funding_rate, next_funding_ms
        latest = self._funding.latest_as_of(symbol, as_of_ms)
        if latest is None:
            notes.append(f"funding 이력 없음 (as_of={as_of_ms})")
            return funding_rate, next_funding_ms
        resolved_next = latest.next_funding_ms
        if resolved_next <= as_of_ms:
            # 다음 정산 시각이 이미 지났는데 그 행이 없다 = 적재 누락. 추정하지 않는다.
            notes.append(f"funding 이 오래됐다 (마지막 정산 {latest.funding_ms})")
            return funding_rate, next_funding_ms
        return (
            latest.rate if funding_rate is None else funding_rate,
            resolved_next if next_funding_ms is None else next_funding_ms,
        )

    def _is_stale(self, series: TimeframeSeries, as_of_ms: int) -> bool:
        """[쓰임] 마지막 마감봉이 너무 오래됐는지 (봉이 하나도 없어도 True).
        [이유] 정상이라면 마지막 마감봉의 나이는 봉 하나 길이 미만이다. 그보다
               ``staleness_multiple`` 배 넘게 벌어지면 피드가 끊긴 것으로 본다."""
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
        """[쓰임] 기준 가격을 정한다 — 주어진 값, 없으면 가장 짧은 TF 의 마지막 마감 종가.
        [이유] 어느 TF 에도 마감봉이 없으면 가격을 지어내지 않고 오류를 낸다."""
        if mark_price is not None:
            return mark_price
        for candidate in series:  # build_requirements 가 TF 오름차순을 보장한다
            last = candidate.last
            if last is not None:
                notes.append(f"mark_price 출처: {candidate.timeframe.value} 마감 종가")
                return last.close
        raise DataIntegrityError("mark_price 를 정할 수 없다 — 어떤 타임프레임에도 마감봉이 없다")
