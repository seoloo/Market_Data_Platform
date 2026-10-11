# ruff: noqa: E501 — 한국어 설명 · 표 문자열이 길다
"""문서 213 — 연속형 추세 강도(TCONT) vs 부호 방식(TSIGN = T/v2) · BTC · ETH 무기한 LONG/FLAT 후향 파일럿. 주문 없음.

두 비교군의 차이는 추세 점수뿐이다. 신호 · 변동성은 T/v2 와 같은 **현물 일봉**(DB binance_spot 1d), 체결 · 보호 · 손익은 선물(210 엔진 · 캐시).

사용: ``python -m scripts.run_tcont_213 run`` → reports/tcont_213/result.json
"""

from __future__ import annotations

import json
import math
import sqlite3
import statistics
import sys
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import asdict, replace
from datetime import UTC, date, datetime
from itertools import pairwise
from pathlib import Path
from typing import Any

from scripts.run_qb_dc_210 import (
    DAY,
    END,
    H4,
    MIN,
    WARM,
    Market,
    Params,
    Signals,
    _load,
    block_bootstrap,
    simulate,
    summarize,
)

from auto_trading.backtest.trend import TrendConfig, target_weight

REPO = Path(__file__).resolve().parent.parent
REPORT = REPO / "reports" / "tcont_213"
CFG = TrendConfig(long_only=True)  # T/v2: (20, 60, 120) · 60 일 · 15% · 0.5 · 밴드 0.05
BASE = Params(decide_hours=24, sig_is_weight=True)


def spot_closes() -> dict[str, dict[int, float]]:
    """현물 일봉 종가 (일 시작 ms → 종가). 2026-09-01 이후 안 읽음."""
    db = sqlite3.connect(f"file:{REPO / 'data' / 'trading.db'}?mode=ro", uri=True)
    out: dict[str, dict[int, float]] = defaultdict(dict)
    for s, t, c in db.execute(
        "select symbol, open_ms, close from candles where exchange='binance_spot' and timeframe='1d' and symbol in ('BTCUSDT','ETHUSDT') and open_ms < ?",
        (END,),
    ):
        out[s][int(t)] = float(c)
    return dict(out)


def cont_weight(closes: Sequence[float], cfg: TrendConfig = CFG) -> float | None:
    """연속형: z_n = ln(P_t / P_(t−n)) / (σ_일 √n), s = max(0, 평균 tanh(z_n)), 비중 = s × min(목표 변동성 / σ_연, 상한).

    σ_연 · 이력 요건 · 변동성 0 처리는 ``target_weight``(T/v2) 와 같다. σ_일 = σ_연 / √365.
    """
    need = max(max(cfg.lookbacks), cfg.vol_days) + 1
    if len(closes) < need:
        return None
    last = closes[-1]
    steps = [math.log(closes[-k] / closes[-k - 1]) for k in range(1, cfg.vol_days + 1)]
    vol = statistics.stdev(steps) * math.sqrt(365)
    if vol <= 0:
        return 0.0
    sig_d = vol / math.sqrt(365)
    score = statistics.fmean(
        math.tanh(math.log(last / closes[-1 - n]) / (sig_d * math.sqrt(n))) for n in cfg.lookbacks
    )
    return max(0.0, score) * min(cfg.target_vol / vol, cfg.max_weight)


def sign_score(closes: Sequence[float], cfg: TrendConfig = CFG) -> float | None:
    """T/v2 방향(롱만) — 보고용 (약한 추세 구분)."""
    if len(closes) < max(cfg.lookbacks) + 1:
        return None
    last = closes[-1]
    d = statistics.fmean(
        (last / closes[-1 - k] - 1 > 0) - (last / closes[-1 - k] - 1 < 0) for k in cfg.lookbacks
    )
    return max(0.0, d)


