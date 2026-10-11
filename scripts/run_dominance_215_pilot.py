# ruff: noqa: E501, N803, N806, RUF007 — 한국어 설명이 길고, 수식 변수(X · L · A · B · C)는 대문자로 둔다
"""문서 215 §5 ~ §7 — A · B · C 신호의 24 시간 BTC 선물 롱 파일럿 (계좌 10,000 USDT). 주문 없음 · 독립 원장.

엔진: BTCUSDT USD-M 1 분봉 · funding · mark(210 캐시). 신호는 ``run_dominance_215.signals``.
"""

from __future__ import annotations

import csv
import json
import math
import random
import statistics
from collections import Counter
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from typing import Any

from scripts.run_dominance_215 import END, H4, REPORT, START, aligned, signals
from scripts.run_qb_dc_210 import DAY, MIN, WARM, Market, Params, Signals, _load, simulate

DELAY_MIN = 5  # 4 시간봉 완성 뒤 5 분 (지수 계산 · 게시 여유) 부터 주문
ATTEMPTS = 4
HOLD = 24 * 60  # 분
EMERG_WAIT = 60  # 청산 4 번이 덜 차면 60 분 뒤 비상 정리


@dataclass(frozen=True, slots=True)
class P:
    cost: float = 0.00095
    limit: float = 0.003
    participation: float = 0.01
    fill: str = "open"  # open · worst
    extra: float = 0.01
    risk: float = 0.0025
    stop_atr: float = 2.0
    max_notional: float = 1.0
    step: float = 0.001  # BTCUSDT LOT_SIZE stepSize = minQty (거래소 정보 2026-10-11)
    min_qty: float = 0.001
    min_notional: float = 50.0  # MIN_NOTIONAL (같은 출처, 지금 규격)
    daily_loss: float = 0.02
    halt: float = 0.30
    use_halt: bool = True
    equity0: float = 10_000.0


@dataclass
class Pos:
    qty: float = 0.0
    avg: float = 0.0
    stop: float = 0.0
    first_fill: int = -1
    exit_due: int = -1
    exiting: bool = False
    exit_tries: int = 0
    emerg_at: int = -1
    entry_i: int = -1
    cash_at_entry: float = 0.0
    fees: float = 0.0
    funding: float = 0.0
    extra: float = 0.0


@dataclass
class Book:
    cash: float
    pos: Pos = field(default_factory=Pos)
    trades: list[dict[str, Any]] = field(default_factory=list)
    skips: Counter[str] = field(default_factory=Counter)
    fees: float = 0.0
    funding: float = 0.0
    extra: float = 0.0
    traded: float = 0.0
    halted: str | None = None
    loss_block_days: int = 0
    fund_proxy: int = 0


def floor_step(q: float, step: float) -> float:
    return math.floor(q / step + 1e-9) * step


def atr20_4h(m: Market, t_ms: int) -> float | None:
    """t_ms 이전에 끝난 선물 4 시간봉 20 개의 true range 평균 (1 분봉에서)."""
    end_i = (t_ms - WARM) // MIN
    bars = []
    for k in range(21, 0, -1):
        a, b = end_i - k * 240, end_i - (k - 1) * 240
        if a < 0:
            return None
        bars.append((max(m.h[a:b]), min(m.l[a:b]), m.c[b - 1]))
    tr = [
        max(h - lo, abs(h - pc), abs(lo - pc))
        for (_ph, _pl, pc), (h, lo, _c) in zip(bars[:-1], bars[1:], strict=True)
    ]
    return statistics.fmean(tr)


