"""토스증권 Open API 어댑터 — 미국 · 국내 주식 조회와 주문 (docs/research/118 §6).

언제 쓰나: Deneb · 자동 적립(평일 21:00 · 12:10) · 개별주 · 현황 화면이 토스를 볼 때 · 주문할 때.
읽고 쓰는 것: 토스 Open API, 접속 토큰 ``data/toss_token.json`` (24 시간 유효, 값은 출력 안 함).

공식 명세: ``https://openapi.tossinvest.com/openapi-docs/latest/openapi.json`` (v1.2.19 로 확인).

* 인증: OAuth 2.0 Client Credentials (``POST /oauth2/token``) → ``Authorization: Bearer``.
  계좌 · 주문 API 는 ``X-Tossinvest-Account: {accountSeq}`` 헤더도 필요하다.
* 허용 IP 에서만 호출된다 — 아니면 403. 집 공인 IP 가 바뀌면 WTS 설정 > Open API >
  허용 IP 관리에서 새 IP 를 추가한다(키를 새로 받을 필요 없음).
* 성공은 ``{"result": ...}``, 실패는 ``{"error": {"code", "message", "data"}}``.
* 환전 API 는 없다 — 환전은 사용자가 앱에서 한다.

주문 (:meth:`TossTrader.order`) 은 아래를 어기면 보내지 않는다 (:class:`TradingDisabledError`):
``TOSS_TRADING_ENABLED=true`` · 허용 종목 · 수량 1 ~ 종목별 상한(정수) · 0 < 지정가 ≤ 상한 ·
매도는 허용된 종목만. 주문 POST 는 다시 보내지 않는다 — 네트워크 · 5xx 는
:class:`OrderStateUnknownError` (접수 여부 모름).

디자인 패턴: **Adapter** — 토스 API 의 JSON 을 우리 코드가 쓰는 모양(``TossPosition`` 등)으로.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from decimal import ROUND_DOWN, Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import httpx

from auto_trading.config.secrets import TossCredentials
from auto_trading.core.errors import ExchangeError, OrderStateUnknownError, RateLimitError
from auto_trading.exchange.overseas import TradingDisabledError

BASE_URL = "https://openapi.tossinvest.com"
TOKEN_EARLY_EXPIRY_MS = 300_000
"""토큰 만료 5 분 전부터 새로 받는다 (요청 도중 만료되지 않게)."""
IP_NOT_ALLOWED = "허용 IP 가 아니다 — WTS 허용 IP 관리 확인"


def _to_decimal(value: Any) -> Decimal | None:
    """[쓰임] API 가 준 숫자(문자열 · 숫자 · 빈 값)를 Decimal 로. 비었거나 숫자가 아니면 None.
    [이유] 돈 계산은 float 오차가 없게 Decimal 로 한다. 토스는 값이 없을 때 빈 문자열도 준다."""
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


class TossTokenCache:
    """[쓰임] 토스 접속 토큰을 파일에 보관한다 (24 시간 유효). 토큰 값은 출력하지 않는다.
    [이유] 실행할 때마다 새 토큰을 받으면 토스 발급 한도에 걸릴 수 있어서 파일에 두고 다시 쓴다."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self, client_id: str, now_ms: int) -> str | None:
        """[쓰임] 저장된 토큰이 같은 키(``client_id`` 끝 6 자리)의 것이고 아직 유효하면 돌려준다."""
        try:
            saved = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        same_key = saved.get("client") == client_id[-6:]
        still_valid = int(saved.get("expires_at_ms", 0)) > now_ms
        if not same_key or not still_valid:
            return None
        return str(saved.get("token") or "") or None

    def save(self, client_id: str, token: str, expires_at_ms: int) -> None:
        """[쓰임] 새로 받은 토큰을 만료 시각과 함께 저장한다 (키 전체가 아니라 끝 6 자리만 남김)."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"client": client_id[-6:], "token": token, "expires_at_ms": expires_at_ms}
        self.path.write_text(json.dumps(payload), encoding="utf-8")

    def clear(self) -> None:
        """[쓰임] 저장된 토큰을 지운다 — 토스가 401(토큰 거절)을 줬을 때 새로 받게."""
        self.path.unlink(missing_ok=True)


@dataclass(frozen=True, slots=True)
class TossPosition:
    """보유 종목 하나 (토스 ``/holdings`` 한 줄)."""

    symbol: str
    qty: Decimal
    avg_price: Decimal
    """토스 앱이 보여 주는 매수 평균가."""
    last: Decimal
    """현재가."""
    currency: str


@dataclass(frozen=True, slots=True)
class TossOrderResult:
    """주문 · 취소를 보낸 결과 (토스가 받았나)."""

    accepted: bool
    code: str
    """성공이면 주문 ID, 실패면 오류 코드 (예: ``insufficient-buying-power``)."""
    message: str


@dataclass(frozen=True, slots=True)
class TossOrderStatus:
    """주문 하나의 지금 상태 (체결 수량 · 평균 체결가 · 수수료)."""

    status: str
    filled_qty: Decimal
    avg_price: Decimal | None
    filled_amount: Decimal | None
    commission: Decimal | None


class TossApiError(ExchangeError):
    """토스가 오류로 답했을 때 (HTTP 상태 · 토스 오류 코드 · 문구)."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(f"토스 오류 {status} {code}: {message}")
        self.status = status
        self.code = code
        self.message = message


