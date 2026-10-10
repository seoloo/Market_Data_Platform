# ruff: noqa: E501 — 결과 사전 줄이 길다
"""문서 201 — 김프가 없다면 Glide · Cruise 비율 (기록, 결정은 사용자).

사용 예::

    python -m scripts.run_glide_cruise_mix_201 run   # reports/glide_cruise_mix_201/result.json

* 합계 매달 60 만 원, Glide 비중 0 ~ 100%. Cruise 가격 N(김프 없음, 200) 기준 · U(업비트) 참고.
* 197 B 와 같은 91 일 달력 창 부트스트랩(seed 198), 여러 프로세스로 경로를 나눠 돈다(결과는 같다).
"""

from __future__ import annotations

import json
import os
import random
import statistics
import sys
from collections.abc import Sequence
from concurrent.futures import ProcessPoolExecutor
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import scripts.run_brake_184 as m184
from scripts.run_brake_183 import data_real
from scripts.run_cruise_kimp_200 import pair
from scripts.run_cruise_range_197 import (
    END_GLIDE,
    PATHS,
    SEED,
    START,
    WINDOW,
    YEARS,
    build_crypto,
    build_glide,
    combo_stats,
    crypto_rows,
    glide_rows,
    month_end,
    paid_by,
    simulate_cruise,
)
from scripts.run_glide_range_195 import pct

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "reports" / "glide_cruise_mix_201"
TOTAL = 600_000.0
SHARES = (0.0, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 1.0)
HZ = (1, 5, 10)

_W: dict[str, Any] = {}


def _init(g_all: Any, srcs: Any, c_days: Any) -> None:
    _W.update(g_all=g_all, srcs=srcs, c_days=c_days, c_index={d: i for i, d in enumerate(c_days)})


def _one(starts: Sequence[date]) -> dict[str, list[dict[str, Any]]]:
    """경로 하나: 같은 창으로 Glide · Cruise(N · U) → 비율마다 합친 계좌 지표."""
    g_rows: list[Any] = []
    c_rows: dict[str, list[Any]] = {k: [] for k in _W["srcs"]}
    n_c = (date(2000 + YEARS + 1, 1, 1) - date(2000, 1, 1)).days
    for s in starts:
        e = (s + timedelta(days=WINDOW - 1)).isoformat()
        a = s.isoformat()
        g_rows.extend(r for d, r in _W["g_all"] if a <= d <= e)
        for k, src in _W["srcs"].items():
            c_rows[k].extend(src[_W["c_index"][d]] for d in _W["c_days"] if a <= d <= e)
    gd = build_glide(g_rows)
    g_cache: dict[float, Any] = {}
    out: dict[str, list[dict[str, Any]]] = {}
    for k, rows in c_rows.items():
        coins = list(rows[0])
        cd, cpx = build_crypto(rows[:n_c], coins)
        res = []
        for g in SHARES:
            parts = []
            ga, ca = round(TOTAL * g), round(TOTAL * (1 - g))
            if ga:
                if ga not in g_cache:
                    gs = m184.simulate(gd, gd["lev"], ref="T", park="cash", monthly_krw=float(ga))
                    g_cache[ga] = (
                        month_end(gs["nav_days"], gs["vals"]),
                        month_end(gs["nav_days"], paid_by(gs["nav_days"], gs["flows"])),
                    )
                parts.append(g_cache[ga])
            if ca:
                cs = simulate_cruise(cd, cpx, start=START, monthly=float(ca))
                parts.append(
                    (
                        month_end(cs["nav_days"], cs["vals"]),
                        month_end(cs["nav_days"], paid_by(cs["nav_days"], cs["flows"])),
                    )
                )
            res.append(combo_stats(parts))
        out[k] = res
    return out


