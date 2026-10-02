"""문서 93 B — 1h degree Elliott 되돌림: 합성 점검과 시장 사건 수·검정력 (수익률 미열람).

판독기(EW-3d′: 4h 카운트 · 1h 하위파 · 1m 경로)는 **한 줄도 바꾸지 않는다.**
대신 시간을 4 배로 늘려 넣는다:

* 실제 1h 봉 → 시각 × 4 = 판독기가 보는 "4h" 봉
* 실제 15m 봉 → 시각 × 4 = "1h" 봉
* 실제 1m 봉 → 같은 봉 4 개(시각 × 4 + 0·1·2·3 분) = "1m" 경로. 1m 보다 잘게 나눈 자료가 없어서다.
  1 분 안의 순서는 원래도 모르는 정보라 새 정보를 만들지 않는다.
  경로가 거칠어지는 효과는 합성 점검이 잰다.

꼭짓점 임계 1% (4h 의 2% × √(1/4)). 시작 후보 창·호라이즌은 봉 수로 같다
(360 봉 · 30 봉 = 실제 15 일 · 30 시간).

``synthetic``: 개발 seed 20260924 합성 경로를 진폭·임계 절반으로 만들고 1m 경로를 4 분 단위로
거칠게 해서 34 §6 기준(impulse 1순위 IMPULSE ≥ 50% · zigzag ≤ 30%)을 본다.
같은 seed 의 원래 설정(34 §8 재현)을 나란히 적는다.
``power``: 현물 BTCUSDT 1m 2017-08 ~ 2026-08 로 사건 수·n1·n2·SD·MDE.
**사건 뒤 수익률은 계산하지 않는다.**
"""

from __future__ import annotations

import json
import math
import random
import sys
from collections import Counter
from collections.abc import Iterator, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from statistics import pstdev

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from scripts.audit_ew3c_hourly import CELLS, DEGREES, hour_bars  # noqa: E402
from scripts.audit_ew_edge_power import (  # noqa: E402
    SPOT_BTC_DIR,
    _aggregate,
    _chunks,
    _groups,
    _pooled_thin,
    _vision_minutes,
)

from auto_trading.core.enums import Timeframe  # noqa: E402
from auto_trading.core.types import Candle  # noqa: E402
from auto_trading.research.elliott import synthetic as synthetic_module  # noqa: E402
from auto_trading.research.elliott.minute_path import replay_minute_groups_ladder  # noqa: E402
from auto_trading.research.elliott.pivots import EW0_THRESHOLD_PCT  # noqa: E402
from auto_trading.research.elliott.scanner import (  # noqa: E402
    TriggerResult,
    causal_triggers,
    evaluate_triggers,
)
from auto_trading.research.elliott.synthetic import build_path  # noqa: E402

DEV_SEED = 20260924
CONFIRM_SEED = 20260925
"""41 §2 의 확인 seed (정답 구간 채점용). 20260927 은 JEV 예약이라 쓰지 않는다."""
SCALE = 4
"""시간 배율 — 실제 1h 를 판독기의 4h 로."""
THRESHOLD_B = EW0_THRESHOLD_PCT / 2
HORIZON = 30
MDE_GATE_B = 0.01
H4 = Timeframe.H4.ms
M1 = Timeframe.M1.ms


# ---------------------------------------------------------------- 공통


def coarsen(minutes: Sequence[Candle], k: int = SCALE) -> list[Candle]:
    """연속 ``k`` 개 1m 을 하나로 합친 봉을 같은 ``k`` 개 시각에 복사한다 (시각·개수는 그대로)."""
    out: list[Candle] = []
    for i in range(0, len(minutes), k):
        group = minutes[i : i + k]
        merged = (
            group[0].open,
            max(c.high for c in group),
            min(c.low for c in group),
            group[-1].close,
        )
        for c in group:
            out.append(replace(c, open=merged[0], high=merged[1], low=merged[2], close=merged[3]))
    return out


# ---------------------------------------------------------------- 합성


_VARIANTS = {
    "a": (False, False),
    "b": (True, True),
    "half_only": (True, False),
    "coarse_only": (False, True),
}
"""(진폭·임계 절반, 1m 경로 거칠게). half_only·coarse_only 는 b 실패 원인 진단용(판정 아님)."""


def _synthetic_cell(
    variant: str, seed: int = DEV_SEED, keep_bars: bool = True
) -> dict[str, object]:
    """``a``: 34 §8 그대로 (재현 확인) · ``b``: 진폭·임계 절반 + 1m 경로 4 분 단위로 거칠게.

    ``keep_bars``: 진폭을 줄일 때 생성기 속도(``SPEED_PER_MINUTE``)도 같이 줄여 파동 길이(봉 수)를
    그대로 둔다. 실제 1h 파동을 4 배 늘려 넣으면 4h 파동과 같은 봉 수에 진폭만 절반이라서다.
    False 는 2026-10-02 첫 실행(잘못된 설정: 진폭과 함께 길이도 절반)을 재현할 때만 쓴다.
    """
    half, coarse = _VARIANTS[variant]
    original_speed = synthetic_module.SPEED_PER_MINUTE
    if half and keep_bars:
        synthetic_module.SPEED_PER_MINUTE = original_speed / 2
    try:
        return _synthetic_counts(half, coarse, seed)
    finally:
        synthetic_module.SPEED_PER_MINUTE = original_speed


