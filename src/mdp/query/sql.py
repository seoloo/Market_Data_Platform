"""SQL 한 줄 실행 — Delta 표를 뷰로 등록한 DuckDB 에서 아무 SQL 이나 돌려 표로 보여 준다.

언제 쓰나: 분석 · 시연 · 확인할 때, 호스트에서::

    python -m mdp.query.sql "SELECT trade_date, count(*) FROM silver_agg_trades GROUP BY 1"
    python -m mdp.query.sql --views          # 쓸 수 있는 뷰 이름만

읽고 쓰는 것: ``<lake>/silver/*`` · ``<lake>/gold/*`` Delta 표(읽기만) → 화면.

[이유] Spark 컨테이너를 띄우지 않고 1 초 안에 답을 본다 — 키 없는 시연(6-1)에서도 이것으로 결과를
       보여 준다.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from mdp.query.duck import connect, register_views

__all__ = ["main"]


def main(argv: Sequence[str] | None = None) -> int:
    """[쓰임] 뷰를 등록하고 SQL 을 돌려 결과를 표로 찍는다(``--max-rows`` 줄까지)."""
    parser = argparse.ArgumentParser(prog="python -m mdp.query.sql")
    parser.add_argument("query", nargs="?", help="돌릴 SQL")
    parser.add_argument("--views", action="store_true", help="등록된 뷰 이름만 보여 준다")
    parser.add_argument("--max-rows", type=int, default=50)
    args = parser.parse_args(argv)
    con = connect()
    views = [*register_views(con, "silver"), *register_views(con, "gold")]
    if args.views or not args.query:
        sys.stdout.write("\n".join(views) + "\n")
        return 0
    con.sql(args.query).show(max_rows=args.max_rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
