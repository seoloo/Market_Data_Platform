"""문서 36 — WA 보유 방향 × Elliott 종료·피보나치 되돌림 (CROSS_VENUE_PROXY).

``precheck``: 시장만으로 노출 시각·주 수와 근사 MDE (WA 보유 상태를 읽지 않는다).
``run``: 사전 점검 통과 뒤 한 번 실행 — 월 층화 차이, 주 블록 부트스트랩, Holm.
"""

from __future__ import annotations

import json
import math
import random
import sys
from bisect import bisect_right
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from scripts.explore_wonyotti_trips import SOURCE, build_trips, load_xbtusd  # noqa: E402

from scripts import audit_wa_elliott_fib_join as join  # noqa: E402

SEED = 20260925
BOOT = 10_000
TESTS = {  # 가설: (결과 방향 = 노출 판정 방향, 노출 종류)
    "P1a_short_held_after_up_impulse_end": (-1, "ew"),
    "P1b_long_held_after_down_impulse_end": (1, "ew"),
    "P2a_long_held_in_up_leg_fib_zone": (1, "fib"),
    "P2b_short_held_in_down_leg_fib_zone": (-1, "fib"),
}
START = datetime(2018, 3, 1, tzinfo=UTC).timestamp() * 1000
END = datetime(2022, 1, 1, tzinfo=UTC).timestamp() * 1000


def _key(ms: float) -> tuple[str, str]:
    at = datetime.fromtimestamp(ms / 1000, UTC)
    iso = at.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}", at.strftime("%Y-%m")


def _exposures() -> list[tuple[float, str, str, dict[str, bool]]]:
    index, hours, _ = join.market_index()
    closes = [h.close_ms for h in hours]
    grid: list[tuple[float, str, str, dict[str, bool]]] = []
    for h in hours:
        t = float(h.close_ms + 1)
        if not START <= t < END:
            continue
        states = {
            name: join.exposed(index, closes, hours, t, need, kind)
            for name, (need, kind) in TESTS.items()
        }
        if any(v is None for v in states.values()):
            continue
        week, month = _key(t)
        grid.append((t, week, month, {k: bool(v) for k, v in states.items()}))
    return grid


def precheck() -> dict[str, object]:
    grid = _exposures()
    weeks_all = {w for _, w, _, _ in grid}
    out: dict[str, object] = {"grid_hours": len(grid), "weeks": len(weeks_all)}
    ok = True
    for name in TESTS:
        exposed_weeks = {w for _, w, _, s in grid if s[name]}
        hours_exposed = sum(1 for *_, s in grid if s[name])
        mde = 2.8 * math.sqrt(0.25 / max(1, len(exposed_weeks)) + 0.25 / len(weeks_all))
        passed = mde <= 0.15 and len(exposed_weeks) >= 30
        ok = ok and passed
        out[name] = {
            "exposed_hours": hours_exposed,
            "exposed_weeks": len(exposed_weeks),
            "approx_mde": round(mde, 4),
            "pass": passed,
        }
    out["precheck_pass"] = ok
    return out


def _stratified(cells: list[tuple[str, int, int, int, int]]) -> float | None:
    """cells: (월, 노출 시각, 노출 중 결과, 비노출 시각, 비노출 중 결과)."""
    by_month: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0, 0])
    for month, e, a, u, b in cells:
        m = by_month[month]
        m[0] += e
        m[1] += a
        m[2] += u
        m[3] += b
    num = den = 0.0
    for e, a, u, b in by_month.values():
        if e and u:
            num += e * (a / e - b / u)
            den += e
    return num / den if den else None


def run() -> dict[str, object]:
    grid = _exposures()
    fills, funding, _ = load_xbtusd(SOURCE)
    trips = sorted(build_trips(fills, funding), key=lambda t: t.opened)
    starts = [t.opened.timestamp() * 1000 for t in trips]

    def held(at: float) -> int:
        k = bisect_right(starts, at) - 1
        if k < 0:
            return 0
        trip = trips[k]
        end = trip.closed.timestamp() * 1000 if trip.closed else float("inf")
        return trip.side if at < end else 0

    positions = [held(t) for t, *_ in grid]
    rng = random.Random(SEED)
    report: dict[str, object] = {}
    pvalues: dict[str, float] = {}
    for name, (need, _kind) in TESTS.items():
        cells_by_week: dict[str, dict[str, list[int]]] = defaultdict(
            lambda: defaultdict(lambda: [0, 0, 0, 0])
        )
        for (_, week, month, states), s in zip(grid, positions, strict=True):
            c = cells_by_week[week][month]
            if states[name]:
                c[0] += 1
                c[1] += s == need
            else:
                c[2] += 1
                c[3] += s == need
        weeks = list(cells_by_week.values())

        def flatten(chosen: list[dict[str, list[int]]]) -> list[tuple[str, int, int, int, int]]:
            return [(m, *v) for wk in chosen for m, v in wk.items()]  # type: ignore[misc]

        point = _stratified(flatten(weeks))
        boots = []
        for _ in range(BOOT):
            d = _stratified(flatten([weeks[rng.randrange(len(weeks))] for _ in weeks]))
            if d is not None:
                boots.append(d)
        boots.sort()
        p = sum(b <= 0 for b in boots) / len(boots)
        pvalues[name] = p
        e = sum(v[0] for wk in weeks for v in wk.values())
        a = sum(v[1] for wk in weeks for v in wk.values())
        u = sum(v[2] for wk in weeks for v in wk.values())
        b = sum(v[3] for wk in weeks for v in wk.values())
        report[name] = {
            "exposed_hours": e,
            "held_rate_exposed": round(a / e, 4) if e else None,
            "held_rate_unexposed": round(b / u, 4) if u else None,
            "stratified_diff": None if point is None else round(point, 4),
            "ci95": [
                round(boots[int(0.025 * len(boots))], 4),
                round(boots[int(0.975 * len(boots)) - 1], 4),
            ],
            "p_one_sided": round(p, 4),
        }
    order = sorted(pvalues, key=lambda k: pvalues[k])
    still = True
    for rank, name in enumerate(order):
        still = still and pvalues[name] <= 0.05 / (len(order) - rank)
        entry = report[name]
        assert isinstance(entry, dict)
        entry["holm_significant"] = still and (entry["stratified_diff"] or 0) > 0
    flat_share = sum(s == 0 for s in positions) / len(positions)
    return {
        "grid_hours": len(grid),
        "wa_flat_share_of_hours": round(flat_share, 4),
        "tests": report,
        "note": "문서 36 1회 실행. 준확증(35 결과를 본 뒤), CROSS_VENUE_PROXY.",
    }


def main(argv: list[str]) -> int:
    modes = {"precheck": precheck, "run": run}
    if not argv or argv[0] not in modes:
        raise SystemExit("usage: audit_wa_position_state.py precheck|run")
    sys.stdout.write(json.dumps(modes[argv[0]](), ensure_ascii=True, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
