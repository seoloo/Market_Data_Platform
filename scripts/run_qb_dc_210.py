# ruff: noqa: E501 — 한국어 설명 · 표 문자열이 길다
"""문서 210 — QB(거래대금 봉 20개 추세) · DC(가격 5% 방향전환) 후향 회계 파일럿. 주문 없음 · 실거래 연결 없음.

사용::

    python -m scripts.run_qb_dc_210 cache   # 1분봉 · funding · mark 를 읽어 캐시 (2026-08-31 까지만)
    python -m scripts.run_qb_dc_210 power   # 검정력 메모 (일간 수익 SD 만 — 비교 전략 수익은 보지 않음)
    python -m scripts.run_qb_dc_210 run     # 파일럿 한 번 → reports/qb_dc_210/result.json

규칙 · 비용 · 실행 모형은 문서 210 §2 ~ §6 에 고정돼 있다. 결과를 보고 바꾸지 않는다.
"""

from __future__ import annotations

import argparse
import io
import json
import math
import pickle
import random
import sqlite3
import statistics
import sys
import zipfile
from array import array
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
REPORT = REPO / "reports" / "qb_dc_210"
CACHE = REPO / "data" / "research" / "qb_dc_210" / "cache.pkl"
VISION = REPO / "data" / "external" / "binance_vision"

MIN = 60_000
H4 = 4 * 3_600_000
DAY = 86_400_000


def _ms(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, tzinfo=UTC).timestamp() * 1000)


WARM = _ms(date(2020, 1, 1))  # 지표 준비 시작 (평가 수익에 넣지 않음)
EVAL_START = _ms(date(2020, 3, 1))
END = _ms(date(2026, 9, 1))  # 이 시각 이후 자료는 읽지 않는다
SYMBOLS = ("BTCUSDT", "ETHUSDT")
N_MIN = (END - WARM) // MIN


# ------------------------------------------------------------------ 자료


@dataclass
class Market:
    """한 종목 1분봉을 WARM 부터 빈칸 없이 (빠진 분은 직전 종가 · 거래대금 0 · 체결 불가)."""

    o: array[float]
    h: array[float]
    l: array[float]  # noqa: E741
    c: array[float]
    qv: array[float]
    missing: int
    funding: list[tuple[int, float, float | None]] = field(default_factory=list)
    """(정산 분 시작 ms, rate, 정산 시각 mark 또는 None)."""


def _dense(rows: Sequence[tuple[int, float, float, float, float, float]]) -> Market:
    o, h, lo, c, qv = (array("d", [math.nan]) * N_MIN for _ in range(5))
    for t, a, b, d, e, v in rows:
        if WARM <= t < END:
            i = (t - WARM) // MIN
            o[i], h[i], lo[i], c[i], qv[i] = a, b, d, e, v
    missing = 0
    last = next(x for x in c if not math.isnan(x))
    for i in range(N_MIN):
        if math.isnan(c[i]):
            missing += 1
            o[i] = h[i] = lo[i] = c[i] = last
            qv[i] = 0.0
        else:
            last = c[i]
    return Market(o, h, lo, c, qv, missing)


def _vision_rows(
    market: str, symbol: str, interval: str
) -> list[tuple[int, float, float, float, float, float]]:
    out = []
    for z in sorted((VISION / market / symbol / interval).glob("*.zip")):
        if z.stem.split("-", 2)[2] >= "2026-09":
            continue  # 보존 중인 2026-09 이후는 열지 않는다
        with zipfile.ZipFile(z) as zf:
            for line in io.TextIOWrapper(zf.open(zf.namelist()[0]), "utf-8"):
                p = line.split(",")
                if not p[0].isdigit():
                    continue
                out.append(
                    (int(p[0]), float(p[1]), float(p[2]), float(p[3]), float(p[4]), float(p[7]))
                )
    return out


