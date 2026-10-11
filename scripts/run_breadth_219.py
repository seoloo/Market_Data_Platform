# ruff: noqa: E501 — 한국어 설명이 길다
"""문서 219 — 코인 상승 참여 급확대(4 시간봉 상승 종목 비율 ≤ 30 % → ≥ 80 %) 뒤 BTCUSDT 선물 롱 24 시간 · 후향 파일럿. 주문 없음.

사용::

    python -m scripts.run_breadth_219 audit   # 종목군 · 자료 · 참여율 · 신호 수 (손익 없음)
    python -m scripts.run_breadth_219 run     # 사전 고정(문서 219 §4 ~ §8) 뒤 한 번 → reports/breadth_219/

종목군 = 212 의 월별 USD-M 거래대금 상위 20(BTC 제외 · 폐지 포함 · 180 일 이상 상장, `data/research/rr_212/members.json`).
4 시간봉 = 바이낸스 공개 아카이브 USD-M klines 4h. 실행 = 210 캐시 BTCUSDT 1 분봉 · funding · mark. 2026-09-01 이후는 읽지 않는다.
"""

from __future__ import annotations

import bisect
import csv
import io
import json
import math
import random
import statistics
import sys
import zipfile
from collections import Counter
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, date, datetime
from itertools import pairwise
from pathlib import Path
from typing import Any

from scripts.run_qb_dc_210 import MIN, WARM, Market, _load

REPO = Path(__file__).resolve().parent.parent
VISION = REPO / "data" / "external" / "binance_vision" / "um"
MEMBERS = REPO / "data" / "research" / "rr_212" / "members.json"
REPORT = REPO / "reports" / "breadth_219"
H4 = 4 * 3_600_000
DAY = 86_400_000


def _ms(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, tzinfo=UTC).timestamp() * 1000)


EVAL_START = _ms(date(2020, 7, 1))
END = _ms(date(2026, 9, 1))  # 이 시각 이후는 읽지 않는다
MID = _ms(date(2023, 8, 1))  # 2020-07-01 ~ 2023-07-31 / 2023-08-01 ~ 2026-08-31 (같은 길이)
B_LOW, B_HIGH = 0.30, 0.80
MIN_VALID = 15  # 참여율 계산에 필요한 유효 종목 수 (20 개 중)
VOL_BARS = 42  # BTC 과거 변동성: 직전 42 개 4 시간봉(7 일) 로그 수익 표준편차
K_MATCH = 5  # C: A 사건마다 가장 가까운 대조 시점 5 개
EXCL_H = (
    12  # C: A 신호 봉 ±12 시간 안의 시점은 대조 후보에서 뺀다 (손익 전 균형만 보고 정함, 문서 §6)
)
ENTRY_MIN = 20
HOLD_MIN = 24 * 60
EXIT_TRIES = 4
EMERG_WAIT = 60


# ------------------------------------------------------------------ 자료


def load_4h(symbol: str) -> dict[int, tuple[float, float, float, float]]:
    """봉 시작 ms → (시가, 고가, 저가, 종가). 공개 아카이브 월 파일, 2026-08 까지."""
    out: dict[int, tuple[float, float, float, float]] = {}
    for z in sorted((VISION / symbol / "4h").glob(f"{symbol}-4h-*.zip")):
        if z.stem.rsplit("-", 2)[-2] + "-" + z.stem.rsplit("-", 1)[-1] > "2026-08":
            continue
        with zipfile.ZipFile(z) as zf:
            for line in io.TextIOWrapper(zf.open(zf.namelist()[0]), "utf-8"):
                p = line.split(",")
                if not p[0].isdigit():
                    continue
                t = int(p[0])
                if t + H4 <= END:
                    out[t] = (float(p[1]), float(p[2]), float(p[3]), float(p[4]))
    return out


def month_key(t: int) -> str:
    return datetime.fromtimestamp(t / 1000, UTC).strftime("%Y-%m")


@dataclass(frozen=True, slots=True)
class Row:
    """완성 4 시간봉 t (시작 ms). 판단 시각 = t + 4 시간."""

    t: int
    n_valid: int
    b_prev: float | None
    b_cur: float | None
    mean_ret: float | None  # 같은 유효 종목의 동일가중 4 시간 수익
    btc_ret: float | None
    btc_vol: float | None
    btc_close: float | None
    btc_low: float | None


