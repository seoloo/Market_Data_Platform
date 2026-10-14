"""문서 232 — AG + 숏, 숏 차단만 숏 자기 손익 기준으로 바꿔 'AG 만' 과 비교 (설명용 후향 1 회).

Codex 교정판 엔진(`ag_short_leverage_20261013`)과 동결 입력을 그대로 쓰고, ``Account.short_halted``
만 바꾼 하위 클래스로 같은 ``simulate`` 를 돌린다. 엔진 파일은 고치지 않는다.

    python -m scripts.run_ag_short_own_gate_232
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
from auto_trading.research.ag_short_own_gate_232 import (
    NoGateAccount,
    OwnGateAccount,
    account_class,
)
from scripts import run_ag_short_leverage_20261013 as base_run
from scripts import wallet_grid_data_20261013 as data

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "reports" / "ag_short_own_gate_232"
CODEX_RESULT = ROOT / "reports" / "ag_short_leverage_corrected_20261014" / "result.json"
CODEX_RESULT_SHA = "f53eb97ac2da74ada2aaecf558d5a9664869e6274c7fede67d0d3c5937a8bf47"
FILES = (
    "docs/research/232-ag-short-own-gate.md",
    "scripts/run_ag_short_own_gate_232.py",
    "src/auto_trading/research/ag_short_own_gate_232.py",
)
MIN_SHORTS = 30


def _run(cls: type[p.Account], selected: tuple[p.Case, ...]) -> dict[str, dict[str, Any]]:
    manifest, features = base_run.source()
    with account_class(cls):
        runs = p.simulate(
            data.blocks(base_run.SOURCE / "aligned", manifest, features, progress=True),
            end_ms=base_run.END,
            selected=selected,
        )
    out = {}
    for key, run in runs.items():
        saved = json.loads(json.dumps(run, default=base_run.serialize))
        summary = base_run.metrics(saved)
        summary["independent_accounting"] = base_run.reconcile(saved)
        summary["short_trades"] = [t for t in saved["trades"] if t["side"] == "SHORT"]
        out[key] = summary
    return out


def _verdict(rows: list[dict[str, Any]]) -> str:
    core = [r for r in rows if r["scenario"] in ("BASE", "COST2")]
    if sum(r["short_roundtrips"] for r in core) < MIN_SHORTS:
        return "표본 부족"
    deltas = [Decimal(r["delta_net"]) for r in core]
    if all(d > 0 for d in deltas):
        return "후향 기여 유리"
    if all(d <= 0 for d in deltas):
        return "불리"
    return "혼재"


def main() -> int:
    if (OUT / "result.json").exists():
        raise DataIntegrityError("232 already ran; no rerun")
    if subprocess.check_output(["git", "status", "--porcelain", "--", *FILES], cwd=ROOT, text=True):
        raise DataIntegrityError("232 contract/script must be committed first")
    if base_run.digest(CODEX_RESULT) != CODEX_RESULT_SHA:
        raise DataIntegrityError("Codex corrected result changed")
    codex = json.loads(CODEX_RESULT.read_text(encoding="utf8"))["summaries"]
    started = datetime.now(UTC).isoformat()
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    own = _run(OwnGateAccount, p.cases())
    for key, s in own.items():
        if key.split(":")[1] == "BASE":
            c = codex[key]
            if Decimal(s["net"]) != Decimal(c["net"]) or Decimal(s["mdd"]) != Decimal(c["mdd"]):
                raise DataIntegrityError(f"BASE mismatch vs Codex: {key}")
    shorts = tuple(c for c in p.cases() if c.mode != "BASE")
    none = _run(NoGateAccount, shorts)
    rows: list[dict[str, Any]] = []
    for gate, runs in (("OWN", own), ("NONE", none)):
        for c in shorts:
            s = runs[c.key]
            b = own[f"AG{c.exposure}:BASE:{c.scenario.name}"]
            rows.append(
                {
                    "gate": gate,
                    "ag": c.exposure,
                    "mode": c.mode,
                    "scenario": c.scenario.name,
                    "base_net": b["net"],
                    "net": s["net"],
                    "delta_net": str(Decimal(s["net"]) - Decimal(b["net"])),
                    "base_mdd": b["mdd"],
                    "mdd": s["mdd"],
                    "base_cagr": b["cagr"],
                    "cagr": s["cagr"],
                    "short_roundtrips": s["short_roundtrips"],
                    "short_losing": s["short_losing_roundtrips"],
                    "closed_short_net": s["closed_short_net"],
                    "short_by_year": s["short_by_year"],
                    "short_by_coin": s["short_by_coin"],
                    "first_short_halt_ms": s["first_short_halt_ms"],
                }
            )
    verdicts = {
        f"AG{ag}:{mode}": _verdict(
            [r for r in rows if r["gate"] == "OWN" and r["ag"] == ag and r["mode"] == mode]
        )
        for ag in (3, 4)
        for mode in ("SHORT_1X", "SHORT_MAX")
    }
    result = {
        "doc": "docs/research/232-ag-short-own-gate.md",
        "code_commit": commit,
        "started_utc": started,
        "completed_utc": datetime.now(UTC).isoformat(),
        "input_id": base_run.INPUT_ID,
        "base_matches_codex": True,
        "rows": rows,
        "verdicts": verdicts,
        "status": "EXPLANATORY_RETROSPECTIVE_ADOPTION_UNVERIFIED",
        "trades": {
            f"{gate}:{k}": v["short_trades"]
            for gate, runs in (("OWN", own), ("NONE", none))
            for k, v in runs.items()
            if k.split(":")[1] != "BASE"
        },
    }
    OUT.mkdir(parents=True, exist_ok=True)
    with (OUT / "result.json").open("x", encoding="utf8") as f:
        json.dump(result, f, default=base_run.serialize, ensure_ascii=False, indent=1)
    print(json.dumps({"verdicts": verdicts}, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
