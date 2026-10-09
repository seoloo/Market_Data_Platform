"""문서 191 — 주도주를 더 일찍 잡으면 벌 수 있나 (진입 시점 사건 연구).

사용 예::

    python -m scripts.run_early_leader_191 run   # 1 회 판정 → reports/early_leader_191/result.json

* 사전 등록: docs/research/191-early-leader-entry-preregistration.md.
* 판정: 유가증권시장(141 자료, 상장폐지 포함). 보고: 미국 S&P 500 지금 종목(113 자료, 생존 편향).
"""

from __future__ import annotations

import json
import statistics
import sys
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scripts.run_lt_kr import UNKNOWN, build_panel, data_hash, members

from auto_trading.ops.lt_paper import TOP_SECTORS, TOP_STOCKS
from auto_trading.research.theme_leaders import nw_se

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "reports" / "early_leader_191"
EXPECTED_SHA = "58588878f98dac2c5532f4de4422d11cad44ed3d591f5510fe0ce47f06f4fe68"
BASE = (21, 63, 126)
EARLY = (5, 21, 63)
GAP = 20
HOLD = 63
COST = 0.005
FIRST, LAST = "2008-01-01", "2026-05-31"
Z = 2.0

Closes = Mapping[str, Sequence[float | None]]


def leaders_generic(
    close: Closes,
    sector: Mapping[str, str],
    pool: Sequence[str],
    t: int,
    looks: Sequence[int],
) -> list[str]:
    """문서 113 주도주 계산(백분위 평균 → 섹터 중앙값 상위 3 → 상위 10)을 기간 ``looks`` 로.

    순수 함수."""
    rets: dict[int, dict[str, float]] = {}
    for k in looks:
        rets[k] = {}
        if t - k < 0:
            return []
        for s in pool:
            a, b = close[s][t], close[s][t - k]
            if a is not None and b is not None and b > 0:
                rets[k][s] = a / b - 1
    common = set.intersection(*(set(v) for v in rets.values()))
    if len(common) < 50:
        return []
    score: dict[str, float] = defaultdict(float)
    for k in looks:
        ordered = sorted(common, key=lambda s: (rets[k][s], s))
        n = len(ordered)
        for i, s in enumerate(ordered):
            score[s] += i / (n - 1) / len(looks)
    by_sector: dict[str, list[float]] = defaultdict(list)
    for s in common:
        if sector.get(s, UNKNOWN) != UNKNOWN:
            by_sector[sector[s]].append(score[s])
    top = sorted(by_sector, key=lambda x: (statistics.median(by_sector[x]), x), reverse=True)[
        :TOP_SECTORS
    ]
    picked = sorted(
        (s for s in common if sector.get(s) in top), key=lambda s: (score[s], s), reverse=True
    )
    return picked[:TOP_STOCKS]


def first_entries(lists: Sequence[Sequence[str]], gap: int = GAP) -> list[tuple[int, str]]:
    """(t, 종목) — t 일 목록에 있고 직전 ``gap`` 일 목록에는 없던 것 (순수)."""
    out = []
    for t, today in enumerate(lists):
        if t < gap:
            continue
        seen = set().union(*lists[t - gap : t])
        out += [(t, s) for s in today if s not in seen]
    return out


def fwd_return(xs: Sequence[float | None], a: int, b: int) -> float | None:
    """a 일 종가에 사서 b 일 종가(그 전에 자료가 끊기면 마지막 종가)에 판 수익 (순수)."""
    if a >= len(xs) or xs[a] is None:
        return None
    last = xs[a]
    for i in range(a + 1, min(b, len(xs) - 1) + 1):
        if xs[i] is not None:
            last = xs[i]
    assert last is not None
    return last / xs[a] - 1  # type: ignore[operator]


def forward_fill(xs: Sequence[float | None]) -> list[float | None]:
    out: list[float | None] = []
    last: float | None = None
    for x in xs:
        if x is not None:
            last = x
        out.append(last)
    return out


def events_table(
    days: Sequence[str],
    close: Closes,
    lists: Sequence[Sequence[str]],
    pools: Sequence[Sequence[str]],
    hold: int,
    ff: Mapping[str, Sequence[float | None]] | None = None,
) -> list[dict[str, Any]]:
    """사건마다 t+1 종가 → t+1+hold 종가(끊기면 마지막 종가) 초과 수익. ``ff`` 는 앞채움 종가."""
    ff = ff if ff is not None else {k: forward_fill(v) for k, v in close.items()}
    cache: dict[int, float | None] = {}

    def ret(sym: str, a: int, b: int) -> float | None:
        x0 = close[sym][a]
        x1 = ff[sym][min(b, len(days) - 1)]
        return None if x0 is None or x1 is None or x0 <= 0 else x1 / x0 - 1

    out = []
    for t, s in first_entries(lists):
        if not (FIRST <= days[t] <= LAST) or t + 1 + hold >= len(days):
            continue
        r = ret(s, t + 1, t + 1 + hold)
        if r is None:
            continue
        if t not in cache:
            u = [x for p in pools[t] if (x := ret(p, t + 1, t + 1 + hold)) is not None]
            cache[t] = statistics.fmean(u) if u else None
        base = cache[t]
        if base is None:
            continue
        out.append({"t": t, "day": days[t], "sym": s, "excess": r - base - COST})
    return out


