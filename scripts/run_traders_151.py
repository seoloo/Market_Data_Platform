"""문서 151 — 사진 속 트레이더 방법 중 안 본 셋을 생존 편향 없는 유가증권시장 자료로. 1회 판정.

사용 예::

    python -m scripts.run_traders_151 audit   # 자료 해시·표본 수 (수익 없음)
    python -m scripts.run_traders_151 run     # 깨끗한 트리 · 자료 해시 일치 · 판정 파일 없음

* **BNF** (고테가와 다카시): 종가가 25 일선보다 20% 이상 아래로 처음 내려간 종목 → 다음
  거래일 종가에 사서, 종가가 25 일선 위로 돌아온 날 종가(최대 20 거래일)에 판다.
  수정 종가 2007 ~ 2026-08.
* **EP** (크리스티안 쿨라매기, 에피소딕 피벗): 시가가 전날 종가보다 10% 이상 높고 거래량이
  직전 20 일 평균의 3 배 이상이며 종가 ≥ 시가인 날 종가에 산다. 손절 그날 저가, 종가가 10 일선
  아래면 그날 종가에 판다(최대 120 거래일). 원래 시세 2017 ~ 2026-08.
* **SCHWARTZ** (마틴 슈워츠): KOSPI 종가가 10 일 지수이동평균 위면 다음 거래일 종가에 KOSPI(ETF)를
  사고, 아래면 다음 거래일 종가에 판다.
"""

from __future__ import annotations

import hashlib
import json
import sys
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from scripts.run_a1_train import git_state
from scripts.run_community_150 import (
    KR,
    KR_FEE,
    KR_FEE_REF,
    MIN_N,
    KrStock,
    _consistent,
    _read,
    equity_stats,
    load_close,
    load_kr,
    summary,
    top_by_value,
    trade_verdict,
)
from scripts.run_lt_kr import KrPanel, build_panel
from scripts.run_lt_leaders import LOOKS

REPO = Path(__file__).resolve().parent.parent
OUTPUT = REPO / "reports" / "traders_151"
EXPECTED_DATA: str | None = "8b035208e02fbf230cea11f08f72b4184f9b448d0a0fc718530fd77261b6cce2"
"""자료 묶음 해시 — audit 뒤 문서 151 에 적고 여기에 고정한다."""

UNIVERSE = 200
LIQ_DAYS = 63
BNF_MA, BNF_DEV, BNF_MAX = 25, 0.20, 20
BNF_FROM, BNF_SPLIT, BNF_TO = "2007-07-01", "2017-01-01", "2026-08-31"
EP_GAP, EP_VOL, EP_VOL_DAYS, EP_MA, EP_MAX = 0.10, 3.0, 20, 10, 120
EP_SPLIT, EP_TO = "20220101", "20260831"
LIMIT = 0.29
EMA_DAYS = 10
ETF_FEE = 0.0010
"""KOSPI ETF 편도 (거래세 없음: 수수료 + 불리폭, 보수적). 참고 줄 0.03%."""
ETF_FEE_REF = 0.0003
IDX_FROM, IDX_SPLIT, IDX_TO = "2008-01-01", "2017-01-01", "2026-08-31"


# ---------------------------------------------------------------- BNF


def pit_members(panel: KrPanel, ts: Sequence[int]) -> dict[int, set[str]]:
    """``run_lt_kr.members`` 와 같은 종목군(직전 63 거래일 평균 거래대금 상위 200,
    1·3·6 달 수익을 낼 수 있는 종목)을 ``ts`` 날짜에만 누적합으로 빨리 계산."""
    pre: dict[str, list[float]] = {}
    for sym, vs in panel.value.items():
        acc = [0.0]
        for v in vs:
            acc.append(acc[-1] + v)
        pre[sym] = acc
    out: dict[int, set[str]] = {}
    for t in sorted(set(ts)):
        lo = max(0, t - LIQ_DAYS + 1)
        ranked: list[tuple[float, str]] = []
        for sym, xs in panel.close.items():
            if t < panel.listed_from[sym] or xs[t] is None:
                continue
            if any(t - k < panel.listed_from[sym] or xs[t - k] is None for k in LOOKS):
                continue
            ranked.append(((pre[sym][t + 1] - pre[sym][lo]) / (t + 1 - lo), sym))
        ranked.sort(key=lambda x: (-x[0], x[1]))
        out[t] = {s for _, s in ranked[:UNIVERSE]}
    return out


