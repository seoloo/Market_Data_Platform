"""문서 207 — D22 종합 점수(지표 · 엘리엇 · 삼각형 · 캔들) 낙관 탐색, BTC 4 시간봉 · 일봉.

사용자(2026-10-11): "A 로 하고 4 시간 일봉 둘 다 … 정말 진짜 편향적이어도 좋으니까 낙관적으로,
최대한 수익이 나는 방향으로". → TRAIN(2018 ~ 2022)에서는 마음껏 고르고, OOS(2023 ~ 2026-08)는
봉인했다가 고른 규칙 그대로 한 번. 전체 기간을 보고 고른 '사후 최대치'는 따로 표시.

    python -m scripts.run_d22_score_207 run   → reports/d22_score_207/result.json
"""

from __future__ import annotations

import bisect
import json
import sqlite3
import statistics
import sys
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from auto_trading.research.score_d22 import (
    FEATURES,
    GROUPS,
    Bars,
    Model,
    features,
    fit_ridge,
    fit_univariate,
    forward_returns,
    positions_from,
    scores,
    simulate,
    stats,
)

REPO = Path(__file__).resolve().parent.parent
DB = REPO / "data" / "trading.db"
OUT = REPO / "reports" / "d22_score_207"
HOUR_MS = 3_600_000
TRAIN = ("2018-01-01", "2022-12-31")
OOS = ("2023-01-01", "2026-08-31")
FULL = ("2018-01-01", "2026-08-31")
COST = 0.00095
COSTS = {"project_0.095": 0.00095, "user_0.06": 0.0006, "realistic_0.023": 0.00023}
TF: dict[str, dict[str, Any]] = {
    "4h": {"hours": 4, "per_year": 2190.0, "horizons": (1, 3, 6, 12)},
    "1d": {"hours": 24, "per_year": 365.0, "horizons": (1, 3, 5, 10)},
}
PCTS = (50, 60, 70, 80, 90)
MODES = ("both", "long", "short")
ALPHAS = (0.01, 0.1, 1.0)
GREEDY_MAX = 8
NO_EW = tuple(f for f in FEATURES if f not in GROUPS["elliott"])


def _ms(day: str) -> int:
    return int(datetime.fromisoformat(day).replace(tzinfo=UTC).timestamp() * 1000)


def load(hours: int) -> Bars:
    """Binance 현물 BTCUSDT 1 시간봉 → ``hours`` 봉 (구성 시간봉이 다 있는 묶음만)."""
    con = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)
    with con:
        rows = [
            (int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]))
            for r in con.execute(
                "select open_ms, open, high, low, close from candles where "
                "exchange='binance_spot' and symbol='BTCUSDT' and timeframe='1h' "
                "and open_ms <= ? order by open_ms",
                (_ms("2026-08-31") + 23 * HOUR_MS,),
            )
        ]
    span = hours * HOUR_MS
    groups: dict[int, list[tuple[int, float, float, float, float]]] = {}
    for r in rows:
        groups.setdefault(r[0] - r[0] % span, []).append(r)
    t, o, h, lo, c = [], [], [], [], []
    for start, g in sorted(groups.items()):
        if len(g) != hours:
            continue
        t.append(start)
        o.append(g[0][1])
        h.append(max(x[2] for x in g))
        lo.append(min(x[3] for x in g))
        c.append(g[-1][4])
    return Bars(t, o, h, lo, c)


def funding_fn(b: Bars) -> Callable[[int], float]:
    """t 결정 → (t+1 시가, t+2 시가] 사이 BTCUSDT 무기한 funding rate 합 (2020 전은 0)."""
    con = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)
    with con:
        f = sorted(
            (int(ms), float(rate))
            for ms, rate in con.execute(
                "select funding_ms, rate from funding_rates where symbol='BTCUSDT'"
            )
        )
    times = [x[0] for x in f]
    cum = [0.0]
    for _, rate in f:
        cum.append(cum[-1] + rate)

    def total(t: int) -> float:
        if t + 2 >= len(b.t):
            return 0.0
        a = bisect.bisect_right(times, b.t[t + 1])
        z = bisect.bisect_right(times, b.t[t + 2])
        return cum[z] - cum[a]

    return total


def index(b: Bars, span: tuple[str, str]) -> list[int]:
    lo, hi = _ms(span[0]), _ms(span[1]) + 24 * HOUR_MS
    return [k for k, t in enumerate(b.t) if lo <= t < hi]


