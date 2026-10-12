"""문서 227 — Laya 확률 + Claude 판단 규칙 BTC 숏, 후향 파일럿 1 회. 주문 · 실거래와 무관.

사용 예::

    python -m scripts.run_laya_short_227 ask   # Laya 답 받기 (답 저장, 다시 돌려도 같은 답)
    python -m scripts.run_laya_short_227 run   # 판정 → reports/laya_short_227/result.json
"""

from __future__ import annotations

import bisect
import hashlib
import json
import sqlite3
import statistics
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

from auto_trading.ops.laya_client import choice_of, from_env  # noqa: E402
from auto_trading.research.laya_short_227 import (  # noqa: E402
    DAY_MS,
    QUESTION,
    Bar,
    Features,
    claude_ok,
    features,
    laya_ok,
    max_drawdown,
    random_totals,
    simulate,
    state_text,
    total,
)

DB = REPO / "data" / "trading.db"
RAW = REPO / "data" / "research" / "laya_short_227"
OUT = REPO / "reports" / "laya_short_227"


def _ms(day: str) -> int:
    return int(datetime.fromisoformat(day).replace(tzinfo=UTC).timestamp() * 1000)


FROM, TO, SPLIT = _ms("2020-03-01"), _ms("2026-08-31"), _ms("2023-06-01")


def load() -> tuple[dict[int, float], dict[int, Bar], list[tuple[int, float]]]:
    with sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True) as c:
        spot = {
            int(ms): float(cl)
            for ms, cl in c.execute(
                "SELECT open_ms, close FROM candles WHERE exchange='binance_spot'"
                " AND symbol='BTCUSDT' AND timeframe='1d'"
            )
        }
        perp = {
            int(ms): Bar(float(o), float(h), float(lo), float(cl))
            for ms, o, h, lo, cl in c.execute(
                "SELECT open_ms, open, high, low, close FROM candles WHERE exchange='binance_usdm'"
                " AND symbol='BTCUSDT' AND timeframe='1d'"
            )
        }
        fund = [
            (int(ms), float(r))
            for ms, r in c.execute(
                "SELECT funding_ms, rate FROM funding_rates WHERE exchange='binance_usdm'"
                " AND symbol='BTCUSDT' ORDER BY funding_ms"
            )
        ]
    return spot, perp, fund


def all_features(spot: dict[int, float], fund: list[tuple[int, float]]) -> dict[int, Features]:
    days = sorted(spot)
    closes = [spot[d] for d in days]
    ft = [t for t, _ in fund]
    out: dict[int, Features] = {}
    for i, d in enumerate(days):
        if d < FROM or d > TO:
            continue
        end = d + DAY_MS  # d 일봉 마감
        rates = [
            r
            for _, r in fund[bisect.bisect_left(ft, end - 7 * DAY_MS) : bisect.bisect_left(ft, end)]
        ]
        f = features(closes[: i + 1], rates)
        if f is not None:
            out[d] = f
    return out


def ask() -> int:
    spot, _, fund = load()
    feats = all_features(spot, fund)
    RAW.mkdir(parents=True, exist_ok=True)
    path = RAW / "answers.json"
    answers: dict[str, Any] = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    todo = {hashlib.sha1(state_text(f).encode()).hexdigest(): state_text(f) for f in feats.values()}
    todo = {k: v for k, v in todo.items() if k not in answers}
    laya = from_env(REPO)
    if laya is None:
        raise SystemExit("Laya 키 없음")
    keys = list(todo)
    try:
        res = laya.batch([todo[k] for k in keys], QUESTION)
    finally:
        laya.close()
    for k, r in zip(keys, res, strict=True):
        if r is None:
            continue
        ans = r["answers"]["next7"]
        answers[k] = {"choice": ans.get("choice"), "probabilities": ans.get("probabilities")}
    path.write_text(json.dumps(answers), encoding="utf-8")
    print(f"상태 {len(feats)} · 새로 물음 {len(keys)} · 답 {len(answers)} · 토큰 {laya.tokens}")
    return 0


