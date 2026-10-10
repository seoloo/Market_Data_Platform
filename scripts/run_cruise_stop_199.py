# ruff: noqa: E501 — 결과 사전 줄이 길다
"""문서 199 — Cruise 30% 정지 뒤 언제 풀까 (비교 기록, 결정은 사용자).

사용 예::

    python -m scripts.run_cruise_stop_199 run   # reports/cruise_stop_199/result.json

* 엔진 = 197 ``simulate_cruise`` + ``stop``. P0 정지 없음 · P1 안 풂 · P2 30 일 · P3 90 일 · P4 BTC 추세 회복.
* 실제 역사 E(BTC + ETH 2018-02 ~) · S3(+ SOL 2022-03 ~), 매달 30 만 원.
* 부트스트랩: 197 A 와 같은 경로(seed 197, 91 일 덩어리, 400 개, 10 년).
"""

from __future__ import annotations

import json
import random
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
OUT = REPO / "reports" / "cruise_stop_199"
POLICIES: dict[str, str | None] = {
    "P0": None,
    "P1": "never",
    "P2": "days30",
    "P3": "days90",
    "P4": "trend",
}
HZ = (1, 5, 10)


def history(px: Mapping[str, Mapping[str, float]], start: str) -> dict[str, Any]:
    days = sorted(d for d in next(iter(px.values())) if all(d in px[c] for c in px))
    out: dict[str, Any] = {"period": [start, days[-1]]}
    for k, pol in POLICIES.items():
        s = simulate_cruise(days, px, start=start, stop=pol)
        out[k] = {
            "irr": round(m184.irr(s["flows"], s["nav_days"][-1], s["vals"][-1]), 4),
            "final_x": round(s["vals"][-1] / s["paid"], 3),
            "mdd": round(mdd(s["navs"]), 4),
            "halts": s["halts"],
            "halted_share": round(s["halted_days"] / len(s["nav_days"]), 3),
            "stops": s["stop_days"],
        }
    return out


def boot(px: Mapping[str, Mapping[str, float]]) -> dict[str, Any]:
    coins = list(px)
    a = max(min(v) for v in px.values())
    _, src = crypto_rows(px, a, END_CRYPTO)
    rng = random.Random(SEED)
    n = (date(2000 + YEARS + 1, 1, 1) - date(2000, 1, 1)).days
    rows_by: dict[str, list[dict[str, Any]]] = {k: [] for k in POLICIES}
    for _ in range(PATHS):
        rows: list[dict[str, float]] = []
        while len(rows) < n:
            i = rng.randrange(0, len(src) - WINDOW)
            rows.extend(src[i : i + WINDOW])
        days, sp = build_crypto(rows[:n], coins)
        for k, pol in POLICIES.items():
            s = simulate_cruise(days, sp, start=START, stop=pol)
            paid = paid_by(s["nav_days"], s["flows"])
            rows_by[k].append(
                {
                    **horizon_x(s["nav_days"], s["vals"], paid),
                    "mdd": mdd(s["navs"]),
                    "halts": s["halts"],
                    "halted_share": s["halted_days"] / len(s["nav_days"]),
                }
            )
    out: dict[str, Any] = {"source": f"{a} ~ {END_CRYPTO}", "paths": PATHS}
    base = rows_by["P0"]
    for k, rs in rows_by.items():
        r: dict[str, Any] = {}
        for h in HZ:
            xs = [x[f"x{h}"] for x in rs if x.get(f"x{h}") is not None]
            r[f"{h}y"] = {**pct(xs), "loss_prob": round(sum(x < 1 for x in xs) / len(xs), 3)}
        r["mdd"] = pct([x["mdd"] for x in rs])
        r["halts_mean"] = round(sum(x["halts"] for x in rs) / len(rs), 2)
        r["halted_share_mean"] = round(sum(x["halted_share"] for x in rs) / len(rs), 3)
        diffs = [
            x["x10"] / b["x10"] - 1
            for x, b in zip(rs, base, strict=True)
            if x.get("x10") and b.get("x10")
        ]
        r["vs_P0_10y"] = {
            **{kk: round(v * 100, 2) for kk, v in pct(diffs).items()},
            "share_better": round(sum(dv > 1e-9 for dv in diffs) / len(diffs), 3),
            "share_worse": round(sum(dv < -1e-9 for dv in diffs) / len(diffs), 3),
        }
        out[k] = r
    return out


def run() -> dict[str, Any]:
    target = OUT / "result.json"
    if target.exists():
        raise SystemExit(f"이미 계산했다: {target}")
    e = {c: load(c) for c in ("BTC", "ETH")}
    s3 = {c: load(c) for c in ("BTC", "ETH", "SOL")}
    res = {
        "hist_E": history(e, "2018-02-01"),
        "hist_S3": history(s3, "2022-03-01"),
        "boot_E": boot(e),
        "boot_S3": boot(s3),
        "computed": datetime.now(UTC).date().isoformat(),
    }
    OUT.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    return res


def main(argv: list[str]) -> int:
    if argv != ["run"]:
        raise SystemExit("usage: python -m scripts.run_cruise_stop_199 run")
    sys.stdout.write(json.dumps(run(), ensure_ascii=False, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            _reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main(sys.argv[1:]))
