"""BTCUSDT 무기한 포지셔닝 이벤트 연구 (docs/research/48) — FB·LQ.

사용 예::

    python -m scripts.screen_perp_events count        # 이벤트 빈도만 (수익 미계산)
    python -m scripts.screen_perp_events train        # 사전 등록 뒤 1회 — 2021 + 2023
    python -m scripts.screen_perp_events validation   # train 통과 가설만, 1회 — 2024
    python -m scripts.screen_perp_events oos          # validation 통과 가설만, 1회 — 2025~2026-08

train·validation·oos 는 작업 트리가 깨끗해야 하고, 이미 결과 파일이 있으면 거부한다.
DB 는 읽기 전용으로 연다.
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import statistics
import sys
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from statistics import NormalDist
from typing import Any

from scripts.run_a1_train import git_state

from auto_trading.backtest.perp_events import (
    BAR_MS,
    FB_DEFAULT,
    LQ_DEFAULT,
    Bars,
    EventTrade,
    PerpEvent,
    detect_fb,
    detect_lq,
    settle_trade,
)

DB = Path("data/trading.db")
OUTPUT = Path("reports/perp_events")
SYMBOL = "BTCUSDT"
COST_BASE = 0.00095
"""편도 판정 비용 (수수료 + 슬리피지, 42 §8)."""
COST_REFERENCE = 0.00023
"""참고 줄 전용 (42 §7)."""


def _ms(y: int, m: int, d: int) -> int:
    return int(datetime(y, m, d, tzinfo=UTC).timestamp() * 1000)


LOAD_FROM = _ms(2020, 12, 1)
LOAD_TO = _ms(2026, 9, 1)
SPLITS: dict[str, list[tuple[int, int]]] = {
    "train": [(_ms(2021, 1, 1), _ms(2022, 1, 1)), (_ms(2023, 1, 1), _ms(2024, 1, 1))],
    "validation": [(_ms(2024, 1, 1), _ms(2025, 1, 1))],
    "oos": [(_ms(2025, 1, 1), _ms(2026, 9, 1))],
}
TRAIN_YEARS = {"2021": SPLITS["train"][0], "2023": SPLITS["train"][1]}
HOLM_ALPHA = 0.05
MIN_EVENTS = 30


def load() -> tuple[Bars, list[tuple[int, float]]]:
    with sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True) as conn:
        rows = conn.execute(
            "SELECT open_ms, open, high, low, close FROM candles WHERE exchange = 'binance_usdm'"
            " AND symbol = ? AND timeframe = '5m' AND open_ms >= ? AND open_ms < ?"
            " ORDER BY open_ms",
            (SYMBOL, LOAD_FROM, LOAD_TO),
        ).fetchall()
        oi = dict(
            conn.execute(
                "SELECT at_ms, open_interest FROM perp_metrics WHERE exchange = 'binance_usdm'"
                " AND symbol = ? AND at_ms >= ? AND at_ms <= ? AND open_interest IS NOT NULL",
                (SYMBOL, LOAD_FROM, LOAD_TO),
            ).fetchall()
        )
        settlements = [
            (int(t), float(r))
            for t, r in conn.execute(
                "SELECT funding_ms, rate FROM funding_rates WHERE exchange = 'binance_usdm'"
                " AND symbol = ? AND funding_ms >= ? AND funding_ms < ? ORDER BY funding_ms",
                (SYMBOL, LOAD_FROM - 100 * 86_400_000, LOAD_TO),
            )
        ]
    open_ms = [int(r[0]) for r in rows]
    funding: list[float | None] = []
    k = -1
    for t in open_ms:
        close = t + BAR_MS
        while k + 1 < len(settlements) and settlements[k + 1][0] <= close:
            k += 1
        funding.append(settlements[k][1] if k >= 0 else None)
    bars = Bars(
        open_ms=open_ms,
        open=[float(r[1]) for r in rows],
        high=[float(r[2]) for r in rows],
        low=[float(r[3]) for r in rows],
        close=[float(r[4]) for r in rows],
        oi=[float(oi[t + BAR_MS]) if (t + BAR_MS) in oi else None for t in open_ms],
        funding=funding,
    )
    return bars, settlements


def _in(ms: int, spans: list[tuple[int, int]]) -> bool:
    return any(lo <= ms < hi for lo, hi in spans)


def _year(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y")


def events_all(bars: Bars, settlements: list[tuple[int, float]]) -> dict[str, list[PerpEvent]]:
    return {"FB": detect_fb(bars, settlements), "LQ": detect_lq(bars)}


def count() -> dict[str, Any]:
    bars, settlements = load()
    out: dict[str, Any] = {"note": "문서 48 §2 빈도 측정. 수익은 계산하지 않았다."}
    oi_years: dict[str, list[int]] = {}
    for t, v in zip(bars.open_ms, bars.oi, strict=True):
        oi_years.setdefault(_year(t), [0, 0])
        oi_years[_year(t)][0] += 1
        oi_years[_year(t)][1] += v is not None
    out["oi_coverage"] = {y: round(b / a, 3) for y, (a, b) in oi_years.items()}
    for name, evs in events_all(bars, settlements).items():
        by: dict[str, dict[str, int]] = {}
        for e in evs:
            y = _year(bars.open_ms[e.entry_index])
            by.setdefault(y, {"+1": 0, "-1": 0})
            by[y]["+1" if e.side == 1 else "-1"] += 1
        out[name] = {
            "by_year_side": by,
            "split_counts": {
                s: sum(_in(bars.open_ms[e.entry_index], spans) for e in evs)
                for s, spans in SPLITS.items()
            },
        }
    return out


def _stats(xs: list[float]) -> dict[str, Any]:
    n = len(xs)
    if n < 2:
        return {"n": n, "mean": statistics.fmean(xs) if xs else None}
    mean = statistics.fmean(xs)
    sd = statistics.stdev(xs)
    se = sd / math.sqrt(n)
    return {
        "n": n,
        "mean_bps": mean * 1e4,
        "sd_bps": sd * 1e4,
        "se_bps": se * 1e4,
        "t": mean / se if se > 0 else None,
        "win_rate": sum(x > 0 for x in xs) / n,
    }


def _p_value(t: float | None, *, two_sided: bool) -> float:
    if t is None:
        return 1.0
    tail = 1 - NormalDist().cdf(abs(t) if two_sided else t)
    return min(1.0, 2 * tail) if two_sided else tail


def _trades(
    bars: Bars,
    settlements: list[tuple[int, float]],
    events: list[PerpEvent],
    spans: list[tuple[int, int]],
    *,
    direction_of: Any,
    hold: int,
    cost: float,
    use_stop: bool,
) -> list[EventTrade]:
    out = []
    for e in events:
        if not _in(bars.open_ms[e.entry_index], spans):
            continue
        tr = settle_trade(
            bars,
            e,
            direction=direction_of(e),
            hold_bars=hold,
            cost_per_side=cost,
            settlements=settlements,
            use_stop=use_stop,
        )
        if tr is not None:
            out.append(tr)
    return out


def _describe(trades: list[EventTrade]) -> dict[str, Any]:
    net = [t.net for t in trades]
    return {
        "net": _stats(net),
        "gross": _stats([t.gross for t in trades]),
        "funding_mean_bps": statistics.fmean(t.funding for t in trades) * 1e4 if trades else None,
        "stopped": sum(t.stopped for t in trades),
        "by_direction": {
            d: _stats([t.net for t in trades if t.direction == int(d)]) for d in ("1", "-1")
        },
        "worst_net_bps": min(net) * 1e4 if net else None,
    }


def _strategy(trades: list[EventTrade]) -> dict[str, Any]:
    """1x 명목, 거래 순서대로 복리 — 전략 수준 보고 (PF·MDD)."""
    eq, peak, mdd = 1.0, 1.0, 0.0
    for t in sorted(trades, key=lambda x: x.entry_ms):
        eq *= 1 + t.net
        peak = max(peak, eq)
        mdd = max(mdd, 1 - eq / peak)
    wins = sum(t.net for t in trades if t.net > 0)
    losses = -sum(t.net for t in trades if t.net < 0)
    return {
        "compounded_return": eq - 1,
        "mdd": mdd,
        "profit_factor": wins / losses if losses > 0 else None,
    }


def _guard(stage: str, output: Path) -> tuple[Path, str] | None:
    path = output / f"{stage}.json"
    if path.exists():
        print(f"이미 결과가 있다: {path}. 사전 등록상 1회만 실행한다.")
        return None
    sha, dirty = git_state()
    if dirty:
        print("작업 트리에 커밋되지 않은 변경이 있다. 재현 가능한 코드로만 실행한다.")
        return None
    return path, sha


def _write(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(record, ensure_ascii=False, indent=1, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _fb_direction(e: PerpEvent) -> int:
    return e.side


def _run_hypothesis(
    bars: Bars,
    settlements: list[tuple[int, float]],
    events: list[PerpEvent],
    spans: list[tuple[int, int]],
    direction_of: Any,
    hold: int,
) -> dict[str, Any]:
    """주 규칙(구조 스톱 + 12시간 시간 청산, 기준 비용)과 참고 줄."""
    primary = _trades(
        bars,
        settlements,
        events,
        spans,
        direction_of=direction_of,
        hold=hold,
        cost=COST_BASE,
        use_stop=True,
    )
    out: dict[str, Any] = {"primary": _describe(primary), "strategy_1x": _strategy(primary)}
    out["by_year"] = {}
    for t in primary:
        out["by_year"].setdefault(_year(t.entry_ms), []).append(t.net)
    out["by_year"] = {y: _stats(v) for y, v in sorted(out["by_year"].items())}
    rep: dict[str, Any] = {}
    for label, h, cost, stop in (
        ("no_stop", hold, COST_BASE, False),
        ("cost_reference_0_023pct", hold, COST_REFERENCE, True),
        ("hold_4h", 48, COST_BASE, True),
        ("hold_24h", 288, COST_BASE, True),
    ):
        trades = _trades(
            bars,
            settlements,
            events,
            spans,
            direction_of=direction_of,
            hold=h,
            cost=cost,
            use_stop=stop,
        )
        rep[label] = {"net": _stats([t.net for t in trades]), **_strategy(trades)}
    out["reference_only"] = rep
    return out


def _status(crit: list[tuple[str, bool, Any]]) -> dict[str, Any]:
    return {
        "criteria": [{"name": a, "passed": b, "detail": c} for a, b, c in crit],
        "status": "PASS" if all(b for _, b, _ in crit) else "REJECT",
    }


def _print(stage: str, results: dict[str, Any], path: Path) -> None:
    for k, r in results.items():
        print(
            f"[{stage}] {k}: {r['status']}  "
            + "  ".join(f"{c['name']}={'통과' if c['passed'] else '실패'}" for c in r["criteria"])
        )
    print(f"기록: {path}")


def train(output: Path) -> int:
    """48 §4 T1~T3 (2021 + 2023)."""
    guard = _guard("train", output)
    if guard is None:
        return 3
    path, sha = guard
    bars, settlements = load()
    evs = events_all(bars, settlements)
    spans = SPLITS["train"]
    # LQ 방향: 반전으로 잰 비용 전 평균(스톱 없음)의 부호로 정하고 양측 검정한다 (48 §3)
    rev = _trades(
        bars,
        settlements,
        evs["LQ"],
        spans,
        direction_of=lambda e: -e.side,
        hold=LQ_DEFAULT.hold_bars,
        cost=0.0,
        use_stop=False,
    )
    lq_sign = -1 if rev and statistics.fmean(t.gross + t.funding for t in rev) >= 0 else 1
    directions = {"FB": _fb_direction, "LQ": lambda e: lq_sign * e.side}
    holds = {"FB": FB_DEFAULT.hold_bars, "LQ": LQ_DEFAULT.hold_bars}
    results: dict[str, Any] = {}
    for k in ("FB", "LQ"):
        r = _run_hypothesis(bars, settlements, evs[k], spans, directions[k], holds[k])
        r["p_value"] = _p_value(r["primary"]["net"].get("t"), two_sided=k == "LQ")
        results[k] = r
    results["LQ"]["direction"] = "reversal" if lq_sign == -1 else "continuation"
    results["LQ"]["direction_sign"] = lq_sign
    order = sorted(results, key=lambda k: results[k]["p_value"])
    holm_ok: dict[str, bool] = {}
    still = True
    for rank, k in enumerate(order):
        still = still and results[k]["p_value"] < HOLM_ALPHA / (len(order) - rank)
        holm_ok[k] = still
    for k, r in results.items():
        net = r["primary"]["net"]
        years = {y: (r["by_year"].get(y) or {}).get("mean_bps") for y in TRAIN_YEARS}
        r.update(
            _status(
                [
                    ("T1_events", net["n"] >= MIN_EVENTS, net["n"]),
                    (
                        "T2_net_mean_positive_holm",
                        bool((net.get("mean_bps") or 0) > 0 and holm_ok[k]),
                        {"mean_bps": net.get("mean_bps"), "p": r["p_value"], "holm": holm_ok[k]},
                    ),
                    ("T3_both_years_positive", all((v or 0) > 0 for v in years.values()), years),
                ]
            )
        )
    record = {
        "git_sha": sha,
        "executed_at": datetime.now(tz=UTC).isoformat(),
        "config": {"FB": asdict(FB_DEFAULT), "LQ": asdict(LQ_DEFAULT), "cost": COST_BASE},
        "results": results,
    }
    _write(path, record)
    _print("train", results, path)
    return 0


def _later(stage: str, prior: str, output: Path, crit_fn: Any) -> int:
    guard = _guard(stage, output)
    if guard is None:
        return 3
    path, sha = guard
    prior_path = output / f"{prior}.json"
    if not prior_path.exists():
        print(f"{prior} 결과가 없다 — 순서대로 실행한다.")
        return 3
    before = json.loads(prior_path.read_text(encoding="utf-8"))["results"]
    passed = [k for k, r in before.items() if r["status"] == "PASS"]
    if not passed:
        print(f"{prior} 를 통과한 가설이 없다 — 폐기 (48 §4).")
        return 3
    bars, settlements = load()
    evs = events_all(bars, settlements)
    train_rec = json.loads((output / "train.json").read_text(encoding="utf-8"))["results"]
    lq_sign = int(train_rec["LQ"]["direction_sign"])
    directions = {"FB": _fb_direction, "LQ": lambda e: lq_sign * e.side}
    holds = {"FB": FB_DEFAULT.hold_bars, "LQ": LQ_DEFAULT.hold_bars}
    results: dict[str, Any] = {}
    for k in passed:
        r = _run_hypothesis(bars, settlements, evs[k], SPLITS[stage], directions[k], holds[k])
        r["p_value_one_sided"] = _p_value(r["primary"]["net"].get("t"), two_sided=False)
        r.update(_status(crit_fn(r)))
        results[k] = r
    _write(
        path, {"git_sha": sha, "executed_at": datetime.now(tz=UTC).isoformat(), "results": results}
    )
    _print(stage, results, path)
    return 0


def _validation_criteria(r: dict[str, Any]) -> list[tuple[str, bool, Any]]:
    net = r["primary"]["net"]
    return [
        ("V1_events", net["n"] >= 20, net["n"]),
        (
            "V2_net_mean_positive_p05",
            bool((net.get("mean_bps") or 0) > 0 and r["p_value_one_sided"] < 0.05),
            {"mean_bps": net.get("mean_bps"), "p": r["p_value_one_sided"]},
        ),
    ]


def _oos_criteria(r: dict[str, Any]) -> list[tuple[str, bool, Any]]:
    net = r["primary"]["net"]
    return [
        ("O1_events", net["n"] >= 20, net["n"]),
        ("O2_net_mean_positive", (net.get("mean_bps") or 0) > 0, net.get("mean_bps")),
        ("O3_mdd_le_30pct", r["strategy_1x"]["mdd"] <= 0.30, r["strategy_1x"]["mdd"]),
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("count", "train", "validation", "oos"))
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    if args.stage == "count":
        sys.stdout.write(json.dumps(count(), ensure_ascii=False, indent=1) + "\n")
        return 0
    if args.stage == "train":
        return train(args.output)
    if args.stage == "validation":
        return _later("validation", "train", args.output, _validation_criteria)
    return _later("oos", "validation", args.output, _oos_criteria)


if __name__ == "__main__":
    sys.exit(main())