def _now_ms() -> int:
    """[쓰임] 지금 시각(ms) — 토큰 만료 셈 · 시험이 바꿔 끼운다."""
    return int(time.time() * 1000)


@dataclass(slots=True)
class TossReader:
    """[쓰임] 토스 조회 전용 (시세 · 일봉 · 잔고 · 매수 가능 금액 · 수수료 · 주문 상세).
    [이유] 조회만 하는 곳(현황 화면 · 안전 점검)이 실수로 주문할 수 없게 주문 기능은
           :class:`TossTrader` 에만 둔다."""

    credentials: TossCredentials
    cache: TossTokenCache
    client: httpx.Client = field(default_factory=lambda: httpx.Client(timeout=20.0))
    clock: Callable[[], int] = field(default_factory=lambda: _now_ms)
    """지금 시각(ms) — 시험에서 바꿔 끼운다."""
    sleep: Callable[[float], None] = time.sleep
    base_url: str = BASE_URL

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.credentials!r})"

    # ------------------------------------------------------------ 인증

    def _token(self) -> str:
        """[쓰임] 접속 토큰 — 저장된 것이 유효하면 그것, 아니면 새로 받아 저장한다.
        [이유] 토큰 발급 실패 문구에 키 값이 섞이지 않게, 오류에는 코드만 남긴다."""
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
            raise TossApiError(403, "forbidden", IP_NOT_ALLOWED)
        if response.status_code != 200:
            body = response.json() if response.content else {}
            code = str(body.get("error") or response.status_code)
            raise TossApiError(response.status_code, code, "토큰 발급 실패 (값은 보여 주지 않음)")
        data = response.json()
        token = str(data["access_token"])
        lifetime_ms = int(data.get("expires_in", 3600)) * 1000
        self.cache.save(creds.client_id, token, self.clock() + lifetime_ms - TOKEN_EARLY_EXPIRY_MS)
        return token

    # ------------------------------------------------------------ 요청

    def _headers(self, account: bool) -> dict[str, str]:
        """[쓰임] 요청 머리말 — 토큰, 계좌 API 면 계좌 번호까지."""
        headers = {"Authorization": f"Bearer {self._token()}"}
        if account:
            headers["X-Tossinvest-Account"] = str(self.credentials.account_seq)
        return headers

    @staticmethod
    def _error(response: httpx.Response) -> TossApiError:
        """[쓰임] 토스 오류 응답 → :class:`TossApiError` (오류 코드 · 문구 꺼내기)."""
        try:
            error_body = (response.json() or {}).get("error") or {}
        except ValueError:
            error_body = {}
        code = str(error_body.get("code") or response.status_code)
        message = str(error_body.get("message") or "").strip()
        if response.status_code == 403 and not message:
            message = IP_NOT_ALLOWED
        return TossApiError(response.status_code, code, message)

    def _get(
        self, path: str, params: Mapping[str, Any] | None = None, *, account: bool = False
    ) -> Any:
        """[쓰임] 조회 GET 하나 — 성공하면 ``result`` 부분만 돌려준다.
        [이유] 조회는 다시 보내도 안전해서 한 번만 다시 시도한다:
               401(토큰 만료) → 토큰 지우고 다시, 429(초당 한도) → 1 초 쉬고 다시,
               5xx(토스 서버 문제) → 2 초 쉬고 다시. 두 번째도 실패하면 오류."""
        retried = False
        while True:
            try:
                response = self.client.get(
                    f"{self.base_url}{path}", params=params, headers=self._headers(account)
                )
            except httpx.HTTPError as exc:
                raise ExchangeError(f"토스 요청 실패: {path} ({type(exc).__name__})") from exc
            status = response.status_code
            if status == 200:
                return (response.json() or {}).get("result")
            if status == 401 and not retried:
                retried = True
                self.cache.clear()
                continue
            if status == 429:
                if not retried:
                    retried = True
                    self.sleep(1.0)
                    continue
                raise RateLimitError(f"토스 초당 한도 초과: {path}")
            if status >= 500 and not retried:
                retried = True
                self.sleep(2.0)
                continue
            raise self._error(response)

    # ------------------------------------------------------------ 시세

    def prices(self, symbols: list[str]) -> dict[str, Decimal]:
        """[쓰임] 종목들의 현재가 {기호: 가격}. 가격이 없거나 0 인 종목은 뺀다."""
        rows = self._get("/api/v1/prices", {"symbols": ",".join(symbols)}) or []
        prices_by_symbol = {}
        for row in rows:
            price = _to_decimal(row.get("lastPrice"))
            if price is not None and price > 0:
                prices_by_symbol[str(row["symbol"]).upper()] = price
        return prices_by_symbol

    def stocks_all(self, market: str) -> list[dict[str, Any]]:
        """[쓰임] 거래소별 거래 가능 종목 (KOSPI · KOSDAQ · NYSE · NASDAQ · AMEX).
        [이유] 설정 화면 '종목 추가' 가 이 목록에서만 찾는다. 하루 한 번이면 충분."""
        rows = self._get("/api/v1/stocks/all", {"market": market}) or []
        return [dict(row) for row in rows if isinstance(row, dict)]

    def kr_open_today(self, day: str) -> bool:
        """[쓰임] 국내 정규장이 그날(YYYY-MM-DD) 열리나. 휴장이면 ``integrated`` 가 비어 있다."""
        data = self._get("/api/v1/market-calendar/KR", {"date": day}) or {}
        today = data.get("today") or {}
        return str(today.get("date")) == day and bool(today.get("integrated"))

    def us_regular_session(self, us_date: str) -> tuple[str, str] | None:
        """[쓰임] 그 미국 날짜(YYYY-MM-DD)의 정규장 (시작, 끝) — KST ISO 문자열. 휴장이면 None."""
        data = self._get("/api/v1/market-calendar/US", {"date": us_date}) or {}
        today = data.get("today") or {}
        regular = today.get("regularMarket") or {}
        if str(today.get("date")) != us_date or not regular:
            return None
        return str(regular["startTime"]), str(regular["endTime"])

    def checked_regular_session(self, day: str, *, kr: bool) -> tuple[str, str] | None:
        """[쓰임] 개별주 도구용 정규장 (시작, 끝). 확실한 휴장이면 None.
        [이유] 개별주 손절은 '휴장' 과 '달력 응답이 이상함' 을 구분해야 해서, 이상한 응답은
               None 이 아니라 오류(ExchangeError)로 알린다."""
        market = "KR" if kr else "US"
        data = self._get(f"/api/v1/market-calendar/{market}", {"date": day})
        today = data.get("today") if isinstance(data, dict) else None
        if not isinstance(today, dict) or today.get("date") != day:
            raise ExchangeError("거래일 달력 날짜 미확인")
        session_info = today.get("integrated") if kr else today
        if kr and "integrated" in today and session_info is None:
            return None
        if not isinstance(session_info, dict) or "regularMarket" not in session_info:
            raise ExchangeError("거래일 정규장 필드 미확인")
        regular = session_info["regularMarket"]
        if regular is None:
            return None
        if (
            not isinstance(regular, dict)
            or not regular.get("startTime")
            or not regular.get("endTime")
        ):
            raise ExchangeError("거래일 정규장 시각 미확인")
        return str(regular["startTime"]), str(regular["endTime"])

    def regular_open(self, symbol: str, start_ms: int) -> Decimal | None:
        """[쓰임] 정규장 첫 1 분봉의 시가.
        [이유] 시작 시각과 정확히 같은 봉 · 거래량이 있는 봉만 인정한다 — 장 전 봉이나 거래 없는
               봉의 가격을 시가로 잘못 쓰지 않게."""
        from datetime import UTC, datetime

        data = self._get(
            "/api/v1/candles",
            {
                "symbol": symbol,
                "interval": "1m",
                "count": 2,
                "adjusted": "false",
                "before": datetime.fromtimestamp(start_ms / 1000, UTC).isoformat(),
            },
        )
        for candle in (data or {}).get("candles") or []:
            try:
                stamp = datetime.fromisoformat(str(candle["timestamp"]))
                if stamp.tzinfo is None or int(stamp.timestamp() * 1000) != start_ms:
                    continue
                price = _to_decimal(candle.get("openPrice"))
                volume = _to_decimal(candle.get("volume"))
                if price is None or volume is None or not price.is_finite():
                    continue
                if price > 0 and volume > 0:
                    return price
            except (ValueError, TypeError, KeyError):
                continue
        return None

    def _book_side(self, symbol: str, side: str) -> list[tuple[Decimal, Decimal]]:
        """[쓰임] 호가창 한쪽(``bids`` 매수 · ``asks`` 매도)의 [(가격, 잔량)], 토스가 준 순서."""
        data = self._get("/api/v1/orderbook", {"symbol": symbol}) or {}
        levels = []
        for row in data.get(side) or []:
            price = _to_decimal(row.get("price"))
            volume = _to_decimal(row.get("volume"))
            if price is None or volume is None:
                continue
            if price > 0 and volume > 0:
                levels.append((price, volume))
        return levels

    def bids(self, symbol: str) -> list[tuple[Decimal, Decimal]]:
        """[쓰임] 매수 호가 [(가격, 잔량)] 높은 가격 순. 없으면 빈 목록."""
        return self._book_side(symbol, "bids")

    def asks(self, symbol: str) -> list[tuple[Decimal, Decimal]]:
        """[쓰임] 매도 호가 [(가격, 잔량)] 낮은 가격 순. 없으면 빈 목록."""
        return sorted(self._book_side(symbol, "asks"))

    def usd_krw(self) -> Decimal:
        """[쓰임] 토스 달러 → 원 환율. 못 읽으면 오류 (환율 0 으로 계산하면 금액이 다 틀림)."""
        row = self._get("/api/v1/exchange-rate", {"baseCurrency": "USD", "quoteCurrency": "KRW"})
        rate = _to_decimal((row or {}).get("rate"))
        if rate is None or rate <= 0:
            raise ExchangeError("토스 환율을 읽지 못했다")
        return rate

    def daily_closes(
        self, symbol: str, count: int = 200, *, before_day: str | None = None
    ) -> list[tuple[str, Decimal]]:
        """[쓰임] 수정주가 일봉 종가 [(미국 날짜, 종가)], 오래된 것 먼저. 한 번에 200 개까지.
        [이유] 토스 일봉 종가 = 정규장 공식 종가(FAQ)라 Deneb 판단(QQQ 200 일선 등)에 쓴다.
               ``before_day`` 를 주면 그날보다 앞의 봉만 (그날 종가는 아직 안 쓴다)."""
        params = {
            "symbol": symbol,
            "interval": "1d",
            "count": str(min(count, 200)),
            "adjusted": "true",
        }
        if before_day is not None:
            from datetime import date, timedelta

            previous_day = date.fromisoformat(before_day) - timedelta(days=1)
            params["before"] = f"{previous_day}T23:59:59+09:00"
        data = self._get("/api/v1/candles", params)
        closes = []
        for candle in (data or {}).get("candles") or []:
            price = _to_decimal(candle.get("closePrice"))
            if price is None or price <= 0:
                continue
            candle_day = str(candle["timestamp"])[:10]
            if before_day is not None and candle_day >= before_day:
                continue
            closes.append((candle_day, price))
        return sorted(closes)

    # ------------------------------------------------------------ 계좌

    def holdings(self) -> dict[str, TossPosition]:
        """[쓰임] 지금 보유 종목 {기호: 보유} — 수량 0 인 줄은 뺀다."""
        data = self._get("/api/v1/holdings", account=True) or {}
        positions = {}
        for item in data.get("items") or []:
            qty = _to_decimal(item.get("quantity")) or Decimal(0)
            if qty <= 0:
                continue
            symbol = str(item["symbol"]).upper()
            positions[symbol] = TossPosition(
                symbol,
                qty,
                _to_decimal(item.get("averagePurchasePrice")) or Decimal(0),
                _to_decimal(item.get("lastPrice")) or Decimal(0),
                str(item.get("currency") or ""),
            )
        return positions

    def buying_power(self, currency: str) -> Decimal:
        """[쓰임] 그 통화(USD · KRW)로 지금 살 수 있는 현금."""
        data = self._get("/api/v1/buying-power", {"currency": currency}, account=True) or {}
        return _to_decimal(data.get("cashBuyingPower")) or Decimal(0)

    def us_commission(self) -> Decimal | None:
        """[쓰임] 이 계좌의 미국 주식 수수료율 (예: 0.001 = 0.1%). 못 찾으면 None."""
        for row in self._get("/api/v1/commissions", account=True) or []:
            if row.get("marketCountry") == "US":
                return _to_decimal(row.get("commissionRate"))
        return None

    def sellable(self, symbol: str) -> Decimal:
        """[쓰임] 지금 팔 수 있는 수량 (미체결 매도에 묶인 것은 빠진다). 미국은 소수점일 수 있다."""
        data = self._get("/api/v1/sellable-quantity", {"symbol": symbol}, account=True) or {}
        return _to_decimal(data.get("sellableQuantity")) or Decimal(0)

    def open_orders(self) -> list[TossOpenOrder]:
        """[쓰임] 진행 중 주문 전부 (체결 대기 · 부분 체결 · 취소/정정 대기)."""
        data = self._get("/api/v1/orders", {"status": "OPEN"}, account=True) or {}
        orders = []
        for order in data.get("orders") or []:
            execution = order.get("execution") or {}
            orders.append(
                TossOpenOrder(
                    order_id=str(order.get("orderId") or ""),
                    symbol=str(order.get("symbol") or "").upper(),
                    side=str(order.get("side") or "").upper(),
                    status=str(order.get("status") or ""),
                    time_in_force=str(order.get("timeInForce") or ""),
                    qty=_to_decimal(order.get("quantity")) or Decimal(0),
                    filled_qty=_to_decimal(execution.get("filledQuantity")) or Decimal(0),
                )
            )
        return orders

    def order_status(self, order_id: str) -> TossOrderStatus:
        """[쓰임] 주문 하나의 상태 · 체결 내용 (어젯밤 낸 장 마감 주문이 체결됐나 볼 때)."""
        data = self._get(f"/api/v1/orders/{order_id}", account=True) or {}
        execution = data.get("execution") or {}
        return TossOrderStatus(
            status=str(data.get("status") or ""),
            filled_qty=_to_decimal(execution.get("filledQuantity")) or Decimal(0),
            avg_price=_to_decimal(execution.get("averageFilledPrice")),
            filled_amount=_to_decimal(execution.get("filledAmount")),
            commission=_to_decimal(execution.get("commission")),
        )

    def fills(self, *, start: str | None = None, end: str | None = None) -> list[TossFill]:
        """[쓰임] 끝난 주문(CLOSED) 중 체결이 있는 것 — 앱에서 낸 주문 포함,
        시간외 종가 주문 제외(공식 명세).
        [이유] 현황 · 양도세 계산이 '실제로 체결된 것' 을 토스 기록에서 직접 가져오려고.

        ``start`` · ``end`` = 주문일(KST, YYYY-MM-DD). 비우면 전체 기간. 100 개씩 커서로 넘긴다."""
        params: dict[str, Any] = {"status": "CLOSED", "limit": 100}
        if start:
            params["from"] = start
        if end:
            params["to"] = end
        fills: list[TossFill] = []
        for _page in range(500):  # 5 만 건 안전 상한 (커서가 끝없이 이어지는 경우 대비)
            data = self._get("/api/v1/orders", params, account=True) or {}
            for order in data.get("orders") or []:
                fill = _fill_from_order(order)
                if fill is not None:
                    fills.append(fill)
            cursor = data.get("nextCursor")
            if not data.get("hasNext") or not cursor:
                break
            params["cursor"] = cursor
        return fills

    def close(self) -> None:
        """[쓰임] 연결을 닫는다 (실행이 끝날 때)."""
        self.client.close()


