"""문서 34 수익 가능성 시험 — §6 합성 구간 찾기 점검, §5 시장 사건 수·검정력 (수익률 미열람).

``synthetic``: 개발 seed 합성 경로에서 진짜 5파(C) 끝 발동의 1순위 해석.
``power``: BTCUSDT 2020-01~2026-09 사건·대조 수와 MDE. **사건 뒤 수익률은 계산하지 않는다.**
DB 는 읽기 전용이며 WA·D22·전략 경로를 조회하지 않는다.
"""

from __future__ import annotations

import json
import math
import random
import sqlite3
import sys
from collections import Counter
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from pathlib import Path
from statistics import pstdev

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from scripts.audit_ew3c_hourly import CELLS, DEGREES, hour_bars  # noqa: E402

from auto_trading.core.enums import Timeframe  # noqa: E402
from auto_trading.core.types import Candle  # noqa: E402
from auto_trading.research.elliott.minute_path import replay_minute_groups_ladder  # noqa: E402
from auto_trading.research.elliott.pivots import EW0_THRESHOLD_PCT  # noqa: E402
from auto_trading.research.elliott.scanner import (  # noqa: E402
    TriggerResult,
    causal_triggers,
    evaluate_triggers,
)
from auto_trading.research.elliott.synthetic import build_path  # noqa: E402
from scripts import audit_ew0_minute_path as market  # noqa: E402

DEV_SEED = 20260924
FROM_MS = 1_577_836_800_000  # 2020-01-01
HORIZON = 30
MDE_GATE = 0.02


# ---------------------------------------------------------------- §6 합성


def synthetic() -> dict[str, object]:
    rng = random.Random(DEV_SEED)
    out: Counter[str] = Counter()
    for _degree, (scale, threshold) in DEGREES.items():
        for pattern in ("IMPULSE", "ZIGZAG"):
            for noise, count in CELLS:
                for _ in range(count):
                    path = build_path(rng, pattern, scale, threshold, noise)
                    if noise == 0.0:
                        continue
                    four = path.four_hour
                    hours = hour_bars(path.minutes)
                    groups = (path.minutes[240 * i : 240 * (i + 1)] for i in range(len(four)))
                    (replay,) = replay_minute_groups_ladder(
                        four, groups, four[-1].close_ms, (EW0_THRESHOLD_PCT,)
                    )
                    triggers = causal_triggers(replay, hours, four)
                    truth = [
                        (kind, Timeframe.H1.open_ms_of(path.minutes[t].open_ms))
                        for t, kind in zip(path.vertex_minutes, path.vertex_kinds, strict=True)
                    ]
                    hit = next(
                        (
                            trig
                            for trig in triggers
                            if (trig.segment[-1].kind, trig.segment[-1].hour_open_ms) == truth[-1]
                        ),
                        None,
                    )
                    out[f"{pattern}|paths"] += 1
                    if hit is None:
                        out[f"{pattern}|no_trigger_at_true_end"] += 1
                        continue
                    (res,) = evaluate_triggers([hit], hours)
                    if res.is_event:
                        out[f"{pattern}|top1_impulse"] += 1
                        where = {(p.kind, p.hour_open_ms): k for k, p in enumerate(hit.segment)}
                        bounds = tuple(where.get(v, -1) for v in truth)
                        if bounds in res.top_bounds:
                            out[f"{pattern}|top1_true_bounds"] += 1
                    elif not res.top_patterns:
                        out[f"{pattern}|no_candidate"] += 1
                    else:
                        out[f"{pattern}|top1_{'_'.join(res.top_patterns)}"] += 1
    imp = out["IMPULSE|top1_impulse"] / max(1, out["IMPULSE|paths"])
    zz = out["ZIGZAG|top1_impulse"] / max(1, out["ZIGZAG|paths"])
    return {
        "counts": dict(sorted(out.items())),
        "impulse_top1_impulse": round(imp, 4),
        "zigzag_top1_impulse": round(zz, 4),
        "check_pass": imp >= 0.5 and zz <= 0.3,
    }


# ---------------------------------------------------------------- §5 시장 (수익률 미열람)


