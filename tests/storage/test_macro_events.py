"""거시 일정 DB 읽기는 인과적이며 coverage를 암묵적으로 만들지 않는다."""

from __future__ import annotations

import sqlite3

import pytest

from auto_trading.core.errors import DataIntegrityError
from auto_trading.storage.repositories.macro_events import MacroEventRepository


@pytest.fixture
def conn() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute(
        "CREATE TABLE macro_events (indicator TEXT, period TEXT, scheduled_ms INTEGER,"
        " vintage_ms INTEGER, source TEXT)"
    )
    yield connection
    connection.close()


def test_empty_table_does_not_synthesize_calendar(conn: sqlite3.Connection) -> None:
    assert (
        MacroEventRepository(conn).schedule_revisions(indicators=frozenset({"CPI"}), as_of_ms=100)
        == ()
    )


@pytest.mark.leakage
def test_only_versions_published_by_cutoff_are_loaded(conn: sqlite3.Connection) -> None:
    conn.executemany(
        "INSERT INTO macro_events VALUES (?, ?, ?, ?, ?)",
        [
            ("CPI", "2021-01", 60, 10, "BLS-v1"),
            ("CPI", "2021-01", 70, 110, "BLS-v2"),
            ("PCE", "2021-01", 80, 10, "BEA"),
        ],
    )
    repo = MacroEventRepository(conn)
    before = repo.schedule_revisions(indicators=frozenset({"CPI"}), as_of_ms=100)
    assert len(before) == 1
    assert before[0].scheduled_ms == 60
    after = repo.schedule_revisions(indicators=frozenset({"CPI"}), as_of_ms=120)
    assert [event.scheduled_ms for event in after] == [60, 70]


@pytest.mark.parametrize("period, source", [(None, "BLS"), ("", "BLS"), ("2021-01", "")])
def test_missing_event_identity_or_source_is_rejected(
    conn: sqlite3.Connection, period: str | None, source: str
) -> None:
    conn.execute("INSERT INTO macro_events VALUES (?, ?, ?, ?, ?)", ("CPI", period, 60, 10, source))
    with pytest.raises(DataIntegrityError):
        MacroEventRepository(conn).schedule_revisions(indicators=frozenset({"CPI"}), as_of_ms=100)
