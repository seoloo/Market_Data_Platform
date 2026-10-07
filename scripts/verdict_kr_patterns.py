"""158 — 국장 반전형 패턴 × 거래량 추세 **1 회 판정** (사전 등록 §4·§5 그대로).

사용 예::

    python -m scripts.verdict_kr_patterns hash      # 자료 · 사건 해시만 (수익 없음)
    python -m scripts.verdict_kr_patterns verdict --out reports/indicator_a/verdict.json

* 사건: ``scripts/count_kr_patterns.py`` 가 만든 ``reports/indicator_a/events.jsonl`` (해시 고정).
* 진입: 탐지 다음 거래일부터 최대 5 일, 한도 = 전날 종가 × 1.005 내림(``kr_portfolio.buy_limit``),
  종가 < 한도면 한도에 체결. 청산: 체결일 + 20 거래일 종가 확인 → 다음 날부터 한도 = 전날 종가 ×
  0.995 올림, 종가 > 한도면 한도에 체결, 날마다 다시. 폐지(자료 끝)면 마지막 종가.
* 비용: FUNDAMENTAL_KR_GATES §4 — 매수 0.25%, 매도 max(0.25%, 0.015% + 그날 세금). 코스닥
  증권거래세는 같은 기간 코스피 세금 + 농특세 합과 같아 표 하나로 쓴다. 참고 줄 = 실제 비용.
* 기준: 체결일 종가 → 청산일 종가, 체결일 유동성(20 일 평균 거래대금 ≥ 10 억) 종목 동일 가중.
* 판정 SE: 탐지일 20 거래일 묶음 합의 Bartlett(시차 1) 군집 SE.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import statistics
import sys
from array import array
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from scripts.count_kr_patterns import (  # noqa: E402
    ADV_DAYS,
    BLOCK,
    CAL,
    DIRS,
    MIN_VALUE,
    PRICE_FLOOR,
    WINDOW,
    _calendar,
    _load,
    violations,
)

from auto_trading.research.kr_portfolio import buy_limit, sell_limit  # noqa: E402

EVENTS = REPO_ROOT / "reports/indicator_a/events.jsonl"
KOSPI_TR = REPO_ROOT / "data/research/kr_stocks/kospi_tr.csv"
EXPECTED_DATA_SHA = "8741da78084ccae20b4743d91c107874b2c7754f3969badff13ae117e8192b38"
EXPECTED_EVENTS_SHA = "b4f18ffa581e0a42a3df1bb6bcd150343396b225620aa4ca8a2d90d65ee4dbdc"
EXPECTED_CLEAN = 26_555
HOLD = 20
MAX_BUY_TRIES = 5
BUY_COST = 0.0025
MIN_SELL_COST = 0.0025
COMMISSION = 0.00015
HALF_SPLIT = "20180101"
SEED = 20261007
RANDOM_DRAWS = 100
SLOTS, SLOT_WEIGHT, CAPITAL, HALT_DD = 20, 0.05, 10_000_000.0, 0.30
Z_H2 = 2.24
TAX = (  # (시작일, 매도 세금 합계)
    ("00000000", 0.0030),
    ("20190530", 0.0025),
    ("20210101", 0.0023),
    ("20230101", 0.0020),
    ("20240101", 0.0018),
    ("20250101", 0.0015),
    ("20260101", 0.0020),
)


def sell_tax(day: str) -> float:
    rate = TAX[0][1]
    for start, r in TAX:
        if day >= start:
            rate = r
    return rate


def costs(day_buy: str, day_sell: str, real: bool = False) -> tuple[float, float]:
    """(매수 비용률, 매도 비용률)."""
    actual_sell = COMMISSION + sell_tax(day_sell)
    if real:
        return COMMISSION, actual_sell
    return BUY_COST, max(MIN_SELL_COST, actual_sell)


@dataclass(frozen=True, slots=True)
class Trade:
    fill: int
    entry: float
    exit_i: int
    exit: float
    delisted: bool


def ffill(px: Sequence[float | None]) -> array[float]:
    out = array("d")
    last = math.nan
    for x in px:
        if x is not None:
            last = x
        out.append(last)
    return out


def last_index(p: Sequence[float | None]) -> int:
    return max((i for i, x in enumerate(p) if x is not None), default=-1)


def simulate_trade(
    cal: Sequence[str],
    p: Sequence[float | None],
    pf: Sequence[float],
    t: int,
    last_data: int | None = None,
) -> Trade | str | None:
    """탐지 칸 t 의 거래. None = 미체결, "incomplete" = 자료(달력) 끝까지 청산 못 함 (순수)."""
    if last_data is None:
        last_data = last_index(p)
    fill = -1
    entry = math.nan
    for k in range(1, MAX_BUY_TRIES + 1):
        d = t + k
        if d >= len(cal) or d > last_data:
            break
        x = p[d]
        if x is None or math.isnan(pf[d - 1]):
            continue
        lim = buy_limit(pf[d - 1], cal[d])
        if x < lim:
            fill, entry = d, lim
            break
    if fill < 0:
        return None
    start = fill + HOLD + 1
    for k in range(start, len(cal)):
        if k > last_data:
            break
        x = p[k]
        if x is None:
            continue
        lim = sell_limit(pf[k - 1], cal[k])
        if x > lim:
            return Trade(fill, entry, k, lim, False)
    if last_data < len(cal) - 1:  # 상장폐지 · 거래 끝 — 마지막 종가
        return Trade(fill, entry, last_data, float(pf[last_data]), True)
    return "incomplete"


def net_return(tr: Trade, cal: Sequence[str], real: bool = False) -> float:
    bc, sc = costs(cal[tr.fill], cal[tr.exit_i], real)
    return tr.exit * (1 - sc) / (tr.entry * (1 + bc)) - 1


def cluster_se(values: Sequence[float], blocks: Sequence[int]) -> float:
    """묶음 합의 Bartlett 시차 1 장기 분산으로 평균의 SE (순수)."""
    n = len(values)
    m = statistics.fmean(values)
    sums: dict[int, float] = defaultdict(float)
    for x, b in zip(values, blocks, strict=True):
        sums[b] += x - m
    keys = sorted(sums)
    var = sum(sums[k] ** 2 for k in keys)
    var += sum(sums[a] * sums[b] for a, b in pairwise(keys) if b == a + 1)  # 2 × 가중치 0.5
    return math.sqrt(max(var, 0.0)) / n


def summary_stats(xs: Sequence[float], blocks: Sequence[int], z: float = 2.0) -> dict[str, Any]:
    if not xs:
        return {"n": 0}
    m = statistics.fmean(xs)
    se = cluster_se(xs, blocks)
    return {"n": len(xs), "mean": round(m, 5), "se": round(se, 5), "lower": round(m - z * se, 5)}


class Market:
    def __init__(self) -> None:
        self.cal = _calendar()
        self.p: dict[str, list[float | None]] = {}
        self.pf: dict[str, array[float]] = {}
        self.liquid: dict[str, array[float]] = {}
        self.market: dict[str, str] = {}
        self.last: dict[str, int] = {}
        for m, d in DIRS.items():
            for f in sorted(d.glob("*.csv")):
                if f.stem in self.p:
                    continue
                px, val = _load(f)
                p = [px.get(x) for x in self.cal]
                v = [val.get(x, 0.0) for x in self.cal]
                adv = array("d")
                run = 0.0
                for i, x in enumerate(v):
                    run += x - (v[i - ADV_DAYS] if i >= ADV_DAYS else 0.0)
                    adv.append(run / ADV_DAYS)
                self.p[f.stem], self.pf[f.stem], self.liquid[f.stem] = p, ffill(p), adv
                self.market[f.stem] = m
                self.last[f.stem] = last_index(p)
        self._bench: dict[tuple[int, int], float] = {}

    def bench(self, f: int, x: int) -> float:
        key = (f, x)
        if key not in self._bench:
            rets = [
                self.pf[s][x] / self.p[s][f] - 1  # type: ignore[operator]
                for s in self.p
                if self.p[s][f] is not None and self.liquid[s][f] >= MIN_VALUE
            ]
            self._bench[key] = statistics.fmean(rets) if rets else 0.0
        return self._bench[key]

    def eligible_days(self, code: str) -> list[int]:
        """사건 계수와 같은 창 조건(탐지 없이) — 무작위 대조용."""
        p, v = self.p[code], self.liquid[code]
        bad = violations(self.cal, p)
        out = []
        for t in range(WINDOW - 1, len(self.cal)):
            win = p[t - WINDOW + 1 : t + 1]
            if any(x is None for x in win) or min(x for x in win if x is not None) < PRICE_FLOOR:
                continue
            if any(bad[t - WINDOW + 2 : t + 1]) or v[t] < MIN_VALUE:
                continue
            out.append(t)
        return out


def evaluate(
    mk: Market, rows: Sequence[Mapping[str, Any]], real: bool = False
) -> tuple[list[dict[str, Any]], Counter[str]]:
    pos = {d: i for i, d in enumerate(mk.cal)}
    done: list[dict[str, Any]] = []
    status: Counter[str] = Counter()
    for e in rows:
        t = pos[e["detect"]]
        c = e["code"]
        tr = simulate_trade(mk.cal, mk.p[c], mk.pf[c], t, mk.last[c])
        if tr is None:
            status["unfilled"] += 1
            continue
        if isinstance(tr, str):
            status["incomplete"] += 1
            continue
        status["filled"] += 1
        net = net_return(tr, mk.cal, real)
        done.append(
            {
                **e,
                "block": t // BLOCK,
                "net": net,
                "excess": net - mk.bench(tr.fill, tr.exit_i),
                "delisted": tr.delisted,
                "fill": mk.cal[tr.fill],
                "exit": mk.cal[tr.exit_i],
                "entry_px": tr.entry,
                "exit_px": tr.exit,
            }
        )
    return done, status


def account(mk: Market, trades: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """보고용 계좌 재생: 1,000 만 원 · 종목당 5% · 최대 20 · 낙폭 30% 면 새 진입 중단."""
    pos = {d: i for i, d in enumerate(mk.cal)}
    by_fill: dict[int, list[Mapping[str, Any]]] = defaultdict(list)
    for tr in sorted(trades, key=lambda r: (r["fill"], r["code"])):
        by_fill[pos[tr["fill"]]].append(tr)
    cash, peak, halted = CAPITAL, CAPITAL, ""
    open_: list[dict[str, Any]] = []
    curve: list[float] = []
    first = min(by_fill, default=0)
    for i in range(first, len(mk.cal)):
        for o in [o for o in open_ if o["exit_i"] == i]:
            cash += o["shares"] * o["exit_px"] * (1 - o["sc"])
            open_.remove(o)
        equity = cash + sum(o["shares"] * mk.pf[o["code"]][i] for o in open_)
        if not halted and equity <= peak * (1 - HALT_DD):
            halted = mk.cal[i]
        if not halted:
            for tr in by_fill.get(i, []):
                if len(open_) >= SLOTS:
                    break
                bc, sc = costs(tr["fill"], tr["exit"])
                spend = min(cash, equity * SLOT_WEIGHT)
                entry_px = tr["entry_px"]
                shares = math.floor(spend / (entry_px * (1 + bc)))
                if shares <= 0:
                    continue
                cash -= shares * entry_px * (1 + bc)
                open_.append(
                    {
                        "code": tr["code"],
                        "shares": shares,
                        "exit_i": pos[tr["exit"]],
                        "exit_px": tr["exit_px"],
                        "sc": sc,
                    }
                )
        equity = cash + sum(o["shares"] * mk.pf[o["code"]][i] for o in open_)
        peak = max(peak, equity)
        curve.append(equity)
    rets = [b / a - 1 for a, b in pairwise(curve) if a > 0]
    years = len(curve) / 252
    mdd, hi = 0.0, -math.inf
    for x in curve:
        hi = max(hi, x)
        mdd = max(mdd, 1 - x / hi)
    sd = statistics.pstdev(rets) if rets else 0.0
    return {
        "cagr": round((curve[-1] / CAPITAL) ** (1 / years) - 1, 4) if curve else None,
        "mdd": round(mdd, 4),
        "sharpe": round(statistics.fmean(rets) / sd * math.sqrt(252), 3) if sd else None,
        "halted": halted or None,
        "note": "1%·2% 규칙은 손절이 없어 적용하지 않음(사전 등록 §5)",
    }


def data_sha() -> tuple[str, int]:
    h = hashlib.sha256()
    files = [CAL, KOSPI_TR] + [f for d in DIRS.values() for f in sorted(d.glob("*.csv"))]
    for f in files:
        h.update(f.relative_to(REPO_ROOT).as_posix().encode())
        h.update(f.read_bytes())
    return h.hexdigest(), len(files)


def events_sha() -> str:
    return hashlib.sha256(EVENTS.read_bytes()).hexdigest()


def load_events() -> list[dict[str, Any]]:
    rows = [json.loads(x) for x in EVENTS.read_text(encoding="utf-8").splitlines() if x]
    return [e for e in rows if e["hold_complete"] and not e["hold_violation"]]


def random_control(mk: Market, rows: Sequence[Mapping[str, Any]], actual: float) -> dict[str, Any]:
    """같은 종목 · 같은 해의 조건 만족 창에서 무작위 탐지일 (146 방식)."""
    rng = random.Random(SEED)
    pool: dict[tuple[str, str], list[int]] = {}
    for code in {e["code"] for e in rows}:
        for t in mk.eligible_days(code):
            pool.setdefault((code, mk.cal[t][:4]), []).append(t)
    means = []
    for _ in range(RANDOM_DRAWS):
        fake = []
        for e in rows:
            days = pool.get((e["code"], e["detect"][:4]))
            if days:
                fake.append({**e, "detect": mk.cal[rng.choice(days)]})
        done, _ = evaluate(mk, fake)
        means.append(statistics.fmean(r["excess"] for r in done))
    means.sort()
    return {
        "draws": RANDOM_DRAWS,
        "median": round(means[len(means) // 2], 5),
        "p90": round(means[int(0.9 * len(means))], 5),
        "actual_rank_above": sum(1 for m in means if actual > m),
    }


def verdict(out: Path) -> dict[str, Any]:
    sha, nfiles = data_sha()
    if EXPECTED_DATA_SHA and sha != EXPECTED_DATA_SHA:
        raise SystemExit(f"자료 해시 불일치 {sha}")
    if EXPECTED_EVENTS_SHA and events_sha() != EXPECTED_EVENTS_SHA:
        raise SystemExit("사건 파일 해시 불일치")
    rows = load_events()
    if len(rows) != EXPECTED_CLEAN:
        raise SystemExit(f"사건 수 {len(rows)} ≠ {EXPECTED_CLEAN}")
    mk = Market()
    done, status = evaluate(mk, rows)
    xs = [r["excess"] for r in done]
    bl = [r["block"] for r in done]
    main = summary_stats(xs, bl)
    halves = {
        "first": summary_stats(
            [r["excess"] for r in done if r["detect"] < HALF_SPLIT],
            [r["block"] for r in done if r["detect"] < HALF_SPLIT],
        ),
        "second": summary_stats(
            [r["excess"] for r in done if r["detect"] >= HALF_SPLIT],
            [r["block"] for r in done if r["detect"] >= HALF_SPLIT],
        ),
    }
    t1 = main["lower"] > 0
    t2 = halves["first"]["mean"] > 0 and halves["second"]["mean"] > 0
    up = [r for r in done if r["volume"] == "up"]
    dn = [r for r in done if r["volume"] == "down"]
    su = summary_stats([r["excess"] for r in up], [r["block"] for r in up])
    sd_ = summary_stats([r["excess"] for r in dn], [r["block"] for r in dn])
    diff = su["mean"] - sd_["mean"]
    diff_se = math.hypot(su["se"], sd_["se"])
    worst = sorted(done, key=lambda r: r["excess"])
    delisted = [r for r in done if r["delisted"]]
    real_done, _ = evaluate(mk, rows, real=True)

    def group(key: str) -> dict[str, Any]:
        g: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for r in done:
            g[str(r[key])].append(r)
        return {
            k: summary_stats([r["excess"] for r in v], [r["block"] for r in v])
            for k, v in sorted(g.items())
        }

    by_year: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in sorted(done, key=lambda r: r["detect"]):
        by_year[r["detect"][:4]].append(r)
    result = {
        "worst_first": {
            "worst_event": {k: worst[0][k] for k in ("code", "detect", "kind", "excess", "net")},
            "bottom_5pct_excess": round(worst[int(0.05 * len(worst))]["excess"], 4),
            "delisted_in_hold": len(delisted),
            "delisted_mean_excess": round(statistics.fmean(r["excess"] for r in delisted), 4)
            if delisted
            else None,
        },
        "verdict": "HISTORICAL_SUPPORT" if t1 and t2 else "REJECT",
        "T1_lower_2se_gt0": t1,
        "T2_both_halves_gt0": t2,
        "H1": main,
        "halves": halves,
        "H2_volume_up_minus_down": {
            "diff": round(diff, 5),
            "se": round(diff_se, 5),
            "lower_2.24se": round(diff - Z_H2 * diff_se, 5),
            "up": su,
            "down": sd_,
        },
        "status": dict(status),
        "unfilled_rate": round(status["unfilled"] / max(1, len(rows)), 4),
        "by_kind": group("kind"),
        "by_market": group("market"),
        "by_volume": group("volume"),
        "by_year": {
            y: round(statistics.fmean(r["excess"] for r in v), 5)
            for y, v in sorted(by_year.items())
        },
        "median_excess": round(statistics.median(xs), 5),
        "real_cost_line": summary_stats(
            [r["excess"] for r in real_done], [r["block"] for r in real_done]
        ),
        "random_control": random_control(mk, rows, main["mean"]),
        "account_replay": account(mk, done),
        "data_sha": sha,
        "data_files": nfiles,
        "events_sha": events_sha(),
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("hash", "verdict"))
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "reports/indicator_a/verdict.json")
    args = parser.parse_args(argv)
    if args.mode == "hash":
        sha, n = data_sha()
        rows = load_events()
        info = {"data_sha": sha, "files": n, "events_sha": events_sha(), "clean": len(rows)}
        sys.stdout.write(json.dumps(info, ensure_ascii=False, indent=1) + "\n")
        return 0
    sys.stdout.write(json.dumps(verdict(args.out), ensure_ascii=False, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            _reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
