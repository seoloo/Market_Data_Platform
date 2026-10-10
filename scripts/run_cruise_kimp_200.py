"""문서 200 — 김치 프리미엄을 빼고 본 Cruise (기록, 판정 없음).

사용 예::

    python -m scripts.run_cruise_kimp_200 run   # reports/cruise_kimp_200/result.json

* U = 업비트 원화 일봉, N = 바이낸스 USDT 일봉 × DEXKOUS (김프 없는 원화 길). 같은 날만 쓴다.
* 엔진 = 197 ``simulate_cruise``. 부트스트랩은 197 A 와 같은 방법 · 같은 덩어리 위치로 U · N 짝지음.
"""

from __future__ import annotations

import csv
import json
import random
import statistics
import sys
from collections.abc import Mapping
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import scripts.run_brake_184 as m184
from scripts.run_cruise_range_197 import (
    END_CRYPTO,
    PATHS,
    SEED,
    START,
    WINDOW,
    YEARS,
    build_crypto,
    crypto_rows,
    horizon_x,
    load,
    mdd,
    paid_by,
    simulate_cruise,
)
from scripts.run_glide_range_195 import pct

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "reports" / "cruise_kimp_200"
BIN = REPO / "data" / "research" / "binance_daily"
FX_CSV = REPO / "data" / "research" / "multi_asset" / "DEXKOUS.csv"
HZ = (1, 5, 10)


def fx_daily() -> dict[str, float]:
    with FX_CSV.open(encoding="utf-8") as f:
        return {
            r["observation_date"]: float(r["DEXKOUS"])
            for r in csv.DictReader(f)
            if r["DEXKOUS"] not in ("", ".")
        }


def no_kimp(coin: str, days: list[str], fx: Mapping[str, float]) -> dict[str, float]:
    """바이낸스 종가 × 그날(없으면 앞) 환율 (순수에 가깝게 — 파일만 읽음)."""
    with (BIN / f"{coin}USDT.csv").open(encoding="utf-8") as f:
        usd = {r["date"]: float(r["close"]) for r in csv.DictReader(f)}
    fx_days = sorted(fx)
    out: dict[str, float] = {}
    k, last = 0, None
    for d in days:
        while k < len(fx_days) and fx_days[k] <= d:
            last = fx[fx_days[k]]
            k += 1
        if d in usd and last is not None:
            out[d] = usd[d] * last
    return out


def pair(coins: tuple[str, ...]) -> tuple[dict[str, dict[str, float]], dict[str, dict[str, float]]]:
    fx = fx_daily()
    u = {c: load(c) for c in coins}
    days = sorted(set.intersection(*(set(v) for v in u.values())))
    n = {c: no_kimp(c, days, fx) for c in coins}
    common = [d for d in days if all(d in n[c] for c in coins)]
    return {c: {d: u[c][d] for d in common} for c in coins}, {
        c: {d: n[c][d] for d in common} for c in coins
    }


