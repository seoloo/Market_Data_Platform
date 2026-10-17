"""NH투자증권 나무 플러그 Open API — **해외주식(미국) 원화 주문** 클라이언트.

사용자 (2026-10-17): "나무증권 해외주식 원화 주문 연결해줘" ·
"나무 증권 같은 경우는 정말 안정적이게".

* 정본: https://www.nhplug.com/openapi-docs/gbstock/openapi.json (명세서 260911).
* 매수는 증거금 통화 **원화**(``wtm_cur_knd_cd = 2``) — 달러로 바꿔 두지 않아도 원화 예수금으로
  산다.
  모의투자 서버는 원화 주문을 받지 않아 모의에서만 달러(``1``)로 낸다.
* **지정가만** (``ahi_nmn_pr_tp_cd = 00``, 사용자 규칙 2026-10-05 '시장가 금지'). 정규장만.
* 실전(운영 주소)은 ``NAMU_TRADING_ENABLED == true`` 일 때만 주문 · 취소한다(기본 꺼짐). 모의투자
  주소는 스위치 없이 된다. 실전 · 모의 모두 허용 종목 · 한 번 수량 · 한 번 금액(달러) 상한을 지킨다.
* 계좌: 실전은 종합(``01``) 계좌가 **정확히 하나**일 때만, 모의는 첫 ``03``.
* 주문 결과를 모르면(네트워크 · 5xx) 다시 보내지 않고 :class:`OrderStateUnknownError`.
* 주문 번호는 ``"YYYYMMDD:주문번호"`` — 체결 조회 API 가 주문 날짜를 요구해서 낸 날짜(한국)를
  붙인다.
  그날 없으면 앞 · 다음 날도 본다(미국 장은 한국 자정을 넘기고, 나무의 주문 날짜 기준은 미확인).
* 토큰 · 호출 간격 · 401/429 는 :class:`~auto_trading.exchange.namu.NamuSession`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from auto_trading.core.errors import OrderStateUnknownError
from auto_trading.exchange.namu import LIVE_URL, MOCK_URL, NamuError, NamuSession, _code, _msg
from auto_trading.exchange.overseas import TradingDisabledError

__all__ = [
    "US",
    "NamuGlobal",
    "NamuHolding",
    "NamuOrderResult",
    "NamuOrderStatus",
    "NamuQuote",
]

KST = ZoneInfo("Asia/Seoul")
US = "200"
"""외화증권거래국가코드 — 미국."""
LIMIT = "00"
KRW_MARGIN = "2"
USD_MARGIN = "1"
GTS = "1"
"""해외증권주문종류코드 1 = GTS(미국시장주문)."""


@dataclass(frozen=True, slots=True)
class NamuQuote:
    symbol: str
    last: float
    ask: float
    bid: float
    fx: float
    """원/달러 (응답 ``currency_prc``)."""
    asks: list[tuple[Decimal, Decimal]]
    bids: list[tuple[Decimal, Decimal]]
    regular_close: float
    session: str
    """``marketperiod_cls`` 그대로 (정규장 판단은 호출하는 쪽 시계로)."""
    trade_date: str = ""
    """시세의 미국 거래일 YYYYMMDD — 휴장일엔 앞 거래일로 남는다."""


@dataclass(frozen=True, slots=True)
class NamuHolding:
    symbol: str
    name: str
    qty: int
    sellable: int
    avg_usd: float
    last_usd: float
    eval_krw: int
    pnl_krw: int


@dataclass(frozen=True, slots=True)
class NamuOrderResult:
    accepted: bool
    code: str
    """접수면 주문 ID(``YYYYMMDD:번호``), 거절이면 응답 코드."""
    message: str


@dataclass(frozen=True, slots=True)
class NamuOrderStatus:
    status: str
    """토스와 같은 이름: PENDING · PARTIAL_FILLED · FILLED · CANCELED · REJECTED · '' (모름)."""
    filled_qty: Decimal
    avg_price: Decimal | None
    filled_amount: Decimal | None
    commission: Decimal | None
    open_qty: int = 0
    note: str = ""


def _f(v: Any) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _i(v: Any) -> int:
    return int(_f(v))


@dataclass(slots=True)
class NamuGlobal(NamuSession):
    """미국 주식 — 원화 증거금 조회 · 보유 · 현재가/호가 · 지정가 매수/매도 · 체결 조회 · 취소."""

    allowed: frozenset[str] = frozenset()
    """주문할 수 있는 종목(대문자). 비어 있으면 주문 못 함."""
    max_qty: int = 0
    max_order_usd: float = 0.0
    """한 번 주문 금액(지정가 × 수량) 상한."""
    _account: str | None = None

    def __post_init__(self) -> None:
        if self.base_url not in (LIVE_URL, MOCK_URL):
            raise ValueError(f"알 수 없는 나무 주소: {self.base_url}")

    def __repr__(self) -> str:
        return f"NamuGlobal({'실전' if self.live else '모의'})"

    @property
    def live(self) -> bool:
        return self.base_url == LIVE_URL

    @property
    def margin(self) -> str:
        """매수 증거금 통화 — 실전 원화(2). 모의투자는 원화 주문을 받지 않아(14300 '모의투자에서는
        거래국가통화만 가능합니다', 2026-10-17) 달러(1)로 경로만 시험한다."""
        return KRW_MARGIN if self.live else USD_MARGIN

    # ------------------------------------------------------------ 계좌 · 조회

    def account(self) -> str:
        if self._account is None:
            data = self._post("/n2/acctinfo", {}, base=LIVE_URL)
            want = "01" if self.live else "03"
            found = [
                str(a["acct_no"])
                for a in data.get("Output_0") or []
                if str(a.get("acct_type")) == want
            ]
            if not found or (self.live and len(set(found)) != 1):
                raise NamuError(
                    "/n2/acctinfo",
                    "-",
                    f"{want} 계좌가 {len(set(found))} 개 — 실전은 하나여야 한다",
                )
            self._account = found[0]  # 모의(03)는 여러 개여도 국내 모의와 같은 첫 계좌
        return self._account

    def buyable_krw(self, symbol: str = "AAPL", price: float = 100.0) -> float:
        """원화 증거금으로 미국 주식을 살 수 있는 금액(원). 모의투자는 원화 증거금이 없어 달러."""
        path = "/gbstock/inquiry/v1/buyableAmount"
        data = self._post(
            path,
            {
                "act_no": self.account(),
                "pcs_dit": "1",
                "fc_sec_trd_nat_cd": US,
                "iem_cd": symbol.upper(),
                "fc_orr_uit_pr": round(price, 2),
                "wtm_cur_knd_cd": self.margin,
                "oss_orr_knd_cd": GTS,
                "ahi_nmn_pr_tp_cd": LIMIT,
            },
        )
        out = data.get("Output_0")
        if not isinstance(out, dict):
            raise NamuError(path, _code(data), _msg(data))
        return _f(out.get("orr_pbl_amt"))

    def sellable(self, symbol: str) -> int:
        path = "/gbstock/inquiry/v1/buyableAmount"
        data = self._post(
            path,
            {
                "act_no": self.account(),
                "pcs_dit": "3",
                "fc_sec_trd_nat_cd": US,
                "iem_cd": symbol.upper(),
                "wtm_cur_knd_cd": KRW_MARGIN,
                "oss_orr_knd_cd": GTS,
                "ahi_nmn_pr_tp_cd": LIMIT,
            },
        )
        out = data.get("Output_0")
        if not isinstance(out, dict):
            raise NamuError(path, _code(data), _msg(data))
        return _i(out.get("sll_pbl_qty"))

    def holdings(self) -> tuple[dict[str, Any], list[NamuHolding]]:
        """(계좌 합계 원문, 미국 보유 목록) — 원화 환산 · 비용 포함."""
        path = "/gbstock/inquiry/v1/balance"
        data = self._post(
            path,
            {
                "act_no": self.account(),
                "qut_iqr_dit_cd": "9",
                "fc_sec_trd_nat_cd": US,
                "cur_cd": "KRW",
                "xns_dit_cd": "1",
            },
        )
        head = data.get("Output_0")
        if not isinstance(head, dict):
            raise NamuError(path, _code(data), _msg(data))
        rows = [
            NamuHolding(
                symbol=str(r.get("iem_cd") or "").strip(),
                name=str(r.get("iem_nm") or "").strip(),
                qty=_i(r.get("cns_bse_bnc_qty")),
                sellable=_i(r.get("sll_pbl_qty1")),
                avg_usd=_f(r.get("fc_avg_phs_pr")) or _f(r.get("fc_phs_uit_pr")),
                last_usd=_f(r.get("fc_sec_end_pr")),
                eval_krw=_i(r.get("krw_eal_amt")),
                pnl_krw=_i(r.get("krw_eal_pls_amt")),
            )
            for r in data.get("Output_1") or []
            if _i(r.get("cns_bse_bnc_qty")) > 0
        ]
        return head, rows

    def quote(self, symbol: str) -> NamuQuote:
        """현재가 · 10 호가 (시세는 운영 서버에서만 — 모의투자도 같다)."""
        path = "/gbstock/quote/v1/current"
        data = self._post(path, {"iem_cd": symbol.upper()}, base=LIVE_URL)
        o = data.get("Output_0")
        if not isinstance(o, dict) or not _f(o.get("trdprc")):
            raise NamuError(path, _code(data), _msg(data))

        def side(name: str) -> list[tuple[Decimal, Decimal]]:
            out = []
            for k in range(1, 11):
                px, vol = _f(o.get(f"best_{name}{k}")), _f(o.get(f"best_{name[0]}siz{k}"))
                if px > 0 and vol > 0:
                    out.append((Decimal(str(px)), Decimal(str(vol))))
            return out

        return NamuQuote(
            symbol=symbol.upper(),
            last=_f(o.get("trdprc")),
            ask=_f(o.get("best_ask1")),
            bid=_f(o.get("best_bid1")),
            fx=_f(o.get("currency_prc")),
            asks=sorted(side("ask")),
            bids=sorted(side("bid"), reverse=True),
            regular_close=_f(o.get("normal_trdprc")),
            session=str(o.get("marketperiod_cls") or ""),
            trade_date=str(o.get("trade_date") or ""),
        )

    def asks(self, symbol: str) -> list[tuple[Decimal, Decimal]]:
        return self.quote(symbol).asks

    def bids(self, symbol: str) -> list[tuple[Decimal, Decimal]]:
        return self.quote(symbol).bids

    def orders(self, day: str, order_no: int | None = None) -> list[dict[str, Any]]:
        """그날(YYYYMMDD, 한국) 주문 · 체결 원문."""
        body: dict[str, Any] = {
            "orr_dt": day,
            "act_no": self.account(),
            "oss_sby_dit_cd": "0",
            "sot_dit": "0",
            "ost_cns_dit": "0",
        }
        if order_no:
            body["orr_no"] = order_no
        data = self._post("/gbstock/inquiry/v1/unexecuted", body)
        return [dict(r) for r in data.get("Output_0") or []]

    # ------------------------------------------------------------ 주문 (지정가 · 정규장만)

    def _check(self, symbol: str, side: str, qty: int, limit: Decimal) -> None:
        if self.live and not self.credentials.trading_enabled:
            raise TradingDisabledError("NAMU_TRADING_ENABLED 가 true 가 아니다 — 주문하지 않는다")
        if side not in ("BUY", "SELL"):
            raise TradingDisabledError(f"주문 방향 오류: {side}")
        if symbol not in self.allowed:
            raise TradingDisabledError(f"허용되지 않은 종목: {symbol}")
        if not 1 <= qty <= self.max_qty:
            raise TradingDisabledError(f"수량은 1 ~ {self.max_qty} 주만 (요청 {qty})")
        if limit <= 0 or limit != limit.quantize(Decimal("0.01")):
            raise TradingDisabledError(f"지정가는 0 보다 크고 센트 단위 (요청 {limit})")
        if float(limit) * qty > self.max_order_usd:
            raise TradingDisabledError(
                f"주문 금액 ${float(limit) * qty:,.2f} > 한 번 상한 ${self.max_order_usd:,.2f}"
            )

    def order(
        self,
        symbol: str,
        side: str,
        qty: int,
        limit: Decimal,
        *,
        client_order_id: str = "",
        time_in_force: str = "DAY",
    ) -> NamuOrderResult:
        """지정가 주문. ``client_order_id`` 는 나무에 없는 값이라 기록용으로만 받는다."""
        del client_order_id
        symbol = symbol.upper()
        self._check(symbol, side, qty, limit)
        if time_in_force != "DAY":
            raise TradingDisabledError("나무 해외 주문은 당일(DAY) 지정가만")
        body: dict[str, Any] = {
            "act_no": self.account(),
            "fc_sec_trd_nat_cd": US,
            "iem_cd": symbol,
            "orr_qty": qty,
            "fc_orr_uit_pr": float(limit),
            "ahi_nmn_pr_tp_cd": LIMIT,
        }
        path = "/gbstock/order/v1/buy" if side == "BUY" else "/gbstock/order/v1/sell"
        if side == "BUY":
            body["wtm_cur_knd_cd"] = self.margin
        day = datetime.fromtimestamp(self.clock(), KST).strftime("%Y%m%d")
        try:
            data = self._post(path, body)
        except httpx.HTTPError as exc:
            raise OrderStateUnknownError(
                f"나무 주문 전송 결과 모름 ({type(exc).__name__}) — 다시 보내지 않는다"
            ) from exc
        except NamuError as exc:
            if exc.code.isdigit() and int(exc.code) >= 500:
                raise OrderStateUnknownError(
                    f"나무 주문 서버 오류 {exc.code} — 접수 여부 모름, 다시 보내지 않는다"
                ) from exc
            return NamuOrderResult(False, exc.code, exc.message)
        out = data.get("Output_0")
        if isinstance(out, dict) and _i(out.get("orr_no")) > 0:
            return NamuOrderResult(True, f"{day}:{_i(out['orr_no'])}", "접수")
        return NamuOrderResult(False, _code(data), _msg(data))

    def order_status(self, order_id: str) -> NamuOrderStatus:
        day, no = _split(order_id)
        row: dict[str, Any] | None = None
        kids: list[dict[str, Any]] = []
        for d in (day, _shift(day, -1), _shift(day, 1)):
            rows = self.orders(d)
            row = next((r for r in rows if _i(r.get("orr_no")) == no), row)
            kids += [r for r in rows if _i(r.get("org_orr_no")) == no and _i(r.get("orr_no")) != no]
            if row is not None:
                break
        if row is None:
            return NamuOrderStatus("", Decimal(0), None, None, None, note="주문 조회에 없음")
        qty = _i(row.get("orr_qty"))
        filled = _i(row.get("cns_qty"))
        open_qty = _i(row.get("ny_cns_orr_qty"))
        canceled = _i(row.get("can_qty")) + sum(
            _i(k.get("can_qty")) or _i(k.get("cns_qty")) for k in kids
        )
        px = _f(row.get("cns_pr"))
        reject = str(row.get("rjt_rsn_cts") or "").strip()
        if reject and filled == 0:
            status = "REJECTED"
        elif filled >= qty > 0:
            status = "FILLED"
        elif canceled > 0 and filled + canceled >= qty:
            status = "CANCELED"
        elif open_qty > 0 or filled + canceled < qty:
            status = "PARTIAL_FILLED" if filled else "PENDING"
        else:
            status = ""
        return NamuOrderStatus(
            status,
            Decimal(filled),
            Decimal(str(px)) if px else None,
            Decimal(str(round(px * filled, 4))) if px and filled else None,
            None,
            open_qty=open_qty,
            note=f"{row.get('orr_sts_nm') or ''} {reject}".strip(),
        )

    def cancel(self, order_id: str, symbol: str = "") -> NamuOrderResult:
        """남은 수량 전체 취소. 종목을 모르면 체결 조회에서 찾는다."""
        if self.live and not self.credentials.trading_enabled:
            raise TradingDisabledError("NAMU_TRADING_ENABLED 가 true 가 아니다 — 취소하지 않는다")
        day, no = _split(order_id)
        if not symbol:
            rows = [r for d in (day, _shift(day, -1), _shift(day, 1)) for r in self.orders(d)]
            hit = next((r for r in rows if _i(r.get("orr_no")) == no), None)
            if hit is None:
                return NamuOrderResult(False, "-", "취소할 주문을 찾지 못함")
            symbol = str(hit.get("iem_cd") or "").strip()
        path = "/gbstock/order/v1/cancel"
        try:
            data = self._post(
                path,
                {
                    "act_no": self.account(),
                    "org_orr_no": no,
                    "fc_sec_trd_nat_cd": US,
                    "iem_cd": symbol.upper(),
                    "all_pat_dit_cd": "1",
                },
            )
        except httpx.HTTPError as exc:
            raise OrderStateUnknownError(
                f"나무 취소 전송 결과 모름 ({type(exc).__name__}) — 조회로 확인"
            ) from exc
        except NamuError as exc:
            return NamuOrderResult(False, exc.code, exc.message)
        out = data.get("Output_0")
        if isinstance(out, dict) and _i(out.get("orr_no")) > 0:
            return NamuOrderResult(True, f"{day}:{_i(out['orr_no'])}", "취소 접수")
        return NamuOrderResult(False, _code(data), _msg(data))


def _split(order_id: str) -> tuple[str, int]:
    day, _, no = order_id.partition(":")
    if len(day) != 8 or not no.isdigit():
        raise ValueError(f"나무 주문 ID 형식 오류: {order_id}")
    return day, int(no)


def _shift(day: str, n: int) -> str:
    d = datetime.strptime(day, "%Y%m%d").replace(tzinfo=KST)
    return (d + timedelta(days=n)).strftime("%Y%m%d")
