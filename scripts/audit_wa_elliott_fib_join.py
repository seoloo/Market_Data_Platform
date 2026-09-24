"""문서 35 결합 분석 — WA 진입 × Elliott 종료·피보나치 되돌림 (1회 실행, CROSS_VENUE_PROXY).

시장: data.binance.vision 현물 BTCUSDT 1m (2018-01~2021-12) → 1h·4h 집계 → EW-3d′ 스캐너.
WA: XBTUSD 체결 → flat→flat 에피소드. 손익·보유기간·MFE/MAE 는 쓰지 않는다.
"""

from __future__ import annotations

import json
import random
import sys
import zipfile
from bisect import bisect_left
from collections import Counter, defaultdict
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from scripts.audit_ew_edge_power import _aggregate, _chunks, _groups  # noqa: E402
from scripts.explore_wonyotti_trips import SOURCE, build_trips, load_xbtusd  # noqa: E402

from auto_trading.core.enums import Timeframe  # noqa: E402
from auto_trading.core.types import Candle  # noqa: E402
from auto_trading.research.elliott.exposure import ExposureIndex  # noqa: E402
from auto_trading.research.elliott.minute_path import replay_minute_groups_ladder  # noqa: E402
from auto_trading.research.elliott.pivots import EW0_THRESHOLD_PCT  # noqa: E402
from auto_trading.research.elliott.scanner import (  # noqa: E402
    Trigger,
    TriggerResult,
    causal_triggers,
    evaluate_triggers,
)

SPOT_DIR = REPO_ROOT / "data" / "external" / "binance_vision" / "spot" / "BTCUSDT" / "1m"
SEED = 20260925
CONTROLS = 5
BOOT = 10_000
CASE_FROM = datetime(2018, 3, 1, tzinfo=UTC)
CASE_TO = datetime(2022, 1, 1, tzinfo=UTC)
H1 = Timeframe.H1.ms
H4 = Timeframe.H4.ms
TESTS = {
    # 가설: (사례 방향, 노출 종류)
    "H1_short_after_up_impulse_end": (-1, "ew"),
    "H2_long_after_down_impulse_end": (1, "ew"),
    "H5a_long_in_up_leg_fib_zone": (1, "fib"),
    "H5b_short_in_down_leg_fib_zone": (-1, "fib"),
}


def spot_minutes() -> Iterator[Candle]:
    for path in sorted(SPOT_DIR.glob("BTCUSDT-1m-*.zip")):
        with zipfile.ZipFile(path) as archive:
            (name,) = archive.namelist()
            for line in archive.read(name).decode("ascii").splitlines():
                cells = line.split(",")
                if not cells[0].isdigit():
                    continue
                open_ms = int(cells[0])
                yield Candle(
                    open_ms=open_ms,
                    close_ms=open_ms + Timeframe.M1.ms - 1,
                    open=Decimal(cells[1]),
                    high=Decimal(cells[2]),
                    low=Decimal(cells[3]),
                    close=Decimal(cells[4]),
                    volume=Decimal(cells[5]),
                )


def market_index() -> tuple[ExposureIndex, list[Candle], dict[str, int]]:
    four = _aggregate(spot_minutes(), H4)
    hours = _aggregate(spot_minutes(), H1)
    minutes = spot_minutes()
    triggers: list[Trigger] = []
    results: list[TriggerResult] = []
    blocked = 0
    chunks = _chunks(four)
    for chunk in chunks:
        (replay,) = replay_minute_groups_ladder(
            chunk, _groups(minutes, chunk), chunk[-1].close_ms, (EW0_THRESHOLD_PCT,)
        )
        blocked += len(replay.blocked_four_hours)
        chunk_hours = [h for h in hours if chunk[0].open_ms <= h.open_ms <= chunk[-1].close_ms]
        found = causal_triggers(replay, chunk_hours, chunk)
        triggers.extend(found)
        results.extend(evaluate_triggers(found, chunk_hours))
    meta = {
        "four_hour_bars": len(four),
        "chunks": len(chunks),
        "blocked_four_hours": blocked,
        "triggers": len(triggers),
        "impulse_end_events": sum(r.is_event for r in results),
    }
    return ExposureIndex(triggers, results), hours, meta


def _ms(at: datetime) -> float:
    return at.timestamp() * 1000


def exposed(index: ExposureIndex, hour_closes: list[int], hours: list[Candle], t: float,
            direction: int, kind: str) -> bool | None:  # fmt: skip
    """None = 상태 미정(t 이전 시장 자료 없음)."""
    k = bisect_left(hour_closes, t) - 1
    if k < 0 or t - hour_closes[k] > 2 * H1:
        return None
    t_ms = int(t) if t == int(t) else int(t) + 1  # t 와 같은 ms 의 사건은 이후로 취급
    if kind == "ew":
        return index.elliott_end(t_ms) == -direction
    return index.in_fib_zone(t_ms, hours[k].close, direction)


def _odds(a: int, n: int, c: int, m: int) -> float:
    a2, b2, c2, d2 = a + 0.5, n - a + 0.5, c + 0.5, m - c + 0.5
    return (a2 / b2) / (c2 / d2)