def sma_full(xs: Sequence[float | None], t: int, n: int) -> float | None:
    """t 까지 n 일 단순 평균 — 그 n 일 중 하루라도 거래가 없으면 None."""
    if t < n - 1:
        return None
    w = xs[t - n + 1 : t + 1]
    if any(x is None for x in w):
        return None
    return sum(w) / n  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class Trade:
    sym: str
    date: str
    """신호일."""
    ret: float
    """비용 전 수익."""
    days: int
    reason: str


def bnf_signals(panel: KrPanel) -> list[tuple[str, int]]:
    """(종목, 신호일 색인) — 종가 ≤ 25 일선 × 0.8 로 처음 내려간 날."""
    out: list[tuple[str, int]] = []
    start = next(i for i, d in enumerate(panel.days) if d >= BNF_FROM)
    end = max(i for i, d in enumerate(panel.days) if d <= BNF_TO)
    for sym, xs in panel.close.items():
        prev_below = False
        for t in range(max(start, BNF_MA), end + 1):
            c = xs[t]
            m = sma_full(xs, t, BNF_MA)
            if c is None or m is None:
                prev_below = False
                continue
            below = c <= m * (1 - BNF_DEV)
            if below and not prev_below:
                out.append((sym, t))
            prev_below = below
    return out


def bnf_trades(panel: KrPanel) -> list[Trade]:
    signals = bnf_signals(panel)
    universe = pit_members(panel, [t for _, t in signals])
    end = max(i for i, d in enumerate(panel.days) if d <= BNF_TO)
    busy_until: dict[str, int] = defaultdict(lambda: -1)
    trades: list[Trade] = []
    for sym, t in sorted(signals, key=lambda x: (x[1], x[0])):
        if sym not in universe[t] or t <= busy_until[sym]:
            continue
        xs = panel.close[sym]
        j = t + 1
        if j > end or xs[j] is None:
            continue  # 다음 날 거래 없음 → 사지 않음
        entry = xs[j]
        assert entry is not None
        exit_t, reason = None, ""
        held = 0
        u = j
        while u < min(end, panel.last[sym]):
            u += 1
            c = xs[u]
            if c is None:
                continue
            held += 1
            m = sma_full(xs, u, BNF_MA)
            if m is not None and c >= m:
                exit_t, reason = u, "back_to_ma"
                break
            if held >= BNF_MAX:
                exit_t, reason = u, "max_days"
                break
        if exit_t is None:
            exit_t = min(end, panel.last[sym])
            while xs[exit_t] is None:
                exit_t -= 1
            reason = "delisted" if panel.last[sym] < end else "period_end"
        px = xs[exit_t]
        assert px is not None
        trades.append(Trade(sym, panel.days[t], px / entry - 1, exit_t - j, reason))
        busy_until[sym] = exit_t
    return trades


def _week(d: str) -> str:
    y, w, _ = date.fromisoformat(d).isocalendar()
    return f"{y}-{w:02d}"


def run_bnf(panel: KrPanel) -> dict[str, object]:
    trades = bnf_trades(panel)
    nets = [x.ret - 2 * KR_FEE for x in trades]
    dates = [x.date for x in trades]
    weeks = [_week(d) for d in dates]
    out = trade_verdict(nets, dates, BNF_SPLIT, MIN_N, keys=weeks)
    reasons: dict[str, int] = defaultdict(int)
    for x in trades:
        reasons[x.reason] += 1
    by_year: dict[str, list[float]] = defaultdict(list)
    for x, n in zip(trades, nets, strict=True):
        by_year[x.date[:4]].append(n)
    return {
        **out,
        "exit_reasons": dict(reasons),
        "mean_days": round(sum(x.days for x in trades) / len(trades), 1) if trades else 0,
        "by_year": {y: [len(v), round(sum(v) / len(v), 4)] for y, v in sorted(by_year.items())},
        "ref_cost": summary([x.ret - 2 * KR_FEE_REF for x in trades], weeks),
    }


# ---------------------------------------------------------------- EP


