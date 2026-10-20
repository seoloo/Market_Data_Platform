"""실거래 홈 그래프용 하루 평가 기록 — 장부 + 공개 시세로 한 줄 남긴다 (주문 · 계좌 조회 없음).

사용 예::

    python -m scripts.live_snapshot        # data/live/history.jsonl 에 오늘(KST) 한 줄
    python -m scripts.live_snapshot show   # 남은 기록 보기

* 2026-10-10 화면 개선(Codex 제안 4번): "그림보다 기록 기반부터". 기록이 없는 과거는 만들지 않는다.
* 값 = 실거래 홈과 같은 계산(`auto_trading.ui.live_home.build_books`). 시세 · 환율이 하나라도 없으면
  ``priced: false`` 로 남기고 화면 그래프는 그 줄을 쓰지 않는다.
* 예약 작업으로 하루 한 번(예: 21:30) 돌리면 된다 — 등록은 사용자가 한다.

언제 실행: 매일 23:00 기록 맞추기(``scripts/ledger_sync run``)가 먼저 부른다 · 손으로.
읽고 쓰는 것: 실거래 장부 · 공개 시세 · (그 순간 계좌로 맞추기 — ``app/live_accounts``) →
              ``data/live/history.jsonl``.
"""

from __future__ import annotations

import json
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from auto_trading.app import live_accounts
from auto_trading.ui.live_home import HISTORY, build_books, fetch_quotes, load_history, needed

# 현황을 그 순간 실제 계좌로 맞출 조회 창구 (ui 는 거래소를 직접 부르지 않는다, 2026-10-21)
live_accounts.install()

REPO = Path(__file__).resolve().parent.parent
_KST = timedelta(hours=9)


def snapshot_row(root: Path, now_ms: int) -> dict[str, Any] | None:
    """[쓰임] 오늘 실거래 평가 한 줄 (카드마다 평가 · 넣은 돈 · 시세 다 있나).
    [이유] 실거래 홈과 같은 계산(``build_books``). 장부가 없으면 None — 기록하지 않는다."""
    if not build_books(root, fetch_quotes((), ())):
        return None
    coins, tickers = needed(root)
    quotes = fetch_quotes(coins, tickers)
    books = build_books(root, quotes)
    fx_ok = quotes.fx is not None
    per = {
        book.key: {
            "value": round(book.value_krw),
            "deposits": round(book.deposits_krw),
            "priced": book.priced and (book.key == "vega" or fx_ok),
        }
        for book in books
    }
    return {
        "date": (datetime.fromtimestamp(now_ms / 1000, UTC) + _KST).strftime("%Y-%m-%d"),
        "at_ms": now_ms,
        "value": sum(book_row["value"] for book_row in per.values()),
        "deposits": sum(book_row["deposits"] for book_row in per.values()),
        "priced": all(book_row["priced"] for book_row in per.values()),
        "fx": quotes.fx,
        "errors": list(quotes.errors),
        "books": per,
    }


def write(root: Path, row: dict[str, Any]) -> Path:
    """[쓰임] 기록 파일에 오늘 줄을 넣는다 (같은 날은 바꿔 끼움, 날짜 순)."""
    path = root / HISTORY
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [old_row for old_row in load_history(root) if old_row["date"] != row["date"]] + [row]
    rows.sort(key=lambda old_row: old_row["date"])
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        "".join(json.dumps(old_row, ensure_ascii=False) + "\n" for old_row in rows), "utf-8"
    )
    tmp.replace(path)
    return path


def main(argv: list[str]) -> int:
    """[쓰임] 명령 이름 → 함수 (``show`` = 남은 기록 보기)."""
    if argv == ["show"]:
        for history_row in load_history(REPO):
            sys.stdout.write(
                f"{history_row['date']} 평가 {history_row.get('value', 0):,}"
                f" · 기준 {history_row.get('deposits', 0):,}"
                f"{'' if history_row.get('priced') else ' (추정)'}\n"
            )
        return 0
    if argv:
        raise SystemExit("usage: python -m scripts.live_snapshot [show]")
    row = snapshot_row(REPO, int(time.time() * 1000))
    if row is None:
        sys.stdout.write("실거래 장부 없음 — 기록 안 함\n")
        return 0
    path = write(REPO, row)
    sys.stdout.write(
        f"{row['date']} 평가 {row['value']:,} · 기준 {row['deposits']:,}"
        f"{'' if row['priced'] else ' (추정 — 그래프 제외)'} → {path}\n"
    )
    return 0


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            _reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main(sys.argv[1:]))
