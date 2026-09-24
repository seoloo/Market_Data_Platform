"""문서 40 RSI 다이버전스 수익 시험 — ``power``(사건 수·MDE, 수익률 없음) / ``test``(승인 후 1회).

BTCUSDT(DB) + ETHUSDT(내려받은 1m) USD-M, 2020-01~. 다이버전스는 `exposure.DivergenceIndex`.
"""

from __future__ import annotations

import json
import math
import sys
from collections import Counter
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
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
from auto_trading.research.elliott.divergence import DivergenceKind  # noqa: E402
from auto_trading.research.elliott.exposure import DivergenceIndex  # noqa: E402
from auto_trading.research.elliott.minute_path import replay_minute_groups_ladder  # noqa: E402
from auto_trading.research.elliott.pivots import EW0_THRESHOLD_PCT  # noqa: E402
from auto_trading.research.elliott.scanner import Trigger, causal_triggers  # noqa: E402

H4 = Timeframe.H4.ms
HORIZON = 6
MDE_GATE = 0.005
Sample = tuple[int, str, int]  # (마감 ms, 자산, 부호: 강세 +1 / 약세 −1 / 대조 0)


def _divergences(
    four: Sequence[Candle], hours: Sequence[Candle], minutes: Iterator[Candle]
) -> DivergenceIndex:
    triggers: list[Trigger] = []
    chunks = _chunks(four)
    for chunk in chunks:
        (replay,) = replay_minute_groups_ladder(
            chunk, _groups(minutes, chunk), chunk[-1].close_ms, (EW0_THRESHOLD_PCT,)
        )
        chunk_hours = [h for h in hours if chunk[0].open_ms <= h.open_ms <= chunk[-1].close_ms]
        triggers.extend(causal_triggers(replay, chunk_hours, chunk))
    return DivergenceIndex(triggers, chunks)


def _thin(items: Sequence[Sample]) -> list[Sample]:
    kept: list[Sample] = []
    last = -(10**18)
    for close_ms, asset, sign in sorted(items):
        if close_ms - last >= HORIZON * H4:
            kept.append((close_ms, asset, sign))
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
        index = _divergences(four, hours, minutes)
        close_at = {bar.close_ms: float(bar.close) for bar in four}
        closes[asset] = close_at
        fwd_all.extend(
            math.log(close_at[c + step] / v) for c, v in close_at.items() if c + step in close_at
        )
        signs = {
            c: (1 if k is DivergenceKind.REGULAR_BULLISH else -1)
            for c, k in index.events
            if c + step in close_at
        }
        per_asset[asset] = dict(Counter("bull" if s > 0 else "bear" for s in signs.values()))
        events.extend((c, asset, s) for c, s in signs.items())
        controls.extend((c, asset, 0) for c in close_at if c + step in close_at and c not in signs)
    return _thin(events), _thin(controls), closes, fwd_all, per_asset


def power() -> dict[str, object]:
    ev, co, _, fwd_all, per_asset = _collect()
    sd = pstdev(fwd_all)
    mde = 2.84 * sd * math.sqrt(1 / len(ev) + 1 / len(co)) if ev and co else math.inf
    return {
        "events_by_asset_before_thinning": per_asset,
        "n1_events_independent": len(ev),
        "n1_by_sign": dict(Counter("bull" if s > 0 else "bear" for _, _, s in ev)),
        "n2_controls_independent": len(co),
        "sd_forward_6bar_log_return": round(sd, 5),
        "mde_conservative": round(mde, 5),
        "gate_mde_le_0_5pct": mde <= MDE_GATE,
        "note": "문서 40 §4. 사건 뒤 수익률은 계산하지 않았다.",
    }


def _log_return(sample: Sample, closes: dict[str, dict[int, float]]) -> float:
    close_ms, asset, _ = sample
    at = closes[asset]
    return math.log(at[close_ms + HORIZON * H4] / at[close_ms])


def _mean_var(xs: Sequence[float]) -> tuple[float, float]:
    m = sum(xs) / len(xs)
    return m, sum((x - m) ** 2 for x in xs) / (len(xs) - 1)


def test() -> dict[str, object]:
    """문서 40 §3 — 연구자 승인 뒤 한 번만 실행."""
    ev, co, closes, _, _ = _collect()
    r_ev = [s * _log_return(x, closes) for x in ev for s in (x[2],)]
    r_co = [_log_return(x, closes) for x in co]
    w_plus = sum(1 for x in ev if x[2] > 0) / len(ev)
    tilt = w_plus - (1 - w_plus)
    m1, v1 = _mean_var(r_ev)
    m2, v2 = _mean_var(r_co)
    d = m1 - tilt * m2
    se = math.sqrt(v1 / len(r_ev) + tilt**2 * v2 / len(r_co))
    z = d / se
    p = 1 - NormalDist().cdf(z)

    def by(key: str) -> dict[str, list[float]]:
        groups: dict[str, list[float]] = {}
        for x, r in zip(ev, r_ev, strict=True):
            label = (
                ("bull" if x[2] > 0 else "bear")
                if key == "sign"
                else x[1]
                if key == "asset"
                else datetime.fromtimestamp(x[0] / 1000, UTC).strftime("%Y")
            )
            groups.setdefault(label, []).append(r)
        return {k: [len(g), round(sum(g) / len(g), 5)] for k, g in sorted(groups.items())}

    return {
        "n1": len(r_ev),
        "n2": len(r_co),
        "bull_share": round(w_plus, 4),
        "mean_event_R": round(m1, 5),
        "mean_control_r": round(m2, 5),
        "sign_matched_control": round(tilt * m2, 5),
        "D": round(d, 5),
        "z": round(z, 3),
        "p_one_sided": round(p, 4),
        "significant_0_05": p < 0.05,
        "D_exceeds_cost_0_1pct": d > 0.001,
        "secondary_descriptive": {
            "event_R_by_sign": by("sign"),
            "event_R_by_asset": by("asset"),
            "event_R_by_year": by("year"),
        },
        "note": "문서 40 §3 1회 판정. 보조는 기술 통계.",
    }


def main(argv: list[str]) -> int:
    modes = {"power": power, "test": test}
    mode = argv[0] if argv else "power"
    if mode not in modes:
        raise SystemExit("usage: audit_div_edge.py [power|test]")
    sys.stdout.write(json.dumps(modes[mode](), ensure_ascii=True, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