def _synthetic_counts(half: bool, coarse: bool, seed: int) -> dict[str, object]:
    rng = random.Random(seed)
    out: Counter[str] = Counter()
    threshold = THRESHOLD_B if half else EW0_THRESHOLD_PCT
    for _degree, (scale, parent_threshold) in DEGREES.items():
        if half:
            scale, parent_threshold = scale / 2, parent_threshold / 2
        for pattern in ("IMPULSE", "ZIGZAG"):
            for noise, count in CELLS:
                for _ in range(count):
                    path = build_path(rng, pattern, scale, parent_threshold, noise)
                    if noise == 0.0:
                        continue
                    minutes = coarsen(path.minutes) if coarse else path.minutes
                    four = path.four_hour
                    hours = hour_bars(minutes)
                    groups = (minutes[240 * i : 240 * (i + 1)] for i in range(len(four)))
                    (replay,) = replay_minute_groups_ladder(
                        four, groups, four[-1].close_ms, (threshold,)
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


def synthetic() -> dict[str, object]:
    """문서 93 B §3 (정정판): b 를 개발 seed 와 확인 seed 에서 — **둘 다** 통과해야 시장 단계."""
    dev = _synthetic_cell("b", DEV_SEED)
    confirm = _synthetic_cell("b", CONFIRM_SEED)
    return {
        "a_reference_doc34": _synthetic_cell("a"),
        "b_first_run_wrong_lengths": _synthetic_cell("b", DEV_SEED, keep_bars=False),
        "b_dev_seed": dev,
        "b_confirm_seed": confirm,
        "check_pass": bool(dev["check_pass"]) and bool(confirm["check_pass"]),
        "note": "문서 93 B §3 정정판. check_pass(두 seed 모두)가 시장 단계의 선행 조건.",
    }


# ---------------------------------------------------------------- 시장 (수익률 미열람)


def _stretch(bars: Iterator[Candle]) -> Iterator[Candle]:
    for b in bars:
        open_ms = b.open_ms * SCALE
        yield replace(
            b, open_ms=open_ms, close_ms=open_ms + (b.close_ms - b.open_ms + 1) * SCALE - 1
        )


def _stretched_minutes() -> Iterator[Candle]:
    for m in _vision_minutes(SPOT_BTC_DIR, "BTCUSDT"):
        base = m.open_ms * SCALE
        for j in range(SCALE):
            yield replace(m, open_ms=base + j * M1, close_ms=base + (j + 1) * M1 - 1)


def power() -> dict[str, object]:
    real_hours = _aggregate(_vision_minutes(SPOT_BTC_DIR, "BTCUSDT"), Timeframe.H1.ms)
    real_quarters = _aggregate(_vision_minutes(SPOT_BTC_DIR, "BTCUSDT"), Timeframe.M15.ms)
    four = tuple(_stretch(iter(real_hours)))
    hours = tuple(_stretch(iter(real_quarters)))
    results: list[TriggerResult] = []
    blocked = 0
    minutes = _stretched_minutes()
    for chunk in _chunks(four):
        (replay,) = replay_minute_groups_ladder(
            chunk, _groups(minutes, chunk), chunk[-1].close_ms, (THRESHOLD_B,)
        )
        blocked += len(replay.blocked_four_hours)
        chunk_hours = [h for h in hours if chunk[0].open_ms <= h.open_ms <= chunk[-1].close_ms]
        results.extend(evaluate_triggers(causal_triggers(replay, chunk_hours, chunk), chunk_hours))
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

    def year(fake_ms: int) -> str:
        return datetime.fromtimestamp(fake_ms / SCALE / 1000, UTC).strftime("%Y")

    return {
        "real_hour_bars": len(four),
        "chunks": len(_chunks(four)),
        "blocked_hours": blocked,
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
        "sd_forward_30h_log_return": round(sd, 5),
        "mde": round(mde, 5),
        "gate_mde_le_1pct": mde <= MDE_GATE_B,
        "note": "문서 93 B. 사건 뒤 수익률은 계산하지 않았다.",
    }


def diagnose() -> dict[str, object]:
    """b 실패 원인 나누기 — 진폭 절반만 · 거칠게만. 판정에 쓰지 않는다."""
    return {v: _synthetic_cell(v, keep_bars=False) for v in ("half_only", "coarse_only")}


def main(argv: list[str]) -> int:
    modes = {"synthetic": synthetic, "power": power, "diagnose": diagnose}
    if not argv or argv[0] not in modes:
        raise SystemExit("usage: audit_ew93_hourly_degree.py synthetic|power|diagnose")
    sys.stdout.write(json.dumps(modes[argv[0]](), ensure_ascii=True, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
