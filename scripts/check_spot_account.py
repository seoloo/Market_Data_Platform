"""Binance 현물 읽기 전용 점검 (T/v2 Phase 5-4). 주문을 보내지 않는다.

1. 키 권한: 읽기 외 권한(현물 거래·출금·선물 등)이 켜져 있거나 IP 제한이 없으면 경고하고 종료 코드 2
2. 서버 시계 차이
3. 0이 아닌 잔고
4. BTCUSDT·ETHUSDT 주문 규칙(step·최소 수량·최소 주문 금액)과 현재가

키 값은 출력하지 않는다. 사용 예::

    python scripts/check_spot_account.py
"""

from __future__ import annotations

import sys

from auto_trading.config.secrets import load_binance_credentials
from auto_trading.core.errors import AutoTradingError
from auto_trading.exchange.binance_spot import BinanceSpotReadOnly

for _stream in (sys.stdout, sys.stderr):
    _reconfigure = getattr(_stream, "reconfigure", None)
    if _reconfigure is not None:
        _reconfigure(encoding="utf-8", errors="replace")

SYMBOLS = ("BTCUSDT", "ETHUSDT")
_MUST_BE_OFF = (
    "enableSpotAndMarginTrading",
    "enableWithdrawals",
    "enableFutures",
    "enableMargin",
    "enableInternalTransfer",
    "permitsUniversalTransfer",
    "enableVanillaOptions",
    "enablePortfolioMarginTrading",
)


def main() -> int:
    creds = load_binance_credentials()
    ex = BinanceSpotReadOnly(creds)
    try:
        flags = ex.api_restrictions()
        risky = [name for name in _MUST_BE_OFF if flags.get(name)]
        print("키 권한:", ", ".join(f"{k}={v}" for k, v in sorted(flags.items())))
        if not flags.get("enableReading"):
            print("⚠ 읽기 권한이 꺼져 있다.")
            return 2
        if risky:
            print(f"⚠ 읽기 전용 키가 아니다: 켜진 권한 {risky}")
            print("  키를 삭제하고 Enable Reading만 켠 새 키를 .env에 넣은 뒤 다시 실행한다.")
            return 2
        if not flags.get("ipRestrict"):
            print("⚠ 권한은 읽기 전용이지만 IP 제한이 없다.")
            print(
                "  Binance API 관리 → 이 키 → Restrict access to trusted IPs 를 켠 뒤 다시 실행한다."
            )
            return 2
        print("✓ 읽기 전용 · IP 제한 키")
        print(f"서버 시계 차이: {ex.clock_offset_ms()} ms")
        balances = ex.balances()
        print(
            "잔고(0 제외):",
            ", ".join(f"{a} {q.normalize()}" for a, q in sorted(balances.items())) or "없음",
        )
        for symbol in SYMBOLS:
            f = ex.filters(symbol)
            print(
                f"{symbol}: 현재가 {ex.last_price(symbol).normalize()} · step {f.step_size} ·"
                f" 최소 수량 {f.min_qty} · 최소 주문 {f.min_notional} USDT"
            )
    except AutoTradingError as exc:
        print(f"조회 실패: {exc}")
        return 1
    finally:
        ex.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
