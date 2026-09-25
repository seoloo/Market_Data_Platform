"""거시 지표 발표 첫 반응의 지속·반전 — BTCUSDT 무기한 (docs/research/49).

사용 예::

    python -m scripts.screen_macro_drift measure   # 빈도·변동성만 (방향·수익 미계산)
    python -m scripts.screen_macro_drift train     # 사전 등록 뒤 1회 — 2021~2022

Validation(2023~2024)·OOS(2025~2026-08)는 Train 통과 뒤 발표 일정 자료를 모아 추가한다(49 §4).
train 은 작업 트리가 깨끗해야 하고, 이미 결과 파일이 있으면 거부한다. DB 는 읽기 전용.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scripts.screen_perp_events import (
    COST_BASE,
    COST_REFERENCE,
    _describe,
    _guard,
    _p_value,
    _stats,
    _strategy,
    _write,
    _year,
)

from auto_trading.backtest.macro_drift import reaction_events
from auto_trading.backtest.perp_events import BAR_MS, Bars, EventTrade, PerpEvent, settle_trade

DB = Path("data/trading.db")
OUTPUT = Path("reports/macro_drift")
SYMBOL = "BTCUSDT"
INDICATORS = ("CPI", "NFP", "PCE", "FOMC")
WINDOW_BARS = 3
"""첫 반응 창: 15분."""
HOLD_BARS = 48
"""진입 뒤 보유: 4시간."""
MIN_EVENTS = 30
ALPHA = 0.05


def _ms(y: int, m: int, d: int) -> int:
    return int(datetime(y, m, d, tzinfo=UTC).timestamp() * 1000)


TRAIN = (_ms(2021, 1, 1), _ms(2023, 1, 1))
TRAIN_YEARS = {"2021": (_ms(2021, 1, 1), _ms(2022, 1, 1)), "2022": (_ms(2022, 1, 1), TRAIN[1])}


def load() -> tuple[Bars, list[tuple[int, float]], list[tuple[str, int]]]:
    lo, hi = TRAIN[0] - 86_400_000, TRAIN[1] + 86_400_000
    with sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True) as conn:
        rows = conn.execute(
            "SELECT open_ms, open, high, low, close FROM candles WHERE exchange = 'binance_usdm'"
            " AND symbol = ? AND timeframe = '5m' AND open_ms >= ? AND open_ms < ?"
            " ORDER BY open_ms",
            (SYMBOL, lo, hi),
        ).fetchall()
        settlements = [
            (int(t), float(r))
            for t, r in conn.execute(
                "SELECT funding_ms, rate FROM funding_rates WHERE exchange = 'binance_usdm'"
                " AND symbol = ? AND funding_ms >= ? AND funding_ms < ? ORDER BY funding_ms",
                (SYMBOL, lo, hi),
            )
        ]
        marks = ",".join("?" * len(INDICATORS))
        releases = [
            (str(ind), int(ms))
            for ind, ms in conn.execute(
                f"SELECT indicator, COALESCE(released_ms, scheduled_ms) FROM macro_events"
                f" WHERE indicator IN ({marks}) ORDER BY 2",
                INDICATORS,
            )
            if TRAIN[0] <= int(ms) < TRAIN[1]
        ]
    n = len(rows)
    bars = Bars(
        open_ms=[int(r[0]) for r in rows],
        open=[float(r[1]) for r in rows],
        high=[float(r[2]) for r in rows],
        low=[float(r[3]) for r in rows],
        close=[float(r[4]) for r in rows],
        oi=[None] * n,
        funding=[None] * n,
    )
    return bars, settlements, releases


def _events(bars: Bars, releases: list[tuple[str, int]]) -> list[tuple[str, PerpEvent]]:
    kind = {ms: ind for ind, ms in releases}
    evs = reaction_events(
        bars, [ms for _, ms in releases], window_bars=WINDOW_BARS, hold_bars=HOLD_BARS
    )
    return [(kind[bars.open_ms[e.signal_index - WINDOW_BARS + 1]], e) for e in evs]


def measure() -> dict[str, Any]:
    """빈도와 **방향 없는** 크기만.

    첫 반응 |수익|, 진입 뒤 4시간 수익의 표준편차 (대조: 7일 전 같은 시각).
    """
    bars, _, releases = load()
    evs = _events(bars, releases)
    index = {t: i for i, t in enumerate(bars.open_ms)}

    def after(i: int) -> float | None:
        j = i + HOLD_BARS - 1
        return bars.close[j] / bars.open[i] - 1 if j < len(bars.close) else None

    event_times = {ms for _, ms in releases}
    react, post, control = [], [], []
    for _, e in evs:
        react.append(abs(bars.close[e.signal_index] / bars.open[e.signal_index - 2] - 1))
        x = after(e.entry_index)
        if x is not None:
            post.append(x)
        t = bars.open_ms[e.entry_index] - 7 * 86_400_000
        if t in index and (t - WINDOW_BARS * BAR_MS) not in event_times:
            c = after(index[t])
            if c is not None:
                control.append(c)
    by: dict[str, dict[str, int]] = {}
    for kind, e in evs:
        by.setdefault(_year(bars.open_ms[e.entry_index]), {}).setdefault(kind, 0)
        by[_year(bars.open_ms[e.entry_index])][kind] += 1
    return {
        "note": "문서 49 §2. 방향·수익은 계산하지 않았다 (크기·표준편차만).",
        "releases": len(releases),
        "events": len(evs),
        "by_year_kind": by,
        "first_15m_abs_move_bps": {
            "median": statistics.median(react) * 1e4,
            "p90": sorted(react)[int(0.9 * (len(react) - 1))] * 1e4,
        },
        "post_4h_sd_bps": statistics.stdev(post) * 1e4,
        "control_same_time_7d_before_sd_bps": statistics.stdev(control) * 1e4,
        "control_n": len(control),
    }


def _trades(
    bars: Bars,
    settlements: list[tuple[int, float]],
    evs: list[tuple[str, PerpEvent]],
    *,
    sign: int,
    hold: int,
    cost: float,
    use_stop: bool,
) -> list[tuple[str, EventTrade]]:
    out = []
    for kind, e in evs:
        tr = settle_trade(
            bars,
            e,
            direction=sign * e.side,
            hold_bars=hold,
            cost_per_side=cost,
            settlements=settlements,
            use_stop=use_stop,
        )
        if tr is not None:
            out.append((kind, tr))
    return out


def train(output: Path) -> int:
    """49 §4 T1~T3."""
    guard = _guard("train", output)
    if guard is None:
        return 3
    path, sha = guard
    bars, settlements, releases = load()
    evs = _events(bars, releases)
    # 방향: 지속으로 잰 비용 전 평균(스톱 없음)의 부호로 정하고 양측 검정한다 (49 §3)
    cont = _trades(bars, settlements, evs, sign=1, hold=HOLD_BARS, cost=0.0, use_stop=False)
    sign = 1 if cont and statistics.fmean(t.gross + t.funding for _, t in cont) >= 0 else -1
    primary = _trades(
        bars, settlements, evs, sign=sign, hold=HOLD_BARS, cost=COST_BASE, use_stop=True
    )
    trades = [t for _, t in primary]
    desc = _describe(trades)
    net = desc["net"]
    p = _p_value(net.get("t"), two_sided=True)
    years = {
        y: _stats([t.net for t in trades if lo <= t.entry_ms < hi])
        for y, (lo, hi) in TRAIN_YEARS.items()
    }
    crit = [
        ("T1_events", net["n"] >= MIN_EVENTS, net["n"]),
        (
            "T2_net_mean_positive_p05_two_sided",
            bool((net.get("mean_bps") or 0) > 0 and p < ALPHA),
            {"mean_bps": net.get("mean_bps"), "p": p},
        ),
        (
            "T3_both_years_positive",
            all((years[y].get("mean_bps") or 0) > 0 for y in years),
            {y: years[y].get("mean_bps") for y in years},
        ),
    ]
    reference: dict[str, Any] = {}
    for label, hold, cost, stop in (
        ("no_stop", HOLD_BARS, COST_BASE, False),
        ("cost_reference_0_023pct", HOLD_BARS, COST_REFERENCE, True),
        ("hold_1h", 12, COST_BASE, True),
        ("hold_12h", 144, COST_BASE, True),
    ):
        tr = [
            t
            for _, t in _trades(
                bars, settlements, evs, sign=sign, hold=hold, cost=cost, use_stop=stop
            )
        ]
        reference[label] = {"net": _stats([t.net for t in tr]), **_strategy(tr)}
    record = {
        "git_sha": sha,
        "executed_at": datetime.now(tz=UTC).isoformat(),
        "config": {
            "window_bars": WINDOW_BARS,
            "hold_bars": HOLD_BARS,
            "cost": COST_BASE,
            "indicators": INDICATORS,
        },
        "direction": "continuation" if sign == 1 else "reversal",
        "direction_sign": sign,
        "primary": desc,
        "p_value_two_sided": p,
        "years": years,
        "by_kind": {k: _stats([t.net for kk, t in primary if kk == k]) for k in INDICATORS},
        "strategy_1x": _strategy(trades),
        "reference_only": reference,
        "criteria": [{"name": a, "passed": b, "detail": c} for a, b, c in crit],
        "status": "PASS" if all(b for _, b, _ in crit) else "REJECT",
    }
    _write(path, record)
    print(
        f"[train] MD ({record['direction']}): {record['status']}  "
        + "  ".join(f"{c['name']}={'통과' if c['passed'] else '실패'}" for c in record["criteria"])
    )
    print(f"기록: {path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("measure", "train"))
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    if args.stage == "measure":
        sys.stdout.write(json.dumps(measure(), ensure_ascii=False, indent=1) + "\n")
        return 0
    return train(args.output)


if __name__ == "__main__":
    sys.exit(main())
