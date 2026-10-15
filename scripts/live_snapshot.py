"""실거래 홈 그래프용 하루 평가 기록 — 장부 + 공개 시세로 한 줄 남긴다 (주문 · 계좌 조회 없음).

사용 예::

    python -m scripts.live_snapshot        # data/live/history.jsonl 에 오늘(KST) 한 줄
    python -m scripts.live_snapshot show   # 남은 기록 보기

* 2026-10-10 화면 개선(Codex 제안 4번): "그림보다 기록 기반부터". 기록이 없는 과거는 만들지 않는다.
* 값 = 실거래 홈과 같은 계산(`auto_trading.ui.live_home.build_books`). 시세 · 환율이 하나라도 없으면
  ``priced: false`` 로 남기고 화면 그래프는 그 줄을 쓰지 않는다.
* 예약 작업으로 하루 한 번(예: 21:30) 돌리면 된다 — 등록은 사용자가 한다.
"""

from __future__ import annotations

import json
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from auto_trading.ui.live_home import HISTORY, build_books, fetch_quotes, load_history, needed

REPO = Path(__file__).resolve().parent.parent
_KST = timedelta(hours=9)


def snapshot_row(root: Path, now_ms: int) -> dict[str, Any] | None:
    if not build_books(root, fetch_quotes((), ())):
        return None
    coins, tickers = needed(root)
    q = fetch_quotes(coins, tickers)
    books = build_books(root, q)
    fx_ok = q.fx is not None
    per = {
        b.key: {
            "value": round(b.value_krw),
            "deposits": round(b.deposits_krw),
            "priced": b.priced and (b.key == "vega" or fx_ok),
        }
        for b in books
    }
    return {
        "date": (datetime.fromtimestamp(now_ms / 1000, UTC) + _KST).strftime("%Y-%m-%d"),
        "at_ms": now_ms,
        "value": sum(v["value"] for v in per.values()),
        "deposits": sum(v["deposits"] for v in per.values()),
        "priced": all(v["priced"] for v in per.values()),
        "fx": q.fx,
        "errors": list(q.errors),
        "books": per,
    }


def write(root: Path, row: dict[str, Any]) -> Path:
    path = root / HISTORY
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [r for r in load_history(root) if r["date"] != row["date"]] + [row]
    rows.sort(key=lambda r: r["date"])
    tmp = path.with_suffix(".tmp")
    tmp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), "utf-8")
    tmp.replace(path)
    return path


def main(argv: list[str]) -> int:
    if argv == ["show"]:
        for r in load_history(REPO):
            sys.stdout.write(
                f"{r['date']} 평가 {r.get('value', 0):,} · 기준 {r.get('deposits', 0):,}"
                f"{'' if r.get('priced') else ' (추정)'}\n"
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
