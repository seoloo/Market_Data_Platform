"""문서 98 — Elliott 삼각형 돌파(H-T) · 조정 끝 정의 1(H-C1), SOL 1h degree.

``synthetic``: 새 seed 20261003·20261004 에서 두 정의의 맞게·잘못 잡는 비율 (자료 필요 없음).
``power``: SOL 발동마다 모양 정보를 파일로 남기고 가설별 사건·대조 수와 MDE (수익률 미계산).
``verdict H-T|H-C1``: 사용자 승인 뒤 가설마다 **한 번만** — 발동 파일(SHA 고정)과 SOL 종가만 읽는다.
"""

from __future__ import annotations

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
from statistics import pstdev

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from scripts.audit_ew3c_hourly import CELLS, DEGREES, hour_bars  # noqa: E402
from scripts.audit_ew93_hourly_degree import SCALE, THRESHOLD_B, coarsen  # noqa: E402
from scripts.audit_ew94_wave3_count import _bars, _dir  # noqa: E402
from scripts.audit_ew97_correction_resume import _real, _thin, _trend, _welch  # noqa: E402
from scripts.audit_ew_edge_power import (  # noqa: E402
    _aggregate,
    _chunks,
    _groups,
    _vision_minutes,
)

from auto_trading.core.enums import Timeframe  # noqa: E402
from auto_trading.research.elliott import synthetic as synthetic_module  # noqa: E402
from auto_trading.research.elliott.minute_path import replay_minute_groups_ladder  # noqa: E402
from auto_trading.research.elliott.parser import Label  # noqa: E402
from auto_trading.research.elliott.scanner import (  # noqa: E402
    EW3E_LOOKBACK_BARS,
    ScenarioList,
    Trigger,
    causal_triggers,
    scenario_lists,
)
from auto_trading.research.elliott.synthetic import build_path  # noqa: E402

SEEDS = (20261003, 20261004)
"""문서 98 §3 — 아무도 보지 않은 새 seed."""
CORRECTIVE = frozenset({Label.ZIGZAG, Label.FLAT, Label.TRIANGLE})
TOP_N, NEED = 10, 6
HORIZON = 30
H4 = Timeframe.H4.ms
GATE_SD_UNITS = 0.254
ALPHA = 0.025
COST = 0.0019
OUT = REPO_ROOT / "reports" / "research" / "elliott" / "98"
SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
"""문서 98 §8 — H-T 를 세 자산 합산으로."""
ASSET_SHA256: dict[str, str] = {}
"""자산 → 발동 파일 SHA-256. ``power-asset`` 뒤(수익률 보기 전) 문서 98 과 함께 고정한다."""
EXPECTED_POOLED: tuple[int, int] | None = None
"""합친 관문의 (독립 사건, 독립 대조) — 판정 때 그대로 나와야 한다."""


def _shape(scen: ScenarioList, trigger: Trigger) -> dict[str, object]:
    """상위 10 의 모양 수 · 1 등(동점 전체) 모양 · 1 등 삼각형의 A 다리 폭(%)과 지속(실제 분)."""
    top = [parse for parse, _ in scen.ranked[:TOP_N]]
    first_key = scen.ranked[0][1] if scen.ranked else None
    rank1 = [parse for parse, key in scen.ranked if key == first_key]
    patterns = sorted({p.pattern.value for p in rank1})
    tri = next((p for p in rank1 if p.pattern is Label.TRIANGLE), None)
    tri_a_pct = tri_minutes = None
    if tri is not None:
        seg = trigger.segment
        a0, a1 = seg[tri.bounds[0]], seg[tri.bounds[1]]
        tri_a_pct = abs(float(a1.price) / float(a0.price) - 1)
        span = seg[tri.bounds[-1]].hour_open_ms - seg[tri.bounds[0]].hour_open_ms
        tri_minutes = span / SCALE / 60_000
    return {
        "corrective": sum(1 for p in top if p.pattern in CORRECTIVE),
        "impulse": sum(1 for p in top if p.pattern is Label.IMPULSE),
        "triangle": sum(1 for p in top if p.pattern is Label.TRIANGLE),
        "rank1": patterns,
        "tri_a_pct": tri_a_pct,
        "tri_minutes": tri_minutes,
    }


def is_ht(row: dict[str, object]) -> bool:
    return row["rank1"] == [Label.TRIANGLE.value]


