"""문서 239 — H1 숏 지정가 실행 연구 1 회. 주문 · 실거래와 무관.

사용 예::

    python -m scripts.run_h1_exec_239 fetch   # 거래마다 1 분봉 창 받기 (공개 API)
    python -m scripts.run_h1_exec_239 run     # reports/h1_exec_239/result.json · 화면
"""

from __future__ import annotations

import bisect
import json
import math
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

from scripts.run_alt_short_230 import COINS as ALTS  # noqa: E402
from scripts.run_alt_short_230 import _coin as alt_coin  # noqa: E402
from scripts.run_short_search_229 import load as load_btc_eth  # noqa: E402

from auto_trading.research.h1_exec_239 import (  # noqa: E402
    MAKER,
    TAKER,
    WAIT_MIN,
    entry_filled,
    exit_fill,
    short_return,
)
from auto_trading.research.short_search_229 import (  # noqa: E402
    STOP_SLIP,
    Coin,
    TradeCache,
    run_config,
)

RAW = REPO / "data" / "research" / "h1_exec_239"
OUT = REPO / "reports" / "h1_exec_239"
FAPI = "https://fapi.binance.com/fapi/v1/klines"
H1 = ("R5", "T2", "E5", "S3")
NORMAL_FEE = 0.00095
ZERO = (0.0, 0.0, 0.0, STOP_SLIP)
MIN = 60_000


def _ms(day: str) -> int:
    return int(datetime.fromisoformat(day).replace(tzinfo=UTC).timestamp() * 1000)


FROM, SPLIT, TO = _ms("2020-06-01"), _ms("2024-01-01"), _ms("2026-09-01")


def coins() -> list[Coin]:
    return [*load_btc_eth(), *[alt_coin(s) for s in ALTS]]


def trades() -> list[dict[str, Any]]:
    """H1 거래(비용 0 엔진)와 진입가 · 청산가 · funding · 청산 종류."""
    cs = coins()
    cache = TradeCache(cs)
    cache.cost = ZERO
    by = {k.name: k for k in cs}
    out = []
    for t in run_config(cache, *H1, FROM, TO):
        k = by[t.coin]
        entry = k.o[t.signal + 1]
        lo = bisect.bisect_left(k.fund_t, t.entry_t)
        hi = bisect.bisect_left(k.fund_t, t.exit_t)
        fund = sum(k.fund_r[lo:hi])
        exit_px = entry * (1 - (2 * t.ret - fund))  # 비용 0 엔진의 ret 에서 되돌림
        out.append(
            {
                "coin": t.coin,
                "entry_t": t.entry_t,
                "exit_t": t.exit_t,
                "entry": entry,
                "exit": exit_px,
                "fund": fund,
                "stopped": t.stopped,
            }
        )
    return out


def _path(coin: str) -> Path:
    return RAW / f"{coin}.json"


def fetch() -> int:
    RAW.mkdir(parents=True, exist_ok=True)
    tr = trades()
    need: dict[str, set[int]] = {}
    for x in tr:
        need.setdefault(x["coin"], set()).add(x["entry_t"])
        if not x["stopped"]:
            need[x["coin"]].add(x["exit_t"])
    with httpx.Client(timeout=30.0) as c:
        for coin, starts in sorted(need.items()):
            got: dict[str, Any] = (
                json.loads(_path(coin).read_text(encoding="utf-8")) if _path(coin).exists() else {}
            )
            for s in sorted(starts):
                if str(s) in got:
                    continue
                time.sleep(0.15)
                r = c.get(
                    FAPI,
                    params={"symbol": coin, "interval": "1m", "startTime": s, "limit": WAIT_MIN},
                )
                if r.status_code != 200:
                    time.sleep(5)
                    continue
                got[str(s)] = [
                    [int(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4])]
                    for k in r.json()
                ]
            _path(coin).write_text(json.dumps(got), encoding="utf-8")
            print(f"{coin}: 창 {len(got)} / 필요 {len(starts)}", flush=True)
    return 0


