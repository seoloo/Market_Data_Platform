"""Binance USD-M 선물 읽기 전용 점검 (Phase 5 선물). 주문을 보내지 않는다.

1. 키 권한: 출금·거래 권한이 켜져 있거나 IP 제한이 없으면 조회하지 않고 종료 코드 2
2. 선물 서버 시계 차이
3. BTCUSDT·ETHUSDT 거래 규칙(step·최소 수량·최소 명목·tick)과 mark 가격
4. 선물 지갑·포지션·걸린 일반 주문·걸린 보호 스톱(Algo)

키 값은 출력하지 않는다. 사용 예::

    python scripts/check_futures_account.py

선물 계좌 조회가 권한 오류(-2015)로 막히면: 선물 계좌가 아직 열리지 않았거나,
키에 선물 읽기가 허용되지 않은 경우다. **선물 거래 권한을 켜라는 뜻이 아니다** —
거래 권한은 Phase 8(소액 실거래) 승인 뒤의 일이다.
"""

from __future__ import annotations

import sys

from auto_trading.config.secrets import load_binance_credentials
from auto_trading.core.errors import AutoTradingError, ExchangeError
from auto_trading.exchange.binance_spot import BinanceSpotReadOnly
from auto_trading.exchange.binance_usdm import BinanceUsdmReadOnly

for _stream in (sys.stdout, sys.stderr):
    _reconfigure = getattr(_stream, "reconfigure", None)
    if _reconfigure is not None:
        _reconfigure(encoding="utf-8", errors="replace")

SYMBOLS = ("BTCUSDT", "ETHUSDT")
_MUST_BE_OFF = (
    "enableWithdrawals",
    "enableSpotAndMarginTrading",
    "enableFutures",
    "enableMargin",
    "enableInternalTransfer",
    "permitsUniversalTransfer",
    "enableVanillaOptions",
    "enablePortfolioMarginTrading",
)


def main() -> int:
    creds = load_binance_credentials()
    spot = BinanceSpotReadOnly(creds)
    futures = BinanceUsdmReadOnly(creds)
    try:
        flags = spot.api_restrictions()
        risky = [name for name in _MUST_BE_OFF if flags.get(name)]
        if not flags.get("enableReading"):
            print("⚠ 읽기 권한이 꺼져 있다.")
            return 2
        if risky:
            print(f"⚠ 읽기 전용 키가 아니다: 켜진 권한 {risky} — 조회하지 않는다.")
            return 2
        if not flags.get("ipRestrict"):
            print("⚠ IP 제한이 없는 키다 — 조회하지 않는다.")
            return 2
        print("✓ 읽기 전용 · IP 제한 키")
        print(f"선물 서버 시계 차이: {futures.clock_offset_ms()} ms")
        for symbol in SYMBOLS:
            f = futures.filters(symbol)
            print(
                f"{symbol}: mark {futures.mark_price(symbol):f} · step {f.step_size:f} ·"
                f" 최소 수량 {f.min_qty:f} · 최소 명목 {f.min_notional:f} USDT"
                f" · tick {f.tick_size:f}"
            )
        try:
            account = futures.account()
        except ExchangeError as exc:
            print(f"선물 계좌 조회 실패: {exc}")
            if "-2015" in str(exc):
                print("  선물 계좌가 아직 없거나 이 키로 선물 읽기가 허용되지 않은 경우다.")
                print("  거래 권한을 켜지 않는다 — 거래 권한은 Phase 8 승인 뒤의 일이다.")
            return 1
        print(
            f"선물 지갑 {account.wallet_balance:f} USDT"
            f" · 사용 가능 {account.available_balance:f} USDT"
        )
        if account.positions:
            for p in account.positions.values():
                print(
                    f"  포지션 {p.symbol} {p.quantity.normalize()} @ {p.entry_price.normalize()}"
                    f" · {p.leverage}배"
                )
        else:
            print("  포지션 없음")
        for symbol in SYMBOLS:
            orders, stops = futures.open_orders(symbol), futures.open_stops(symbol)
            print(f"  {symbol}: 걸린 일반 주문 {len(orders)}건 · 걸린 보호 스톱 {len(stops)}건")
    except AutoTradingError as exc:
        print(f"조회 실패: {exc}")
        return 1
    finally:
        spot.close()
        futures.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
