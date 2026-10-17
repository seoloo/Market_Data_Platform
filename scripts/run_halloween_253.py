"""문서 253 — 할로윈 효과(11 ~ 4 월 vs 5 ~ 10 월)를 논문 발표 뒤 자료로. 1회 판정.

사용 예::

    python -m scripts.run_halloween_253 run   # 깨끗한 트리 · 판정 파일 없음일 때만

* 해마다 y: 겨울 W = 4 월 말 종가(y) ÷ 10 월 말 종가(y−1) − 1,
  여름 S = 10 월 말(y) ÷ 4 월 말(y) − 1, D = W − S. 지수 가격(배당 빼고)만.
* 주 판정: S&P 500(^GSPC), y = 2003 ~ 2025 (Bouman · Jacobsen 2002 발표 뒤, 2026 여름은 미완).
  여러 시장 일관성: 13 지수 중 D 평균 > 0 인 수.
* 참고: 발표 전(1929 ~ 2002) S&P 500, 규칙 '11 ~ 4 월만 보유 · 5 ~ 10 월 3 개월 국채'
  vs 보유(나스닥 100 · S&P 500).
"""

from __future__ import annotations

import csv
import itertools
import json
import math
import statistics
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from scripts.run_a1_train import git_state

REPO = Path(__file__).resolve().parent.parent
XMKT = REPO / "data" / "research" / "qs_bottom_xmkt"
FRED = REPO / "data" / "research" / "tradfi" / "fred"
OUT = REPO / "reports" / "halloween_253"
MARKETS = (
    "GSPC", "AXJO", "BSESN", "BVSP", "FCHI", "FTSE", "GDAXI", "GSPTSE",
    "HSI", "KS11", "N225", "TWII",
)  # fmt: skip
MAIN = "GSPC"
OOS = (2003, 2025)
PRE = (1929, 2002)
T_MIN = 2.0
MARKETS_MIN = 9
"""13 지수(12 + 나스닥 100) 중."""


def _csv(path: Path, date_col: str, val_col: str) -> dict[str, float]:
    with path.open(encoding="utf-8") as f:
        return {
            r[date_col]: float(r[val_col])
            for r in csv.DictReader(f)
            if r[val_col] not in ("", ".") and float(r[val_col]) > 0
        }


def month_ends(daily: Mapping[str, float]) -> dict[str, float]:
    """'YYYY-MM' → 그달 마지막 종가."""
    out: dict[str, float] = {}
    for d in sorted(daily):
        out[d[:7]] = daily[d]
    return out


def seasons(me: Mapping[str, float], y: int) -> tuple[float, float] | None:
    """(겨울 11/y−1 ~ 4/y, 여름 5/y ~ 10/y) 수익. 하나라도 없으면 None."""
    keys = (f"{y - 1}-10", f"{y}-04", f"{y}-10")
    if any(k not in me for k in keys):
        return None
    a, b, c = (me[k] for k in keys)
    return b / a - 1, c / b - 1


def diffs(me: Mapping[str, float], lo: int, hi: int) -> dict[int, float]:
    out = {}
    for y in range(lo, hi + 1):
        s = seasons(me, y)
        if s is not None:
            out[y] = s[0] - s[1]
    return out


def t_stat(xs: Sequence[float]) -> float:
    if len(xs) < 3:
        return 0.0
    sd = statistics.stdev(xs)
    return statistics.fmean(xs) / (sd / math.sqrt(len(xs))) if sd > 0 else 0.0


def summary(me: Mapping[str, float], lo: int, hi: int) -> dict[str, Any]:
    d = diffs(me, lo, hi)
    xs = list(d.values())
    w = [s[0] for y in d if (s := seasons(me, y))]
    s_ = [s[1] for y in d if (s := seasons(me, y))]
    return {
        "years": len(xs),
        "first": min(d) if d else None,
        "winter": round(statistics.fmean(w), 4) if w else None,
        "summer": round(statistics.fmean(s_), 4) if s_ else None,
        "d_mean": round(statistics.fmean(xs), 4) if xs else None,
        "t": round(t_stat(xs), 2),
        "d_wins": round(sum(1 for x in xs if x > 0) / len(xs), 3) if xs else None,
    }


