"""문서 111 — SB 유니버스 자료: 위키백과 S&P 500 구성 종목 → 한국투자증권 일봉 (읽기 전용).

사용::

    python -m scripts.fetch_sb_universe tickers   # 위키백과 종목 목록 → stocks/universe.json
    python -m scripts.fetch_sb_universe bars      # 종목마다 일봉 (받은 종목은 건너뜀, 이어 받기)

주문하지 않는다. 거래소 코드는 NAS → NYS → AMS 순서로 찾는다.
"""

from __future__ import annotations

import csv
import json
import re
import sys
import time
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "data" / "research" / "stocks"
UNIVERSE = OUT / "universe.json"
SINCE, UNTIL = "2007-08-01", "2026-08-31"
PAGES = {
    "sp500": "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
}
EXCHANGES = ("NAS", "NYS", "AMS")
_TICKER = re.compile(r"^[A-Z][A-Z.\-]{0,6}$")


class _Tables(HTMLParser):
    """wikitable 들의 행을 셀 글자 목록으로 모은다."""

    def __init__(self) -> None:
        super().__init__()
        self.tables: list[list[list[str]]] = []
        self._in_table = 0
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        cls = dict(attrs).get("class") or ""
        if tag == "table" and "wikitable" in cls:
            self._in_table += 1
            self.tables.append([])
        elif self._in_table and tag == "tr":
            self._row = []
        elif self._in_table and tag in ("td", "th") and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "table" and self._in_table:
            self._in_table -= 1
        elif tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append("".join(self._cell).strip())
            self._cell = None
        elif tag == "tr" and self._row is not None and self._in_table:
            self.tables[-1].append(self._row)
            self._row = None

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)


def _symbols_from(html: str) -> list[str]:
    parser = _Tables()
    parser.feed(html)
    for table in parser.tables:
        if not table:
            continue
        header = [h.lower() for h in table[0]]
        col = next((i for i, h in enumerate(header) if h in ("symbol", "ticker")), None)
        if col is None:
            continue
        out = [row[col].strip() for row in table[1:] if len(row) > col]
        out = [s for s in out if _TICKER.match(s)]
        if len(out) >= 90:
            return out
    raise SystemExit("종목 표를 찾지 못했다")


def tickers() -> dict[str, object]:
    import httpx

    headers = {
        "User-Agent": "AutoTradingResearch/1.0 (https://github.com/seoloo; personal research) httpx"
    }
    lists: dict[str, list[str]] = {}
    with httpx.Client(headers=headers, timeout=30, follow_redirects=True) as client:
        for name, url in PAGES.items():
            resp = client.get(url)
            resp.raise_for_status()
            lists[name] = _symbols_from(resp.text)
    union = sorted(set(lists["sp500"]))
    OUT.mkdir(parents=True, exist_ok=True)
    record = {
        "fetched_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "sources": PAGES,
        "counts": {k: len(v) for k, v in lists.items()},
        "union": union,
        "lists": lists,
    }
    UNIVERSE.write_text(json.dumps(record, indent=1), encoding="utf-8")
    return {"counts": record["counts"], "union": len(union)}


def bars() -> dict[str, object]:
    from auto_trading.config.secrets import load_kis_credentials
    from auto_trading.core.errors import ExchangeError
    from auto_trading.exchange.kis import KisOverseasReadOnly, KisTokenCache

    union = json.loads(UNIVERSE.read_text(encoding="utf-8"))["union"]
    status_path = OUT / "fetch_status.json"
    status: dict[str, object] = (
        json.loads(status_path.read_text(encoding="utf-8")) if status_path.exists() else {}
    )
    broker = KisOverseasReadOnly(
        load_kis_credentials(REPO / ".env"), KisTokenCache(REPO / "data" / "kis_token.json")
    )
    try:
        for n, symbol in enumerate(union, 1):
            path = OUT / f"{symbol}.csv"
            if path.exists() or symbol in status:
                continue
            kis_symbol = symbol.replace(".", "/")
            got = None
            for excd in EXCHANGES:
                try:
                    rows = broker.daily_history(
                        kis_symbol, since=SINCE, until=UNTIL, exchange=excd, adjusted=True
                    )
                except ExchangeError as exc:
                    if "초당" in str(exc):
                        time.sleep(2.0)
                    continue
                if rows:
                    got = (excd, rows)
                    break
            if got is None:
                status[symbol] = {"ok": False}
            else:
                excd, rows = got
                with path.open("w", encoding="utf-8", newline="\n") as fh:
                    w = csv.writer(fh, lineterminator="\n")
                    w.writerow(("date", "open", "high", "low", "close", "volume"))
                    for b in rows:
                        day = datetime.fromtimestamp(b.open_ms / 1000, UTC).strftime("%Y-%m-%d")
                        w.writerow((day, b.open, b.high, b.low, b.close, b.volume))
                status[symbol] = {"ok": True, "exchange": excd, "rows": len(rows)}
            status_path.write_text(json.dumps(status, indent=1), encoding="utf-8")
            sys.stderr.write(f"{n}/{len(union)} {symbol} {status[symbol]}\n")
            sys.stderr.flush()
    finally:
        broker.close()
    ok = sum(1 for v in status.values() if isinstance(v, dict) and v.get("ok"))
    return {"symbols": len(union), "ok": ok, "failed": len(status) - ok}


def main(argv: list[str]) -> int:
    if argv == ["tickers"]:
        result = tickers()
    elif argv == ["bars"]:
        result = bars()
    else:
        raise SystemExit("usage: python -m scripts.fetch_sb_universe tickers | bars")
    sys.stdout.write(json.dumps(result, ensure_ascii=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
