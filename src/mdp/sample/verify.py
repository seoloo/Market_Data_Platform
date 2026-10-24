"""샘플 시연 확인 — 파이프라인이 만든 표를 정답지(``expected.json``)와 DuckDB 로 맞댄다.

언제 쓰나: 샘플로 Silver → Gold (→ 복사 · 대사)를 돌린 뒤, 호스트에서::

    MDP_LAKE_ROOT=data/sample/lake python -m mdp.sample.verify --root data/sample

읽고 쓰는 것: ``<root>/expected.json`` · 샘플 레이크의 Delta 표(DuckDB, 읽기만) · PostgreSQL
``gold_bars_1m`` · ``reconcile_daily_latest`` (``--pg-dsn`` 이 있을 때, 읽기만). 표준 출력에 확인표.
모두 맞으면 종료 코드 0.

[이유] '돌아갔다' 가 아니라 **'넣은 결함을 빠짐없이 · 더하지도 않고 찾았다'** 를 보여야 한다. 그래서
       정답은 데이터를 만든 쪽(:mod:`mdp.sample.generate`)이 미리 적어 두고, 확인은 Spark 가 아닌
       DuckDB 로 Delta 표를 직접 읽어 한다(만든 엔진과 확인하는 엔진을 다르게).
"""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from mdp.query.duck import connect, delta_source

__all__ = ["Check", "main", "verify"]


@dataclass(frozen=True, slots=True)
class Check:
    """[쓰임] 확인 하나 — 이름 · 정답 · 실제."""

    name: str
    expected: Any
    actual: Any

    @property
    def ok(self) -> bool:
        return bool(self.expected == self.actual)


def _one(con: Any, sql: str) -> Any:
    row = con.execute(sql).fetchone()
    return None if row is None else row[0]


def verify(root: Path, pg_dsn: str | None) -> list[Check]:
    """[쓰임] 정답지의 항목마다 실제 값을 읽어 :class:`Check` 목록으로."""
    expected = json.loads((root / "expected.json").read_text(encoding="utf-8"))
    symbol, silver, gold = expected["symbol"], expected["silver"], expected["gold"]
    con = connect()
    where = f"symbol = '{symbol}'"
    trades, quarantine = (
        delta_source("silver", "agg_trades"),
        delta_source("silver", "agg_trades_quarantine"),
    )
    audit_file, gaps = (
        delta_source("silver", "agg_trades_audit_file"),
        delta_source("silver", "agg_trades_gaps"),
    )
    daily, bars = delta_source("silver", "agg_trades_audit_daily"), delta_source("gold", "bars_1m")
    kline_check = delta_source("gold", "bars_1m_kline_check")
    reasons = dict(
        con.execute(
            f"SELECT reason, count(*) FROM {quarantine} WHERE {where} GROUP BY 1"
        ).fetchall()
    )
    found_gaps = [
        {"prev_id": p, "next_id": n, "missing_ids": m}
        for p, n, m in con.execute(
            f"SELECT prev_id, next_id, missing_ids FROM {gaps}"
            f" WHERE {where} AND kind = 'gap' ORDER BY 1"
        ).fetchall()
    ]
    suspect = [
        f"{d:%Y-%m-%d}"
        for (d,) in con.execute(
            f"SELECT trade_date FROM {daily} WHERE {where} AND suspect ORDER BY 1"
        ).fetchall()
    ]
    missing_inside = {
        f"{d:%Y-%m-%d}": int(n)
        for d, n in con.execute(
            f"SELECT trade_date, missing_ids_inside_day FROM {daily}"
            f" WHERE {where} AND missing_ids_inside_day > 0"
        ).fetchall()
    }
    equal_by_day = {
        f"{d:%Y-%m-%d}": int(n)
        for d, n in con.execute(
            f"SELECT trade_date, all_fields_equal FROM {kline_check} WHERE {where}"
        ).fetchall()
    }
    volume_gap = _one(
        con, f"SELECT sum(kline_volume - gold_volume) FROM {kline_check} WHERE {where}"
    )
    checks = [
        Check(
            "Silver 깨끗한 체결 행",
            silver["clean_rows"],
            _one(con, f"SELECT count(*) FROM {trades} WHERE {where}"),
        ),
        Check("격리 — 이유별 줄 수", silver["quarantine"], reasons),
        Check(
            "중복 줄 (같은 번호가 또 나온 줄)",
            silver["duplicate_rows"],
            _one(con, f"SELECT sum(duplicate_rows) FROM {audit_file} WHERE {where}"),
        ),
        Check("빈 곳 — 앞 번호 · 뒤 번호 · 빠진 수", silver["gaps"], found_gaps),
        Check(
            "시각 역행 줄",
            silver["time_reversed"],
            _one(con, f"SELECT sum(time_reversed_rows) FROM {audit_file} WHERE {where}"),
        ),
        Check("의심일", silver["suspect_days"], suspect),
        Check("날 안에서 빠진 번호", silver["missing_ids_inside_day"], missing_inside),
        Check(
            "Gold 1 분봉 수", gold["bars"], _one(con, f"SELECT count(*) FROM {bars} WHERE {where}")
        ),
        Check(
            "Gold = 공식 봉 (칸마다 같은 분, 날마다)", gold["kline_all_fields_equal"], equal_by_day
        ),
        Check("공식 봉 − Gold 거래량 (빠진 체결만큼)", Decimal(gold["lost_volume"]), volume_gap),
    ]
    con.close()
    if pg_dsn:
        import psycopg

        with psycopg.connect(pg_dsn) as pg:
            copied = pg.execute(
                "SELECT count(*) FROM gold_bars_1m WHERE symbol = %s", [symbol]
            ).fetchone()
            checks.append(
                Check("PostgreSQL 복사본 행", gold["bars"], copied[0] if copied else None)
            )
            stream = expected["stream"]
            row = pg.execute(
                "SELECT stream_gap, trades_not_received, late, duplicate, other"
                " FROM reconcile_daily WHERE symbol = %s AND trade_date = %s AND repaired_now > 0"
                " ORDER BY reconciled_at DESC LIMIT 1",
                [symbol, stream["date"]],
            ).fetchone()
            if row is not None:  # 스트림 단계(--with-stream)를 돌렸을 때만
                checks.append(
                    Check(
                        "대사 — 끊김 분 · 받지 못한 체결 · 늦음 · 중복 · 기타",
                        (stream["stream_gap_minutes"], stream["trades_not_received"], 0, 0, 0),
                        tuple(row),
                    )
                )
    return checks


def main(argv: Sequence[str] | None = None) -> int:
    """[쓰임] 명령줄 실행 — 확인표를 찍고, 모두 맞으면 0."""
    parser = argparse.ArgumentParser(prog="python -m mdp.sample.verify")
    parser.add_argument("--root", type=Path, default=Path("data/sample"))
    parser.add_argument("--pg-dsn", default=os.environ.get("MDP_PG_DSN"))
    args = parser.parse_args(argv)
    checks = verify(args.root, args.pg_dsn)
    width = max(len(c.name) for c in checks)
    for check in checks:
        mark = "OK " if check.ok else "다름"
        print(f"[{mark}] {check.name:<{width}}  정답 {check.expected}  ·  실제 {check.actual}")
    passed = sum(c.ok for c in checks)
    print(f"{passed}/{len(checks)} 맞음")
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
