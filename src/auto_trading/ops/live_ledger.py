"""실거래 기록 DB — 거래 · 입금 · 하루 평가를 한 곳에 (2026-10-10 사용자 "DB 에 넣어야 하지 않니").

언제 쓰나: 매일 23:00 ``AutoTrading_Ledger_Sync`` (``scripts/ledger_sync.py``) 가 부른다.
읽고 쓰는 것: 장부(``ui.live_home.Book`` 으로 읽은 것) · ``data/live/history.jsonl`` →
              ``data/live_ledger.db`` (SQLite) · Supabase 같은 두 표.

* 원본은 지금처럼 실행기의 JSON 장부(``data/toss/*.json`` · ``data/upbit_t2/state.json``)다.
  실거래 실행기는 건드리지 않고, 이 모듈이 장부를 읽어 기록 DB 로 옮긴다.
* PC: SQLite ``data/live_ledger.db`` (인터넷 없이도 쌓임, 매일 백업에 들어감).
* Supabase: 같은 두 표를 한 방향으로 올린다(사용자가 써 보고 싶어 함 · 나중에 폰 앱 창구).
  표 만들기 SQL 은 ``docs/supabase/schema.sql`` — Supabase SQL 편집기에서 한 번 실행.
* 줄마다 고유 번호(``id``)가 있어 여러 번 돌려도 겹치지 않는다. 같은 주문이 '접수 → 체결' 로
  바뀌면 그 줄만 바뀐다(upsert).
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Callable, Iterable, Mapping, Sequence
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from auto_trading.ui.live_home import Book

__all__ = [
    "EVENT_COLUMNS",
    "SNAPSHOT_COLUMNS",
    "event_rows",
    "push_supabase",
    "snapshot_rows",
    "write_sqlite",
]

DB_PATH = Path("data") / "live_ledger.db"
EVENT_COLUMNS = (
    "id",
    "account",
    "kind",
    "trade_date",
    "symbol",
    "qty",
    "price",
    "amount",
    "fee",
    "currency",
    "fx",
    "status",
    "note",
    "updated_at",
)
SNAPSHOT_COLUMNS = ("date", "value", "deposits", "priced", "fx", "books", "at", "updated_at")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
"""YYYY-MM-DD 날짜 모양 (아니면 날짜 칸을 비운다)."""

_SCHEMA = """
CREATE TABLE IF NOT EXISTS ledger_events (
    id TEXT PRIMARY KEY,
    account TEXT NOT NULL,
    kind TEXT NOT NULL,
    trade_date TEXT,
    symbol TEXT,
    qty REAL,
    price REAL,
    amount REAL,
    fee REAL,
    currency TEXT,
    fx REAL,
    status TEXT,
    note TEXT,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ledger_events_date ON ledger_events (trade_date);
CREATE TABLE IF NOT EXISTS daily_snapshots (
    date TEXT PRIMARY KEY,
    value REAL,
    deposits REAL,
    priced INTEGER,
    fx REAL,
    books TEXT,
    at TEXT,
    updated_at TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def event_rows(books: Iterable[Book], now: str | None = None) -> list[dict[str, Any]]:
    """[쓰임] 장부들 → 거래 · 입금 줄 목록 (계산만, 파일 안 씀). 번호 순으로 정렬.
    [이유] 고유 번호(``ref``)가 없는 옛 장부 줄은 내용(계좌 · 종류 · 종목 · 날짜 · 순번)으로
           번호를 만든다 — 여러 번 돌려도 같은 번호라 DB 에 겹쳐 쌓이지 않는다."""
    stamp = now or _now()
    rows_by_id: dict[str, dict[str, Any]] = {}
    for book in books:
        for index, trade in enumerate(book.trades):
            row_id = trade.ref or f"{book.key}:{trade.kind}:{trade.symbol}:{trade.date}:{index}"
            rows_by_id[row_id] = {
                "id": row_id,
                "account": book.key,
                "kind": trade.kind,
                "trade_date": trade.date if _DATE.fullmatch(trade.date or "") else None,
                "symbol": trade.symbol,
                "qty": trade.qty,
                "price": trade.price,
                "amount": trade.amount,
                "fee": trade.fee,
                "currency": trade.currency,
                "fx": trade.fx,
                "status": trade.status,
                "note": trade.note,
                "updated_at": stamp,
            }
    return [rows_by_id[row_id] for row_id in sorted(rows_by_id)]


def snapshot_rows(
    history: Iterable[Mapping[str, Any]], now: str | None = None
) -> list[dict[str, Any]]:
    """[쓰임] ``data/live/history.jsonl`` 줄들 → 하루 평가 줄 목록 (계산만).

    날짜가 YYYY-MM-DD 가 아닌 줄은 뺀다."""
    stamp = now or _now()
    rows = []
    for record in history:
        day = str(record.get("date") or "")
        if not _DATE.fullmatch(day):
            continue
        at_ms = record.get("at_ms")
        recorded_at = None
        if at_ms:
            recorded_at = datetime.fromtimestamp(int(at_ms) / 1000, UTC).isoformat(
                timespec="seconds"
            )
        rows.append(
            {
                "date": day,
                "value": record.get("value"),
                "deposits": record.get("deposits"),
                "priced": bool(record.get("priced")),
                "fx": record.get("fx"),
                "books": record.get("books") or {},
                "at": recorded_at,
                "updated_at": stamp,
            }
        )
    return rows


def _upsert_sql(table: str, columns: Sequence[str], key: str) -> str:
    """[쓰임] '없으면 넣고, 같은 번호가 있으면 바꾼다' (upsert) SQL 한 줄."""
    names = ", ".join(columns)
    marks = ", ".join("?" * len(columns))
    updates = ", ".join(f"{column}=excluded.{column}" for column in columns if column != key)
    return (
        f"INSERT INTO {table} ({names}) VALUES ({marks}) ON CONFLICT({key}) DO UPDATE SET {updates}"
    )


def _snapshot_values(row: Mapping[str, Any]) -> tuple[Any, ...]:
    """[쓰임] 하루 평가 줄 → SQLite 에 넣을 값.

    장부별 값 ``books`` 는 JSON 글자로, ``priced`` 는 0 · 1 로."""
    values: list[Any] = []
    for column in SNAPSHOT_COLUMNS:
        if column == "books":
            values.append(json.dumps(row[column], ensure_ascii=False))
        elif column == "priced":
            values.append(int(row[column]))
        else:
            values.append(row[column])
    return tuple(values)


def write_sqlite(
    path: Path, events: Sequence[Mapping[str, Any]], snapshots: Sequence[Mapping[str, Any]]
) -> tuple[int, int]:
    """[쓰임] PC 기록 DB 에 upsert 하고 DB 전체 (거래 줄 수, 평가 줄 수) 를 돌려준다.
    [이유] 표가 없으면 먼저 만든다 — 새 PC 에서도 그냥 돌리면 되게."""
    path.parent.mkdir(parents=True, exist_ok=True)
    event_values = [tuple(row[column] for column in EVENT_COLUMNS) for row in events]
    snapshot_values = [_snapshot_values(row) for row in snapshots]
    with closing(sqlite3.connect(path)) as db, db:
        db.executescript(_SCHEMA)
        db.executemany(_upsert_sql("ledger_events", EVENT_COLUMNS, "id"), event_values)
        db.executemany(_upsert_sql("daily_snapshots", SNAPSHOT_COLUMNS, "date"), snapshot_values)
        event_count = db.execute("SELECT COUNT(*) FROM ledger_events").fetchone()[0]
        snapshot_count = db.execute("SELECT COUNT(*) FROM daily_snapshots").fetchone()[0]
    return int(event_count), int(snapshot_count)


Post = Callable[[str, Mapping[str, str], list[dict[str, Any]]], int]
"""Supabase 로 보내는 함수 모양 (주소, 머리말, 줄들) → HTTP 상태. 시험에서 바꿔 끼운다."""


def _post(url: str, headers: Mapping[str, str], body: list[dict[str, Any]]) -> int:
    """[쓰임] Supabase 로 줄들을 POST. 실패(300 이상)면 오류."""
    import httpx

    response = httpx.post(url, headers=dict(headers), json=body, timeout=30.0)
    if response.status_code >= 300:
        # 응답 본문에 키가 들어가지 않는다 (PostgREST 오류 문구만)
        raise RuntimeError(f"Supabase {response.status_code}: {response.text[:300]}")
    return response.status_code


def push_supabase(
    url: str,
    key: str,
    events: Sequence[Mapping[str, Any]],
    snapshots: Sequence[Mapping[str, Any]],
    post: Post = _post,
) -> tuple[int, int]:
    """[쓰임] Supabase 같은 두 표로 upsert (Data API · Secret 키). 보낸 (거래, 평가) 줄 수.
    [이유] 나중에 폰 앱에서 기록을 보려고 한 방향으로만 올린다 (2026-10-10 사용자)."""
    base = url.rstrip("/") + "/rest/v1"
    headers = {
        "apikey": key,
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=minimal",
    }
    if key.startswith("eyJ"):  # 옛 service_role(JWT) 키는 Authorization 도 필요
        headers["Authorization"] = f"Bearer {key}"
    if events:
        post(f"{base}/ledger_events?on_conflict=id", headers, [dict(row) for row in events])
    if snapshots:
        post(f"{base}/daily_snapshots?on_conflict=date", headers, [dict(row) for row in snapshots])
    return len(events), len(snapshots)