def participation(
    members: dict[str, list[str]],
    bars: dict[str, dict[int, tuple[float, float, float, float]]],
    btc: dict[int, tuple[float, float, float, float]],
    start: int = EVAL_START,
    end: int = END,
) -> list[Row]:
    """봉 t 의 참여율. 유효 종목 = 봉 t 가 속한 달의 종목군 중 봉 t−2 · t−1 · t 가 모두 있는 종목.

    B_(t−1) 과 B_t 를 **같은 유효 종목**으로 센다 → 달이 바뀔 때 종목 구성 변경 · 자료 결측이 가짜 전환을 만들지 않는다.
    수익 0 은 상승이 아니다. 유효 종목 < 15 면 참여율 없음.
    """
    out = []
    for t in range(start, end, H4):
        if t + H4 > end:
            break
        mem = members.get(month_key(t), [])
        r_prev, r_cur = [], []
        for s in mem:
            b = bars.get(s, {})
            x0, x1, x2 = b.get(t - 2 * H4), b.get(t - H4), b.get(t)
            if x0 is None or x1 is None or x2 is None or x0[3] <= 0 or x1[3] <= 0:
                continue
            r_prev.append(x1[3] / x0[3] - 1)
            r_cur.append(x2[3] / x1[3] - 1)
        n = len(r_cur)
        bp = sum(1 for x in r_prev if x > 0) / n if n >= MIN_VALID else None
        bc = sum(1 for x in r_cur if x > 0) / n if n >= MIN_VALID else None
        mr = statistics.fmean(r_cur) if n >= MIN_VALID else None
        y0, y1 = btc.get(t - H4), btc.get(t)
        br = y1[3] / y0[3] - 1 if (y0 and y1) else None
        hist = [btc.get(t - k * H4) for k in range(VOL_BARS + 1)]
        vol = (
            statistics.stdev(math.log(hist[k][3] / hist[k + 1][3]) for k in range(VOL_BARS))  # type: ignore[index]
            if all(h is not None for h in hist)
            else None
        )
        out.append(Row(t, n, bp, bc, mr, br, vol, y1[3] if y1 else None, y1[2] if y1 else None))
    return out


def is_a(r: Row) -> bool:
    return (
        r.b_prev is not None
        and r.b_cur is not None
        and r.btc_ret is not None
        and r.b_prev <= B_LOW
        and r.b_cur >= B_HIGH
        and r.btc_ret > 0
    )


def is_b(r: Row) -> bool:
    return r.btc_ret is not None and r.btc_ret > 0 and r.btc_low is not None


def match_controls(rows: list[Row], k: int = K_MATCH) -> dict[int, list[int]]:
    """C: A 사건 봉 t → 대조 봉 k 개. 후보 = BTC 4 시간 수익 > 0 · 참여율 계산됨 · A 아님 · 모든 A 신호 봉과 12 시간 이상 떨어짐.

    거리 = (BTC 4 시간 수익, 종목군 동일가중 4 시간 수익, BTC 7 일 변동성) 을 후보 전체 표준편차로 나눈 유클리드 거리.
    가까운 순(같으면 이른 시각) k 개, 사건 사이에서는 같은 대조 시점을 다시 써도 된다.
    """
    a_rows = [r for r in rows if is_a(r) and r.btc_vol is not None and r.mean_ret is not None]
    a_ts = sorted(r.t for r in a_rows)
    cand = []
    for r in rows:
        if is_a(r) or not is_b(r) or r.mean_ret is None or r.btc_vol is None or r.b_cur is None:
            continue
        j = bisect.bisect_left(a_ts, r.t)
        near = any(
            abs(r.t - a_ts[x]) < EXCL_H * 3_600_000 for x in (j - 1, j) if 0 <= x < len(a_ts)
        )
        if not near:
            cand.append(r)
    sd = [
        statistics.pstdev([getattr(c, f) for c in cand]) or 1.0
        for f in ("btc_ret", "mean_ret", "btc_vol")
    ]
    out: dict[int, list[int]] = {}
    for a in a_rows:
        dist = sorted(
            (
                math.sqrt(
                    sum(
                        ((getattr(c, f) - getattr(a, f)) / s) ** 2
                        for f, s in zip(("btc_ret", "mean_ret", "btc_vol"), sd, strict=True)
                    )
                ),
                c.t,
            )
            for c in cand
        )
        out[a.t] = [t for _, t in dist[:k]]
    return out


# ------------------------------------------------------------------ 체결 · 보유 (1 분봉 재생)


@dataclass(frozen=True, slots=True)
class P:
    cost: float = 0.00095
    limit: float = 0.003  # 진입 상한 = 신호 종가 × 1.003, 청산 하한 = 직전 분 종가 × 0.997
    participation: float = 0.01  # 계좌: 그 분 거래대금의 1 % 까지
    fill: str = (
        "open"  # open = 시가가 한도 안일 때 시가 · worst = 한도 안일 때 그 분 최악가(한도까지)
    )
    extra: float = 0.01  # 보호 · 비상 체결 추가 비용
    risk: float = 0.0025
    max_notional: float = 0.25
    step: float = 0.001  # BTCUSDT LOT_SIZE (거래소 정보 2026-10-11)
    min_qty: float = 0.001
    min_notional: float = 50.0
    daily_loss: float = 0.02
    halt: float = 0.30
    equity0: float = 10_000.0


def floor_step(q: float, step: float) -> float:
    return math.floor(q / step + 1e-9) * step


@dataclass
class Trade:
    t_sig: int  # 판단 시각 ms
    stop: float
    limit: float
    want: float
    fills: list[tuple[int, float, float, float, float]] = field(
        default_factory=list
    )  # (분, 수량 ±, 가격, 수수료, 추가 비용)
    funds: list[tuple[int, float]] = field(default_factory=list)  # (분, 낸 금액)
    first_fill: int = -1
    exit_i: int = -1
    reason: str = ""
    exit_delay_min: int = -1

    @property
    def qty_in(self) -> float:
        return sum(q for _, q, *_ in self.fills if q > 0)

    @property
    def avg_in(self) -> float:
        q = self.qty_in
        return sum(q_ * p for _, q_, p, *_ in self.fills if q_ > 0) / q if q else 0.0

    def cash(self) -> float:
        """거래가 끝난 뒤 현금 변화 (가격 손익 − 수수료 − 추가 비용 − funding)."""
        return -sum(q * p + f + x for _, q, p, f, x in self.fills) - sum(a for _, a in self.funds)

    def parts(self) -> dict[str, float]:
        gross = -sum(q * p for _, q, p, _f, _x in self.fills)
        return {
            "gross": gross,
            "fees": sum(f for *_, f, _x in self.fills),
            "extra": sum(x for *_, x in self.fills),
            "funding": sum(a for _, a in self.funds),
            "net": self.cash(),
        }


