"""AG Paper (문서 117) — 112 소액 공격형 AG-US · AG-CR · AG-MIX 를 1,000 USD 가상 계좌로 기록.

주문 없음.

사용 예::

    python -m scripts.ag_paper daily    # AG-CR 원장 하루 진행 + 보고서 (예약 작업용)
    python -m scripts.ag_paper report   # 보고서만 다시 (원장은 그대로)
    python -m scripts.ag_paper status

- AG-US 는 QS Paper(``data/qs_paper/state.json``)의 AG 장부를 읽는다
  — QS Paper 가 먼저 돌아야 그날 값이 들어간다.
- AG-CR 은 오버레이 Paper 원장 ``ag_cr3`` (``run_paper_o1 --ledger ag`` 와 같은 실행기,
  공개 시세만).
보고 ``reports/ag_paper/<날짜>.json`` · ``reports/ag_paper/latest.json``.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scripts.run_paper_o1 import DB, LEDGERS
from scripts.run_paper_o1 import run as run_overlay

from auto_trading.ops.ag_paper import Path_, mix, scale, summary
from auto_trading.storage.db import connect_read_only
from auto_trading.storage.repositories.paper_overlay_days import OverlayPaperDayRepository

REPO = Path(__file__).resolve().parent.parent
QS_STATE = REPO / "data" / "qs_paper" / "state.json"
REPORTS = REPO / "reports" / "ag_paper"


def _date(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=UTC).strftime("%Y-%m-%d")


def _paths() -> tuple[Path_, Path_, dict[str, Any], dict[str, Any]]:
    qs = json.loads(QS_STATE.read_text(encoding="utf-8")) if QS_STATE.exists() else {}
    ag_book = (qs.get("books") or {}).get("AG") or {}
    us = {str(d): float(v) for d, v in ag_book.get("equity") or []}
    conn = connect_read_only(DB)
    try:
        rows = OverlayPaperDayRepository(conn).all(LEDGERS["ag"].strategy_id)
    finally:
        conn.close()
    cr = scale({_date(r.decision_ms): r.equity for r in rows})
    latest = rows[-1] if rows else None
    cr_info: dict[str, Any] = {}
    if latest is not None:
        cr_info = {
            "status": latest.status,
            "exposure": latest.tally.get("exposure"),
            "next_target_weights": latest.state.get("target"),
            "liquidations": latest.tally.get("liquidations"),
            "stop_hits": latest.tally.get("stop_hits"),
            "note": latest.note,
        }
    us_info = {
        "tqqq_shares": ag_book.get("tqqq"),
        "cash_usd": ag_book.get("cash"),
        "trades": (ag_book.get("trades") or [])[-10:],
        "tax_paid_usd": ag_book.get("tax_paid"),
    }
    return us, cr, us_info, cr_info


def report() -> dict[str, object]:
    us, cr, us_info, cr_info = _paths()
    mixed = mix(us, cr)
    out: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "books": {
            "AG-US": {**summary(us), **us_info},
            "AG-CR": {**summary(cr), **cr_info},
            "AG-MIX": summary(mixed),
        },
        "paths": {
            "AG-US": {d: round(v, 2) for d, v in sorted(us.items())},
            "AG-CR": {d: round(v, 2) for d, v in sorted(cr.items())},
            "AG-MIX": {d: round(v, 2) for d, v in sorted(mixed.items())},
        },
    }
    day = max((str(b.get("date")) for b in out["books"].values() if b.get("date")), default=None)
    out["date"] = day
    REPORTS.mkdir(parents=True, exist_ok=True)
    text = json.dumps(out, ensure_ascii=False, indent=1)
    if day:
        (REPORTS / f"{day}.json").write_text(text, encoding="utf-8")
    (REPORTS / "latest.json").write_text(text, encoding="utf-8")
    return {k: b.get("value_usd") for k, b in out["books"].items()}


def status() -> dict[str, object]:
    latest = REPORTS / "latest.json"
    if not latest.exists():
        return {"note": "아직 실행 전"}
    data = json.loads(latest.read_text(encoding="utf-8"))
    return {"date": data.get("date"), "books": data.get("books")}


def main(argv: list[str]) -> int:
    if argv == ["daily"]:
        code = run_overlay(LEDGERS["ag"])
        result: dict[str, object] = {"ag_cr_ledger_exit": code, "report": report()}
    elif argv == ["report"]:
        result, code = report(), 0
    elif argv == ["status"]:
        result, code = status(), 0
    else:
        raise SystemExit("usage: python -m scripts.ag_paper daily | report | status")
    sys.stdout.write(json.dumps(result, ensure_ascii=True, indent=1) + "\n")
    return code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
