"""업비트 원화 현물 어댑터 — 조회 · 지정가 IOC 주문 (T/v2 업비트 실거래, 2026-10-09 사용자 결정).

언제 쓰나: Vega(매일 09:05) · 전부 정리 · 현황 화면 · 안전 점검이 업비트 계좌를 볼 때와 주문할 때.
읽고 쓰는 것: 업비트 API 만 (파일을 쓰지 않는다).

* 인증: JWT(HS256) — access_key · nonce · (매개변수가 있으면) query_hash(SHA512).
  비밀키는 서명에만 쓰고 출력 · 파일 · 오류 문구에 남기지 않는다.
* 주문은 **지정가 IOC 만** 낸다 (2026-10-09 당시 사용자 규칙 '시장가 금지' — 규칙은 10-18 에
  지워졌지만 이 코드는 그대로 둔다). 가격은 호가창에 있는 가격을 그대로 써서
  호가 단위 표가 필요 없다.
* 같은 ``identifier`` 로 두 번 주문하지 않는다 — 결과를 모르면 :meth:`get_order` 로만 확인한다.

디자인 패턴: **Adapter** — 업비트 JSON 을 우리 코드가 쓰는 모양(``UpbitOrder`` · ``Candle``)으로.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from urllib.parse import urlencode

import httpx

from auto_trading.core.errors import ExchangeError, OrderStateUnknownError
from auto_trading.core.types import Candle

API = "https://api.upbit.com/v1"
_DAY_MS = 86_400_000


@dataclass(frozen=True, slots=True)
class UpbitOrder:
    """주문 하나의 상태 (업비트 ``/order`` 응답)."""

    identifier: str
    """우리가 붙인 주문 번호 (같은 번호로 두 번 주문하지 않으려고)."""
    state: str
    """wait · watch · done · cancel."""
    side: str
    volume: Decimal
    executed_volume: Decimal
    paid_fee: Decimal
    funds: Decimal
    """체결 금액 합(원, 수수료 제외)."""


def _base64url(raw: bytes) -> bytes:
    """[쓰임] JWT 용 base64url (끝의 ``=`` 를 뗀다 — JWT 규격)."""
    return base64.urlsafe_b64encode(raw).rstrip(b"=")


def plain(amount: Decimal) -> str:
    """[쓰임] 지수 표기 없는 숫자 글자 (``1E+5`` → ``100000``, ``0.50`` → ``0.5``).
    [이유] 업비트는 ``1E+5`` 같은 지수 표기를 받지 않는다."""
    text = format(amount, "f")
    if "." not in text:
        return text
    return text.rstrip("0").rstrip(".")


def _to_decimal(value: Any) -> Decimal:
    """[쓰임] 업비트 숫자 값 → Decimal (비었으면 0)."""
    return Decimal(str(value or "0"))


class UpbitClient:
    """[쓰임] 업비트 조회 · 주문 창구 하나.
    [이유] 주문 허용(``trading_enabled``)이 기본 꺼짐이라, 조회만 하는 곳(현황 · 안전 점검)은
           실수로도 주문할 수 없다."""

    __slots__ = ("_access_key", "_client", "_secret_key", "trading_enabled")

    def __init__(
        self,
        access_key: str,
        secret_key: str,
        *,
        client: httpx.Client | None = None,
        trading_enabled: bool = False,
    ) -> None:
        self._access_key = access_key
        self._secret_key = secret_key
        self._client = client if client is not None else httpx.Client(timeout=15.0)
        self.trading_enabled = trading_enabled
        """주문 허용 — 2026-10-16 점검에서 추가. 기본 꺼짐(조회만), 부르는 쪽이
        :func:`upbit_trading_enabled` 로 켠다. 예전엔 스위치가 없어 키만 있으면 주문이 나갔다."""

    def __repr__(self) -> str:
        return "UpbitClient(***)"

    # ------------------------------------------------------------ 인증
    def _token(self, params: Mapping[str, str] | None = None) -> str:
        """[쓰임] 요청 하나에 쓸 JWT 서명 토큰 (요청마다 새 nonce).
        [이유] 업비트 규격: 매개변수가 있으면 그 내용의 SHA512 해시를 함께 서명해야 한다."""
        payload: dict[str, str] = {"access_key": self._access_key, "nonce": str(uuid.uuid4())}
        if params:
            query = urlencode(params).encode()
            payload["query_hash"] = hashlib.sha512(query).hexdigest()
            payload["query_hash_alg"] = "SHA512"
        header_part = _base64url(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
        payload_part = _base64url(json.dumps(payload).encode())
        signed = header_part + b"." + payload_part
        signature = hmac.new(self._secret_key.encode(), signed, hashlib.sha256).digest()
        return (signed + b"." + _base64url(signature)).decode()

    def _get(self, path: str, params: Mapping[str, str] | None = None, *, auth: bool) -> Any:
        """[쓰임] 조회 GET 하나. ``auth`` = 계좌 조회라 서명이 필요한가.
        [이유] ``/order`` 의 404 는 '그 번호로 들어간 주문이 없음' 이라 오류가 아니라 None."""
        headers = {"Authorization": "Bearer " + self._token(params)} if auth else {}
        try:
            response = self._client.get(f"{API}{path}", params=dict(params or {}), headers=headers)
        except httpx.HTTPError as exc:
            raise ExchangeError(f"업비트 조회 실패 ({type(exc).__name__})") from None
        if response.status_code == 404 and path == "/order":
            return None
        if response.status_code != 200:
            raise ExchangeError(f"업비트 조회 {path} {response.status_code}: {response.text[:200]}")
        return response.json()

    # ------------------------------------------------------------ 시세 (공개)
    def tickers(self, markets: list[str]) -> dict[str, Decimal]:
        """[쓰임] 현재가 {마켓(KRW-BTC 등): 가격}."""
        rows = self._get("/ticker", {"markets": ",".join(markets)}, auth=False)
        return {row["market"]: Decimal(str(row["trade_price"])) for row in rows}

    def orderbook(self, market: str) -> tuple[list[Decimal], list[Decimal]]:
        """[쓰임] 호가창 (매도 호가 가격들 낮은 순, 매수 호가 가격들 높은 순)."""
        rows = self._get("/orderbook", {"markets": market}, auth=False)
        levels = rows[0]["orderbook_units"]
        ask_prices = [Decimal(str(level["ask_price"])) for level in levels]
        bid_prices = [Decimal(str(level["bid_price"])) for level in levels]
        return ask_prices, bid_prices

    def closed_daily(self, market: str, as_of_ms: int, count: int = 200) -> list[Candle]:
        """[쓰임] ``as_of_ms`` 까지 마감된 일봉(00:00 UTC 경계), 오래된 것 먼저.
        [이유] 아직 안 끝난 오늘 봉으로 판단하면 하루 안에서 신호가 바뀔 수 있어 뺀다."""
        rows = self._get("/candles/days", {"market": market, "count": str(count)}, auth=False)
        candles = []
        for row in rows:
            opened = datetime.fromisoformat(str(row["candle_date_time_utc"])).replace(tzinfo=UTC)
            open_ms = int(opened.timestamp() * 1000)
            close_ms = open_ms + _DAY_MS - 1
            if close_ms > as_of_ms:
                continue
            candles.append(
                Candle(
                    open_ms=open_ms,
                    close_ms=close_ms,
                    open=Decimal(str(row["opening_price"])),
                    high=Decimal(str(row["high_price"])),
                    low=Decimal(str(row["low_price"])),
                    close=Decimal(str(row["trade_price"])),
                    volume=Decimal(str(row["candle_acc_trade_volume"])),
                    quote_volume=Decimal(str(row["candle_acc_trade_price"])),
                )
            )
        return sorted(candles, key=lambda candle: candle.open_ms)

    # ------------------------------------------------------------ 계좌
    def balances(self) -> dict[str, Decimal]:
        """[쓰임] 통화 → 잔고 (주문에 묶인 것 포함)."""
        rows = self._get("/accounts", auth=True)
        totals = {}
        for row in rows:
            totals[row["currency"]] = Decimal(str(row["balance"])) + Decimal(str(row["locked"]))
        return totals

    def accounts(self) -> dict[str, tuple[Decimal, Decimal]]:
        """[쓰임] 통화 → (잔고 · 주문에 묶인 것 포함, 업비트 앱의 매수 평균가). 원화는 평균가 0.
        [이유] 2026-10-20 사용자 'vega 알림에 오는 수익이 업비트에 있는 것과 다른데 … 동기화' —
               코인별 원가를 업비트가 보여 주는 평균가로 맞추려고."""
        rows = self._get("/accounts", auth=True)
        result = {}
        for row in rows:
            total = Decimal(str(row["balance"])) + Decimal(str(row["locked"]))
            average_price = Decimal(str(row.get("avg_buy_price") or "0"))
            result[row["currency"]] = (total, average_price)
        return result

    def get_order(self, identifier: str) -> UpbitOrder | None:
        """[쓰임] 우리 주문 번호(``identifier``)로 조회. 없으면 None (주문이 들어가지 않았다)."""
        data = self._get("/order", {"identifier": identifier}, auth=True)
        if data is None:
            return None
        return _order_from_json(data)

    def place_limit_ioc(
        self, market: str, side: str, volume: Decimal, price: Decimal, identifier: str
    ) -> UpbitOrder:
        """[쓰임] 지정가 IOC 주문 — 지금 그 가격에 체결될 만큼만 사고 남은 것은 바로 취소.
        ``side`` = 'bid'(매수) · 'ask'(매도). 주문 허용이 꺼져 있으면 막는다.
        [이유] 주문은 다시 보내면 두 번 살 수 있어, 네트워크 오류 · 5xx 는 다시 보내지 않고
               '결과 모름'(OrderStateUnknownError)으로 올린다 — 다음은 조회로만 확인."""
        if not self.trading_enabled:
            raise ExchangeError("UPBIT_TRADING_ENABLED=true 가 아니라 업비트 주문하지 않는다")
        if side not in ("bid", "ask"):
            raise ExchangeError(f"주문 방향 오류: {side}")
        body = {
            "market": market,
            "side": side,
            "volume": plain(volume),
            "price": plain(price),
            "ord_type": "limit",
            "time_in_force": "ioc",
            "identifier": identifier,
        }
        headers = {"Authorization": "Bearer " + self._token(body)}
        try:
            response = self._client.post(f"{API}/orders", json=body, headers=headers)
        except httpx.HTTPError as exc:
            raise OrderStateUnknownError(
                f"업비트 주문 전송 결과 모름 ({type(exc).__name__}) — 조회로만 확인"
            ) from None
        if response.status_code >= 500:
            raise OrderStateUnknownError(
                f"업비트 주문 서버 오류 {response.status_code} — 조회로만 확인"
            )
        if response.status_code not in (200, 201):
            raise ExchangeError(f"업비트 주문 거절 {response.status_code}: {response.text[:200]}")
        return _order_from_json(response.json())

    def close(self) -> None:
        """[쓰임] 연결을 닫는다 (실행이 끝날 때)."""
        self._client.close()


def _order_from_json(data: Mapping[str, Any]) -> UpbitOrder:
    """[쓰임] 업비트 주문 응답 → :class:`UpbitOrder`. 체결 금액은 체결(trades)마다 더한다."""
    trades = data.get("trades") or []
    funds = Decimal(0)
    for trade in trades:
        funds += Decimal(str(trade.get("funds", "0")))
    return UpbitOrder(
        identifier=str(data.get("identifier") or ""),
        state=str(data.get("state") or ""),
        side=str(data.get("side") or ""),
        volume=_to_decimal(data.get("volume")),
        executed_volume=_to_decimal(data.get("executed_volume")),
        paid_fee=_to_decimal(data.get("paid_fee")),
        funds=funds,
    )


def upbit_trading_enabled(env: Mapping[str, str]) -> bool:
    """[쓰임] 업비트 주문을 켜도 되나 — ``UPBIT_TRADING_ENABLED`` 가 정확히 ``true`` 일 때만.
    [이유] 사람이 ``.env`` 에서 직접 켜는 스위치.
           시스템 환경 변수가 있으면 그것이 ``.env`` 보다 앞선다."""
    raw = os.environ.get("UPBIT_TRADING_ENABLED", env.get("UPBIT_TRADING_ENABLED", ""))
    return raw.strip().lower() == "true"