def _fill_from_order(order: Mapping[str, Any]) -> TossFill | None:
    """[쓰임] 끝난 주문 한 줄 → :class:`TossFill`. 체결 수량이 0 이면 None.
    [이유] 체결 금액이 비어 있는 주문도 있어, 그때는 수량 × 평균 체결가로 채운다."""
    execution = order.get("execution") or {}
    qty = _to_decimal(execution.get("filledQuantity")) or Decimal(0)
    if qty <= 0:
        return None
    amount = _to_decimal(execution.get("filledAmount"))
    if not amount:
        amount = qty * (_to_decimal(execution.get("averageFilledPrice")) or Decimal(0))
    return TossFill(
        order_id=str(order.get("orderId") or ""),
        symbol=str(order.get("symbol") or "").upper(),
        side=str(order.get("side") or "").upper(),
        currency=str(order.get("currency") or ""),
        qty=qty,
        amount=amount,
        commission=_to_decimal(execution.get("commission")) or Decimal(0),
        tax=_to_decimal(execution.get("tax")) or Decimal(0),
        filled_at=str(execution.get("filledAt") or order.get("orderedAt") or ""),
        settle_date=str(execution.get("settlementDate") or "")[:10],
    )


@dataclass(frozen=True, slots=True)
class TossOpenOrder:
    """진행 중 주문 하나."""

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


