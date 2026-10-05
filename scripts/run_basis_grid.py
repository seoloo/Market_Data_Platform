"""문서 149 — 베이시스 그리드 (BTC 무기한 선물 ÷ 현물 − 1). 1회 판정.

사용 예::

    python -m scripts.run_basis_grid hash   # 맞춘 베이시스 계열 SHA-256 (수익 계산 없음)
    python -m scripts.run_basis_grid run    # 깨끗한 트리 · 해시 일치 · 판정 파일 없음일 때만

칸 k(1…5) = 베이시스 k·s. 베이시스가 칸 이상이면 열고(현물 매수 + 같은 수량 선물 매도), 한 칸 아래로
오면 닫는다. 그 분 종가로 정하고 다음 분 종가에 체결. 연 칸은 8 시간마다 펀딩을 받는다(음수면 낸다).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import statistics
import sys
from array import array
from collections.abc import Mapping, Sequence
from contextlib import closing
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scripts.run_a1_train import git_state
from scripts.run_grid_upbit import load_minutes

REPO = Path(__file__).resolve().parent.parent
DB = REPO / "data" / "trading.db"
OUT = REPO / "reports" / "basis_grid"
FROM, SPLIT, TO = "2020-01-01", "2023-01-01", "2026-09-01"
LEVELS = 5
MARGIN = 0.5
"""칸당 선물 증거금 = 명목의 절반(선물 2 배)."""
MAIN_STEP = 0.003
STEPS = (0.002, 0.003, 0.005)
COSTS = {
    "limit": (0.001, 0.0002),
    "market": (0.001, 0.0005),
    "bnb": (0.00075, 0.00018),
}
"""(현물, 선물) 한 번 체결 비용. 판정은 지정가."""
EXPECTED_SERIES = "d46c5a64bdc2a17db6f7bb3405ff65cddc892f4a5f400feaa8a7815d3221ad1f"
"""``hash`` 뒤(수익 보기 전) 고정한다."""
DAY_MS = 86_400_000


def _ms(text: str) -> int:
    return int(datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=UTC).timestamp() * 1000)


@dataclass(slots=True)
class Series:
    t: array[int]
    spot: array[float]
    perp: array[float]


def load_series() -> Series:
    bars = load_minutes("BTCUSDT")
    lo, hi = _ms(FROM), _ms(TO)
    spot = {t: c for t, c in zip(bars.t, bars.c, strict=True) if lo <= t < hi}
    out = Series(array("q"), array("d"), array("d"))
    with closing(sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)) as conn:
        rows = conn.execute(
            "select open_ms, close from candles where exchange='binance_usdm' and "
            "symbol='BTCUSDT' and timeframe='1m' and open_ms >= ? and open_ms < ? order by open_ms",
            (lo, hi),
        )
        for t, c in rows:
            s = spot.get(t)
            if s is not None:
                out.t.append(t)
                out.spot.append(s)
                out.perp.append(float(c))
    return out


def load_funding() -> dict[int, float]:
    with closing(sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)) as conn:
        rows = conn.execute(
            "select funding_ms, rate from funding_rates where exchange='binance_usdm' "
            "and symbol='BTCUSDT'"
        )
        return {int(ms): float(r) for ms, r in rows}


def series_hash(s: Series) -> str:
    h = hashlib.sha256()
    h.update(s.t.tobytes())
    h.update(s.spot.tobytes())
    h.update(s.perp.tobytes())
    return h.hexdigest()


@dataclass(slots=True)
class GridRun:
    days: list[int] = field(default_factory=list)
    equity: list[float] = field(default_factory=list)
    trade_pnl: float = 0.0
    """칸을 닫으며 실현한 손익(비용 뺌, 펀딩 뺌)."""
    funding: float = 0.0
    fees: float = 0.0
    round_trips: int = 0
    hold_minutes: list[int] = field(default_factory=list)
    trade_pnl_by_half: list[float] = field(default_factory=lambda: [0.0, 0.0])
    funding_by_half: list[float] = field(default_factory=lambda: [0.0, 0.0])


def simulate(
    s: Series,
    funding: Mapping[int, float],
    *,
    step: float,
    spot_fee: float,
    perp_fee: float,
    delay: int = 1,
    levels: int = LEVELS,
    notional: float = 1.0,
) -> GridRun:
    """자본 = levels × notional × (1 + MARGIN). 날마다 평가한 자본을 남긴다."""
    run = GridRun()
    capital = levels * notional * (1 + MARGIN)
    cash = capital
    units: dict[int, tuple[float, float, float, int, float]] = {}
    """칸 → (수량, 현물가, 선물가, 열린 색인, 연 비용)."""
    pending: list[tuple[str, int, int]] = []  # ("open"/"close", 칸, 체결 색인)
    fund_times = sorted(funding)
    fi = 0
    split = _ms(SPLIT)
    n = len(s.t)
    day_cur: int | None = None

    def mark(i: int) -> float:
        value = cash
        for q, s0, p0, _i, _f in units.values():
            value += q * (s.spot[i] - s0) - q * (s.perp[i] - p0)
        return value

    def fill(i: int, half: int) -> None:
        nonlocal cash, pending
        still = []
        for kind, k, at in pending:
            if at > i:
                still.append((kind, k, at))
                continue
            if kind == "open" and k not in units:
                q = notional / s.spot[i]
                fee = q * s.spot[i] * spot_fee + q * s.perp[i] * perp_fee
                cash -= fee
                run.fees += fee
                units[k] = (q, s.spot[i], s.perp[i], i, fee)
            elif kind == "close" and k in units:
                q, s0, p0, i0, fee0 = units.pop(k)
                fee = q * s.spot[i] * spot_fee + q * s.perp[i] * perp_fee
                pnl = q * (s.spot[i] - s0) - q * (s.perp[i] - p0)
                cash += pnl - fee
                run.fees += fee
                net = pnl - fee - fee0  # 열 때·닫을 때 비용 모두 뺀 한 칸 손익
                run.trade_pnl += net
                run.trade_pnl_by_half[half] += net
                run.round_trips += 1
                run.hold_minutes.append(i - i0)
        pending = still

    for i in range(n):
        t = s.t[i]
        half = 0 if t < split else 1
        day = t - t % DAY_MS
        if day_cur is not None and day != day_cur:
            run.days.append(day_cur)
            run.equity.append(mark(i - 1))
        day_cur = day
        fill(i, half)  # 1) 정한 체결 (이번 분 종가)
        # 2) 펀딩 (이 분까지 지난 펀딩 시각)
        while fi < len(fund_times) and fund_times[fi] <= t:
            rate = funding[fund_times[fi]]
            got = sum(u[0] * s.perp[i] * rate for u in units.values())
            cash += got
            run.funding += got
            run.funding_by_half[half] += got
            fi += 1
        # 3) 이 분 종가로 다음 할 일 (delay 0 이면 같은 분 종가에 바로 — 비교용)
        b = s.perp[i] / s.spot[i] - 1
        busy = {k for _kind, k, _a in pending}
        for k in range(1, levels + 1):
            if k in busy:
                continue
            if k not in units and b >= k * step:
                pending.append(("open", k, i + delay))
            elif k in units and b <= (k - 1) * step:
                pending.append(("close", k, i + delay))
        if delay == 0:
            fill(i, half)
    if day_cur is not None and n:
        run.days.append(day_cur)
        run.equity.append(mark(n - 1))
    return run


def stats(days: Sequence[int], eq: Sequence[float], lo: int, hi: int) -> dict[str, float]:
    seg = [v for d, v in zip(days, eq, strict=True) if lo <= d < hi]
    if len(seg) < 2:
        return {"cagr": 0.0, "mdd": 0.0}
    years = (len(seg) - 1) / 365
    peak, mdd = seg[0], 0.0
    for v in seg:
        peak = max(peak, v)
        mdd = max(mdd, 1 - v / peak)
    return {"cagr": round((seg[-1] / seg[0]) ** (1 / years) - 1, 4), "mdd": round(mdd, 4)}


def basis_share(s: Series) -> dict[str, float]:
    bs = [p / q - 1 for p, q in zip(s.perp, s.spot, strict=True)]
    out = {
        f">={x:.1%}": round(sum(b >= x for b in bs) / len(bs), 4)
        for x in (0.001, 0.002, 0.003, 0.005)
    }
    out["<=-0.1%"] = round(sum(b <= -0.001 for b in bs) / len(bs), 4)
    out["median"] = round(statistics.median(bs), 6)
    return out


def _summary(run: GridRun, capital: float) -> dict[str, Any]:
    lo, mid, hi = _ms(FROM), _ms(SPLIT), _ms(TO)
    return {
        "full": stats(run.days, run.equity, lo, hi),
        "first": stats(run.days, run.equity, lo, mid),
        "second": stats(run.days, run.equity, mid, hi),
        "trade_pnl_pct": round(run.trade_pnl / capital, 4),
        "trade_pnl_by_half_pct": [round(x / capital, 4) for x in run.trade_pnl_by_half],
        "funding_pct": round(run.funding / capital, 4),
        "funding_by_half_pct": [round(x / capital, 4) for x in run.funding_by_half],
        "fees_pct": round(run.fees / capital, 4),
        "round_trips": run.round_trips,
        "hold_minutes_median": statistics.median(run.hold_minutes) if run.hold_minutes else None,
    }


def run() -> dict[str, Any]:
    target = OUT / "verdict.json"
    if target.exists():
        raise SystemExit(f"이미 판정했다: {target}")
    sha, dirty = git_state()
    if dirty:
        raise SystemExit("작업 트리가 깨끗하지 않다 — 커밋 뒤에 실행")
    s = load_series()
    digest = series_hash(s)
    if digest != EXPECTED_SERIES:
        raise SystemExit("베이시스 계열 해시가 고정값과 다르다")
    funding = load_funding()
    capital = LEVELS * (1 + MARGIN)
    main = simulate(
        s, funding, step=MAIN_STEP, spot_fee=COSTS["limit"][0], perp_fee=COSTS["limit"][1]
    )
    m = _summary(main, capital)
    b1 = m["full"]["cagr"] >= 0.05
    b2 = m["first"]["cagr"] >= 0.03 and m["second"]["cagr"] >= 0.03
    b3 = all(x > 0 for x in m["trade_pnl_by_half_pct"])
    b4 = m["full"]["mdd"] <= 0.10
    refs: dict[str, Any] = {}
    for step in STEPS:
        for cost, (sf, pf) in COSTS.items():
            if step == MAIN_STEP and cost == "limit":
                continue
            r = simulate(s, funding, step=step, spot_fee=sf, perp_fee=pf)
            refs[f"{step:.1%}_{cost}"] = _summary(r, capital)
    no_delay = simulate(
        s, funding, step=MAIN_STEP, spot_fee=COSTS["limit"][0], perp_fee=COSTS["limit"][1], delay=0
    )
    yearly = {}
    for y in range(2020, 2027):
        yearly[str(y)] = stats(main.days, main.equity, _ms(f"{y}-01-01"), _ms(f"{y + 1}-01-01"))[
            "cagr"
        ]
    out = {
        "git": sha,
        "series_sha256": digest,
        "minutes": len(s.t),
        "main": m,
        "B1_cagr_5": b1,
        "B2_halves_3": b2,
        "B3_grid_pnl_halves": b3,
        "B4_mdd_10": b4,
        "verdict": "PASS" if b1 and b2 and b3 and b4 else "REJECT",
        "report": {
            "yearly_cagr": yearly,
            "basis_share": basis_share(s),
            "no_delay_same_minute_fill": _summary(no_delay, capital),
            "refs": refs,
        },
        "note": "문서 149 1 회 판정. 선물 다리 청산 위험 무시(지갑 이체 가정).",
    }
    OUT.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def main(argv: list[str]) -> int:
    if argv == ["hash"]:
        s = load_series()
        print(f"{series_hash(s)} ({len(s.t)} 분)")
        return 0
    if argv == ["run"]:
        if not EXPECTED_SERIES:
            raise SystemExit("계열 해시가 고정되지 않았다 — hash 뒤 EXPECTED_SERIES 를 적고 커밋")
        out = run()
        sys.stdout.write(
            json.dumps({k: out[k] for k in ("main", "verdict")}, ensure_ascii=False, indent=1)
            + "\n"
        )
        return 0
    raise SystemExit("usage: python -m scripts.run_basis_grid hash | run")


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            _reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main(sys.argv[1:]))
