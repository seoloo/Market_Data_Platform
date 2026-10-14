"""AGS Paper — AG-CR(AG3) Paper + 문서 232 숏 관찰 장부 (주문 없음, 공개 시세만).

    python -m scripts.ags_paper daily    # 시작부터 다시 계산 + 보고서
    python -m scripts.ags_paper status

* AGS1 = AG-CR + 숏 1 배, AGS3 = AG-CR + 숏 3 배 (232 의 AG3 SHORT_1X · SHORT_MAX).
* 롱은 AG-CR Paper 원장을 읽기만 한다 — ``ag_paper daily`` 가 먼저 돌아야 그날 값이 들어간다.
* 매번 시작 시각부터 다시 계산한다(상태 파일 없음, 같은 입력이면 같은 결과).
보고 ``reports/ags_paper/<날짜>.json`` · ``latest.json``.
"""

from __future__ import annotations

import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scripts.run_paper_o1 import DB, LEDGERS

from auto_trading.exchange.binance_usdm_public import BinanceUsdmPublic
from auto_trading.ops.ag_short_paper import (
    DAY,
    H4,
    SYMBOLS,
    AgDay,
    Bar,
    Book,
    replay,
    short_signals,
)
from auto_trading.storage.db import connect_read_only
from auto_trading.storage.repositories.paper_overlay_days import OverlayPaperDayRepository

REPO = Path(__file__).resolve().parent.parent
REPORTS = REPO / "reports" / "ags_paper"
START_MS = int(datetime(2026, 10, 14, 16, tzinfo=UTC).timestamp() * 1000)
"""첫 4 시간봉 (2026-10-15 01:00 KST). 사용자 2026-10-14 '장부를 만들어줘'."""
BOOKS = (("AGS1", 1.0), ("AGS3", 3.0))
WARMUP_BARS = 300


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m-%d %H:%M")


def _bars_4h(api: BinanceUsdmPublic, symbol: str, now_ms: int) -> list[Bar]:
    out: list[Bar] = []
    cursor = START_MS - WARMUP_BARS * H4
    while cursor < now_ms:
        rows = api._get(
            "/fapi/v1/klines",
            {"symbol": symbol, "interval": "4h", "startTime": cursor, "limit": 1500},
        )
        if not rows:
            break
        for r in rows:
            if int(r[6]) < now_ms:  # 끝난 봉만
                out.append(Bar(int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4])))
        nxt = int(rows[-1][0]) + H4
        if nxt <= cursor or len(rows) < 1500:
            break
        cursor = nxt
        time.sleep(0.2)
    return out


def _daily(api: BinanceUsdmPublic, symbol: str, now_ms: int) -> list[Bar]:
    return [
        Bar(c.open_ms, float(c.open), float(c.high), float(c.low), float(c.close))
        for c in api.closed_daily(symbol, now_ms, limit=300)
    ]


def _funding(api: BinanceUsdmPublic, symbol: str, now_ms: int) -> list[tuple[int, float]]:
    out: list[tuple[int, float]] = []
    lo = START_MS - DAY
    while lo < now_ms:
        hi = min(now_ms, lo + 250 * DAY)
        out += api.funding(symbol, lo, hi)
        lo = hi + 1
    return out


def _ag_days() -> list[AgDay]:
    conn = connect_read_only(DB)
    try:
        rows = OverlayPaperDayRepository(conn).all(LEDGERS["ag"].strategy_id)
    finally:
        conn.close()
    days = []
    for r in rows:
        st = r.state or {}
        days.append(
            AgDay(
                decision_ms=r.decision_ms,
                equity=float(r.equity),
                target={k: float(v) for k, v in (st.get("target") or {}).items()},
                wallet=float(st.get("wallet") or 0.0),
                spot_qty={k: float(v) for k, v in (st.get("spot_qty") or {}).items()},
                fut_qty={k: float(v) for k, v in (st.get("fut_qty") or {}).items()},
                exposure=float(st.get("exposure") or 3.0),
            )
        )
    return days


