"""문서 97 — Elliott 조정파(지그재그·플랫·삼각형) 끝 → 추세 재개, SOL 1h degree.

``synthetic``: 1h 설정(93 §8 방식)에서 진짜 조정 끝의 "10 중 6" 합의 비율과
충격파 끝의 거짓 비율 (두 seed).
``power``: SOL 발동마다 상위 10 의 조정·충격파 수를 파일로 남기고 사건·대조 수와 MDE.
수익률은 계산하지 않는다.
``verdict``: 사용자 승인 뒤 **한 번만** — 발동 파일(SHA 고정)과 SOL 종가만 읽는다.
"""

from __future__ import annotations

import bisect
import hashlib
import json
import math
import random
import sys
from collections import Counter
from collections.abc import Sequence
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
from scripts.audit_ew93_hourly_degree import (  # noqa: E402
    CONFIRM_SEED,
    DEV_SEED,
    SCALE,
    THRESHOLD_B,
    coarsen,
)
from scripts.audit_ew94_wave3_count import _bars, _dir  # noqa: E402
from scripts.audit_ew_edge_power import _aggregate, _chunks, _groups, _vision_minutes  # noqa: E402

from auto_trading.core.enums import Timeframe  # noqa: E402
from auto_trading.features.tsmom import trend_direction  # noqa: E402
from auto_trading.research.elliott import synthetic as synthetic_module  # noqa: E402
from auto_trading.research.elliott.minute_path import replay_minute_groups_ladder  # noqa: E402
from auto_trading.research.elliott.parser import Label  # noqa: E402
from auto_trading.research.elliott.scanner import (  # noqa: E402
    EW3E_LOOKBACK_BARS,
    ScenarioList,
    causal_triggers,
    scenario_lists,
)
from auto_trading.research.elliott.synthetic import build_path  # noqa: E402

SYMBOL = "SOLUSDT"
CORRECTIVE = frozenset({Label.ZIGZAG, Label.FLAT, Label.TRIANGLE})
TOP_N, NEED = 10, 6
HORIZON = 30
H4 = Timeframe.H4.ms
LOOKBACKS = (20, 60, 120)
GATE_SD_UNITS = 0.254
"""관문 MDE ≤ 0.254 × SD — 93 B 의 1.0%p ÷ BTC SD 3.94% 를 변동성 단위로."""
COST = 0.0019
OUT = REPO_ROOT / "reports" / "research" / "elliott" / "97"
TRIGGERS = OUT / "triggers_1h_SOLUSDT.jsonl"
TRIGGERS_SHA256 = ""
"""``power`` 뒤(수익률 보기 전) 문서 97 과 함께 고정한다."""
EXPECTED_N: tuple[int, int] | None = None
"""관문의 (독립 사건, 독립 대조) — 판정 때 그대로 나와야 한다."""
VERDICT = OUT / "verdict.json"


def _consensus(scen: ScenarioList) -> tuple[int, int, str]:
    """(상위 10 중 조정 모양 수, 충격파 수, 조정 중 다수 모양)."""
    top = [parse for parse, _ in scen.ranked[:TOP_N]]
    corrective = [p for p in top if p.pattern in CORRECTIVE]
    impulse = sum(1 for p in top if p.pattern is Label.IMPULSE)
    majority = (
        Counter(p.pattern.value for p in corrective).most_common(1)[0][0] if corrective else ""
    )
    return len(corrective), impulse, majority


# ---------------------------------------------------------------- 합성


def _synthetic_seed(seed: int) -> dict[str, object]:
    rng = random.Random(seed)
    out: Counter[str] = Counter()
    original_speed = synthetic_module.SPEED_PER_MINUTE
    synthetic_module.SPEED_PER_MINUTE = original_speed / 2  # 봉 수 유지 (93 §8)
    try:
        for _degree, (scale, parent_threshold) in DEGREES.items():
            for pattern in ("IMPULSE", "ZIGZAG", "FLAT", "TRIANGLE"):
                for noise, count in CELLS:
                    for _ in range(count):
                        path = build_path(rng, pattern, scale / 2, parent_threshold / 2, noise)
                        if noise == 0.0:
                            continue
                        minutes = coarsen(path.minutes)
                        four = path.four_hour
                        hours = hour_bars(minutes)
                        groups = (minutes[240 * i : 240 * (i + 1)] for i in range(len(four)))
                        (replay,) = replay_minute_groups_ladder(
                            four, groups, four[-1].close_ms, (THRESHOLD_B,)
                        )
                        triggers = causal_triggers(replay, hours, four, all_new_pivots=True)
                        truth_minute = path.vertex_minutes[-1]
                        truth = (
                            path.vertex_kinds[-1],
                            Timeframe.H1.open_ms_of(path.minutes[truth_minute].open_ms),
                        )
                        hit = next(
                            (
                                t
                                for t in triggers
                                if (t.segment[-1].kind, t.segment[-1].hour_open_ms) == truth
                            ),
                            None,
                        )
                        out[f"{pattern}|paths"] += 1
                        if hit is None:
                            out[f"{pattern}|no_trigger_at_true_end"] += 1
                            continue
                        (scen,) = scenario_lists(
                            [hit], hours, lookback_bars=EW3E_LOOKBACK_BARS, ew4=True, nested=True
                        )
                        corrective, _, _ = _consensus(scen)
                        if corrective >= NEED:
                            out[f"{pattern}|correction_consensus"] += 1
    finally:
        synthetic_module.SPEED_PER_MINUTE = original_speed
    corr_paths = sum(out[f"{p}|paths"] for p in ("ZIGZAG", "FLAT", "TRIANGLE"))
    corr_hits = sum(out[f"{p}|correction_consensus"] for p in ("ZIGZAG", "FLAT", "TRIANGLE"))
    true_rate = corr_hits / max(1, corr_paths)
    false_rate = out["IMPULSE|correction_consensus"] / max(1, out["IMPULSE|paths"])
    return {
        "counts": dict(sorted(out.items())),
        "corrective_paths_consensus_rate": round(true_rate, 4),
        "impulse_paths_false_rate": round(false_rate, 4),
        "check_pass": true_rate >= 0.5 and false_rate <= 0.3,
    }


