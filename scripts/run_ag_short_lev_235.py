"""문서 235 — AG3 + 숏 2 배, AG4 + 숏 2 배 · 3 배 (232 엔진 · OWN 차단), 설명용 후향 1 회.

python -m scripts.run_ag_short_lev_235
"""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from auto_trading.core.errors import DataIntegrityError
from auto_trading.research import ag_short_leverage_20261013 as p
from auto_trading.research.ag_short_lev_235 import account_with_leverage
from auto_trading.research.ag_short_own_gate_232 import account_class
from scripts import run_ag_short_leverage_20261013 as base_run
from scripts import wallet_grid_data_20261013 as data

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "reports" / "ag_short_lev_235"
R232 = ROOT / "reports" / "ag_short_own_gate_232" / "result.json"
FILES = (
    "docs/research/235-ag-short-leverage-2x-3x.md",
    "scripts/run_ag_short_lev_235.py",
    "src/auto_trading/research/ag_short_lev_235.py",
)
RUNS = ((3, 3, "check"), (3, 2, "AG3+S2"), (4, 2, "AG4+S2"), (4, 3, "AG4+S3"))
"""(AG 배율, 숏 배율, 이름). 첫 줄은 232 AG3 MAX 재현 대조."""


def main() -> int:
    if (OUT / "result.json").exists():
        raise DataIntegrityError("235 already ran; no rerun")
    if subprocess.check_output(["git", "status", "--porcelain", "--", *FILES], cwd=ROOT, text=True):
        raise DataIntegrityError("235 contract/script must be committed first")
    r232 = json.loads(R232.read_text(encoding="utf8"))["rows"]
    own = {(r["ag"], r["mode"], r["scenario"]): r for r in r232 if r["gate"] == "OWN"}
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    started = datetime.now(UTC).isoformat()
    manifest, features = base_run.source()
    rows: list[dict[str, Any]] = []
    for exposure, lev, name in RUNS:
        selected = tuple(c for c in p.cases() if c.exposure == exposure and c.mode == "SHORT_MAX")
        with account_class(account_with_leverage(Decimal(lev))):
            runs = p.simulate(
                data.blocks(base_run.SOURCE / "aligned", manifest, features, progress=False),
                end_ms=base_run.END,
                selected=selected,
            )
        for c in selected:
            saved = json.loads(json.dumps(runs[c.key], default=base_run.serialize))
            s = base_run.metrics(saved)
            ref = own[(exposure, "SHORT_MAX", c.scenario.name)]
            if name == "check":
                if Decimal(s["net"]) != Decimal(ref["net"]):
                    raise DataIntegrityError(f"override check failed {c.key}")
                continue
            if Decimal(saved["case"]["short_leverage"]) != Decimal(lev):
                raise DataIntegrityError("leverage override not applied")
            rows.append(
                {
                    "name": name,
                    "ag": exposure,
                    "short_leverage": lev,
                    "scenario": c.scenario.name,
                    "base_net": ref["base_net"],
                    "net": s["net"],
                    "delta_net": str(Decimal(s["net"]) - Decimal(ref["base_net"])),
                    "base_mdd": ref["base_mdd"],
                    "mdd": s["mdd"],
                    "cagr": s["cagr"],
                    "short_roundtrips": s["short_roundtrips"],
                    "closed_short_net": s["closed_short_net"],
                    "short_by_year": s["short_by_year"],
                    "first_short_halt_ms": s["first_short_halt_ms"],
                    "mean_entry_short_notional_pct": s["mean_entry_short_notional_pct"],
                }
            )
        print(json.dumps({"done": name}), flush=True)
    verdicts = {}
    for name in ("AG3+S2", "AG4+S2", "AG4+S3"):
        core = [r for r in rows if r["name"] == name and r["scenario"] in ("BASE", "COST2")]
        n = sum(r["short_roundtrips"] for r in core)
        d = [Decimal(r["delta_net"]) for r in core]
        verdicts[name] = (
            "표본 부족"
            if n < 30
            else "후향 기여 유리"
            if all(x > 0 for x in d)
            else "불리"
            if all(x <= 0 for x in d)
            else "혼재"
        )
    result = {
        "doc": FILES[0],
        "code_commit": commit,
        "started_utc": started,
        "completed_utc": datetime.now(UTC).isoformat(),
        "input_id": base_run.INPUT_ID,
        "override_check_ag3_max_matches_232": True,
        "rows": rows,
        "verdicts": verdicts,
        "status": "EXPLANATORY_RETROSPECTIVE_ADOPTION_UNVERIFIED",
    }
    OUT.mkdir(parents=True, exist_ok=True)
    with (OUT / "result.json").open("x", encoding="utf8") as f:
        json.dump(result, f, default=base_run.serialize, ensure_ascii=False, indent=1)
    print(json.dumps({"verdicts": verdicts}, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
