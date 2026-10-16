"""문서 240 — 실적 발표 갭 상승 따라가기 1 회. 주문 · 실거래와 무관.

사용 예::

    python -m scripts.run_pead_240 fetch   # SEC submissions → 8-K 2.02 접수 시각
    python -m scripts.run_pead_240 run     # reports/pead_240/result.json · 화면
"""

from __future__ import annotations

import csv
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any

import httpx

REPO = Path(__file__).resolve().parent.parent
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

from auto_trading.config.secrets import read_env_file  # noqa: E402
from auto_trading.research.pead_240 import (  # noqa: E402
    CONTROL_GAP_DAYS,
    Event,
    bootstrap_mean,
    dedupe,
    is_signal,
    outcome,
    reaction_index,
)

BARS = REPO / "data" / "lt_paper" / "bars"
SPY = REPO / "data" / "research" / "tradfi" / "SPY_adj.csv"
RAW = REPO / "data" / "research" / "pead_240"
OUT = REPO / "reports" / "pead_240"
FROM, TO, SPLIT = "2008-01-01", "2026-08-31", "2017-01-01"


def _hdr() -> dict[str, str]:
    email = read_env_file(REPO / ".env").get("SEC_CONTACT_EMAIL", "").strip()
    if not email:
        raise SystemExit("SEC_CONTACT_EMAIL 없음")
    return {"User-Agent": f"AutoTrading research {email}", "Accept-Encoding": "gzip, deflate"}


def _get(c: httpx.Client, url: str) -> Any:
    for attempt in range(4):
        time.sleep(0.15)
        r = c.get(url)
        if r.status_code == 200:
            return r.json()
        time.sleep(2 * (attempt + 1))
    return None


def _earnings(sub: dict[str, Any]) -> list[str]:
    out = []
    for f, it, acc in zip(sub["form"], sub["items"], sub["acceptanceDateTime"], strict=False):
        if f in ("8-K", "8-K/A") and "2.02" in (it or "") and f == "8-K":
            out.append(acc)
    return out


def fetch() -> int:
    RAW.mkdir(parents=True, exist_ok=True)
    tickers = sorted(p.stem for p in BARS.glob("*.csv"))
    path = RAW / "earnings.json"
    have: dict[str, list[str]] = (
        json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    )
    with httpx.Client(timeout=30.0, headers=_hdr()) as c:
        m = _get(c, "https://www.sec.gov/files/company_tickers.json")
        cik = {v["ticker"].upper(): int(v["cik_str"]) for v in m.values()}
        missing = []
        for k, t in enumerate(tickers):
            if t in have:
                continue
            key = t.replace(".", "-").upper()
            if key not in cik:
                missing.append(t)
                continue
            sub = _get(c, f"https://data.sec.gov/submissions/CIK{cik[key]:010d}.json")
            if not sub:
                missing.append(t)
                continue
            acc = _earnings(sub["filings"]["recent"])
            for extra in sub["filings"].get("files", []):
                old = _get(c, f"https://data.sec.gov/submissions/{extra['name']}")
                if old:
                    acc += _earnings(old)
            have[t] = sorted(set(acc))
            if k % 25 == 0:
                path.write_text(json.dumps(have), encoding="utf-8")
                print(f"{k}/{len(tickers)} {t}: 2.02 {len(have[t])}", flush=True)
        path.write_text(json.dumps(have), encoding="utf-8")
    (RAW / "missing.json").write_text(json.dumps(missing), encoding="utf-8")
    print(f"종목 {len(have)} · 없음 {len(missing)} {missing[:20]}")
    return 0


def _bars(p: Path) -> tuple[list[str], list[float], list[float], list[float]]:
    with p.open(encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if r.get("close")]
    return (
        [r["date"][:10] for r in rows],
        [float(r["open"]) for r in rows],
        [float(r["close"]) for r in rows],
        [float(r["volume"]) for r in rows],
    )


def _summ(evs: list[Event]) -> dict[str, Any]:
    m, lo, hi = bootstrap_mean(evs)
    x = [e.excess for e in evs]
    return {
        "n": len(evs),
        "mean": m,
        "ci95": [lo, hi],
        "median": statistics.median(x) if x else None,
        "win": sum(v > 0 for v in x) / len(x) if x else None,
    }


def run() -> int:
    earn = json.loads((RAW / "earnings.json").read_text(encoding="utf-8"))
    with SPY.open(encoding="utf-8") as f:
        spy = {r["date"][:10]: (float(r["open"]), float(r["close"])) for r in csv.DictReader(f)}
    sig: dict[str, list[Event]] = {
        "up20": [],
        "down20": [],
        "control20": [],
        "up60": [],
        "control60": [],
    }
    per_year: dict[str, int] = {}
    n_events = 0
    for p in sorted(BARS.glob("*.csv")):
        acc = earn.get(p.stem)
        if not acc:
            continue
        days, opn, close, vol = _bars(p)
        r_idx = dedupe([i for a in acc if (i := reaction_index(days, a)) is not None])
        r_idx = [i for i in r_idx if FROM <= days[i] <= TO]
        n_events += len(r_idx)
        near = {j for i in r_idx for j in range(i - CONTROL_GAP_DAYS, i + CONTROL_GAP_DAYS + 1)}
        for i in r_idx:
            s = is_signal(close, vol, i)
            if s == 0:
                continue
            for hold, key in ((20, "20"), (60, "60")):
                ev = outcome(days, opn, close, i, spy, hold)
                if ev is None:
                    continue
                if s > 0:
                    sig[f"up{key}"].append(ev)
                    if hold == 20:
                        per_year[days[i][:4]] = per_year.get(days[i][:4], 0) + 1
                elif hold == 20:
                    sig["down20"].append(ev)
        for i in range(21, len(days)):
            if i in near or not (FROM <= days[i] <= TO) or is_signal(close, vol, i) != 1:
                continue
            for hold, key in ((20, "control20"), (60, "control60")):
                ev = outcome(days, opn, close, i, spy, hold)
                if ev is not None:
                    sig[key].append(ev)
    up = sig["up20"]
    halves = [
        _summ([e for e in up if e.entry_day < SPLIT]),
        _summ([e for e in up if e.entry_day >= SPLIT]),
    ]
    res: dict[str, Any] = {k: _summ(v) for k, v in sig.items()}
    res["events_total"] = n_events
    res["halves_up20"] = halves
    res["by_gap_up20"] = {
        "5-10%": _summ([e for e in up if e.gap < 0.10]),
        "10%+": _summ([e for e in up if e.gap >= 0.10]),
    }
    res["per_year_up"] = dict(sorted(per_year.items()))
    u, c = res["up20"], res["control20"]
    if (u["mean"] or 0) <= 0:
        verdict = "불리"
    elif (
        u["ci95"][0] > 0
        and all((h["mean"] or 0) > 0 for h in halves)
        and u["mean"] > (c["mean"] or 0)
    ):
        verdict = "지지"
    else:
        verdict = "불명"
    res["verdict"] = verdict
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
