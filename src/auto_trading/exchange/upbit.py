"""업비트 원화 현물 — 조회 · 지정가 IOC 주문 (T/v2 업비트 실거래, 2026-10-09 사용자 결정).

* 인증: JWT(HS256) — access_key · nonce · (매개변수가 있으면) query_hash(SHA512).
  비밀키는 서명에만 쓰고 출력 · 파일 · 오류 문구에 남기지 않는다.
* 주문은 **지정가 IOC 만** 낸다(사용자 규칙: 시장가 금지). 가격은 호가창에 있는 가격을 그대로 쓰므로
  호가 단위 표가 필요 없다.
* 같은 ``identifier`` 로 두 번 주문하지 않는다 — 결과를 모르면 :meth:`get_order` 로만 확인한다.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
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
    identifier: str
    state: str
    """wait · watch · done · cancel."""
    side: str
    volume: Decimal
    executed_volume: Decimal
    paid_fee: Decimal
    funds: Decimal
    """체결 금액 합(원, 수수료 제외)."""


def _b64(raw: bytes) -> bytes:
    return base64.urlsafe_b64encode(raw).rstrip(b"=")


def plain(x: Decimal) -> str:
    """지수 표기 없는 문자열 (업비트는 '1E+5' 를 받지 않는다)."""
    s = format(x, "f")
    return s.rstrip("0").rstrip(".") if "." in s else s


class UpbitClient:
    __slots__ = ("_ak", "_client", "_sk")

    def __init__(
        self, access_key: str, secret_key: str, *, client: httpx.Client | None = None
    ) -> None:
        self._ak, self._sk = access_key, secret_key
        self._client = client if client is not None else httpx.Client(timeout=15.0)

    def __repr__(self) -> str:
        return "UpbitClient(***)"

    # ------------------------------------------------------------ 인증
    def _token(self, params: Mapping[str, str] | None = None) -> str:
        payload: dict[str, str] = {"access_key": self._ak, "nonce": str(uuid.uuid4())}
        if params:
            q = urlencode(params).encode()
            payload["query_hash"] = hashlib.sha512(q).hexdigest()
            payload["query_hash_alg"] = "SHA512"
        head = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
        body = _b64(json.dumps(payload).encode())
        sig = _b64(hmac.new(self._sk.encode(), head + b"." + body, hashlib.sha256).digest())
        return (head + b"." + body + b"." + sig).decode()

    def _get(self, path: str, params: Mapping[str, str] | None = None, *, auth: bool) -> Any:
        headers = {"Authorization": "Bearer " + self._token(params)} if auth else {}
        try:
            r = self._client.get(f"{API}{path}", params=dict(params or {}), headers=headers)
        except httpx.HTTPError as exc:
            raise ExchangeError(f"업비트 조회 실패 ({type(exc).__name__})") from None
        if r.status_code == 404 and path == "/order":
            return None
        if r.status_code != 200:
            raise ExchangeError(f"업비트 조회 {path} {r.status_code}: {r.text[:200]}")
        return r.json()

    # ------------------------------------------------------------ 시세 (공개)
    def tickers(self, markets: list[str]) -> dict[str, Decimal]:
        rows = self._get("/ticker", {"markets": ",".join(markets)}, auth=False)
        return {r["market"]: Decimal(str(r["trade_price"])) for r in rows}

    def orderbook(self, market: str) -> tuple[list[Decimal], list[Decimal]]:
        """(매도 호가 가격들 낮은 순, 매수 호가 가격들 높은 순)."""
        rows = self._get("/orderbook", {"markets": market}, auth=False)
        units = rows[0]["orderbook_units"]
        asks = [Decimal(str(u["ask_price"])) for u in units]
        bids = [Decimal(str(u["bid_price"])) for u in units]
        return asks, bids

    def closed_daily(self, market: str, as_of_ms: int, count: int = 200) -> list[Candle]:
        """``as_of_ms`` 까지 마감된 일봉(00:00 UTC 경계), 오래된 것 먼저."""
        rows = self._get("/candles/days", {"market": market, "count": str(count)}, auth=False)
        out = []
        for r in rows:
            opened = datetime.fromisoformat(str(r["candle_date_time_utc"])).replace(tzinfo=UTC)
            open_ms = int(opened.timestamp() * 1000)
            if open_ms + _DAY_MS - 1 > as_of_ms:
                continue
            out.append(
                Candle(
                    open_ms=open_ms,
                    close_ms=open_ms + _DAY_MS - 1,
                    open=Decimal(str(r["opening_price"])),
                    high=Decimal(str(r["high_price"])),
                    low=Decimal(str(r["low_price"])),
                    close=Decimal(str(r["trade_price"])),
                    volume=Decimal(str(r["candle_acc_trade_volume"])),
                    quote_volume=Decimal(str(r["candle_acc_trade_price"])),
                )
            )
        return sorted(out, key=lambda c: c.open_ms)

    # ------------------------------------------------------------ 계좌
    def balances(self) -> dict[str, Decimal]:
        """통화 → 잔고(주문에 묶인 것 포함)."""
        rows = self._get("/accounts", auth=True)
        return {r["currency"]: Decimal(str(r["balance"])) + Decimal(str(r["locked"])) for r in rows}

    def get_order(self, identifier: str) -> UpbitOrder | None:
        """``identifier`` 로 조회. 없으면 None (주문이 들어가지 않았다)."""
        data = self._get("/order", {"identifier": identifier}, auth=True)
        return None if data is None else _order(data)

    def place_limit_ioc(
        self, market: str, side: str, volume: Decimal, price: Decimal, identifier: str
    ) -> UpbitOrder:
        """지정가 IOC. ``side`` = 'bid'(매수) · 'ask'(매도)."""
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
            r = self._client.post(f"{API}/orders", json=body, headers=headers)
        except httpx.HTTPError as exc:
            raise OrderStateUnknownError(
                f"업비트 주문 전송 결과 모름 ({type(exc).__name__}) — 조회로만 확인"
            ) from None
        if r.status_code >= 500:
            raise OrderStateUnknownError(f"업비트 주문 서버 오류 {r.status_code} — 조회로만 확인")
        if r.status_code not in (200, 201):
            raise ExchangeError(f"업비트 주문 거절 {r.status_code}: {r.text[:200]}")
        return _order(r.json())

    def close(self) -> None:
        self._client.close()


def _order(d: Mapping[str, Any]) -> UpbitOrder:
    trades = d.get("trades") or []
    funds = sum((Decimal(str(t.get("funds", "0"))) for t in trades), Decimal(0))
    return UpbitOrder(
        identifier=str(d.get("identifier") or ""),
        state=str(d.get("state") or ""),
        side=str(d.get("side") or ""),
        volume=Decimal(str(d.get("volume") or "0")),
        executed_volume=Decimal(str(d.get("executed_volume") or "0")),
        paid_fee=Decimal(str(d.get("paid_fee") or "0")),
        funds=funds,
    )
