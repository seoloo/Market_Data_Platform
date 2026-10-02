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

import bisect
import json
import math
import random
import sys
from collections import Counter
from collections.abc import Iterator, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from statistics import NormalDist, pstdev

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
from auto_trading.features.tsmom import trend_direction  # noqa: E402
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


def _collect() -> tuple[
    list[TriggerResult], list[TriggerResult], list[TriggerResult], dict[int, float], dict[str, int]
]:
    """(전체 결과, 독립 사건, 독립 대조, 4h 마감 → 종가, 자료 통계). 수익률은 계산하지 않는다."""
    real_hours = _aggregate(_vision_minutes(SPOT_BTC_DIR, "BTCUSDT"), Timeframe.H1.ms)
    real_quarters = _aggregate(_vision_minutes(SPOT_BTC_DIR, "BTCUSDT"), Timeframe.M15.ms)
    # 거래량 0 인 1h(거래소 점검 채움 봉 4 개)는 자료 공백 — 그 자리에서 끊는다 (93 §8)
    four = tuple(_stretch(h for h in real_hours if h.volume > 0))
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
    ev = [r for _, r in _pooled_thin([("BTCUSDT", r) for r in usable if r.is_event])]
    co = [r for _, r in _pooled_thin([("BTCUSDT", r) for r in usable if not r.is_event])]
    stats = {
        "real_hour_bars": len(four),
        "zero_volume_hours_dropped": sum(1 for h in real_hours if h.volume == 0),
        "chunks": len(_chunks(four)),
        "blocked_hours": blocked,
        "usable": len(usable),
    }
    return results, ev, co, close_at, stats


def _year(fake_ms: int) -> str:
    return datetime.fromtimestamp(fake_ms / SCALE / 1000, UTC).strftime("%Y")


def power() -> dict[str, object]:
    results, ev, co, close_at, stats = _collect()
    fwd = [
        math.log(close_at[c + HORIZON * H4] / v)
        for c, v in close_at.items()
        if c + HORIZON * H4 in close_at
    ]
    events = [r for r in results if r.is_event and r.close_ms + HORIZON * H4 in close_at]
    sd = pstdev(fwd)
    n1, n2 = len(ev), len(co)
    mde = 2.84 * sd * math.sqrt(1 / n1 + 1 / n2) if n1 and n2 else math.inf
    return {
        **{k: v for k, v in stats.items() if k != "usable"},
        "triggers": len(results),
        "events": len(events),
        "controls": stats["usable"] - len(events),
        "top_pattern_mix": dict(
            sorted(Counter("|".join(r.top_patterns) or "NONE" for r in results).items())
        ),
        "events_by_year": dict(sorted(Counter(_year(r.close_ms) for r in events).items())),
        "events_by_direction": dict(sorted(Counter(str(r.direction) for r in events).items())),
        "n1_events_independent": n1,
        "n2_controls_independent": n2,
        "sd_forward_30h_log_return": round(sd, 5),
        "mde": round(mde, 5),
        "gate_mde_le_1pct": mde <= MDE_GATE_B,
        "note": "문서 93 B. 사건 뒤 수익률은 계산하지 않았다.",
    }


VERDICT = REPO_ROOT / "reports" / "research" / "elliott" / "93" / "verdict_b.json"
EXPECTED_N = (407, 2224)
"""관문 계산(``power``, 2026-10-03)의 독립 사건·대조 수 — 판정 때 그대로 나와야 한다."""
COST = 0.0019
"""판정표 "차이 > 비용": 무기한 왕복 (편도 0.095% × 2). 참고 줄 현물 왕복 0.30%."""


LOOKBACKS = (20, 60, 120)
"""B2 의 일봉 추세 — T/v2 와 같은 20·60·120 일 수익률 부호 평균."""
ALPHA_B2 = 0.025
MDE_GATE_B2 = 0.01


def _real_close(fake_close_ms: int) -> int:
    return (fake_close_ms + 1) // SCALE - 1


def _trend_lookup() -> tuple[list[int], list[Decimal]]:
    days = _aggregate(_vision_minutes(SPOT_BTC_DIR, "BTCUSDT"), Timeframe.D1.ms)
    return [d.close_ms for d in days], [d.close for d in days]


def _trend_at(lookup: tuple[list[int], list[Decimal]], real_ms: int) -> Decimal | None:
    """``real_ms`` 전에 마감된 일봉만으로 본 추세 (인과적)."""
    close_ms, closes = lookup
    k = bisect.bisect_left(close_ms, real_ms)
    return trend_direction(closes[:k], LOOKBACKS)


def _welch(r1: Sequence[float], r2: Sequence[float]) -> dict[str, object]:
    def mean_var(xs: Sequence[float]) -> tuple[float, float]:
        m = sum(xs) / len(xs)
        return m, sum((x - m) ** 2 for x in xs) / (len(xs) - 1)

    m1, v1 = mean_var(r1)
    m2, v2 = mean_var(r2)
    se = math.sqrt(v1 / len(r1) + v2 / len(r2))
    t_stat = (m1 - m2) / se
    df = (v1 / len(r1) + v2 / len(r2)) ** 2 / (
        (v1 / len(r1)) ** 2 / (len(r1) - 1) + (v2 / len(r2)) ** 2 / (len(r2) - 1)
    )
    return {
        "n1": len(r1),
        "n2": len(r2),
        "mean_event": round(m1, 6),
        "mean_control": round(m2, 6),
        "diff": round(m1 - m2, 6),
        "welch_t": round(t_stat, 3),
        "welch_df": round(df, 1),
        "p_one_sided_normal_approx": round(1 - NormalDist().cdf(t_stat), 5),
    }


