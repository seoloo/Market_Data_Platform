"""실거래 기록 DB — 거래 · 입금 · 하루 평가를 한 곳에 (2026-10-10 사용자 "DB 에 넣어야 하지 않니").

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
    """장부 → 거래 · 입금 줄 (순수). 고유 번호 없는 줄(옛 장부)은 내용으로 번호를 만든다."""
    stamp = now or _now()
    rows: dict[str, dict[str, Any]] = {}
    for book in books:
        for i, t in enumerate(book.trades):
            ref = t.ref or f"{book.key}:{t.kind}:{t.symbol}:{t.date}:{i}"
            rows[ref] = {
                "id": ref,
                "account": book.key,
                "kind": t.kind,
                "trade_date": t.date if _DATE.fullmatch(t.date or "") else None,
                "symbol": t.symbol,
                "qty": t.qty,
                "price": t.price,
                "amount": t.amount,
                "fee": t.fee,
                "currency": t.currency,
                "fx": t.fx,
                "status": t.status,
                "note": t.note,
                "updated_at": stamp,
            }
    return [rows[k] for k in sorted(rows)]


def snapshot_rows(
    history: Iterable[Mapping[str, Any]], now: str | None = None
) -> list[dict[str, Any]]:
    """``data/live/history.jsonl`` 줄 → 하루 평가 줄 (순수)."""
    stamp = now or _now()
    out = []
    for r in history:
        d = str(r.get("date") or "")
        if not _DATE.fullmatch(d):
            continue
        at = r.get("at_ms")
        out.append(
            {
                "date": d,
                "value": r.get("value"),
                "deposits": r.get("deposits"),
                "priced": bool(r.get("priced")),
                "fx": r.get("fx"),
                "books": r.get("books") or {},
                "at": None
                if not at
                else datetime.fromtimestamp(int(at) / 1000, UTC).isoformat(timespec="seconds"),
                "updated_at": stamp,
            }
        )
    return out


def write_sqlite(
    path: Path, events: Sequence[Mapping[str, Any]], snapshots: Sequence[Mapping[str, Any]]
) -> tuple[int, int]:
    """PC 기록 DB 에 upsert. (거래 줄 수, 평가 줄 수) 를 돌려준다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as db, db:
        db.executescript(_SCHEMA)
        db.executemany(
            f"INSERT INTO ledger_events ({', '.join(EVENT_COLUMNS)}) "
            f"VALUES ({', '.join('?' * len(EVENT_COLUMNS))}) "
            "ON CONFLICT(id) DO UPDATE SET "
            + ", ".join(f"{c}=excluded.{c}" for c in EVENT_COLUMNS if c != "id"),
            [tuple(r[c] for c in EVENT_COLUMNS) for r in events],
        )
        db.executemany(
            f"INSERT INTO daily_snapshots ({', '.join(SNAPSHOT_COLUMNS)}) "
            f"VALUES ({', '.join('?' * len(SNAPSHOT_COLUMNS))}) "
            "ON CONFLICT(date) DO UPDATE SET "
            + ", ".join(f"{c}=excluded.{c}" for c in SNAPSHOT_COLUMNS if c != "date"),
            [
                tuple(
                    json.dumps(r[c], ensure_ascii=False)
                    if c == "books"
                    else int(r[c])
                    if c == "priced"
                    else r[c]
                    for c in SNAPSHOT_COLUMNS
                )
                for r in snapshots
            ],
        )
        n_e = db.execute("SELECT COUNT(*) FROM ledger_events").fetchone()[0]
        n_s = db.execute("SELECT COUNT(*) FROM daily_snapshots").fetchone()[0]
    return int(n_e), int(n_s)


Post = Callable[[str, Mapping[str, str], list[dict[str, Any]]], int]


def _post(url: str, headers: Mapping[str, str], body: list[dict[str, Any]]) -> int:
    import httpx

    r = httpx.post(url, headers=dict(headers), json=body, timeout=30.0)
    if r.status_code >= 300:
        # 응답 본문에 키가 들어가지 않는다 (PostgREST 오류 문구만)
        raise RuntimeError(f"Supabase {r.status_code}: {r.text[:300]}")
    return r.status_code


def push_supabase(
    url: str,
    key: str,
    events: Sequence[Mapping[str, Any]],
    snapshots: Sequence[Mapping[str, Any]],
    post: Post = _post,
) -> tuple[int, int]:
    """Supabase 같은 표로 upsert (Data API · Secret 키). 줄 수를 돌려준다."""
    base = url.rstrip("/") + "/rest/v1"
    headers = {
        "apikey": key,
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=minimal",
    }
    if key.startswith("eyJ"):  # 옛 service_role(JWT) 키는 Authorization 도 필요
        headers["Authorization"] = f"Bearer {key}"
    if events:
        post(f"{base}/ledger_events?on_conflict=id", headers, [dict(r) for r in events])
    if snapshots:
        post(f"{base}/daily_snapshots?on_conflict=date", headers, [dict(r) for r in snapshots])
    return len(events), len(snapshots)
