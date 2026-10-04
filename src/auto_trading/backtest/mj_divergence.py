"""MJ-B · MJ-C — RSI 다이버전스 1시간봉 + MJ 자금 관리 (docs/research/140). 순수 함수, I/O 없음.

봉마다 (종목 하나, 한 번에 한 포지션):

1. 시가: 예약된 지정가 진입(롱은 시가 ≤ 지정가면 시가, 아니면 저가 ≤ 지정가면 지정가,
   둘 다 아니면 놓침). 손절 폭이 ``min_stop`` 미만이면 건너뛴다.
2. 봉 안: 지금 걸린 손절(또는 청산가)에 닿으면 남은 물량을 ``min(손절, 시가)`` 에 판다 —
   같은 봉에서 익절과 손절이 둘 다 닿으면 손절이 먼저. 손절에 안 닿았으면 익절 지정가를
   낮은 것부터 채운다(시가가 이미 넘었으면 시가). 진입 봉은 시가에 채워졌을 때만 익절을 본다.
   1.5R 익절 뒤 본전 손절은 **다음 봉부터** 걸린다.
3. 종가: 다이버전스가 확정되고 포지션이 없으면 다음 봉 지정가 진입을 예약한다.

결과는 거래마다 R 배수 = (비용 뒤 손익) ÷ (진입 때 위험 금액). 크기는 R 단위라 자본과 무관하다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from auto_trading.backtest.ema_box import Bars, atr
from auto_trading.backtest.indicator_signals import rsi

__all__ = [
    "M0",
    "M1",
    "Boxes",
    "Management",
    "Signal",
    "SignalConfig",
    "Trade",
    "TradeRun",
    "boxes",
    "confluent",
    "pivots",
    "signals",
    "simulate",
]


@dataclass(frozen=True, slots=True)
class SignalConfig:
    left: int = 5
    right: int = 2
    min_gap: int = 5
    max_gap: int = 60
    rsi_len: int = 14
    oversold: float = 30.0
    overbought: float = 70.0
    atr_len: int = 14
    box_bars: int = 12
    box_atr: float = 1.5
    zone_atr: float = 0.5
    zone_from: int = 24
    zone_to: int = 500


@dataclass(frozen=True, slots=True)
class Management:
    """익절 단계 ``(R 배수, 물량 비율)`` 과 첫 익절 뒤 본전 손절 여부."""

    targets: tuple[tuple[float, float], ...]
    breakeven: bool


M0 = Management(targets=((1.5, 1.0),), breakeven=False)
M1 = Management(targets=((1.5, 0.5), (3.0, 0.4), (5.0, 0.1)), breakeven=True)


@dataclass(frozen=True, slots=True)
class Signal:
    i: int
    """확정 봉(피벗 + right) 인덱스 — 이 봉 종가가 지정가."""
    side: int
    """+1 롱(상승 다이버전스), -1 숏(하락 다이버전스)."""
    pivot: int
    prev: int
    stop: float


@dataclass(frozen=True, slots=True)
class Trade:
    side: int
    signal_i: int
    entry_i: int
    exit_i: int
    entry: float
    stop: float
    r: float
    exit_kind: str
    """``stop`` · ``breakeven`` · ``target`` · ``liquidation`` · ``end``."""


@dataclass(slots=True)
class TradeRun:
    trades: list[Trade] = field(default_factory=list)
    signals: int = 0
    busy: int = 0
    """포지션을 들고 있어 버린 신호."""
    missed: int = 0
    skipped: int = 0
    """손절 폭이 너무 좁아 건너뛴 진입."""


# ------------------------------------------------------------------ 신호


def pivots(b: Bars, cfg: SignalConfig) -> tuple[list[bool], list[bool]]:
    """(저점, 고점). 봉 p 는 앞 ``left`` 봉보다 엄격히 낮고 뒤 ``right`` 봉 이하일 때 저점."""
    n = len(b.c)
    lows = [False] * n
    highs = [False] * n
    for p in range(cfg.left, n - cfg.right):
        before = range(p - cfg.left, p)
        after = range(p + 1, p + cfg.right + 1)
        lp, hp = b.low[p], b.h[p]
        if all(lp < b.low[k] for k in before) and all(lp <= b.low[k] for k in after):
            lows[p] = True
        if all(hp > b.h[k] for k in before) and all(hp >= b.h[k] for k in after):
            highs[p] = True
    return lows, highs


def signals(b: Bars, cfg: SignalConfig) -> list[Signal]:
    """정규 다이버전스. 직전 같은 종류 피벗 q 와 비교하고, 확정 봉 종가로 반등을 확인한다."""
    r = rsi(b.c, cfg.rsi_len)
    lows, highs = pivots(b, cfg)
    out: list[Signal] = []
    last_low: int | None = None
    last_high: int | None = None
    for p in range(len(b.c) - cfg.right):
        i = p + cfg.right
        if lows[p]:
            q = last_low
            last_low = p
            rp, rq = r[p], r[q] if q is not None else None
            if (
                q is not None
                and rp is not None
                and rq is not None
                and cfg.min_gap <= p - q <= cfg.max_gap
                and b.low[p] < b.low[q]
                and rp > rq
                and min(rp, rq) <= cfg.oversold
                and b.c[i] > b.c[p]
            ):
                out.append(Signal(i, 1, p, q, b.low[p]))
        if highs[p]:
            q = last_high
            last_high = p
            rp, rq = r[p], r[q] if q is not None else None
            if (
                q is not None
                and rp is not None
                and rq is not None
                and cfg.min_gap <= p - q <= cfg.max_gap
                and b.h[p] > b.h[q]
                and rp < rq
                and max(rp, rq) >= cfg.overbought
                and b.c[i] < b.c[p]
            ):
                out.append(Signal(i, -1, p, q, b.h[p]))
    out.sort(key=lambda s: (s.i, -s.side))
    return out


@dataclass(frozen=True, slots=True)
class Boxes:
    end: list[int]
    lo: list[float]
    hi: list[float]
    pad: list[float]
    """``zone_atr × ATR(박스 끝 봉)``."""


def boxes(b: Bars, cfg: SignalConfig) -> Boxes:
    """연속 ``box_bars`` 봉의 고저 폭 ≤ ``box_atr × ATR(끝 봉)`` 인 횡보 박스(끝 봉에 확정)."""
    a = atr(b, cfg.atr_len)
    out = Boxes([], [], [], [])
    n = cfg.box_bars
    for e in range(n - 1, len(b.c)):
        ae = a[e]
        if ae is None:
            continue
        hi = max(b.h[k] for k in range(e - n + 1, e + 1))
        lo = min(b.low[k] for k in range(e - n + 1, e + 1))
        if hi - lo <= cfg.box_atr * ae:
            out.end.append(e)
            out.lo.append(lo)
            out.hi.append(hi)
            out.pad.append(cfg.zone_atr * ae)
    return out


def confluent(sig: Signal, z: Boxes, cfg: SignalConfig) -> bool:
    """피벗 가격이 피벗보다 ``zone_from ~ zone_to`` 봉 앞에 확정된 박스(± 여유) 안에 있는가."""
    import bisect

    first = bisect.bisect_left(z.end, sig.pivot - cfg.zone_to)
    last = bisect.bisect_right(z.end, sig.pivot - cfg.zone_from)
    price = sig.stop
    return any(z.lo[k] - z.pad[k] <= price <= z.hi[k] + z.pad[k] for k in range(first, last))


# ------------------------------------------------------------------ 매매


@dataclass(slots=True)
class _Pos:
    side: int
    signal_i: int
    entry_i: int
    entry: float
    stop: float
    risk: float
    """R 단위 크기: 물량 1 = 위험 금액 1 이 되도록 qty = 1 / |진입 − 손절|."""
    qty: float
    level: float
    """지금 걸린 손절(또는 청산) 가격."""
    level_kind: str
    level_fee: float
    pnl: float = 0.0
    done: int = 0
    """채운 익절 단계 수."""
    pending_be: bool = False


def _price_gap(side: int, a: float, b: float) -> float:
    return (a - b) * side


def simulate(
    b: Bars,
    sigs: Sequence[Signal],
    mgmt: Management,
    *,
    from_ms: int,
    to_ms: int,
    fee: float = 0.00095,
    min_stop: float = 0.0025,
    liq_mmr: float | None = None,
    liq_fee: float = 0.005,
) -> TradeRun:
    """``sigs`` (확정 봉 순) 를 지정가로 들어가 ``mgmt`` 로 관리한다.

    ``liq_mmr`` 가 있으면 계산기식 격리 증거금(증거금 = 위험 금액)을 흉내 낸다: 첫 익절 전에는
    손절 대신 청산가 ``손절 + side × liq_mmr × 진입가`` 에서 ``liq_fee`` 를 내고 나간다
    (손실은 증거금 = 1R 까지)."""
    run = TradeRun()
    by_i: dict[int, Signal] = {}
    for s in sigs:
        if from_ms <= b.t[s.i] <= to_ms and s.i not in by_i:
            by_i[s.i] = s
    pending: Signal | None = None
    pos: _Pos | None = None
    end = to_ms

    def leg(p: _Pos, qty: float, price: float, rate: float) -> None:
        gross = _price_gap(p.side, price, p.entry) * qty
        p.pnl += gross - qty * price * rate
        p.qty -= qty

    def close(p: _Pos, i: int, kind: str) -> None:
        run.trades.append(Trade(p.side, p.signal_i, p.entry_i, i, p.entry, p.stop, p.pnl, kind))

    last = -1
    for i in range(len(b.t)):
        t = b.t[i]
        if t > end:
            break
        last = i
        op, hi, lo = b.o[i], b.h[i], b.low[i]
        filled_at_open = False
        if pending is not None:
            s, pending = pending, None
            limit = b.c[s.i]
            if s.side > 0:
                fill = op if op <= limit else (limit if lo <= limit else None)
            else:
                fill = op if op >= limit else (limit if hi >= limit else None)
            if fill is None:
                run.missed += 1
            elif _price_gap(s.side, fill, s.stop) < min_stop * fill:
                run.skipped += 1
            else:
                dist = _price_gap(s.side, fill, s.stop)
                qty = 1.0 / dist
                level, kind, lfee = s.stop, "stop", fee
                if liq_mmr is not None:
                    level, kind, lfee = s.stop + s.side * liq_mmr * fill, "liquidation", liq_fee
                pos = _Pos(s.side, s.i, i, fill, s.stop, 1.0, qty, level, kind, lfee)
                pos.pnl -= qty * fill * fee
                filled_at_open = fill == op
        if pos is not None:
            p = pos
            if p.pending_be:
                p.level, p.level_kind, p.level_fee = p.entry, "breakeven", fee
                p.pending_be = False
            hit = lo <= p.level if p.side > 0 else hi >= p.level
            if hit:
                px = min(p.level, op) if p.side > 0 else max(p.level, op)
                before = p.pnl
                leg(p, p.qty, px, p.level_fee)
                if p.level_kind == "liquidation":
                    p.pnl = max(p.pnl, before - p.risk)
                close(p, i, p.level_kind)
                pos = None
            elif i > p.entry_i or filled_at_open:
                full = 1.0 / abs(p.entry - p.stop)
                while p.done < len(mgmt.targets):
                    mult, frac = mgmt.targets[p.done]
                    tp = p.entry + p.side * mult * abs(p.entry - p.stop)
                    reached = hi >= tp if p.side > 0 else lo <= tp
                    if not reached:
                        break
                    px = max(tp, op) if p.side > 0 else min(tp, op)
                    qty = min(full * frac, p.qty)
                    leg(p, qty, px, fee)
                    p.done += 1
                    if p.done == 1 and mgmt.breakeven:
                        p.pending_be = True
                if p.qty <= full * 1e-9:
                    close(p, i, "target")
                    pos = None
        if t >= from_ms and i in by_i:
            run.signals += 1
            if pos is not None:
                run.busy += 1
            else:
                pending = by_i[i]
    if pos is not None and last >= 0:
        leg(pos, pos.qty, b.c[last], fee)
        close(pos, last, "end")
    return run
