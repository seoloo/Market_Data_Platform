"""문서 103 — 시장형 합성 자료: 실제 1m 흔들림 배율 k 를 시장 통계(후보 없는 발동 63.7%)에 맞춘다.

사용::

    python scripts/audit_ew103_market_synthetic.py returns     # BTCUSDT 1m 로그수익 캐시
    python scripts/audit_ew103_market_synthetic.py calibrate   # 개발 seed 20261009, k 격자

수익 시험이 아니다. 시장 자료는 흔들림 모양(1m 수익)만 쓴다.
"""

from __future__ import annotations

import json
import math
import random
import sys
from array import array
from collections.abc import Sequence
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
for _p in (REPO_ROOT, REPO_ROOT / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from scripts.audit_div101_15m import _minutes  # noqa: E402
from scripts.audit_ew3c_hourly import DEGREES, hour_bars  # noqa: E402
from scripts.audit_ew93_hourly_degree import THRESHOLD_B, coarsen  # noqa: E402
from scripts.audit_ew100_synthetic import CLASS, PATTERNS  # noqa: E402

from auto_trading.core.enums import Timeframe  # noqa: E402
from auto_trading.research.elliott import synthetic as synthetic_module  # noqa: E402
from auto_trading.research.elliott.minute_path import replay_minute_groups_ladder  # noqa: E402
from auto_trading.research.elliott.scanner import (  # noqa: E402
    EW3E_LOOKBACK_BARS,
    ScenarioList,
    causal_triggers,
    scenario_lists,
)
from auto_trading.research.elliott.synthetic import MarketNoise, build_path  # noqa: E402

RETURNS = REPO_ROOT / "data" / "external" / "derived" / "btcusdt_1m_logret.bin"
OUT = REPO_ROOT / "reports" / "research" / "elliott" / "103"
DEV_SEED = 20261009
K_GRID = (0.0, 0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0)
PATHS_PER_DEGREE = 4
NOISE = 0.001
MARKET_EMPTY = 0.6374
MARKET_ANY_FULL = 0.0197
STEP_1H = 4


def build_returns() -> dict[str, object]:
    out = array("d")
    prev_ms, prev_close = None, None
    for open_ms, _o, _h, _l, close in _minutes("BTCUSDT"):
        if prev_ms is not None and open_ms == prev_ms + 60_000 and prev_close and close > 0:
            out.append(math.log(close / prev_close))
        prev_ms, prev_close = open_ms, close
    RETURNS.parent.mkdir(parents=True, exist_ok=True)
    with RETURNS.open("wb") as fh:
        out.tofile(fh)
    sd = math.sqrt(sum(x * x for x in out) / len(out))
    return {"returns": len(out), "rms": round(sd, 6)}


def load_returns() -> array[float]:
    data = array("d")
    with RETURNS.open("rb") as fh:
        data.frombytes(fh.read())
    return data


def _features(scen: ScenarioList) -> dict[str, list[float]]:
    """모양(채점 갈래)마다 (최고 검증 비율, 그 검증 비율에서 최소 거리)."""
    best: dict[str, list[float]] = {}
    for parse, key in scen.ranked:
        name = CLASS.get(parse.pattern.value, parse.pattern.value)
        v, dist = -key.neg_verified, key.distance
        cur = best.get(name)
        if cur is None or v > cur[0] or (v == cur[0] and dist < cur[1]):
            best[name] = [round(v, 4), round(dist, 4)]
    return best


def _top(scen: ScenarioList) -> str:
    if not scen.ranked:
        return "EMPTY"
    first = scen.ranked[0][1]
    names = {CLASS.get(p.pattern.value, p.pattern.value) for p, key in scen.ranked if key == first}
    return names.pop() if len(names) == 1 else "TIE"


def evaluate_path(path: synthetic_module.SyntheticPath) -> dict[str, object]:
    """1h 설정(93 §8)으로 경로 안 모든 발동을 읽는다."""
    minutes = coarsen(path.minutes)
    four = path.four_hour
    hours = hour_bars(minutes)
    groups = (minutes[240 * i : 240 * (i + 1)] for i in range(len(four)))
    (replay,) = replay_minute_groups_ladder(four, groups, four[-1].close_ms, (THRESHOLD_B,))
    triggers = causal_triggers(replay, hours, four, all_new_pivots=True)
    lists = scenario_lists(
        triggers, hours, lookback_bars=EW3E_LOOKBACK_BARS, ew4=True, nested=True, ew5=True
    )
    truth = (
        path.vertex_kinds[-1],
        Timeframe.H1.open_ms_of(path.minutes[path.vertex_minutes[-1]].open_ms),
    )
    empty = sum(1 for s in lists if not s.ranked)
    any_full = sum(1 for s in lists if any(k.neg_verified == -1 for _, k in s.ranked))
    end = next(
        (
            s
            for t, s in zip(triggers, lists, strict=True)
            if (t.segment[-1].kind, t.segment[-1].hour_open_ms) == truth
        ),
        None,
    )
    return {
        "triggers": len(lists),
        "empty": empty,
        "any_full": any_full,
        "end_found": end is not None,
        "end_top": None if end is None else _top(end),
        "end_features": None if end is None else _features(end),
    }


def run_grid(
    seed: int, ks: Sequence[float], returns: Sequence[float], depth: int = 2
) -> dict[str, object]:
    original = synthetic_module.SPEED_PER_MINUTE
    synthetic_module.SPEED_PER_MINUTE = original / 2  # 봉 수 유지 (93 §8)
    out: dict[str, object] = {}
    try:
        for k in ks:
            market = MarketNoise(returns, k, STEP_1H, depth)
            rows: list[dict[str, object]] = []
            for pattern in PATTERNS:
                rng = random.Random(f"{seed}-mkt-{k}-{depth}-{pattern}")
                for _degree, (scale, threshold) in DEGREES.items():
                    for _ in range(PATHS_PER_DEGREE):
                        path = build_path(rng, pattern, scale / 2, threshold / 2, NOISE, market)
                        rows.append({"pattern": CLASS.get(pattern, pattern), **evaluate_path(path)})
            trig = sum(int(str(r["triggers"])) for r in rows)
            empty = sum(int(str(r["empty"])) for r in rows) / max(1, trig)
            any_full = sum(int(str(r["any_full"])) for r in rows) / max(1, trig)
            out[f"k={k}|depth={depth}"] = {
                "triggers": trig,
                "empty_frac": round(empty, 4),
                "any_full_frac": round(any_full, 4),
                "end_found": round(sum(bool(r["end_found"]) for r in rows) / len(rows), 4),
                "rows": rows,
            }
            sys.stderr.write(f"k={k} depth={depth} empty={empty:.4f} any_full={any_full:.4f}\n")
            sys.stderr.flush()
    finally:
        synthetic_module.SPEED_PER_MINUTE = original
    return out


def calibrate() -> dict[str, object]:
    returns = load_returns()
    grid = run_grid(DEV_SEED, K_GRID, returns)

    def gap(key: str) -> float:
        cell = grid[key]
        assert isinstance(cell, dict)
        return abs(float(cell["empty_frac"]) - MARKET_EMPTY)

    best = min(grid, key=gap)
    result: dict[str, object] = {
        "seed": DEV_SEED,
        "market_empty": MARKET_EMPTY,
        "market_any_full": MARKET_ANY_FULL,
        "grid": grid,
        "chosen": best,
        "chosen_gap": round(gap(best), 4),
    }
    if gap(best) > 0.10:
        for depth in (0, 1):
            extra = run_grid(DEV_SEED, K_GRID, returns, depth)
            grid.update(extra)
        best = min(grid, key=gap)
        result["chosen"] = best
        result["chosen_gap"] = round(gap(best), 4)
    return result


def main(argv: list[str]) -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    if argv == ["returns"]:
        result = build_returns()
    elif argv == ["calibrate"]:
        result = calibrate()
        (OUT / "calibrate.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
        result = {k: v for k, v in result.items() if k != "grid"} | {
            "summary": {
                key: {kk: vv for kk, vv in cell.items() if kk != "rows"}
                for key, cell in result["grid"].items()  # type: ignore[union-attr]
            }
        }
    else:
        raise SystemExit("usage: audit_ew103_market_synthetic.py returns | calibrate")
    sys.stdout.write(json.dumps(result, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
