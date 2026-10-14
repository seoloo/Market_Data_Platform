"""문서 233 — AG4 롱 손보기(T 추세 · V 변동성 · D 낙폭 브레이크) ± 232 숏, 설명용 후향 1 회.

python -m scripts.run_ag4_long_233
"""

from __future__ import annotations

import bisect
import json
import math
import sqlite3
import statistics
import subprocess
from datetime import UTC, datetime
from decimal import Decimal
from itertools import pairwise
from pathlib import Path
from typing import Any

from auto_trading.core.errors import DataIntegrityError
from auto_trading.research import ag_short_leverage_20261013 as p
from auto_trading.research.ag4_long_233 import BRAKE_EVENTS, VARIANTS, account_for, day_info
from auto_trading.research.ag_short_own_gate_232 import account_class
from scripts import run_ag_short_leverage_20261013 as base_run
from scripts import wallet_grid_data_20261013 as data

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "reports" / "ag4_long_233"
DB = ROOT / "data" / "trading.db"
CODEX_RESULT = ROOT / "reports" / "ag_short_leverage_corrected_20261014" / "result.json"
R232 = ROOT / "reports" / "ag_short_own_gate_232" / "result.json"
FILES = (
    "docs/research/233-ag4-long-trend-vol-dd.md",
    "scripts/run_ag4_long_233.py",
    "src/auto_trading/research/ag4_long_233.py",
)
DAY = 86_400_000
MIN_SHORTS = 30


def _closes() -> dict[str, tuple[list[int], list[float]]]:
    out = {}
    with sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True) as db:
        for s in p.SYMBOLS:
            rows = sorted(
                (int(ms), float(c))
                for ms, c in db.execute(
                    "SELECT open_ms, close FROM candles WHERE exchange='binance_spot'"
                    " AND symbol=? AND timeframe='1d'",
                    (s,),
                )
            )
            out[s] = ([r[0] for r in rows], [r[1] for r in rows])
    return out


def _infos(features: list[dict[str, Any]]) -> dict[int, Any]:
    closes = _closes()
    infos = {}
    for f in features:
        if not f.get("targets"):
            continue
        pair = []
        for s in p.SYMBOLS:
            days, cl = closes[s]
            k = bisect.bisect_right(days, int(f["target_as_of_ms"]) - DAY)
            info = day_info(cl[:k])
            if info is None:
                raise DataIntegrityError("233 warmup")
            pair.append(info)
        infos[int(f["at_ms"])] = (pair[0], pair[1])
    return infos


def _calmar(s: dict[str, Any]) -> float | None:
    mdd = float(s["mdd"])
    return s["cagr"] / mdd if s.get("cagr") is not None and mdd > 0 else None


def _rule(rows: list[tuple[float, float]]) -> str:
    """(새, 기준) 쌍 BASE · COST2 — 둘 다 크면 개선, 둘 다 작거나 같으면 불리."""
    if all(a > b for a, b in rows):
        return "후향 개선"
    if all(a <= b for a, b in rows):
        return "불리"
    return "혼재"