def synthetic() -> dict[str, object]:
    dev = _synthetic_seed(DEV_SEED)
    confirm = _synthetic_seed(CONFIRM_SEED)
    return {
        "dev_seed": dev,
        "confirm_seed": confirm,
        "check_pass": bool(dev["check_pass"]) and bool(confirm["check_pass"]),
        "note": "문서 97 §4 합성 점검 — 두 seed 모두 통과해야 시장 단계.",
    }


# ---------------------------------------------------------------- 시장


def _daily() -> tuple[list[int], list[Decimal]]:
    days = _aggregate(_vision_minutes(_dir(SYMBOL), SYMBOL), Timeframe.D1.ms)
    return [d.close_ms for d in days], [d.close for d in days]


def _trend(lookup: tuple[list[int], list[Decimal]], real_ms: int) -> Decimal | None:
    close_ms, closes = lookup
    return trend_direction(closes[: bisect.bisect_left(close_ms, real_ms)], LOOKBACKS)


def _real(fake_close_ms: int) -> int:
    return (fake_close_ms + 1) // SCALE - 1


def _thin(rows: Sequence[dict[str, object]]) -> list[dict[str, object]]:
    kept: list[dict[str, object]] = []
    last = -(10**18)
    for r in sorted(rows, key=lambda x: int(str(x["close_ms"]))):
        c = int(str(r["close_ms"]))
        if c - last >= HORIZON * H4:
            kept.append(r)
            last = c
    return kept


