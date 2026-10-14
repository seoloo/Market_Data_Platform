"""문서 234 — AG4 L0 · L0 + 숏 · T · T + 숏 을 시작 달 9 개로, 설명용 후향 1 회.

python -m scripts.run_ag4_start_234
"""

from __future__ import annotations

import json
import statistics
import subprocess
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from scripts.run_ag4_long_233 import _infos

from auto_trading.core.errors import DataIntegrityError
from auto_trading.research import ag_short_leverage_20261013 as p
from auto_trading.research.ag4_long_233 import account_for
from auto_trading.research.ag_short_own_gate_232 import account_class
from scripts import run_ag_short_leverage_20261013 as base_run
from scripts import wallet_grid_data_20261013 as data

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "reports" / "ag4_start_234"
R233 = ROOT / "reports" / "ag4_long_233" / "result.json"
FILES = ("docs/research/234-ag4-start-robustness.md", "scripts/run_ag4_start_234.py")
DAY = 86_400_000
STARTS = (
    "2020-06-01",
    "2020-12-01",
    "2021-06-01",
    "2021-12-01",
    "2022-06-01",
    "2022-12-01",
    "2023-06-01",
    "2023-12-01",
    "2024-06-01",
)
SCENARIOS = ("BASE", "COST2")


def _ms(day: str) -> int:
    return int(datetime.fromisoformat(day).replace(tzinfo=UTC).timestamp() * 1000)


def _from(blocks: Iterator[p.Block], start_ms: int) -> Iterator[p.Block]:
    return (b for b in blocks if b.at_ms >= start_ms)


def _stats(run: dict[str, Any], start_ms: int) -> dict[str, Any]:
    net = Decimal(run["net"])
    final = float(p.INITIAL + net)
    years = (base_run.END - start_ms) / DAY / 365.25
    cagr = (final / float(p.INITIAL)) ** (1 / years) - 1 if final > 0 else None
    mdd = float(run["mdd"])
    shorts = [t for t in run["trades"] if t["side"] == "SHORT"]
    return {
        "net": str(net),
        "cagr": cagr,
        "mdd": mdd,
        "calmar": cagr / mdd if cagr is not None and mdd > 0 else None,
        "short_roundtrips": len(shorts),
        "closed_short_net": str(sum((Decimal(t["net"]) for t in shorts), Decimal(0))),
    }


def _count(rows: dict[str, dict[str, Any]], a: str, b: str, scen: str) -> int:
    return sum(
        1
        for s in STARTS
        if (rows[f"{s}:{a}:{scen}"]["calmar"] or -9e9) > (rows[f"{s}:{b}:{scen}"]["calmar"] or -9e9)
    )


def main() -> int:
    if (OUT / "result.json").exists():
        raise DataIntegrityError("234 already ran; no rerun")
    if subprocess.check_output(["git", "status", "--porcelain", "--", *FILES], cwd=ROOT, text=True):
        raise DataIntegrityError("234 contract/script must be committed first")
    ref = {
        (r["variant"], r["mode"], r["scenario"]): r["net"]
        for r in json.loads(R233.read_text(encoding="utf8"))["rows"]
    }
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    started = datetime.now(UTC).isoformat()
    manifest, features = base_run.source()
    infos = _infos(features)
    selected = tuple(
        c
        for c in p.cases()
        if c.exposure == 4 and c.mode in ("BASE", "SHORT_1X") and c.scenario.name in SCENARIOS
    )
    rows: dict[str, dict[str, Any]] = {}
    names = {
        ("L0", "BASE"): "L0",
        ("L0", "SHORT_1X"): "L0+S",
        ("T", "BASE"): "T",
        ("T", "SHORT_1X"): "T+S",
    }
    for start in STARTS:
        start_ms = _ms(start)
        for variant in ("L0", "T"):
            with account_class(account_for(variant, infos)):
                runs = p.simulate(
                    _from(
                        data.blocks(
                            base_run.SOURCE / "aligned", manifest, features, progress=False
                        ),
                        start_ms,
                    ),
                    end_ms=base_run.END,
                    selected=selected,
                )
            for c in selected:
                saved = json.loads(json.dumps(runs[c.key], default=base_run.serialize))
                key = f"{start}:{names[(variant, c.mode)]}:{c.scenario.name}"
                rows[key] = _stats(saved, start_ms)
                if start == STARTS[0] and Decimal(rows[key]["net"]) != Decimal(
                    ref[(variant, c.mode, c.scenario.name)]
                ):
                    raise DataIntegrityError(f"2020-06 mismatch vs 233: {key}")
        print(json.dumps({"start_done": start}), flush=True)
    counts = {
        f"{a} vs {b}:{scen}": _count(rows, a, b, scen)
        for a, b in (("T+S", "L0"), ("T", "L0"), ("L0+S", "L0"))
        for scen in SCENARIOS
    }
    mdd_gap = statistics.median(
        rows[f"{s}:L0:BASE"]["mdd"] - rows[f"{s}:T+S:BASE"]["mdd"] for s in STARTS
    )
    lo = min(counts["T+S vs L0:BASE"], counts["T+S vs L0:COST2"])
    verdict = (
        "시작 시점에 강함"
        if lo >= 7 and mdd_gap >= 0.05
        else "시작 시점에 약함(운)"
        if lo <= 4
        else "혼재"
    )
    result = {
        "doc": FILES[0],
        "code_commit": commit,
        "started_utc": started,
        "completed_utc": datetime.now(UTC).isoformat(),
        "input_id": base_run.INPUT_ID,
        "start_2020_matches_233": True,
        "rows": rows,
        "counts": counts,
        "median_mdd_gap_base_l0_minus_ts": mdd_gap,
        "verdict": verdict,
        "status": "EXPLANATORY_RETROSPECTIVE_ADOPTION_UNVERIFIED",
    }
    OUT.mkdir(parents=True, exist_ok=True)
    with (OUT / "result.json").open("x", encoding="utf8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    print(json.dumps({"verdict": verdict, "counts": counts}, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
