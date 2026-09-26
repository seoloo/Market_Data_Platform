"""한국투자증권(KIS) 실전 계좌 **읽기 전용** 점검. 주문을 보내지 않는다.

1. `.env` 의 KIS 값 확인 (값은 출력하지 않는다) · 주문 스위치 상태
2. 접근 토큰 (``data/kis_token.json`` 캐시 — 1분에 1회 발급 제한 때문)
3. SPY·QQQ·GLD·SLV 현재가와 최근 일봉
4. 해외주식 잔고 (보유 종목 수·평가 합계)
5. 주문 차단 확인 (네트워크에 닿지 않는다)

사용 예::

    python -m scripts.check_kis_account             # 빠른 점검
    python -m scripts.check_kis_account --history   # 일봉 시작일까지 (종목당 약 50번 조회)
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from auto_trading.config.secrets import load_kis_credentials
from auto_trading.core.errors import AutoTradingError
from auto_trading.exchange.kis import KisOverseasReadOnly, KisTokenCache
from auto_trading.exchange.overseas import TradingDisabledError

REPO = Path(__file__).resolve().parent.parent
SYMBOLS = ("SPY", "QQQ", "GLD", "SLV")


def _day(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m-%d")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history", action="store_true", help="일봉 시작일까지 찾는다")
    args = parser.parse_args()
    try:
        creds = load_kis_credentials(REPO / ".env")
    except AutoTradingError as exc:
        print(f"설정 오류: {exc}")
        return 2
    print(f"자격: {creds}")
    print(
        f"주문 스위치 KIS_TRADING_ENABLED = {creds.trading_enabled}"
        " (어느 쪽이든 이 어댑터는 주문하지 않는다)"
    )
    broker = KisOverseasReadOnly(creds, KisTokenCache(REPO / "data" / "kis_token.json"))
    problems = 0
    try:
        now = int(time.time() * 1000)
        for symbol in SYMBOLS:
            try:
                q = broker.quote(symbol)
                bars = broker.closed_daily(symbol, now, limit=5)
                last = f"{_day(bars[-1].open_ms)} 종가 {bars[-1].close}" if bars else "일봉 없음"
                print(
                    f"{symbol:4} ({q.exchange}) 현재 {q.last} · 전일 {q.prev_close}"
                    f" · 최근 일봉 {last}"
                )
                if args.history:
                    full = broker.daily_history(symbol, since="1990-01-01")
                    print(
                        f"      일봉 {len(full)}개 · {_day(full[0].open_ms)}"
                        f" ~ {_day(full[-1].open_ms)}"
                        f" · 건너뛴 줄 누적 {broker.skipped_rows}"
                    )
            except AutoTradingError as exc:
                problems += 1
                print(f"{symbol:4} 실패: {exc}")
        try:
            bal = broker.overseas_balance()
            print(
                f"해외주식 잔고: 보유 {len(bal.positions)}종목"
                f" · 평가 합계 {bal.value} {bal.currency}"
            )
        except AutoTradingError as exc:
            problems += 1
            print(f"잔고 실패: {exc}")
        try:
            broker.place_order("SPY", "BUY", 1)
            problems += 1
            print("⚠ 주문 차단이 작동하지 않았다")
        except TradingDisabledError:
            print("주문 차단: 확인 (place_order 는 항상 막힌다)")
    finally:
        broker.close()
    return 1 if problems else 0


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            _reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
