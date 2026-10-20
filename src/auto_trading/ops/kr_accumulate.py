"""🇰🇷 국내 주식 매일 적립 계산 (사용자 2026-10-10 "국내 주식도 포함") · 토스 종목 찾기.

언제 쓰나: 평일 12:10 ``AutoTrading_Toss_KR`` (``scripts/toss_kr.py``) · 설정 화면 '종목 추가'.
읽고 쓰는 것: 계산만 — :func:`read_universe` 만 종목 목록 ``data/toss/universe.json`` 을 읽는다.

* 하루 금액(원)이 달력 날마다 쌓이고, 쌓인 돈이 1 주 값(+ 비용)이 되면 **살 수 있는 만큼(정수 주)**
  산다 — 미국 적립과 같은 셈(사용자 2026-10-21 "그 금액만큼 적립해야지", 그 전엔 하루 1 주 고정).
  남는 돈은 다음 날로 이어지고, 한 번에 :data:`MAX_ORDER_QTY` 주까지.
* 토스 API 는 국내 장 마감 지정가(CLS)가 없다 → **평일 낮 당일(DAY) 지정가**, 현재가 +0.5% 를
  호가 단위로 올림(바로 체결될 만큼 — 2026-10-05 당시 사용자 규칙 '시장가 금지',
  규칙은 10-18 에 지워졌지만 이 코드는 그대로).
* 체결은 다음 실행에서 주문 상세로 확인한다(당일 지정가는 장 마감에 남은 수량이 저절로 취소).
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

BUFFER = 0.005
"""지정가 = 현재가 × (1 + 0.5%) 를 호가 단위로 올림."""
MAX_ORDER_QTY = 50
"""국내 적립 주문 한 건 수량 상한 — 토스 주문 창구의 마지막 문과 같은 값 (넘는 만큼은 다음 날로)."""
COST = 0.0015
"""수수료 · 세금 여유 (매수는 세금 없음, 토스 수수료 약 0.015% 보다 넉넉히)."""


def tick(price: float, etf: bool) -> int:
    """[쓰임] 그 가격의 호가 단위 (원) — 유가증권 · 코스닥 (2023-01 개편).
    [이유] 호가 단위에 안 맞는 가격은 주문이 거절된다.
           ETF · ETN 은 2,000 원 미만 1 원, 그 위 5 원."""
    if etf:
        return 1 if price < 2_000 else 5
    for upper_price, step in (
        (2_000, 1),
        (5_000, 5),
        (20_000, 10),
        (50_000, 50),
        (200_000, 100),
        (500_000, 500),
    ):
        if price < upper_price:
            return step
    return 1_000


def limit_for(price: float, etf: bool) -> int:
    """[쓰임] 현재가 +0.5% 를 그 가격대 호가 단위로 올린 지정가 (원)."""
    wanted = price * (1 + BUFFER)
    step = tick(wanted, etf)
    return int(math.ceil(wanted / step) * step)


def _days(start: str, end: str) -> int:
    """[쓰임] 두 날짜 사이 날수 — 양 끝 포함."""
    from datetime import date

    return (date.fromisoformat(end) - date.fromisoformat(start)).days + 1


def _previous_day(day: str) -> str:
    from datetime import date, timedelta

    return (date.fromisoformat(day) - timedelta(days=1)).isoformat()


def accrual(history: Sequence[tuple[str, float]], start: str, today: str) -> float:
    """[쓰임] (그날부터, 하루 금액) 기록 → ``start`` ~ ``today`` (양 끝 포함) 쌓인 돈. 끈 기간은 0.
    [이유] 하루 금액을 바꿔도 지난 날은 그때 금액으로 센다 (설정 화면 '내일부터')."""
    if today < start:
        return 0.0
    rows = sorted(history)
    total = 0.0
    for index, (day, rate) in enumerate(rows):
        segment_start = max(day, start)
        next_change = rows[index + 1][0] if index + 1 < len(rows) else None
        segment_end = today if next_change is None else min(today, _previous_day(next_change))
        if segment_start <= segment_end:
            total += rate * _days(segment_start, segment_end)
    return total


@dataclass(slots=True)
class KrOrder:
    """국내 적립 주문 하나 (장부 ``orders`` 한 줄 — 키 이름은 파일에 저장돼 바꾸지 않는다)."""

    date: str
    limit: int
    client_id: str
    order_id: str = ""
    status: str = "SENT"
    """SENT · UNKNOWN(전송 결과 모름) · FILLED · EXPIRED · REJECTED."""
    filled_qty: float = 0.0
    filled_krw: float = 0.0
    commission: float = 0.0
    message: str = ""


@dataclass(slots=True)
class KrState:
    """국내 적립 종목 장부 (``data/toss/KR_<코드>.json``)."""

    symbol: str
    name: str
    etf: bool
    start_date: str
    buys: list[dict[str, Any]] = field(default_factory=list)
    """{"date", "qty", "cost_krw"} — 체결 확인된 것만."""
    orders: list[KrOrder] = field(default_factory=list)
    currency: str = "KRW"
    market: str = ""
    """KOSPI · KOSDAQ — 시세 기호(.KS · .KQ)를 고르는 데 쓴다."""

    @property
    def qty(self) -> float:
        """[쓰임] 적립으로 산 수량 합."""
        return sum(float(buy["qty"]) for buy in self.buys)

    @property
    def spent(self) -> float:
        """[쓰임] 적립으로 쓴 돈 합 (수수료 포함, 원)."""
        return sum(float(buy["cost_krw"]) for buy in self.buys)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["last_qty"] = self.qty  # 대시보드 · 보고가 읽는 이름 (미국 적립 장부와 같게)
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> KrState:
        return cls(
            symbol=str(data["symbol"]),
            name=str(data.get("name") or data["symbol"]),
            etf=bool(data.get("etf")),
            start_date=str(data["start_date"]),
            buys=[dict(buy) for buy in data.get("buys") or []],
            orders=[KrOrder(**order) for order in data.get("orders") or []],
            market=str(data.get("market") or ""),
        )


@dataclass(frozen=True, slots=True)
class KrAdvice:
    """오늘 판단."""

    status: str
    """BUY · WAIT · OPEN(어제 주문 확인 전) · OFF."""
    accrued: float
    limit: int
    need: float
    """1 주 + 비용 여유."""
    qty: int = 0
    """오늘 살 주수 (BUY 일 때만 1 이상) — 쌓인 돈 ÷ :attr:`need` 의 정수 부분."""


def advise(
    state: KrState, history: Sequence[tuple[str, float]], today: str, price: float
) -> KrAdvice:
    """[쓰임] 오늘 살 차례인가 — 쌓인 돈(쓴 돈 뺌)이 1 주 지정가 + 비용 여유 이상이면 BUY
    (살 수 있는 만큼 ``qty`` 주).
    [이유] 확인 전 주문이 있으면(OPEN) · 꺼졌으면(OFF) · 오늘 이미 냈으면(WAIT) 사지 않는다
           — 하루 한 건."""
    accrued = accrual(history, state.start_date, today) - state.spent
    limit = limit_for(price, state.etf)
    need = limit * (1 + COST)
    if any(order.status in ("SENT", "UNKNOWN") for order in state.orders):
        return KrAdvice("OPEN", accrued, limit, need)
    if not history or sorted(history)[-1][1] <= 0:
        return KrAdvice("OFF", accrued, limit, need)
    if any(order.date == today for order in state.orders):
        return KrAdvice("WAIT", accrued, limit, need)  # 하루 한 건
    if accrued < need:
        return KrAdvice("WAIT", accrued, limit, need)
    qty = max(1, min(math.floor(accrued / need + 1e-9), MAX_ORDER_QTY))
    return KrAdvice("BUY", accrued, limit, need, qty)


def settle(
    order: KrOrder, status: str, filled_qty: float, filled_krw: float, commission: float
) -> dict[str, Any] | None:
    """[쓰임] 주문 상세 → 주문 상태를 고치고, 체결이 있으면 장부에 넣을 매수 한 줄.

    아직 장중(대기 · 부분 체결)이면 None 을 돌려주고 주문은 그대로 둔다."""
    if status in ("PENDING", "PENDING_CANCEL", "PENDING_REPLACE", "PARTIAL_FILLED"):
        return None  # 아직 장중
    order.filled_qty = filled_qty
    order.filled_krw = filled_krw
    order.commission = commission
    if filled_qty > 0:
        order.status = "FILLED"
        return {"date": order.date, "qty": filled_qty, "cost_krw": filled_krw + commission}
    order.status = "REJECTED" if status == "REJECTED" else "EXPIRED"
    return None


# ---------------------------------------------------------------- 종목 찾기


def _normalize(text: str) -> str:
    """[쓰임] 찾기용 글자 — 띄어쓰기 · 점 · 괄호 · 하이픈을 지우고 대소문자를 무시."""
    return re.sub(r"[\s·\-_.()]+", "", text).casefold()


@dataclass(frozen=True, slots=True)
class Listed:
    """토스 거래 가능 종목 하나."""

    symbol: str
    name: str
    market: str
    """KOSPI · KOSDAQ · NYSE · NASDAQ · AMEX."""
    kind: str
    """STOCK · ETF · …"""

    @property
    def country(self) -> str:
        return "KR" if self.market in ("KOSPI", "KOSDAQ") else "US"

    @property
    def etf(self) -> bool:
        return self.kind in ("ETF", "ETN", "FOREIGN_ETF")


def universe_rows(by_market: Mapping[str, Iterable[Mapping[str, Any]]]) -> list[Listed]:
    """[쓰임] 토스 종목 목록 {시장: [행]} → :class:`Listed` 목록 (기호 · 이름이 빈 줄은 뺀다)."""
    listed = []
    for market, rows in by_market.items():
        for row in rows:
            symbol = str(row.get("symbol") or "")
            name = str(row.get("name") or "")
            if symbol and name:
                listed.append(
                    Listed(symbol.upper(), name, market, str(row.get("securityType") or ""))
                )
    return listed


def find(query: str, universe: Sequence[Listed], country: str) -> list[Listed]:
    """[쓰임] 기호 · 코드 · 한글 이름(띄어쓰기 무시)으로 찾는다.
    [이유] 정확히 맞는 것이 있으면 그 하나만, 없으면 이름에 포함된 것 5 개 (고르기 쉽게)."""
    query = query.strip()
    pool = [item for item in universe if item.country == country]
    exact = [
        item
        for item in pool
        if item.symbol == query.upper() or _normalize(item.name) == _normalize(query)
    ]
    if exact:
        return exact[:1]
    key = _normalize(query)
    return [item for item in pool if key and key in _normalize(item.name)][:5]


def read_universe(path: Any) -> list[Listed]:
    """[쓰임] ``data/toss/universe.json`` ({"markets": {시장: [행]}}) → 목록.

    없거나 깨지면 빈 목록."""
    import json
    from pathlib import Path

    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    markets = data.get("markets") if isinstance(data, dict) else None
    return universe_rows(markets) if isinstance(markets, dict) else []