def quant(xs: Sequence[float], q: float) -> float:
    s = sorted(xs)
    return s[min(len(s) - 1, max(0, int(q * len(s))))]


class Search:
    """한 시간 단위에서 TRAIN(또는 전체) 고르기."""

    def __init__(self, b: Bars, feats: dict[str, list[float | None]], tf: dict[str, Any]) -> None:
        self.b = b
        self.feats = feats
        self.tf = tf
        self.fund = funding_fn(b)

    def run_model(
        self,
        model: Model,
        fit_idx: Sequence[int],
        pct: int,
        mode: str,
        eval_idx: Sequence[int],
        cost: float,
    ) -> tuple[dict[str, Any], list[float]]:
        sc = scores(self.feats, model, len(self.b.t))
        fit_scores = [s for t in fit_idx if (s := sc[t]) is not None]
        hi = quant(fit_scores, pct / 100)
        lo = quant(fit_scores, 1 - pct / 100)
        pos = positions_from(sc, lo, hi, mode)
        sim = simulate(self.b, pos, eval_idx, cost, self.fund)
        st = stats(sim.returns, self.tf["per_year"])
        res = {
            **{k: round(v, 4) for k, v in st.items()},
            "trades": sim.trades,
            "exposure": round(statistics.fmean(abs(p) for p in sim.positions), 3)
            if sim.positions
            else 0,
            "long_share": round(sum(p > 0 for p in sim.positions) / max(len(sim.positions), 1), 3),
            "short_share": round(sum(p < 0 for p in sim.positions) / max(len(sim.positions), 1), 3),
            "long_pnl_sum": round(sim.long_pnl, 4),
            "short_pnl_sum": round(sim.short_pnl, 4),
            "cost_sum": round(sim.cost, 4),
            "funding_sum": round(sim.funding, 4),
        }
        return res, sim.returns

    def greedy(self, fit_idx: Sequence[int], target: Sequence[float | None]) -> tuple[str, ...]:
        chosen: list[str] = []
        best = -9.0
        for _ in range(GREEDY_MAX):
            pick = None
            for f in FEATURES:
                if f in chosen:
                    continue
                m = fit_univariate(self.feats, [*chosen, f], target, fit_idx)
                r, _ = self.run_model(m, fit_idx, 70, "both", fit_idx, COST)
                if r["sharpe"] > best + 1e-9:
                    best, pick = r["sharpe"], f
            if pick is None:
                break
            chosen.append(pick)
        return tuple(chosen)

    def search(self, fit_span: tuple[str, str]) -> list[dict[str, Any]]:
        fit_idx = index(self.b, fit_span)
        rows = []
        for h in self.tf["horizons"]:
            target = forward_returns(self.b, h)
            models: dict[str, Model] = {
                "uni_all": fit_univariate(self.feats, FEATURES, target, fit_idx),
                "uni_no_elliott": fit_univariate(self.feats, NO_EW, target, fit_idx),
            }
            for a in ALPHAS:
                models[f"ridge_all_a{a}"] = fit_ridge(self.feats, FEATURES, target, fit_idx, a)
            g = self.greedy(fit_idx, target)
            if g:
                models["greedy_uni"] = fit_univariate(self.feats, g, target, fit_idx)
            for name, m in models.items():
                for pct in PCTS:
                    for mode in MODES:
                        r, _ = self.run_model(m, fit_idx, pct, mode, fit_idx, COST)
                        rows.append(
                            {
                                "horizon": h,
                                "model": name,
                                "pct": pct,
                                "mode": mode,
                                "fit": r,
                                "_model": m,
                            }
                        )
        rows.sort(key=lambda r: -r["fit"]["sharpe"])
        return rows


def _public(row: dict[str, Any]) -> dict[str, Any]:
    m: Model = row["_model"]
    return {
        **{k: v for k, v in row.items() if k != "_model"},
        "features": list(m.names),
        "weights": {f: round(w, 4) for f, w in zip(m.names, m.weights, strict=True)},
    }


def yearly(b: Bars, idx: Sequence[int], rets: Sequence[float]) -> dict[str, float]:
    by: dict[str, float] = {}
    for t, r in zip(idx, rets, strict=False):
        y = datetime.fromtimestamp(b.t[t] / 1000, UTC).strftime("%Y")
        by[y] = (1 + by.get(y, 0.0)) * (1 + r) - 1
    return {k: round(v, 4) for k, v in sorted(by.items())}


