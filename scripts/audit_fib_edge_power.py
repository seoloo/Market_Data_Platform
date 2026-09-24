"""문서 37 피보나치 되돌림 수익 시험 — 사건 수·검정력만 (수익률 계산 없음).

BTCUSDT(DB) + ETHUSDT(내려받은 1m) USD-M, 2020-01~.
직전 다리는 EW-3d′ 스캐너 발동 꼭짓점 열(`fib_leg`).
**사건 뒤 수익률은 계산하지 않는다.**
"""

from __future__ import annotations

import json
import math
import sys
from collections import Counter
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from statistics import NormalDist, pstdev

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from scripts.audit_ew_edge_power import (  # noqa: E402
    _aggregate,
    _btc_bars,
    _btc_minutes,
    _chunks,
    _eth_minutes,
    _groups,
)

from auto_trading.core.enums import Timeframe  # noqa: E402
from auto_trading.core.types import Candle  # noqa: E402
from auto_trading.research.elliott.exposure import FIB_ZONE, ExposureIndex  # noqa: E402
from auto_trading.research.elliott.minute_path import replay_minute_groups_ladder  # noqa: E402
from auto_trading.research.elliott.pivots import EW0_THRESHOLD_PCT  # noqa: E402
from auto_trading.research.elliott.scanner import (  # noqa: E402
    Trigger,
    TriggerResult,
    causal_triggers,
    evaluate_triggers,
)

H4 = Timeframe.H4.ms
HORIZON = 6
MDE_GATE = 0.005


def _index(
    four: Sequence[Candle], hours: Sequence[Candle], minutes: Iterator[Candle]
) -> ExposureIndex:
    triggers: list[Trigger] = []
    results: list[TriggerResult] = []
    for chunk in _chunks(four):
        (replay,) = replay_minute_groups_ladder(
            chunk, _groups(minutes, chunk), chunk[-1].close_ms, (EW0_THRESHOLD_PCT,)
        )
        chunk_hours = [h for h in hours if chunk[0].open_ms <= h.open_ms <= chunk[-1].close_ms]
        found = causal_triggers(replay, chunk_hours, chunk)
        triggers.extend(found)
        results.extend(evaluate_triggers(found, chunk_hours))
    return ExposureIndex(triggers, results)


def classify(index: ExposureIndex, four: Sequence[Candle]) -> list[tuple[int, str, int]]:
    """(마감 ms, EVENT|CONTROL, 다리 방향). 구간 안에 머무는 마감은 뺀다 (문서 37 §3)."""
    out: list[tuple[int, str, int]] = []
    prev_key: tuple[Decimal, Decimal, int] | None = None
    prev_in = False
    for bar in four:
        leg = index.fib_leg(bar.close_ms + 1)
        if leg is None:
            prev_key, prev_in = None, False
            continue
        key = (leg.a, leg.b, leg.direction)
        r = leg.retracement(bar.close)
        inside = FIB_ZONE[0] <= r <= FIB_ZONE[1]
        if inside and (key != prev_key or not prev_in):
            out.append((bar.close_ms, "EVENT", leg.direction))
        elif not inside:
            out.append((bar.close_ms, "CONTROL", leg.direction))
        prev_key, prev_in = key, inside
    return out


Sample = tuple[int, str, int]  # (마감 ms, 자산, 다리 방향)


def _thin(items: Sequence[Sample]) -> list[Sample]:
    kept: list[Sample] = []
    last = -(10**18)
    for close_ms, asset, direction in sorted(items):
        if close_ms - last >= HORIZON * H4:
            kept.append((close_ms, asset, direction))
            last = close_ms
    return kept


def _collect() -> tuple[
    list[Sample], list[Sample], dict[str, dict[int, float]], list[float], dict[str, object]
]:
    btc_four, btc_hours, to_ms = _btc_bars()
    eth_four = _aggregate(_eth_minutes(), H4)
    eth_hours = _aggregate(_eth_minutes(), Timeframe.H1.ms)
    assets = {
        "BTCUSDT": (btc_four, btc_hours, _btc_minutes(to_ms)),
        "ETHUSDT": (tuple(eth_four), tuple(eth_hours), _eth_minutes()),
    }
    events: list[Sample] = []
    controls: list[Sample] = []
    fwd_all: list[float] = []
    closes: dict[str, dict[int, float]] = {}
    per_asset: dict[str, object] = {}
    step = HORIZON * H4
    for asset, (four, hours, minutes) in assets.items():
        index = _index(four, hours, minutes)
        close_at = {bar.close_ms: float(bar.close) for bar in four}
        closes[asset] = close_at
        fwd_all.extend(
            math.log(close_at[c + step] / v) for c, v in close_at.items() if c + step in close_at
        )
        rows = [r for r in classify(index, four) if r[0] + step in close_at]
        kinds = Counter(kind for _, kind, _ in rows)
        per_asset[asset] = {
            "events": kinds["EVENT"],
            "controls": kinds["CONTROL"],
            "event_direction": dict(Counter(str(d) for _, k, d in rows if k == "EVENT")),
        }
        events.extend((c, asset, d) for c, k, d in rows if k == "EVENT")
        controls.extend((c, asset, d) for c, k, d in rows if k == "CONTROL")
    return _thin(events), _thin(controls), closes, fwd_all, per_asset


