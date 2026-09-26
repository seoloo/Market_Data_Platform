"""한국투자증권(KIS) Open API **실전** 어댑터 — **읽기 전용** (해외주식·ETF 시세·일봉·잔고).

* 인증: ``POST /oauth2/tokenP`` 로 접근 토큰을 받는다. 토큰은 약 24시간 유효하고,
  KIS 는 발급을 1분에 1회로 제한한다. 그래서 **파일에 캐시**하고 만료 10분 전까지 재사용한다.
* 요청 헤더: ``authorization: Bearer <토큰>`` · ``appkey`` · ``appsecret`` · ``tr_id``
  · ``custtype: P``. 응답 ``rt_cd == "0"`` 이 성공이다.
* 오류 처리: 토큰 만료·무효(EGW00123·EGW00121)면 캐시를 버리고 **한 번** 다시 받아 재시도한다.
  초당 한도(EGW00201)·네트워크 오류는 짧게 기다렸다 최대 3번까지 다시 묻는다
  (조회만 하므로 안전하다).
* :meth:`KisOverseasReadOnly.place_order` 는 ``KIS_TRADING_ENABLED`` 와 상관없이 항상 막는다.
  유일한 주문 경로는 :class:`KisReservationBuyer` — 사용자 요청(2026-09-26, 문서 89)으로 더한
  **미국 주식 예약 매수 1 주 전용**이다(허용 종목·가격 상한·``KIS_TRADING_ENABLED=true`` 필수,
  매도·정정·취소 없음).
* 비밀값(앱 키·시크릿·토큰)은 예외 메시지·repr·로그에 싣지 않는다.
  토큰 캐시 파일은 ``data/`` (git 제외) 에 둔다.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import httpx

from auto_trading.config.secrets import KisCredentials
from auto_trading.core.errors import (
    DataIntegrityError,
    ExchangeError,
    OrderStateUnknownError,
    RateLimitError,
)
from auto_trading.core.types import Candle
from auto_trading.exchange.overseas import (
    OverseasBalance,
    OverseasPosition,
    OverseasQuote,
    TradingDisabledError,
)

__all__ = [
    "BASE_URL",
    "PRICE_EXCHANGE",
    "BuyingPower",
    "KisOverseasReadOnly",
    "KisReservationBuyer",
    "KisTokenCache",
    "QuoteDetail",
    "ReservationResult",
]

BASE_URL = "https://openapi.koreainvestment.com:9443"
"""실전 도메인. 모의투자는 ``https://openapivts.koreainvestment.com:29443`` (tr_id 도 다르다)."""
PRICE_EXCHANGE = {"SPY": "AMS", "GLD": "AMS", "SLV": "AMS", "QQQ": "NAS"}
"""시세 조회용 거래소 코드 (NAS 나스닥 · NYS 뉴욕 · AMS 아멕스/NYSE Arca)."""
_BALANCE_EXCHANGES = ("NASD", "NYSE", "AMEX")
"""잔고 조회용 거래소 코드 (시세용 코드와 다르다)."""
_TOKEN_ERRORS = frozenset({"EGW00121", "EGW00123"})
_RATE_ERRORS = frozenset({"EGW00201"})
_TOKEN_TOO_SOON = "EGW00133"
_REFRESH_MARGIN_MS = 10 * 60_000
_DAY_MS = 86_400_000
_TR_PRICE = "HHDFS00000300"
_TR_PRICE_DETAIL = "HHDFS76200200"
_TR_DAILY = "HHDFS76240000"
_TR_BALANCE = "TTTS3012R"


@dataclass(frozen=True, slots=True)
class QuoteDetail:
    symbol: str
    exchange: str
    last: Decimal
    prev_close: Decimal | None
    fx_rate: Decimal | None
    """KIS 당일 고시 환율 (원/달러, ``t_rate``). 실제 환전 적용 환율과 조금 다를 수 있다."""
    krw_price: Decimal | None
    """원화 환산 당일 가격 (``t_xprc``)."""


def _key_fingerprint(app_key: str) -> str:
    return hashlib.sha256(app_key.encode()).hexdigest()[:16]


