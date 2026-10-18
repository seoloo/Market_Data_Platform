"""문서 278 — 미장 주도주 GBM 을 나스닥 상장 종목만으로 (A: S&P 모델로 나스닥만 · B: 나스닥만 학습).

사용 예::

    PYTHONPATH="src;.;.venv/Lib/site-packages" build/tcn-venv/Scripts/python.exe \
        -m scripts.run_leader_nasdaq_278 run
"""

from __future__ import annotations

import hashlib
import json
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scripts.run_a1_train import git_state
from scripts.run_leader_hold_275 import START, HoldRun, simulate_hold, summarize
from scripts.run_leader_quality_261 import Fundamentals
from scripts.run_leader_small_276 import thin
from scripts.run_lt_leaders import FEE
from scripts.run_sb_breakout import QQQ, STOCKS, load

from scripts import run_leader_ml_274 as m274

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "reports" / "leader_nasdaq_278"
R275 = REPO / "reports" / "leader_hold_275" / "result.json"
EXCH = REPO / "data" / "research" / "leader_ml_us" / "exchanges_20261018.json"
EXCH_SHA = "96ee745f1b5feeb0a44c299a7c1a28c04c4c3524177082e4e294646906d03239"


def nasdaq() -> set[str]:
    if hashlib.sha256(EXCH.read_bytes()).hexdigest() != EXCH_SHA:
        raise SystemExit("거래소 파일 해시가 문서 278 §0 과 다르다")
    ex = json.loads(EXCH.read_text(encoding="utf-8"))
    return {s for s, v in ex.items() if v == "NAS"}


def only(
    rows: Sequence[dict[str, Any]], prob: Sequence[float], keep: set[str]
) -> tuple[list[dict[str, Any]], list[float]]:
    pairs = [(r, p) for r, p in zip(rows, prob, strict=True) if r["sym"] in keep]
    return [r for r, _p in pairs], [p for _r, p in pairs]


def judge(res: Mapping[str, Any]) -> dict[str, Any]:
    """문서 278 §2 (순수)."""

    def ok(u: str) -> dict[str, bool]:
        a, s = res[u]["A"]["R10x6"], res[u]["SP_GBM"]
        return {
            "sharpe_gain": a["full"]["sharpe"] >= s["full"]["sharpe"] + 0.10,
            "halves": all(a[h]["sharpe"] >= s[h]["sharpe"] for h in ("first", "second")),
            "beats_qqq": a["full"]["sharpe"] > res[u]["QQQ"]["full"]["sharpe"],
        }

    checks = {u: ok(u) for u in ("N1", "N2")}
    if all(all(c.values()) for c in checks.values()):
        status = "나스닥판 우위(탐색)"
    elif all(
        res[u]["A"]["R10x6"]["full"]["sharpe"] < res[u]["SP_GBM"]["full"]["sharpe"]
        for u in ("N1", "N2")
    ):
        status = "S&P 판 유지"
    else:
        status = "차이 불명"
    return {"status": status, "checks": checks}


def evaluate(universe: str, nas: set[str], r275: Mapping[str, Any]) -> dict[str, Any]:
    rows, mk = m274.build_rows(universe)
    prob = m274.walk_forward(rows, mk.days)["GBM"]
    days = mk.days
    test = {r["t"] for r in rows if r["day"] >= START}

    def in_test(p: Mapping[int, list[str]]) -> dict[int, list[str]]:
        return {t: v for t, v in p.items() if t in test}

    sp = in_test(m274.model_picks(rows, prob))
    sp_run = summarize(days, simulate_hold(days, mk.close, sp, START))
    want = r275["results"][universe]["arms"]["A_EQ"]["GBM"]["full"]
    if sp_run["full"] != want:
        raise SystemExit(f"{universe} S&P GBM 이 275 와 다르다")
    nrows, nprob = only(rows, prob, nas)
    a_picks = in_test(m274.model_picks(nrows, nprob))
    # B: 나스닥만으로 정답 · 순위 · 학습
    dvol: dict[str, list[float | None]] = {}
    for sym in mk.close:
        ser = load(STOCKS / f"{sym}.csv")
        mm = {d: c * v for d, c, v in zip(ser.dates, ser.close, ser.volume, strict=True)}
        dvol[sym] = [mm.get(d) for d in days]
    sector = json.loads((STOCKS / "sectors.json").read_text(encoding="utf-8"))["sectors"]
    if universe == "U2":
        pdates, psets = m274.pit_members()

        def members(d: str) -> set[str]:
            return m274.member_at(pdates, psets, d) & nas
    else:

        def members(d: str) -> set[str]:
            return nas

    brows = m274.rows_from(days, mk.close, dvol, sector, Fundamentals(), members)
    bprob = m274.walk_forward(brows, days)["GBM"]
    b_picks = in_test(m274.model_picks(brows, bprob))
    base = in_test(m274.base_picks(nrows, mk))
    ew: dict[int, list[str]] = {}
    for r in nrows:
        if r["t"] in test:
            ew.setdefault(r["t"], []).append(r["sym"])
    out: dict[str, Any] = {"pool_median": len(nrows) / max(1, len({r["t"] for r in nrows}))}
    for name, picks in (("A", a_picks), ("B", b_picks), ("BASE", base)):
        out[name] = {
            plan: summarize(days, simulate_hold(days, mk.close, thin(picks, days, n, 1), START))
            for plan, n in (("R10x6", 10), ("R5x6", 5))
        }
    out["EW"] = summarize(days, simulate_hold(days, mk.close, ew, START))
    out["SP_GBM"] = sp_run
    q = load(QQQ)
    qm = dict(zip(q.dates, q.close, strict=True))
    s0 = next(i for i, d in enumerate(days) if d >= START)
    qc = [qm[d] for d in days[s0:]]
    out["QQQ"] = summarize(days, HoldRun([c / qc[0] * (1 - FEE) for c in qc], []))
    out["last_picks"] = {
        k: {days[t]: p[t] for t in sorted(p)[-2:]} for k, p in (("A", a_picks), ("B", b_picks))
    }
    print(json.dumps({"universe": universe, "done": True}), flush=True)
    return out


def run() -> dict[str, Any]:
    target = OUT / "result.json"
    if target.exists():
        raise SystemExit(f"이미 판정했다: {target}")
    sha, dirty = git_state()
    if dirty:
        raise SystemExit("작업 트리가 깨끗하지 않다 — 커밋 뒤에 실행")
    nas = nasdaq()
    r275 = json.loads(R275.read_text(encoding="utf-8"))
    res = {"N1": evaluate("U1", nas, r275), "N2": evaluate("U2", nas, r275)}
    out = {
        "git": sha,
        "completed_utc": datetime.now(UTC).isoformat(),
        "verdict": judge(res),
        "results": res,
        "note": "문서 278 1 회. S&P 500 중 나스닥 상장 157 종목(진짜 나스닥100 아님).",
    }
    OUT.mkdir(parents=True, exist_ok=True)
    with target.open("x", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    return out


def main(argv: list[str]) -> int:
    if len(argv) > 1 and argv[1] == "run":
        print(json.dumps(run()["verdict"]["status"], ensure_ascii=False))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
