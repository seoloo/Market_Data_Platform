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
from decimal import Decimal
from pathlib import Path
from statistics import pstdev

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


def _thin(items: Sequence[tuple[int, str]]) -> list[tuple[int, str]]:
    kept: list[tuple[int, str]] = []
    last = -(10**18)
    for close_ms, asset in sorted(items):
        if close_ms - last >= HORIZON * H4:
            kept.append((close_ms, asset))
            last = close_ms
    return kept


def main() -> int:
    btc_four, btc_hours, to_ms = _btc_bars()
    eth_four = _aggregate(_eth_minutes(), H4)
    eth_hours = _aggregate(_eth_minutes(), Timeframe.H1.ms)
    assets = {
        "BTCUSDT": (btc_four, btc_hours, _btc_minutes(to_ms)),
        "ETHUSDT": (tuple(eth_four), tuple(eth_hours), _eth_minutes()),
    }
    events: list[tuple[int, str]] = []
    controls: list[tuple[int, str]] = []
    fwd_all: list[float] = []
    per_asset: dict[str, object] = {}
    for asset, (four, hours, minutes) in assets.items():
        index = _index(four, hours, minutes)
        close_at = {bar.close_ms: float(bar.close) for bar in four}
        step = HORIZON * H4
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
        events.extend((c, asset) for c, k, _ in rows if k == "EVENT")
        controls.extend((c, asset) for c, k, _ in rows if k == "CONTROL")
    ev, co = _thin(events), _thin(controls)
    sd = pstdev(fwd_all)
    mde = 2.84 * sd * math.sqrt(1 / len(ev) + 1 / len(co)) if ev and co else math.inf
    result = {
        "per_asset": per_asset,
        "n1_events_independent": len(ev),
        "n2_controls_independent": len(co),
        "events_by_asset_independent": dict(Counter(a for _, a in ev)),
        "sd_forward_6bar_log_return": round(sd, 5),
        "mde": round(mde, 5),
        "gate_mde_le_0_5pct": mde <= MDE_GATE,
        "note": "문서 37 §4. 사건 뒤 수익률은 계산하지 않았다.",
    }
    sys.stdout.write(json.dumps(result, ensure_ascii=True, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
