"""문서 273 — Altair(AG4 + 숏 2 배) 를 BTC 만 · ETH 만으로 (설명용 1 회).

python -m scripts.run_altair_single_273

BOTH 는 266 NONE · BASE 결과를 그대로 읽는다. BTC2 · ETH2 는 다른 코인 목표 0 · 숏 신호 끔 ·
남은 코인 목표 두 배(상한 1.0). BTC1 · ETH1 은 목표 그대로(보고, 2020-06 만).
"""

from __future__ import annotations

import dataclasses
import json
import statistics
import subprocess
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from scripts.run_ag4_start_234 import STARTS, _from, _ms, _stats

from auto_trading.core.errors import DataIntegrityError
from auto_trading.research import ag_short_leverage_20261013 as p
from auto_trading.research.ag_short_lev_235 import account_with_leverage
from auto_trading.research.ag_short_own_gate_232 import account_class
from scripts import run_ag_short_leverage_20261013 as base_run
from scripts import wallet_grid_data_20261013 as data

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "reports" / "altair_single_273"
R266 = ROOT / "reports" / "altair_halt_266" / "result.json"
FILES = ("docs/research/273-altair-single-coin.md", "scripts/run_altair_single_273.py")
ONE, ZERO = Decimal(1), Decimal(0)


def single_account(keep: int, scale: Decimal) -> type[p.Account]:
    """``keep`` 코인만 (0 = BTC, 1 = ETH). 목표는 읽을 때 ``scale`` 배(상한 1)."""

    class Single(account_with_leverage(Decimal(2))):  # type: ignore[misc]
        @property
        def goals(self) -> tuple[Decimal, Decimal]:
            raw = self.__dict__.get("_goals", (ZERO, ZERO))
            out = [ZERO, ZERO]
            out[keep] = min(ONE, raw[keep] * scale)
            return out[0], out[1]

        @goals.setter
        def goals(self, value: tuple[Decimal, Decimal]) -> None:
            self.__dict__["_goals"] = value

    return Single


def only_signals(blocks: Iterator[p.Block], keep: int) -> Iterator[p.Block]:
    for b in blocks:
        sig = tuple(s if i == keep else False for i, s in enumerate(b.signals))
        yield dataclasses.replace(b, signals=sig)


def one(start: str, keep: int, scale: Decimal, selected: tuple[Any, ...]) -> dict[str, Any]:
    manifest, features = base_run.source()
    start_ms = _ms(start)
    with account_class(single_account(keep, scale)):
        runs = p.simulate(
            only_signals(
                _from(
                    data.blocks(base_run.SOURCE / "aligned", manifest, features, progress=False),
                    start_ms,
                ),
                keep,
            ),
            end_ms=base_run.END,
            selected=selected,
        )
    saved = json.loads(json.dumps(runs[selected[0].key], default=base_run.serialize))
    row = _stats(saved, start_ms)
    row["mean_exposure"] = float(Decimal(saved["mean_exposure"]))
    return row


def main() -> int:
    target = OUT / "result.json"
    if target.exists():
        raise DataIntegrityError("273 already ran; no rerun")
    if subprocess.check_output(["git", "status", "--porcelain", "--", *FILES], cwd=ROOT, text=True):
        raise DataIntegrityError("273 contract/script must be committed first")
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    both = json.loads(R266.read_text(encoding="utf-8"))["rows"]
    selected = tuple(
        c
        for c in p.cases()
        if c.exposure == 4 and c.mode == "SHORT_MAX" and c.scenario.name == "BASE"
    )
    rows: dict[str, dict[str, Any]] = {}
    for start in STARTS:
        rows[f"{start}:BOTH"] = both[f"{start}:NONE:BASE"]
        for name, keep in (("BTC2", 0), ("ETH2", 1)):
            rows[f"{start}:{name}"] = one(start, keep, Decimal(2), selected)
        print(json.dumps({"done": start}), flush=True)
    for name, keep in (("BTC1", 0), ("ETH1", 1)):
        rows[f"{STARTS[0]}:{name}"] = one(STARTS[0], keep, ONE, selected)

    def better(a: str, key: str) -> int:
        return sum(
            1
            for s in STARTS
            if float(rows[f"{s}:{a}"][key] or -9e9) > float(rows[f"{s}:BOTH"][key] or -9e9)
        )

    summary = {
        name: {
            "more_final": better(name, "net"),
            "higher_calmar": better(name, "calmar"),
            "median_cagr_diff": statistics.median(
                (rows[f"{s}:{name}"]["cagr"] or -1) - (rows[f"{s}:BOTH"]["cagr"] or -1)
                for s in STARTS
            ),
            "median_mdd_diff": statistics.median(
                rows[f"{s}:{name}"]["mdd"] - rows[f"{s}:BOTH"]["mdd"] for s in STARTS
            ),
        }
        for name in ("BTC2", "ETH2")
    }
    out = {
        "doc": FILES[0],
        "code_commit": commit,
        "completed_utc": datetime.now(UTC).isoformat(),
        "rows": rows,
        "summary": summary,
        "status": "EXPLANATORY_RETROSPECTIVE_NO_VERDICT",
    }
    OUT.mkdir(parents=True, exist_ok=True)
    with target.open("x", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
