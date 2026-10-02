"""문서 34 수익 가능성 시험 — §6 합성 구간 찾기 점검, §5 시장 사건 수·검정력 (수익률 미열람).

``power-spot``: 문서 93 A — 같은 판독기·규칙, 자료만 현물 BTCUSDT 1m 2017-08 ~ 2026-08.

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
import zipfile
from collections import Counter
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from decimal import Decimal
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


# ---------------------------------------------------------------- §9 BTC + ETH 합산 (수익률 미열람)

ETH_DIR = REPO_ROOT / "data" / "external" / "binance_vision" / "um" / "ETHUSDT" / "1m"
H4 = Timeframe.H4.ms


def _eth_minutes() -> Iterator[Candle]:
    """내려받은 ETHUSDT 1m zip 을 월 순서대로 읽는다 (헤더 줄은 건너뜀)."""
    for path in sorted(ETH_DIR.glob("ETHUSDT-1m-*.zip")):
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


SPOT_BTC_DIR = REPO_ROOT / "data" / "external" / "binance_vision" / "spot" / "BTCUSDT" / "1m"
_MICROS = 10**14
"""2025-01 부터 현물 파일의 시각은 마이크로초다 — 이보다 크면 1,000 으로 나눈다."""


def _vision_minutes(directory: Path, symbol: str) -> Iterator[Candle]:
    """data.binance.vision 1m zip 을 월 순서대로 읽는다 (헤더 줄은 건너뜀, 마이크로초 시각 처리)."""
    for path in sorted(directory.glob(f"{symbol}-1m-*.zip")):
        with zipfile.ZipFile(path) as archive:
            (name,) = archive.namelist()
            for line in archive.read(name).decode("ascii").splitlines():
                cells = line.split(",")
                if not cells[0].isdigit():
                    continue
                open_ms = int(cells[0])
                if open_ms > _MICROS:
                    open_ms //= 1000
                yield Candle(
                    open_ms=open_ms,
                    close_ms=open_ms + Timeframe.M1.ms - 1,
                    open=Decimal(cells[1]),
                    high=Decimal(cells[2]),
                    low=Decimal(cells[3]),
                    close=Decimal(cells[4]),
                    volume=Decimal(cells[5]),
                )


def _aggregate(minutes: Iterator[Candle], step: int) -> list[Candle]:
    out: list[Candle] = []
    bucket: list[Candle] = []
    for m in minutes:
        if bucket and m.open_ms // step != bucket[0].open_ms // step:
            out.append(_bar(bucket, step))
            bucket = []
        bucket.append(m)
    if bucket:
        out.append(_bar(bucket, step))
    return out


def _bar(bucket: list[Candle], step: int) -> Candle:
    start = bucket[0].open_ms // step * step
    return Candle(
        open_ms=start,
        close_ms=start + step - 1,
        open=bucket[0].open,
        high=max(c.high for c in bucket),
        low=min(c.low for c in bucket),
        close=bucket[-1].close,
        volume=sum((c.volume for c in bucket), start=Decimal(0)),
    )


def _chunks(four: Sequence[Candle]) -> list[list[Candle]]:
    """1m 이 하나도 없는 4h 에서 자료를 끊는다 (§9)."""
    chunks: list[list[Candle]] = [[]]
    for bar in four:
        if chunks[-1] and bar.open_ms != chunks[-1][-1].open_ms + H4:
            chunks.append([])
        chunks[-1].append(bar)
    return [c for c in chunks if c]


def _groups(minutes: Iterator[Candle], four: Sequence[Candle]) -> Iterator[list[Candle]]:
    row = next(minutes, None)
    for outer in four:
        while row is not None and row.open_ms < outer.open_ms:
            row = next(minutes, None)
        group: list[Candle] = []
        while row is not None and row.open_ms < outer.open_ms + H4:
            group.append(row)
            row = next(minutes, None)
        yield group


def _scan_asset(
    four: Sequence[Candle], hours: Sequence[Candle], minute_source: Iterator[Candle] | None
) -> tuple[list[TriggerResult], int]:
    results: list[TriggerResult] = []
    blocked = 0
    for chunk in _chunks(four):
        source = minute_source if minute_source is not None else iter(())
        (replay,) = replay_minute_groups_ladder(
            chunk, _groups(source, chunk), chunk[-1].close_ms, (EW0_THRESHOLD_PCT,)
        )
        blocked += len(replay.blocked_four_hours)
        chunk_hours = [h for h in hours if chunk[0].open_ms <= h.open_ms <= chunk[-1].close_ms]
        results.extend(evaluate_triggers(causal_triggers(replay, chunk_hours, chunk), chunk_hours))
    return results, blocked


def _btc_bars() -> tuple[tuple[Candle, ...], tuple[Candle, ...], int]:
    conn = sqlite3.connect(market.DB_PATH.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        conn.execute("PRAGMA query_only=ON")
        last_open = conn.execute(
            "SELECT MAX(open_ms) FROM candles WHERE exchange='binance_usdm' AND symbol='BTCUSDT' "
            "AND timeframe='4h'"
        ).fetchone()[0]
        to_ms = int(last_open) + H4
        four = tuple(market._candle(r) for r in _rows(conn, "4h", to_ms))
        hours = tuple(market._candle(r) for r in _rows(conn, "1h", to_ms))
    finally:
        conn.close()
    return four, hours, to_ms


def _btc_minutes(to_ms: int) -> Iterator[Candle]:
    conn = sqlite3.connect(market.DB_PATH.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        conn.execute("PRAGMA query_only=ON")
        for row in _rows(conn, "1m", to_ms):
            yield market._candle(row)
    finally:
        conn.close()


def _pooled_thin(results: Sequence[tuple[str, TriggerResult]]) -> list[tuple[str, TriggerResult]]:
    """두 자산을 한 시간축에서 겹침 제거 (§9, 보수적)."""
    kept: list[tuple[str, TriggerResult]] = []
    last = -(10**18)
    for asset, r in sorted(results, key=lambda x: (x[1].close_ms, x[0])):
        if r.close_ms - last >= HORIZON * H4:
            kept.append((asset, r))
            last = r.close_ms
    return kept


def power_pooled() -> dict[str, object]:
    btc_four, btc_hours, to_ms = _btc_bars()
    eth_four = _aggregate(_eth_minutes(), H4)
    eth_hours = _aggregate(_eth_minutes(), Timeframe.H1.ms)
    assets = {
        "BTCUSDT": (btc_four, btc_hours, _btc_minutes(to_ms)),
        "ETHUSDT": (tuple(eth_four), tuple(eth_hours), _eth_minutes()),
    }
    tagged: list[tuple[str, TriggerResult]] = []
    fwd_all: list[float] = []
    per_asset: dict[str, object] = {}
    for asset, (four, hours, minutes) in assets.items():
        results, blocked = _scan_asset(four, hours, minutes)
        close_at = {bar.close_ms: float(bar.close) for bar in four}
        usable = [r for r in results if r.close_ms + HORIZON * H4 in close_at]
        fwd = [
            math.log(close_at[c + HORIZON * H4] / v)
            for c, v in close_at.items()
            if c + HORIZON * H4 in close_at
        ]
        fwd_all.extend(fwd)
        events = [r for r in usable if r.is_event]
        solo_ev = _pooled_thin([(asset, r) for r in events])
        solo_co = _pooled_thin([(asset, r) for r in usable if not r.is_event])
        sd = pstdev(fwd)
        per_asset[asset] = {
            "four_hour_bars": len(four),
            "chunks": len(_chunks(four)),
            "blocked_four_hours": blocked,
            "triggers": len(results),
            "events": len(events),
            "n1": len(solo_ev),
            "n2": len(solo_co),
            "sd": round(sd, 5),
            "mde_alone": round(2.84 * sd * math.sqrt(1 / len(solo_ev) + 1 / len(solo_co)), 5)
            if solo_ev and solo_co
            else None,
        }
        tagged.extend((asset, r) for r in usable)
    ev = _pooled_thin([x for x in tagged if x[1].is_event])
    co = _pooled_thin([x for x in tagged if not x[1].is_event])
    sd = pstdev(fwd_all)
    n1, n2 = len(ev), len(co)
    mde = 2.84 * sd * math.sqrt(1 / n1 + 1 / n2) if n1 and n2 else math.inf
    return {
        "per_asset": per_asset,
        "pooled_n1_events_independent": n1,
        "pooled_n2_controls_independent": n2,
        "pooled_events_by_asset": dict(sorted(Counter(a for a, _ in ev).items())),
        "pooled_sd": round(sd, 5),
        "pooled_mde": round(mde, 5),
        "gate_mde_le_2pct": mde <= MDE_GATE,
        "note": "문서 34 §9. 사건 뒤 수익률은 계산하지 않았다.",
    }


def power_spot() -> dict[str, object]:
    """문서 93 A. 34 §3~§5 그대로, 자료만 현물 2017-08 ~ 2026-08. 수익률은 계산하지 않는다."""

    def minutes() -> Iterator[Candle]:
        return _vision_minutes(SPOT_BTC_DIR, "BTCUSDT")

    four = tuple(_aggregate(minutes(), H4))
    hours = tuple(_aggregate(minutes(), Timeframe.H1.ms))
    results, blocked = _scan_asset(four, hours, minutes())
    close_at = {bar.close_ms: float(bar.close) for bar in four}
    usable = [r for r in results if r.close_ms + HORIZON * H4 in close_at]
    fwd = [
        math.log(close_at[c + HORIZON * H4] / v)
        for c, v in close_at.items()
        if c + HORIZON * H4 in close_at
    ]
    events = [r for r in usable if r.is_event]
    ev = _pooled_thin([("BTCUSDT", r) for r in events])
    co = _pooled_thin([("BTCUSDT", r) for r in usable if not r.is_event])
    sd = pstdev(fwd)
    n1, n2 = len(ev), len(co)
    mde = 2.84 * sd * math.sqrt(1 / n1 + 1 / n2) if n1 and n2 else math.inf

    def year(ms: int) -> str:
        return datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y")

    cut = 1_577_836_800_000  # 2020-01-01 — 34 와 겹치는 구간과 새 구간을 나눠 보고만 한다
    return {
        "period_utc": f"{four[0].open_ms}/{four[-1].close_ms}",
        "four_hour_bars": len(four),
        "hour_bars": len(hours),
        "chunks": len(_chunks(four)),
        "blocked_four_hours": blocked,
        "triggers": len(results),
        "events": len(events),
        "controls": len(usable) - len(events),
        "top_pattern_mix": dict(
            sorted(Counter("|".join(r.top_patterns) or "NONE" for r in results).items())
        ),
        "events_by_year": dict(sorted(Counter(year(r.close_ms) for r in events).items())),
        "events_by_direction": dict(sorted(Counter(str(r.direction) for r in events).items())),
        "n1_events_independent": n1,
        "n2_controls_independent": n2,
        "n1_before_2020": sum(1 for _, r in ev if r.close_ms < cut),
        "sd_forward_30bar_log_return": round(sd, 5),
        "mde": round(mde, 5),
        "gate_mde_le_2pct": mde <= MDE_GATE,
        "note": "문서 93 A. 사건 뒤 수익률은 계산하지 않았다.",
    }


def main(argv: list[str]) -> int:
    modes = {
        "synthetic": synthetic,
        "power": power,
        "power-pooled": power_pooled,
        "power-spot": power_spot,
    }
    if not argv or argv[0] not in modes:
        raise SystemExit("usage: audit_ew_edge_power.py synthetic|power|power-pooled|power-spot")
    result = modes[argv[0]]()
    sys.stdout.write(json.dumps(result, ensure_ascii=True, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
