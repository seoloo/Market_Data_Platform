"""KB — EMA 10·20·50 정배열 + 박스 돌파, 롱만 (docs/research/139). 순수 함수, I/O 없음.

봉(일봉·4시간봉)마다:

1. 시가: 예약된 청산(절반·전량)을 시가에 → 예약된 지정가 진입
   (시가 ≤ 지정가면 시가, 아니면 저가 ≤ 지정가면 지정가, 둘 다 아니면 놓침).
2. 봉 안: 저가가 묶음의 손절가에 닿으면 그 묶음을 ``min(손절가, 시가)`` 에 판다
   (진입한 봉 포함, 보수적).
3. 종가: EMA10·EMA20 아래 연속 봉 수로 다음 봉 시가의 절반·전량 청산을 예약하고,
   새 신호면 다음 봉 진입을 예약한다.

크기는 진입마다 몫 자본의 1% 위험, 보유 합 ≤ 몫 자본 100%. 모든 주문은 지정가로 본다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

__all__ = [
    "Bars",
    "BoxConfig",
    "BoxResult",
    "aggregate",
    "atr",
    "ema",
    "hold_alignment",
    "signal_at",
    "simulate_box",
]

DAY_MS = 86_400_000


@dataclass(frozen=True, slots=True)
class Bars:
    t: Sequence[int]
    o: Sequence[float]
    h: Sequence[float]
    low: Sequence[float]
    c: Sequence[float]
    v: Sequence[float]


@dataclass(frozen=True, slots=True)
class BoxConfig:
    fast: int = 10
    mid: int = 20
    slow: int = 50
    atr_len: int = 14
    box_bars: int = 10
    box_atr: float = 3.0
    near_atr: float = 1.0
    warmup: int = 200
    entry_slack: float = 0.005
    risk: float = 0.01
    min_stop: float = 0.005
    fee: float = 0.0015


@dataclass(slots=True)
class BoxResult:
    days: list[int] = field(default_factory=list)
    equity: list[float] = field(default_factory=list)
    exposure: list[float] = field(default_factory=list)
    signals: int = 0
    entries: int = 0
    adds: int = 0
    missed: int = 0
    stops: int = 0
    half_exits: int = 0
    full_exits: int = 0
    fees: float = 0.0
    r_multiples: list[float] = field(default_factory=list)
    """묶음마다 (비용 뒤 손익) ÷ (진입 때 위험 금액)."""


def aggregate(
    t: Sequence[int],
    o: Sequence[float],
    h: Sequence[float],
    low: Sequence[float],
    c: Sequence[float],
    v: Sequence[float],
    step_ms: int,
) -> Bars:
    """1분봉을 UTC ``step_ms`` 격자로 묶는다."""
    bt: list[int] = []
    bo: list[float] = []
    bh: list[float] = []
    bl: list[float] = []
    bc: list[float] = []
    bv: list[float] = []
    for i in range(len(t)):
        key = t[i] - t[i] % step_ms
        if bt and bt[-1] == key:
            bh[-1] = max(bh[-1], h[i])
            bl[-1] = min(bl[-1], low[i])
            bc[-1] = c[i]
            bv[-1] += v[i]
        else:
            bt.append(key)
            bo.append(o[i])
            bh.append(h[i])
            bl.append(low[i])
            bc.append(c[i])
            bv.append(v[i])
    return Bars(bt, bo, bh, bl, bc, bv)


def ema(values: Sequence[float], n: int) -> list[float]:
    a = 2 / (n + 1)
    out: list[float] = []
    for x in values:
        out.append(x if not out else out[-1] + a * (x - out[-1]))
    return out


def atr(b: Bars, n: int) -> list[float | None]:
    """Wilder ATR. 앞 ``n`` 봉은 None."""
    tr: list[float] = []
    for i in range(len(b.c)):
        if i == 0:
            tr.append(b.h[i] - b.low[i])
        else:
            prev = b.c[i - 1]
            tr.append(max(b.h[i] - b.low[i], abs(b.h[i] - prev), abs(b.low[i] - prev)))
    out: list[float | None] = [None] * len(tr)
    if len(tr) < n:
        return out
    val = sum(tr[:n]) / n
    out[n - 1] = val
    for i in range(n, len(tr)):
        val = (val * (n - 1) + tr[i]) / n
        out[i] = val
    return out


@dataclass(frozen=True, slots=True)
class _Ind:
    e1: list[float]
    e2: list[float]
    e3: list[float]
    a: list[float | None]


def _indicators(b: Bars, cfg: BoxConfig) -> _Ind:
    return _Ind(ema(b.c, cfg.fast), ema(b.c, cfg.mid), ema(b.c, cfg.slow), atr(b, cfg.atr_len))


def signal_at(b: Bars, ind: _Ind, i: int, cfg: BoxConfig) -> float | None:
    """봉 i 종가에 매수 신호면 손절가(박스 최저가), 아니면 None."""
    n = cfg.box_bars
    if i < max(cfg.warmup, n + 1):
        return None
    p = i - 1
    a_prev = ind.a[p]
    if a_prev is None:
        return None
    if not (ind.e1[p] > ind.e2[p] > ind.e3[p] and b.c[p] > ind.e1[p]):
        return None
    box = range(i - n, i)
    hi = max(b.h[k] for k in box)
    lo = min(b.low[k] for k in box)
    if hi - lo > cfg.box_atr * a_prev:
        return None
    for k in box:
        ak = ind.a[k]
        if ak is None or abs(b.c[k] - ind.e1[k]) > cfg.near_atr * ak or b.low[k] <= ind.e3[k]:
            return None
    if b.c[i] <= hi:
        return None
    if b.v[i] <= sum(b.v[k] for k in box) / n:
        return None
    return lo


@dataclass(slots=True)
class _Lot:
    qty: float
    stop: float
    risk_amt: float
    basis: float
    pnl: float = 0.0


def _mark(res: BoxResult, day: int, equity: float, value: float) -> None:
    if res.days and res.days[-1] == day:
        res.equity[-1] = equity
        res.exposure[-1] = value / equity if equity > 0 else 0.0
    else:
        res.days.append(day)
        res.equity.append(equity)
        res.exposure.append(value / equity if equity > 0 else 0.0)


def _fill_days(res: BoxResult, from_ms: int, to_ms: int, start: float) -> BoxResult:
    by = dict(zip(res.days, zip(res.equity, res.exposure, strict=True), strict=True))
    days, eq, ex = [], [], []
    last = (start, 0.0)
    for d in range(from_ms - from_ms % DAY_MS, to_ms - to_ms % DAY_MS + 1, DAY_MS):
        last = by.get(d, last)
        days.append(d)
        eq.append(last[0])
        ex.append(last[1])
    res.days, res.equity, res.exposure = days, eq, ex
    return res


def simulate_box(b: Bars, *, from_ms: int, to_ms: int, initial: float, cfg: BoxConfig) -> BoxResult:
    ind = _indicators(b, cfg)
    res = BoxResult()
    cash = initial
    lots: list[_Lot] = []
    pending: tuple[float, float] | None = None  # (지정가, 손절가)
    want_half = want_full = False
    half_taken = False
    below1 = below2 = 0
    end = to_ms + DAY_MS

    def held() -> float:
        return sum(lot.qty for lot in lots)

    def sell(lot: _Lot, qty: float, price: float) -> None:
        nonlocal cash
        gross = qty * price
        f = gross * cfg.fee
        res.fees += f
        cash += gross - f
        part = lot.basis * qty / lot.qty
        lot.pnl += gross - f - part
        lot.basis -= part
        lot.qty -= qty

    def close_lot(lot: _Lot) -> None:
        res.r_multiples.append(lot.pnl / lot.risk_amt if lot.risk_amt > 0 else 0.0)

    for i in range(len(b.t)):
        t = b.t[i]
        if t >= end:
            break
        op, lo, cl = b.o[i], b.low[i], b.c[i]
        active = t >= from_ms
        if active:
            # 1. 시가 — 청산 먼저, 그다음 진입
            if lots and want_full:
                for lot in lots:
                    sell(lot, lot.qty, op)
                    close_lot(lot)
                lots = []
                res.full_exits += 1
            elif lots and want_half:
                for lot in lots:
                    sell(lot, lot.qty / 2, op)
                res.half_exits += 1
            want_half = want_full = False
            if pending is not None:
                limit, stop = pending
                fill = op if op <= limit else (limit if lo <= limit else None)
                if fill is None:
                    res.missed += 1
                elif fill > stop:
                    equity = cash + held() * op
                    dist = max(fill - stop, cfg.min_stop * fill)
                    qty = cfg.risk * equity / dist
                    room = (equity - held() * fill) / (fill * (1 + cfg.fee))
                    qty = min(qty, room, cash / (fill * (1 + cfg.fee)))
                    if qty > 0:
                        cost = qty * fill
                        f = cost * cfg.fee
                        cash -= cost + f
                        res.fees += f
                        if lots:
                            res.adds += 1
                        else:
                            res.entries += 1
                            half_taken = False
                        lots.append(_Lot(qty, stop, qty * dist, cost + f))
            pending = None
            # 2. 봉 안 손절
            keep: list[_Lot] = []
            for lot in lots:
                if lo <= lot.stop:
                    sell(lot, lot.qty, min(lot.stop, op))
                    close_lot(lot)
                    res.stops += 1
                else:
                    keep.append(lot)
            lots = keep
            if not lots:
                half_taken = False
        # 3. 종가 — 연속 봉 수·청산 예약·신호
        below1 = below1 + 1 if cl < ind.e1[i] else 0
        below2 = below2 + 1 if cl < ind.e2[i] else 0
        if active and lots:
            if below2 >= 2:
                want_full = True
            elif below1 >= 2 and not half_taken:
                want_half = True
                half_taken = True
        stop_px = signal_at(b, ind, i, cfg)
        if stop_px is not None and active:
            res.signals += 1
            pending = (cl * (1 + cfg.entry_slack), stop_px)
        if active:
            value = held() * cl
            _mark(res, t - t % DAY_MS, cash + value, value)
    return _fill_days(res, from_ms, to_ms, initial)


def hold_alignment(
    b: Bars,
    *,
    from_ms: int,
    to_ms: int,
    initial: float,
    cfg: BoxConfig,
    always: bool = False,
) -> BoxResult:
    """AH: 종가에 정배열(EMA10 > EMA20 > EMA50, 종가 > EMA10)이면 다음 봉 시가에 전부 사고,
    아니면 판다.
    ``always=True`` 면 BH(첫 봉 시가에 사서 보유)."""
    ind = _indicators(b, cfg)
    res = BoxResult()
    cash, qty = initial, 0.0
    want: bool | None = None
    end = to_ms + DAY_MS
    for i in range(len(b.t)):
        t = b.t[i]
        if t >= end:
            break
        op, cl = b.o[i], b.c[i]
        if t >= from_ms:
            target = True if always else want
            if target and qty == 0:
                f = cash * cfg.fee / (1 + cfg.fee)
                qty = (cash - f) / op
                res.fees += f
                cash = 0.0
                res.entries += 1
            elif target is False and qty > 0:
                gross = qty * op
                f = gross * cfg.fee
                cash += gross - f
                res.fees += f
                qty = 0.0
            value = qty * cl
            _mark(res, t - t % DAY_MS, cash + value, value)
        want = ind.e1[i] > ind.e2[i] > ind.e3[i] and cl > ind.e1[i]
    return _fill_days(res, from_ms, to_ms, initial)