def _limit_price_text(limit: Decimal, korean: bool) -> str:
    """[쓰임] 지정가를 토스가 받는 글자로.

    국내는 원 단위 정수, 미국은 $1 이상이면 소수 2 자리 · 미만이면 4 자리."""
    if korean:
        return f"{int(limit)}"
    if limit >= 1:
        return f"{limit:.2f}"
    return f"{limit:.4f}"


@dataclass(slots=True)
class TossTrader(TossReader):
    """[쓰임] 토스 주문 — 조회(:class:`TossReader`)에 주문 · 취소를 더한 것.
    [이유] 미국은 기본 장 마감 지정가(LOC = ``LIMIT`` + ``CLS``) — 그날 종가로 사서 장중 흔들림에
           휘둘리지 않게. 국내(6 자리 종목코드, 2026-10-10 적립)는 ``CLS`` 가 없어
           ``DAY`` 지정가 · 원 단위 정수 가격 · ``max_limit_krw`` 상한.
           호가 단위는 부르는 쪽이 맞춘다(ops/kr_accumulate.tick_up).

    아래 허용 목록 · 상한은 실행기가 정한다 — 버그로 엉뚱한 종목 · 수량 · 가격이 나가지 않게
    막는 마지막 문."""

    allowed_symbols: frozenset[str] = frozenset()
    max_qty: Mapping[str, int] = field(default_factory=dict)
    """종목별 한 번 주문 수량 상한 (없으면 1 주)."""
    sell_symbols: frozenset[str] = frozenset()
    max_limit_usd: Decimal = Decimal(0)
    max_limit_krw: Decimal = Decimal(0)
    fraction_symbols: frozenset[str] = frozenset()
    """금액(소수점) 매수 · 소수점 시장가 매도를 허용할 미국 종목 (Sirius, 2026-10-18)."""
    max_amount_usd: Decimal = Decimal(0)
    """금액 주문 한 건 상한 (달러)."""

    def _require_trading_enabled(self, action: str = "주문하지") -> None:
        """[쓰임] ``.env`` 의 ``TOSS_TRADING_ENABLED=true`` 가 아니면 막는다."""
        if not self.credentials.trading_enabled:
            raise TradingDisabledError(f"TOSS_TRADING_ENABLED 가 true 가 아니다 — {action} 않는다")

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
        """[쓰임] 정수 수량 지정가 주문 (미국 기본 LOC, 국내 DAY).
        [이유] 아래 검사 중 하나라도 어기면 토스에 보내지 않고 오류 — 실수 주문을 막는다."""
        symbol = symbol.upper()
        # 1 단계: 주문 스위치 · 종목 · 방향 검사
        self._require_trading_enabled()
        if symbol not in self.allowed_symbols:
            raise TradingDisabledError(f"허용되지 않은 종목: {symbol}")
        if side not in ("BUY", "SELL"):
            raise TradingDisabledError(f"주문 방향 오류: {side}")
        if side == "SELL" and symbol not in self.sell_symbols:
            raise TradingDisabledError(f"매도가 허용되지 않은 종목: {symbol}")
        # 2 단계: 수량 · 가격 상한 검사
        qty_cap = self.max_qty.get(symbol, 1)
        if not 1 <= qty <= qty_cap:
            raise TradingDisabledError(f"수량은 1 ~ {qty_cap} 주만 허용한다 (요청 {qty})")
        korean = is_kr_symbol(symbol)
        price_cap = self.max_limit_krw if korean else self.max_limit_usd
        if not Decimal(0) < limit <= price_cap:
            raise TradingDisabledError(f"지정가 {limit} 가 허용 범위 (0, {price_cap}] 밖")
        if korean and (time_in_force != "DAY" or limit != limit.to_integral_value()):
            raise TradingDisabledError("국내 주문은 당일(DAY) 지정가 · 원 단위 정수만")
        # 3 단계: 보내기
        body = {
            "clientOrderId": client_order_id,
            "symbol": symbol,
            "side": side,
            "orderType": "LIMIT",
            "timeInForce": time_in_force,
            "quantity": str(qty),
            "price": _limit_price_text(limit, korean),
        }
        return self._send(body)

    def amount_buy(self, symbol: str, usd: Decimal, *, client_order_id: str) -> TossOrderResult:
        """[쓰임] 미국 금액 주문 매수 (``MARKET`` + ``orderAmount``, 소수점 수량으로 체결).
        [이유] 1 주 값보다 적은 돈으로도 사려고 (Sirius, 2026-10-18). 토스가 시장가만 받는다.

        정규장 시작 ~ 종료 1 시간 전만 접수(그 밖은 422 ``amount-order-outside-regular-hours``)."""
        symbol = symbol.upper()
        self._require_trading_enabled()
        if symbol not in self.fraction_symbols or is_kr_symbol(symbol):
            raise TradingDisabledError(f"금액 주문이 허용되지 않은 종목: {symbol}")
        amount = usd.quantize(Decimal("0.01"), rounding=ROUND_DOWN)
        if not Decimal(0) < amount <= self.max_amount_usd:
            raise TradingDisabledError(
                f"주문 금액 {amount} 가 허용 범위 (0, {self.max_amount_usd}] 밖"
            )
        return self._send(
            {
                "clientOrderId": client_order_id,
                "symbol": symbol,
                "side": "BUY",
                "orderType": "MARKET",
                "orderAmount": f"{amount:.2f}",
            }
        )

    def fractional_sell(
        self, symbol: str, qty: Decimal, *, client_order_id: str
    ) -> TossOrderResult:
        """[쓰임] 미국 소수점 수량 시장가 매도 (소수점 6 자리까지, 정규장 종료 1 시간 전까지)."""
        symbol = symbol.upper()
        self._require_trading_enabled()
        if symbol not in self.fraction_symbols or is_kr_symbol(symbol):
            raise TradingDisabledError(f"소수점 매도가 허용되지 않은 종목: {symbol}")
        sell_qty = qty.quantize(Decimal("0.000001"), rounding=ROUND_DOWN)
        if sell_qty <= 0:
            raise TradingDisabledError(f"매도 수량 오류: {qty}")
        return self._send(
            {
                "clientOrderId": client_order_id,
                "symbol": symbol,
                "side": "SELL",
                "orderType": "MARKET",
                "quantity": format(sell_qty.normalize(), "f"),
            }
        )

    def _send(self, body: Mapping[str, str]) -> TossOrderResult:
        """[쓰임] 주문 POST 를 한 번만 보낸다.
        [이유] 주문은 다시 보내면 두 번 살 수 있다. 그래서 네트워크 오류 · 5xx 는 다시 보내지 않고
               '결과 모름'(OrderStateUnknownError)으로 올려, 다음 실행이 조회로 확인하게 한다."""
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
        error = self._error(response)
        return TossOrderResult(False, error.code, error.message)

    def cancel(self, order_id: str) -> TossOrderResult:
        """[쓰임] 진행 중 주문 취소 (이미 체결된 것은 못 한다).
        [이유] 주문과 같은 까닭으로 네트워크 · 5xx 는 다시 보내지 않는다 — 조회로 확인."""
        self._require_trading_enabled("취소하지")
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
        error = self._error(response)
        return TossOrderResult(False, error.code, error.message)


def is_kr_symbol(symbol: str) -> bool:
    """[쓰임] 국내 종목코드인가 (6 자리, 숫자 또는 영문 · 숫자 섞임: 005930 · 0101N0).
    [이유] 미국 기호(TQQQ 등)와 국내 코드는 주문 규칙(LOC 여부 · 가격 단위)이 달라서 먼저 가른다."""
    if len(symbol) != 6 or not symbol.isalnum():
        return False
    return any(char.isdigit() for char in symbol[:2])