def boot(coins: tuple[str, ...], with_u: bool) -> dict[str, Any]:
    u, n = pair(coins)
    a = min(next(iter(u.values())))
    dr = data_real()
    qqq = m184.qqq_real()
    starts_all = [
        d
        for d in (date.fromisoformat(a) + timedelta(days=i) for i in range(4000))
        if (d + timedelta(days=WINDOW - 1)).isoformat() <= END_GLIDE
    ]
    g_all = glide_rows(dr, qqq, a, END_GLIDE)
    c_days, src_n = crypto_rows(n, a, END_GLIDE)
    srcs = {"N": src_n}
    if with_u:
        c_days_u, src_u = crypto_rows(u, a, END_GLIDE)
        assert c_days_u == c_days
        srcs["U"] = src_u
    rng = random.Random(SEED + 1)
    n_c = (date(2000 + YEARS + 1, 1, 1) - date(2000, 1, 1)).days
    plans = []
    for _ in range(PATHS):
        st: list[date] = []
        got = 0
        while got < n_c:
            s = rng.choice(starts_all)
            st.append(s)
            got += sum(
                1
                for d in c_days
                if s.isoformat() <= d <= (s + timedelta(days=WINDOW - 1)).isoformat()
            )
        plans.append(st)
    workers = max(1, (os.cpu_count() or 2) - 2)
    with ProcessPoolExecutor(workers, initializer=_init, initargs=(g_all, srcs, c_days)) as ex:
        results = list(ex.map(_one, plans, chunksize=8))
    out: dict[str, Any] = {"source": f"{a} ~ {END_GLIDE}", "coins": list(coins), "paths": PATHS}
    for k in srcs:
        rows_k: dict[str, Any] = {}
        for j, g in enumerate(SHARES):
            rs = [r[k][j] for r in results]
            row: dict[str, Any] = {}
            sh = [x["sharpe"] for x in rs if x.get("sharpe") is not None]
            row["sharpe_median"] = round(statistics.median(sh), 3)
            for h in HZ:
                xs = [x[f"x{h}"] for x in rs if x.get(f"x{h}") is not None]
                row[f"{h}y"] = {**pct(xs), "loss_prob": round(sum(x < 1 for x in xs) / len(xs), 3)}
            row["mdd"] = pct([x["mdd"] for x in rs])
            rows_k[f"glide{int(g * 100)}"] = row
        out[k] = rows_k
    return out


def history() -> dict[str, Any]:
    """실제 2018-02 ~ 2026-08, 매달, Glide 실제 + Cruise E(N · U)."""
    u, n = pair(("BTC", "ETH"))
    dr = {**data_real(), "start": "2018-02-01"}
    dr["days"] = [d for d in dr["days"] if d <= END_GLIDE]
    out: dict[str, Any] = {}
    g_cache: dict[int, Any] = {}
    for k, px in (("N", n), ("U", u)):
        sub = {c: {d: v for d, v in s.items() if d <= END_GLIDE} for c, s in px.items()}
        days = sorted(next(iter(sub.values())))
        rows: dict[str, Any] = {}
        for g in SHARES:
            parts = []
            ga, ca = round(TOTAL * g), round(TOTAL * (1 - g))
            if ga:
                if ga not in g_cache:
                    gs = m184.simulate(
                        dr, m184.qqq_real(), ref="T", park="cash", monthly_krw=float(ga)
                    )
                    g_cache[ga] = (
                        month_end(gs["nav_days"], gs["vals"]),
                        month_end(gs["nav_days"], paid_by(gs["nav_days"], gs["flows"])),
                    )
                parts.append(g_cache[ga])
            if ca:
                cs = simulate_cruise(days, sub, start="2018-02-01", monthly=float(ca))
                parts.append(
                    (
                        month_end(cs["nav_days"], cs["vals"]),
                        month_end(cs["nav_days"], paid_by(cs["nav_days"], cs["flows"])),
                    )
                )
            st = combo_stats(parts)
            months = sorted(set.intersection(*(set(v) for v, _ in parts)))
            last = months[-1]
            final = sum(v[last] for v, _ in parts) / sum(p[last] for _, p in parts)
            rows[f"glide{int(g * 100)}"] = {
                "sharpe": round(st["sharpe"], 3),
                "mdd": round(st["mdd"], 4),
                "final_x": round(final, 3),
            }
        out[k] = rows
    return out


def run() -> dict[str, Any]:
    target = OUT / "result.json"
    if target.exists():
        raise SystemExit(f"이미 계산했다: {target}")
    res = {
        "history": history(),
        "boot_E": boot(("BTC", "ETH"), with_u=True),
        "boot_S3": boot(("BTC", "ETH", "SOL"), with_u=False),
        "computed": datetime.now(UTC).date().isoformat(),
    }
    OUT.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    return res


def main(argv: list[str]) -> int:
    if argv != ["run"]:
        raise SystemExit("usage: python -m scripts.run_glide_cruise_mix_201 run")
    sys.stdout.write(json.dumps(run(), ensure_ascii=False, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            _reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main(sys.argv[1:]))
