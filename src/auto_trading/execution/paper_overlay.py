"""O/v1 Paper 실행기 — 하루 1회 (Phase 6 전향 관찰, docs/research/25).

T/v2 Paper(``paper_t2``)와 **원장을 분리**한다. T/v2 의 신호·설정·기록은 읽지도 쓰지도 않는다.

매 실행(00:00 UTC 이후):

1. 방금 마감된 일봉 ``d``의 close_ms를 ``decision_ms``로 둔다.
   이미 기록이 있으면 그대로 돌려준다(멱등).
2. 마지막 기록 다음 날부터 ``d``까지를 차례로 계산한다. 하루의 규칙은 연구 시뮬레이터와
   **같은 함수** :func:`~auto_trading.backtest.overlay.step_overlay_day` 다
   — 손익·funding·청산·계좌 정지·일 손실·체결.
3. 정상이면 ``d-1`` 종가로 정한 목표를 ``d`` 종가에 체결한다(연구의 1일 지연과 같다).
4. ``d`` 종가까지로 다음 목표를 정해 저장한다(다음 실행이 체결).

**실행이 빠진 날(PC 꺼짐 등)**: 그동안에도 포지션은 살아 있어 손익·funding·청산 위험이 생긴다.
그래서 빠진 날을 과거 시세로 **보유만 한 채** 계산해 ``MISSED``로 남긴다 — 거래는 하지 않는다
(그때 실제로 체결되지 않았으므로). 오늘도 오래된 목표는 체결하지 않고 ``NO_PRIOR_DECISION``으로
새 목표만 정한다(T/v2 와 같은 원칙). 빠진 날을 건너뛰면 funding·청산 판정이 빠져 성과가 부풀려진다.

**데이터가 모자라면 기록하지 않는다(fail-closed).** 일봉·funding 이 하나라도 비면 예외를 내고
아무 행도 쓰지 않는다 — 회계에 구멍을 남기지 않고, 다음 실행이 그날을 다시 계산한다.

체결가는 **마감 일봉 종가**다(연구와 비트 단위 일치, Phase 6 parity). 실행 시각의 현재가는
``observed``에 따로 남겨 종가 체결 가정과의 차이를 관찰한다.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Any, Protocol

from auto_trading.backtest.overlay import (
    OverlayBar,
    OverlayConfig,
    OverlayMarketDay,
    OverlayResult,
    OverlayState,
    next_targets,
    step_overlay_day,
)
from auto_trading.core.enums import Timeframe
from auto_trading.core.errors import DataIntegrityError
from auto_trading.core.types import Candle
from auto_trading.storage.repositories.paper_overlay_days import (
    OverlayPaperDay,
    OverlayPaperDayRepository,
)

__all__ = [
    "HISTORY_BARS",
    "SYMBOLS",
    "OverlayMarket",
    "PaperOverlayRunner",
    "state_from_json",
    "state_to_json",
    "verify_ledger",
]

_DAY_MS = Timeframe.D1.ms
SYMBOLS = ("BTCUSDT", "ETHUSDT")
HISTORY_BARS = 200
"""목표 계산에 받는 현물 일봉 수. T/v2 목표는 마지막 121개만 쓴다(120일 추세 + 60일 변동성)."""

_TALLY_FIELDS = (
    "spot_pnl",
    "futures_pnl",
    "funding",
    "costs",
    "trades",
    "liquidations",
    "halted_day",
    "loss_limit_days",
    "capped_days",
    "deleverage_days",
    "max_futures_leverage",
    "worst_intraday_drawdown",
)


class OverlayMarket(Protocol):
    def spot_daily(self, symbol: str, as_of_ms: int, limit: int) -> list[Candle]: ...

    def perp_daily(self, symbol: str, as_of_ms: int, limit: int) -> list[Candle]: ...

    def funding(self, symbol: str, start_ms: int, end_ms: int) -> list[tuple[int, float]]: ...

    def spot_price(self, symbol: str) -> Decimal: ...

    def perp_price(self, symbol: str) -> Decimal: ...


def state_to_json(state: OverlayState) -> dict[str, Any]:
    return asdict(state)


def state_from_json(data: Mapping[str, Any]) -> OverlayState:
    target = data["target"]
    return OverlayState(
        spot_qty={s: float(q) for s, q in data["spot_qty"].items()},
        fut_qty={s: float(q) for s, q in data["fut_qty"].items()},
        wallet=float(data["wallet"]),
        peak=float(data["peak"]),
        exposure=float(data["exposure"]),
        halted_day=None if data["halted_day"] is None else int(data["halted_day"]),
        target=None if target is None else {s: float(w) for s, w in target.items()},
    )


@dataclass(frozen=True, slots=True)
class _Series:
    """한 실행에 필요한 시장 자료 (open_ms → 봉)."""

    spot: dict[str, dict[int, OverlayBar]]
    perp: dict[str, dict[int, OverlayBar]]
    spot_closes: dict[str, list[tuple[int, float]]]
    funding: dict[str, dict[int, list[float]]]


def _bars(candles: Sequence[Candle]) -> dict[int, OverlayBar]:
    return {c.open_ms: OverlayBar(float(c.low), float(c.close)) for c in candles}


class PaperOverlayRunner:
    def __init__(
        self,
        market: OverlayMarket,
        days: OverlayPaperDayRepository,
        *,
        clock: Callable[[], int],
        cfg: OverlayConfig | None = None,
        symbols: Sequence[str] = SYMBOLS,
        initial_equity: float = 10_000.0,
        strategy_id: str = "o1_overlay",
        min_funding_per_day: int = 3,
        max_catchup_days: int = 30,
    ) -> None:
        self._market = market
        self._days = days
        self._clock = clock
        self._cfg = cfg if cfg is not None else OverlayConfig()
        self._symbols = tuple(sorted(symbols))
        self._initial = initial_equity
        self._strategy_id = strategy_id
        self._min_funding = min_funding_per_day
        self._max_catchup = max_catchup_days

    # ------------------------------------------------------------ 자료

    def _load(self, first_day: int, last_day: int) -> _Series:
        decision_ms = last_day + _DAY_MS - 1
        span = (last_day - first_day) // _DAY_MS + 2  # 첫날의 전날 종가까지
        spot: dict[str, dict[int, OverlayBar]] = {}
        perp: dict[str, dict[int, OverlayBar]] = {}
        closes: dict[str, list[tuple[int, float]]] = {}
        funding: dict[str, dict[int, list[float]]] = {}
        for s in self._symbols:
            spot_candles = self._market.spot_daily(s, decision_ms, max(HISTORY_BARS, span + 125))
            spot[s] = _bars(spot_candles)
            closes[s] = sorted((c.open_ms, float(c.close)) for c in spot_candles)
            perp[s] = _bars(self._market.perp_daily(s, decision_ms, span + 1))
            rows = self._market.funding(s, first_day, last_day + _DAY_MS - 1)
            by_day: dict[int, list[float]] = {}
            for ms, rate in rows:
                by_day.setdefault(ms - ms % _DAY_MS, []).append(rate)
            funding[s] = by_day
        series = _Series(spot, perp, closes, funding)
        self._validate(series, first_day, last_day)
        return series

    def _validate(self, series: _Series, first_day: int, last_day: int) -> None:
        day = first_day
        while day <= last_day:
            for s in self._symbols:
                for name, src in (("현물", series.spot), ("무기한", series.perp)):
                    for need in (day - _DAY_MS, day):
                        if need not in src[s]:
                            raise DataIntegrityError(f"{name} 일봉 결측: {s} open_ms={need}")
                got = len(series.funding[s].get(day, []))
                if got < self._min_funding:
                    raise DataIntegrityError(
                        f"funding 정산 {got}건 < {self._min_funding}: {s} open_ms={day}"
                    )
            day += _DAY_MS
        # 목표는 오늘만 계산한다. target_weight 가 쓰는 만큼(추세 창·변동성 창 중 긴 쪽 + 1)이
        # 없으면 목표가 0(현금)으로 조용히 바뀌므로 거부한다.
        trend = self._cfg.trend
        need = max(max(trend.lookbacks), trend.vol_days) + 1
        for s in self._symbols:
            history = [ms for ms, _ in series.spot_closes[s] if ms <= last_day]
            if len(history) < need or history[-1] != last_day:
                raise DataIntegrityError(f"목표 계산 이력 부족: {s} {len(history)} < {need}")
            # target_weight 는 위치로 과거를 찾는다. 중간에 빈 날이 있으면 "20일 전" 이 틀어진다.
            window = history[-need:]
            if window[-1] - window[0] != (need - 1) * _DAY_MS:
                raise DataIntegrityError(f"목표 계산 이력에 빈 날이 있다: {s}")

    # ------------------------------------------------------------ 실행

    def run_once(self, now_ms: int) -> list[OverlayPaperDay]:
        """오늘까지 계산하고 이번에 쓴 행들을 돌려준다. 이미 했으면 오늘 행 하나를 돌려준다."""
        decision_ms = now_ms - now_ms % _DAY_MS - 1
        today = decision_ms + 1 - _DAY_MS
        done = self._days.get(self._strategy_id, decision_ms)
        if done is not None:
            return [done]
        last = self._days.latest(self._strategy_id)
        if last is None:
            state = OverlayState.initial(self._symbols, self._initial, self._cfg)
            first_day = today
            fresh = True
        else:
            last_day = last.decision_ms + 1 - _DAY_MS
            if last_day >= today:
                raise DataIntegrityError(
                    f"마지막 기록({last.decision_ms})이 오늘({decision_ms})보다 늦다 — 시계 확인"
                )
            state = state_from_json(last.state)
            first_day = last_day + _DAY_MS
            fresh = False
        missed = (today - first_day) // _DAY_MS
        if missed > self._max_catchup:
            raise DataIntegrityError(
                f"{missed}일이 빠졌다 (한도 {self._max_catchup}일)"
                " — 자동 보정하지 않는다. 수동 확인 필요"
            )
        if missed:
            state.target = None  # 오래된 목표는 체결하지 않는다
        series = self._load(first_day, today)
        observed = {
            "spot": {s: str(self._market.spot_price(s)) for s in self._symbols},
            "perp": {s: str(self._market.perp_price(s)) for s in self._symbols},
            "at_ms": self._clock(),
        }
        rows: list[OverlayPaperDay] = []
        day = first_day
        while day <= today:
            is_today = day == today
            rows.append(self._step(state, day, series, is_today, fresh, missed, observed))
            day += _DAY_MS
        self._days.insert_all(rows)
        return rows

    def _step(
        self,
        state: OverlayState,
        day: int,
        series: _Series,
        is_today: bool,
        fresh: bool,
        missed: int,
        observed: dict[str, Any],
    ) -> OverlayPaperDay:
        prev = day - _DAY_MS
        rates = {s: list(series.funding[s].get(day, [])) for s in self._symbols}
        market = OverlayMarketDay(
            spot_prev_close={s: series.spot[s][prev].close for s in self._symbols},
            spot={s: series.spot[s][day] for s in self._symbols},
            perp_prev_close={s: series.perp[s][prev].close for s in self._symbols},
            perp={s: series.perp[s][day] for s in self._symbols},
            funding_rates=rates,
        )
        executed_target = state.target
        tally = OverlayResult()
        outcome = step_overlay_day(state, day, market, self._cfg, tally)
        if is_today:
            history = {
                s: [c for ms, c in series.spot_closes[s] if ms <= day] for s in self._symbols
            }
            state.target = next_targets(history, self._cfg)
        else:
            state.target = None  # 빠진 날의 목표는 그때 체결되지 않았다
        if not is_today:
            status, note = "MISSED", "실행 누락 — 과거 시세로 보유만 계산, 거래 없음"
        elif fresh:
            status, note = "NO_PRIOR_DECISION", "첫 실행 — 체결할 목표 없음"
        elif missed:
            status, note = "NO_PRIOR_DECISION", f"{missed}일 누락 뒤 — 오래된 목표는 체결하지 않음"
        else:
            status, note = "OK", None
        market_json = {
            "spot_prev_close": dict(market.spot_prev_close),
            "spot": {s: [b.low, b.close] for s, b in market.spot.items()},
            "perp_prev_close": dict(market.perp_prev_close),
            "perp": {s: [b.low, b.close] for s, b in market.perp.items()},
            "funding_rates": rates,
            "executed_target": executed_target,
        }
        tally_json = {name: getattr(tally, name) for name in _TALLY_FIELDS}
        tally_json["start_equity"] = outcome.start_equity
        tally_json["exposure"] = outcome.exposure
        return OverlayPaperDay(
            strategy_id=self._strategy_id,
            decision_ms=day + _DAY_MS - 1,
            status=status,
            equity=outcome.equity,
            state=state_to_json(state),
            tally=tally_json,
            market=market_json,
            fills=[dict(f) for f in outcome.fills],
            observed=observed if is_today else {},
            note=note,
            created_ms=self._clock(),
        )


def verify_ledger(
    rows: Sequence[OverlayPaperDay],
    *,
    cfg: OverlayConfig | None = None,
    symbols: Sequence[str] = SYMBOLS,
    initial_equity: float = 10_000.0,
) -> list[str]:
    """저장된 원장을 첫날부터 다시 계산해 어긋나는 점을 돌려준다. 빈 목록이면 이상 없음.

    각 행에 남긴 시장 입력(``market``)과 체결한 목표만으로 다음 행의 상태가 **정확히**
    나와야 한다. 날짜가 비었거나, 행이 손으로 고쳐졌거나, 코드가 바뀌어 규칙이 달라졌으면 잡힌다.
    """
    cfg = cfg if cfg is not None else OverlayConfig()
    problems: list[str] = []
    if not rows:
        return problems
    names = tuple(sorted(symbols))
    state = OverlayState.initial(names, initial_equity, cfg)
    previous_ms: int | None = None
    for row in rows:
        day = row.decision_ms + 1 - _DAY_MS
        if previous_ms is not None and row.decision_ms - previous_ms != _DAY_MS:
            problems.append(f"{row.decision_ms}: 앞 행과 {row.decision_ms - previous_ms}ms 간격")
        previous_ms = row.decision_ms
        m = row.market
        raw = m["executed_target"]
        target = None if raw is None else {s: float(w) for s, w in raw.items()}
        # OK 날은 전날 저장한 목표를 그대로 체결해야 하고, 그 밖의 날은 아무것도 체결하지 않는다.
        expected = state.target if row.status == "OK" else None
        if target != expected:
            problems.append(f"{row.decision_ms}: 체결한 목표가 전날 저장한 목표와 다르다")
        state.target = target
        market = OverlayMarketDay(
            spot_prev_close={s: float(v) for s, v in m["spot_prev_close"].items()},
            spot={s: OverlayBar(float(lo), float(c)) for s, (lo, c) in m["spot"].items()},
            perp_prev_close={s: float(v) for s, v in m["perp_prev_close"].items()},
            perp={s: OverlayBar(float(lo), float(c)) for s, (lo, c) in m["perp"].items()},
            funding_rates={s: [float(r) for r in v] for s, v in m["funding_rates"].items()},
        )
        outcome = step_overlay_day(state, day, market, cfg, OverlayResult())
        if outcome.equity != row.equity:
            problems.append(f"{row.decision_ms}: 자본 {row.equity!r} ≠ 재계산 {outcome.equity!r}")
        stored = state_from_json(row.state)
        for field_name in ("spot_qty", "fut_qty", "wallet", "peak", "exposure", "halted_day"):
            if getattr(stored, field_name) != getattr(state, field_name):
                problems.append(f"{row.decision_ms}: {field_name} 불일치")
        state = stored  # 다음 날은 저장된 목표로 이어간다
    return problems