def monthly(events: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    by: dict[str, list[float]] = defaultdict(list)
    for e in events:
        by[str(e["day"])[:7]].append(float(e["excess"]))
    return {m: statistics.fmean(v) for m, v in sorted(by.items())}


def summary(events: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    m = monthly(events)
    xs = list(m.values())
    if len(xs) < 3:
        return {"events": len(events), "months": len(xs)}
    se = nw_se(xs, 3)
    years: dict[str, list[float]] = defaultdict(list)
    for e in events:
        years[str(e["day"])[:4]].append(float(e["excess"]))
    return {
        "events": len(events),
        "months": len(xs),
        "mean_monthly_excess": round(statistics.fmean(xs), 4),
        "nw_se": round(se, 4),
        "lower_2se": round(statistics.fmean(xs) - Z * se, 4),
        "win_rate": round(sum(float(e["excess"]) > 0 for e in events) / len(events), 3),
        "median_event": round(statistics.median(float(e["excess"]) for e in events), 4),
        "by_year": {y: round(statistics.fmean(v), 4) for y, v in sorted(years.items())},
    }


def compare(e_ev: Sequence[Mapping[str, Any]], b_ev: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    me, mb = monthly(e_ev), monthly(b_ev)
    months = sorted(set(me) & set(mb))
    d = [me[m] - mb[m] for m in months]
    days = sorted(str(e["day"]) for e in [*e_ev, *b_ev])
    split = days[len(days) // 2][:7]
    h1 = [x for m, x in zip(months, d, strict=True) if m < split]
    h2 = [x for m, x in zip(months, d, strict=True) if m >= split]
    se = nw_se(d, 3)
    mean = statistics.fmean(d)
    return {
        "months": len(d),
        "mean_diff": round(mean, 4),
        "t": round(mean / se, 2) if se > 0 else None,
        "halves": [round(statistics.fmean(h1), 4), round(statistics.fmean(h2), 4), split],
    }


def lead_days(
    e_ev: Sequence[Mapping[str, Any]], b_ev: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """B 사건마다 같은 종목의 앞선 126 일 안 E 사건 → 며칠 먼저 잡았나."""
    by: dict[str, list[int]] = defaultdict(list)
    for e in e_ev:
        by[str(e["sym"])].append(int(e["t"]))
    leads = []
    for b in b_ev:
        prior = [t for t in by.get(str(b["sym"]), []) if 0 < int(b["t"]) - t <= 126]
        if prior:
            leads.append(int(b["t"]) - max(prior))
    return {
        "b_events": len(b_ev),
        "caught_earlier_by_e": len(leads),
        "median_lead_days": statistics.median(leads) if leads else None,
    }


def scan(
    days: Sequence[str],
    close: Closes,
    sector: Mapping[str, str],
    pool_of: Callable[[int], Sequence[str]],
) -> dict[str, Any]:
    lists_b: list[list[str]] = []
    lists_e: list[list[str]] = []
    pools: list[list[str]] = []
    for t in range(len(days)):
        pool = list(pool_of(t))
        pools.append(pool)
        lists_b.append(leaders_generic(close, sector, pool, t, BASE))
        lists_e.append(leaders_generic(close, sector, pool, t, EARLY))
    res: dict[str, Any] = {}
    ff = {k: forward_fill(v) for k, v in close.items()}
    for hold in (HOLD, 21, 126):
        b = events_table(days, close, lists_b, pools, hold, ff)
        e = events_table(days, close, lists_e, pools, hold, ff)
        res[f"hold_{hold}"] = {
            "B": summary(b),
            "E": summary(e),
            "E_minus_B": compare(e, b),
            **({"lead": lead_days(e, b)} if hold == HOLD else {}),
        }
    return res


def run() -> dict[str, Any]:
    target = OUT / "result.json"
    if target.exists():
        raise SystemExit(f"이미 계산했다: {target}")
    if data_hash()[0] != EXPECTED_SHA:
        raise SystemExit("자료 해시 불일치 (141)")
    panel, _ = build_panel()
    kr = scan(panel.days, panel.close, panel.sector, lambda t: members(panel, t))
    main_ = kr[f"hold_{HOLD}"]
    e1 = main_["E_minus_B"]
    e1_pass = (
        e1["mean_diff"] > 0 and (e1["t"] or 0) >= Z and e1["halves"][0] > 0 and e1["halves"][1] > 0
    )
    e2_pass = main_["E"].get("lower_2se", -1) > 0
    res: dict[str, Any] = {"kr": kr, "E1": e1_pass, "E2": e2_pass}
    res["verdict"] = "EARLY_SUPPORT" if e1_pass and e2_pass else "REJECT"
    from scripts.run_lt_leaders import build_panel as us_panel

    us = us_panel()
    syms = list(us.close)
    res["us_report"] = scan(us.days, us.close, us.sector, lambda t: syms)
    res["computed"] = datetime.now(UTC).date().isoformat()
    OUT.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    return res


def main(argv: list[str]) -> int:
    if argv != ["run"]:
        raise SystemExit("usage: python -m scripts.run_early_leader_191 run")
    sys.stdout.write(json.dumps(run(), ensure_ascii=False, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            _reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main(sys.argv[1:]))