def ep_trades(
    stocks: Mapping[str, KrStock],
    universe: Mapping[str, set[str]],
    lows: Mapping[str, Sequence[float]],
) -> tuple[list[Trade], dict[str, int]]:
    trades: list[Trade] = []
    drop: dict[str, int] = defaultdict(int)
    for sym, s in stocks.items():
        low = lows[sym]
        busy = -1
        n = len(s.dates)
        for i in range(EP_VOL_DAYS + 1, n):
            if s.dates[i] > EP_TO or i <= busy:
                continue
            pc, o, c = s.c[i - 1], s.o[i], s.c[i]
            if pc <= 0 or o < pc * (1 + EP_GAP) or c < o or s.vol[i] <= 0:
                continue
            avg = sum(s.vol[i - EP_VOL_DAYS : i]) / EP_VOL_DAYS
            if avg <= 0 or s.vol[i] < EP_VOL * avg:
                continue
            if sym not in universe.get(s.dates[i], ()):
                continue
            if s.lock[i] != "00" or not _consistent(s, i):
                drop["corporate_action_on_signal"] += 1
                continue
            if c >= pc * (1 + LIMIT):
                drop["closes_limit_up_unfillable"] += 1
                continue
            stop = low[i]
            entry = c
            exit_px, exit_i, reason = None, i, ""
            held = 0
            u = i
            while u < n - 1 and s.dates[u + 1] <= EP_TO:
                u += 1
                if s.vol[u] <= 0:
                    continue
                if s.lock[u] != "00" or not _consistent(s, u):
                    exit_px, exit_i, reason = s.c[u - 1], u - 1, "corporate_action"
                    break
                held += 1
                if low[u] <= stop:
                    exit_px, exit_i, reason = min(s.o[u], stop), u, "stop"
                    break
                ma = _ma(s, u, EP_MA)
                if ma is not None and s.c[u] < ma:
                    exit_px, exit_i, reason = s.c[u], u, "below_ma"
                    break
                if held >= EP_MAX:
                    exit_px, exit_i, reason = s.c[u], u, "max_days"
                    break
            if exit_px is None:
                k = u
                while k > i and s.vol[k] <= 0:
                    k -= 1
                exit_px, exit_i = s.c[k], k
                reason = "delisted" if u == n - 1 and s.dates[u] < EP_TO else "period_end"
            if exit_i <= i:
                drop["no_exit_day"] += 1
                continue
            trades.append(Trade(sym, s.dates[i], exit_px / entry - 1, exit_i - i, reason))
            busy = exit_i
    trades.sort(key=lambda x: (x.date, x.sym))
    return trades, dict(drop)


def _ma(s: KrStock, u: int, n: int) -> float | None:
    if u < n - 1:
        return None
    w = [s.c[k] for k in range(u - n + 1, u + 1) if s.vol[k] > 0]
    return sum(w) / len(w) if len(w) == n else None


def load_lows() -> dict[str, list[float]]:
    """원래 시세의 저가 (``KrStock`` 에 저가가 없어 따로 읽는다)."""
    return {
        p.stem: [float(r["low"] or 0) for r in _read(p)] for p in sorted((KR / "raw").glob("*.csv"))
    }