def kimp_stats(
    u: Mapping[str, Mapping[str, float]], n: Mapping[str, Mapping[str, float]]
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for c in u:
        ds = sorted(u[c])
        k = [u[c][d] / n[c][d] - 1 for d in ds]
        by_year: dict[str, list[float]] = {}
        for d, x in zip(ds, k, strict=True):
            by_year.setdefault(d[:4], []).append(x)
        out[c] = {
            "median": round(statistics.median(k), 4),
            "mean": round(statistics.fmean(k), 4),
            **{kk: round(v, 4) for kk, v in pct(k).items()},
            "max": [ds[k.index(max(k))], round(max(k), 4)],
            "min": [ds[k.index(min(k))], round(min(k), 4)],
            "first": [ds[0], round(k[0], 4)],
            "last": [ds[-1], round(k[-1], 4)],
            "by_year_mean": {y: round(statistics.fmean(v), 4) for y, v in by_year.items()},
        }
    return out


def history(
    u: Mapping[str, Mapping[str, float]],
    n: Mapping[str, Mapping[str, float]],
    start: str,
    *,
    end: str = END_CRYPTO,
    lump: float = 0.0,
    tax: bool = True,
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name, px in (("U", u), ("N", n)):
        sub = {c: {d: v for d, v in s.items() if d <= end} for c, s in px.items()}
        days = sorted(next(iter(sub.values())))
        s = simulate_cruise(
            days, sub, start=start, monthly=0.0 if lump else 300_000.0, lump=lump, tax=tax
        )
        yrs = (
            datetime.fromisoformat(s["nav_days"][-1]) - datetime.fromisoformat(s["nav_days"][0])
        ).days / 365.25
        out[name] = {
            "cagr_nav": round((s["navs"][-1] / s["navs"][0]) ** (1 / yrs) - 1, 4),
            "irr": round(m184.irr(s["flows"], s["nav_days"][-1], s["vals"][-1]), 4),
            "final_x": round(s["vals"][-1] / s["paid"], 3),
            "mdd": round(mdd(s["navs"]), 4),
            "trades": s["trades"],
        }
    out["period"] = [start, end]
    return out


def boot(
    u: Mapping[str, Mapping[str, float]], n: Mapping[str, Mapping[str, float]]
) -> dict[str, Any]:
    coins = list(u)
    a = min(next(iter(u.values())))
    _, src_u = crypto_rows(u, a, END_CRYPTO)
    _, src_n = crypto_rows(n, a, END_CRYPTO)
    rng = random.Random(SEED)
    days_n = (date(2000 + YEARS + 1, 1, 1) - date(2000, 1, 1)).days
    rows: dict[str, list[dict[str, Any]]] = {"U": [], "N": []}
    for _ in range(PATHS):
        idx: list[int] = []
        got = 0
        while got < days_n:
            i = rng.randrange(0, len(src_u) - WINDOW)
            idx.append(i)
            got += WINDOW
        for name, src in (("U", src_u), ("N", src_n)):
            seq: list[dict[str, float]] = []
            for i in idx:
                seq.extend(src[i : i + WINDOW])
            days, sp = build_crypto(seq[:days_n], coins)
            s = simulate_cruise(days, sp, start=START)
            paid = paid_by(s["nav_days"], s["flows"])
            rows[name].append({**horizon_x(s["nav_days"], s["vals"], paid), "mdd": mdd(s["navs"])})
    out: dict[str, Any] = {"source": f"{a} ~ {END_CRYPTO}", "paths": PATHS}
    for name, rs in rows.items():
        r: dict[str, Any] = {}
        for h in HZ:
            xs = [x[f"x{h}"] for x in rs if x.get(f"x{h}") is not None]
            r[f"{h}y"] = {**pct(xs), "loss_prob": round(sum(x < 1 for x in xs) / len(xs), 3)}
        r["mdd"] = pct([x["mdd"] for x in rs])
        out[name] = r
    ratio = [
        nn["x10"] / uu["x10"]
        for nn, uu in zip(rows["N"], rows["U"], strict=True)
        if nn.get("x10") and uu.get("x10")
    ]
    out["N_over_U_10y"] = {
        **pct(ratio),
        "share_N_better": round(sum(x > 1 for x in ratio) / len(ratio), 3),
    }
    return out


def run() -> dict[str, Any]:
    target = OUT / "result.json"
    if target.exists():
        raise SystemExit(f"이미 계산했다: {target}")
    ue, ne = pair(("BTC", "ETH"))
    us, ns = pair(("BTC", "ETH", "SOL"))
    res = {
        "kimp_E": kimp_stats(ue, ne),
        "kimp_SOL": kimp_stats({"SOL": us["SOL"]}, {"SOL": ns["SOL"]}),
        "check_190": history(ue, ne, "2020-03-01", end="2026-08-31", lump=10_000_000.0, tax=False),
        "hist_E": history(ue, ne, "2018-02-01"),
        "hist_S3": history(us, ns, "2022-03-01"),
        "boot_E": boot(ue, ne),
        "boot_S3": boot(us, ns),
        "computed": datetime.now(UTC).date().isoformat(),
    }
    OUT.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    return res


def main(argv: list[str]) -> int:
    if argv != ["run"]:
        raise SystemExit("usage: python -m scripts.run_cruise_kimp_200 run")
    sys.stdout.write(json.dumps(run(), ensure_ascii=False, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            _reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main(sys.argv[1:]))
