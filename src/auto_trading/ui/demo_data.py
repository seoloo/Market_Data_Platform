"""데모·실거래 준비 화면의 데이터 — DB 는 **읽기 전용**, 보고서·상태 파일은 읽기만 한다.

출처 (docs/research/51·55·56·57·59):

* 데모 현물 T/v2 — ``paper_days`` strategy_id='t2_demo', ``spot_orders`` mode='demo',
  ``reports/demo_t2/*.json``
* 데모 O/v2 선물 추가분 — ``reports/demo_o2/*.json``
* 데모 Elliott BTC — ``reports/demo_ew/state.json``·``events.jsonl``
* 그림자 T/v2 — ``reports/shadow/t2/*.json``
* 긴급 정지 스위치 — ``data/HALT`` (있으면 첫 줄이 사유)

import 는 core·storage 만 한다(UI 는 매매 코드를 import 하지 않는다).
읽다 실패하면 ``errors`` 에 적고 계속한다.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from auto_trading.storage.repositories.paper_days import PaperDay, PaperDayRepository
from auto_trading.storage.repositories.spot_orders import OPEN_STATES, SpotOrderRepository

__all__ = ["DemoOrder", "DemoView", "load_demo"]

DEMO_T2 = "t2_demo"
DEMO_MODE = "demo"


@dataclass(frozen=True, slots=True)
class DemoOrder:
    created_ms: int
    symbol: str
    side: str
    qty: str
    state: str
    filled: str
    quote: str
    fee: str
    maker: bool | None
    """지정가(True)·추격 시장가(False)·모름(None)."""
    cost_bps: float | None
    """중간가 대비 가격 + 수수료 (보고서에 있을 때)."""


@dataclass(frozen=True, slots=True)
class DemoView:
    t2_days: tuple[PaperDay, ...] = ()
    """데모 T/v2 결정 기록 (오래된 것 먼저)."""
    t2_report: dict[str, Any] | None = None
    orders: tuple[DemoOrder, ...] = ()
    """데모 현물 주문 (새것 먼저, 최대 20)."""
    unresolved: int = 0
    o2_report: dict[str, Any] | None = None
    o2_days: int = 0
    ew_state: dict[str, Any] | None = None
    ew_events: tuple[dict[str, Any], ...] = ()
    """최근 사건 (새것 먼저, 최대 30)."""
    shadow: dict[str, Any] | None = None
    shadow_days: int = 0
    halt: str | None = None
    errors: tuple[str, ...] = field(default_factory=tuple)


def _latest_json(folder: Path, errors: list[str], label: str) -> tuple[dict[str, Any] | None, int]:
    paths = sorted(folder.glob("20*.json")) if folder.exists() else []
    if not paths:
        return None, 0
    try:
        return json.loads(paths[-1].read_text(encoding="utf-8")), len(paths)
    except (OSError, ValueError) as exc:
        errors.append(f"{label} 보고서 읽기 실패 {paths[-1].name}: {type(exc).__name__}")
        return None, len(paths)


def _costs(report: dict[str, Any] | None) -> dict[str, tuple[bool, float | None]]:
    """보고서의 주문 ID → (지정가만 체결?, 총 비용 bp)."""
    out: dict[str, tuple[bool, float | None]] = {}
    for o in (report or {}).get("orders", []):
        cost = None
        if o.get("cost_price_bps") not in (None, "None"):
            cost = float(o["cost_price_bps"]) + float(o["cost_fee_bps"])
        out[str(o.get("client_id"))] = (o.get("chased") != "True", cost)
    return out


def _legacy_orders(path: Path, errors: list[str]) -> list[DemoOrder]:
    """첫 실행(2026-09-25, 옛 데모 스크립트)의 주문 — DB 가 아니라 JSON 줄에 남아 있다."""
    if not path.exists():
        return []
    out = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            for e in row.get("executed", []):
                cost = None
                if e.get("is_price_bps") is not None:
                    cost = float(e["is_price_bps"]) + float(e["is_fee_bps"])
                filled = float(e["maker_qty"]) + float(e["taker_qty"])
                quote = filled * float(e["avg_price"]) if e.get("avg_price") else 0.0
                out.append(
                    DemoOrder(
                        int(row["created_ms"]),
                        str(e["symbol"]),
                        str(e["side"]),
                        str(e["qty"]),
                        "FILLED" if filled else "?",
                        str(filled),
                        f"{quote:.2f}",
                        str(e.get("fee_usdt", "")),
                        not e.get("chased", False),
                        cost,
                    )
                )
    except (OSError, ValueError, KeyError, TypeError) as exc:
        errors.append(f"첫 데모 기록 읽기 실패: {type(exc).__name__}")
    return out


def load_demo(root: Path, conn: sqlite3.Connection | None) -> DemoView:
    errors: list[str] = []
    reports = root / "reports"
    t2_report, _ = _latest_json(reports / "demo_t2", errors, "데모 T/v2")
    costs = _costs(t2_report)
    days: Sequence[PaperDay] = ()
    orders: list[DemoOrder] = []
    unresolved = 0
    if conn is not None:
        try:
            days = list(reversed(PaperDayRepository(conn).recent(DEMO_T2, 400)))
            records = [r for r in SpotOrderRepository(conn).all() if r.mode == DEMO_MODE]
            unresolved = sum(1 for r in records if r.state in OPEN_STATES)
            for r in sorted(records, key=lambda x: x.created_ms, reverse=True)[:20]:
                parent = r.client_order_id.removesuffix("m")
                maker, cost = costs.get(parent, (None, None))
                if r.client_order_id.endswith("m"):
                    maker = False
                orders.append(
                    DemoOrder(
                        r.created_ms,
                        r.symbol,
                        r.side,
                        str(r.qty),
                        r.state,
                        str(r.filled_qty),
                        str(r.quote_qty),
                        str(r.fee),
                        maker,
                        cost,
                    )
                )
        except (sqlite3.Error, KeyError, ValueError, TypeError) as exc:
            errors.append(f"데모 원장 읽기 실패: {type(exc).__name__}: {exc}")
    orders.extend(_legacy_orders(reports / "demo_t2" / "days.jsonl", errors))
    orders.sort(key=lambda o: o.created_ms, reverse=True)
    o2_report, o2_days = _latest_json(reports / "demo_o2", errors, "데모 O/v2")
    shadow, shadow_days = _latest_json(reports / "shadow" / "t2", errors, "그림자")
    ew_state: dict[str, Any] | None = None
    events: list[dict[str, Any]] = []
    ew = reports / "demo_ew"
    try:
        if (ew / "state.json").exists():
            ew_state = json.loads((ew / "state.json").read_text(encoding="utf-8"))
        if (ew / "events.jsonl").exists():
            lines = (ew / "events.jsonl").read_text(encoding="utf-8").splitlines()
            events = [json.loads(x) for x in lines[-30:] if x.strip()]
    except (OSError, ValueError) as exc:
        errors.append(f"Elliott 데모 기록 읽기 실패: {type(exc).__name__}")
    halt = None
    halt_path = root / "data" / "HALT"
    if halt_path.exists():
        try:
            first = halt_path.read_text(encoding="utf-8").strip().splitlines()
            halt = first[0].strip() if first and first[0].strip() else "사유 없음"
        except (OSError, UnicodeDecodeError):
            halt = "정지 파일을 읽을 수 없음"
    return DemoView(
        t2_days=tuple(days),
        t2_report=t2_report,
        orders=tuple(orders),
        unresolved=unresolved,
        o2_report=o2_report,
        o2_days=o2_days,
        ew_state=ew_state,
        ew_events=tuple(reversed(events)),
        shadow=shadow,
        shadow_days=shadow_days,
        halt=halt,
        errors=tuple(errors),
    )