def verdict() -> dict[str, object]:
    """문서 93 B §3 — 사용자 승인 뒤 **한 번만**. 판정 파일이 있으면 거부한다."""
    if VERDICT.exists():
        raise SystemExit(f"이미 판정했다: {VERDICT} — 같은 자료로 다시 판정하지 않는다")
    _, ev, co, close_at, _ = _collect()
    if (len(ev), len(co)) != EXPECTED_N:
        raise SystemExit(f"사건·대조 수가 관문 계산과 다르다: {(len(ev), len(co))} != {EXPECTED_N}")

    def outcome(r: TriggerResult, bars: int) -> float | None:
        later = close_at.get(r.close_ms + bars * H4)
        return None if later is None else -r.direction * math.log(later / close_at[r.close_ms])

    def values(samples: Sequence[TriggerResult], bars: int) -> list[float]:
        return [v for v in (outcome(r, bars) for r in samples) if v is not None]

    main = _welch(values(ev, HORIZON), values(co, HORIZON))
    diff = float(str(main["diff"]))
    p = float(str(main["p_one_sided_normal_approx"]))

    def by(samples: Sequence[TriggerResult], key: str) -> dict[str, list[float]]:
        groups: dict[str, list[float]] = {}
        for r in samples:
            v = outcome(r, HORIZON)
            if v is None:
                continue
            label = _year(r.close_ms) if key == "year" else str(r.direction)
            groups.setdefault(label, []).append(v)
        return {k: [len(g), round(sum(g) / len(g), 6)] for k, g in sorted(groups.items())}

    # B2 (93 §10): 되돌림(−d)이 일봉 추세와 같은 표본만. 수·MDE 먼저, 관문 통과 때만 수익률
    lookup = _trend_lookup()

    def aligned(r: TriggerResult) -> bool:
        trend = _trend_at(lookup, _real_close(r.close_ms))
        return trend is not None and -r.direction * trend > 0

    ev_w = [r for r in ev if aligned(r)]
    co_w = [r for r in co if aligned(r)]
    fwd = [
        math.log(close_at[c + HORIZON * H4] / v)
        for c, v in close_at.items()
        if c + HORIZON * H4 in close_at
    ]
    sd = pstdev(fwd)
    mde_w = 2.84 * sd * math.sqrt(1 / len(ev_w) + 1 / len(co_w)) if ev_w and co_w else math.inf
    b2: dict[str, object] = {
        "n1_aligned": len(ev_w),
        "n2_aligned": len(co_w),
        "mde": round(mde_w, 5),
        "gate_mde_le_1pct": mde_w <= MDE_GATE_B2,
    }
    if mde_w <= MDE_GATE_B2:
        test_w = _welch(values(ev_w, HORIZON), values(co_w, HORIZON))
        b2["test"] = test_w
        b2["significant_0_025"] = float(str(test_w["p_one_sided_normal_approx"])) < ALPHA_B2
        b2["diff_exceeds_cost_0_19pct"] = float(str(test_w["diff"])) > COST
    else:
        b2["result"] = "관문 미달 — 판정 불가, 수익률 미계산"

    record: dict[str, object] = {
        "main_30h": main,
        "b2_aligned_with_daily_trend": b2,
        "significant_0_05": p < 0.05,
        "diff_exceeds_cost_0_19pct": diff > COST,
        "diff_exceeds_spot_cost_0_30pct_reference": diff > 0.003,
        "secondary_descriptive": {
            "horizon_6h": _welch(values(ev, 6), values(co, 6)),
            "horizon_60h": _welch(values(ev, 60), values(co, 60)),
            "event_by_year": by(ev, "year"),
            "control_by_year": by(co, "year"),
            "event_by_direction": by(ev, "direction"),
            "control_by_direction": by(co, "direction"),
        },
        "note": "문서 93 B 1회 판정. p 는 정규 근사(자유도 큼). 보조는 기술 통계(판정 아님).",
    }
    VERDICT.parent.mkdir(parents=True, exist_ok=True)
    VERDICT.write_text(json.dumps(record, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return record


def diagnose() -> dict[str, object]:
    """b 실패 원인 나누기 — 진폭 절반만 · 거칠게만. 판정에 쓰지 않는다."""
    return {v: _synthetic_cell(v, keep_bars=False) for v in ("half_only", "coarse_only")}


def main(argv: list[str]) -> int:
    modes = {"synthetic": synthetic, "power": power, "diagnose": diagnose, "verdict": verdict}
    if not argv or argv[0] not in modes:
        raise SystemExit("usage: audit_ew93_hourly_degree.py synthetic|power|diagnose|verdict")
    sys.stdout.write(json.dumps(modes[argv[0]](), ensure_ascii=True, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