def _rows(conn: sqlite3.Connection, timeframe: str, to_ms: int) -> Iterator[tuple[object, ...]]:
    return iter(conn.execute(market._QUERY, ("binance_usdm", "BTCUSDT", timeframe, FROM_MS, to_ms)))


def _minute_groups(
    conn: sqlite3.Connection, four: Sequence[Candle], to_ms: int
) -> Iterator[list[Candle]]:
    rows = _rows(conn, "1m", to_ms)
    row = next(rows, None)
    for outer in four:
        while row is not None and int(str(row[0])) < outer.open_ms:
            row = next(rows, None)
        group: list[Candle] = []
        while row is not None and int(str(row[0])) < outer.open_ms + Timeframe.H4.ms:
            group.append(market._candle(row))
            row = next(rows, None)
        yield group


def _thin(results: Sequence[TriggerResult], index_of: dict[int, int]) -> list[TriggerResult]:
    kept: list[TriggerResult] = []
    last = -(10**9)
    for r in sorted(results, key=lambda x: x.close_ms):
        k = index_of[r.close_ms]
        if k - last >= HORIZON:
            kept.append(r)
            last = k
    return kept


def power() -> dict[str, object]:
    conn = sqlite3.connect(market.DB_PATH.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        conn.execute("PRAGMA query_only=ON")
        last_open = conn.execute(
            "SELECT MAX(open_ms) FROM candles WHERE exchange='binance_usdm' AND symbol='BTCUSDT' "
            "AND timeframe='4h'"
        ).fetchone()[0]
        to_ms = int(last_open) + Timeframe.H4.ms  # 마지막 4h 봉까지
        four = tuple(market._candle(r) for r in _rows(conn, "4h", to_ms))
        hours = tuple(market._candle(r) for r in _rows(conn, "1h", to_ms))
        (replay,) = replay_minute_groups_ladder(
            four, _minute_groups(conn, four, to_ms), four[-1].close_ms, (EW0_THRESHOLD_PCT,)
        )
    finally:
        conn.close()
    triggers = causal_triggers(replay, hours, four)
    results = evaluate_triggers(triggers, hours)
    index_of = {bar.close_ms: k for k, bar in enumerate(four)}
    usable = [r for r in results if index_of[r.close_ms] + HORIZON < len(four)]
    events = [r for r in usable if r.is_event]
    controls = [r for r in usable if not r.is_event]
    ev, co = _thin(events, index_of), _thin(controls, index_of)
    closes = [float(bar.close) for bar in four]
    fwd = [math.log(closes[k + HORIZON] / closes[k]) for k in range(len(closes) - HORIZON)]
    sd = pstdev(fwd)
    n1, n2 = len(ev), len(co)
    mde = 2.84 * sd * math.sqrt(1 / n1 + 1 / n2) if n1 and n2 else math.inf

    def year(ms: int) -> str:
        return datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y")

    return {
        "period_utc": f"{four[0].open_ms}/{four[-1].close_ms}",
        "four_hour_bars": len(four),
        "blocked_four_hours": len(replay.blocked_four_hours),
        "triggers": len(results),
        "events": len(events),
        "controls": len(controls),
        "top_pattern_mix": dict(
            sorted(Counter("|".join(r.top_patterns) or "NONE" for r in results).items())
        ),
        "events_by_year": dict(sorted(Counter(year(r.close_ms) for r in events).items())),
        "events_by_direction": dict(sorted(Counter(str(r.direction) for r in events).items())),
        "n1_events_independent": n1,
        "n2_controls_independent": n2,
        "sd_forward_30bar_log_return": round(sd, 5),
        "mde": round(mde, 5),
        "gate_mde_le_2pct": mde <= MDE_GATE,
        "note": "문서 34 §5. 사건 뒤 수익률은 계산하지 않았다.",
    }


def main(argv: list[str]) -> int:
    if not argv or argv[0] not in ("synthetic", "power"):
        raise SystemExit("usage: audit_ew_edge_power.py synthetic|power")
    result = synthetic() if argv[0] == "synthetic" else power()
    sys.stdout.write(json.dumps(result, ensure_ascii=True, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
