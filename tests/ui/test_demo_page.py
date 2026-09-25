"""대시보드 '데모 · 실거래 준비' 화면 — 데이터 읽기·화면·이스케이프."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from auto_trading.storage.db import connect
from auto_trading.storage.migrations import apply_migrations
from auto_trading.storage.repositories.paper_days import PaperDay, PaperDayRepository
from auto_trading.storage.repositories.spot_orders import SpotOrderRecord, SpotOrderRepository
from auto_trading.ui.data import load_dashboard
from auto_trading.ui.render import render_page

EVIL = "<script>alert(1)</script>"
NOW = 1_790_320_000_000
DAY = 86_400_000


def build(tmp_path: Path, *, halt: bool = False, unresolved: bool = False) -> Path:
    (tmp_path / "data").mkdir()
    conn = connect(tmp_path / "data" / "trading.db")
    apply_migrations(conn, now_ms=1)
    days = PaperDayRepository(conn)
    for k, equity in enumerate(("5000", "5040")):
        days.insert(
            PaperDay(
                "t2_demo",
                NOW - (2 - k) * DAY,
                "OK",
                Decimal(equity),
                {"BTC": "0.02", "USDT": "3000"},
                {},
                {"BTCUSDT": "0.3"},
                [],
                EVIL,
                1,
            )
        )
    orders = SpotOrderRepository(conn)
    orders.insert(
        SpotOrderRecord(
            "t2d-1-BTCUSDT-B",
            "demo",
            "t2_demo",
            1,
            "BTCUSDT",
            "BUY",
            Decimal("0.01"),
            Decimal("0.3"),
            "SUBMITTED" if unresolved else "FILLED",
            NOW - 3_600_000,
            NOW,
            "x1",
            Decimal("0.01"),
            Decimal("840"),
        )
    )
    conn.close()
    t2 = tmp_path / "reports" / "demo_t2"
    t2.mkdir(parents=True)
    (t2 / "2026-09-25.json").write_text(
        json.dumps(
            {
                "orders": [
                    {
                        "client_id": "t2d-1-BTCUSDT-B",
                        "chased": "False",
                        "cost_price_bps": "-0.2",
                        "cost_fee_bps": "10.0",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (t2 / "days.jsonl").write_text(
        json.dumps(
            {
                "created_ms": NOW - 2 * DAY,
                "executed": [
                    {
                        "symbol": "ETHUSDT",
                        "side": "BUY",
                        "qty": "0.47",
                        "maker_qty": "0.47",
                        "taker_qty": "0",
                        "avg_price": "2679",
                        "fee_usdt": "1.2",
                        "chased": False,
                        "is_price_bps": "-0.0",
                        "is_fee_bps": "10.0",
                    }
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    o2 = tmp_path / "reports" / "demo_o2"
    o2.mkdir(parents=True)
    (o2 / "2026-09-24.json").write_text(
        json.dumps(
            {
                "decision_date": "2026-09-24",
                "plans": [{"weight": "0.25"}],
                "report": {
                    "state": "OK",
                    "fired_stops": [],
                    "notes": [EVIL],
                    "legs": [
                        {
                            "symbol": "ETHUSDT",
                            "before": "0",
                            "after": "0.236",
                            "stop": "2149.32 교체",
                            "orders": [EVIL],
                        }
                    ],
                },
            }
        ),
        encoding="utf-8",
    )
    ew = tmp_path / "reports" / "demo_ew"
    ew.mkdir(parents=True)
    (ew / "state.json").write_text(
        json.dumps(
            {
                "phase": "PENDING",
                "last_scan_ms": NOW - 600_000,
                "entry": "61000",
                "stop": "58000",
                "target": "70708",
            }
        ),
        encoding="utf-8",
    )
    (ew / "events.jsonl").write_text(
        json.dumps(
            {
                "kind": "trigger",
                "at_ms": NOW - 700_000,
                "scenarios": 12,
                "setup": {"direction": 1, "agree": 7, "top_n": 10, "entry": "61000"},
            }
        )
        + "\n"
        + json.dumps({"kind": "error", "at_ms": NOW - 1000, "error": EVIL})
        + "\n",
        encoding="utf-8",
    )
    if halt:
        (tmp_path / "data" / "HALT").write_text("거래소 점검\n", encoding="utf-8")
    return tmp_path


def test_reads_every_demo_source(tmp_path: Path) -> None:
    data = load_dashboard(build(tmp_path), NOW)
    d = data.demo
    assert len(d.t2_days) == 2 and d.unresolved == 0 and d.halt is None
    assert [o.symbol for o in d.orders] == ["BTCUSDT", "ETHUSDT"]  # DB 주문 + 첫날 JSON 기록
    assert d.orders[0].maker is True and d.orders[0].cost_bps == 9.8
    assert d.o2_report is not None and d.ew_state is not None
    assert d.ew_events[0]["kind"] == "error"  # 새것 먼저
    assert data.errors == ()


def test_demo_page_renders_and_escapes(tmp_path: Path) -> None:
    html = render_page(load_dashboard(build(tmp_path), NOW), "demo")
    assert EVIL not in html and "&lt;script&gt;" in html
    for text in (
        "현물 T/v2",
        "선물 O/v2 추가분",
        "Elliott BTC 5x",
        "예약 중",
        "실거래 1단계 준비",
        "7/10 합의",
        "ETH 0.236",
        "10.0bp",
    ):
        assert text in html, text


def test_overview_links_to_demo_and_flags_problems(tmp_path: Path) -> None:
    root = build(tmp_path, halt=True, unresolved=True)
    data = load_dashboard(root, NOW)
    assert data.demo.unresolved == 1 and data.demo.halt == "거래소 점검"
    overview = render_page(data, "overview")
    assert 'href="/demo"' in overview and "미해결 주문 1" in overview
    assert "긴급 정지: 거래소 점검" in overview
    assert "켜짐" in render_page(data, "demo")


def test_no_demo_files_still_renders(tmp_path: Path) -> None:
    (tmp_path / "data").mkdir()
    conn = connect(tmp_path / "data" / "trading.db")
    apply_migrations(conn, now_ms=1)
    conn.close()
    html = render_page(load_dashboard(tmp_path, NOW), "demo")
    assert "기록 없음" in html