def judge(main_mean: float | None, main_t: float, n_pos: int) -> str:
    """사전 등록 §4 (순수)."""
    if main_mean is None or main_mean <= 0:
        return "불리"
    if main_t >= T_MIN and n_pos >= MARKETS_MIN:
        return "지지"
    return "불명"


def rule_path(
    me: Mapping[str, float], bill: Mapping[str, float], lo: int, hi: int
) -> tuple[list[float], list[float]]:
    """월 수익 (보유, 11 ~ 4 월만 보유 · 5 ~ 10 월 국채). 월 = lo−1 년 11 월 ~ hi 년 10 월."""
    months = sorted(me)
    hold: list[float] = []
    rule: list[float] = []
    for prev, cur in itertools.pairwise(months):
        m = int(cur[5:])
        if not (f"{lo - 1}-11" <= cur <= f"{hi}-10"):
            continue
        r = me[cur] / me[prev] - 1
        hold.append(r)
        rule.append(r if m in (11, 12, 1, 2, 3, 4) else bill.get(prev, 0.0) / 100 / 12)
    return hold, rule


def perf(rets: Sequence[float]) -> dict[str, float]:
    if len(rets) < 2:
        return {"cagr": 0.0, "sharpe": 0.0, "mdd": 0.0}
    eq, peak, mdd = 1.0, 1.0, 0.0
    for r in rets:
        eq *= 1 + r
        peak = max(peak, eq)
        mdd = max(mdd, 1 - eq / peak)
    sd = statistics.stdev(rets)
    return {
        "cagr": round(eq ** (12 / len(rets)) - 1, 4),
        "sharpe": round(statistics.fmean(rets) / sd * math.sqrt(12), 3) if sd > 0 else 0.0,
        "mdd": round(mdd, 4),
    }


def load() -> dict[str, dict[str, float]]:
    out = {m: month_ends(_csv(XMKT / f"{m}.csv", "date", "close")) for m in MARKETS}
    out["NDX"] = month_ends(_csv(FRED / "NASDAQ100.csv", "observation_date", "NASDAQ100"))
    return out


def run() -> dict[str, Any]:
    target = OUT / "result.json"
    if target.exists():
        raise SystemExit(f"이미 판정했다: {target}")
    sha, dirty = git_state()
    if dirty:
        raise SystemExit("작업 트리가 깨끗하지 않다 — 커밋 뒤에 실행")
    data = load()
    bill = month_ends(_csv(FRED / "DTB3_full.csv", "observation_date", "DTB3"))
    oos = {m: summary(me, *OOS) for m, me in data.items()}
    n_pos = sum(1 for s in oos.values() if s["d_mean"] is not None and s["d_mean"] > 0)
    main = oos[MAIN]
    rules = {}
    for m in (MAIN, "NDX"):
        h, r = rule_path(data[m], bill, *OOS)
        rules[m] = {"hold": perf(h), "halloween": perf(r), "months": len(h)}
    out = {
        "git": sha,
        "verdict": judge(main["d_mean"], main["t"], n_pos),
        "main": main,
        "markets_positive": n_pos,
        "markets": oos,
        "pre_gspc": summary(data[MAIN], *PRE),
        "rules": rules,
        "note": "문서 253 1 회 판정. 지수 가격만, 2003 ~ 2025 (겨울 2002-11 ~, 여름 ~ 2025-10).",
    }
    OUT.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def main(argv: list[str]) -> int:
    if (argv[1] if len(argv) > 1 else "") != "run":
        print(__doc__)
        return 2
    print(json.dumps(run(), ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
