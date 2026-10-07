"""IDEAS 22 후보 A — 국장 일봉 IHS · DBOT **사건 수만** 센다 (수익 미열람).

사용 예::

    python -m scripts.count_kr_patterns --out reports/indicator_a

* 사용자 확정(2026-10-07): 국장만 · 20 일 평균 거래대금 ≥ 10 억 · 판정 손절 없음 ·
  계좌 규칙 분리 · 2010 ~ 2026-08 1 회. 미국은 국장 판정 뒤 재현 후보로만.
* 사건 뒤 가격은 읽지 않는다. 예외는 자료 품질 표시(보유 구간의 제한폭 위반 · 빈 날)와
  보유가 자료 끝을 넘는지뿐이며, 둘 다 수익이 아니다.
* G-KR1 (수정 주가 정제, 초안): 이웃 거래일 수정 종가 수익이 제한폭(2015-06-15 전 15%, 뒤 30%)
  + 1%p 를 넘는 날 = 위반. 위반이 든 창에서는 탐지하지 않는다.
* 창 조건: 38 거래일 연속 자료 · 창 안 최저 수정 종가 ≥ 200 원(정수 반올림이 1.5% 허용 폭을
  흔들지 않게) · 창 끝 20 일 평균 거래대금 ≥ 10 억 · 창 안 거래대금 > 0.
* 완성 시점은 탐지기가 정한다(마지막 극점 = 창 끝 3 칸 앞, LMW). 같은 날 둘 다면 IHS.
* 한 종목 한 번에 하나: 사건 뒤 21 거래일(다음 날 진입 + 20 일 보유) 동안 그 종목은 새로
  세지 않는다 — 체결 여부와 무관하게 정해 가격에 기대지 않는다.
* 거래량 추세(LMW §III.D): 창 앞 절반 · 뒤 절반 평균 거래대금 t1 · t2. t2 > 1.2·t1 이면 '증가',
  t1 > 1.2·t2 이면 '감소', 아니면 '없음'. 회전율 대신 거래대금(분할에 영향 없음).
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from auto_trading.research.patterns_lmw import detect  # noqa: E402

DIRS = {
    "KOSPI": REPO_ROOT / "data/research/kr_stocks/prices",
    "KOSDAQ": REPO_ROOT / "data/research/kq_stocks/prices",
}
CAL = REPO_ROOT / "data/research/kr_stocks/kospi.csv"
FROM, TO = "20100104", "20260831"
WINDOW, LAG = 38, 3
VOL_RATIO = 1.2
HOLD = 20
BUSY = HOLD + 1
MIN_VALUE = 1e9
ADV_DAYS = 20
PRICE_FLOOR = 200.0
LIMIT_CHANGE = "20150615"
TOLERANCE = 0.01
BLOCK = 20


def limit_for(day: str) -> float:
    return (0.15 if day < LIMIT_CHANGE else 0.30) + TOLERANCE


def violations(cal: Sequence[str], px: Sequence[float | None]) -> list[bool]:
    """칸 i 의 종가가 칸 i−1 대비 제한폭을 넘었으면 True (둘 다 있을 때만, 순수)."""
    out = [False] * len(cal)
    for i in range(1, len(cal)):
        a, b = px[i - 1], px[i]
        if a and b and abs(b / a - 1) > limit_for(cal[i]):
            out[i] = True
    return out


def volume_trend(values: Sequence[float]) -> str:
    """LMW 거래량 추세 — 앞 절반 · 뒤 절반 평균 비교 (순수)."""
    half = len(values) // 2
    t1 = sum(values[:half]) / half
    t2 = sum(values[half:]) / (len(values) - half)
    if t2 > VOL_RATIO * t1:
        return "up"
    if t1 > VOL_RATIO * t2:
        return "down"
    return "none"


def scan_stock(
    cal: Sequence[str],
    px: Mapping[str, float],
    value: Mapping[str, float],
    first: str = FROM,
    last: str = TO,
) -> tuple[list[dict[str, Any]], int]:
    """한 종목의 (사건 목록, 조건을 만족한 창 수). 칸 t 의 판단은 칸 t 까지의 자료만 쓴다."""
    p = [px.get(d) for d in cal]
    v = [value.get(d) for d in cal]
    bad = violations(cal, p)
    events: list[dict[str, Any]] = []
    free_from = 0
    eligible = 0
    for t in range(WINDOW - 1, len(cal)):
        if cal[t] < first or cal[t] > last or t < free_from:
            continue
        lo = t - WINDOW + 1
        win = p[lo : t + 1]
        vol = v[lo : t + 1]
        if any(x is None for x in win) or any(not y or y <= 0 for y in vol):
            continue
        closes = [float(x) for x in win if x is not None]
        vals = [float(y) for y in vol if y is not None]
        if min(closes) < PRICE_FLOOR or any(bad[lo + 1 : t + 1]):
            continue
        if sum(vals[-ADV_DAYS:]) / ADV_DAYS < MIN_VALUE:
            continue
        eligible += 1
        found = detect(closes, LAG)
        if not found:
            continue
        found.sort(key=lambda q: q.kind != "IHS")
        q = found[0]
        events.append(
            {
                "detect": cal[t],
                "t": t,
                "kind": q.kind,
                "points": [cal[lo + i] for i in q.points],
                "also": sorted({x.kind for x in found} - {q.kind}),
                "volume": volume_trend(vals),
                "adv20": round(sum(vals[-ADV_DAYS:]) / ADV_DAYS),
            }
        )
        free_from = t + BUSY
    for e in events:  # 자료 품질 표시 — 수익이 아니다
        t = e.pop("t")
        end = t + 1 + HOLD
        e["hold_complete"] = end < len(cal) and cal[end] <= TO
        e["hold_violation"] = any(bad[t + 1 : min(end, len(cal) - 1) + 1])
        e["hold_missing_days"] = sum(
            1 for i in range(t + 1, min(end, len(cal) - 1) + 1) if p[i] is None
        )
    return events, eligible


def _calendar() -> list[str]:
    with CAL.open(encoding="utf-8") as fh:
        return [r["date"] for r in csv.DictReader(fh) if "20091101" <= r["date"] <= TO]


def _load(path: Path) -> tuple[dict[str, float], dict[str, float]]:
    px: dict[str, float] = {}
    val: dict[str, float] = {}
    with path.open(encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            d = r["date"]
            if "20091101" <= d <= TO and r["close"]:
                px[d] = float(r["close"])
                val[d] = float(r["value"] or 0)
    return px, val


def _job(args: tuple[str, str, list[str]]) -> tuple[str, str, list[dict[str, Any]], int, int]:
    market, path, cal = args
    px, val = _load(Path(path))
    n_bad = sum(violations(cal, [px.get(d) for d in cal]))
    events, eligible = scan_stock(cal, px, val)
    return market, Path(path).stem, events, eligible, n_bad


def summarize(events: Sequence[dict[str, Any]], cal: Sequence[str]) -> dict[str, Any]:
    pos = {d: i for i, d in enumerate(cal)}
    usable = [e for e in events if e["hold_complete"]]
    clean = [e for e in usable if not e["hold_violation"]]
    cells = Counter(f"{e['kind']}·{e['volume']}" for e in clean)
    blocks = Counter(pos[e["detect"]] // BLOCK for e in clean)
    top5 = sum(n for _, n in blocks.most_common(5))
    return {
        "events_all": len(events),
        "hold_incomplete": len(events) - len(usable),
        "hold_violation_excluded_candidates": len(usable) - len(clean),
        "events_clean": len(clean),
        "cells": dict(sorted(cells.items())),
        "by_market": dict(sorted(Counter(e["market"] for e in clean).items())),
        "by_year": dict(sorted(Counter(e["detect"][:4] for e in clean).items())),
        "both_kinds_same_window": sum(1 for e in clean if e["also"]),
        "blocks_with_events": len(blocks),
        "blocks_total": len(range(0, len(cal), BLOCK)),
        "events_per_block_mean": round(len(clean) / max(1, len(blocks)), 1),
        "events_per_block_max": max(blocks.values(), default=0),
        "top5_block_share": round(top5 / max(1, len(clean)), 3),
        "stocks_with_events": len({e["code"] for e in clean}),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=11)
    args = parser.parse_args(argv)
    cal = _calendar()
    jobs = [(m, str(f), cal) for m, d in DIRS.items() for f in sorted(d.glob("*.csv"))]
    events: list[dict[str, Any]] = []
    seen: set[str] = set()
    violations_total = 0
    eligible_windows = 0
    with ProcessPoolExecutor(args.workers) as ex:
        for market, code, evs, eligible, n_bad in ex.map(_job, jobs, chunksize=8):
            violations_total += n_bad
            if code in seen:  # 시장을 옮긴 종목 — 먼저 읽은(유가증권) 쪽만
                continue
            seen.add(code)
            eligible_windows += eligible
            for e in evs:
                e["code"], e["market"] = code, market
            events.extend(evs)
    events.sort(key=lambda e: (e["detect"], e["code"]))
    args.out.mkdir(parents=True, exist_ok=True)
    with (args.out / "events.jsonl").open("w", encoding="utf-8") as fh:
        for e in events:
            fh.write(json.dumps(e, ensure_ascii=False) + "\n")
    summary = {
        "rules": {
            "window": WINDOW,
            "lag": LAG,
            "busy_days": BUSY,
            "min_value_krw": MIN_VALUE,
            "price_floor": PRICE_FLOOR,
            "period": [FROM, TO],
        },
        "stock_files": len(jobs),
        "duplicate_codes_skipped": len(jobs) - len(seen),
        "g_kr1_violation_days": violations_total,
        "eligible_windows": eligible_windows,
        **summarize(events, cal),
    }
    stock_years = eligible_windows / 252
    summary["per_stock_year"] = {
        k: round(sum(1 for e in events if e["kind"] == k) / stock_years, 3) for k in ("IHS", "DBOT")
    }
    summary["lmw_reference_per_stock_year"] = {  # LMW 표 I, NYSE/AMEX 50 종목 × 7 × 5 년
        "IHS": round(1654 / 1750, 3),
        "DBOT": round(2075 / 1750, 3),
    }
    (args.out / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    sys.stdout.write(json.dumps(summary, ensure_ascii=False, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            _reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
