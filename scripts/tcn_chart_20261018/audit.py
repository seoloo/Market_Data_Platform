"""Read-only source inventory. Does not create signals or returns."""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
END_MS = 1788220800000  # 2026-09-01 UTC, exclusive


def main() -> None:
    with sqlite3.connect(f"file:{ROOT / 'data/trading.db'}?mode=ro", uri=True) as con:
        rows = con.execute(
            "select exchange,symbol,timeframe,count(*),min(open_ms),max(open_ms) "
            "from candles where open_ms < ? and timeframe in ('1h','4h') "
            "and exchange='binance_usdm' group by exchange,symbol,timeframe",
            (END_MS,),
        ).fetchall()
        tables = con.execute("select name from sqlite_master where type='table'").fetchall()
    vision = ROOT / "data/external/binance_vision"
    files = []
    for kind in ("um", "um_mark", "um_funding"):
        for p in (vision / kind).rglob("*.zip"):
            month = p.stem[-7:]
            if "2020-01" <= month <= "2026-08":
                files.append(
                    (
                        kind,
                        p.relative_to(vision / kind).parts[0],
                        p.parent.name,
                        month,
                        p.stat().st_size,
                    )
                )
    out = {
        "db_candles": rows,
        "tables": tables,
        "file_counts": dict(Counter(f"{k}/{s}/{tf}" for k, s, tf, _, _ in files)),
        "archive_bytes": sum(r[-1] for r in files),
        "protected_cutoff_exclusive_ms": END_MS,
    }
    dest = ROOT / "reports/tcn_chart_20261018"
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "inventory.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
