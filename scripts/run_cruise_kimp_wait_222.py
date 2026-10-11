"""문서 222 — Cruise 매수 × 김치 프리미엄 ≥ 5% 면 대기, 후향 파일럿 1 회. 주문 · 실거래와 무관.

사용 예::

    python -m scripts.run_cruise_kimp_wait_222     # reports/cruise_kimp_wait_222/result.json · 화면
"""

from __future__ import annotations

import json
import statistics
import sys
from collections.abc import Mapping
from datetime import date, datetime
from pathlib import Path
from typing import Any

import scripts.run_brake_184 as m184
from scripts.run_cruise_kimp_200 import pair
from scripts.run_cruise_range_197 import END_CRYPTO, mdd, simulate_cruise

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "reports" / "cruise_kimp_wait_222"
WAIT = 0.05
WIN_YEARS = 3

Px = Mapping[str, Mapping[str, float]]


def premium(u: Px, n: Px) -> dict[str, dict[str, float]]:
    return {c: {d: u[c][d] / n[c][d] - 1 for d in u[c] if d in n[c]} for c in u}


def blocks(k: Mapping[str, Mapping[str, float]], th: float = WAIT) -> dict[str, set[str]]:
    return {c: {d for d, x in v.items() if x >= th} for c, v in k.items()}


def run_one(px: Px, start: str, end: str, block: Mapping[str, set[str]] | None) -> dict[str, Any]:
    sub = {c: {d: v for d, v in s.items() if d <= end} for c, s in px.items()}
    days = sorted(next(iter(sub.values())))
    s = simulate_cruise(days, sub, start=start, buy_block=block)
    return {
        "irr": m184.irr(s["flows"], s["nav_days"][-1], s["vals"][-1]),
        "final_x": s["vals"][-1] / s["paid"],
        "mdd": mdd(s["navs"]),
        "trades": s["trades"],
    }


def compare(px: Px, start: str, end: str, block: Mapping[str, set[str]]) -> dict[str, Any]:
    base = run_one(px, start, end, None)
    wait = run_one(px, start, end, block)
    return {
        "cruise": base,
        "wait": wait,
        "d_irr": wait["irr"] - base["irr"],
        "d_mdd": wait["mdd"] - base["mdd"],
    }


def windows(px: Px, block: Mapping[str, set[str]]) -> dict[str, Any]:
    diffs = []
    y, m = 2018, 2
    while (y, m) <= (2023, 10):
        start = f"{y}-{m:02d}-01"
        end = date(y + WIN_YEARS, m, 1).isoformat()
        diffs.append(compare(px, start, end, block)["d_irr"])
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return {
        "n": len(diffs),
        "win_share": sum(d > 0 for d in diffs) / len(diffs),
        "median_d_irr": statistics.median(diffs),
        "tie_share": sum(d == 0 for d in diffs) / len(diffs),
    }


def describe_k(k: Mapping[str, Mapping[str, float]], start: str) -> dict[str, Any]:
    return {
        c: {
            "days": sum(1 for d in v if d >= start),
            "share_ge_wait": sum(1 for d, x in v.items() if d >= start and x >= WAIT)
            / max(1, sum(1 for d in v if d >= start)),
        }
        for c, v in k.items()
    }


def verdict(e: Mapping[str, Any], s3: Mapping[str, Any], w: Mapping[str, Any]) -> str:
    if e["d_irr"] <= 0 and s3["d_irr"] <= 0:
        return "불리"
    ok = (
        e["d_irr"] > 0
        and s3["d_irr"] > 0
        and w["win_share"] >= 0.60
        and w["median_d_irr"] > 0
        and e["d_mdd"] <= 0.02
        and s3["d_mdd"] <= 0.02
    )
    return "지지" if ok else "불명"


def main() -> int:
    ue, ne = pair(("BTC", "ETH"))
    us, ns = pair(("BTC", "ETH", "SOL"))
    ke, ks = premium(ue, ne), premium(us, ns)
    be, bs = blocks(ke), blocks(ks)
    e = compare(ue, "2018-02-01", END_CRYPTO, be)
    s3 = compare(us, "2022-03-01", END_CRYPTO, bs)
    w = windows(ue, be)
    res: dict[str, Any] = {
        "wait": WAIT,
        "E": e,
        "S3": s3,
        "windows_E_3y": w,
        "verdict": verdict(e, s3, w),
        "premium_E(참고)": describe_k(ke, "2018-02-01"),
        "premium_S3(참고)": describe_k(ks, "2022-03-01"),
        # 점검: 김프 없는 길에서 같은 대기 날짜를 써도 결과 차이는 '대기 날짜' 효과일 뿐 —
        # 김프로 정한 대기를 N 길에 적용해 웃돈과 무관한 시점 효과를 따로 본다
        "N_path_same_block(참고)": {
            "E": compare(ne, "2018-02-01", END_CRYPTO, be),
            "S3": compare(ns, "2022-03-01", END_CRYPTO, bs),
        },
        "computed": datetime.now().isoformat(timespec="seconds"),  # noqa: DTZ005
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "result.json").write_text(
        json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(json.dumps(res, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