def timeframe(name: str) -> dict[str, Any]:
    tf = TF[name]
    b = load(int(tf["hours"]))
    feats = features(b)
    s = Search(b, feats, tf)
    train_idx, oos_idx, full_idx = index(b, TRAIN), index(b, OOS), index(b, FULL)
    out: dict[str, Any] = {"bars": len(b.t), "train_bars": len(train_idx), "oos_bars": len(oos_idx)}

    # 1) TRAIN 에서 고르기 → OOS 한 번
    ranked = s.search(TRAIN)
    best = ranked[0]
    out["searched_configs"] = len(ranked)
    out["train_top5"] = [_public(r) for r in ranked[:5]]
    pick = _public(best)
    oos: dict[str, Any] = {}
    for cname, c in COSTS.items():
        r, rets = s.run_model(best["_model"], train_idx, best["pct"], best["mode"], oos_idx, c)
        oos[cname] = r
        if cname == "project_0.095":
            oos["yearly"] = yearly(b, oos_idx, rets)
    tr_rets = s.run_model(best["_model"], train_idx, best["pct"], best["mode"], train_idx, COST)[1]
    pick["train_yearly"] = yearly(b, train_idx, tr_rets)
    out["picked"] = pick
    out["picked_oos"] = oos
    # TRAIN 상위 20 개 설정의 OOS 분포 (고르기 운의 크기)
    top = []
    for r in ranked[:20]:
        o, _ = s.run_model(r["_model"], train_idx, r["pct"], r["mode"], oos_idx, COST)
        top.append(
            {"train_sharpe": r["fit"]["sharpe"], "oos_sharpe": o["sharpe"], "oos_cagr": o["cagr"]}
        )
    out["train_top20_in_oos"] = top

    # 2) 사후 최대치: 전체 기간을 보고 고름 (완전 편향)
    hind = s.search(FULL)[0]
    out["hindsight_full_best"] = _public(hind)

    # 3) 기준선
    n = len(b.t)
    bench = {
        "buy_hold": [1.0] * n,
        "ema200_long_short": [v if v is not None else 0.0 for v in feats["above200"]],
        "ema200_long_only": [max(v, 0.0) if v is not None else 0.0 for v in feats["above200"]],
    }
    out["benchmarks"] = {
        k: {
            "train": s_run(s, pos, train_idx),
            "oos": s_run(s, pos, oos_idx),
        }
        for k, pos in bench.items()
    }

    # 4) 요소 하나씩: TRAIN 상관 부호대로 롱 · 숏 (h = 고른 h)
    target = forward_returns(b, best["horizon"])
    single = {}
    for f in FEATURES:
        m = fit_univariate(feats, [f], target, train_idx)
        w = m.weights[0]
        pos = [
            (1.0 if (v - m.mean[0]) * w > 0 else -1.0 if (v - m.mean[0]) * w < 0 else 0.0)
            if (v := feats[f][t]) is not None
            else 0.0
            for t in range(n)
        ]
        single[f] = {
            "train_corr": round(w, 4),
            "train": s_run(s, pos, train_idx),
            "oos": s_run(s, pos, oos_idx),
        }
    out["single_features"] = single
    out["feature_coverage"] = {
        f: round(sum(1 for t in full_idx if feats[f][t] not in (None, 0.0)) / len(full_idx), 3)
        for f in FEATURES
    }
    return out


def s_run(s: Search, pos: Sequence[float], idx: Sequence[int]) -> dict[str, float]:
    sim = simulate(s.b, pos, idx, COST, s.fund)
    st = stats(sim.returns, s.tf["per_year"])
    return {**{k: round(v, 4) for k, v in st.items()}, "trades": sim.trades}


def run() -> dict[str, Any]:
    target = OUT / "result.json"
    if target.exists():
        raise SystemExit(f"이미 실행했다: {target}")
    res: dict[str, Any] = {"computed": datetime.now(UTC).isoformat(timespec="seconds")}
    for name in TF:
        res[name] = timeframe(name)
    OUT.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    return res


def main(argv: list[str]) -> int:
    if argv != ["run"]:
        raise SystemExit("usage: python -m scripts.run_d22_score_207 run")
    run()
    sys.stdout.write("ok\n")
    return 0


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            _reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main(sys.argv[1:]))
