"""엘리엇 보조 지표 알림 — **1 시간 단위** (BTC, 주문 없음). 2026-10-09 사용자.

사용자: "4시간 단위 말고 최소 1시간 단위로 변경해서 알림 보내는 걸로 변경해줘".

* 연구 엔진(EW-3e)은 '4h 봉 = 큰 파동 · 1h 봉 = 하위 파동 · 1m = 극값 순서' 로 고정돼 있다.
  엔진은 고치지 않고 **시간축을 4 배로 늘려** 넣는다(실제 1m 하나 → 엔진 1m 넷, 같은 값).
  그러면 실제 1h = 엔진 4h, 실제 15m = 엔진 1h 가 된다.
  - 꼭짓점 반전 크기는 2% → **1%** (파동 크기를 시간에 맞춰 줄임 · 결과 보기 전 정함).
  - 엔진의 120 일 창 = 실제 30 일, 실제 1m 은 40 일치를 받는다.
* 알림 조건 · 모양은 4h 판과 같다.
  - 상위 10 중 진입 패턴(충격파 5파 끝)이 하나라도 있을 때만 보낸다.
  - 내용: 큰 추세 해석 상승 · 하락 수, 패턴별, 1 등 해석 무효선.
* 실행: ``python -m scripts.ew_indicator_1h`` (상주, 매시 정각 1 분 30 초 뒤 확인).
  ``--once`` 는 한 번만 확인하고 끝낸다. 상태는 ``reports/ew_indicator_1h/``.
* 엘리엇 매매 규칙은 연구에서 기각됐다. 이 알림은 사람이 흐름을 볼 때 쓰는 참고 자료다.
"""

from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Callable
from decimal import Decimal
from typing import Any

import httpx
from scripts.demo_ew_btc import (
    REPO_ROOT,
    SIGNAL_SYMBOL,
    Notify,
    Store,
    _lock,
    has_pattern,
    no_setup_message,
    scan,
)

from auto_trading.core.errors import AutoTradingError
from auto_trading.core.types import Candle

OUT = REPO_ROOT / "reports" / "ew_indicator_1h"
TAG = "🌊 [엘리엇 보조지표 · BTC]"
STRETCH = 4
THRESHOLD = Decimal("0.01")
REAL_DAYS = 40
HOUR_MS = 3_600_000
DELAY_MS = 90_000


def fetch_real_minutes(client: httpx.Client | None, now_ms: int) -> list[list[Any]]:
    """실제 BTCUSDT 무기한 1m (끝난 1h 까지)."""
    start = now_ms - REAL_DAYS * 86_400_000
    start -= start % HOUR_MS
    end = now_ms - now_ms % HOUR_MS
    rows: list[list[Any]] = []
    if client is None:
        return rows
    cursor = start
    while cursor < end:
        batch = client.get(
            "https://fapi.binance.com/fapi/v1/klines",
            params={"symbol": SIGNAL_SYMBOL, "interval": "1m", "startTime": cursor, "limit": 1500},
        ).json()
        if not batch:
            break
        rows.extend(r for r in batch if int(r[0]) < end)
        cursor = int(batch[-1][0]) + 60_000
    return rows


def stretch(rows: list[list[Any]]) -> list[Candle]:
    """실제 1m 하나를 엔진 1m 넷으로 (시간 × 4, 값 그대로 · 거래량 ÷ 4) — 순수."""
    out: list[Candle] = []
    for r in rows:
        base = int(r[0]) * STRETCH
        vol = Decimal(str(r[5])) / STRETCH
        o, h, lo, c = (Decimal(str(x)) for x in r[1:5])
        for j in range(STRETCH):
            start = base + j * 60_000
            out.append(Candle(start, start + 59_999, o, h, lo, c, vol))
    return out


def real_ms(engine_ms: int) -> int:
    return (engine_ms + 1) // STRETCH - 1


def check_once(
    client: httpx.Client | None, store: Store, notify: Callable[[str], None], now_ms: int
) -> int:
    """새로 확정된 1h 꼭짓점을 알린다. 보낸 수."""
    rows = scan(stretch(fetch_real_minutes(client, now_ms)), THRESHOLD)
    state = store.load()
    last = int(state.get("last_trigger_ms", 0))
    sent = 0
    if not last:
        state["last_trigger_ms"] = real_ms(rows[-1][0]) if rows else now_ms
        store.event("init", last_trigger_ms=state["last_trigger_ms"])
    else:
        for row in rows:
            at = real_ms(row[0])
            if at <= last:
                continue
            store.event("trigger", close_ms=at, scenarios=row[2], **row[3])
            if has_pattern(row[3]):
                notify(no_setup_message(row[3], TAG, demo=False, tf="1h"))
                sent += 1
            state["last_trigger_ms"] = at
    store.save(state)
    return sent


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)
    if not args.once and not _lock(OUT):
        print("1h 보조 지표가 이미 돌고 있다 — 끝낸다")
        return 0
    client = httpx.Client(timeout=30)
    store = Store(OUT)
    notify = Notify(OUT, telegram=True)
    next_check = 0
    while True:
        now = int(time.time() * 1000)
        if now >= next_check:
            try:
                n = check_once(client, store, notify, now)
                print(f"확인 · 알림 {n}", flush=True)
            except (AutoTradingError, httpx.HTTPError, ValueError, KeyError) as exc:
                store.event("error", error=str(exc))
                print(f"오류 (다음 확인에서 다시): {exc}", flush=True)
            next_check = now - now % HOUR_MS + HOUR_MS + DELAY_MS
        if args.once:
            return 0
        time.sleep(20)


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            _reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main(sys.argv[1:]))
