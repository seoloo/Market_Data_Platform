"""나무증권(NH) 해외주식 원화 주문 — 조회 · 모의 시험.

    python -m scripts.namu_global check            # 실전 조회만: 원화로 살 수 있는 돈 · 미국 보유
    python -m scripts.namu_global mock-test [종목]  # 모의 주문 경로 시험 (기본 AAPL, 미국 정규장)

모의 시험 (사용자 2026-10-17 "나무증권 해외주식 원화 주문 연결해줘" — 실전 전 확인):

1. **미체결 → 취소**: 매수 1 호가 −5% 에 1 주 지정가 매수(안 닿는 가격) → 미체결 확인 → 취소 →
   취소 확인.
2. **체결 → 되팔기**: 매도 1 호가에 1 주 지정가 매수 → 체결 확인 → 보유 확인 → 매수 1 호가에 1 주
   지정가 매도 → 체결 확인.

실전 주문은 이 스크립트에 없다(``NAMU_TRADING_ENABLED`` 와 별개). 결과는
``reports/namu_global/<날짜>-mock.json`` — 응답 원문(주문 상태 이름 · 취소 줄 모양)을 남겨
:meth:`NamuGlobal.order_status` 해석이 맞는지 본다.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from dataclasses import asdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parent.parent
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

from auto_trading.config.secrets import load_namu_credentials  # noqa: E402
from auto_trading.exchange.namu import LIVE_URL, MOCK_URL, NamuTokenCache  # noqa: E402
from auto_trading.exchange.namu_global import NamuGlobal, NamuOrderStatus  # noqa: E402

OUT = REPO / "reports" / "namu_global"
KST = ZoneInfo("Asia/Seoul")
WAIT_S = 30.0
POLL_S = 3.0


def client(
    *, live: bool, allowed: frozenset[str] = frozenset(), max_usd: float = 0.0
) -> NamuGlobal:
    return NamuGlobal(
        load_namu_credentials(REPO / ".env"),
        NamuTokenCache(REPO / "data" / "namu_token.json"),
        base_url=LIVE_URL if live else MOCK_URL,
        allowed=allowed,
        max_qty=1 if allowed else 0,
        max_order_usd=max_usd,
    )


def check() -> int:
    nh = client(live=True)
    try:
        acct = nh.account()
        krw = nh.buyable_krw()
        head, rows = nh.holdings()
        q = nh.quote("VOO")
    finally:
        nh.close()
    print(f"나무 실전 계좌 ***{acct[-3:]} (조회만)")
    print(f"  원화로 미국 주식 살 수 있는 돈: {krw:,.0f}원  (환율 {q.fx:,.0f})")
    print(f"  미국 보유 평가 {int(head.get('eal_amt_sum') or 0):,}원 · {len(rows)} 종목")
    for h in rows:
        print(
            f"   └ {h.symbol} {h.qty}주 · 평단 ${h.avg_usd:,.2f} → ${h.last_usd:,.2f}"
            f" · {h.pnl_krw:+,}원"
        )
    switch = "켜짐" if nh.credentials.trading_enabled else "꺼짐"
    print(f"  주문 스위치 NAMU_TRADING_ENABLED: {switch}")
    return 0


def _until(nh: NamuGlobal, oid: str, done: Callable[[NamuOrderStatus], bool]) -> NamuOrderStatus:
    waited = 0.0
    while True:
        st = nh.order_status(oid)
        if done(st) or waited >= WAIT_S:
            return st
        nh.sleep(POLL_S)
        waited += POLL_S


def _st(st: NamuOrderStatus) -> dict[str, Any]:
    d = asdict(st)
    return {k: (str(v) if isinstance(v, Decimal) else v) for k, v in d.items()}


def mock_test(symbol: str) -> int:
    nh = client(live=False, allowed=frozenset({symbol}), max_usd=2_000.0)
    log: dict[str, Any] = {"symbol": symbol, "at": datetime.now(KST).isoformat(timespec="seconds")}
    try:
        q = nh.quote(symbol)
        log["quote"] = {"ask": q.ask, "bid": q.bid, "last": q.last, "session": q.session}
        log["buyable"] = nh.buyable_krw(symbol, q.ask)
        # 1. 미체결 → 취소
        far = Decimal(str(round(q.bid * 0.95, 2)))
        r = nh.order(symbol, "BUY", 1, far)
        log["t1_order"] = asdict(r)
        if r.accepted:
            log["t1_open"] = _st(_until(nh, r.code, lambda s: s.status != ""))
            c = nh.cancel(r.code, symbol)
            log["t1_cancel"] = asdict(c)
            log["t1_after"] = _st(_until(nh, r.code, lambda s: s.status == "CANCELED"))
        # 2. 체결 → 되팔기
        r2 = nh.order(symbol, "BUY", 1, Decimal(str(round(q.ask, 2))))
        log["t2_buy"] = asdict(r2)
        if r2.accepted:
            log["t2_buy_after"] = _st(_until(nh, r2.code, lambda s: s.status == "FILLED"))
            log["t2_held"] = [asdict(h) for h in nh.holdings()[1]]
            log["t2_sellable"] = nh.sellable(symbol)
            b = nh.quote(symbol).bid
            r3 = nh.order(symbol, "SELL", 1, Decimal(str(round(b, 2))))
            log["t2_sell"] = asdict(r3)
            if r3.accepted:
                log["t2_sell_after"] = _st(_until(nh, r3.code, lambda s: s.status == "FILLED"))
        day = datetime.now(KST).strftime("%Y%m%d")
        log["raw_orders"] = nh.orders(day)
    finally:
        nh.close()
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{datetime.now(KST):%Y-%m-%d-%H%M}-mock.json"
    path.write_text(json.dumps(log, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(
        json.dumps(
            {k: v for k, v in log.items() if k != "raw_orders"},
            ensure_ascii=False,
            indent=1,
            default=str,
        )
    )
    print(f"→ {path.relative_to(REPO)}")
    return 0


def main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else ""
    if cmd == "check":
        return check()
    if cmd == "mock-test":
        return mock_test((argv[2] if len(argv) > 2 else "AAPL").upper())
    print(__doc__)
    return 2


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            _reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main(sys.argv))