def build(
    markets: dict[str, Market], spot: dict[str, dict[int, float]]
) -> tuple[dict[str, Signals], dict[str, list[tuple[str, float, float, float]]]]:
    """판단 시각(4 시간 칸)마다 목표 비중. 00:00 UTC 칸에서 그 전날까지 완성된 현물 일봉으로. 나머지 칸은 같은 값(쓰지 않음)."""
    sigs: dict[str, Signals] = {}
    daily_rows: dict[str, list[tuple[str, float, float, float]]] = {}
    for s, m in markets.items():
        n_j = len(m.c) * MIN // H4
        days = sorted(spot[s])
        idx = {d: i for i, d in enumerate(days)}
        closes = [spot[s][d] for d in days]
        ts, tc = [0.0] * n_j, [0.0] * n_j
        rows: list[tuple[str, float, float, float]] = []
        cur_s = cur_c = 0.0
        for j in range(n_j):
            d_ms = WARM + j * H4
            if d_ms % DAY == 0:
                last_day = d_ms - DAY
                i = idx.get(last_day)
                if i is None:
                    cur_s = cur_c = 0.0  # 결측이면 둘 다 0 (같은 보수적 처리)
                else:
                    hist = closes[: i + 1]
                    a = target_weight(hist, CFG)
                    b = cont_weight(hist, CFG)
                    cur_s = a if a is not None else 0.0
                    cur_c = b if b is not None else 0.0
                    sc = sign_score(hist, CFG)
                    rows.append(
                        (
                            datetime.fromtimestamp(last_day / 1000, UTC).date().isoformat(),
                            sc if sc is not None else 0.0,
                            cur_s,
                            cur_c,
                        )
                    )
            ts[j], tc[j] = cur_s, cur_c
        sigs[s] = Signals({"TSIGN": ts, "TCONT": tc})
        daily_rows[s] = rows
    return sigs, daily_rows


def signal_report(
    rows: dict[str, list[tuple[str, float, float, float]]], start: str
) -> dict[str, Any]:
    """약한 추세 노출 · 전환 · 목표 비중 변화 (신호만, 체결 전)."""
    out: dict[str, Any] = {}
    for s, rs in rows.items():
        rs = [r for r in rs if r[0] >= start]
        by_cat: dict[str, list[tuple[float, float]]] = defaultdict(list)
        for _d, sc, a, b in rs:
            by_cat[f"{sc:.2f}"].append((a, b))
        sw_a = sum(1 for x, y in pairwise(rs) if (x[2] > 0) != (y[2] > 0))
        sw_b = sum(1 for x, y in pairwise(rs) if (x[3] > 0) != (y[3] > 0))
        dw_a = [abs(y[2] - x[2]) for x, y in pairwise(rs)]
        dw_b = [abs(y[3] - x[3]) for x, y in pairwise(rs)]
        out[s] = {
            "days": len(rs),
            "avg_target_by_sign_score": {
                k: {
                    "n": len(v),
                    "TSIGN": round(statistics.fmean(x for x, _ in v), 3),
                    "TCONT": round(statistics.fmean(y for _, y in v), 3),
                }
                for k, v in sorted(by_cat.items())
            },
            "on_off_switches": {"TSIGN": sw_a, "TCONT": sw_b},
            "mean_abs_daily_target_change": {
                "TSIGN": round(statistics.fmean(dw_a), 4),
                "TCONT": round(statistics.fmean(dw_b), 4),
            },
            "days_target_change_gt_band": {
                "TSIGN": sum(1 for x in dw_a if x > CFG.band),
                "TCONT": sum(1 for x in dw_b if x > CFG.band),
            },
        }
    return out


SENS: dict[str, Params] = {
    "main": BASE,
    "cost_x2": replace(BASE, cost=0.0019),
    "fill_worst": replace(BASE, fill="worst"),
    "participation_0.1pct": replace(BASE, participation=0.001),
    "funding_price_proxy": replace(BASE, use_mark=False),
    "cost_realistic_ref": replace(BASE, cost=0.00023),
}