def run_ep(stocks: Mapping[str, KrStock], lows: Mapping[str, Sequence[float]]) -> dict[str, object]:
    trades, drop = ep_trades(stocks, top_by_value(stocks), lows)
    nets = [x.ret - 2 * KR_FEE for x in trades]
    dates = [x.date for x in trades]
    out = trade_verdict(nets, dates, EP_SPLIT, MIN_N, keys=dates)
    reasons: dict[str, int] = defaultdict(int)
    for x in trades:
        reasons[x.reason] += 1
    rs = sorted(x.ret for x in trades)
    return {
        **out,
        "dropped": drop,
        "exit_reasons": dict(reasons),
        "mean_days": round(sum(x.days for x in trades) / len(trades), 1) if trades else 0,
        "best5": [round(r, 3) for r in rs[-5:]],
        "median_gross": round(rs[len(rs) // 2], 4) if rs else 0.0,
        "ref_cost": summary([x.ret - 2 * KR_FEE_REF for x in trades], dates),
    }


# ---------------------------------------------------------------- SCHWARTZ


def ema(xs: Sequence[float], n: int) -> list[float]:
    a = 2 / (n + 1)
    out = [xs[0]]
    for x in xs[1:]:
        out.append(a * x + (1 - a) * out[-1])
    return out


def timing_equity(closes: Sequence[float], on: Sequence[bool], fee: float) -> list[float]:
    """``on[t]`` (t 종가에 판단) → t+1 종가에 들어가거나 나온다. 수익 t+1→t+2 부터 반영.
    현금 이자는 0 (보수적)."""
    eq = [1.0]
    pos = False
    for t in range(1, len(closes)):
        v = eq[-1]
        if pos:
            v *= closes[t] / closes[t - 1]
        want = on[t - 1]  # 전날 종가 신호를 오늘 종가에 실행
        if want != pos:
            v *= 1 - fee
            pos = want
        eq.append(v)
    return eq


def run_schwartz() -> dict[str, object]:
    d, c = load_close(KR / "kospi.csv")
    e = ema(c, EMA_DAYS)
    on = [x > m for x, m in zip(c, e, strict=True)]
    i0 = next(i for i, x in enumerate(d) if x >= IDX_FROM)
    i1 = max(i for i, x in enumerate(d) if x <= IDX_TO)
    split = next(i for i, x in enumerate(d) if x >= IDX_SPLIT)

    def seg(lo: int, hi: int, fee: float) -> tuple[dict[str, float], dict[str, float]]:
        eq = timing_equity(c[lo : hi + 1], on[lo : hi + 1], fee)
        bh = [x / c[lo] for x in c[lo : hi + 1]]
        return equity_stats(eq), equity_stats(bh)

    full, full_bh = seg(i0, i1, ETF_FEE)
    first, first_bh = seg(i0, split - 1, ETF_FEE)
    second, second_bh = seg(split, i1, ETF_FEE)
    ref, _ = seg(i0, i1, ETF_FEE_REF)
    switches = sum(on[t] != on[t - 1] for t in range(i0 + 1, i1 + 1))
    years = (i1 - i0) / 252
    checks = {
        "a_sharpe_gt_bh_full": full["sharpe"] > full_bh["sharpe"],
        "b_sharpe_gt_bh_halves": first["sharpe"] > first_bh["sharpe"]
        and second["sharpe"] > second_bh["sharpe"],
        "c_mdd_lt_bh": full["mdd"] < full_bh["mdd"],
    }
    return {
        "verdict": "PASS" if all(checks.values()) else "REJECT",
        "checks": checks,
        "strategy": full,
        "buy_hold": full_bh,
        "halves": [[first, first_bh], [second, second_bh]],
        "ref_cost": ref,
        "switches_per_year": round(switches / years, 1),
        "exposure": round(sum(on[i0 : i1 + 1]) / (i1 - i0 + 1), 3),
    }


# ---------------------------------------------------------------- 실행


def data_files() -> list[Path]:
    files = [KR / "kospi.csv", KR / "info.json"]
    files += sorted((KR / "raw").glob("*.csv"))
    files += sorted((KR / "prices").glob("*.csv"))
    return files


def data_hash() -> tuple[str, int]:
    h = hashlib.sha256()
    files = data_files()
    for p in files:
        h.update(p.name.encode())
        h.update(p.read_bytes())
    return h.hexdigest(), len(files)


def _load() -> tuple[KrPanel, dict[str, KrStock], dict[str, list[float]]]:
    panel, _ = build_panel()
    return panel, load_kr(), load_lows()


def audit() -> dict[str, object]:
    """표본 수만 센다 (수익 없음)."""
    digest, n = data_hash()
    panel, stocks, lows = _load()
    bnf = bnf_trades(panel)
    ep, drop = ep_trades(stocks, top_by_value(stocks), lows)
    return {
        "data_sha256": digest,
        "files": n,
        "bnf_trades": len(bnf),
        "bnf_halves": [
            sum(x.date < BNF_SPLIT for x in bnf),
            sum(x.date >= BNF_SPLIT for x in bnf),
        ],
        "bnf_weeks": len({_week(x.date) for x in bnf}),
        "ep_trades": len(ep),
        "ep_halves": [sum(x.date < EP_SPLIT for x in ep), sum(x.date >= EP_SPLIT for x in ep)],
        "ep_dropped": drop,
    }


def run() -> dict[str, object]:
    target = OUTPUT / "verdict.json"
    if target.exists():
        raise SystemExit(f"이미 판정했다: {target}")
    sha, dirty = git_state()
    if dirty:
        raise SystemExit("작업 트리가 깨끗하지 않다 — 커밋 뒤에 실행")
    digest, _ = data_hash()
    if EXPECTED_DATA is None or digest != EXPECTED_DATA:
        raise SystemExit(f"자료 해시가 다르다: {digest}")
    panel, stocks, lows = _load()
    result: dict[str, object] = {
        "commit": sha,
        "data_sha256": digest,
        "BNF": run_bnf(panel),
        "EP": run_ep(stocks, lows),
        "SCHWARTZ": run_schwartz(),
    }
    OUTPUT.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else "audit"
    if cmd == "audit":
        print(json.dumps(audit(), ensure_ascii=False, indent=2))
    elif cmd == "run":
        print(json.dumps(run(), ensure_ascii=False, indent=2))
    else:
        raise SystemExit(f"알 수 없는 명령: {cmd}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