def power() -> dict[str, object]:
    ev, co, _, fwd_all, per_asset = _collect()
    sd = pstdev(fwd_all)
    mde = 2.84 * sd * math.sqrt(1 / len(ev) + 1 / len(co)) if ev and co else math.inf
    return {
        "per_asset": per_asset,
        "n1_events_independent": len(ev),
        "n2_controls_independent": len(co),
        "events_by_asset_independent": dict(Counter(a for _, a, _ in ev)),
        "sd_forward_6bar_log_return": round(sd, 5),
        "mde": round(mde, 5),
        "gate_mde_le_0_5pct": mde <= MDE_GATE,
        "note": "문서 37 §4. 사건 뒤 수익률은 계산하지 않았다.",
    }


def _outcome(sample: Sample, closes: dict[str, dict[int, float]]) -> float:
    close_ms, asset, direction = sample
    at = closes[asset]
    return direction * math.log(at[close_ms + HORIZON * H4] / at[close_ms])


def _mean_var(xs: Sequence[float]) -> tuple[float, float]:
    m = sum(xs) / len(xs)
    return m, sum((x - m) ** 2 for x in xs) / (len(xs) - 1)


def test() -> dict[str, object]:
    """문서 37 §3 검정 — 연구자 승인 뒤 한 번만 실행."""
    ev, co, closes, _, _ = _collect()
    r1 = [_outcome(s, closes) for s in ev]
    r2 = [_outcome(s, closes) for s in co]
    m1, v1 = _mean_var(r1)
    m2, v2 = _mean_var(r2)
    se = math.sqrt(v1 / len(r1) + v2 / len(r2))
    t_stat = (m1 - m2) / se
    df = (v1 / len(r1) + v2 / len(r2)) ** 2 / (
        (v1 / len(r1)) ** 2 / (len(r1) - 1) + (v2 / len(r2)) ** 2 / (len(r2) - 1)
    )
    p_one = 1 - NormalDist().cdf(t_stat)

    def group_mean(
        samples: Sequence[Sample], values: Sequence[float], key: str
    ) -> dict[str, object]:
        groups: dict[str, list[float]] = {}
        for s, v in zip(samples, values, strict=True):
            label = (
                s[1]
                if key == "asset"
                else str(s[2])
                if key == "direction"
                else datetime.fromtimestamp(s[0] / 1000, UTC).strftime("%Y")
            )
            groups.setdefault(label, []).append(v)
        return {k: [len(g), round(sum(g) / len(g), 5)] for k, g in sorted(groups.items())}

    diff = m1 - m2
    return {
        "n1": len(r1),
        "n2": len(r2),
        "mean_event": round(m1, 5),
        "mean_control": round(m2, 5),
        "diff": round(diff, 5),
        "welch_t": round(t_stat, 3),
        "welch_df": round(df, 1),
        "p_one_sided_normal_approx": round(p_one, 4),
        "significant_0_05": p_one < 0.05,
        "diff_exceeds_cost_0_1pct": diff > 0.001,
        "secondary_descriptive": {
            "event_by_asset": group_mean(ev, r1, "asset"),
            "control_by_asset": group_mean(co, r2, "asset"),
            "event_by_direction": group_mean(ev, r1, "direction"),
            "control_by_direction": group_mean(co, r2, "direction"),
            "event_by_year": group_mean(ev, r1, "year"),
            "control_by_year": group_mean(co, r2, "year"),
        },
        "note": "문서 37 §3 1회 판정. p 는 정규 근사(자유도 큼, scipy 없음). 보조는 기술 통계.",
    }


def main(argv: list[str]) -> int:
    modes = {"power": power, "test": test}
    mode = argv[0] if argv else "power"
    if mode not in modes:
        raise SystemExit("usage: audit_fib_edge_power.py [power|test]")
    sys.stdout.write(json.dumps(modes[mode](), ensure_ascii=True, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