def main() -> int:
    index, hours, meta = market_index()
    hour_closes = [h.close_ms for h in hours]
    fills, funding, _ = load_xbtusd(SOURCE)
    trips = build_trips(fills, funding)
    open_intervals = sorted(
        (_ms(t.opened), _ms(t.closed) if t.closed else float("inf")) for t in trips
    )
    cases = sorted((t for t in trips if CASE_FROM <= t.opened < CASE_TO), key=lambda t: t.opened)

    def flat(at: float) -> bool:
        k = bisect_left(open_intervals, (at, float("inf"))) - 1
        return all(not (s <= at < e) for s, e in open_intervals[max(0, k - 50) : k + 1])

    # 같은 달 flat 1h 경계 시각 목록 (대조 후보)
    month_slots: dict[str, list[int]] = defaultdict(list)
    for h in hours:
        at = h.open_ms
        if flat(at):
            month_slots[datetime.fromtimestamp(at / 1000, UTC).strftime("%Y-%m")].append(at)

    rng = random.Random(SEED)
    rows: list[dict[str, object]] = []  # 사례 1 행 + 대조들
    unknown = Counter()
    for trip in cases:
        t = _ms(trip.opened)
        iso = trip.opened.isocalendar()
        week = f"{iso[0]}-W{iso[1]:02d}"
        slots = month_slots.get(trip.opened.strftime("%Y-%m"), [])
        controls = rng.sample(slots, min(CONTROLS, len(slots)))
        for name, (need, kind) in TESTS.items():
            if trip.side != need:
                continue
            case_state = exposed(index, hour_closes, hours, t, need, kind)
            if case_state is None:
                unknown[name] += 1
                continue
            ctrl = [exposed(index, hour_closes, hours, float(c), need, kind) for c in controls]
            ctrl_known = [x for x in ctrl if x is not None]
            rows.append(
                {
                    "test": name,
                    "week": week,
                    "year": trip.opened.year,
                    "day": trip.opened.date().isoformat(),
                    "case": bool(case_state),
                    "ctrl_exposed": sum(bool(x) for x in ctrl_known),
                    "ctrl_n": len(ctrl_known),
                }
            )

    boot_rng = random.Random(SEED)
    report: dict[str, object] = {}
    pvalues: dict[str, float] = {}
    for name in TESTS:
        mine = [r for r in rows if r["test"] == name]
        by_week: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0, 0])
        for r in mine:
            w = by_week[str(r["week"])]
            w[0] += int(bool(r["case"]))
            w[1] += 1
            w[2] += int(str(r["ctrl_exposed"]))
            w[3] += int(str(r["ctrl_n"]))
        weeks = list(by_week.values())
        a, n = sum(w[0] for w in weeks), sum(w[1] for w in weeks)
        c, m = sum(w[2] for w in weeks), sum(w[3] for w in weeks)
        point = _odds(a, n, c, m)
        boots: list[float] = []
        for _ in range(BOOT):
            pick = [weeks[boot_rng.randrange(len(weeks))] for _ in weeks]
            boots.append(
                _odds(
                    sum(w[0] for w in pick),
                    sum(w[1] for w in pick),
                    sum(w[2] for w in pick),
                    sum(w[3] for w in pick),
                )
            )
        boots.sort()
        p = sum(b <= 1.0 for b in boots) / BOOT
        pvalues[name] = p
        years: dict[int, list[int]] = defaultdict(lambda: [0, 0, 0, 0])
        for r in mine:
            y = years[int(str(r["year"]))]
            y[0] += int(bool(r["case"]))
            y[1] += 1
            y[2] += int(str(r["ctrl_exposed"]))
            y[3] += int(str(r["ctrl_n"]))
        days = {str(r["day"]): bool(r["case"]) for r in mine}
        report[name] = {
            "cases": n,
            "weeks": len(weeks),
            "case_rate": round(a / n, 4) if n else None,
            "control_rate": round(c / m, 4) if m else None,
            "odds_ratio": round(point, 3),
            "ci95": [round(boots[int(0.025 * BOOT)], 3), round(boots[int(0.975 * BOOT) - 1], 3)],
            "p_one_sided": round(p, 4),
            "unknown_cases": unknown[name],
            "by_year_case_vs_control": {
                str(yr): [round(v[0] / v[1], 4), round(v[2] / v[3], 4) if v[3] else None]
                for yr, v in sorted(years.items())
            },
            "entry_day_level_case_rate": round(sum(days.values()) / len(days), 4) if days else None,
        }
    # Holm
    order = sorted(pvalues, key=lambda k: pvalues[k])
    holm: dict[str, bool] = {}
    still = True
    for rank, name in enumerate(order):
        threshold = 0.05 / (len(order) - rank)
        still = still and pvalues[name] <= threshold
        holm[name] = still
    for name in TESTS:
        entry = report[name]
        assert isinstance(entry, dict)
        entry["holm_significant"] = holm[name] and float(str(entry["odds_ratio"])) > 1
    result = {
        "market": "binance_spot:BTCUSDT 1m 2018-01..2021-12 (CROSS_VENUE_PROXY)",
        "market_meta": meta,
        "wa_episodes_in_window": len(cases),
        "wa_side_counts": dict(Counter(str(t.side) for t in cases)),
        "tests": report,
        "note": "문서 35 사전 등록 1회 실행. 연관만, 의도·인과·수익 아님.",
    }
    sys.stdout.write(json.dumps(result, ensure_ascii=True, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
