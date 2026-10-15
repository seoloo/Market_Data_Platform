from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from auto_trading.ops.live_ledger import (
    event_rows,
    push_supabase,
    snapshot_rows,
    write_sqlite,
)
from auto_trading.ui.live_home import Book, Quotes, Trade, build_books


@pytest.fixture(autouse=True)
def _show_upbit(monkeypatch: pytest.MonkeyPatch) -> None:
    """이 시험들은 업비트 Vega 장부로 화면 규칙을 본다 (실화면은 2026-10-15 부터 숨김)."""
    from auto_trading.ui import live_home

    monkeypatch.setattr(live_home, "SHOW_UPBIT", True)


def _write(root: Path, rel: str, obj: dict[str, Any]) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj), encoding="utf-8")


def _seed(root: Path, *, status: str = "SENT", filled: float = 0.0) -> None:
    _write(
        root,
        "data/toss/DENEB.json",
        {
            "deposits": [["2026-10-09", 300_000.0, 223.13, 1344.5]],
            "orders": [
                {
                    "date": "2026-10-09",
                    "symbol": "SCHD",
                    "side": "BUY",
                    "qty": 3,
                    "limit": 34.08,
                    "client_id": "M3SCHD-2026-10-09-2",
                    "status": status,
                    "filled_qty": filled,
                    "filled_usd": 99.12 if filled else 0.0,
                    "commission": 0.1 if filled else 0.0,
                }
            ],
        },
    )
    _write(
        root,
        "data/upbit_t2/state.json",
        {
            "deposits": [["2026-10-08", "300000"]],
            "orders": [
                {
                    "id": "t2u-2026-10-08-BTC-B",
                    "identifier": "t2u-2026-10-08-BTC-B",
                    "side": "bid",
                    "executed_volume": "0.001",
                    "funds": "120000",
                    "paid_fee": "60",
                }
            ],
        },
    )
    _write(
        root,
        "data/toss/BTQ.json",
        {
            "symbol": "BTQ",
            "last_qty": 1.0,
            "buys": [{"date": "2026-10-08", "qty": 1, "cost_usd": 3.08, "fx_rate": 1347.84}],
        },
    )


def test_rows_have_stable_ids_and_status_updates_in_place(tmp_path: Path) -> None:
    _seed(tmp_path)
    first = event_rows(build_books(tmp_path, Quotes()), now="t1")
    ids = {r["id"] for r in first}
    assert {
        "deneb:deposit:2026-10-09:0",
        "deneb:order:M3SCHD-2026-10-09-2",
        "vega:deposit:2026-10-08:0",
        "vega:order:t2u-2026-10-08-BTC-B",
        "accumulate:BTQ:buy:2026-10-08:0",
    } == ids
    schd = next(r for r in first if r["id"] == "deneb:order:M3SCHD-2026-10-09-2")
    assert schd["status"].startswith("접수") and schd["currency"] == "USD"
    dep = next(r for r in first if r["id"] == "deneb:deposit:2026-10-09:0")
    assert dep["fx"] == 1344.5 and dep["amount"] == 300_000.0
    db = tmp_path / "data/live_ledger.db"
    assert write_sqlite(db, first, []) == (5, 0)
    # 다음 날 체결되면 같은 번호의 줄만 바뀐다
    _seed(tmp_path, status="FILLED", filled=3.0)
    second = event_rows(build_books(tmp_path, Quotes()), now="t2")
    assert write_sqlite(db, second, []) == (5, 0)
    with sqlite3.connect(db) as c:
        status, qty, fee = c.execute(
            "SELECT status, qty, fee FROM ledger_events WHERE id='deneb:order:M3SCHD-2026-10-09-2'"
        ).fetchone()
    assert (status, qty, fee) == ("체결", 3.0, 0.1)


def test_rows_without_ref_get_content_id_and_bad_dates_become_null() -> None:
    book = Book("x", "X", "", 0, 0, (), trades=(Trade("None", "buy", "AAA", 1, 2, 2),))
    (row,) = event_rows([book], now="t")
    assert row["id"] == "x:buy:AAA:None:0" and row["trade_date"] is None


def test_snapshots_and_supabase_upsert_requests(tmp_path: Path) -> None:
    snaps = snapshot_rows(
        [
            {
                "date": "2026-10-10",
                "value": 636_363,
                "deposits": 639_889,
                "priced": True,
                "at_ms": 0,
            },
            {"date": "bad", "value": 1},
        ],
        now="t",
    )
    assert [s["date"] for s in snaps] == ["2026-10-10"] and snaps[0]["books"] == {}
    assert write_sqlite(tmp_path / "l.db", [], snaps) == (0, 1)
    calls: list[tuple[str, dict[str, str], list[dict[str, Any]]]] = []

    def post(url: str, headers: Any, body: list[dict[str, Any]]) -> int:
        calls.append((url, dict(headers), body))
        return 201

    events = [{"id": "a", "account": "deneb"}]
    assert push_supabase("https://p.supabase.co/", "sb_secret_x", events, snaps, post) == (1, 1)
    assert calls[0][0] == "https://p.supabase.co/rest/v1/ledger_events?on_conflict=id"
    assert calls[1][0].endswith("/daily_snapshots?on_conflict=date")
    h = calls[0][1]
    assert h["apikey"] == "sb_secret_x" and "Authorization" not in h
    assert "merge-duplicates" in h["Prefer"]
    legacy: list[dict[str, str]] = []

    def post_legacy(url: str, headers: Any, body: list[dict[str, Any]]) -> int:
        legacy.append(dict(headers))
        return 201

    push_supabase("https://p.supabase.co", "eyJabc", events, [], post_legacy)
    assert legacy[0]["Authorization"] == "Bearer eyJabc"
