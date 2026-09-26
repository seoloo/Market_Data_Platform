"""문서 92 — Binance 스테이킹 캐리(WBETH·BNSOL + 같은 수량 무기한 숏) 과거 평가. 1 회 판정.

사용 예::

    python -m scripts.run_staking_carry check   # 자료 해시·결측만 확인 (수익 미계산)
    python -m scripts.run_staking_carry run     # 사용자 승인 뒤 1 회 판정

자료: Git 제외 ``data/research/staking/`` 스냅숏(해시 확인)
+ ``data/trading.db`` 현물 1h (읽기 전용).
판정 파일 ``reports/research/staking/carry-92/verdict.json`` 이 있으면 다시 돌지 않는다.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from dataclasses import asdict, replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from scripts.observe_staking_ratio import load_ratios
from scripts.run_a1_train import git_state

from auto_trading.research.staking_carry import (
    DAY_MS,
    HOUR_MS,
    Bar,
    CarryConfig,
    CarryResult,
    Funding,
    longest_negative_funding_days,
    mdd,
    simulate_carry,
    value_at,
)

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "data" / "research" / "staking"
DB = REPO / "data" / "trading.db"
OUT = REPO / "reports" / "research" / "staking" / "carry-92" / "verdict.json"
FUNDING_MANIFEST = DATA / "funding-manifest-1790416904496.json"
MARK_MANIFEST = DATA / "mark-manifest-1790417586155.json"
START_MS = int(datetime(2024, 9, 13, tzinfo=UTC).timestamp() * 1000)
END_MS = int(datetime(2026, 8, 31, tzinfo=UTC).timestamp() * 1000)
DAYS = (END_MS - START_MS) // DAY_MS  # 717
CASH_YIELD = 0.0308
"""같은 돈의 USDT 유동형 예치 가정 (문서 29)."""
SEGMENTS = ("2025-01-01", "2026-01-01")
SYMBOLS = {"ETH": "ETHUSDT", "SOL": "SOLUSDT"}
REALISTIC = {"spot_cost": 0.0011, "perp_cost": 0.00023}


def _ms(text: str) -> int:
    return int(datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=UTC).timestamp() * 1000)


def _iso(ms: int | None) -> str | None:
    return None if ms is None else datetime.fromtimestamp(ms / 1000, UTC).isoformat()


def _snapshot(manifest: Path, asset: str) -> list[dict[str, Any]]:
    meta = json.loads(manifest.read_text(encoding="utf-8"))["assets"][asset]
    blob = (DATA / meta["path"]).read_bytes()
    if hashlib.sha256(blob).hexdigest() != meta["sha256"]:
        raise SystemExit(f"{meta['path']} 해시 불일치 — 판정하지 않는다")
    rows = json.loads(blob)
    if not isinstance(rows, list):
        raise SystemExit(f"{meta['path']} 형식 불일치")
    return rows


def load(asset: str) -> tuple[list[Bar], list[Funding], tuple[float, float], dict[date, float]]:
    marks = {int(r["open_ms"]): r for r in _snapshot(MARK_MANIFEST, asset)}
    conn = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)
    try:
        conn.execute("PRAGMA query_only=ON")
        spot = dict(
            conn.execute(
                "SELECT open_ms, CAST(close AS REAL) FROM candles WHERE exchange='binance_spot'"
                " AND symbol=? AND timeframe='1h' AND open_ms>=? AND open_ms<?",
                (SYMBOLS[asset], START_MS - HOUR_MS, END_MS),
            ).fetchall()
        )
    finally:
        conn.close()
    hours = range(START_MS, END_MS, HOUR_MS)
    missing = [h for h in [START_MS - HOUR_MS, *hours] if h not in spot or h not in marks]
    if missing:
        raise SystemExit(
            f"{asset} 1h 결측 {len(missing)} (첫 {_iso(missing[0])}) — 판정하지 않는다"
        )
    bars = [
        Bar(h, float(spot[h]), float(marks[h]["close"]), float(marks[h]["high"])) for h in hours
    ]
    start = (float(spot[START_MS - HOUR_MS]), float(marks[START_MS - HOUR_MS]["close"]))
    funding = [
        Funding(int(r["fundingTime"]), float(r["fundingRate"]), float(r["markPrice"]))
        for r in _snapshot(FUNDING_MANIFEST, asset)
        if START_MS < int(r["fundingTime"]) <= END_MS
    ]
    ratios = {d: float(v) for d, v in load_ratios()[asset].items()}
    return bars, funding, start, ratios


def _ratio_fn(ratios: dict[date, float] | None) -> Any:
    if ratios is None:
        return lambda _t: 1.0
    return lambda t: ratios[datetime.fromtimestamp(t / 1000, UTC).date()]


def describe(res: CarryResult, cfg: CarryConfig) -> dict[str, Any]:
    cagr = (res.final_equity / cfg.capital) ** (365 / DAYS) - 1 if res.final_equity > 0 else -1.0
    worst, peak_t, trough_t = mdd(res.equity)
    worst30 = 0.0
    eq = res.equity
    for i in range(30 * 24, len(eq)):
        worst30 = min(worst30, eq[i][1] / eq[i - 30 * 24][1] - 1)
    segs, prev = [], float(cfg.capital)
    for s in (*SEGMENTS, None):
        v = value_at(eq, _ms(s)) if s else res.final_equity
        segs.append(None if v is None else v / prev - 1)
        prev = v or prev
    return {
        "final_equity": res.final_equity,
        "cagr": cagr,
        "liquidated_at": _iso(res.liquidated_ms),
        "min_buffer": res.min_buffer,
        "min_buffer_at": _iso(res.min_buffer_ms),
        "mdd": worst,
        "mdd_span": [_iso(peak_t), _iso(trough_t)],
        "worst_30d": worst30,
        "funding": res.funding,
        "costs": res.costs,
        "weekly_rebalances": res.weekly_rebalances,
        "emergency_rebalances": res.emergency_rebalances,
        "segments": dict(zip(("2024-09-13~2025", "2025", "2026~08-31"), segs, strict=True)),
    }


def check() -> int:
    for asset in SYMBOLS:
        bars, funding, _start, ratios = load(asset)
        print(f"{asset}: 1h {len(bars)} · funding {len(funding)} · 비율 {len(ratios)}일 — 통과")
    return 0


def run() -> int:
    if OUT.exists():
        print(f"이미 판정이 있다: {OUT}. 사전 등록상 1 회만.")
        return 3
    sha, dirty = git_state()
    if dirty:
        print("작업 트리에 커밋되지 않은 변경이 있다. 재현 가능한 코드로만 실행한다.")
        return 3
    base = CarryConfig()
    record: dict[str, Any] = {"git_sha": sha, "executed_at": datetime.now(tz=UTC).isoformat()}
    record["config"] = asdict(base) | {"cash_yield": CASH_YIELD, "days": DAYS}
    for asset in SYMBOLS:
        bars, funding, start, ratios = load(asset)
        out: dict[str, Any] = {}
        for name, rfn in (("C", _ratio_fn(ratios)), ("B0", _ratio_fn(None))):
            out[name] = describe(
                simulate_carry(bars, funding, rfn, start_price=start, cfg=base), base
            )
            real = replace(base, **REALISTIC)
            out[f"{name}_realistic"] = describe(
                simulate_carry(bars, funding, rfn, start_price=start, cfg=real), real
            )
        neg_days, neg_end = longest_negative_funding_days(funding)
        out["longest_negative_30d_funding_days"] = [neg_days, _iso(neg_end)]
        out["staking_increment_cagr"] = out["C"]["cagr"] - out["B0"]["cagr"]
        c = out["C"]
        criteria = [
            {"name": "S1", "passed": c["cagr"] > CASH_YIELD, "detail": {"cagr": c["cagr"]}},
            {"name": "S2", "passed": c["liquidated_at"] is None, "detail": c["min_buffer"]},
        ]
        out["verdict"] = {
            "status": "PASS" if all(x["passed"] for x in criteria) else "REJECT",
            "criteria": criteria,
        }
        record[asset] = out
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(record, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    for asset in SYMBOLS:
        v = record[asset]["verdict"]
        print(
            f"{asset}: {v['status']} · "
            + " · ".join(f"{x['name']} {'통과' if x['passed'] else '실패'}" for x in v["criteria"])
        )
    print(f"기록: {OUT}")
    return 0


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in ("check", "run"):
        raise SystemExit("usage: python -m scripts.run_staking_carry check | run")
    return check() if sys.argv[1] == "check" else run()


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        _r = getattr(_s, "reconfigure", None)
        if _r is not None:
            _r(encoding="utf-8", errors="replace")
    sys.exit(main())
