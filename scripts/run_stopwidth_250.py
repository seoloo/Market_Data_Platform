"""문서 250 — 💹 손절 한 번 0.3% 고정, 종목 손절 폭 −3% · −5% · −8% (비중 = 0.3% ÷ 폭). 1회 판정.

사용 예::

    python -m scripts.run_stopwidth_250 run   # 깨끗한 트리 · 판정 파일 없음일 때만

242 의 판(113 미국 · 141 국장 주도주 진입, 추매 없음, 진입가 고정 손절만)을 쓰고
손절 폭과 비중만 바꾼다.
비중이 작으면 같은 돈을 더 많은 종목에 담도록 최대 종목 수 = min(25, 1 ÷ 비중).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from scripts.run_a1_train import git_state
from scripts.run_lt_leaders import FROM

from scripts import run_exit_242 as base

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "reports" / "stopwidth_250"
RISK = 0.003
WIDTHS = (0.03, 0.05, 0.08)
MAX_CAP = 25
SHARPE_GAIN = 0.10
MDD_SLACK = 0.05


def arm_name(width: float) -> str:
    return f"S{round(width * 100)}"


def slots(width: float) -> int:
    return min(MAX_CAP, round(width / RISK))


def one(mk: base.Market, width: float) -> dict[str, Any]:
    old = (base.RISK, base.CAP, base.MAX_POS)
    w = RISK / width
    base.RISK, base.CAP, base.MAX_POS = RISK, w, slots(width)
    try:
        run = base.simulate_exit(mk, base.Exit(arm_name(width), width, None, None))
    finally:
        base.RISK, base.CAP, base.MAX_POS = old
    start = next(i for i, d in enumerate(mk.days) if d >= FROM)
    stops = [x for x in run.trades if x["reason"] == "stop"]
    return {
        **base._row(mk.days[start:], run.equity),
        "weight": round(w, 4),
        "max_pos": slots(width),
        "trade": base.trade_stats(mk, run),
        "hit_avg": round(sum(x["ret"] * x["w"] for x in stops) / len(stops), 4) if stops else 0,
    }


def judge(kr: dict[str, Any]) -> str:
    """사전 등록 §4 (순수): S5 · S8 중 국장 샤프 S3 + 0.10 · 두 반쪽 S3 이상 · 낙폭 +5%p 안."""
    s3 = kr["S3"]
    ok = [
        n
        for n in ("S5", "S8")
        if kr[n]["full"]["sharpe"] >= s3["full"]["sharpe"] + SHARPE_GAIN
        and all(kr[n][h]["sharpe"] >= s3[h]["sharpe"] for h in ("first", "second"))
        and kr[n]["full"]["mdd"] <= s3["full"]["mdd"] + MDD_SLACK
    ]
    return "ADOPT_" + max(ok, key=lambda n: kr[n]["full"]["sharpe"]) if ok else "KEEP_S3"


def run() -> dict[str, Any]:
    target = OUT / "verdict.json"
    if target.exists():
        raise SystemExit(f"이미 판정했다: {target}")
    sha, dirty = git_state()
    if dirty:
        raise SystemExit("작업 트리가 깨끗하지 않다 — 커밋 뒤에 실행")
    kr_mk = base.kr_market()[0]
    kr = {arm_name(w): one(kr_mk, w) for w in WIDTHS}
    us_mk = base.us_market()[0]
    us = {arm_name(w): one(us_mk, w) for w in WIDTHS}
    out = {"git": sha, "verdict": judge(kr), "kr": kr, "us_reference": us}
    OUT.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def main(argv: list[str]) -> int:
    if argv[1:] != ["run"]:
        print(__doc__)
        return 2
    print(json.dumps({"verdict": run()["verdict"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
