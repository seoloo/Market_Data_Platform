"""문서 229 — 지표 전부 × Laya 숏 진입 탐색. 주문 · 실거래와 무관.

사용 예::

    python -m scripts.run_short_search_229 train   # TRAIN 탐색 → Laya → 최종 5 개 고정
    python -m scripts.run_short_search_229 refine  # §4-1 Claude 거부 규칙 더해 다시 고정
    python -m scripts.run_short_search_229 oos     # 봉인 구간 1 회 (selection.json 커밋 뒤)
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import statistics
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

from auto_trading.ops.laya_client import from_env  # noqa: E402
from auto_trading.research.short_search_229 import (  # noqa: E402
    EXITS,
    H4,
    REGIMES,
    STOPS,
    TRIGGERS,
    Coin,
    TradeCache,
    build_coin,
    claude_veto,
    laya_text,
    random_totals,
    run_config,
    total,
    tstat,
)

DB = REPO / "data" / "trading.db"
RAW = REPO / "data" / "research" / "short_search_229"
OUT = REPO / "reports" / "short_search_229"
COINS = ("BTCUSDT", "ETHUSDT")
LAYA_Q = {
    "short": {
        "type": "choice",
        "instructions": "Is this a good moment to open a short position on this coin for the "
        "next 1-2 days?",
        "criteria": {
            "good": "a good short entry: price likely to fall over the next 1-2 days",
            "bad": "not a good short entry: price likely to rise or stay flat",
        },
    }
}
MIN_TRAIN = 60
MIN_TRAIN_LAYA = 40
TOP = 20
FINAL = 5
LAYA_TH: tuple[float | None, ...] = (None, 0.5, 0.6, 0.7)


def _ms(day: str) -> int:
    return int(datetime.fromisoformat(day).replace(tzinfo=UTC).timestamp() * 1000)


TRAIN = (_ms("2020-06-01"), _ms("2024-01-01"))
OOS = (_ms("2024-01-01"), _ms("2026-09-01"))


def load() -> list[Coin]:
    coins = []
    with sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True) as c:
        for s in COINS:
            rows = c.execute(
                "SELECT open_ms, open, high, low, close, volume FROM candles"
                " WHERE exchange='binance_usdm' AND symbol=? AND timeframe='1h' ORDER BY open_ms",
                (s,),
            ).fetchall()
            buckets: dict[int, list[tuple[float, float, float, float, float]]] = {}
            for ms, o, h, lo, cl, v in rows:
                buckets.setdefault(int(ms) // H4 * H4, []).append(
                    (float(o), float(h), float(lo), float(cl), float(v))
                )
            bars = [
                (
                    b,
                    x[0][0],
                    max(r[1] for r in x),
                    min(r[2] for r in x),
                    x[-1][3],
                    sum(r[4] for r in x),
                )
                for b, x in sorted(buckets.items())
                if len(x) == 4
            ]
            fund = [
                (int(ms), float(r))
                for ms, r in c.execute(
                    "SELECT funding_ms, rate FROM funding_rates WHERE exchange='binance_usdm'"
                    " AND symbol=? ORDER BY funding_ms",
                    (s,),
                )
            ]
            met: dict[int, tuple[float, float]] = {}
            last: tuple[float, float] | None = None
            bar_ends = [b[0] + H4 for b in bars]
            k = 0
            for at, oi, ratio in c.execute(
                "SELECT at_ms, open_interest, toptrader_account_ratio FROM perp_metrics"
                " WHERE symbol=? ORDER BY at_ms",
                (s,),
            ):
                while k < len(bar_ends) and bar_ends[k] <= int(at):
                    if last is not None:
                        met[bar_ends[k]] = last
                    k += 1
                if oi is not None and ratio is not None:
                    last = (float(oi), float(ratio))
            coins.append(build_coin(s, bars, fund, met))
            print(
                f"{s}: 4 시간봉 {len(bars)} · funding {len(fund)} · 지표 시각 {len(met)}",
                flush=True,
            )
    return coins


def _key(text: str) -> str:
    return hashlib.sha1(text.encode()).hexdigest()


def _summ(trades: list[Any]) -> dict[str, Any]:
    r = [x.ret for x in trades]
    return {
        "n": len(trades),
        "total": total(trades),
        "t": tstat(trades),
        "win": sum(x > 0 for x in r) / len(r) if r else None,
        "mean": statistics.fmean(r) if r else None,
        "stops": sum(x.stopped for x in trades),
    }


def train() -> int:
    t0 = time.time()
    coins = load()
    cache = TradeCache(coins)
    rows = []
    for reg in REGIMES:
        for trg in TRIGGERS:
            for ex in EXITS:
                for sp in STOPS:
                    tr = run_config(cache, reg, trg, ex, sp, *TRAIN)
                    s = _summ(tr)
                    rows.append({"cfg": [reg, trg, ex, sp], **s})
        print(f"{reg} 끝 · {time.time() - t0:.0f} 초", flush=True)
    ok = [r for r in rows if r["n"] >= MIN_TRAIN and r["t"] == r["t"]]
    ok.sort(key=lambda r: -r["t"])
    top = ok[:TOP]
    # Laya — 상위 20 개의 모든 진입 순간 (TRAIN + OOS). OOS 답은 받아만 둔다.
    RAW.mkdir(parents=True, exist_ok=True)
    apath = RAW / "laya_answers.json"
    answers: dict[str, Any] = (
        json.loads(apath.read_text(encoding="utf-8")) if apath.exists() else {}
    )
    want: dict[str, str] = {}
    for r in top:
        for period in (TRAIN, OOS):
            for x in run_config(cache, *r["cfg"], *period):
                text = laya_text(cache.coins[x.coin], x.signal)
                want[_key(text)] = text
    todo = [k for k in want if k not in answers]
    laya = from_env(REPO)
    if laya is None:
        raise SystemExit("Laya 키 없음")
    try:
        res = laya.batch([want[k] for k in todo], LAYA_Q)
    finally:
        laya.close()
    for k, a in zip(todo, res, strict=True):
        if a is not None:
            ans = a["answers"]["short"]
            answers[k] = {"choice": ans.get("choice"), "probabilities": ans.get("probabilities")}
    apath.write_text(json.dumps(answers), encoding="utf-8")
    print(
        f"Laya 새로 물음 {len(todo)} · 토큰 {laya.tokens} · {time.time() - t0:.0f} 초", flush=True
    )

    def p_good(coin: str, i: int) -> float:
        a = answers.get(_key(laya_text(cache.coins[coin], i)))
        return float((a or {}).get("probabilities", {}).get("good", 0.0)) if a else 0.0

    chosen = []
    for r in top:
        best = None
        for th in LAYA_TH:
            keep = None if th is None else (lambda c, i, th=th: p_good(c, i) >= th)
            tr = run_config(cache, *r["cfg"], *TRAIN, keep=keep)
            s = _summ(tr)
            if s["n"] < (MIN_TRAIN if th is None else MIN_TRAIN_LAYA) or s["t"] != s["t"]:
                continue
            if best is None or s["t"] > best["train"]["t"]:
                best = {"cfg": r["cfg"], "laya": th, "train": s}
        if best is not None:
            chosen.append(best)
    chosen.sort(key=lambda b: -b["train"]["t"])
    final = chosen[:FINAL]
    sel = {
        "made_at": datetime.now(UTC).isoformat(),
        "configs_tested": len(rows),
        "eligible": len(ok),
        "top20_train": top,
        "final": final,
        "train_t_distribution": {
            "p50": statistics.median(r["t"] for r in ok),
            "p90": sorted(r["t"] for r in ok)[int(0.9 * (len(ok) - 1))],
            "max": ok[0]["t"] if ok else None,
            "share_total_gt0": sum(r["total"] > 0 for r in ok) / len(ok) if ok else None,
        },
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "selection.json").write_text(
        json.dumps(sel, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(
        json.dumps(
            {k: v for k, v in sel.items() if k != "top20_train"}, ensure_ascii=False, indent=1
        )
    )
    return 0


def refine() -> int:
    """229 §4-1 — 상위 20 × Laya 문턱 4 × Claude 거부 끔/켬 중 TRAIN t 최대 (Laya 재호출 없음)."""
    path = OUT / "selection.json"
    sel = json.loads(path.read_text(encoding="utf-8"))
    (OUT / "selection_laya_only.json").write_text(
        json.dumps(sel, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    coins = load()
    cache = TradeCache(coins)
    answers = json.loads((RAW / "laya_answers.json").read_text(encoding="utf-8"))

    def p_good(coin: str, i: int) -> float:
        a = answers.get(_key(laya_text(cache.coins[coin], i)))
        return float((a or {}).get("probabilities", {}).get("good", 0.0)) if a else 0.0

    chosen = []
    for r in sel["top20_train"]:
        best = None
        for th in LAYA_TH:
            for veto in (False, True):

                def keep(c: str, i: int, th: float | None = th, veto: bool = veto) -> bool:
                    if th is not None and p_good(c, i) < th:
                        return False
                    return not (veto and claude_veto(cache.coins[c], i))

                tr = run_config(cache, *r["cfg"], *TRAIN, keep=keep)
                s = _summ(tr)
                need = MIN_TRAIN if th is None and not veto else MIN_TRAIN_LAYA
                if s["n"] < need or s["t"] != s["t"]:
                    continue
                if best is None or s["t"] > best["train"]["t"]:
                    best = {"cfg": r["cfg"], "laya": th, "veto": veto, "train": s}
        if best is not None:
            chosen.append(best)
    chosen.sort(key=lambda b: -b["train"]["t"])
    sel["final"] = chosen[:FINAL]
    sel["refined_at"] = datetime.now(UTC).isoformat()
    path.write_text(json.dumps(sel, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(sel["final"], ensure_ascii=False, indent=1))
    return 0


def oos() -> int:
    sel = json.loads((OUT / "selection.json").read_text(encoding="utf-8"))
    coins = load()
    cache = TradeCache(coins)
    answers = json.loads((RAW / "laya_answers.json").read_text(encoding="utf-8"))

    def p_good(coin: str, i: int) -> float:
        a = answers.get(_key(laya_text(cache.coins[coin], i)))
        return float((a or {}).get("probabilities", {}).get("good", 0.0)) if a else 0.0

    out = []
    for f in sel["final"]:
        th, veto = f["laya"], f.get("veto", False)

        def keep(c: str, i: int, th: float | None = th, veto: bool = veto) -> bool:
            if th is not None and p_good(c, i) < th:
                return False
            return not (veto and claude_veto(cache.coins[c], i))

        tr = run_config(cache, *f["cfg"], *OOS, keep=keep)
        s = _summ(tr)
        s["by_coin"] = {c: total([x for x in tr if x.coin == c]) for c in COINS}
        out.append({"cfg": f["cfg"], "laya": th, "veto": veto, "train": f["train"], "oos": s})
    rep = out[0]
    rnd = (
        random_totals(cache, rep["oos"]["n"], rep["cfg"][2], rep["cfg"][3], *OOS)
        if rep["oos"]["n"]
        else []
    )
    rep_total = rep["oos"]["total"]
    random_info = {
        "median": statistics.median(rnd) if rnd else None,
        "share_below_rep": sum(x < rep_total for x in rnd) / len(rnd) if rnd else None,
    }
    positives = sum(o["oos"]["total"] > 0 for o in out)
    if rep_total <= 0:
        verdict = "불리"
    elif (
        (rep["oos"]["t"] or 0) >= 2.0
        and (random_info["share_below_rep"] or 0) >= 0.95
        and positives >= 3
    ):
        verdict = "지지"
    else:
        verdict = "불명"
    # ---- §5-1 낙관 참고 줄 (판정 아님)
    opt_cache = TradeCache(coins, optimistic=True)

    def keep_for(f: dict[str, Any]):  # type: ignore[no-untyped-def]
        th, veto = f.get("laya"), f.get("veto", False)

        def keep(c: str, i: int) -> bool:
            if th is not None and p_good(c, i) < th:
                return False
            return not (veto and claude_veto(cache.coins[c], i))

        return keep

    optimistic_final = [
        _summ(run_config(opt_cache, *f["cfg"], *OOS, keep=keep_for(f))) for f in sel["final"]
    ]
    top20_oos = [
        {
            "cfg": r["cfg"],
            "normal": total(run_config(cache, *r["cfg"], *OOS)),
            "optimistic": total(run_config(opt_cache, *r["cfg"], *OOS)),
        }
        for r in sel["top20_train"]
    ]
    all_oos = []
    for reg in REGIMES:
        for trg in TRIGGERS:
            for ex in EXITS:
                for sp in STOPS:
                    trn = run_config(cache, reg, trg, ex, sp, *OOS)
                    tro = run_config(opt_cache, reg, trg, ex, sp, *OOS)
                    if len(trn) >= 20:
                        all_oos.append(
                            {
                                "cfg": [reg, trg, ex, sp],
                                "n": len(trn),
                                "normal": total(trn),
                                "optimistic": total(tro),
                            }
                        )
    best_n = max(all_oos, key=lambda r: r["normal"]) if all_oos else None
    best_o = max(all_oos, key=lambda r: r["optimistic"]) if all_oos else None
    optimism = {
        "final5_optimistic_cost": optimistic_final,
        "top20_oos": top20_oos,
        "all_configs_oos": {
            "count": len(all_oos),
            "share_positive_normal": sum(r["normal"] > 0 for r in all_oos) / len(all_oos)
            if all_oos
            else None,
            "share_positive_optimistic": sum(r["optimistic"] > 0 for r in all_oos) / len(all_oos)
            if all_oos
            else None,
            "best_normal_hindsight": best_n,
            "best_optimistic_hindsight": best_o,
        },
    }
    res = {
        "final": out,
        "random_rep": random_info,
        "positives_of_5": positives,
        "verdict": verdict,
        "optimistic_reference(판정 아님)": optimism,
    }
    (OUT / "oos.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(res, ensure_ascii=False, indent=1))
    return 0


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else ""
    if cmd == "train":
        return train()
    if cmd == "refine":
        return refine()
    if cmd == "oos":
        return oos()
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
