"""문서 224 — LT 그 시점 S&P 500 명단 자료 감사 (수익 안 봄).

사용: ``python -m scripts.audit_lt_pit_224`` → ``reports/lt_pit_224/audit.json``

현재 명단(113 · 115 의 503 종목, 2026-10-03)에 위키 판 시각까지의 변경을 앞으로 적용한 뒤,
208 의 변경 기록을 거꾸로 되돌려 날마다 명단을 만든다. 관문 G1 ~ G4 는 문서 224 §4.
"""

from __future__ import annotations

import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
STOCKS = REPO / "data" / "research" / "stocks"
SP400 = REPO / "data" / "research" / "stocks_sp400"
EV = REPO / "data" / "research" / "sp500_events_208"
SPY = REPO / "data" / "research" / "tradfi" / "SPY_adj.csv"
OUT = REPO / "reports" / "lt_pit_224"
FROM, TO = "2008-06-01", "2026-08-31"
LIST_DATE = "2026-10-03"
"""113 · 115 명단을 받은 날 (sectors.json fetched)."""
G1_LO, G1_HI, G1_SHARE = 495, 510, 0.99
G2_ALL, G2_YEAR = 0.97, 0.95
G3_SHARE, G3_LEAD = 0.90, 5
G4_SHARE = 0.95


def _closes(sym: str) -> dict[str, float]:
    for folder in (STOCKS, SP400, EV / "prices"):
        p = folder / f"{sym}.csv"
        if p.exists():
            with p.open(encoding="utf-8") as fh:
                return {r["date"]: float(r["close"]) for r in csv.DictReader(fh) if r.get("close")}
    return {}


def _source(sym: str) -> str:
    for name, folder in (("stocks", STOCKS), ("sp400", SP400), ("tiingo", EV / "prices")):
        if (folder / f"{sym}.csv").exists():
            return name
    return "empty" if (EV / "prices" / f"{sym}.empty").exists() else "none"


def build() -> dict[str, Any]:
    events = json.loads((EV / "events.json").read_text(encoding="utf-8"))
    status = json.loads((STOCKS / "fetch_status.json").read_text(encoding="utf-8"))
    members = {s for s, v in status.items() if v.get("ok")}
    # 명단 받은 날 뒤의 변경은 앞으로 적용 (위키 판 시각까지)
    for e in sorted(events, key=lambda r: r["effective"]):
        if e["effective"] > LIST_DATE:
            if e["removed"]:
                members.discard(e["removed"])
            if e["added"]:
                members.add(e["added"])
    with SPY.open(encoding="utf-8") as fh:
        days = [r["date"] for r in csv.DictReader(fh) if FROM <= r["date"] <= TO]
    changes = sorted((e for e in events if e["effective"] > FROM), key=lambda r: r["effective"])
    # 날마다 명단: 가장 늦은 날부터 거꾸로
    daily: dict[str, frozenset[str]] = {}
    cur = set(members)
    ci = len(changes) - 1
    for d in reversed(days):
        while ci >= 0 and changes[ci]["effective"] > d:
            e = changes[ci]
            if e["added"]:
                cur.discard(e["added"])
            if e["removed"]:
                cur.add(e["removed"])
            ci -= 1
        daily[d] = frozenset(cur)
    sectors = {
        **json.loads((SP400 / "sectors.json").read_text(encoding="utf-8"))["sectors"],
        **json.loads((STOCKS / "sectors.json").read_text(encoding="utf-8"))["sectors"],
    }
    universe = sorted(set().union(*daily.values()))
    closes = {s: _closes(s) for s in universe}

    sizes = [len(daily[d]) for d in days]
    g1_share = sum(G1_LO <= n <= G1_HI for n in sizes) / len(sizes)

    have = total = 0
    by_year: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    sec_have = 0
    missing_days: Counter[str] = Counter()
    for d in days:
        for s in daily[d]:
            ok = d in closes[s]
            total += 1
            have += ok
            by_year[d[:4]][0] += ok
            by_year[d[:4]][1] += 1
            sec_have += s in sectors
            if not ok:
                missing_days[s] += 1
    year_cov = {y: round(a / b, 4) for y, (a, b) in sorted(by_year.items())}

    removed = [e for e in changes if e["removed"] and e["effective"] <= TO]
    g3_rows = []
    for e in removed:
        s, eff = e["removed"], e["effective"]
        before = [d for d in days if d < eff]
        need = before[-G3_LEAD] if len(before) >= G3_LEAD else None
        last = max(closes[s]) if closes[s] else None
        g3_rows.append(
            {
                "symbol": s,
                "name": e["removed_name"],
                "effective": eff,
                "reason": e["reason"][:120],
                "source": _source(s),
                "last_price": last,
                "ok": bool(last and need and last >= need),
            }
        )
    g3_share = sum(r["ok"] for r in g3_rows) / len(g3_rows)

    # 티커 재사용 의심: 빠진 티커가 나중에 다시 들어오거나 지금 명단에 있음
    reuse = []
    later_added = {e["added"]: e for e in changes if e["added"]}
    for e in removed:
        s = e["removed"]
        back = later_added.get(s)
        if (back and back["effective"] > e["effective"]) or s in members:
            reuse.append(
                {
                    "symbol": s,
                    "removed": e["effective"],
                    "removed_name": e["removed_name"],
                    "readded": back["effective"] if back else None,
                    "readded_name": back["added_name"] if back else "(지금 명단)",
                }
            )

    gates = {
        "G1": {
            "value": round(g1_share, 4),
            "pass": g1_share >= G1_SHARE,
            "sizes_min_max": [min(sizes), max(sizes)],
        },
        "G2": {
            "value": round(have / total, 4),
            "pass": have / total >= G2_ALL and min(year_cov.values()) >= G2_YEAR,
            "worst_year": min(year_cov.items(), key=lambda x: x[1]),
        },
        "G3": {
            "value": round(g3_share, 4),
            "pass": g3_share >= G3_SHARE,
            "removed": len(g3_rows),
            "ok": sum(r["ok"] for r in g3_rows),
        },
        "G4": {"value": round(sec_have / total, 4), "pass": sec_have / total >= G4_SHARE},
    }
    verdict = "DATA_OK" if all(g["pass"] for g in gates.values()) else "DATA_INSUFFICIENT"
    size_by_year = {y: [min(v), max(v)] for y, v in _group_sizes(days, sizes).items()}
    return {
        "verdict": verdict,
        "gates": gates,
        "period": [days[0], days[-1]],
        "trading_days": len(days),
        "universe_symbols": len(universe),
        "member_days": total,
        "price_coverage_by_year": year_cov,
        "member_count_by_year": size_by_year,
        "missing_price_top": missing_days.most_common(40),
        "removed_without_price": [r for r in g3_rows if not r["ok"]],
        "removed_source_counts": Counter(r["source"] for r in g3_rows),
        "ticker_reuse_suspects": reuse,
    }


def _group_sizes(days: list[str], sizes: list[int]) -> dict[str, list[int]]:
    out: dict[str, list[int]] = defaultdict(list)
    for d, n in zip(days, sizes, strict=True):
        out[d[:4]].append(n)
    return dict(out)


def main() -> int:
    res = build()
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "audit.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    brief = {
        k: res[k]
        for k in (
            "verdict",
            "gates",
            "member_count_by_year",
            "price_coverage_by_year",
            "removed_source_counts",
        )
    }
    sys.stdout.write(json.dumps(brief, ensure_ascii=False, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