def is_hc1(row: dict[str, object]) -> bool:
    rank1 = row["rank1"]
    assert isinstance(rank1, list)
    corrective_values = {c.value for c in CORRECTIVE}
    return (
        int(str(row["corrective"])) >= NEED
        and bool(rank1)
        and all(p in corrective_values for p in rank1)
    )


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
                        truth = (
                            path.vertex_kinds[-1],
                            Timeframe.H1.open_ms_of(path.minutes[path.vertex_minutes[-1]].open_ms),
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
                        row = _shape(scen, hit)
                        if is_ht(row):
                            out[f"{pattern}|ht"] += 1
                        if is_hc1(row):
                            out[f"{pattern}|hc1"] += 1
    finally:
        synthetic_module.SPEED_PER_MINUTE = original_speed

    def rate(key: str, patterns: Sequence[str]) -> float:
        paths = sum(out[f"{p}|paths"] for p in patterns)
        return round(sum(out[f"{p}|{key}"] for p in patterns) / max(1, paths), 4)

    ht_true = rate("ht", ["TRIANGLE"])
    ht_false = rate("ht", ["IMPULSE", "ZIGZAG", "FLAT"])
    hc1_true = rate("hc1", ["ZIGZAG", "FLAT", "TRIANGLE"])
    hc1_false = rate("hc1", ["IMPULSE"])
    return {
        "counts": dict(sorted(out.items())),
        "ht_true_rate": ht_true,
        "ht_false_rate": ht_false,
        "ht_pass": ht_true >= 0.5 and ht_false <= 0.3,
        "hc1_true_rate": hc1_true,
        "hc1_false_rate": hc1_false,
        "hc1_pass": hc1_true >= 0.5 and hc1_false <= 0.3,
    }


def synthetic() -> dict[str, object]:
    seeds = {str(s): _synthetic_seed(s) for s in SEEDS}
    return {
        "seeds": seeds,
        "ht_pass_both": all(bool(v["ht_pass"]) for v in seeds.values()),
        "hc1_pass_both": all(bool(v["hc1_pass"]) for v in seeds.values()),
        "note": "문서 98 §3 — 가설마다 두 seed 모두 통과해야 시장 단계.",
    }


# ---------------------------------------------------------------- 시장 (문서 98 §8: H-T, 세 자산)


def _triggers_path(symbol: str) -> Path:
    return OUT / f"triggers_1h_{symbol}.jsonl"


def _asset_daily(symbol: str) -> tuple[list[int], list[Decimal]]:
    days = _aggregate(_vision_minutes(_dir(symbol), symbol), Timeframe.D1.ms)
    return [d.close_ms for d in days], [d.close for d in days]


def _closes(symbol: str) -> dict[int, float]:
    """판독기가 본 "4h"(= 실제 1h, 시간 ×4, 거래량 0 뺌) 마감 → 종가."""
    real_hours = _aggregate(_vision_minutes(_dir(symbol), symbol), Timeframe.H1.ms)
    return {(h.open_ms * SCALE) + H4 - 1: float(h.close) for h in real_hours if h.volume > 0}


def power_asset(symbol: str) -> dict[str, object]:
    """자산 하나의 발동마다 모양 정보를 파일로 남긴다. 수익률은 계산하지 않는다."""
    four, hours, minutes, threshold = _bars("1h", symbol)
    lookup = _asset_daily(symbol)
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
            trend = _trend(lookup, _real(trig.close_ms))
            rows.append(
                {
                    "symbol": symbol,
                    "close_ms": trig.close_ms,
                    "real_close_ms": _real(trig.close_ms),
                    "d": trig.direction,
                    "trend": None if trend is None else str(trend),
                    **_shape(scen, trig),
                }
            )
    OUT.mkdir(parents=True, exist_ok=True)
    path = _triggers_path(symbol)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        for r in rows:
            f.write(json.dumps(r, sort_keys=True) + "\n")
    mix = Counter("|".join(str(x) for x in r["rank1"]) or "NONE" for r in rows)  # type: ignore[attr-defined]
    return {
        "symbol": symbol,
        "real_hour_bars": len(four),
        "triggers": len(rows),
        "ht_all": sum(1 for r in rows if is_ht(r)),
        "rank1_mix": dict(sorted(mix.items())),
        "triggers_file_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "note": "문서 98 §8 자산별 발동. 수익률은 계산하지 않았다.",
    }


def _load(symbol: str) -> list[dict[str, object]]:
    raw = _triggers_path(symbol).read_bytes()
    want = ASSET_SHA256.get(symbol, "")
    if not want or hashlib.sha256(raw).hexdigest() != want:
        raise SystemExit(f"{symbol} 발동 파일 해시가 고정값과 다르다 (또는 고정 전)")
    return [json.loads(line) for line in raw.decode("utf-8").splitlines()]


def _pooled(
    rows: Sequence[dict[str, object]], closes: dict[str, dict[int, float]]
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """세 자산을 한 시간축에서 겹침 제거 (34 §9). 사건·대조 각각."""
    usable = [r for r in rows if int(str(r["close_ms"])) + HORIZON * H4 in closes[str(r["symbol"])]]
    return _thin([r for r in usable if is_ht(r)]), _thin([r for r in usable if not is_ht(r)])


def gate() -> dict[str, object]:
    rows = [r for s in SYMBOLS for r in _load(s)]
    closes = {s: _closes(s) for s in SYMBOLS}
    ev, co = _pooled(rows, closes)
    fwd = [
        math.log(c[t + HORIZON * H4] / v)
        for c in closes.values()
        for t, v in c.items()
        if t + HORIZON * H4 in c
    ]
    sd = pstdev(fwd)
    mde = 2.84 * sd * math.sqrt(1 / len(ev) + 1 / len(co)) if ev and co else math.inf

    def year(r: dict[str, object]) -> str:
        return datetime.fromtimestamp(int(str(r["real_close_ms"])) / 1000, UTC).strftime("%Y")

    return {
        "triggers_per_asset": {s: sum(1 for r in rows if r["symbol"] == s) for s in SYMBOLS},
        "ht_per_asset": {s: sum(1 for r in rows if r["symbol"] == s and is_ht(r)) for s in SYMBOLS},
        "n1_events_independent": len(ev),
        "n2_controls_independent": len(co),
        "events_by_asset": dict(sorted(Counter(str(r["symbol"]) for r in ev).items())),
        "events_by_year": dict(sorted(Counter(year(r) for r in ev).items())),
        "events_by_direction": dict(sorted(Counter(str(r["d"]) for r in ev).items())),
        "sd_pooled_30h": round(sd, 5),
        "mde": round(mde, 5),
        "gate_threshold": round(GATE_SD_UNITS * sd, 5),
        "gate_pass": mde <= GATE_SD_UNITS * sd,
        "note": "문서 98 §8 합친 관문. 사건 뒤 수익률은 계산하지 않았다.",
    }


def verdict_pooled() -> dict[str, object]:
    path = OUT / "verdict_H-T.json"
    if path.exists():
        raise SystemExit(f"이미 판정했다: {path}")
    if EXPECTED_POOLED is None:
        raise SystemExit("합친 관문 수가 고정되지 않았다")
    rows = [r for s in SYMBOLS for r in _load(s)]
    closes = {s: _closes(s) for s in SYMBOLS}
    ev, co = _pooled(rows, closes)
    if (len(ev), len(co)) != EXPECTED_POOLED:
        raise SystemExit(f"사건·대조 수가 관문과 다르다: {(len(ev), len(co))}")

    def r(row: dict[str, object], bars: int) -> float | None:
        c = closes[str(row["symbol"])]
        t = int(str(row["close_ms"]))
        later = c.get(t + bars * H4)
        return None if later is None else -int(str(row["d"])) * math.log(later / c[t])

    def values(rows_: Sequence[dict[str, object]], bars: int) -> list[float]:
        return [v for v in (r(x, bars) for x in rows_) if v is not None]

    main = _welch(values(ev, HORIZON), values(co, HORIZON))
    diff = float(str(main["diff"]))
    p = float(str(main["p_one_sided_normal_approx"]))
    per_asset: dict[str, object] = {}
    for s in SYMBOLS:
        e = [x for x in ev if x["symbol"] == s]
        c = [x for x in co if x["symbol"] == s]
        if len(e) > 1 and len(c) > 1:
            per_asset[s] = _welch(values(e, HORIZON), values(c, HORIZON))
    reached = [
        v >= float(str(x["tri_a_pct"]))
        for x in ev
        if x["tri_a_pct"] is not None and (v := r(x, HORIZON)) is not None
    ]
    record: dict[str, object] = {
        "hypothesis": "H-T pooled BTC+ETH+SOL",
        "main_30h": main,
        "significant_0_025": p < ALPHA,
        "diff_exceeds_cost_0_19pct": diff > COST,
        "secondary_descriptive": {
            "per_asset_30h": per_asset,
            "horizon_6h": _welch(values(ev, 6), values(co, 6)),
            "horizon_60h": _welch(values(ev, 60), values(co, 60)),
            "thrust_reached_widest_leg_30h": round(sum(reached) / len(reached), 4)
            if reached
            else None,
        },
        "note": "문서 98 §8 1회 판정. p 는 정규 근사. 보조는 기술 통계(판정 아님).",
    }
    path.write_text(json.dumps(record, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return record


def main(argv: list[str]) -> int:
    if argv == ["synthetic"]:
        result = synthetic()
    elif len(argv) == 2 and argv[0] == "power-asset" and argv[1] in SYMBOLS:
        result = power_asset(argv[1])
    elif argv == ["gate"]:
        result = gate()
    elif argv == ["verdict-pooled"]:
        result = verdict_pooled()
    else:
        raise SystemExit("usage: audit_ew98 synthetic|power-asset SYMBOL|gate|verdict-pooled")
    sys.stdout.write(json.dumps(result, ensure_ascii=True, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