def run_trade(
    m: Market,
    fund_t: list[int],
    t_sig: int,
    close: float,
    stop: float,
    want: float,
    p: P,
    account: bool,
) -> Trade:
    """판단 시각 t_sig 뒤 다음 1 분봉(t_sig + 1 분)부터 20 분 지정가 진입 → 첫 체결 + 24 시간 지정가 청산. 보호 = stop (STOP_MARKET 예외)."""
    tr = Trade(t_sig, stop, close * (1 + p.limit), want)
    i0 = (t_sig - WARM) // MIN + 1
    qty = 0.0
    entering = True
    exit_due = emerg = -1
    tries = 0
    fptr = bisect.bisect_right(fund_t, WARM + i0 * MIN)
    i = i0
    n_end = (END - WARM) // MIN
    while i < n_end:
        # funding (그 분 시작 보유 수량, 체결보다 먼저)
        while fptr < len(fund_t) and (fund_t[fptr] - WARM) // MIN <= i:
            f_ms, rate, mk = m.funding[fptr]
            fptr += 1
            if qty > 0:
                px = mk if mk is not None else m.c[(f_ms - WARM) // MIN - 1]
                tr.funds.append((i, qty * px * rate))
        # 보호 (첫 체결 다음 분부터)
        if qty > 0 and i > tr.first_fill and m.qv[i] > 0 and m.l[i] <= stop:
            px = min(stop, m.o[i])
            tr.fills.append((i, -qty, px, qty * px * p.cost, qty * px * p.extra))
            qty = 0.0
            tr.exit_i, tr.reason = i, "보호 청산"
            return tr
        # 진입 (20 분)
        if entering:
            if i >= i0 + ENTRY_MIN or (
                qty == 0 and (m.o[i] <= stop or (i > i0 and m.l[i - 1] <= stop))
            ):
                entering = False
                if qty == 0:
                    tr.reason = "미체결" if i >= i0 + ENTRY_MIN else "진입 전 보호가 이하"
                    return tr
            elif m.qv[i] > 0 and m.o[i] <= tr.limit:
                px = m.o[i] if p.fill == "open" else min(m.h[i], tr.limit)
                room = want - qty
                q = (
                    floor_step(min(room, p.participation * m.qv[i] / px), p.step)
                    if account
                    else room
                )
                if q > 0:
                    tr.fills.append((i, q, px, q * px * p.cost, 0.0))
                    qty += q
                    if tr.first_fill < 0:
                        tr.first_fill = i
                        exit_due = i + HOLD_MIN
                if qty >= want - 1e-12:
                    entering = False
        # 24 시간 청산
        if qty > 0 and exit_due >= 0 and i >= exit_due:
            if emerg < 0:
                emerg = exit_due + EMERG_WAIT
            if i >= emerg:
                px = m.o[i]
                tr.fills.append((i, -qty, px, qty * px * p.cost, qty * px * p.extra))
                qty = 0.0
                tr.exit_i, tr.reason, tr.exit_delay_min = i, "시간 청산(비상)", i - exit_due
                return tr
            if tries < EXIT_TRIES:
                tries += 1
                lim = m.c[i - 1] * (1 - p.limit)
                if m.qv[i] > 0 and m.o[i] >= lim:
                    px = m.o[i] if p.fill == "open" else max(m.l[i], lim)
                    q = min(qty, p.participation * m.qv[i] / px) if account else qty
                    q = qty if q >= qty - 1e-12 else floor_step(q, p.step)
                    if q > 0:
                        tr.fills.append((i, -q, px, q * px * p.cost, 0.0))
                        qty -= q
                if qty <= 1e-12:
                    tr.exit_i, tr.reason, tr.exit_delay_min = i, "시간 청산", i - exit_due
                    return tr
        i += 1
    tr.reason = "평가 끝 보유 중"
    return tr


def mark_value(tr: Trade, m: Market, i: int) -> float:
    """분 i 시작 시점의 거래 현금 변화 + 보유 평가 (i 이전 사건만)."""
    cash = -sum(q * p + f + x for k, q, p, f, x in tr.fills if k < i) - sum(
        a for k, a in tr.funds if k < i
    )
    qty = sum(q for k, q, *_ in tr.fills if k < i)
    return cash + qty * m.c[i - 1]


def account(
    m: Market, sigs: list[Row], p: P, start: int = EVAL_START, end: int = END
) -> dict[str, Any]:
    """BTC 포지션 하나. 시각순 신호 · 보유 중 신호 무시 · 일 손실 2 % · 누적 30 % 정지(새 진입 없음, 자동 재개 없음)."""
    fund_t = [f[0] for f in m.funding]
    trades: list[Trade] = []
    skips: Counter[str] = Counter()
    cur: Trade | None = None
    base = p.equity0  # 마지막 거래 시작 전 자산
    peak = p.equity0
    halted: str | None = None
    daily: list[tuple[str, float, float]] = []
    day_eq: dict[int, float] = {}
    next_day = start

    def equity_at(i: int) -> tuple[float, float]:
        """분 i 시작 시점 (자산, 보유 명목)."""
        if cur is None:
            return base, 0.0
        if cur.exit_i >= 0 and i > cur.exit_i:
            return base + cur.cash(), 0.0
        qty = sum(q for k, q, *_ in cur.fills if k < i)
        return base + mark_value(cur, m, i), qty * m.c[i - 1]

    def advance(until: int) -> None:
        nonlocal next_day, peak, halted
        while next_day <= until and next_day <= end:
            if next_day > start:
                e, nt = equity_at((next_day - WARM) // MIN)
                daily.append(
                    (
                        datetime.fromtimestamp((next_day - DAY) / 1000, UTC).date().isoformat(),
                        e,
                        nt / e if e > 0 else 0.0,
                    )
                )
                peak = max(peak, e)
                if halted is None and e <= peak * (1 - p.halt):
                    halted = datetime.fromtimestamp(next_day / 1000, UTC).date().isoformat()
            day_eq[next_day] = equity_at((next_day - WARM) // MIN)[0]
            next_day += DAY

    for r in sorted(sigs, key=lambda x: x.t):
        ts = r.t + H4
        if not (start <= ts < end):
            continue
        advance(ts)
        i_ts = (ts - WARM) // MIN
        if cur is not None and (cur.exit_i < 0 or i_ts <= cur.exit_i):
            skips["보유 중 신호"] += 1
            continue
        if halted:
            skips["계좌 30% 정지"] += 1
            continue
        e_now = equity_at(i_ts)[0]
        if e_now < day_eq[ts // DAY * DAY] * (1 - p.daily_loss):
            skips["일 손실 2%"] += 1
            continue
        assert r.btc_close is not None and r.btc_low is not None
        dist = r.btc_close - r.btc_low
        if dist <= 0:
            skips["보호 불가(종가 = 저가)"] += 1
            continue
        want = floor_step(min(p.risk * e_now / dist, p.max_notional * e_now / r.btc_close), p.step)
        if want < p.min_qty or want * r.btc_close < p.min_notional:
            skips["수량 미달(사전 검사)"] += 1
            continue
        tr = run_trade(m, fund_t, ts, r.btc_close, r.btc_low, want, p, account=True)
        if not tr.fills:
            skips[tr.reason] += 1
            continue
        if cur is not None:
            base += cur.cash()
        trades.append(tr)
        cur = tr
    advance(end)
    if cur is not None and cur.exit_i < 0:
        skips["평가 끝 보유 중(평가만)"] += 1
    return {"daily": daily, "trades": trades, "skips": skips, "halted": halted}


# ------------------------------------------------------------------ 통계


def account_stats(res: dict[str, Any], p: P) -> dict[str, Any]:
    d = res["daily"]
    eq = [x[1] for x in d]
    r = [eq[k] / eq[k - 1] - 1 for k in range(1, len(eq))]
    years = len(r) / 365
    sd = statistics.stdev(r)
    peak, mdd = eq[0], 0.0
    for x in eq:
        peak = max(peak, x)
        mdd = max(mdd, 1 - x / peak)
    trs: list[Trade] = res["trades"]
    closed = [t for t in trs if t.exit_i >= 0]
    pn = [t.cash() for t in closed]
    parts = [t.parts() for t in closed]
    wins = [x for x in pn if x > 0]
    loss = [x for x in pn if x <= 0]
    traded = sum(abs(q) * px for t in trs for _, q, px, *_ in t.fills)
    top5 = sorted(pn, reverse=True)[:5]
    by_year: dict[str, float] = {}
    for t in closed:
        y = datetime.fromtimestamp((WARM + t.exit_i * MIN) / 1000, UTC).strftime("%Y")
        by_year[y] = by_year.get(y, 0.0) + t.cash()
    by_month: dict[str, float] = {}
    for t in closed:
        mo = datetime.fromtimestamp((WARM + t.exit_i * MIN) / 1000, UTC).strftime("%Y-%m")
        by_month[mo] = by_month.get(mo, 0.0) + t.cash()
    best_mo = max(by_month.items(), key=lambda x: x[1]) if by_month else ("", 0.0)
    return {
        "net": round(eq[-1] - eq[0], 2),
        "cagr_pct": round(((eq[-1] / eq[0]) ** (1 / years) - 1) * 100, 3),
        "arith_ann_pct": round(statistics.fmean(r) * 365 * 100, 3),
        "sharpe": round(statistics.fmean(r) / sd * math.sqrt(365), 3) if sd > 0 else 0.0,
        "mdd_pct": round(mdd * 100, 2),
        "avg_exposure_pct": round(statistics.fmean(x[2] for x in d) * 100, 3),
        "max_exposure_pct": round(max(x[2] for x in d) * 100, 2),
        "turnover_x_per_year": round(traded / statistics.fmean(eq) / years, 2),
        "trades": len(closed),
        "win_pct": round(len(wins) / len(pn) * 100, 1) if pn else 0.0,
        "avg_trade": round(statistics.fmean(pn), 3) if pn else 0.0,
        "median_trade": round(statistics.median(pn), 3) if pn else 0.0,
        "avg_win": round(statistics.fmean(wins), 3) if wins else 0.0,
        "avg_loss": round(statistics.fmean(loss), 3) if loss else 0.0,
        "gross_before_costs": round(sum(x["gross"] for x in parts), 2),
        "fees": round(sum(x["fees"] for x in parts), 2),
        "funding": round(sum(x["funding"] for x in parts), 2),
        "emergency_extra": round(sum(x["extra"] for x in parts), 2),
        "net_ex_top5_trades": round(sum(pn) - sum(top5), 2),
        "top5_trades": [round(x, 2) for x in top5],
        "best_month": [best_mo[0], round(best_mo[1], 2)],
        "by_year_net": {k: round(v, 2) for k, v in sorted(by_year.items())},
        "halves_net": [
            round(sum(t.cash() for t in closed if t.t_sig < MID), 2),
            round(sum(t.cash() for t in closed if t.t_sig >= MID), 2),
        ],
        "exit_reasons": dict(Counter(t.reason for t in closed)),
        "exit_delay_min": dict(
            Counter(t.exit_delay_min for t in closed if t.reason.startswith("시간"))
        ),
        "partial_entries": sum(1 for t in trs if t.qty_in < t.want - 1e-9),
        "skips": dict(res["skips"]),
        "halted": res["halted"],
    }


def event_trade(m: Market, fund_t: list[int], r: Row, p: P) -> dict[str, Any] | None:
    """사건 단위(계좌 제약 없음, 수량 1): 같은 체결 · 보호 · 보유 · 비용. 순수익 = 진입 명목 대비, R = 계획 위험(종가 − 저가) 대비."""
    assert r.btc_close is not None and r.btc_low is not None
    dist = r.btc_close - r.btc_low
    if dist <= 0:
        return None
    tr = run_trade(m, fund_t, r.t + H4, r.btc_close, r.btc_low, 1.0, p, account=False)
    if not tr.fills or tr.exit_i < 0:
        return {"t": r.t, "filled": False, "reason": tr.reason}
    pr = tr.parts()
    notional = tr.avg_in
    return {
        "t": r.t,
        "filled": True,
        "reason": tr.reason,
        "net": pr["net"] / notional,
        "gross": pr["gross"] / notional,
        "fees": pr["fees"] / notional,
        "funding": pr["funding"] / notional,
        "extra": pr["extra"] / notional,
        "R": pr["net"] / dist,
        "risk_pct": dist / r.btc_close,
    }


def week(t: int) -> str:
    y, w, _ = datetime.fromtimestamp(t / 1000, UTC).isocalendar()
    return f"{y}-{w:02d}"


def cluster_boot(
    groups: dict[str, list[float]], reps: int = 2000, seed: int = 219
) -> dict[str, Any]:
    """주(ISO week) 군집 부트스트랩: 같은 주의 사건을 함께 뽑아 평균. 90 % 구간."""
    keys = sorted(groups)
    rng = random.Random(seed)
    means = []
    for _ in range(reps):
        xs: list[float] = []
        for _k in keys:
            xs.extend(groups[keys[rng.randrange(len(keys))]])
        means.append(statistics.fmean(xs) if xs else 0.0)
    means.sort()
    allx = [x for k in keys for x in groups[k]]
    return {
        "n": len(allx),
        "clusters": len(keys),
        "mean": round(statistics.fmean(allx), 6) if allx else None,
        "ci90": [round(means[int(0.05 * reps)], 6), round(means[int(0.95 * reps)], 6)],
        "boot_share_gt0": round(sum(1 for x in means if x > 0) / reps, 3),
    }


def paired_boot_diff(
    a: dict[str, list[float]], b: dict[str, list[float]], reps: int = 2000, seed: int = 219
) -> dict[str, Any]:
    """같은 주 묶음으로 두 집합을 함께 뽑아 평균 차이 (A − B)."""
    keys = sorted(set(a) | set(b))
    rng = random.Random(seed)
    diffs = []
    for _ in range(reps):
        xa: list[float] = []
        xb: list[float] = []
        for _k in keys:
            kk = keys[rng.randrange(len(keys))]
            xa.extend(a.get(kk, []))
            xb.extend(b.get(kk, []))
        if xa and xb:
            diffs.append(statistics.fmean(xa) - statistics.fmean(xb))
    diffs.sort()
    fa = [x for v in a.values() for x in v]
    fb = [x for v in b.values() for x in v]
    return {
        "diff": round(statistics.fmean(fa) - statistics.fmean(fb), 6),
        "ci90": [round(diffs[int(0.05 * len(diffs))], 6), round(diffs[int(0.95 * len(diffs))], 6)],
        "boot_share_gt0": round(sum(1 for x in diffs if x > 0) / len(diffs), 3),
    }


def daily_diff_boot(
    da: list[tuple[str, float, float]],
    db: list[tuple[str, float, float]],
    block: int = 14,
    reps: int = 2000,
    seed: int = 219,
) -> dict[str, Any]:
    ra = [da[k][1] / da[k - 1][1] - 1 for k in range(1, len(da))]
    rb = [db[k][1] / db[k - 1][1] - 1 for k in range(1, len(db))]
    d = [x - y for x, y in zip(ra, rb, strict=True)]
    n = len(d)
    rng = random.Random(seed)
    means = []
    for _ in range(reps):
        idx: list[int] = []
        while len(idx) < n:
            st = rng.randrange(n)
            idx.extend((st + k) % n for k in range(block))
        means.append(statistics.fmean(d[k] for k in idx[:n]) * 365)
    means.sort()
    return {
        "block_days": block,
        "mean_diff_ann_pct": round(statistics.fmean(d) * 365 * 100, 3),
        "ci90_ann_pct": [
            round(means[int(0.05 * reps)] * 100, 3),
            round(means[int(0.95 * reps)] * 100, 3),
        ],
    }


# ------------------------------------------------------------------ 실행


def load_all() -> tuple[dict[str, list[str]], list[Row]]:
    members = json.loads(MEMBERS.read_text(encoding="utf-8"))
    syms = sorted({s for v in members.values() for s in v})
    bars = {s: load_4h(s) for s in syms}
    btc = load_4h("BTCUSDT")
    return members, participation(members, bars, btc)


def audit() -> dict[str, Any]:
    members, rows = load_all()
    ok = [r for r in rows if r.b_cur is not None]
    a = [r for r in rows if is_a(r)]
    b = [r for r in rows if is_b(r)]
    # 같은 고참여 상태 반복 여부: A 사이 간격
    gaps_h = [(y.t - x.t) / 3_600_000 for x, y in pairwise(a)]
    within24 = sum(1 for g in gaps_h if g < 24)
    # 독립 사건 (24 시간 안에 이어진 A 는 한 묶음)
    clusters = 0
    last = -(10**15)
    for r in a:
        if r.t - last >= 24 * 3_600_000:
            clusters += 1
        last = r.t
    # 참여율이 BTC · 시장 평균 수익의 재표현인가 (손익 없음)
    full = [r for r in ok if r.mean_ret is not None and r.btc_ret is not None]
    corr_bm = statistics.correlation([r.b_cur for r in full], [r.mean_ret for r in full])  # type: ignore[misc]
    corr_bb = statistics.correlation([r.b_cur for r in full], [r.btc_ret for r in full])  # type: ignore[misc]
    # 시장 평균 수익이 A 사건 범위(A 의 10 ~ 90 백분위) 안인 B 시점 중 A 비율
    am = sorted(r.mean_ret for r in a if r.mean_ret is not None)
    lo, hi = am[int(0.1 * len(am))], am[int(0.9 * len(am))]
    band = [r for r in full if lo <= r.mean_ret <= hi and r.btc_ret > 0]  # type: ignore[operator]
    ctrl = match_controls(rows)
    cts = sorted({t for v in ctrl.values() for t in v})
    by_t = {r.t: r for r in rows}

    def avg(xs: list[Row], f: str) -> float:
        return round(statistics.fmean(getattr(x, f) for x in xs), 5)

    return {
        "members_per_month": {k: len(v) for k, v in sorted(members.items()) if k >= "2020-06"},
        "bars": len(rows),
        "bars_with_participation": len(ok),
        "first_bar_with_participation": datetime.fromtimestamp(ok[0].t / 1000, UTC).isoformat()
        if ok
        else None,
        "valid_count_dist": dict(
            sorted(Counter(r.n_valid for r in rows if r.t >= _ms(date(2020, 9, 1))).items())
        ),
        "B_dist": {
            q: round(sorted(r.b_cur for r in ok)[int(q / 100 * (len(ok) - 1))], 3)
            for q in (5, 25, 50, 75, 95)
        },  # type: ignore[type-var]
        "share_b_le_30": round(sum(1 for r in ok if r.b_cur <= B_LOW) / len(ok), 4),  # type: ignore[operator]
        "share_b_ge_80": round(sum(1 for r in ok if r.b_cur >= B_HIGH) / len(ok), 4),  # type: ignore[operator]
        "A_signals": len(a),
        "A_by_year": dict(
            Counter(datetime.fromtimestamp(r.t / 1000, UTC).strftime("%Y") for r in a)
        ),
        "A_halves": [sum(1 for r in a if r.t < MID), sum(1 for r in a if r.t >= MID)],
        "A_within_24h_of_previous": within24,
        "A_independent_clusters_24h": clusters,
        "A_weeks": len({week(r.t) for r in a}),
        "A_without_btc_condition": sum(
            1
            for r in ok
            if r.b_prev is not None and r.b_prev <= B_LOW and r.b_cur >= B_HIGH  # type: ignore[operator]
        ),
        "B_signals": len(b),
        "corr_B_vs_mean_ret": round(corr_bm, 3),
        "corr_B_vs_btc_ret": round(corr_bb, 3),
        "A_mean_ret_p10_p90": [round(lo, 4), round(hi, 4)],
        "bars_btc_up_in_A_mean_ret_band": len(band),
        "share_of_those_that_are_A": round(sum(1 for r in band if is_a(r)) / len(band), 4)
        if band
        else None,
        "share_of_those_with_B_ge_80": round(
            sum(1 for r in band if r.b_cur >= B_HIGH) / len(band), 4
        )
        if band
        else None,  # type: ignore[operator]
        "C_controls_unique": len(cts),
        "C_balance_weighted": _balance(
            [by_t[t] for t in ctrl], [by_t[t] for v in ctrl.values() for t in v]
        ),
    }


def _balance(a: list[Row], c: list[Row]) -> dict[str, list[float]]:
    """A 사건 · 대조(쓰인 횟수만큼) 평균과 표준화 평균 차이. 기준: 맞춘 세 변수 |차이| < 0.1."""
    out = {}
    for f in ("btc_ret", "mean_ret", "btc_vol", "b_cur", "b_prev"):
        xa = [getattr(x, f) for x in a]
        xc = [getattr(x, f) for x in c]
        sd = statistics.pstdev(xa + xc)
        out[f] = [
            round(statistics.fmean(xa), 5),
            round(statistics.fmean(xc), 5),
            round((statistics.fmean(xa) - statistics.fmean(xc)) / sd, 3),
        ]
    return out


def run() -> dict[str, Any]:
    _members, rows = load_all()
    m = _load()["BTCUSDT"]
    fund_t = [f[0] for f in m.funding]
    a_rows = [r for r in rows if is_a(r)]
    b_rows = [r for r in rows if is_b(r)]
    by_t = {r.t: r for r in rows}
    ctrl = match_controls(rows)
    out: dict[str, Any] = {"made": datetime.now(UTC).isoformat(), "params": asdict(P())}
    sens = {"main": P(), "cost_x2": P(cost=0.0019), "fill_worst": P(fill="worst")}
    acc: dict[str, dict[str, Any]] = {}
    for sname, p in sens.items():
        for arm, sg in (("A", a_rows), ("B", b_rows)):
            res = account(m, sg, p)
            acc.setdefault(sname, {})[arm] = res
            print(sname, arm, account_stats(res, p)["net"], flush=True)
    out["account"] = {
        s: {arm: account_stats(v, sens[s]) for arm, v in d.items()} for s, d in acc.items()
    }
    out["account_A_minus_B"] = {
        s: {
            f"block{bl}": daily_diff_boot(d["A"]["daily"], d["B"]["daily"], bl)
            for bl in (14, 7, 30)
        }
        for s, d in acc.items()
    }
    # 사건 단위
    ev: dict[str, Any] = {}
    for sname, p in sens.items():
        ea = {r.t: event_trade(m, fund_t, r, p) for r in a_rows}
        eb = {r.t: event_trade(m, fund_t, r, p) for r in b_rows}
        ct = {t: event_trade(m, fund_t, by_t[t], p) for t in {t for v in ctrl.values() for t in v}}
        fa = {t: e for t, e in ea.items() if e and e["filled"]}
        fb = {t: e for t, e in eb.items() if e and e["filled"]}

        def grp(d: dict[int, dict[str, Any]], key: str) -> dict[str, list[float]]:
            g: dict[str, list[float]] = {}
            for t, e in d.items():
                g.setdefault(week(t), []).append(e[key])
            return g

        diffs: dict[str, list[float]] = {}
        n_pairs = 0
        for t, e in fa.items():
            cs = [ct[c] for c in ctrl.get(t, []) if ct.get(c) and ct[c]["filled"]]  # type: ignore[index]
            if not cs:
                continue
            n_pairs += 1
            diffs.setdefault(week(t), []).append(e["net"] - statistics.fmean(c["net"] for c in cs))  # type: ignore[index]
        top5 = sorted((e["net"] for e in fa.values()), reverse=True)[:5]
        ev[sname] = {
            "A_signals": len(a_rows),
            "A_filled": len(fa),
            "A_unfilled": dict(Counter(e["reason"] for e in ea.values() if e and not e["filled"])),
            "A_net": cluster_boot(grp(fa, "net")),
            "A_R": cluster_boot(grp(fa, "R")),
            "A_parts_mean": {
                k: round(statistics.fmean(e[k] for e in fa.values()), 6)
                for k in ("gross", "fees", "funding", "extra", "net")
            }
            if fa
            else {},
            "A_median_net": round(statistics.median(e["net"] for e in fa.values()), 6)
            if fa
            else None,
            "A_win_pct": round(sum(1 for e in fa.values() if e["net"] > 0) / len(fa) * 100, 1)
            if fa
            else None,
            "A_net_ex_top5_mean": round(
                (sum(e["net"] for e in fa.values()) - sum(top5)) / max(1, len(fa) - 5), 6
            ),
            "A_halves_net_mean": [
                round(
                    statistics.fmean([e["net"] for t, e in fa.items() if t < MID] or [math.nan]), 6
                ),
                round(
                    statistics.fmean([e["net"] for t, e in fa.items() if t >= MID] or [math.nan]), 6
                ),
            ],
            "A_by_year_net_mean": {
                y: round(statistics.fmean(v), 6)
                for y, v in _by(
                    fa, lambda t: datetime.fromtimestamp(t / 1000, UTC).strftime("%Y")
                ).items()
            },
            "A_exit_reasons": dict(Counter(e["reason"] for e in fa.values())),
            "B_filled": len(fb),
            "B_net": cluster_boot(grp(fb, "net")),
            "A_minus_B_event": paired_boot_diff(grp(fa, "net"), grp(fb, "net")),
            "A_minus_C_matched": {**cluster_boot(diffs), "pairs": n_pairs},
            "C_controls_filled": sum(1 for e in ct.values() if e and e["filled"]),
            "C_net_mean": round(
                statistics.fmean(e["net"] for e in ct.values() if e and e["filled"]), 6
            ),
        }
        if sname == "main":
            REPORT.mkdir(parents=True, exist_ok=True)
            with (REPORT / "events_A.csv").open("w", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh)
                w.writerow(
                    [
                        "signal_bar_utc",
                        "filled",
                        "reason",
                        "net",
                        "gross",
                        "fees",
                        "funding",
                        "extra",
                        "R",
                        "risk_pct",
                        "controls",
                    ]
                )
                for t, e in sorted(ea.items()):
                    if e is None:
                        continue
                    w.writerow(
                        [
                            _iso(t),
                            e["filled"],
                            e["reason"],
                            *(
                                round(e.get(k, 0.0), 6)
                                for k in (
                                    "net",
                                    "gross",
                                    "fees",
                                    "funding",
                                    "extra",
                                    "R",
                                    "risk_pct",
                                )
                            ),
                            ";".join(_iso(c) for c in ctrl.get(t, [])),
                        ]
                    )
    out["events"] = ev
    out["C_balance_weighted"] = _balance(
        [by_t[t] for t in ctrl], [by_t[t] for v in ctrl.values() for t in v]
    )
    out["verdict"] = verdict(out)
    REPORT.mkdir(parents=True, exist_ok=True)
    (REPORT / "result.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1, default=str), encoding="utf-8"
    )
    for arm, res in acc["main"].items():
        with (REPORT / f"daily_{arm}.csv").open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["date", "equity", "exposure"])
            w.writerows(res["daily"])
        with (REPORT / f"trades_{arm}.csv").open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(
                [
                    "decision_utc",
                    "first_fill_utc",
                    "exit_utc",
                    "reason",
                    "want",
                    "qty_in",
                    "avg_in",
                    "stop",
                    "exit_delay_min",
                    "gross",
                    "fees",
                    "funding",
                    "extra",
                    "net",
                ]
            )
            for t in res["trades"]:
                pr = t.parts()
                w.writerow(
                    [
                        _iso(t.t_sig),
                        _iso_i(t.first_fill),
                        _iso_i(t.exit_i),
                        t.reason,
                        t.want,
                        round(t.qty_in, 3),
                        round(t.avg_in, 2),
                        t.stop,
                        t.exit_delay_min,
                        *(round(pr[k], 4) for k in ("gross", "fees", "funding", "extra", "net")),
                    ]
                )
    with (REPORT / "participation.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(
            ["bar_utc", "n_valid", "b_prev", "b_cur", "mean_ret", "btc_ret", "btc_vol", "A", "B"]
        )
        for r in rows:
            w.writerow(
                [
                    _iso(r.t),
                    r.n_valid,
                    r.b_prev,
                    r.b_cur,
                    r.mean_ret,
                    r.btc_ret,
                    r.btc_vol,
                    int(is_a(r)),
                    int(is_b(r)),
                ]
            )
    return out


def _by(d: dict[int, dict[str, Any]], key: Any) -> dict[str, list[float]]:
    g: dict[str, list[float]] = {}
    for t, e in d.items():
        g.setdefault(key(t), []).append(e["net"])
    return dict(sorted(g.items()))


def _iso(t: int) -> str:
    return datetime.fromtimestamp(t / 1000, UTC).isoformat()


def _iso_i(i: int) -> str:
    return _iso(WARM + i * MIN) if i >= 0 else ""


def verdict(out: dict[str, Any]) -> dict[str, Any]:
    """문서 219 §8 — 결과 전 고정."""
    a = out["account"]["main"]["A"]
    ev = out["events"]["main"]
    ac = ev["A_minus_C_matched"]
    if a["net"] <= 0:
        word = "불리 종료"
    elif ev["A_filled"] < 30 or ac["pairs"] < 30:
        word = "판단 불가 종료"
    elif ac["ci90"][1] < 0:
        word = "불리 종료"
    elif ac["ci90"][0] > 0 and all(h > 0 for h in ev["A_halves_net_mean"] if not math.isnan(h)):
        word = "향후 독립 검증 후보"
    else:
        word = "판단 불가 종료"
    return {"word": word}


def main(argv: list[str]) -> int:
    if argv[:1] == ["audit"]:
        r = audit()
        REPORT.mkdir(parents=True, exist_ok=True)
        (REPORT / "audit.json").write_text(
            json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        print(json.dumps(r, ensure_ascii=False, indent=1))
        return 0
    if argv[:1] == ["run"]:
        r = run()
        print(
            json.dumps(
                {
                    "account": {
                        k: {
                            a: {x: v[x] for x in ("net", "trades", "sharpe", "mdd_pct")}
                            for a, v in d.items()
                        }
                        for k, d in r["account"].items()
                    },
                    "verdict": r["verdict"],
                },
                ensure_ascii=False,
                indent=1,
            )
        )
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))


__all__ = [
    "P",
    "Row",
    "account",
    "event_trade",
    "is_a",
    "match_controls",
    "participation",
    "replace",
    "run_trade",
]
