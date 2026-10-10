"""실거래 기록 DB 맞추기 — JSON 장부 → PC SQLite → Supabase (주문 · 계좌 조회 없음).

사용 예::

    python -m scripts.ledger_sync run      # 오늘 평가 기록 → PC DB → Supabase (매일 한 번)
    python -m scripts.ledger_sync local    # PC DB 만 (인터넷 없이)
    python -m scripts.ledger_sync status   # 줄 수 보기

* 모듈 설명: :mod:`auto_trading.ops.live_ledger`. Supabase 표는 ``docs/supabase/schema.sql``.
* ``.env`` 의 ``SUPABASE_URL`` · ``SUPABASE_SERVICE_KEY`` (Secret 키) 를 쓴다. 키는 출력하지 않는다.
* Supabase 가 안 되면 PC DB 는 그대로 남고 끝 코드 2 (다음 실행이 다시 올림).
"""

from __future__ import annotations

import sqlite3
import sys
import time
from contextlib import closing
from pathlib import Path

from auto_trading.config.secrets import read_env_file
from auto_trading.ops.live_ledger import (
    DB_PATH,
    event_rows,
    push_supabase,
    snapshot_rows,
    write_sqlite,
)
from auto_trading.ui.live_home import Quotes, build_books, load_history

REPO = Path(__file__).resolve().parent.parent


def _rows() -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    books = build_books(REPO, Quotes())  # 거래 · 입금은 시세가 필요 없다
    return event_rows(books), snapshot_rows(load_history(REPO))


def _snapshot_today() -> None:
    """그래프용 오늘 평가 한 줄 (scripts/live_snapshot.py 와 같음). 실패해도 기록 맞추기는 계속."""
    from scripts.live_snapshot import snapshot_row, write

    try:
        row = snapshot_row(REPO, int(time.time() * 1000))
        if row is not None:
            write(REPO, row)
            print(f"오늘 평가 {row['date']} {row['value']:,}원{'' if row['priced'] else ' (추정)'}")
    except Exception as exc:  # 공개 시세 실패 등
        print(f"오늘 평가 기록 실패 (기록 맞추기는 계속): {type(exc).__name__}")


def run(push: bool) -> int:
    if push:
        _snapshot_today()
    events, snaps = _rows()
    n_e, n_s = write_sqlite(REPO / DB_PATH, events, snaps)
    print(f"PC 기록 DB: 거래 · 입금 {n_e}줄 · 하루 평가 {n_s}줄 → {DB_PATH}")
    if not push:
        return 0
    env = read_env_file(REPO / ".env")
    url, key = env.get("SUPABASE_URL", ""), env.get("SUPABASE_SERVICE_KEY", "")
    if not url or not key:
        print("Supabase 설정 없음 (.env SUPABASE_URL · SUPABASE_SERVICE_KEY) — PC DB 만 썼다")
        return 2
    try:
        p_e, p_s = push_supabase(url, key, events, snaps)
    except Exception as exc:  # 네트워크 · 표 없음 — 다음 실행이 다시 올린다
        print(f"Supabase 올리기 실패: {exc}")
        return 2
    print(f"Supabase: 거래 · 입금 {p_e}줄 · 하루 평가 {p_s}줄 올림 (같은 번호는 덮어씀)")
    return 0


def status() -> int:
    path = REPO / DB_PATH
    if not path.exists():
        print("아직 PC 기록 DB 없음 — python -m scripts.ledger_sync run")
        return 0
    with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as db:
        for account, kind, n, amount in db.execute(
            "SELECT account, kind, COUNT(*), SUM(amount) FROM ledger_events "
            "GROUP BY account, kind ORDER BY account, kind"
        ):
            print(f"{account:<11} {kind:<8} {n:>4}줄 · 금액 합 {amount or 0:,.2f}")
        n_s = db.execute("SELECT COUNT(*) FROM daily_snapshots").fetchone()[0]
    print(f"하루 평가 {n_s}줄")
    return 0


def main(argv: list[str]) -> int:
    if argv == ["run"]:
        return run(push=True)
    if argv == ["local"]:
        return run(push=False)
    if argv == ["status"]:
        return status()
    raise SystemExit("usage: python -m scripts.ledger_sync run|local|status")


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            _reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main(sys.argv[1:]))