def daily() -> dict[str, Any]:
    now_ms = int(time.time() * 1000)
    api = BinanceUsdmPublic()
    try:
        bars = {s: _bars_4h(api, s, now_ms) for s in SYMBOLS}
        daily_bars = {s: _daily(api, s, now_ms) for s in SYMBOLS}
        funding = {s: _funding(api, s, now_ms) for s in SYMBOLS}
    finally:
        api.close()
    signals = {s: short_signals(bars[s], daily_bars[s]) for s in SYMBOLS}
    ag_days = _ag_days()
    ag_start = next((d.equity for d in ag_days if d.decision_ms >= START_MS - DAY), None)
    ag_now = ag_days[-1] if ag_days else None
    books: dict[str, Any] = {}
    for name, lev in BOOKS:
        b = replay(
            Book(name, lev),
            start_ms=START_MS,
            bars=bars,
            signals=signals,
            ag_days=ag_days,
            funding=funding,
        )
        # AG Paper 그래프와 같은 눈금: AG-CR 첫날 평가액 = 1,000. 하루(UTC) 마지막 값만, 시작 전날은
        # AG-CR 과 같은 값에서 갈라지게 넣는다.
        base = ag_days[0].equity if ag_days else None
        path: dict[str, float] = {}
        if base and ag_start is not None and b.equity_path:
            path[_iso(START_MS - DAY)[:10]] = round(ag_start / base * 1000, 2)
            for ms, eq in b.equity_path:
                path[_iso(ms - 1)[:10]] = round(eq / base * 1000, 2)
        start_eq = b.equity_path[0][1] if b.equity_path else None
        last_eq = b.equity_path[-1][1] if b.equity_path else None
        books[name] = {
            "short_leverage": lev,
            "path": path,
            "start_equity": start_eq,
            "equity": last_eq,
            "return_since_start": (last_eq / start_eq - 1) if start_eq and last_eq else None,
            "short_realized": b.realized,
            "short_trades": len(b.trades),
            "short_losing": sum(t["net"] < 0 for t in b.trades),
            "open_shorts": [
                {
                    "symbol": s.symbol,
                    "entry": s.entry,
                    "entry_at": _iso(s.entry_ms),
                    "qty": s.qty,
                    "stop": s.stop,
                }
                for s in b.open.values()
            ],
            "own_halted_at": _iso(b.halted_ms) if b.halted_ms else None,
            "trades": [
                {**t, "entry_at": _iso(t["entry_ms"]), "exit_at": _iso(t["exit_ms"])}
                for t in b.trades[-20:]
            ],
            "signals": [{**e, "at": _iso(e["at_ms"])} for e in b.events[-20:]],
        }
    last_bar = max((b.t for b in bars[SYMBOLS[0]]), default=0)
    report = {
        "as_of_utc": _iso(last_bar + H4) if last_bar else None,
        "start_utc": _iso(START_MS),
        "ag_cr": {
            "equity_at_start": ag_start,
            "equity_now": ag_now.equity if ag_now else None,
            "return_since_start": (ag_now.equity / ag_start - 1) if ag_now and ag_start else None,
            "long_target_now": dict(ag_now.target) if ag_now else None,
        },
        "books": books,
        "rules": (
            "AG-CR(AG3) Paper 롱 그대로 + 232 숏(RSI7 70 꺾임 · 일봉 ADX 하락 · "
            "롱 목표 0 인 코인만 · 3% 손절 · 48h · 증거금 10% × 배율 · 위험 1% · "
            "숏 자기 손익 2.5% 차단). 4 시간봉 시가 체결 근사."
        ),
        "note": (
            "가상 계좌, 주문 없음. 232 는 이미 본 자료의 설명용 결과 — 이 장부는 앞으로의 관찰용."
        ),
    }
    REPORTS.mkdir(parents=True, exist_ok=True)
    day = datetime.now(UTC).strftime("%Y-%m-%d")
    text = json.dumps(report, ensure_ascii=False, indent=1)
    (REPORTS / f"{day}.json").write_text(text, encoding="utf-8")
    (REPORTS / "latest.json").write_text(text, encoding="utf-8")
    return report


def status() -> dict[str, Any]:
    path = REPORTS / "latest.json"
    return (
        json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"status": "아직 없음"}
    )


def main(argv: list[str]) -> int:
    commands = {"daily": daily, "status": status}
    if len(argv) != 1 or argv[0] not in commands:
        raise SystemExit("usage: python -m scripts.ags_paper daily | status")
    sys.stdout.write(json.dumps(commands[argv[0]](), ensure_ascii=True, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
