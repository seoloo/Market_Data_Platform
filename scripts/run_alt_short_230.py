"""문서 230 — 229 숏 규칙 3 개를 알트 15 개에서 그대로 재현. 주문 · 실거래와 무관.

사용 예::

    python -m scripts.run_alt_short_230 fetch   # 바이낸스 공개 API → data/research/alt_short_230/
    python -m scripts.run_alt_short_230 run     # reports/alt_short_230/result.json · 화면
"""

from __future__ import annotations

import json
import math
import random
import statistics
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

REPO = Path(__file__).resolve().parent.parent
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

from auto_trading.research.short_search_229 import (  # noqa: E402
    H4,
    Trade,
    TradeCache,
    build_coin,
    run_config,
    total,
)

COINS = (
    "XRPUSDT", "BNBUSDT", "ADAUSDT", "DOGEUSDT", "SOLUSDT", "LINKUSDT", "LTCUSDT", "BCHUSDT",
    "DOTUSDT", "TRXUSDT", "XLMUSDT", "ETCUSDT", "ATOMUSDT", "AVAXUSDT", "EOSUSDT",
)  # fmt: skip
RULES = {
    "H1": ("R5", "T2", "E5", "S3"),
    "H2": ("R5", "T8", "E2", "S3"),
    "H3": ("R4", "T4", "E2", "S3"),
}
T_NEED = {"H1": 2.0, "H2": 2.4, "H3": 2.4}
RAW = REPO / "data" / "research" / "alt_short_230"
OUT = REPO / "reports" / "alt_short_230"
FAPI = "https://fapi.binance.com/fapi/v1"


def _ms(day: str) -> int:
    return int(datetime.fromisoformat(day).replace(tzinfo=UTC).timestamp() * 1000)


FROM, SPLIT, TO = _ms("2020-06-01"), _ms("2024-01-01"), _ms("2026-09-01")
HIST_FROM = _ms("2019-09-01")  # 일봉 EMA200 준비용


def fetch() -> int:
    RAW.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=30.0) as c:
        for s in COINS:
            bars: list[list[Any]] = []
            t = HIST_FROM
            while t < TO + 3 * 86_400_000:
                time.sleep(0.3)
                r = c.get(
                    f"{FAPI}/klines",
                    params={"symbol": s, "interval": "4h", "startTime": t, "limit": 1500},
                )
                r.raise_for_status()
                rows = r.json()
                if not rows:
                    break
                bars += rows
                t = int(rows[-1][0]) + 1
                if len(rows) < 1500:
                    break
            fund: list[dict[str, Any]] = []
            t = HIST_FROM
            while True:
                time.sleep(0.3)
                r = c.get(
                    f"{FAPI}/fundingRate", params={"symbol": s, "startTime": t, "limit": 1000}
                )
                r.raise_for_status()
                rows = r.json()
                if not rows:
                    break
                fund += rows
                t = int(rows[-1]["fundingTime"]) + 1
                if len(rows) < 1000:
                    break
            (RAW / f"{s}.json").write_text(
                json.dumps({"klines": bars, "funding": fund}), encoding="utf-8"
            )
            first = datetime.fromtimestamp(bars[0][0] / 1000, UTC).date() if bars else None
            print(f"{s}: 4 시간봉 {len(bars)} (첫 {first}) · funding {len(fund)}", flush=True)
    return 0


def _coin(s: str) -> Any:
    d = json.loads((RAW / f"{s}.json").read_text(encoding="utf-8"))
    bars = [
        (int(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5]))
        for k in d["klines"]
        if int(k[0]) + H4 <= TO + 3 * 86_400_000
    ]
    fund = [(int(f["fundingTime"]), float(f["fundingRate"])) for f in d["funding"]]
    return build_coin(s, bars, fund)


def _stats(rets: list[float]) -> dict[str, Any]:
    if len(rets) < 2:
        return {"n": len(rets), "mean": statistics.fmean(rets) if rets else None, "t": None}
    sd = statistics.stdev(rets)
    return {
        "n": len(rets),
        "mean": statistics.fmean(rets),
        "t": statistics.fmean(rets) / sd * math.sqrt(len(rets)) if sd > 0 else None,
        "win": sum(r > 0 for r in rets) / len(rets),
    }


def _random_means(
    cache: TradeCache, counts: dict[str, int], ex: str, sp: str, reps: int = 1000
) -> list[float]:
    rng = random.Random(230)
    pools = {}
    for name, k in cache.coins.items():
        lo = next((i for i, t in enumerate(k.t) if t >= FROM), len(k.t))
        hi = next((i for i, t in enumerate(k.t) if t >= TO), len(k.t))
        pools[name] = list(range(lo, hi))
    out = []
    for _ in range(reps):
        rets: list[float] = []
        for name, n in counts.items():
            busy: list[tuple[int, int]] = []
            got = tries = 0
            while got < n and tries < n * 200 and pools[name]:
                tries += 1
                tr = cache.get(name, rng.choice(pools[name]), ex, sp)
                if tr is None or any(not (tr.exit_t <= a or tr.entry_t >= b) for a, b in busy):
                    continue
                busy.append((tr.entry_t, tr.exit_t))
                rets.append(tr.ret)
                got += 1
        out.append(statistics.fmean(rets) if rets else 0.0)
    return out


def run() -> int:
    coins = [_coin(s) for s in COINS]
    cache = TradeCache(coins)
    opt = TradeCache(coins, optimistic=True)
    res: dict[str, Any] = {}
    for name, cfg in RULES.items():
        trades: list[Trade] = run_config(cache, *cfg, FROM, TO)
        rets = [t.ret for t in trades]
        pooled = _stats(rets)
        halves = [
            _stats([t.ret for t in trades if t.entry_t < SPLIT]),
            _stats([t.ret for t in trades if t.entry_t >= SPLIT]),
        ]
        per_coin = {}
        for s in COINS:
            tr = [t for t in trades if t.coin == s]
            per_coin[s] = {"n": len(tr), "total": total(tr), "stops": sum(t.stopped for t in tr)}
        eligible = [v for v in per_coin.values() if v["n"] >= 10]
        share_pos = sum(v["total"] > 0 for v in eligible) / len(eligible) if eligible else 0.0
        counts = {s: v["n"] for s, v in per_coin.items() if v["n"]}
        rnd = _random_means(cache, counts, cfg[2], cfg[3])
        opt_tr = run_config(opt, *cfg, FROM, TO)
        mean = pooled["mean"] or 0.0
        if mean <= 0:
            verdict = "불리"
        elif (
            (pooled["t"] or 0) >= T_NEED[name]
            and share_pos >= 0.6
            and all((h["mean"] or 0) > 0 for h in halves)
        ):
            verdict = "지지"
        else:
            verdict = "불명"
        res[name] = {
            "cfg": cfg,
            "pooled": pooled,
            "halves": halves,
            "coins_pos_share(>=10건)": share_pos,
            "coins_eligible": len(eligible),
            "per_coin": per_coin,
            "random_mean_median": statistics.median(rnd),
            "random_share_below": sum(x < mean for x in rnd) / len(rnd),
            "optimistic_cost(참고)": _stats([t.ret for t in opt_tr]),
            "stops": sum(t.stopped for t in trades),
            "verdict": verdict,
        }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "result.json").write_text(
        json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(json.dumps(res, ensure_ascii=False, indent=1))
    return 0


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else ""
    if cmd == "fetch":
        return fetch()
    if cmd == "run":
        return run()
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