@dataclass(slots=True)
class KisTokenCache:
    """접근 토큰 파일 캐시. 다른 앱 키로 만든 토큰은 쓰지 않는다(키 지문 비교)."""

    path: Path

    def load(self, app_key: str, now_ms: int) -> str | None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if data.get("key_fp") != _key_fingerprint(app_key):
            return None
        if int(data.get("expires_at_ms", 0)) - _REFRESH_MARGIN_MS <= now_ms:
            return None
        token = data.get("token")
        return token if isinstance(token, str) and token else None

    def save(self, app_key: str, token: str, expires_at_ms: int) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        body = {"key_fp": _key_fingerprint(app_key), "token": token, "expires_at_ms": expires_at_ms}
        tmp.write_text(json.dumps(body), encoding="utf-8")
        with contextlib.suppress(OSError):
            tmp.chmod(0o600)
        tmp.replace(self.path)

    def clear(self) -> None:
        with contextlib.suppress(OSError):
            self.path.unlink()


def _dec(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value).strip())
    except InvalidOperation:
        return None


@dataclass(slots=True)
class KisOverseasReadOnly:
    """KIS 실전 해외주식 **읽기 전용**. :class:`~.overseas.OverseasBroker` 구현."""

    credentials: KisCredentials
    token_cache: KisTokenCache
    client: httpx.Client = field(default_factory=lambda: httpx.Client(timeout=10.0))
    base_url: str = BASE_URL
    clock: Callable[[], int] = field(default=lambda: int(time.time() * 1000))
    sleep: Callable[[float], None] = field(default=time.sleep)
    max_attempts: int = 3
    skipped_rows: int = 0
    """일봉 중 가격이 0 이하·OHLC 가 어긋나 건너뛴 줄 수 (마지막 조회 누적)."""
    _token: str | None = field(default=None, repr=False)

    def __repr__(self) -> str:
        return f"KisOverseasReadOnly({self.credentials!r}, base_url={self.base_url})"

    # ------------------------------------------------------------ 인증

    def _issue_token(self) -> str:
        creds = self.credentials
        body = {
            "grant_type": "client_credentials",
            "appkey": creds.app_key,
            "appsecret": creds.app_secret,
        }
        try:
            response = self.client.post(f"{self.base_url}/oauth2/tokenP", json=body)
        except httpx.HTTPError as exc:
            raise ExchangeError(f"KIS 토큰 요청 실패 ({type(exc).__name__})") from exc
        data = response.json() if response.content else {}
        if not response.is_success or not data.get("access_token"):
            code = data.get("error_code") or data.get("msg_cd") or response.status_code
            text = data.get("error_description") or data.get("msg1") or ""
            if code == _TOKEN_TOO_SOON:
                raise RateLimitError(f"KIS 토큰 발급은 1분에 1회다 — 잠시 뒤 다시 ({code})")
            raise ExchangeError(f"KIS 토큰 발급 실패 {code}: {text}")
        token = str(data["access_token"])
        expires_in = int(data.get("expires_in") or 0) or 86_400
        self.token_cache.save(creds.app_key, token, self.clock() + expires_in * 1000)
        return token

    def _access_token(self) -> str:
        if self._token is None:
            self._token = self.token_cache.load(self.credentials.app_key, self.clock())
        if self._token is None:
            self._token = self._issue_token()
        return self._token

    def _drop_token(self) -> None:
        self._token = None
        self.token_cache.clear()

    # ------------------------------------------------------------ HTTP

    def _get(self, path: str, tr_id: str, params: Mapping[str, str]) -> dict[str, Any]:
        """조회 요청. 토큰 오류는 1회 재발급, 초당 한도·네트워크 오류는 ``max_attempts`` 번까지."""
        refreshed = False
        attempt = 0
        while True:
            attempt += 1
            headers = {
                "content-type": "application/json; charset=utf-8",
                "authorization": f"Bearer {self._access_token()}",
                "appkey": self.credentials.app_key,
                "appsecret": self.credentials.app_secret,
                "tr_id": tr_id,
                "custtype": "P",
            }
            try:
                response = self.client.get(f"{self.base_url}{path}", params=params, headers=headers)
            except httpx.HTTPError as exc:
                if attempt < self.max_attempts:
                    self.sleep(0.5 * attempt)
                    continue
                raise ExchangeError(f"KIS 요청 실패: {tr_id} ({type(exc).__name__})") from exc
            try:
                data = response.json() if response.content else {}
            except ValueError:
                data = {}
            code = str(data.get("msg_cd") or "")
            if response.is_success and str(data.get("rt_cd")) == "0":
                return data
            if code in _TOKEN_ERRORS and not refreshed:
                refreshed = True
                self._drop_token()
                continue
            if (
                code in _RATE_ERRORS or response.status_code == 429
            ) and attempt < self.max_attempts:
                self.sleep(1.0 * attempt)
                continue
            if code in _RATE_ERRORS or response.status_code == 429:
                raise RateLimitError(f"KIS 초당 한도 초과: {tr_id}")
            text = str(data.get("msg1") or "").strip()
            raise ExchangeError(f"KIS 오류 {response.status_code} {code}: {text} ({tr_id})")

    # ------------------------------------------------------------ 시세

    @staticmethod
    def _exchange(symbol: str, exchange: str | None) -> str:
        code = exchange or PRICE_EXCHANGE.get(symbol.upper())
        if code is None:
            raise ExchangeError(f"{symbol} 의 거래소 코드(NAS·NYS·AMS)를 알려 줘야 한다")
        return code

    def quote(self, symbol: str, exchange: str | None = None) -> OverseasQuote:
        excd = self._exchange(symbol, exchange)
        data = self._get(
            "/uapi/overseas-price/v1/quotations/price",
            _TR_PRICE,
            {"AUTH": "", "EXCD": excd, "SYMB": symbol.upper()},
        )
        out = data.get("output") or {}
        last = _dec(out.get("last"))
        if last is None or last <= 0:
            raise ExchangeError(f"KIS 시세 없음: {symbol} ({excd}) — 거래소 코드를 확인")
        return OverseasQuote(symbol.upper(), excd, last, _dec(out.get("base")))

    def quote_detail(self, symbol: str, exchange: str) -> QuoteDetail:
        """현재가상세 (HHDFS76200200) — 원화 환산 가격·당일 환율이 함께 온다 (문서 89 적립 알림)."""
        data = self._get(
            "/uapi/overseas-price/v1/quotations/price-detail",
            _TR_PRICE_DETAIL,
            {"AUTH": "", "EXCD": exchange, "SYMB": symbol.upper()},
        )
        out = data.get("output") or {}
        last = _dec(out.get("last"))
        if last is None or last <= 0:
            raise ExchangeError(f"KIS 시세 없음: {symbol} ({exchange})")
        return QuoteDetail(
            symbol=symbol.upper(),
            exchange=exchange,
            last=last,
            prev_close=_dec(out.get("base")),
            fx_rate=_dec(out.get("t_rate")),
            krw_price=_dec(out.get("t_xprc")),
        )

    def _daily_page(
        self, symbol: str, excd: str, bymd: str, adjusted: bool
    ) -> tuple[list[Candle], str | None]:
        """(쓸 수 있는 봉, 페이지의 가장 오래된 날짜 — 건너뛴 줄 포함, 빈 페이지면 None)."""
        data = self._get(
            "/uapi/overseas-price/v1/quotations/dailyprice",
            _TR_DAILY,
            {
                "AUTH": "",
                "EXCD": excd,
                "SYMB": symbol.upper(),
                "GUBN": "0",
                "BYMD": bymd,
                "MODP": "1" if adjusted else "0",
            },
        )
        bars: list[Candle] = []
        oldest: str | None = None
        for row in data.get("output2") or []:
            day = str(row.get("xymd") or "")
            if len(day) != 8 or not day.isdigit():
                continue
            oldest = day if oldest is None else min(oldest, day)
            o, h, low, c = (_dec(row.get(k)) for k in ("open", "high", "low", "clos"))
            if None in (o, h, low, c):
                self.skipped_rows += 1
                continue
            start = int(datetime.strptime(day, "%Y%m%d").replace(tzinfo=UTC).timestamp() * 1000)
            try:
                bars.append(
                    Candle(
                        open_ms=start,
                        close_ms=start + _DAY_MS - 1,
                        open=o,  # type: ignore[arg-type]
                        high=h,  # type: ignore[arg-type]
                        low=low,  # type: ignore[arg-type]
                        close=c,  # type: ignore[arg-type]
                        volume=_dec(row.get("tvol")) or Decimal(0),
                        quote_volume=_dec(row.get("tamt")) or Decimal(0),
                    )
                )
            except DataIntegrityError:
                self.skipped_rows += 1
        return bars, oldest

    def daily_history(
        self,
        symbol: str,
        *,
        since: str,
        until: str | None = None,
        exchange: str | None = None,
        adjusted: bool = True,
        max_pages: int = 200,
    ) -> list[Candle]:
        """``since``~``until``(YYYY-MM-DD, 포함) 일봉, 오래된 것 먼저. 한 번에 100봉씩 뒤로 넘긴다.

        봉의 시각은 미국 거래일 날짜의 UTC 00:00 ~ 23:59:59.999 로 둔다.
        실제 장 마감은 그보다 이르다 — 이 봉을 쓰는 쪽은 그날이 끝난 뒤에만 쓴다.
        """
        excd = self._exchange(symbol, exchange)
        floor = since.replace("-", "")
        bymd = (until or "").replace("-", "")
        seen: dict[int, Candle] = {}
        for _ in range(max_pages):
            page, oldest = self._daily_page(symbol, excd, bymd, adjusted)
            if oldest is None:
                break  # 더 과거 자료가 없다
            for b in page:
                seen.setdefault(b.open_ms, b)
            if oldest <= floor:
                break
            # 쓸 수 없는 줄만 있는 페이지도 건너뛰고 계속 간다 (원시 날짜 기준 커서)
            day = datetime.strptime(oldest, "%Y%m%d").replace(tzinfo=UTC)
            nxt = (day - timedelta(days=1)).strftime("%Y%m%d")
            if bymd and nxt >= bymd:
                break  # 커서가 뒤로 가지 않으면 멈춘다 (무한 반복 방지)
            bymd = nxt
        lo = int(datetime.strptime(floor, "%Y%m%d").replace(tzinfo=UTC).timestamp() * 1000)
        return sorted((b for b in seen.values() if b.open_ms >= lo), key=lambda b: b.open_ms)

    def closed_daily(self, symbol: str, as_of_ms: int, limit: int = 200) -> list[Candle]:
        """``as_of_ms`` 까지 끝난 날의 일봉 ``limit`` 개 (신호 계약, 오래된 것 먼저)."""
        until = datetime.fromtimestamp(as_of_ms / 1000, UTC).strftime("%Y-%m-%d")
        # 거래일은 역일의 약 69% — 넉넉히 거슬러 올라간다.
        since_dt = datetime.fromtimestamp(as_of_ms / 1000, UTC) - timedelta(
            days=limit * 3 // 2 + 10
        )
        bars = self.daily_history(symbol, since=since_dt.strftime("%Y-%m-%d"), until=until)
        return [b for b in bars if b.close_ms <= as_of_ms][-limit:]

    # ------------------------------------------------------------ 계좌

    def overseas_balance(self) -> OverseasBalance:
        """미국 주식 보유 종목과 평가 합계 (USD). 거래소 코드별로 묻고 종목으로 합친다."""
        creds = self.credentials
        positions: dict[str, OverseasPosition] = {}
        for excg in _BALANCE_EXCHANGES:
            data = self._get(
                "/uapi/overseas-stock/v1/trading/inquire-balance",
                _TR_BALANCE,
                {
                    "CANO": creds.cano,
                    "ACNT_PRDT_CD": creds.product_code,
                    "OVRS_EXCG_CD": excg,
                    "TR_CRCY_CD": "USD",
                    "CTX_AREA_FK200": "",
                    "CTX_AREA_NK200": "",
                },
            )
            for row in data.get("output1") or []:
                qty = _dec(row.get("ovrs_cblc_qty")) or Decimal(0)
                symbol = str(row.get("ovrs_pdno") or "").strip()
                if not symbol or qty <= 0:
                    continue
                positions[symbol] = OverseasPosition(
                    symbol=symbol,
                    exchange=str(row.get("ovrs_excg_cd") or excg),
                    qty=qty,
                    avg_price=_dec(row.get("pchs_avg_pric")) or Decimal(0),
                    last=_dec(row.get("now_pric2")) or Decimal(0),
                    value=_dec(row.get("ovrs_stck_evlu_amt")) or Decimal(0),
                )
        ordered = tuple(sorted(positions.values(), key=lambda p: p.symbol))
        return OverseasBalance(ordered, sum((p.value for p in ordered), Decimal(0)))

    # ------------------------------------------------------------ 주문 (없음)

    def place_order(self, *args: object, **kwargs: object) -> None:
        """항상 막는다. 실전 주문 코드는 별도 승인 뒤 더한다(이 클래스는 읽기 전용이다)."""
        flag = "true" if self.credentials.trading_enabled else "false"
        raise TradingDisabledError(
            f"KIS 주문 경로 없음 (KIS_TRADING_ENABLED={flag})"
            " — 읽기 전용 어댑터, 실전 주문은 별도 승인 뒤"
        )

    def close(self) -> None:
        self.client.close()


