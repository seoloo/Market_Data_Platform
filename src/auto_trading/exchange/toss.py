"""토스증권 Open API 어댑터 (docs/research/118 §6) — 미국 주식 적립·QS 용.

공식 명세: ``https://openapi.tossinvest.com/openapi-docs/latest/openapi.json`` (v1.2.19 로 확인).

* 인증: OAuth 2.0 Client Credentials (``POST /oauth2/token``) → ``Authorization: Bearer``.
  계좌·주문 API 는 ``X-Tossinvest-Account: {accountSeq}`` 헤더도 필요하다.
* 허용 IP 에서만 호출된다 — 아니면 403. 집 공인 IP 가 바뀌면 WTS 설정 > Open API > 허용 IP 관리에서
  새 IP 를 추가한다(키를 새로 받을 필요 없음).
* 성공은 ``{"result": ...}``, 실패는 ``{"error": {"code", "message", "data"}}``.
* 환전 API 는 없다 — 환전은 사용자가 앱에서 한다.

주문 (:meth:`TossTrader.order`) 은 지키지 않으면 요청을 보내지 않는다
(:class:`TradingDisabledError`):
``TOSS_TRADING_ENABLED=true`` · 허용 종목 · 수량 1 ~ 종목별 상한(정수) · 0 < 지정가 ≤ 상한 · 매도는
허용된 종목만. 주문 POST 는 다시 보내지 않는다 — 네트워크·5xx 는 :class:`OrderStateUnknownError`.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import httpx

from auto_trading.config.secrets import TossCredentials
from auto_trading.core.errors import ExchangeError, OrderStateUnknownError, RateLimitError
from auto_trading.exchange.overseas import TradingDisabledError

BASE_URL = "https://openapi.tossinvest.com"


def _dec(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


class TossTokenCache:
    """액세스 토큰을 파일에 둔다(24 시간 유효). 값은 출력하지 않는다."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self, client_id: str, now_ms: int) -> str | None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if data.get("client") != client_id[-6:] or int(data.get("expires_at_ms", 0)) <= now_ms:
            return None
        return str(data.get("token") or "") or None

    def save(self, client_id: str, token: str, expires_at_ms: int) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"client": client_id[-6:], "token": token, "expires_at_ms": expires_at_ms}
        self.path.write_text(json.dumps(payload), encoding="utf-8")

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)


@dataclass(frozen=True, slots=True)
class TossPosition:
    symbol: str
    qty: Decimal
    avg_price: Decimal
    last: Decimal
    currency: str


@dataclass(frozen=True, slots=True)
class TossOrderResult:
    accepted: bool
    code: str
    """성공이면 주문 ID, 실패면 오류 코드 (예: ``insufficient-buying-power``)."""
    message: str


@dataclass(frozen=True, slots=True)
class TossOrderStatus:
    status: str
    filled_qty: Decimal
    avg_price: Decimal | None
    filled_amount: Decimal | None
    commission: Decimal | None


class TossApiError(ExchangeError):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(f"토스 오류 {status} {code}: {message}")
        self.status, self.code, self.message = status, code, message


