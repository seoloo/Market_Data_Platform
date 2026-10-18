"""문서 269 — Altair(AG4 + 숏 2 배) 하루 롱 맞추기를 메이커 주문으로 겹쳐 보기 (설명용 1 회).

python -m scripts.run_altair_maker_269

235 엔진 체결 중 ``AG_REBALANCE`` 만: 기준가(그 분 시가)에 걸어 두고 W 분 안에 1 분 봉이
기준가를 넘어가면 메이커(수수료 0), 아니면 W 분째 시가에 테이커(0.04% + 미끄럼 0.045%).
"""

from __future__ import annotations

import bisect
import csv
import io
import json
import subprocess
import zipfile
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from scripts.run_ag4_start_234 import _from, _ms

from auto_trading.core.errors import DataIntegrityError
from auto_trading.research import ag_short_leverage_20261013 as p
from auto_trading.research.ag_short_lev_235 import account_with_leverage
from auto_trading.research.ag_short_own_gate_232 import account_class
from scripts import run_ag_short_leverage_20261013 as base_run
from scripts import wallet_grid_data_20261013 as data

ROOT = Path(__file__).resolve().parent.parent
UM = ROOT / "data" / "external" / "binance_vision" / "um"
OUT = ROOT / "reports" / "altair_maker_269"
FILES = ("docs/research/269-altair-maker-rebalance.md", "scripts/run_altair_maker_269.py")
REF_NET = Decimal("186231.4032505557203868579490")
TAKER, SLIP = 0.0004, 0.00045
TAKER_USDT = 0.0005
WAITS = (30, 10, 60)
HALF_MS = _ms("2023-06-01")
MINUTE = 60_000


class Minutes:
    """(심볼, 달) 1 분 봉을 필요할 때 읽어 둔다: 시각 → (시가, 고가, 저가)."""

    def __init__(self) -> None:
        self.cache: dict[tuple[str, str], dict[int, tuple[float, float, float]]] = {}

    def _month(self, sym: str, month: str) -> dict[int, tuple[float, float, float]]:
        key = (sym, month)
        if key not in self.cache:
            path = UM / sym / "1m" / f"{sym}-1m-{month}.zip"
            rows: dict[int, tuple[float, float, float]] = {}
            if path.exists():
                with zipfile.ZipFile(path) as zf:
                    text = zf.read(zf.namelist()[0]).decode("ascii")
                for r in csv.reader(io.StringIO(text)):
                    if r and r[0].isdigit():
                        rows[int(r[0])] = (float(r[1]), float(r[2]), float(r[3]))
            if len(self.cache) > 6:
                self.cache.pop(next(iter(self.cache)))
            self.cache[key] = rows
        return self.cache[key]

    def get(self, sym: str, ms: int) -> tuple[float, float, float] | None:
        month = datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m")
        return self._month(sym, month).get(ms)


def maker(m: Minutes, fill: dict[str, Any], wait: int) -> tuple[bool, float, float]:
    """(걸림, 메이커 비용, 기준 비용) — 비용은 달러, 체결 금액 기준."""
    sym, at = fill["symbol"], int(fill["at_ms"])
    qty = abs(float(Decimal(fill["delta"])))
    ref = float(Decimal(fill["raw_price"]))
    base = qty * ref * (TAKER + SLIP)
    buy = float(Decimal(fill["delta"])) > 0
    for k in range(wait):
        bar = m.get(sym, at + k * MINUTE)
        if bar is None:
            continue
        if (buy and bar[2] < ref) or (not buy and bar[1] > ref):
            return True, 0.0, base
    bar = m.get(sym, at + wait * MINUTE)
    px = bar[0] if bar is not None else ref
    drift = (px - ref) * qty * (1 if buy else -1)
    return False, drift + qty * px * (TAKER + SLIP), base