def _split(
    rows: Sequence[dict[str, object]], close_at: dict[int, float]
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """(독립 사건, 독립 대조) — 재개 방향이 일봉 추세와 같은 발동만."""
    usable = [
        r
        for r in rows
        if r["trend"] is not None
        and -int(str(r["d"])) * float(str(r["trend"])) > 0
        and int(str(r["close_ms"])) + HORIZON * H4 in close_at
    ]
    events = [r for r in usable if int(str(r["corrective"])) >= NEED]
    controls = [r for r in usable if int(str(r["corrective"])) < NEED]
    return _thin(events), _thin(controls)


def _four() -> tuple:  # type: ignore[type-arg]
    four, hours, minutes, threshold = _bars("1h", SYMBOL)
    return four, hours, minutes, threshold


def power() -> dict[str, object]:
    four, hours, minutes, threshold = _four()
    lookup = _daily()
    rows: list[dict[str, object]] = []
    for chunk in _chunks(four):
        (replay,) = replay_minute_groups_ladder(
            chunk, _groups(minutes, chunk), chunk[-1].close_ms, (threshold,)
        )
        chunk_hours = [h for h in hours if chunk[0].open_ms <= h.open_ms <= chunk[-1].close_ms]
        triggers = causal_triggers(replay, chunk_hours, chunk, all_new_pivots=True)
        lists = scenario_lists(
            triggers, chunk_hours, lookback_bars=EW3E_LOOKBACK_BARS, ew4=True, nested=True
        )
        for trig, scen in zip(triggers, lists, strict=True):
            corrective, impulse, majority = _consensus(scen)
            trend = _trend(lookup, _real(trig.close_ms))
            rows.append(
                {
                    "close_ms": trig.close_ms,
                    "real_close_ms": _real(trig.close_ms),
                    "d": trig.direction,
                    "corrective": corrective,
                    "impulse": impulse,
                    "majority": majority,
                    "trend": None if trend is None else str(trend),
                }
            )
    OUT.mkdir(parents=True, exist_ok=True)
    with TRIGGERS.open("w", encoding="utf-8", newline="\n") as f:
        for r in rows:
            f.write(json.dumps(r, sort_keys=True) + "\n")
    close_at = {bar.close_ms: float(bar.close) for bar in four}
    ev, co = _split(rows, close_at)
    fwd = [
        math.log(close_at[c + HORIZON * H4] / v)
        for c, v in close_at.items()
        if c + HORIZON * H4 in close_at
    ]
    sd = pstdev(fwd)
    mde = 2.84 * sd * math.sqrt(1 / len(ev) + 1 / len(co)) if ev and co else math.inf

    def year(r: dict[str, object]) -> str:
        return datetime.fromtimestamp(int(str(r["real_close_ms"])) / 1000, UTC).strftime("%Y")

    return {
        "real_hour_bars": len(four),
        "chunks": len(_chunks(four)),
        "triggers": len(rows),
        "correction_consensus_all": sum(1 for r in rows if int(str(r["corrective"])) >= NEED),
        "n1_events_independent": len(ev),
        "n2_controls_independent": len(co),
        "events_by_year": dict(sorted(Counter(year(r) for r in ev).items())),
        "events_by_majority_shape": dict(sorted(Counter(str(r["majority"]) for r in ev).items())),
        "events_by_direction": dict(sorted(Counter(str(r["d"]) for r in ev).items())),
        "sd_forward_30h_log_return": round(sd, 5),
        "mde": round(mde, 5),
        "gate_threshold": round(GATE_SD_UNITS * sd, 5),
        "gate_pass": mde <= GATE_SD_UNITS * sd,
        "triggers_file_sha256": hashlib.sha256(TRIGGERS.read_bytes()).hexdigest(),
        "note": "문서 97 §5. 사건 뒤 수익률은 계산하지 않았다.",
    }


def _welch(r1: Sequence[float], r2: Sequence[float]) -> dict[str, object]:
    def mean_var(xs: Sequence[float]) -> tuple[float, float]:
        m = sum(xs) / len(xs)
        return m, sum((x - m) ** 2 for x in xs) / (len(xs) - 1)

    m1, v1 = mean_var(r1)
    m2, v2 = mean_var(r2)
    se = math.sqrt(v1 / len(r1) + v2 / len(r2))
    t = (m1 - m2) / se
    return {
        "n1": len(r1),
        "n2": len(r2),
        "mean_event": round(m1, 6),
        "mean_control": round(m2, 6),
        "diff": round(m1 - m2, 6),
        "welch_t": round(t, 3),
        "p_one_sided_normal_approx": round(1 - NormalDist().cdf(t), 5),
    }


def verdict() -> dict[str, object]:
    if VERDICT.exists():
        raise SystemExit(f"이미 판정했다: {VERDICT}")
    if not TRIGGERS_SHA256 or EXPECTED_N is None:
        raise SystemExit("발동 파일 해시·관문 수가 고정되지 않았다")
    raw = TRIGGERS.read_bytes()
    if hashlib.sha256(raw).hexdigest() != TRIGGERS_SHA256:
        raise SystemExit("발동 파일이 관문 때와 다르다")
    rows = [json.loads(line) for line in raw.decode("utf-8").splitlines()]
    four, _, _, _ = _four()
    close_at = {bar.close_ms: float(bar.close) for bar in four}
    ev, co = _split(rows, close_at)
    if (len(ev), len(co)) != EXPECTED_N:
        raise SystemExit(f"사건·대조 수가 관문과 다르다: {(len(ev), len(co))} != {EXPECTED_N}")

    def r(row: dict[str, object], bars: int) -> float | None:
        c = int(str(row["close_ms"]))
        later = close_at.get(c + bars * H4)
        return None if later is None else -int(str(row["d"])) * math.log(later / close_at[c])

    def values(rows_: Sequence[dict[str, object]], bars: int) -> list[float]:
        return [v for v in (r(x, bars) for x in rows_) if v is not None]

    main = _welch(values(ev, HORIZON), values(co, HORIZON))
    diff = float(str(main["diff"]))
    p = float(str(main["p_one_sided_normal_approx"]))

    def by(key: str) -> dict[str, list[float]]:
        groups: dict[str, list[float]] = {}
        for x in ev:
            v = r(x, HORIZON)
            if v is None:
                continue
            if key == "year":
                label = datetime.fromtimestamp(int(str(x["real_close_ms"])) / 1000, UTC).strftime(
                    "%Y"
                )
            else:
                label = str(x[key])
            groups.setdefault(label, []).append(v)
        return {k: [len(g), round(sum(g) / len(g), 6)] for k, g in sorted(groups.items())}

    record: dict[str, object] = {
        "main_30h": main,
        "significant_0_05": p < 0.05,
        "diff_exceeds_cost_0_19pct": diff > COST,
        "secondary_descriptive": {
            "horizon_6h": _welch(values(ev, 6), values(co, 6)),
            "horizon_60h": _welch(values(ev, 60), values(co, 60)),
            "event_by_majority_shape": by("majority"),
            "event_by_direction": by("d"),
            "event_by_year": by("year"),
        },
        "note": "문서 97 1회 판정. p 는 정규 근사. 보조는 기술 통계(판정 아님).",
    }
    VERDICT.write_text(json.dumps(record, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return record


def main(argv: list[str]) -> int:
    modes = {"synthetic": synthetic, "power": power, "verdict": verdict}
    if len(argv) != 1 or argv[0] not in modes:
        raise SystemExit("usage: audit_ew97_correction_resume.py synthetic|power|verdict")
    sys.stdout.write(json.dumps(modes[argv[0]](), ensure_ascii=True, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