@dataclass(slots=True)
class TossReader:
    """조회 전용 (시세·일봉·잔고·매수 가능 금액·수수료·주문 상세)."""

    credentials: TossCredentials
    cache: TossTokenCache
    client: httpx.Client = field(default_factory=lambda: httpx.Client(timeout=20.0))
    clock: Callable[[], int] = field(default_factory=lambda: lambda: int(time.time() * 1000))
    sleep: Callable[[float], None] = time.sleep
    base_url: str = BASE_URL

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.credentials!r})"

    # ------------------------------------------------------------ 인증

    def _token(self) -> str:
        creds = self.credentials
        cached = self.cache.load(creds.client_id, self.clock())
        if cached:
            return cached
        try:
            response = self.client.post(
                f"{self.base_url}/oauth2/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": creds.client_id,
                    "client_secret": creds.client_secret,
                },
            )
        except httpx.HTTPError as exc:
            raise ExchangeError(f"토스 토큰 요청 실패 ({type(exc).__name__})") from exc
        if response.status_code == 403:
            raise TossApiError(403, "forbidden", "허용 IP 가 아니다 — WTS 허용 IP 관리 확인")
        if response.status_code != 200:
            body = response.json() if response.content else {}
            code = str(body.get("error") or response.status_code)
            raise TossApiError(response.status_code, code, "토큰 발급 실패 (값은 보여 주지 않음)")
        data = response.json()
        token = str(data["access_token"])
        ttl_ms = int(data.get("expires_in", 3600)) * 1000
        self.cache.save(creds.client_id, token, self.clock() + ttl_ms - 300_000)
        return token

    # ------------------------------------------------------------ 요청

    def _headers(self, account: bool) -> dict[str, str]:
        h = {"Authorization": f"Bearer {self._token()}"}
        if account:
            h["X-Tossinvest-Account"] = str(self.credentials.account_seq)
        return h

    @staticmethod
    def _error(response: httpx.Response) -> TossApiError:
        try:
            err = (response.json() or {}).get("error") or {}
        except ValueError:
            err = {}
        code = str(err.get("code") or response.status_code)
        message = str(err.get("message") or "").strip()
        if response.status_code == 403 and not message:
            message = "허용 IP 가 아니다 — WTS 허용 IP 관리 확인"
        return TossApiError(response.status_code, code, message)

    def _get(
        self, path: str, params: Mapping[str, Any] | None = None, *, account: bool = False
    ) -> Any:
        retried = False
        while True:
            try:
                response = self.client.get(
                    f"{self.base_url}{path}", params=params, headers=self._headers(account)
                )
            except httpx.HTTPError as exc:
                raise ExchangeError(f"토스 요청 실패: {path} ({type(exc).__name__})") from exc
            if response.status_code == 200:
                return (response.json() or {}).get("result")
            if response.status_code == 401 and not retried:
                retried = True
                self.cache.clear()
                continue
            if response.status_code == 429:
                if not retried:
                    retried = True
                    self.sleep(1.0)
                    continue
                raise RateLimitError(f"토스 초당 한도 초과: {path}")
            if response.status_code >= 500 and not retried:
                retried = True
                self.sleep(2.0)
                continue
            raise self._error(response)

    # ------------------------------------------------------------ 시세

    def prices(self, symbols: list[str]) -> dict[str, Decimal]:
        rows = self._get("/api/v1/prices", {"symbols": ",".join(symbols)}) or []
        out = {}
        for row in rows:
            px = _dec(row.get("lastPrice"))
            if px is not None and px > 0:
                out[str(row["symbol"]).upper()] = px
        return out

    def stocks_all(self, market: str) -> list[dict[str, Any]]:
        """거래소별 거래 가능 종목 (KOSPI · KOSDAQ · NYSE · NASDAQ · AMEX). 하루 한 번이면 충분."""
        rows = self._get("/api/v1/stocks/all", {"market": market}) or []
        return [dict(r) for r in rows if isinstance(r, dict)]

    def kr_open_today(self, day: str) -> bool:
        """국내 정규장이 그날 열리나 (휴장이면 ``integrated`` 가 비어 있다)."""
        data = self._get("/api/v1/market-calendar/KR", {"date": day}) or {}
        today = data.get("today") or {}
        return str(today.get("date")) == day and bool(today.get("integrated"))

    def us_regular_session(self, us_date: str) -> tuple[str, str] | None:
        """그 미국 날짜(YYYY-MM-DD)의 정규장 (시작, 끝) — KST ISO 문자열. 휴장이면 None."""
        data = self._get("/api/v1/market-calendar/US", {"date": us_date}) or {}
        today = data.get("today") or {}
        reg = today.get("regularMarket") or {}
        if str(today.get("date")) != us_date or not reg:
            return None
        return str(reg["startTime"]), str(reg["endTime"])

    def bids(self, symbol: str) -> list[tuple[Decimal, Decimal]]:
        """매수 호가 [(가격, 잔량)] 높은 가격 순. 없으면 빈 목록."""
        data = self._get("/api/v1/orderbook", {"symbol": symbol}) or {}
        out = []
        for row in data.get("bids") or []:
            px, vol = _dec(row.get("price")), _dec(row.get("volume"))
            if px is not None and px > 0 and vol is not None and vol > 0:
                out.append((px, vol))
        return out

    def usd_krw(self) -> Decimal:
        row = self._get("/api/v1/exchange-rate", {"baseCurrency": "USD", "quoteCurrency": "KRW"})
        rate = _dec((row or {}).get("rate"))
        if rate is None or rate <= 0:
            raise ExchangeError("토스 환율을 읽지 못했다")
        return rate

    def daily_closes(self, symbol: str, count: int = 200) -> list[tuple[str, Decimal]]:
        """수정주가 일봉 종가 (미국 거래일, 오래된 것 먼저). 정규장 공식 종가다(FAQ)."""
        data = self._get(
            "/api/v1/candles",
            {"symbol": symbol, "interval": "1d", "count": min(count, 200), "adjusted": "true"},
        )
        out = []
        for c in (data or {}).get("candles") or []:
            px = _dec(c.get("closePrice"))
            if px is not None and px > 0:
                out.append((str(c["timestamp"])[:10], px))
        return sorted(out)

    # ------------------------------------------------------------ 계좌

    def holdings(self) -> dict[str, TossPosition]:
        data = self._get("/api/v1/holdings", account=True) or {}
        out = {}
        for item in data.get("items") or []:
            qty = _dec(item.get("quantity")) or Decimal(0)
            if qty <= 0:
                continue
            sym = str(item["symbol"]).upper()
            out[sym] = TossPosition(
                sym,
                qty,
                _dec(item.get("averagePurchasePrice")) or Decimal(0),
                _dec(item.get("lastPrice")) or Decimal(0),
                str(item.get("currency") or ""),
            )
        return out

    def buying_power(self, currency: str) -> Decimal:
        data = self._get("/api/v1/buying-power", {"currency": currency}, account=True) or {}
        return _dec(data.get("cashBuyingPower")) or Decimal(0)

    def us_commission(self) -> Decimal | None:
        for row in self._get("/api/v1/commissions", account=True) or []:
            if row.get("marketCountry") == "US":
                return _dec(row.get("commissionRate"))
        return None

    def sellable(self, symbol: str) -> Decimal:
        """판매 가능 수량 (미체결 매도에 묶인 것은 빠진다). 미국은 소수점일 수 있다."""
        data = self._get("/api/v1/sellable-quantity", {"symbol": symbol}, account=True) or {}
        return _dec(data.get("sellableQuantity")) or Decimal(0)

    def open_orders(self) -> list[TossOpenOrder]:
        """진행 중 주문 전부 (체결 대기 · 부분 체결 · 취소/정정 대기)."""
        data = self._get("/api/v1/orders", {"status": "OPEN"}, account=True) or {}
        out = []
        for o in data.get("orders") or []:
            ex = o.get("execution") or {}
            out.append(
                TossOpenOrder(
                    order_id=str(o.get("orderId") or ""),
                    symbol=str(o.get("symbol") or "").upper(),
                    side=str(o.get("side") or "").upper(),
                    status=str(o.get("status") or ""),
                    time_in_force=str(o.get("timeInForce") or ""),
                    qty=_dec(o.get("quantity")) or Decimal(0),
                    filled_qty=_dec(ex.get("filledQuantity")) or Decimal(0),
                )
            )
        return out

    def order_status(self, order_id: str) -> TossOrderStatus:
        data = self._get(f"/api/v1/orders/{order_id}", account=True) or {}
        ex = data.get("execution") or {}
        return TossOrderStatus(
            status=str(data.get("status") or ""),
            filled_qty=_dec(ex.get("filledQuantity")) or Decimal(0),
            avg_price=_dec(ex.get("averageFilledPrice")),
            filled_amount=_dec(ex.get("filledAmount")),
            commission=_dec(ex.get("commission")),
        )

    def fills(self, *, start: str | None = None, end: str | None = None) -> list[TossFill]:
        """끝난 주문(CLOSED) 중 체결이 있는 것 — 앱 주문 포함, 시간외 종가 주문 제외(공식 명세).

        ``start`` · ``end`` = 주문일(KST, YYYY-MM-DD). 비우면 전체 기간. 100 개씩 커서로 넘긴다."""
        params: dict[str, Any] = {"status": "CLOSED", "limit": 100}
        if start:
            params["from"] = start
        if end:
            params["to"] = end
        out: list[TossFill] = []
        for _ in range(500):  # 5 만 건 안전 상한
            data = self._get("/api/v1/orders", params, account=True) or {}
            for o in data.get("orders") or []:
                ex = o.get("execution") or {}
                qty = _dec(ex.get("filledQuantity")) or Decimal(0)
                if qty <= 0:
                    continue
                out.append(
                    TossFill(
                        order_id=str(o.get("orderId") or ""),
                        symbol=str(o.get("symbol") or "").upper(),
                        side=str(o.get("side") or "").upper(),
                        currency=str(o.get("currency") or ""),
                        qty=qty,
                        amount=_dec(ex.get("filledAmount"))
                        or qty * (_dec(ex.get("averageFilledPrice")) or Decimal(0)),
                        commission=_dec(ex.get("commission")) or Decimal(0),
                        tax=_dec(ex.get("tax")) or Decimal(0),
                        filled_at=str(ex.get("filledAt") or o.get("orderedAt") or ""),
                        settle_date=str(ex.get("settlementDate") or "")[:10],
                    )
                )
            cursor = data.get("nextCursor")
            if not data.get("hasNext") or not cursor:
                break
            params["cursor"] = cursor
        return out

    def close(self) -> None:
        self.client.close()