def main() -> int:
    target = OUT / "result.json"
    if target.exists():
        raise DataIntegrityError("269 already ran; no rerun")
    if subprocess.check_output(["git", "status", "--porcelain", "--", *FILES], cwd=ROOT, text=True):
        raise DataIntegrityError("269 contract/script must be committed first")
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    manifest, features = base_run.source()
    selected = tuple(
        c
        for c in p.cases()
        if c.exposure == 4 and c.mode == "SHORT_MAX" and c.scenario.name == "BASE"
    )
    with account_class(account_with_leverage(Decimal(2))):
        runs = p.simulate(
            _from(
                data.blocks(base_run.SOURCE / "aligned", manifest, features, progress=False),
                _ms("2020-06-01"),
            ),
            end_ms=base_run.END,
            selected=selected,
        )
    run = json.loads(json.dumps(runs[selected[0].key], default=base_run.serialize))
    if Decimal(run["net"]) != REF_NET:
        raise DataIntegrityError(f"235 대조 실패: {run['net']}")
    trace = [(int(t["at_ms"]), float(Decimal(t["equity"]))) for t in run["trace"]]
    times = [t for t, _ in trace]
    end_eq = trace[-1][1]

    def growth(at: int) -> float:
        k = bisect.bisect_right(times, at) - 1
        eq = trace[k][1] if k >= 0 else float(p.INITIAL)
        return end_eq / eq

    fills = [f for f in run["fills"] if f["reason"] == "AG_REBALANCE"]
    all_cost = sum(float(Decimal(f["fee"])) + float(Decimal(f["slip"])) for f in run["fills"])
    m = Minutes()
    out: dict[str, Any] = {
        "doc": FILES[0],
        "code_commit": commit,
        "net_235": run["net"],
        "end_equity": end_eq,
        "fills_all": len(run["fills"]),
        "fills_rebalance": len(fills),
        "engine_cost_all": all_cost,
        "engine_cost_rebalance": sum(
            float(Decimal(f["fee"])) + float(Decimal(f["slip"])) for f in fills
        ),
    }
    # ① USDT 테이커 → USDC 테이커 (모든 선물 체결, 미끄럼 그대로)
    fee_switch = sum(
        abs(float(Decimal(f["delta"]))) * float(Decimal(f["price"])) * (TAKER_USDT - TAKER)
        * growth(int(f["at_ms"]))
        for f in run["fills"]
        if f["asset"] == "FUTURE"
    )  # fmt: skip
    out["usdc_taker_switch_end_effect"] = fee_switch
    for wait in WAITS:
        rows = []
        for f in sorted(fills, key=lambda x: (x["symbol"], x["at_ms"])):
            hit, mk, base = maker(m, f, wait)
            rows.append((f, hit, mk, base))
        res: dict[str, Any] = {}
        for name, part in (
            ("all", rows),
            ("future", [r for r in rows if r[0]["asset"] == "FUTURE"]),
            ("spot", [r for r in rows if r[0]["asset"] == "SPOT"]),
            ("h1", [r for r in rows if int(r[0]["at_ms"]) < HALF_MS]),
            ("h2", [r for r in rows if int(r[0]["at_ms"]) >= HALF_MS]),
        ):
            miss = [mk - base for _f, hit, mk, base in part if not hit]
            res[name] = {
                "n": len(part),
                "hit_rate": sum(1 for r in part if r[1]) / len(part) if part else None,
                "saved": sum(base - mk for _f, _h, mk, base in part),
                "saved_end_effect": sum(
                    (base - mk) * growth(int(f["at_ms"])) for f, _h, mk, base in part
                ),
                "base_cost": sum(base for *_x, base in part),
                "miss_extra_mean": sum(miss) / len(miss) if miss else None,
            }
        res["end_effect_pct_of_net"] = res["all"]["saved_end_effect"] / float(REF_NET)
        out[f"wait_{wait}"] = res
    w = out["wait_30"]
    out["recommend_maker"] = w["h1"]["saved"] > 0 and w["h2"]["saved"] > 0
    OUT.mkdir(parents=True, exist_ok=True)
    with target.open("x", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