def load_markets() -> dict[str, Market]:
    db = sqlite3.connect(f"file:{REPO / 'data' / 'trading.db'}?mode=ro", uri=True)
    out: dict[str, Market] = {}
    for s in SYMBOLS:
        if s == "BTCUSDT":  # 바이낸스 USD-M 1분봉 (DB, source=bulk)
            rows = [
                (int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5]))
                for r in db.execute(
                    "select open_ms, open, high, low, close, quote_volume from candles "
                    "where exchange='binance_usdm' and symbol=? and timeframe='1m' and open_ms>=? and open_ms<? order by open_ms",
                    (s, WARM, END),
                )
            ]
        else:  # 공개 아카이브 USD-M 1분봉
            rows = _vision_rows("um", s, "1m")
        m = _dense(rows)
        mark = {r[0]: r[1] for r in _vision_rows("um_mark", s, "1h")}
        for ts, rate in db.execute(
            "select funding_ms, rate from funding_rates where exchange='binance_usdm' and symbol=? and funding_ms>=? and funding_ms<? order by funding_ms",
            (s, WARM, END),
        ):
            minute = int(ts) // MIN * MIN
            m.funding.append((minute, float(rate), mark.get(int(ts) // 3_600_000 * 3_600_000)))
        out[s] = m
    return out


# ------------------------------------------------------------------ 신호 (순수, 그 시각까지 완성된 자료만)


def daily_closes(m: Market) -> list[float]:
    """UTC 일 k 의 종가 = 그날 마지막 분 종가. k = 0 은 WARM 의 날."""
    per = DAY // MIN
    return [m.c[(k + 1) * per - 1] for k in range(len(m.c) // per)]


def daily_quote(m: Market) -> list[float]:
    per = DAY // MIN
    return [sum(m.qv[k * per : (k + 1) * per]) for k in range(len(m.qv) // per)]


def qb_bars(m: Market, dq: Sequence[float]) -> list[tuple[int, float, int]]:
    """거래대금 봉 [(정보 이용 가능 ms, 종가, 시작 ms)].

    목표량 = 봉 시작 시각 이전에 끝난 UTC 일 30 개의 일 거래대금 중앙값, 봉 시작에 고정.
    완성된 분봉을 차례로 더해 목표 이상이 된 첫 분봉에서 끝(그 분 전체 · 넘친 양 포함). 다음 봉은 다음 분부터 0.
    이용 가능 시각 = 마지막 분봉이 끝난 시각.
    """
    per = DAY // MIN
    bars: list[tuple[int, float, int]] = []
    i = 30 * per  # 완성된 30 일이 생기는 첫 분
    acc, target, start = 0.0, 0.0, -1
    n = len(m.c)
    while i < n:
        if start < 0:
            day = i // per
            target = statistics.median(dq[day - 30 : day])
            start, acc = i, 0.0
        acc += m.qv[i]
        if acc >= target:
            bars.append((WARM + (i + 1) * MIN, m.c[i], WARM + start * MIN))
            start = -1
        i += 1
    return bars


def dc_states(closes4h: Sequence[float], up: float = 0.05, down: float = 0.05) -> list[int]:
    """4시간 종가 차례대로 방향전환 상태 (1 = 상승/LONG, 0 = 하락/FLAT). 처음은 FLAT, L = 첫 종가."""
    out: list[int] = []
    state, lo, hi = 0, closes4h[0] if closes4h else 0.0, 0.0
    for x in closes4h:
        if state == 0:
            lo = min(lo, x)
            if x >= lo * (1 + up):
                state, hi = 1, x
        else:
            hi = max(hi, x)
            if x <= hi * (1 - down):
                state, lo = 0, x
        out.append(state)
    return out


@dataclass(frozen=True, slots=True)
class Signals:
    """판단 시각(4시간 칸 번호 j = (D − WARM) / H4)마다 원하는 상태 0 · 1 (T/v2 참고는 0 ~ 1 비율)."""

    by_j: dict[str, list[float]]


def build_signals(m: Market) -> Signals:
    dcl = daily_closes(m)
    dq = daily_quote(m)
    n_j = len(m.c) * MIN // H4
    out: dict[str, list[float]] = {k: [0.0] * n_j for k in ("T20", "QB", "DC", "TV2REF")}
    # T20 · T/v2 참고: 판단 시각 D 직전에 끝난 UTC 일 기준
    for j in range(n_j):
        d_ms = WARM + j * H4
        k = d_ms // DAY - WARM // DAY - 1  # 마지막 완성 일
        if k >= 20:
            out["T20"][j] = 1.0 if dcl[k] / dcl[k - 20] - 1 > 0 else 0.0
        if k >= 120:
            s = [1 if dcl[k] / dcl[k - n] - 1 > 0 else -1 for n in (20, 60, 120)]
            out["TV2REF"][j] = max(0.0, sum(s) / 3)
    # QB
    bars = qb_bars(m, dq)
    b = 0
    for j in range(n_j):
        d_ms = WARM + j * H4
        while b < len(bars) and bars[b][0] <= d_ms:
            b += 1
        if b >= 21:
            out["QB"][j] = 1.0 if bars[b - 1][1] / bars[b - 21][1] - 1 > 0 else 0.0
    # DC: 판단 시각 D 에 끝난 4시간봉(마지막 분 = D − 1분) 종가
    step = H4 // MIN
    c4 = [m.c[(j * step) - 1] for j in range(1, n_j)]
    st = dc_states(c4)
    for j in range(1, n_j):
        out["DC"][j] = float(st[j - 1])
    return Signals(out)


# ------------------------------------------------------------------ 계좌 모형


@dataclass(frozen=True, slots=True)
class Params:
    cost: float = 0.00095  # 편도, 모든 체결 명목 (슬리피지 포함 — 가격에서 따로 빼지 않음)
    participation: float = 0.01  # 한 분 거래대금의 1% 까지만 체결
    limit_bp: float = 30.0  # 지정가 = 기준가 ± 0.3%
    attempts: int = 4  # IOC 처음 + 다시 3 번 (연속 분)
    fill: str = "open"  # open · worst(분 고가/저가, 한도 안)
    target_vol: float = 0.15
    max_weight: float = 0.5
    vol_days: int = 60
    band: float = 0.05
    stop_frac: float = 0.20  # 전날 종가 대비 −20% 비상 스톱
    stop_extra: float = 0.01  # 비상 스톱 추가 불리폭 1% (별도 보고)
    daily_loss: float = 0.02  # 그날 −2% 면 늘리기 금지 (줄이기 · 청산 허용)
    use_mark: bool = True  # funding 명목에 mark (False = 같은 분 거래가 근사)
    decide_hours: int = 4  # 판단 간격 (24 = 매일 00:00 UTC 만 — 213 · T/v2 와 같은 하루 한 번)
    sig_is_weight: bool = (
        False  # True 면 신호 값이 이미 목표 비중 (213 — 현물 가격으로 계산한 T/v2 비중)
    )
    equity0: float = 10_000.0


@dataclass
class Book:
    cash: float
    qty: dict[str, float]
    blocked: dict[str, bool]
    stop: dict[str, float]
    pnl_cost: dict[str, float]
    pnl_fund: dict[str, float]
    trade_open: dict[str, bool]
    trade_cash: dict[str, float]
    start: dict[str, int] = field(default_factory=dict)
    trades: list[tuple[str, int, int, float]] = field(default_factory=list)
    traded_notional: float = 0.0
    stops: int = 0
    stop_extra_cost: float = 0.0
    unfilled: int = 0
    loss_blocks: int = 0
    funding_proxy: int = 0
    fill_log: list[tuple[str, int, float]] = field(
        default_factory=list
    )  # (종목, 분, 부호 있는 명목)


def _vol_weight(dcl: Sequence[float], k: int, p: Params) -> float:
    lo = max(1, k - p.vol_days + 1)
    rets = [math.log(dcl[i] / dcl[i - 1]) for i in range(lo, k + 1)]
    if len(rets) < 20:
        return 0.0
    sig = statistics.stdev(rets) * math.sqrt(365)
    return min(p.max_weight, p.target_vol / sig) if sig > 0 else p.max_weight


def simulate(
    markets: dict[str, Market],
    sigs: dict[str, Signals],
    variant: str,
    p: Params,
    eval_start: int = EVAL_START,
) -> dict[str, Any]:
    """4 시간마다: funding(그 직전 수량) → 일 경계면 기록 · 스톱 갱신 → 신호 · 목표 비중 → IOC 지정가 4 번 → 칸 나머지 스톱 · funding."""
    syms = list(markets)
    n_min = len(markets[syms[0]].c)
    end_ms = WARM + n_min * MIN
    dcls = {s: daily_closes(markets[s]) for s in syms}
    bk = Book(
        p.equity0,
        dict.fromkeys(syms, 0.0),
        dict.fromkeys(syms, False),
        dict.fromkeys(syms, 0.0),
        dict.fromkeys(syms, 0.0),
        dict.fromkeys(syms, 0.0),
        dict.fromkeys(syms, False),
        dict.fromkeys(syms, 0.0),
    )
    fptr = dict.fromkeys(syms, 0)
    fund = {s: markets[s].funding for s in syms}
    daily: list[tuple[str, float, float]] = []  # (날, equity, 노출)
    switches = dict.fromkeys(syms, 0)
    prev_sig: dict[str, float | None] = dict.fromkeys(syms, None)
    day_start = p.equity0
    loss_block = False

    def price(s: str, i: int) -> float:
        return float(markets[s].c[i])

    def equity(i: int) -> float:
        return bk.cash + sum(bk.qty[s] * price(s, i) for s in syms)

    def pay_funding(s: str, upto_i: int) -> None:
        """정산 분이 upto_i 이하인 funding 을 그 직전 수량으로 (평가 시작 전은 건너뜀)."""
        ev = fund[s]
        while fptr[s] < len(ev) and (ev[fptr[s]][0] - WARM) // MIN <= upto_i:
            t, rate, mark = ev[fptr[s]]
            fptr[s] += 1
            if t < eval_start or bk.qty[s] == 0:
                continue
            i = (t - WARM) // MIN
            px = mark if (p.use_mark and mark is not None) else markets[s].c[max(0, i - 1)]
            if p.use_mark and mark is None:
                bk.funding_proxy += 1
            amt = bk.qty[s] * px * rate  # 롱: 양수 rate 면 냄
            bk.cash -= amt
            bk.pnl_fund[s] -= amt
            if bk.trade_open[s]:
                bk.trade_cash[s] -= amt

    def fill(s: str, i: int, q: float, px: float, extra: float = 0.0) -> None:
        notional = abs(q) * px
        fee = notional * p.cost + notional * extra
        bk.cash -= q * px + fee
        bk.pnl_cost[s] -= notional * p.cost
        bk.stop_extra_cost += notional * extra
        bk.traded_notional += notional
        bk.fill_log.append((s, i, q * px))
        if not bk.trade_open[s] and q > 0:
            bk.trade_open[s], bk.trade_cash[s] = True, 0.0
            bk.start[s] = i
        if bk.trade_open[s]:
            bk.trade_cash[s] += -(q * px) - fee
        bk.qty[s] += q
        if abs(bk.qty[s]) < 1e-12:
            bk.qty[s] = 0.0
        if bk.trade_open[s] and bk.qty[s] == 0:
            bk.trades.append((s, bk.start[s], i, bk.trade_cash[s]))
            bk.trade_open[s] = False

    def check_stop(s: str, i: int) -> bool:
        if bk.qty[s] <= 0 or bk.stop[s] <= 0:
            return False
        m = markets[s]
        if m.qv[i] > 0 and m.l[i] <= bk.stop[s]:
            px = min(bk.stop[s], m.o[i])
            fill(s, i, -bk.qty[s], px, p.stop_extra)
            bk.blocked[s] = True
            bk.stops += 1
            return True
        return False

    n_j = n_min * MIN // H4
    j0 = (eval_start - WARM) // H4
    step = H4 // MIN
    for j in range(j0, n_j):
        i0 = j * step
        d_ms = WARM + i0 * MIN
        for s in syms:  # 평가 시작 전 funding 포인터 건너뛰기 · 이번 칸 시작까지 정산
            pay_funding(s, i0)
        eq = equity(i0 - 1)
        if d_ms % DAY == 0:
            k = d_ms // DAY - WARM // DAY - 1
            expo = sum(bk.qty[s] * price(s, i0 - 1) for s in syms) / eq if eq > 0 else 0.0
            daily.append(
                (datetime.fromtimestamp((d_ms - DAY) / 1000, UTC).date().isoformat(), eq, expo)
            )
            day_start, loss_block = eq, False
            for s in syms:  # 비상 스톱 = 전날 종가 × 0.8 (보유 중일 때)
                bk.stop[s] = dcls[s][k] * (1 - p.stop_frac) if bk.qty[s] > 0 else 0.0
        elif eq < day_start * (1 - p.daily_loss):
            if not loss_block:
                bk.loss_blocks += 1
            loss_block = True
        k = d_ms // DAY - WARM // DAY - 1
        orders: dict[str, float] = {}
        decide = (j * 4) % p.decide_hours == 0
        for s in syms if decide else ():
            sig = sigs[s].by_j[variant][j]
            ps = prev_sig[s]
            if ps is not None and (sig > 0) != (ps > 0):
                switches[s] += 1
            prev_sig[s] = sig
            if bk.blocked[s]:
                if sig <= 0:
                    bk.blocked[s] = False
                sig = 0.0
            w = min(sig, p.max_weight) if p.sig_is_weight else _vol_weight(dcls[s], k, p) * sig
            ref = price(s, i0 - 1)
            cur_w = bk.qty[s] * ref / eq if eq > 0 else 0.0
            need = (
                (w == 0 and bk.qty[s] > 0) or (w > 0 and bk.qty[s] == 0) or abs(cur_w - w) > p.band
            )
            if not need:
                continue
            dq = w * eq / ref - bk.qty[s]
            if dq > 0 and loss_block:
                continue
            orders[s] = dq
        # 주문 칸 (연속 분, IOC)
        for a in range(p.attempts):
            i = i0 + a
            for s in syms:
                check_stop(s, i)
            for s in list(orders):
                q = orders[s]
                if bk.blocked[s] and q > 0:
                    del orders[s]
                    continue
                m = markets[s]
                ref = price(s, i0 - 1)
                lim = ref * (1 + p.limit_bp / 1e4) if q > 0 else ref * (1 - p.limit_bp / 1e4)
                ok = m.o[i] <= lim if q > 0 else m.o[i] >= lim
                if not ok or m.qv[i] <= 0:
                    continue
                worst = min(m.h[i], lim) if q > 0 else max(m.l[i], lim)
                px = m.o[i] if p.fill == "open" else worst
                cap = p.participation * m.qv[i] / px
                take = max(-cap, min(cap, q))
                if q < 0:
                    take = max(q, -cap, -bk.qty[s])
                if abs(take) <= 0:
                    continue
                fill(s, i, take, px)
                orders[s] = q - take
                if abs(orders[s]) * px < 1.0:
                    del orders[s]
                if bk.qty[s] > 0 and bk.stop[s] <= 0:
                    bk.stop[s] = dcls[s][k] * (1 - p.stop_frac)
        bk.unfilled += len(orders)
        # 칸의 나머지: funding 과 스톱 (분 단위 순서)
        end_i = min(i0 + step, n_min)
        for s in syms:
            m = markets[s]
            i = i0 + p.attempts
            while i < end_i:
                if bk.qty[s] > 0:
                    st = bk.stop[s]
                    lo_arr = m.l
                    qv = m.qv
                    hit = -1
                    for x in range(i, end_i):
                        if qv[x] > 0 and lo_arr[x] <= st:
                            hit = x
                            break
                    if hit < 0:
                        pay_funding(s, end_i - 1)
                        break
                    pay_funding(s, hit)
                    check_stop(s, hit)
                    i = hit + 1
                else:
                    pay_funding(s, end_i - 1)
                    break
    eq_end = equity(n_min - 1)
    daily.append(
        (datetime.fromtimestamp((end_ms - DAY) / 1000, UTC).date().isoformat(), eq_end, 0.0)
    )
    return {
        "daily": daily,
        "switches": switches,
        "book": bk,
    }


# ------------------------------------------------------------------ 통계


def returns(daily: Sequence[tuple[str, float, float]]) -> list[tuple[str, float]]:
    return [(daily[i][0], daily[i][1] / daily[i - 1][1] - 1) for i in range(1, len(daily))]


def metrics(daily: Sequence[tuple[str, float, float]]) -> dict[str, float]:
    r = [x for _, x in returns(daily)]
    eq = [d[1] for d in daily]
    years = len(r) / 365
    cagr = (eq[-1] / eq[0]) ** (1 / years) - 1 if years > 0 else 0.0
    sd = statistics.stdev(r) if len(r) > 1 else 0.0
    sharpe = statistics.fmean(r) / sd * math.sqrt(365) if sd > 0 else 0.0
    peak, mdd = eq[0], 0.0
    for x in eq:
        peak = max(peak, x)
        mdd = max(mdd, 1 - x / peak)
    expo = [d[2] for d in daily]
    return {
        "cagr": round(cagr * 100, 2),
        "sharpe": round(sharpe, 3),
        "mdd": round(mdd * 100, 2),
        "vol": round(sd * math.sqrt(365) * 100, 2),
        "final": round(eq[-1], 2),
        "avg_exposure": round(statistics.fmean(expo) * 100, 1),
        "max_exposure": round(max(expo) * 100, 1),
    }


def block_bootstrap(
    diff: Sequence[float],
    a: Sequence[float],
    b: Sequence[float],
    reps: int = 2000,
    block: int = 20,
    seed: int = 210,
) -> dict[str, Any]:
    """같은 날짜끼리 짝지은 일간 차이 — 원형 블록 부트스트랩(20 일). 평균 차이 · 샤프 차이."""
    rng = random.Random(seed)
    n = len(diff)

    def sh(x: Sequence[float]) -> float:
        s = statistics.pstdev(x)
        return statistics.fmean(x) / s * math.sqrt(365) if s > 0 else 0.0

    md, sd_ = [], []
    for _ in range(reps):
        idx: list[int] = []
        while len(idx) < n:
            st = rng.randrange(n)
            idx.extend((st + t) % n for t in range(block))
        idx = idx[:n]
        md.append(statistics.fmean(diff[t] for t in idx) * 365)
        sd_.append(sh([a[t] for t in idx]) - sh([b[t] for t in idx]))
    md.sort()
    sd_.sort()
    return {
        "mean_diff_annual_pct": round(statistics.fmean(diff) * 365 * 100, 2),
        "mean_diff_ci90": [
            round(md[int(0.05 * reps)] * 100, 2),
            round(md[int(0.95 * reps)] * 100, 2),
        ],
        "sharpe_diff": round(sh(a) - sh(b), 3),
        "sharpe_diff_ci90": [round(sd_[int(0.05 * reps)], 3), round(sd_[int(0.95 * reps)], 3)],
        "p_sharpe_diff_gt0": round(sum(1 for x in sd_ if x > 0) / reps, 3),
    }


# ------------------------------------------------------------------ 명령


def cmd_cache() -> int:
    mk = load_markets()
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    with CACHE.open("wb") as fh:
        pickle.dump(mk, fh, protocol=pickle.HIGHEST_PROTOCOL)
    for s, m in mk.items():
        miss_mark = sum(1 for f in m.funding if f[2] is None)
        print(
            f"{s}: 빠진 분 {m.missing:,} / {N_MIN:,} · funding {len(m.funding)} (mark 없음 {miss_mark})"
        )
    return 0


def _load() -> dict[str, Market]:
    import __main__

    if not hasattr(
        __main__, "Market"
    ):  # 캐시는 `-m` 실행(__main__)에서 만든다 — 다른 곳에서 읽을 때 같은 이름을 붙인다
        __main__.Market = Market
    with CACHE.open("rb") as fh:
        out: dict[str, Market] = pickle.load(fh)
        return out


def summarize(
    markets: dict[str, Market],
    sigs: dict[str, Signals],
    variant: str,
    p: Params,
    eval_start: int = EVAL_START,
) -> dict[str, Any]:
    res = simulate(markets, sigs, variant, p, eval_start=eval_start)
    daily = res["daily"]
    bk: Book = res["book"]
    assert isinstance(daily, list)
    met = metrics(daily)
    rets = returns(daily)
    eq0 = daily[0][1]
    years = len(rets) / 365
    trades = bk.trades
    pnls = [t[3] for t in trades]
    wins = [x for x in pnls if x > 0]
    losses = [x for x in pnls if x <= 0]
    streak = best = 0
    for x in pnls:
        streak = streak + 1 if x <= 0 else 0
        best = max(best, streak)
    by_year: dict[str, list[float]] = {}
    for d, r in rets:
        by_year.setdefault(d[:4], []).append(r)
    half = "2022-12-31"
    halves = {
        "A_2020-03_2022-12": [r for d, r in rets if d <= half],
        "B_2023-01_2026-08": [r for d, r in rets if d > half],
    }
    net = daily[-1][1] - eq0
    cost = -sum(bk.pnl_cost.values())
    fundp = -sum(bk.pnl_fund.values())
    hold_days = [(t[2] - t[1]) / (DAY // MIN) for t in trades]
    days_sorted = sorted((r * daily[i][1] for i, (_, r) in enumerate(rets)), reverse=True)
    top5 = sum(days_sorted[: max(1, len(days_sorted) // 20)])
    sym_trades = {s: [t[3] for t in trades if t[0] == s] for s in markets}
    return {
        **met,
        "net_pnl": round(net, 2),
        "gross_pnl": round(net + cost + fundp + bk.stop_extra_cost, 2),
        "cost": round(cost, 2),
        "funding": round(fundp, 2),
        "stop_extra": round(bk.stop_extra_cost, 2),
        "turnover_x_per_year": round(
            bk.traded_notional / statistics.fmean(d[1] for d in daily) / years, 2
        ),
        "switches": res["switches"],
        "trades": len(trades),
        "win_pct": round(len(wins) / len(pnls) * 100, 1) if pnls else 0.0,
        "avg_win": round(statistics.fmean(wins), 2) if wins else 0.0,
        "avg_loss": round(statistics.fmean(losses), 2) if losses else 0.0,
        "max_losing_streak": best,
        "hold_days_median": round(statistics.median(hold_days), 1) if hold_days else 0.0,
        "trade_pnl_by_symbol": {s: round(sum(v), 2) for s, v in sym_trades.items()},
        "top3_trades_share_pct": round(sum(sorted(pnls, reverse=True)[:3]) / net * 100, 1)
        if net > 0
        else None,
        "top5pct_days_share_pct": round(top5 / net * 100, 1) if net > 0 else None,
        "stops": bk.stops,
        "unfilled_orders": bk.unfilled,
        "daily_loss_blocks": bk.loss_blocks,
        "funding_mark_proxy": bk.funding_proxy,
        "by_year_return_pct": {
            y: round((math.prod(1 + x for x in v) - 1) * 100, 1) for y, v in sorted(by_year.items())
        },
        "halves_sharpe": {
            k: round(statistics.fmean(v) / statistics.stdev(v) * math.sqrt(365), 3)
            for k, v in halves.items()
        },
        "_returns": rets,
    }


def verdict(
    cand: dict[str, Any], base: dict[str, Any], sens_same_sign: bool
) -> tuple[str, dict[str, Any]]:
    """문서 210 §7 — 결과 보기 전 고정."""
    a = [r for _, r in cand["_returns"]]
    b = [r for _, r in base["_returns"]]
    diff = [x - y for x, y in zip(a, b, strict=True)]
    bs = block_bootstrap(diff, a, b)
    ha, hb = cand["halves_sharpe"], base["halves_sharpe"]
    halves_pos = all(ha[k] > hb[k] for k in ha)
    halves_neg = all(ha[k] < hb[k] for k in ha)
    mdd_ok = cand["mdd"] <= base["mdd"] + 5
    p = bs["p_sharpe_diff_gt0"]
    if not sens_same_sign:
        word = "판단 불가"
    elif bs["sharpe_diff"] > 0 and p >= 0.9 and halves_pos and mdd_ok:
        word = "유리"
    elif bs["sharpe_diff"] < 0 and (p <= 0.1 or halves_neg):
        word = "불리"
    else:
        word = "판단 불가"
    return word, {
        **bs,
        "halves_better": halves_pos,
        "halves_worse": halves_neg,
        "mdd_within_5pp": mdd_ok,
    }


SENS = {
    "main": Params(),
    "cost_x2": Params(cost=0.0019),
    "cost_realistic_ref": Params(cost=0.00023),
    "fill_worst": Params(fill="worst"),
    "participation_0.1pct": Params(participation=0.001),
    "funding_price_proxy": Params(use_mark=False),
}


def qb_durations(m: Market) -> dict[str, Any]:
    bars = qb_bars(m, daily_quote(m))
    dur = [(b[0] - b[2]) / 3_600_000 for b in bars if b[2] >= EVAL_START]
    by_year: dict[str, list[float]] = {}
    for b in bars:
        if b[2] >= EVAL_START:
            by_year.setdefault(datetime.fromtimestamp(b[2] / 1000, UTC).strftime("%Y"), []).append(
                (b[0] - b[2]) / 3_600_000
            )
    dur.sort()
    return {
        "bars": len(dur),
        "hours_median": round(statistics.median(dur), 1),
        "hours_p10": round(dur[len(dur) // 10], 1),
        "hours_p90": round(dur[9 * len(dur) // 10], 1),
        "hours_min": round(dur[0], 2),
        "hours_max": round(dur[-1], 1),
        "by_year_median_hours": {
            y: round(statistics.median(v), 1) for y, v in sorted(by_year.items())
        },
    }


def cmd_power() -> int:
    """검정력 메모 — 공식만 (전략 수익은 계산하지 않는다).

    같은 계열 두 전략의 연 샤프 차이 SE ≈ sqrt(2(1 − ρ) / T), T = 년, ρ = 두 전략 일간 수익 상관.
    """
    years = (END - EVAL_START) / DAY / 365.25
    for rho in (0.6, 0.7, 0.8, 0.9):
        se = math.sqrt(2 * (1 - rho) / years)
        need = (2.0 + 0.84) ** 2 * 2 * (1 - rho) / 0.3**2
        mde = (2.0 + 0.84) * se
        print(
            f"ρ {rho}: {years:.2f} 년 · 샤프 차이 SE {se:.2f} · 80% 로 증명 가능한 최소 차이 {mde:.2f} · 차이 0.3 이면 {need:.0f} 년 필요"
        )
    return 0


def cmd_run() -> int:
    mk = _load()
    sigs = {s: build_signals(m) for s, m in mk.items()}
    out: dict[str, Any] = {"params": asdict(Params()), "made": datetime.now(UTC).isoformat()}
    runs: dict[str, dict[str, dict[str, Any]]] = {}
    for sname, p in SENS.items():
        runs[sname] = {v: summarize(mk, sigs, v, p) for v in ("T20", "QB", "DC", "TV2REF")}
    res: dict[str, Any] = {}
    for cand in ("QB", "DC"):
        signs = []
        for sname in SENS:
            if sname == "cost_realistic_ref":
                continue
            a = runs[sname][cand]["sharpe"]
            b = runs[sname]["T20"]["sharpe"]
            signs.append(a > b)
        same = all(signs) or not any(signs)
        word, stat = verdict(runs["main"][cand], runs["main"]["T20"], same)
        res[cand] = {
            "pilot": word,
            "vs_T20": stat,
            "sens_sharpe_diff": {
                s: round(runs[s][cand]["sharpe"] - runs[s]["T20"]["sharpe"], 3) for s in SENS
            },
        }
    out["pilot"] = res
    out["runs"] = {
        s: {v: {k: x for k, x in d.items() if k != "_returns"} for v, d in vs.items()}
        for s, vs in runs.items()
    }
    out["qb_bar_hours"] = {s: qb_durations(m) for s, m in mk.items()}
    out["data"] = {
        s: {
            "missing_minutes": m.missing,
            "funding_rows": len(m.funding),
            "funding_mark_missing": sum(1 for f in m.funding if f[2] is None),
        }
        for s, m in mk.items()
    }
    corr = {}
    for cand in ("QB", "DC", "TV2REF"):
        a = [r for _, r in runs["main"][cand]["_returns"]]
        b = [r for _, r in runs["main"]["T20"]["_returns"]]
        corr[cand] = round(statistics.correlation(a, b), 3)
    out["daily_corr_with_T20"] = corr
    REPORT.mkdir(parents=True, exist_ok=True)
    (REPORT / "result.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1, default=str), encoding="utf-8"
    )
    m = runs["main"]
    for v in ("T20", "QB", "DC", "TV2REF"):
        d = m[v]
        print(
            v,
            {
                k: d[k]
                for k in (
                    "cagr",
                    "sharpe",
                    "mdd",
                    "net_pnl",
                    "cost",
                    "funding",
                    "trades",
                    "avg_exposure",
                    "turnover_x_per_year",
                    "stops",
                )
            },
        )
    print(json.dumps(res, ensure_ascii=False))
    print("상관", corr)
    return 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=("cache", "power", "run"))
    a = ap.parse_args(argv)
    return {"cache": cmd_cache, "power": cmd_power, "run": cmd_run}[a.cmd]()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))


__all__ = ["Market", "Params", "dc_states", "qb_bars", "replace", "simulate"]
