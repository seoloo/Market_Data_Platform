"""문서 266 — Altair(AG4 + 숏 2 배) 에 실거래 30% 정지 · 풀기 규칙을 넣은 설명용 후향 1 회.

python -m scripts.run_altair_halt_266

엔진 파일은 고치지 않는다. 계좌 하위 클래스(정지 중 ``targets_known`` 을 거짓으로 보여 엔진의
새 위험 관문을 막음)와 ``_checkpoint`` 감싸기(4 시간봉 마감마다 정지 · 풀기 판단)만 더한다.
"""

from __future__ import annotations

import json
import statistics
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, ClassVar

from scripts.run_ag4_start_234 import STARTS, _from, _ms, _stats

from auto_trading.core.errors import DataIntegrityError
from auto_trading.research import ag_short_leverage_20261013 as p
from auto_trading.research.ag_short_lev_235 import account_with_leverage
from auto_trading.research.ag_short_own_gate_232 import account_class
from scripts import run_ag_short_leverage_20261013 as base_run
from scripts import wallet_grid_data_20261013 as data

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "reports" / "altair_halt_266"
FILES = ("docs/research/266-altair-live-halt-rule.md", "scripts/run_altair_halt_266.py")
SCENARIOS = ("BASE", "COST2")
RULES = ("NONE", "NEVER", "D30", "TREND")
HALT = Decimal("0.70")
REF_235 = {
    "BASE": Decimal("186231.4032505557203868579490"),
    "COST2": Decimal("142751.7133333037094245001002"),
}


def halt_account(rule: str) -> type[p.Account]:
    """235 계좌(AG4 + 숏 2 배 · OWN) + 정지 중 새 위험 막기."""

    class HaltAccount(account_with_leverage(Decimal(2))):  # type: ignore[misc]
        RULE = rule
        created: ClassVar[list[Any]] = []

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            type(self).created.append(self)

        @property
        def halt(self) -> dict[str, Any]:
            state: dict[str, Any] = self.__dict__.setdefault(
                "_halt",
                {"on": False, "peak": p.INITIAL, "since": None, "off_seen": False, "events": [],
                 "blocks": 0},
            )  # fmt: skip
            return state

        @property
        def targets_known(self) -> bool:
            return bool(self.__dict__.get("_tk", False)) and not self.halt["on"]

        @targets_known.setter
        def targets_known(self, value: bool) -> None:
            self.__dict__["_tk"] = value

    HaltAccount.__name__ = f"HaltAccount_{rule}"
    return HaltAccount


def halt_step(a: Any, b: p.Block) -> None:
    """4 시간봉 마감 — 정지용 고점 갱신, 정지 · 풀기 판단 (실거래 altair_live 와 같은 뜻)."""
    rule = getattr(a, "RULE", "NONE")
    if rule == "NONE":
        return
    h = a.halt
    eq = a.equity(b.spot, b.perp, "c")
    at = b.at_ms + p.H4
    h["peak"] = max(h["peak"], eq)
    if h["on"]:
        h["blocks"] += 1
        if a.goals[0] == 0:
            h["off_seen"] = True
        release = (rule == "D30" and at - h["since"] >= 30 * p.DAY) or (
            rule == "TREND" and h["off_seen"] and a.goals[0] > 0
        )
        if release:
            h["on"], h["peak"] = False, eq
            h["events"].append(["off", at, str(eq)])
        return
    if eq <= h["peak"] * HALT:
        h["on"], h["since"], h["off_seen"] = True, at, a.goals[0] == 0
        h["events"].append(["on", at, str(eq)])


@contextmanager
def checkpoint_hook() -> Iterator[None]:
    original = p._checkpoint

    def wrapped(a: p.Account, b: p.Block) -> None:
        original(a, b)
        halt_step(a, b)

    p._checkpoint = wrapped
    try:
        yield
    finally:
        p._checkpoint = original


def _adds(fill: dict[str, Any]) -> bool:
    """노출을 늘리는 체결인가 — 현물 매수 · 롱 맞추기 매수 · 새 숏."""
    delta = Decimal(fill["delta"])
    if fill["reason"] == "OPEN_SHORT":
        return True
    return delta > 0 and (fill["asset"] == "SPOT" or fill["reason"] == "AG_REBALANCE")


def _day(ms: int | None) -> str | None:
    return None if ms is None else datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m-%d")


def main() -> int:
    if (OUT / "result.json").exists():
        raise DataIntegrityError("266 already ran; no rerun")
    if subprocess.check_output(["git", "status", "--porcelain", "--", *FILES], cwd=ROOT, text=True):
        raise DataIntegrityError("266 contract/script must be committed first")
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    started = datetime.now(UTC).isoformat()
    manifest, features = base_run.source()
    selected = tuple(
        c
        for c in p.cases()
        if c.exposure == 4 and c.mode == "SHORT_MAX" and c.scenario.name in SCENARIOS
    )
    if len(selected) != 2:
        raise DataIntegrityError("case selection")
    rows: dict[str, dict[str, Any]] = {}
    for start in STARTS:
        start_ms = _ms(start)
        for rule in RULES:
            cls = halt_account(rule)
            with account_class(cls), checkpoint_hook():
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
            halts = {acc.case.scenario.name: acc.halt for acc in cls.created}
            for c in selected:
                saved = json.loads(json.dumps(runs[c.key], default=base_run.serialize))
                key = f"{start}:{rule}:{c.scenario.name}"
                row = _stats(saved, start_ms)
                if (
                    start == STARTS[0]
                    and rule == "NONE"
                    and (Decimal(row["net"]) != REF_235[c.scenario.name])
                ):
                    raise DataIntegrityError(f"2020-06 NONE mismatch vs 235: {key}")
                h = halts[c.scenario.name]
                ev = h["events"]
                row["halts"] = sum(1 for e in ev if e[0] == "on")
                row["halted_share"] = h["blocks"] / len(saved["trace"])
                row["first_halt"] = _day(next((e[1] for e in ev if e[0] == "on"), None))
                row["events"] = [[e[0], _day(e[1]), e[2]] for e in ev]
                if rule == "NEVER" and ev:
                    adds = [f for f in saved["fills"] if f["at_ms"] > ev[0][1] and _adds(f)]
                    if adds:
                        raise DataIntegrityError(f"NEVER 정지 뒤 늘리는 체결 {len(adds)}: {key}")
                rows[key] = row
            print(json.dumps({"done": f"{start}:{rule}"}), flush=True)
    summary: dict[str, Any] = {}
    for rule in RULES[1:]:
        for scen in SCENARIOS:
            pairs = [(rows[f"{s}:{rule}:{scen}"], rows[f"{s}:NONE:{scen}"]) for s in STARTS]
            summary[f"{rule}:{scen}"] = {
                "more_final": sum(1 for r, n in pairs if Decimal(r["net"]) > Decimal(n["net"])),
                "median_cagr_diff": statistics.median(
                    (r["cagr"] or -1) - (n["cagr"] or -1) for r, n in pairs
                ),
                "median_mdd_diff": statistics.median(r["mdd"] - n["mdd"] for r, n in pairs),
            }
    result = {
        "doc": FILES[0],
        "code_commit": commit,
        "started_utc": started,
        "completed_utc": datetime.now(UTC).isoformat(),
        "input_id": base_run.INPUT_ID,
        "none_2020_matches_235": True,
        "rows": rows,
        "summary": summary,
        "status": "EXPLANATORY_RETROSPECTIVE_NO_VERDICT",
    }
    OUT.mkdir(parents=True, exist_ok=True)
    with (OUT / "result.json").open("x", encoding="utf8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    print(json.dumps(summary, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