def _vol_table(trace: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """AG4 그대로(L0 BASE)의 하루 수익을 BTC 20 일 변동성 3 구간으로 나눠 본다(설명용)."""
    days, cl = _closes()["BTCUSDT"]
    daily = [(int(x["at_ms"]), float(x["equity"])) for x in trace if int(x["at_ms"]) % DAY == 0]
    rows = []
    for (t0, e0), (_, e1) in pairwise(daily):
        k = bisect.bisect_right(days, t0 - DAY)
        if k < 21:
            continue
        steps = [math.log(cl[k - j] / cl[k - j - 1]) for j in range(1, 21)]
        rows.append((statistics.stdev(steps) * math.sqrt(365), e1 / e0 - 1))
    vols = sorted(v for v, _ in rows)
    cuts = (vols[len(vols) // 3], vols[2 * len(vols) // 3])
    out = []
    for name, lo, hi in (
        ("낮음", 0.0, cuts[0]),
        ("중간", cuts[0], cuts[1]),
        ("높음", cuts[1], 9e9),
    ):
        r = [x for v, x in rows if lo <= v < hi]
        out.append(
            {
                "bucket": name,
                "btc_vol20_range": [lo, hi if hi < 9e9 else None],
                "days": len(r),
                "mean_daily": statistics.fmean(r),
                "sd_daily": statistics.stdev(r),
                "worst_day": min(r),
                "sum_log": sum(math.log1p(x) for x in r),
            }
        )
    return out


def main() -> int:
    if (OUT / "result.json").exists():
        raise DataIntegrityError("233 already ran; no rerun")
    if subprocess.check_output(["git", "status", "--porcelain", "--", *FILES], cwd=ROOT, text=True):
        raise DataIntegrityError("233 contract/script must be committed first")
    codex = json.loads(CODEX_RESULT.read_text(encoding="utf8"))["summaries"]
    r232 = {
        (r["mode"], r["scenario"]): r["net"]
        for r in json.loads(R232.read_text(encoding="utf8"))["rows"]
        if r["gate"] == "OWN" and r["ag"] == 4
    }
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    started = datetime.now(UTC).isoformat()
    manifest, features = base_run.source()
    infos = _infos(features)
    selected = tuple(c for c in p.cases() if c.exposure == 4 and c.mode in ("BASE", "SHORT_1X"))
    summaries: dict[str, dict[str, Any]] = {}
    l0_trace: list[dict[str, Any]] = []
    for variant in VARIANTS:
        with account_class(account_for(variant, infos)):
            runs = p.simulate(
                data.blocks(base_run.SOURCE / "aligned", manifest, features, progress=True),
                end_ms=base_run.END,
                selected=selected,
            )
        for key, run in runs.items():
            saved = json.loads(json.dumps(run, default=base_run.serialize))
            s = base_run.metrics(saved)
            s["independent_accounting"] = base_run.reconcile(saved)
            s["calmar"] = _calmar(s)
            summaries[f"{variant}:{key}"] = s
            if variant == "L0" and key == "AG4:BASE:BASE":
                l0_trace = saved["trace"]
        if variant == "L0":
            for c in selected:
                s = summaries[f"L0:{c.key}"]
                want = codex[c.key]["net"] if c.mode == "BASE" else r232[(c.mode, c.scenario.name)]
                if Decimal(s["net"]) != Decimal(want):
                    raise DataIntegrityError(f"L0 mismatch vs Codex/232: {c.key}")
    rows = []
    for variant in VARIANTS:
        for c in selected:
            s = summaries[f"{variant}:{c.key}"]
            ref = summaries[f"L0:AG4:BASE:{c.scenario.name}"]
            own = summaries[f"{variant}:AG4:BASE:{c.scenario.name}"]
            rows.append(
                {
                    "variant": variant,
                    "mode": c.mode,
                    "scenario": c.scenario.name,
                    "net": s["net"],
                    "cagr": s["cagr"],
                    "mdd": s["mdd"],
                    "sharpe": s["sharpe"],
                    "calmar": s["calmar"],
                    "mean_exposure": s["mean_exposure"],
                    "net_minus_ag4": str(Decimal(s["net"]) - Decimal(ref["net"])),
                    "net_minus_same_variant_long": str(Decimal(s["net"]) - Decimal(own["net"])),
                    "short_roundtrips": s["short_roundtrips"],
                    "closed_short_net": s["closed_short_net"],
                    "short_by_year": s["short_by_year"],
                    "annual": s["annual"],
                }
            )

    def get(variant: str, mode: str, scen: str) -> dict[str, Any]:
        return summaries[f"{variant}:AG4:{mode}:{scen}"]

    verdicts: dict[str, str] = {}
    for v in ("T", "V", "D"):
        verdicts[f"{v} 롱 vs AG4 (Calmar)"] = _rule(
            [
                (get(v, "BASE", sc)["calmar"] or -9e9, get("L0", "BASE", sc)["calmar"] or -9e9)
                for sc in ("BASE", "COST2")
            ]
        )
    for v in VARIANTS:
        n = sum(get(v, "SHORT_1X", sc)["short_roundtrips"] for sc in ("BASE", "COST2"))
        verdicts[f"{v} + 숏 vs {v} (순손익)"] = (
            "표본 부족"
            if n < MIN_SHORTS
            else _rule(
                [
                    (float(get(v, "SHORT_1X", sc)["net"]), float(get(v, "BASE", sc)["net"]))
                    for sc in ("BASE", "COST2")
                ]
            )
        )
    result = {
        "doc": FILES[0],
        "code_commit": commit,
        "started_utc": started,
        "completed_utc": datetime.now(UTC).isoformat(),
        "input_id": base_run.INPUT_ID,
        "l0_matches_codex_and_232": True,
        "rows": rows,
        "verdicts": verdicts,
        "brake_events": BRAKE_EVENTS,
        "vol_buckets_l0": _vol_table(l0_trace),
        "status": "EXPLANATORY_RETROSPECTIVE_ADOPTION_UNVERIFIED",
    }
    OUT.mkdir(parents=True, exist_ok=True)
    with (OUT / "result.json").open("x", encoding="utf8") as f:
        json.dump(result, f, default=base_run.serialize, ensure_ascii=False, indent=1)
    print(json.dumps({"verdicts": verdicts}, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