# ---------------------------------------------------------------- 예약 매수 전용 (문서 89)

_TR_RESV_BUY_US = "TTTT3014U"
_TR_PSAMOUNT = "TTTS3007R"


@dataclass(frozen=True, slots=True)
class BuyingPower:
    """해외주식 매수가능금액조회 결과 (없는 값은 None)."""

    max_qty: Decimal | None
    exrt: Decimal | None
    usd_available: Decimal | None


@dataclass(frozen=True, slots=True)
class ReservationResult:
    accepted: bool
    code: str
    message: str
    output: dict[str, Any]


@dataclass(slots=True)
class KisReservationBuyer(KisOverseasReadOnly):
    """미국 주식 **예약 매수 1 주만** 내는 어댑터 (문서 89 QNDX 적립). 매도·정정·취소 코드는 없다.

    지키지 않으면 요청을 보내지 않고 :class:`TradingDisabledError`:

    * ``KIS_TRADING_ENABLED=true`` — 사용자가 ``.env`` 에서 직접 켠다
    * ``allowed_symbols`` 안의 종목 · 수량 정확히 1 · 0 < 지정가 ≤ ``max_limit_usd``

    예약주문(``TTTT3014U``)은 미국 **지정가만**, **당일 유효**다(접수 10:00~23:20 KST,
    서머타임 10:00~22:20, 16:30~16:45 제한). 정규장 시작 때 거래소로 간다.
    주문 POST 는 **다시 보내지 않는다** — 네트워크 오류면 :class:`OrderStateUnknownError`.
    토큰 무효·초당 한도처럼 거래소가 **거절한** 응답만 한 번 더 보낸다.
    """

    allowed_symbols: frozenset[str] = frozenset()
    max_limit_usd: Decimal = Decimal(0)

    def buying_power(self, symbol: str, order_exchange: str, price: Decimal) -> BuyingPower:
        """매수가능금액조회 (``TTTS3007R``, 조회만)."""
        creds = self.credentials
        data = self._get(
            "/uapi/overseas-stock/v1/trading/inquire-psamount",
            _TR_PSAMOUNT,
            {
                "CANO": creds.cano,
                "ACNT_PRDT_CD": creds.product_code,
                "OVRS_EXCG_CD": order_exchange,
                "OVRS_ORD_UNPR": f"{price:.2f}",
                "ITEM_CD": symbol.upper(),
            },
        )
        out = data.get("output") or {}
        return BuyingPower(
            max_qty=_dec(out.get("max_ord_psbl_qty")),
            exrt=_dec(out.get("exrt")),
            usd_available=_dec(out.get("ovrs_ord_psbl_amt")),
        )

    def reserve_buy(
        self, symbol: str, order_exchange: str, qty: int, limit: Decimal
    ) -> ReservationResult:
        creds = self.credentials
        symbol = symbol.upper()
        if not creds.trading_enabled:
            raise TradingDisabledError("KIS_TRADING_ENABLED 가 true 가 아니다 — 주문하지 않는다")
        if symbol not in self.allowed_symbols:
            raise TradingDisabledError(f"허용되지 않은 종목: {symbol}")
        if qty != 1:
            raise TradingDisabledError(f"수량은 1 주만 허용한다 (요청 {qty})")
        if not Decimal(0) < limit <= self.max_limit_usd:
            raise TradingDisabledError(f"지정가 {limit} 가 허용 범위 (0, {self.max_limit_usd}] 밖")
        body = {
            "CANO": creds.cano,
            "ACNT_PRDT_CD": creds.product_code,
            "PDNO": symbol,
            "OVRS_EXCG_CD": order_exchange,
            "FT_ORD_QTY": str(qty),
            "FT_ORD_UNPR3": f"{limit:.2f}",
            "ORD_SVR_DVSN_CD": "0",
            "ORD_DVSN": "00",
        }
        retried = False
        while True:
            headers = {
                "content-type": "application/json; charset=utf-8",
                "authorization": f"Bearer {self._access_token()}",
                "appkey": creds.app_key,
                "appsecret": creds.app_secret,
                "tr_id": _TR_RESV_BUY_US,
                "custtype": "P",
            }
            try:
                response = self.client.post(
                    f"{self.base_url}/uapi/overseas-stock/v1/trading/order-resv",
                    json=body,
                    headers=headers,
                )
            except httpx.HTTPError as exc:
                raise OrderStateUnknownError(
                    f"KIS 예약주문 전송 결과 모름 ({type(exc).__name__}) — 다시 보내지 않는다"
                ) from exc
            try:
                data = response.json() if response.content else {}
            except ValueError:
                data = {}
            code = str(data.get("msg_cd") or response.status_code)
            text = str(data.get("msg1") or "").strip()
            if response.is_success and str(data.get("rt_cd")) == "0":
                return ReservationResult(True, code, text, dict(data.get("output") or {}))
            if (code in _TOKEN_ERRORS or code in _RATE_ERRORS) and not retried:
                retried = True  # 거절된 요청이라 다시 보내도 중복이 아니다
                if code in _TOKEN_ERRORS:
                    self._drop_token()
                else:
                    self.sleep(1.0)
                continue
            return ReservationResult(False, code, text, dict(data.get("output") or {}))