def simulate_arm(
    m: Market, sig: list[tuple[int, bool]], p: P, start: int = START, end: int = END
) -> dict[str, Any]:
    """sig = [(판단 ms, 신호 ON)] 시각순. 24 시간 보유 · 2ATR 보호 · OFF→ON 에서만 진입."""
    bk = Book(p.equity0)
    fev = [(t, r, mk) for t, r, mk in m.funding if start <= t < end]
    fptr = 0
    i_start, i_end = (start - WARM) // MIN, (end - WARM) // MIN
    on_at = {(t - WARM) // MIN + DELAY_MIN: on for t, on in sig}
    prev_on = False
    peak = p.equity0
    day_start = p.equity0
    day_blocked = False
    daily: list[tuple[str, float, float]] = []

    def eq(i: int) -> float:
        return bk.cash + bk.pos.qty * m.c[i - 1]

    def fill(i: int, q: float, px: float, extra: float = 0.0) -> None:
        nt = abs(q) * px
        fee, ext = nt * p.cost, nt * extra
        bk.cash -= q * px + fee + ext
        bk.fees += fee
        bk.extra += ext
        bk.traded += nt
        ps = bk.pos
        ps.fees += fee
        ps.extra += ext
        if q > 0:
            ps.avg = (ps.avg * ps.qty + px * q) / (ps.qty + q)
        ps.qty = max(0.0, ps.qty + q)
        if ps.qty < 1e-12:
            ps.qty = 0.0

    def close_trade(i: int, reason: str) -> None:
        ps = bk.pos
        pnl = bk.cash - ps.cash_at_entry
        bk.trades.append(
            {
                "entry_i": ps.entry_i,
                "first_fill_i": ps.first_fill,
                "exit_i": i,
                "reason": reason,
                "pnl": pnl,
                "fees": ps.fees,
                "funding": ps.funding,
                "extra": ps.extra,
                "avg": ps.avg,
            }
        )
        bk.pos = Pos()

    i = i_start
    while i < i_end:
        t_ms = WARM + i * MIN
        if t_ms % DAY == 0:
            e = eq(i)
            daily.append(
                (
                    datetime.fromtimestamp((t_ms - DAY) / 1000, UTC).date().isoformat(),
                    e,
                    bk.pos.qty * m.c[i - 1] / e if e > 0 else 0.0,
                )
            )
            peak = max(peak, e)
            day_start, day_blocked = e, False
            if p.use_halt and bk.halted is None and e <= peak * (1 - p.halt):
                bk.halted = datetime.fromtimestamp(t_ms / 1000, UTC).date().isoformat()
        # funding (그 분 시작 보유 수량, 체결보다 먼저)
        while fptr < len(fev) and (fev[fptr][0] - WARM) // MIN <= i:
            ft, rate, mk = fev[fptr]
            fptr += 1
            if bk.pos.qty > 0:
                px = mk if mk is not None else m.c[(ft - WARM) // MIN - 1]
                bk.fund_proxy += mk is None
                amt = bk.pos.qty * px * rate
                bk.cash -= amt
                bk.funding += amt
                bk.pos.funding += amt
        ps = bk.pos
        # 보호 (첫 체결 다음 분부터) — 청산 시도 중에도 유효
        if ps.qty > 0 and ps.stop > 0 and i > ps.first_fill and m.qv[i] > 0 and m.l[i] <= ps.stop:
            fill(i, -ps.qty, min(ps.stop, m.o[i]), p.extra)
            close_trade(i, "보호 청산")
            ps = bk.pos
        # 24 시간 청산
        if ps.qty > 0 and not ps.exiting and ps.exit_due >= 0 and i >= ps.exit_due:
            ps.exiting, ps.exit_tries, ps.emerg_at = True, 0, i + EMERG_WAIT
        if ps.qty > 0 and ps.exiting:
            if i >= ps.emerg_at:
                fill(i, -ps.qty, m.o[i], p.extra)
                close_trade(i, "시간 청산(비상)")
            elif ps.exit_tries < ATTEMPTS:
                ps.exit_tries += 1
                ref = m.c[i - 1]
                lim = ref * (1 - p.limit)
                if m.qv[i] > 0 and m.o[i] >= lim:
                    px = m.o[i] if p.fill == "open" else max(m.l[i], lim)
                    q = min(ps.qty, p.participation * m.qv[i] / px)
                    q = ps.qty if q >= ps.qty - 1e-12 else floor_step(q, p.step)
                    if q > 0:
                        fill(i, -q, px)
                if bk.pos.qty == 0:
                    close_trade(i, "시간 청산")
            ps = bk.pos
        # 신호 판단 (봉 완성 + 5 분)
        if i in on_at:
            on = on_at[i]
            if on and not prev_on:
                e = eq(i)
                if bk.pos.qty > 0 or bk.pos.entry_i >= 0:
                    bk.skips["보유 중 신호"] += 1
                elif p.use_halt and bk.halted:
                    bk.skips["계좌 30% 정지"] += 1
                elif e < day_start * (1 - p.daily_loss):
                    if not day_blocked:
                        bk.loss_block_days += 1
                    day_blocked = True
                    bk.skips["일 손실 2%"] += 1
                else:
                    atr = atr20_4h(m, t_ms - DELAY_MIN * MIN)
                    ref = m.c[i - 1]
                    if atr is None or atr <= 0:
                        bk.skips["ATR 없음"] += 1
                    else:
                        qty = floor_step(
                            min(p.risk * e / (p.stop_atr * atr), p.max_notional * e / ref), p.step
                        )
                        if qty < p.min_qty or qty * ref < p.min_notional:
                            bk.skips["수량 미달(사전 검사)"] += 1
                        else:
                            ps = bk.pos
                            ps.entry_i, ps.cash_at_entry = i, bk.cash
                            want = qty
                            for a in range(ATTEMPTS):
                                k = i + a
                                lim = ref * (1 + p.limit)
                                if m.qv[k] > 0 and m.o[k] <= lim:
                                    px = m.o[k] if p.fill == "open" else min(m.h[k], lim)
                                    q = floor_step(
                                        min(want - ps.qty, p.participation * m.qv[k] / px), p.step
                                    )
                                    if q > 0:
                                        fill(k, q, px)
                                        if ps.first_fill < 0:
                                            ps.first_fill = k
                                if ps.qty >= want - 1e-12:
                                    break
                            if ps.qty == 0:
                                bk.skips["미체결"] += 1
                                bk.pos = Pos()
                            else:
                                ps.stop = ps.avg - p.stop_atr * atr
                                ps.exit_due = ps.first_fill + HOLD
            prev_on = on
        i += 1
    if bk.pos.qty > 0:  # 평가 끝: 남은 포지션은 마지막 분 종가로 평가만 (미래 가격으로 끝내지 않음)
        bk.skips["평가 끝 보유 중(평가만)"] += 1
    daily.append(
        (datetime.fromtimestamp((end - DAY) / 1000, UTC).date().isoformat(), eq(i_end), 0.0)
    )
    return {"daily": daily, "book": bk}


# ------------------------------------------------------------------ 통계


def rets(daily: list[tuple[str, float, float]]) -> list[tuple[str, float]]:
    return [(daily[k][0], daily[k][1] / daily[k - 1][1] - 1) for k in range(1, len(daily))]


def stats(res: dict[str, Any]) -> dict[str, Any]:
    d, bk = res["daily"], res["book"]
    r = [x for _, x in rets(d)]
    eqs = [x[1] for x in d]
    years = len(r) / 365
    sd = statistics.stdev(r)
    peak, mdd = eqs[0], 0.0
    for x in eqs:
        peak = max(peak, x)
        mdd = max(mdd, 1 - x / peak)
    pnl = [t["pnl"] for t in bk.trades]
    streak = best = 0
    for x in pnl:
        streak = streak + 1 if x <= 0 else 0
        best = max(best, streak)
    return {
        "net": round(eqs[-1] - eqs[0], 2),
        "cagr_pct": round(((eqs[-1] / eqs[0]) ** (1 / years) - 1) * 100, 3),
        "arith_ann_pct": round(statistics.fmean(r) * 365 * 100, 3),
        "sharpe": round(statistics.fmean(r) / sd * math.sqrt(365), 3) if sd > 0 else 0.0,
        "mdd_pct": round(mdd * 100, 2),
        "avg_exposure_pct": round(statistics.fmean(x[2] for x in d) * 100, 2),
        "max_exposure_pct": round(max(x[2] for x in d) * 100, 2),
        "turnover_x_per_year": round(bk.traded / statistics.fmean(eqs) / years, 2),
        "trades": len(pnl),
        "win_pct": round(sum(1 for x in pnl if x > 0) / len(pnl) * 100, 1) if pnl else 0.0,
        "avg_trade": round(statistics.fmean(pnl), 3) if pnl else 0.0,
        "median_trade": round(statistics.median(pnl), 3) if pnl else 0.0,
        "max_losing_streak": best,
        "fees": round(bk.fees, 2),
        "funding": round(bk.funding, 2),
        "emergency_extra": round(bk.extra, 2),
        "gross_before_costs": round(eqs[-1] - eqs[0] + bk.fees + bk.funding + bk.extra, 2),
        "exit_reasons": dict(Counter(t["reason"] for t in bk.trades)),
        "skips": dict(bk.skips),
        "halted": bk.halted,
        "loss_block_days": bk.loss_block_days,
        "funding_mark_proxy": bk.fund_proxy,
        "by_year_net": {
            y: round(
                sum(
                    t["pnl"]
                    for t in bk.trades
                    if datetime.fromtimestamp((WARM + t["exit_i"] * MIN) / 1000, UTC).year == int(y)
                ),
                2,
            )
            for y in ("2025", "2026")
        },
    }


MID = "2025-10-31"  # 2025-01-01 ~ 2026-08-31 (608 일)을 시간순 같은 길이로 나눈 경계(앞 304 일 끝)


def paired(
    a: list[tuple[str, float]],
    b: list[tuple[str, float]],
    block: int,
    reps: int = 2000,
    seed: int = 215,
) -> dict[str, Any]:
    d = [x - y for (_, x), (_, y) in zip(a, b, strict=True)]
    days = [k for k, _ in a]
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
    h0 = [v for k, v in zip(days, d, strict=True) if k <= MID]
    h1 = [v for k, v in zip(days, d, strict=True) if k > MID]
    return {
        "block_days": block,
        "mean_diff_ann_pct": round(statistics.fmean(d) * 365 * 100, 3),
        "ci90_ann_pct": [
            round(means[int(0.05 * reps)] * 100, 3),
            round(means[int(0.95 * reps)] * 100, 3),
        ],
        "boot_share_gt0": round(sum(1 for x in means if x > 0) / reps, 3),
        "halves_ann_pct": [
            round(statistics.fmean(h0) * 365 * 100, 3),
            round(statistics.fmean(h1) * 365 * 100, 3),
        ],
        "days_nonzero_diff": sum(1 for x in d if abs(x) > 1e-12),
    }


def nw_ols(y: list[float], X: list[list[float]], lag: int = 6) -> list[tuple[float, float]]:
    """OLS 계수와 Newey-West(lag) t. X 에 상수 포함."""
    k = len(X[0])
    xtx = [[sum(r[a] * r[b] for r in X) for b in range(k)] for a in range(k)]
    xty = [sum(r[a] * yy for r, yy in zip(X, y, strict=True)) for a in range(k)]
    inv = _inv(xtx)
    beta = [sum(inv[a][b] * xty[b] for b in range(k)) for a in range(k)]
    e = [
        yy - sum(bb * xx for bb, xx in zip(beta, r, strict=True))
        for r, yy in zip(X, y, strict=True)
    ]
    s = [[0.0] * k for _ in range(k)]
    n = len(y)
    for L in range(lag + 1):
        w = 1.0 if L == 0 else 1 - L / (lag + 1)
        for t in range(L, n):
            for a in range(k):
                for b in range(k):
                    v = X[t][a] * e[t] * X[t - L][b] * e[t - L]
                    s[a][b] += (
                        w * v if L == 0 else w * (v + X[t - L][a] * e[t - L] * X[t][b] * e[t])
                    )
    cov = [
        [sum(inv[a][c] * s[c][d] * inv[d][b] for c in range(k) for d in range(k)) for b in range(k)]
        for a in range(k)
    ]
    return [(beta[a], beta[a] / math.sqrt(cov[a][a]) if cov[a][a] > 0 else 0.0) for a in range(k)]


def _inv(a: list[list[float]]) -> list[list[float]]:
    n = len(a)
    m = [row[:] + [1.0 if i == j else 0.0 for j in range(n)] for i, row in enumerate(a)]
    for c in range(n):
        piv = max(range(c, n), key=lambda r: abs(m[r][c]))
        m[c], m[piv] = m[piv], m[c]
        pv = m[c][c]
        m[c] = [x / pv for x in m[c]]
        for r in range(n):
            if r != c:
                f = m[r][c]
                m[r] = [x - f * y for x, y in zip(m[r], m[c], strict=True)]
    return [row[n:] for row in m]


def predict(m: Market, sg: list[tuple[int, bool, bool, bool]]) -> dict[str, Any]:
    """§7 ② — 판단 시각 T(+5 분) 시가 → 24 시간 뒤 시가 수익을 신호 · 직전 4 시간 수익 · 직전 24 시간 실현 변동성으로. NW(6)."""
    rows = []
    for t, a, b, c in sg:
        i0 = (t - WARM) // MIN + DELAY_MIN
        i1 = i0 + HOLD
        if i1 >= (END - WARM) // MIN:
            continue
        fwd = m.o[i1] / m.o[i0] - 1
        r4 = (
            m.c[i0 - DELAY_MIN - 1] / m.c[i0 - DELAY_MIN - 241] - 1
        )  # 신호 봉 자체 수익(이후 수익에 넣지 않음)
        hr = [
            math.log(m.c[i0 - DELAY_MIN - 1 - 60 * k] / m.c[i0 - DELAY_MIN - 61 - 60 * k])
            for k in range(24)
        ]
        vol = statistics.pstdev(hr)
        rows.append((fwd, a, b, c, r4, vol))
    out: dict[str, Any] = {
        "n_bars": len(rows),
        "mean_fwd_all_bp": round(statistics.fmean(x[0] for x in rows) * 1e4, 2),
    }
    for name, col in (("A", 1), ("B", 2), ("C", 3)):
        on = [x[0] for x in rows if x[col]]
        off = [x[0] for x in rows if not x[col]]
        y = [x[0] for x in rows]
        X = [[1.0, float(x[col]), x[4], x[5]] for x in rows]
        co = nw_ols(y, X)
        out[name] = {
            "n_on": len(on),
            "mean_fwd_on_bp": round(statistics.fmean(on) * 1e4, 2),
            "mean_fwd_off_bp": round(statistics.fmean(off) * 1e4, 2),
            "coef_signal_bp": round(co[1][0] * 1e4, 2),
            "t_nw6": round(co[1][1], 2),
            "coef_r4": round(co[2][0], 4),
            "t_r4": round(co[2][1], 2),
        }
    y = [x[0] for x in rows]
    X = [
        [1.0, float(x[1] and not x[3]), float(x[3] and not x[1]), float(x[1] and x[3]), x[4], x[5]]
        for x in rows
    ]
    co = nw_ols(y, X)
    out["A_vs_C_split"] = {
        "A_only_n": sum(1 for x in rows if x[1] and not x[3]),
        "C_only_n": sum(1 for x in rows if x[3] and not x[1]),
        "coef_A_only_bp": round(co[1][0] * 1e4, 1),
        "t_A_only": round(co[1][1], 2),
        "coef_C_only_bp": round(co[2][0] * 1e4, 1),
        "t_C_only": round(co[2][1], 2),
        "coef_both_bp": round(co[3][0] * 1e4, 1),
        "t_both": round(co[3][1], 2),
        "A_only_mean_fwd_bp": round(
            statistics.fmean([x[0] for x in rows if x[1] and not x[3]] or [0]) * 1e4, 1
        ),
        "C_only_mean_fwd_bp": round(
            statistics.fmean([x[0] for x in rows if x[3] and not x[1]] or [0]) * 1e4, 1
        ),
    }
    return out


SENS: dict[str, dict[str, Any]] = {
    "main": {},
    "cost_x2": {"cost": 0.0019},
    "fill_worst": {"fill": "worst"},
    "participation_0.1pct": {"participation": 0.001},
    "cost_realistic_ref": {"cost": 0.00023},
    "no_halt_diag": {"use_halt": False},
}


def _iso(k: int) -> str:
    return datetime.fromtimestamp((WARM + k * MIN) / 1000, UTC).isoformat() if k >= 0 else ""


def run() -> int:
    mk = _load()
    m = mk["BTCUSDT"]
    sg = [s for s in signals(aligned()) if START + H4 <= s[0] <= END - H4]
    arms = {
        "A": [(t, a) for t, a, _b, _c in sg],
        "B": [(t, b) for t, _a, b, _c in sg],
        "C": [(t, c) for t, _a, _b, c in sg],
    }
    out: dict[str, Any] = {
        "made": datetime.now(UTC).isoformat(),
        "params": asdict(P()),
        "delay_min": DELAY_MIN,
        "decisions": len(sg),
    }
    res: dict[str, dict[str, Any]] = {}
    raw: dict[str, dict[str, Any]] = {}
    for sname, over in SENS.items():
        res[sname] = {}
        for arm, s in arms.items():
            r = simulate_arm(m, s, replace(P(), **over))
            res[sname][arm] = stats(r)
            res[sname][arm]["_r"] = rets(r["daily"])
            if sname == "main":
                raw[arm] = r
        print(
            sname,
            {
                a: (res[sname][a]["net"], res[sname][a]["sharpe"], res[sname][a]["trades"])
                for a in arms
            },
            flush=True,
        )
    comp: dict[str, Any] = {}
    for x, y in (("A", "C"), ("A", "B")):
        comp[f"{x}-{y}"] = {
            f"block{b}": paired(res["main"][x]["_r"], res["main"][y]["_r"], b) for b in (14, 7, 30)
        }
    comp["A-C_sens_mean_ann_pct"] = {
        s: round(
            (
                statistics.fmean(v for _, v in res[s]["A"]["_r"])
                - statistics.fmean(v for _, v in res[s]["C"]["_r"])
            )
            * 365
            * 100,
            3,
        )
        for s in SENS
    }
    out["comparisons"] = comp
    out["prediction"] = predict(m, sg)
    out["runs"] = {
        s: {a: {k: v for k, v in d.items() if k != "_r"} for a, d in vs.items()}
        for s, vs in res.items()
    }
    # T/v2 참고 (213 TSIGN 엔진, 같은 기간)
    try:
        from scripts.run_tcont_213 import BASE, build, spot_closes

        sigs, _ = build(mk, spot_closes())
        tv = simulate(mk, sigs, "TSIGN", BASE, eval_start=START)
        e = [x[1] for x in tv["daily"]]
        rr = [e[k] / e[k - 1] - 1 for k in range(1, len(e))]
        out["tv2_reference"] = {
            "net": round(e[-1] - e[0], 2),
            "arith_ann_pct": round(statistics.fmean(rr) * 365 * 100, 2),
            "sharpe": round(statistics.fmean(rr) / statistics.stdev(rr) * math.sqrt(365), 3),
            "note": "BTC+ETH, 213 TSIGN 엔진, 참고만",
        }
    except Exception as exc:  # 참고 줄 실패는 판단에 영향 없음
        out["tv2_reference"] = {"error": str(exc)[:200]}
    # 결론 (문서 215 §6.8 — 결과 전 고정)
    a_net = res["main"]["A"]["net"]
    ac = comp["A-C"]["block14"]
    if a_net <= 0:
        word = "불리"
    elif ac["ci90_ann_pct"][0] <= 0 <= ac["ci90_ann_pct"][1] and abs(ac["mean_diff_ann_pct"]) < 1.0:
        word = "추가 가치 없음"
    elif (
        ac["mean_diff_ann_pct"] >= 1.0
        and ac["ci90_ann_pct"][0] > 0
        and all(h > 0 for h in ac["halves_ann_pct"])
    ):
        word = "향후 관찰 후보"
    elif ac["ci90_ann_pct"][1] < 0 and all(h < 0 for h in ac["halves_ann_pct"]):
        word = "불리"
    else:
        word = "판단 불가"
    out["conclusion"] = word
    REPORT.mkdir(parents=True, exist_ok=True)
    (REPORT / "result.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1, default=str), encoding="utf-8"
    )
    for arm, r in raw.items():
        with (REPORT / f"daily_{arm}.csv").open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["date", "equity", "exposure"])
            w.writerows(r["daily"])
        with (REPORT / f"trades_{arm}.csv").open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(
                [
                    "entry_utc",
                    "first_fill_utc",
                    "exit_utc",
                    "reason",
                    "pnl",
                    "fees",
                    "funding",
                    "extra",
                    "avg",
                ]
            )
            for t in r["book"].trades:
                w.writerow(
                    [
                        _iso(t["entry_i"]),
                        _iso(t["first_fill_i"]),
                        _iso(t["exit_i"]),
                        t["reason"],
                        round(t["pnl"], 4),
                        round(t["fees"], 4),
                        round(t["funding"], 4),
                        round(t["extra"], 4),
                        round(t["avg"], 2),
                    ]
                )
    with (REPORT / "signals.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["decision_utc", "A", "B", "C"])
        w.writerows(
            [
                (datetime.fromtimestamp(t / 1000, UTC).isoformat(), int(a), int(b), int(c))
                for t, a, b, c in sg
            ]
        )
    for arm in arms:
        d = res["main"][arm]
        print(
            arm,
            {
                k: d[k]
                for k in (
                    "net",
                    "cagr_pct",
                    "arith_ann_pct",
                    "sharpe",
                    "mdd_pct",
                    "trades",
                    "win_pct",
                    "fees",
                    "funding",
                    "emergency_extra",
                    "exit_reasons",
                    "skips",
                )
            },
        )
    print("A-C", comp["A-C"]["block14"], "\nA-B", comp["A-B"]["block14"], "\n결론", word)
    print(json.dumps(out["prediction"], ensure_ascii=False))
    return 0


__all__ = ["P", "Params", "Signals", "atr20_4h", "simulate_arm"]