def _summ(trades: list[Any], perp: dict[int, Bar]) -> dict[str, Any]:
    first = [t for t in trades if t.signal_day < SPLIT]
    second = [t for t in trades if t.signal_day >= SPLIT]
    rets = [t.ret for t in trades]
    return {
        "trades": len(trades),
        "total": total(trades),
        "halves": [total(first), total(second)],
        "win_rate": sum(r > 0 for r in rets) / len(rets) if rets else None,
        "mean_ret": statistics.fmean(rets) if rets else None,
        "stops": sum(t.stopped for t in trades),
        "funding_sum": sum(t.funding for t in trades),
        "mdd": max_drawdown(trades, perp),
        "worst": min(rets) if rets else None,
    }


def run() -> int:
    spot, perp, fund = load()
    feats = all_features(spot, fund)
    answers = json.loads((RAW / "answers.json").read_text(encoding="utf-8"))
    lab: dict[int, tuple[str | None, float | None]] = {}
    for d, f in feats.items():
        a = answers.get(hashlib.sha1(state_text(f).encode()).hexdigest())
        c = choice_of({"answers": {"next7": a}}, "next7") if a else None
        p_drop = float((a.get("probabilities") or {}).get("drop", 0.0)) if a else None
        lab[d] = (c.label if c else None, p_drop)
    l0_days = [d for d in feats if laya_ok(*lab[d])]
    l_days = [d for d in l0_days if claude_ok(feats[d])]
    t_days = [d for d, f in feats.items() if f.trend <= 0]
    trades = {
        "L": simulate(l_days, perp, fund),
        "L0(참고)": simulate(l0_days, perp, fund),
        "T": simulate(t_days, perp, fund),
    }
    res: dict[str, Any] = {k: _summ(v, perp) for k, v in trades.items()}
    n = len(trades["L"])
    eligible = sorted(feats)
    rnd = random_totals(eligible, n, perp, fund) if n else []
    lt = res["L"]["total"]
    res["R"] = {
        "n": n,
        "median": statistics.median(rnd) if rnd else None,
        "p95": sorted(rnd)[int(0.95 * (len(rnd) - 1))] if rnd else None,
        "share_below_L": sum(x < lt for x in rnd) / len(rnd) if rnd else None,
    }
    # Laya 확률이 맞았나 (참고): P(drop) 구간별 실제 7 일 −5% 하락 비율
    buckets = [(0, 0.2), (0.2, 0.4), (0.4, 0.5), (0.5, 0.6), (0.6, 1.01)]
    cal: dict[str, Any] = {}
    hits_all = []
    for d in feats:
        e, x = d + DAY_MS, d + 8 * DAY_MS
        if e in perp and x in perp and lab[d][1] is not None:
            hits_all.append((lab[d][1], perp[x].open / perp[e].open - 1 <= -0.05))
    for lo, hi in buckets:
        hs = [h for p, h in hits_all if p is not None and lo <= p < hi]
        cal[f"{lo}-{hi}"] = {"n": len(hs), "drop_rate": (sum(hs) / len(hs)) if hs else None}
    cal["base_rate"] = sum(h for _, h in hits_all) / len(hits_all) if hits_all else None
    choices: dict[str, int] = {}
    for c, _ in lab.values():
        choices[str(c)] = choices.get(str(c), 0) + 1
    res["calibration(참고)"] = cal
    res["laya_choices"] = choices
    res["signals"] = {"L0_days": len(l0_days), "L_days": len(l_days), "T_days": len(t_days)}
    lr = res["L"]
    if n < 20:
        verdict = "표본 부족"
    elif lr["total"] <= 0 or (res["R"]["median"] is not None and lr["total"] <= res["R"]["median"]):
        verdict = "불리"
    elif (
        all(h > 0 for h in lr["halves"])
        and res["R"]["share_below_L"] >= 0.95
        and lr["total"] > res["T"]["total"]
        and lr["mdd"] <= 0.30
    ):
        verdict = "지지"
    else:
        verdict = "불명"
    res["verdict"] = verdict
    res["L_trades"] = [
        {
            "signal": datetime.fromtimestamp(t.signal_day / 1000, UTC).date().isoformat(),
            "ret": round(t.ret, 4),
            "stopped": t.stopped,
        }
        for t in trades["L"]
    ]
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "result.json").write_text(
        json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(
        json.dumps({k: v for k, v in res.items() if k != "L_trades"}, ensure_ascii=False, indent=1)
    )
    return 0


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else ""
    if cmd == "ask":
        return ask()
    if cmd == "run":
        return run()
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
