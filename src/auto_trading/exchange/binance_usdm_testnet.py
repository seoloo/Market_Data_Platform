"""Binance USD-M **테스트넷 전용** 주문 어댑터 (Phase 7). 가짜 돈만 움직인다.

읽기는 :class:`BinanceUsdmReadOnly` 를 그대로 쓰고, 주문·취소·설정만 더한다.
**주문을 보낼 수 있는 유일한 어댑터이므로 테스트넷 주소에 묶는다** — 생성 시 다른 주소면 거부한다.
실계좌(mainnet) 주문 경로는 Phase 8 승인 전까지 존재하지 않는다.

테스트넷 주소는 공식 일반 정보 문서의 ``https://demo-fapi.binance.com`` 이다(2026-09-24 확인).
예전 주소 ``testnet.binancefuture.com`` 도 같은 계좌에 닿는 것을 읽기 조회로 확인했다.

전송 결과는 셋으로 나눈다 (주문 관리자가 이 구분으로 REJECTED / UNKNOWN 을 기록한다).

| 거래소 반응 | 분류 | 이유 |
|---|---|---|
| 4xx + Binance 오류 코드 | :class:`OrderRejectedError` | 거래소가 요청을 읽고 **거절했다** |
| 네트워크·5xx·-1007·레이트 리밋 | :class:`OrderStateUnknownError` | 모른다 — 조회로 해소 |

``-1007``은 Binance 의 "백엔드 응답 대기 시간 초과, 실행 상태 알 수 없음"이다.
레이트 리밋은 실제로는 처리되지 않았을 가능성이 크지만 확실하지 않으므로 보수적으로 "모름"으로
둔다 — 조회하면 거래소에 없다는 것이 확인되어 NOT_PLACED 로 닫힌다.

보호 스톱은 Algo Order API 로 보낸다
(:mod:`auto_trading.exchange.futures`, 일반 주문 경로는 ``-4120``).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace
from decimal import Decimal
from typing import Any

import httpx

from auto_trading.config.secrets import BinanceCredentials
from auto_trading.core.errors import (
    ConfigError,
    ExchangeError,
    OrderRejectedError,
    OrderStateUnknownError,
    RateLimitError,
)
from auto_trading.exchange.binance_usdm import BinanceApiError, BinanceUsdmReadOnly
from auto_trading.exchange.futures import FuturesOrderResult, OrderStatus

__all__ = ["TESTNET_HOSTS", "TESTNET_URL", "BinanceUsdmTestnet"]

TESTNET_URL = "https://demo-fapi.binance.com"
TESTNET_HOSTS = frozenset({TESTNET_URL, "https://testnet.binancefuture.com"})
_UNKNOWN_CODES = frozenset({-1007})
_NOT_FOUND = -2013
_NO_CHANGE_MARGIN_TYPE = -4046


def _qty(value: Decimal) -> str:
    """지수 표기 없이 (``5E+1`` 이 아니라 ``50``)."""
    return format(value.normalize(), "f")


class BinanceUsdmTestnet(BinanceUsdmReadOnly):
    __slots__ = ()

    def __init__(
        self,
        credentials: BinanceCredentials,
        *,
        client: httpx.Client | None = None,
        base_url: str = TESTNET_URL,
        clock: Callable[[], int] | None = None,
    ) -> None:
        if base_url.rstrip("/") not in TESTNET_HOSTS:
            raise ConfigError(
                f"테스트넷 전용 어댑터에 테스트넷이 아닌 주소: {base_url} — 실계좌 주문 경로는 없다"
            )
        super().__init__(credentials, client=client, base_url=base_url, clock=clock)

    # ------------------------------------------------------------ 전송

    def _send(self, method: str, path: str, params: Mapping[str, Any]) -> Any:
        try:
            return self._request(path, params, signed=True, method=method)
        except RateLimitError as exc:
            raise OrderStateUnknownError(f"레이트 리밋 — 전송 결과 모름: {exc}") from None
        except BinanceApiError as exc:
            if exc.code is None or exc.code in _UNKNOWN_CODES or exc.status >= 500:
                raise OrderStateUnknownError(f"전송 결과 모름: {exc}") from None
            raise OrderRejectedError(str(exc)) from None
        except ExchangeError as exc:
            raise OrderStateUnknownError(f"전송 결과 모름: {exc}") from None

    def place_market_order(
        self, symbol: str, side: str, quantity: Decimal, client_order_id: str, *, reduce_only: bool
    ) -> FuturesOrderResult:
        data = self._send(
            "POST",
            "/fapi/v1/order",
            {
                "symbol": symbol,
                "side": side,
                "type": "MARKET",
                "quantity": _qty(quantity),
                "newClientOrderId": client_order_id,
                "reduceOnly": "true" if reduce_only else "false",
                "newOrderRespType": "RESULT",
            },
        )
        result = self._order(data)
        if result.executed_qty and not result.avg_price:
            # 응답에 평균가가 없으면(테스트넷 실측) 조회로 채운다.
            # 조회에 실패해도 체결 사실은 바뀌지 않으므로 그대로 둔다.
            try:
                looked = self.get_order(symbol, client_order_id)
            except ExchangeError:
                looked = None
            if looked is not None and looked.avg_price:
                result = replace(result, avg_price=looked.avg_price)
        return result

    def place_limit_ioc(
        self,
        symbol: str,
        side: str,
        quantity: Decimal,
        price: Decimal,
        client_order_id: str,
        *,
        reduce_only: bool,
    ) -> FuturesOrderResult:
        """가격 한도 지정가 IOC — 선물 일반 주문(OPEN·REDUCE·CLOSE)의 시장가 대신 (TRADING_CORE)."""
        data = self._send(
            "POST",
            "/fapi/v1/order",
            {
                "symbol": symbol,
                "side": side,
                "type": "LIMIT",
                "timeInForce": "IOC",
                "quantity": _qty(quantity),
                "price": _qty(price),
                "newClientOrderId": client_order_id,
                "reduceOnly": "true" if reduce_only else "false",
                "newOrderRespType": "RESULT",
            },
        )
        return self._order(data)

    def place_stop_market(
        self,
        symbol: str,
        side: str,
        stop_price: Decimal,
        client_order_id: str,
        *,
        quantity: Decimal | None = None,
    ) -> FuturesOrderResult:
        """보호 스톱. 기본은 closePosition(포지션 전체).

        ``quantity`` 를 주면 **수량 지정 reduce-only** 스톱이다.
        매일 스톱 교체를 공백 없이 할 수 있는지 재는 훈련(docs/research/52)에서 쓴다.
        운영 실행기는 아직 closePosition 만 쓴다.
        """
        params: dict[str, Any] = {
            "algoType": "CONDITIONAL",
            "symbol": symbol,
            "side": side,
            "type": "STOP_MARKET",
            "triggerPrice": _qty(stop_price),
            "workingType": "MARK_PRICE",
            "clientAlgoId": client_order_id,
        }
        if quantity is None:
            params["closePosition"] = "true"
        else:
            params["quantity"] = _qty(quantity)
            params["reduceOnly"] = "true"
        return self._stop(self._send("POST", "/fapi/v1/algoOrder", params))

    def place_limit_order(
        self, symbol: str, side: str, quantity: Decimal, price: Decimal, client_order_id: str
    ) -> FuturesOrderResult:
        """post-only 지정가(GTX) 진입. 바로 체결될 가격이면 거래소가 만료시킨다.

        Elliott 데모(docs/research/56)의 되돌림 예약 진입에서 쓴다.
        """
        data = self._send(
            "POST",
            "/fapi/v1/order",
            {
                "symbol": symbol,
                "side": side,
                "type": "LIMIT",
                "timeInForce": "GTX",
                "quantity": _qty(quantity),
                "price": _qty(price),
                "newClientOrderId": client_order_id,
                "newOrderRespType": "RESULT",
            },
        )
        return self._order(data)

    def place_take_profit_market(
        self, symbol: str, side: str, trigger_price: Decimal, client_order_id: str
    ) -> FuturesOrderResult:
        """목표가 전량 청산 (closePosition, MARK_PRICE 발동). 스톱과 같은 Algo 경로."""
        params: dict[str, Any] = {
            "algoType": "CONDITIONAL",
            "symbol": symbol,
            "side": side,
            "type": "TAKE_PROFIT_MARKET",
            "triggerPrice": _qty(trigger_price),
            "workingType": "MARK_PRICE",
            "clientAlgoId": client_order_id,
            "closePosition": "true",
        }
        return self._stop(self._send("POST", "/fapi/v1/algoOrder", params))

    def asset_balance(self, asset: str) -> Decimal:
        """자산 하나의 지갑 잔고 (USDC 계약용 — ``account()`` 합계는 USDT 기준이다)."""
        rows = self._request("/fapi/v2/balance", signed=True)
        for r in rows:
            if r.get("asset") == asset:
                return Decimal(str(r["balance"]))
        return Decimal(0)

    def trade_fills(self, symbol: str, exchange_order_id: str) -> list[dict[str, Any]]:
        """체결 내역(최근 7일) 그대로 — 수수료 자산을 가리지 않는다 (USDC 계약용)."""
        rows = self._request(
            "/fapi/v1/userTrades", {"symbol": symbol, "orderId": exchange_order_id}, signed=True
        )
        return [
            {
                "price": Decimal(str(r["price"])),
                "qty": Decimal(str(r["qty"])),
                "quote": Decimal(str(r["quoteQty"])),
                "commission": Decimal(str(r["commission"])),
                "commission_asset": str(r["commissionAsset"]),
                "maker": bool(r["maker"]),
                "realized_pnl": Decimal(str(r.get("realizedPnl", "0"))),
            }
            for r in rows
        ]

    def _cancel(self, path: str, params: Mapping[str, Any]) -> Any:
        """취소 요청. 성공하면 응답 본문, 거래소에 없으면 None.

        이미 끝났으면 OrderRejectedError, 결과를 모르면 ExchangeError.
        """
        try:
            return self._request(path, params, signed=True, method="DELETE")
        except BinanceApiError as exc:
            if exc.code == _NOT_FOUND:
                return None
            if exc.code is not None and exc.status < 500:
                raise OrderRejectedError(str(exc)) from None
            raise

    def cancel_order(self, symbol: str, client_order_id: str) -> FuturesOrderResult | None:
        data = self._cancel(
            "/fapi/v1/order", {"symbol": symbol, "origClientOrderId": client_order_id}
        )
        return None if data is None else self._order(data)  # 취소 응답이 주문 본문이다

    def cancel_stop(self, symbol: str, client_order_id: str) -> FuturesOrderResult | None:
        if self._cancel("/fapi/v1/algoOrder", {"clientAlgoId": client_order_id}) is None:
            return None
        result = self.get_stop(symbol, client_order_id)
        if result is not None and result.status is OrderStatus.NEW:
            # 테스트넷 실측: 취소 성공 직후 조회가 아직 NEW 였다. 성공 응답을 기준으로 삼는다.
            result = replace(result, status=OrderStatus.CANCELED)
        return result

    # ------------------------------------------------------------ 계좌 설정

    def dual_side_position(self) -> bool:
        """헤지 모드면 True. 이 시스템은 one-way 만 쓴다."""
        data = self._request("/fapi/v1/positionSide/dual", signed=True)
        return bool(data.get("dualSidePosition"))

    def set_leverage(self, symbol: str, leverage: int) -> int:
        data = self._request(
            "/fapi/v1/leverage",
            {"symbol": symbol, "leverage": leverage},
            signed=True,
            method="POST",
        )
        return int(data.get("leverage", leverage))

    def set_margin_type(self, symbol: str, margin_type: str) -> None:
        """``CROSSED`` 또는 ``ISOLATED``. 이미 그 모드면 조용히 넘어간다 (-4046)."""
        try:
            self._request(
                "/fapi/v1/marginType",
                {"symbol": symbol, "marginType": margin_type},
                signed=True,
                method="POST",
            )
        except BinanceApiError as exc:
            if exc.code != _NO_CHANGE_MARGIN_TYPE:
                raise