def _stats(r: list[float]) -> dict[str, Any]:
    if len(r) < 2:
        return {"n": len(r), "mean": statistics.fmean(r) if r else None, "t": None}
    sd = statistics.stdev(r)
    return {
        "n": len(r),
        "mean": statistics.fmean(r),
        "t": statistics.fmean(r) / sd * math.sqrt(len(r)) if sd > 0 else None,
        "win": sum(x > 0 for x in r) / len(r),
    }


def _total(r: list[tuple[int, float]]) -> float:
    eq = 1.0
    for _, x in sorted(r):
        eq *= 1 + x
    return eq - 1


def run() -> int:
    tr = trades()
    win: dict[str, dict[str, Any]] = {}
    for coin in {x["coin"] for x in tr}:
        win[coin] = (
            json.loads(_path(coin).read_text(encoding="utf-8")) if _path(coin).exists() else {}
        )
    filled, missed, nodata = [], [], 0
    exit_maker = exit_taker = 0
    for x in tr:
        ew = win[x["coin"]].get(str(x["entry_t"]))
        normal = short_return(x["entry"], x["exit"], x["fund"], NORMAL_FEE, NORMAL_FEE)
        if not ew:
            nodata += 1
            continue
        bars = [tuple(b) for b in ew]
        if not entry_filled(bars, x["entry"]):  # type: ignore[arg-type]
            missed.append({**x, "normal": normal})
            continue
        if x["stopped"]:
            px, fee_out = x["exit"], TAKER
        else:
            xw = win[x["coin"]].get(str(x["exit_t"]))
            f = exit_fill([tuple(b) for b in xw], x["exit"]) if xw else None  # type: ignore[misc]
            if f is None:
                nodata += 1
                continue
            px, fee_out = f
            exit_maker += fee_out == MAKER
            exit_taker += fee_out == TAKER
        real = short_return(x["entry"], px, x["fund"], MAKER, fee_out)
        filled.append({**x, "normal": normal, "real": real})
    rr = [f["real"] for f in filled]
    per_coin = {}
    for coin in sorted({x["coin"] for x in tr}):
        fs = [(f["exit_t"], f["real"]) for f in filled if f["coin"] == coin]
        per_coin[coin] = {"n": len(fs), "total": _total(fs)}
    eligible = [v for v in per_coin.values() if v["n"] >= 10]
    share_pos = sum(v["total"] > 0 for v in eligible) / len(eligible) if eligible else 0.0
    halves = [
        _stats([f["real"] for f in filled if f["entry_t"] < SPLIT]),
        _stats([f["real"] for f in filled if f["entry_t"] >= SPLIT]),
    ]
    attempted = len(filled) + len(missed)
    fill_rate = len(filled) / attempted if attempted else 0.0
    pooled = _stats(rr)
    mean = pooled["mean"] or 0.0
    if mean <= 0:
        verdict = "불리"
    elif (
        (pooled["t"] or 0) >= 2.0
        and all((h["mean"] or 0) > 0 for h in halves)
        and share_pos >= 0.6
        and fill_rate >= 0.5
    ):
        verdict = "지지"
    else:
        verdict = "불명"
    res = {
        "trades_engine": len(tr),
        "no_data": nodata,
        "entry_fill_rate": fill_rate,
        "exit_maker_share(시간 청산)": exit_maker / (exit_maker + exit_taker)
        if exit_maker + exit_taker
        else None,
        "pooled_real": pooled,
        "halves_real": halves,
        "coins_pos_share": share_pos,
        "coins_eligible": len(eligible),
        "per_coin": per_coin,
        "adverse_selection(보통 비용 기준)": {
            "filled": _stats([f["normal"] for f in filled]),
            "missed": _stats([m["normal"] for m in missed]),
        },
        "btc_eth_only": _stats([f["real"] for f in filled if f["coin"] in ("BTCUSDT", "ETHUSDT")]),
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
