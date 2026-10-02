"""Elliott 2파 되돌림 → 3파 진입 셋업의 과거 거래 재현 (문서 95). 순수 함수, I/O 없음.

셋업(문서 56·94: 진입 B − 0.5(B − A) · 손절 A · 목표 진입 + 1.618|B − A|)을 1m 경로로 따라간다.
한 번에 하나: 예약 중 새 셋업이 오면 교체, 보유 중 새 셋업은 무시(데모 56 과 같다).

같은 1 분 안의 순서는 모르므로 **항상 불리하게** 본다:

* 예약 중 같은 분에 진입가와 B 넘김(충격파 연장)이 함께 → 취소(거래 없음).
* 체결된 분에 손절가까지 닿으면 → 손절. 체결된 분의 목표 도달은 세지 않는다.
* 같은 분에 손절·목표가 함께 → 손절.
* 지정가 체결은 진입가를 **넘어서야**(같으면 아님).
* 손절은 닿으면 손절가, 분이 이미 손절 너머에서 열리면 그 시가.
* 목표는 닿으면 목표가(유리한 갭은 쓰지 않음).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

__all__ = ["Bar", "Counts", "Rules", "Setup", "Trade", "cluster_trades", "simulate"]


@dataclass(frozen=True, slots=True)
class Setup:
    as_of_ms: int
    """셋업이 알려진 시각(그 봉의 마감). 이 시각 뒤에 열린 분부터 예약이 살아 있다."""
    direction: int
    entry: float
    stop: float
    target: float
    b: float
    """충격파 끝 — 체결 전에 넘으면 예약 취소."""
    skipped: bool = False
    """셋업 순간 이미 진입가를 지남 — 들지 않는다."""


@dataclass(frozen=True, slots=True)
class Bar:
    open_ms: int
    open: float
    high: float
    low: float
    close: float


@dataclass(frozen=True, slots=True)
class Rules:
    valid_minutes: int = 60 * 60
    """예약 유효 — 데모 4h 60 봉(10 일)을 1h degree 봉 수로 (60 시간)."""
    hold_minutes: int = 180 * 60
    """최대 보유 — 데모 30 일(4h 180 봉)을 1h degree 봉 수로 (180 시간)."""
    cost_side: float = 0.00095
    """편도 비용(명목 대비). 진입·청산 두 번."""


@dataclass(frozen=True, slots=True)
class Trade:
    setup: Setup
    fill_ms: int
    exit_ms: int
    exit_price: float
    reason: str
    """TARGET · STOP · TIME · END(자료 끝)."""
    r_gross: float
    r_net: float


@dataclass(slots=True)
class Counts:
    setups: int = 0
    skipped: int = 0
    replaced: int = 0
    ignored_in_position: int = 0
    cancelled_extension: int = 0
    expired: int = 0
    filled: int = 0
    reasons: dict[str, int] = field(default_factory=dict)


def _close(s: Setup, fill_ms: int, exit_ms: int, price: float, reason: str, rules: Rules) -> Trade:
    risk = abs(s.entry - s.stop)
    gross = s.direction * (price - s.entry) / risk
    cost = rules.cost_side * (s.entry + price) / risk
    return Trade(s, fill_ms, exit_ms, price, reason, gross, gross - cost)


def _exit_in(s: Setup, bar: Bar) -> tuple[float, str] | None:
    """이 분에 손절·목표가 났는가 (손절 우선)."""
    d = s.direction
    if (bar.low if d > 0 else -bar.high) <= (s.stop if d > 0 else -s.stop):
        gapped = (bar.open - s.stop) * d <= 0
        return (bar.open if gapped else s.stop), "STOP"
    if (bar.high if d > 0 else -bar.low) >= (s.target if d > 0 else -s.target):
        return s.target, "TARGET"
    return None


def simulate(
    setups: Sequence[Setup], bars: Iterable[Bar], rules: Rules | None = None
) -> tuple[list[Trade], Counts]:
    rules = rules or Rules()
    order = sorted(setups, key=lambda s: s.as_of_ms)
    counts = Counts()
    trades: list[Trade] = []
    k = 0
    pending: Setup | None = None
    expire_ms = 0
    position: tuple[Setup, int] | None = None
    last: Bar | None = None

    def finish(trade: Trade) -> None:
        trades.append(trade)
        counts.reasons[trade.reason] = counts.reasons.get(trade.reason, 0) + 1

    for bar in bars:
        last = bar
        while k < len(order) and order[k].as_of_ms < bar.open_ms:
            s = order[k]
            k += 1
            counts.setups += 1
            if s.skipped:
                counts.skipped += 1
            elif position is not None:
                counts.ignored_in_position += 1
            else:
                if pending is not None:
                    counts.replaced += 1
                pending, expire_ms = s, bar.open_ms + rules.valid_minutes * 60_000
        if position is not None:
            s, fill_ms = position
            if bar.open_ms >= fill_ms + rules.hold_minutes * 60_000:
                finish(_close(s, fill_ms, bar.open_ms, bar.open, "TIME", rules))
                position = None
            else:
                hit = _exit_in(s, bar)
                if hit is not None:
                    finish(_close(s, fill_ms, bar.open_ms, hit[0], hit[1], rules))
                    position = None
            continue
        if pending is None:
            continue
        if bar.open_ms >= expire_ms:
            counts.expired += 1
            pending = None
            continue
        s, d = pending, pending.direction
        extended = bar.high > s.b if d > 0 else bar.low < s.b
        filled = bar.low < s.entry if d > 0 else bar.high > s.entry
        if extended:
            counts.cancelled_extension += 1
            pending = None
            continue
        if filled:
            counts.filled += 1
            pending = None
            stop_hit = (bar.low <= s.stop) if d > 0 else (bar.high >= s.stop)
            if stop_hit:
                finish(_close(s, bar.open_ms, bar.open_ms, s.stop, "STOP", rules))
            else:
                position = (s, bar.open_ms)
    if position is not None and last is not None:
        s, fill_ms = position
        finish(_close(s, fill_ms, last.open_ms, last.close, "END", rules))
    if pending is not None:
        counts.expired += 1
    counts.setups += len(order) - k
    return trades, counts


def cluster_trades(trades: Sequence[Trade]) -> list[list[Trade]]:
    """보유 구간 [체결, 청산] 이 겹치는 거래를 자산과 상관없이 한 묶음으로 (문서 96 §4).

    겹침이 이어지면 계속 합친다. 같은 시기 같은 시장의 거래를 독립 표본으로 세지 않기 위해서다.
    """
    clusters: list[list[Trade]] = []
    end = -1
    for t in sorted(trades, key=lambda x: (x.fill_ms, x.exit_ms)):
        if clusters and t.fill_ms <= end:
            clusters[-1].append(t)
            end = max(end, t.exit_ms)
        else:
            clusters.append([t])
            end = t.exit_ms
    return clusters