def judge(
    cand: dict[str, Any], base: dict[str, Any], same_sign: bool
) -> tuple[str, dict[str, Any]]:
    """문서 213 §7 — 주 지표는 샤프 차이 하나."""
    a = [r for _, r in cand["_returns"]]
    b = [r for _, r in base["_returns"]]
    bs = block_bootstrap([x - y for x, y in zip(a, b, strict=True)], a, b)
    ha, hb = cand["halves_sharpe"], base["halves_sharpe"]
    better = all(ha[k] > hb[k] for k in ha)
    worse = all(ha[k] < hb[k] for k in ha)
    if not same_sign:
        word = "판단 불가"
    elif bs["sharpe_diff"] > 0 and bs["p_sharpe_diff_gt0"] >= 0.9 and better:
        word = "유리"
    elif bs["sharpe_diff"] < 0 and (bs["p_sharpe_diff_gt0"] <= 0.1 or worse):
        word = "불리"
    else:
        word = "판단 불가"
    return word, {**bs, "halves_better": better, "halves_worse": worse}


def fill_stats(
    markets: dict[str, Market], sigs: dict[str, Signals], v: str, p: Params
) -> dict[str, Any]:
    res = simulate(markets, sigs, v, p)
    bk = res["book"]
    by_dec: dict[tuple[str, int], float] = defaultdict(float)
    for s, i, nt in bk.fill_log:
        by_dec[(s, i // (H4 // MIN))] += nt
    sizes = [abs(x) for x in by_dec.values()]
    return {
        "order_events": len(by_dec),
        "mean_abs_order_notional": round(statistics.fmean(sizes), 2) if sizes else 0.0,
        "fills": len(bk.fill_log),
    }


def run() -> int:
    mk = _load()
    spot = spot_closes()
    sigs, rows = build(mk, spot)
    runs: dict[str, dict[str, dict[str, Any]]] = {}
    for name, p in SENS.items():
        runs[name] = {v: summarize(mk, sigs, v, p) for v in ("TSIGN", "TCONT")}
        print(
            name,
            {
                v: (runs[name][v]["sharpe"], runs[name][v]["cagr"], runs[name][v]["mdd"])
                for v in ("TSIGN", "TCONT")
            },
            flush=True,
        )
    signs = [
        runs[s]["TCONT"]["sharpe"] > runs[s]["TSIGN"]["sharpe"]
        for s in SENS
        if s != "cost_realistic_ref"
    ]
    word, stat = judge(runs["main"]["TCONT"], runs["main"]["TSIGN"], all(signs) or not any(signs))
    a = [r for _, r in runs["main"]["TCONT"]["_returns"]]
    b = [r for _, r in runs["main"]["TSIGN"]["_returns"]]
    out: dict[str, Any] = {
        "made": datetime.now(UTC).isoformat(),
        "params": asdict(BASE),
        "trend_config": asdict(CFG),
        "pilot": word,
        "stat": stat,
        "daily_corr": round(statistics.correlation(a, b), 3),
        "sens_sharpe_diff": {
            s: round(runs[s]["TCONT"]["sharpe"] - runs[s]["TSIGN"]["sharpe"], 3) for s in SENS
        },
        "signals": signal_report(rows, "2020-02-29"),
        "orders": {v: fill_stats(mk, sigs, v, BASE) for v in ("TSIGN", "TCONT")},
        "runs": {
            s: {v: {k: x for k, x in d.items() if k != "_returns"} for v, d in vs.items()}
            for s, vs in runs.items()
        },
    }
    REPORT.mkdir(parents=True, exist_ok=True)
    (REPORT / "result.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1, default=str), encoding="utf-8"
    )
    m = runs["main"]
    for v in ("TSIGN", "TCONT"):
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
                    "gross_pnl",
                    "cost",
                    "funding",
                    "stop_extra",
                    "avg_exposure",
                    "max_exposure",
                    "vol",
                    "turnover_x_per_year",
                    "trades",
                    "stops",
                )
            },
        )
    print("결론", word, json.dumps(stat, ensure_ascii=False), "상관", out["daily_corr"])
    return 0


def main(argv: list[str]) -> int:
    if argv[:1] != ["run"]:
        print(__doc__)
        return 2
    return run()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))


__all__ = ["cont_weight", "date", "sign_score", "target_weight"]