@dataclass(frozen=True, slots=True)
class TossOpenOrder:
    order_id: str
    symbol: str
    side: str
    """BUY · SELL."""
    status: str
    time_in_force: str
    qty: Decimal
    filled_qty: Decimal


@dataclass(frozen=True, slots=True)
class TossFill:
    """체결이 있는 끝난 주문 하나 (부분 체결 뒤 취소도 체결분만)."""

    order_id: str
    symbol: str
    side: str
    """BUY · SELL."""
    currency: str
    qty: Decimal
    amount: Decimal
    """체결 금액 (주문 통화)."""
    commission: Decimal
    tax: Decimal
    """거래소 수수료 · 세금 (SEC fee 등)."""
    filled_at: str
    settle_date: str
    """결제일 YYYY-MM-DD — 양도세 연도 · 환율 기준."""


@dataclass(slots=True)
class TossTrader(TossReader):
    """지정가 주문. 미국은 기본 장 마감 지정가(LOC = ``LIMIT`` + ``CLS``).

    국내(6 자리 종목코드, 2026-10-10 적립)는 ``CLS`` 가 없어 ``DAY`` 지정가 · 원 단위 정수 가격 ·
    ``max_limit_krw`` 상한. 호가 단위는 부르는 쪽이 맞춘다(ops/kr_accumulate.tick_up).
    """

    allowed_symbols: frozenset[str] = frozenset()
    max_qty: Mapping[str, int] = field(default_factory=dict)
    sell_symbols: frozenset[str] = frozenset()
    max_limit_usd: Decimal = Decimal(0)
    max_limit_krw: Decimal = Decimal(0)

    def order(
        self,
        symbol: str,
        side: str,
        qty: int,
        limit: Decimal,
        *,
        client_order_id: str,
        time_in_force: str = "CLS",
    ) -> TossOrderResult:
        symbol = symbol.upper()
        if not self.credentials.trading_enabled:
            raise TradingDisabledError("TOSS_TRADING_ENABLED 가 true 가 아니다 — 주문하지 않는다")
        if symbol not in self.allowed_symbols:
            raise TradingDisabledError(f"허용되지 않은 종목: {symbol}")
        if side not in ("BUY", "SELL"):
            raise TradingDisabledError(f"주문 방향 오류: {side}")
        if side == "SELL" and symbol not in self.sell_symbols:
            raise TradingDisabledError(f"매도가 허용되지 않은 종목: {symbol}")
        top = self.max_qty.get(symbol, 1)
        if not 1 <= qty <= top:
            raise TradingDisabledError(f"수량은 1 ~ {top} 주만 허용한다 (요청 {qty})")
        kr = is_kr_symbol(symbol)
        cap = self.max_limit_krw if kr else self.max_limit_usd
        if not Decimal(0) < limit <= cap:
            raise TradingDisabledError(f"지정가 {limit} 가 허용 범위 (0, {cap}] 밖")
        if kr and (time_in_force != "DAY" or limit != limit.to_integral_value()):
            raise TradingDisabledError("국내 주문은 당일(DAY) 지정가 · 원 단위 정수만")
        body = {
            "clientOrderId": client_order_id,
            "symbol": symbol,
            "side": side,
            "orderType": "LIMIT",
            "timeInForce": time_in_force,
            "quantity": str(qty),
            "price": f"{int(limit)}" if kr else (f"{limit:.2f}" if limit >= 1 else f"{limit:.4f}"),
        }
        try:
            response = self.client.post(
                f"{self.base_url}/api/v1/orders", json=body, headers=self._headers(True)
            )
        except httpx.HTTPError as exc:
            raise OrderStateUnknownError(
                f"토스 주문 전송 결과 모름 ({type(exc).__name__}) — 다시 보내지 않는다"
            ) from exc
        if response.status_code == 200:
            result = (response.json() or {}).get("result") or {}
            return TossOrderResult(True, str(result.get("orderId") or ""), "접수")
        if response.status_code >= 500:
            raise OrderStateUnknownError(
                f"토스 주문 서버 오류 {response.status_code} — 접수 여부 모름, 다시 보내지 않는다"
            )
        err = self._error(response)
        return TossOrderResult(False, err.code, err.message)

    def cancel(self, order_id: str) -> TossOrderResult:
        """진행 중 주문 취소 (이미 체결된 것은 못 한다).

        네트워크 · 5xx 는 결과 모름 — 다시 보내지 않는다."""
        if not self.credentials.trading_enabled:
            raise TradingDisabledError("TOSS_TRADING_ENABLED 가 true 가 아니다 — 취소하지 않는다")
        try:
            response = self.client.post(
                f"{self.base_url}/api/v1/orders/{order_id}/cancel",
                json={},
                headers=self._headers(True),
            )
        except httpx.HTTPError as exc:
            raise OrderStateUnknownError(
                f"토스 취소 전송 결과 모름 ({type(exc).__name__}) — 조회로 확인"
            ) from exc
        if response.status_code == 200:
            result = (response.json() or {}).get("result") or {}
            return TossOrderResult(True, str(result.get("orderId") or ""), "취소 접수")
        if response.status_code >= 500:
            raise OrderStateUnknownError(
                f"토스 취소 서버 오류 {response.status_code} — 조회로 확인"
            )
        err = self._error(response)
        return TossOrderResult(False, err.code, err.message)


def is_kr_symbol(symbol: str) -> bool:
    """국내 종목코드 (6 자리, 숫자 또는 영문 · 숫자 섞임: 005930 · 0101N0)."""
    return len(symbol) == 6 and symbol.isalnum() and any(c.isdigit() for c in symbol[:2])
