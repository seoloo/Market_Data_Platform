"""상태 화면에 들어갈 숫자 읽기 — Delta 표는 DuckDB 로, 실행 기록 · 대사는 PostgreSQL 에서.

언제 쓰나: :mod:`mdp.ui.report` 가 화면을 만들 때. 읽기만 한다.
읽고 쓰는 것: ``<lake>/silver/*`` · ``<lake>/gold/*`` Delta 표 · PostgreSQL ``pipeline_runs`` ·
``reconcile_daily_latest`` (읽기만).

[이유] 영역마다 따로 읽고, 실패하면 그 영역에만 이유를 남긴다 — 표 하나가 없거나 DB 가 꺼져 있어도
       나머지 화면은 나온다(아직 만들지 않은 단계가 있는 레이크 · 키 없는 샘플에서도 열리게).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Any

from mdp.query.duck import connect
from mdp.spark.session import lake_root

REPO_ROOT = Path(__file__).resolve().parents[3]

__all__ = ["DeltaTable", "PageData", "Section", "collect", "delta_tables"]

LAYERS = ("silver", "gold")


@dataclass(frozen=True, slots=True)
class DeltaTable:
    """[쓰임] Delta 표 하나의 겉모습 — 계층 · 이름 · 최신 버전 · 마지막 커밋."""

    layer: str
    name: str
    version: int
    committed_at: datetime | None
    operation: str
    rows: int | None = None


@dataclass(slots=True)
class Section:
    """[쓰임] 화면 한 영역의 줄들 — 읽기에 실패하면 ``error`` 에 이유."""

    rows: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None


@dataclass(slots=True)
class PageData:
    """[쓰임] 화면 하나에 들어갈 모든 것."""

    generated_at: datetime
    lake: str
    tables: list[DeltaTable]
    sections: dict[str, Section]


def _last_commit(log_dir: Path) -> tuple[int, datetime | None, str]:
    """[쓰임] ``_delta_log`` 에서 최신 버전 번호 · 그 커밋 시각 · 작업 이름 (예: WRITE · MERGE)."""
    commits = sorted(p for p in log_dir.glob("*.json") if p.stem.isdigit())
    if not commits:
        raise FileNotFoundError(f"Delta 커밋이 없다: {log_dir}")
    latest = commits[-1]
    when, operation = None, ""
    for line in latest.read_text(encoding="utf-8").splitlines():
        info = json.loads(line).get("commitInfo") if line.strip() else None
        if info:
            stamp = info.get("timestamp")
            when = datetime.fromtimestamp(stamp / 1000, UTC) if stamp else None
            operation = str(info.get("operation", ""))
    return int(latest.stem), when, operation


def delta_tables(root: Path) -> list[DeltaTable]:
    """[쓰임] 레이크에 있는 Silver · Gold Delta 표 목록 (행 수 없이 — 로그만 읽어 빠르다)."""
    found = []
    for layer in LAYERS:
        folder = root / layer
        if not folder.is_dir():
            continue
        for table in sorted(p for p in folder.iterdir() if (p / "_delta_log").is_dir()):
            version, when, operation = _last_commit(table / "_delta_log")
            found.append(DeltaTable(layer, table.name, version, when, operation))
    return found


LAKE_SQL: dict[str, tuple[str, str, str]] = {
    "silver_daily": (
        "silver",
        "agg_trades_audit_daily",
        "SELECT symbol, trade_date, rows, suspect, volume_diff_pct, missing_ids_inside_day"
        " FROM {t} ORDER BY symbol, trade_date",
    ),
    "quarantine": (
        "silver",
        "agg_trades_quarantine",
        "SELECT symbol, reason, count(*) AS rows FROM {t} GROUP BY ALL ORDER BY symbol, reason",
    ),
    "gaps": (
        "silver",
        "agg_trades_gaps",
        "SELECT symbol, kind, prev_id, next_id, missing_ids FROM {t}"
        " ORDER BY symbol, prev_id LIMIT 50",
    ),
    "kline_check": (
        "gold",
        "bars_1m_kline_check",
        "SELECT symbol, trade_date, gold_minutes, kline_minutes, all_fields_equal, ohlc_equal,"
        " volume_equal FROM {t} ORDER BY symbol, trade_date",
    ),
    "stream": (
        "gold",
        "stream_bars_1m",
        "SELECT symbol, trade_date, count(*) AS bars,"
        " count(*) FILTER (WHERE NOT ids_contiguous) AS broken_bars,"
        " count(*) FILTER (WHERE coalesce(repaired, false)) AS repaired_bars"
        " FROM {t} GROUP BY ALL ORDER BY symbol, trade_date",
    ),
    # 국내 주식 (주 원천, K-3 · K-3b · K-4)
    "kr_daily_check": (
        "gold",
        "kr_daily_from_minutes",
        "SELECT symbol, result, count(*) AS days, min(trade_date) AS first_day,"
        " max(trade_date) AS last_day FROM {t} GROUP BY ALL ORDER BY symbol, result",
    ),
    "kr_audit": (
        "silver",
        "kr_minute_audit_daily",
        "SELECT symbol, count(*) AS days, count(*) FILTER (WHERE status = 'ok') AS ok_days,"
        " count(*) FILTER (WHERE status = 'no_closing_bar') AS no_closing_bar,"
        " count(*) FILTER (WHERE status = 'not_open_in_calendar') AS not_open_in_calendar,"
        " sum(bars) AS bars, sum(after_hours_bars) AS after_hours_bars,"
        " min(trade_date) AS first_day, max(trade_date) AS last_day FROM {t}"
        " GROUP BY ALL ORDER BY symbol",
    ),
    "kr_halts": (
        "silver",
        "kr_minute_gaps",
        "SELECT trade_date, kind, min(start_time) AS start_time, max(end_time) AS end_time,"
        " max(minutes) AS minutes, count(DISTINCT symbol) AS symbols FROM {t}"
        " WHERE market_wide GROUP BY ALL ORDER BY trade_date",
    ),
    "kr_events": (
        "gold",
        "kr_adjustment_events",
        "SELECT symbol, count(*) AS events, min(trade_date) AS first_day,"
        " max(trade_date) AS last_day, max(step) AS max_step FROM {t}"
        " GROUP BY ALL ORDER BY symbol",
    ),
    "kr_survivorship": (
        "gold",
        "kr_survivorship_yearly",
        "SELECT year, members_at_start, delisted_in_year, survivors,"
        " 100 * mean_return_all AS all_pct, 100 * mean_return_survivors AS survivors_pct,"
        " 100 * survivorship_bias AS bias_pp FROM {t} ORDER BY year",
    ),
    "kr_reconcile": (
        "gold",
        "kr_reconcile_daily",
        "SELECT symbol, trade_date, minutes, matched, stream_missing, only_stream,"
        " missed_trades, values_differ, stream_first, stream_last, reconciled_at FROM {t}"
        " QUALIFY row_number() OVER (PARTITION BY symbol, trade_date"
        " ORDER BY reconciled_at DESC) = 1 ORDER BY trade_date, symbol",
    ),
}
"""영역 이름 → (계층, 표, SQL 틀). 표가 없으면 그 영역은 '아직 없음'."""
RUNS_FILTER = "(params ->> 'symbol' IS NULL OR params ->> 'symbol' = ANY(%(symbols)s))"
PG_SQL = {
    "jobs": """
        SELECT DISTINCT ON (job) job, status, started_at,
               extract(epoch FROM finished_at - started_at) AS seconds,
               count(*) OVER (PARTITION BY job) AS runs, code_version
        FROM pipeline_runs WHERE {runs_filter} ORDER BY job, started_at DESC""",
    "runs": """
        SELECT job, status, started_at, extract(epoch FROM finished_at - started_at) AS seconds,
               code_version, params ->> 'symbol' AS symbol, error
        FROM pipeline_runs WHERE {runs_filter} ORDER BY started_at DESC LIMIT 25""",
    "reconcile": """
        SELECT symbol, trade_date, batch_minutes, matched, repaired, stream_gap, late, duplicate,
               other, only_stream, match_rate, trades_not_received, trades_late, stream_source,
               reconciled_at
        FROM reconcile_daily_latest WHERE {symbol_filter} ORDER BY symbol, trade_date""",
}
"""``{runs_filter}`` · ``{symbol_filter}`` 는 이 레이크의 종목으로 거르는 조건 (PostgreSQL 은
진짜 · 샘플 레이크가 함께 쓴다 — 샘플 화면에 진짜 실행이 섞이지 않게). 종목이 없는 실행(받기 ·
벤치마크 등의 일부)은 늘 보인다."""


def _scan(root: Path, layer: str, name: str) -> str:
    """[쓰임] ``root`` 아래 Delta 표를 읽는 SQL 조각.
    [이유] 환경 변수의 레이크가 아니라 **받은 레이크**를 읽어야 한다 — 진짜 레이크와 샘플 레이크를
           한 프로세스에서 섞어 읽지 않게(시험이 찾은 버그: 표 목록은 샘플, 행 수는 진짜였다)."""
    path = str(root / layer / name).replace("\\", "/").replace("'", "''")
    return f"delta_scan('{path}')"


def _lake_sections(root: Path, names: set[str]) -> dict[str, Section]:
    """[쓰임] Delta 표에서 읽는 영역들 — 필요한 표가 없으면 그 영역만 '없음'."""
    out: dict[str, Section] = {}
    con = connect()
    try:
        for key, (layer, table, sql) in LAKE_SQL.items():
            if f"{layer}/{table}" not in names:
                out[key] = Section(error="표가 아직 없다 (그 단계를 돌리지 않음)")
                continue
            query = sql.format(t=_scan(root, layer, table))
            out[key] = _run(partial(_duck_rows, con, query))
    finally:
        con.close()
    return out


def _duck_rows(con: Any, sql: str) -> list[dict[str, Any]]:
    result = con.execute(sql)
    columns = [c[0] for c in result.description]
    return [dict(zip(columns, row, strict=True)) for row in result.fetchall()]


def _run(read: Callable[[], list[dict[str, Any]]]) -> Section:
    """[쓰임] 읽기 하나 — 실패하면 화면에 보일 짧은 이유로."""
    try:
        return Section(rows=read())
    except Exception as exc:  # 화면은 영역 하나가 실패해도 나와야 한다
        return Section(error=f"{type(exc).__name__}: {str(exc).splitlines()[0][:160]}")


def _pg_sections(pg_dsn: str | None, symbols: list[str]) -> dict[str, Section]:
    """[쓰임] PostgreSQL 에서 읽는 영역들 — DSN 이 없거나 연결이 안 되면 모두 같은 이유."""
    if not pg_dsn:
        return {key: Section(error="PostgreSQL 주소가 없다 (MDP_PG_DSN)") for key in PG_SQL}
    try:
        import psycopg

        conn = psycopg.connect(pg_dsn, connect_timeout=5)
    except Exception as exc:
        reason = f"PostgreSQL 에 연결하지 못했다 ({type(exc).__name__})"
        return {key: Section(error=reason) for key in PG_SQL}
    out = {}
    with conn:
        for key, sql in PG_SQL.items():
            query = sql.format(
                runs_filter=RUNS_FILTER if symbols else "TRUE",
                symbol_filter="symbol = ANY(%(symbols)s)" if symbols else "TRUE",
            )

            def read(q: str = query) -> list[dict[str, Any]]:
                with conn.cursor() as cur:
                    cur.execute(q, {"symbols": symbols})
                    columns = [c.name for c in cur.description or []]
                    return [dict(zip(columns, row, strict=True)) for row in cur.fetchall()]

            out[key] = _run(read)
            if out[key].error:
                conn.rollback()  # 표가 없을 때 다음 질의가 '트랜잭션 중단' 으로 막히지 않게
    return out


def collect(pg_dsn: str | None, root: Path | None = None) -> PageData:
    """[쓰임] 화면 하나 분량을 모두 읽는다."""
    root = root or lake_root()
    tables = delta_tables(root) if root.is_dir() else []
    names = {f"{t.layer}/{t.name}" for t in tables}
    if tables:
        con = connect()
        try:
            counted = []
            for t in tables:
                count_sql = f"SELECT count(*) AS n FROM {_scan(root, t.layer, t.name)}"
                rows = _run(partial(_duck_rows, con, count_sql))
                counted.append(
                    DeltaTable(
                        t.layer,
                        t.name,
                        t.version,
                        t.committed_at,
                        t.operation,
                        rows.rows[0]["n"] if rows.rows else None,
                    )
                )
            tables = counted
        finally:
            con.close()
    lake = _lake_sections(root, names)
    symbols = sorted(
        {r["symbol"] for key in ("silver_daily", "kline_check", "stream") for r in lake[key].rows}
    )
    return PageData(datetime.now(UTC), _shown(root), tables, lake | _pg_sections(pg_dsn, symbols))


def _shown(root: Path) -> str:
    """[쓰임] 화면에 보일 레이크 위치 — 저장소 안이면 저장소 기준 상대 경로.
    [이유] 화면을 갈무리해 공유할 때 PC 의 사용자 폴더 경로가 찍히지 않게."""
    resolved = root.resolve()
    for base in (REPO_ROOT, Path.cwd().resolve()):
        if resolved.is_relative_to(base):
            return resolved.relative_to(base).as_posix() or "."
    return resolved.as_posix() if resolved.anchor == "/" else resolved.name
