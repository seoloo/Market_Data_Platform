"""국내 주식 매일 적립 — 순수 계산 (사용자 2026-10-10 "국내 주식도 포함").

* 하루 금액(원)이 달력 날마다 쌓이고, 쌓인 돈이 1 주 값(+ 비용)이 되면 1 주를 산다.
  미국 적립과 같은 셈.
* 토스 API 는 국내 장 마감 지정가(CLS)가 없다 → **평일 낮 당일(DAY) 지정가**, 현재가 +0.5% 를
  호가 단위로 올림(바로 체결될 만큼, 시장가는 쓰지 않는다 — 사용자 규칙 2026-10-05).
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
COST = 0.0015
"""수수료 · 세금 여유 (매수는 세금 없음, 토스 수수료 약 0.015% 보다 넉넉히)."""


def tick(price: float, etf: bool) -> int:
    """유가증권 · 코스닥 호가 단위 (2023-01 개편). ETF · ETN 은 2,000 원 미만 1 원, 그 위 5 원."""
    if etf:
        return 1 if price < 2_000 else 5
    for top, step in (
        (2_000, 1),
        (5_000, 5),
        (20_000, 10),
        (50_000, 50),
        (200_000, 100),
        (500_000, 500),
    ):
        if price < top:
            return step
    return 1_000


def limit_for(price: float, etf: bool) -> int:
    """현재가 +0.5% 를 그 가격대 호가 단위로 올린 지정가."""
    want = price * (1 + BUFFER)
    step = tick(want, etf)
    return int(math.ceil(want / step) * step)


def _days(a: str, b: str) -> int:
    from datetime import date

    return (date.fromisoformat(b) - date.fromisoformat(a)).days + 1


def accrual(history: Sequence[tuple[str, float]], start: str, today: str) -> float:
    """(그날부터, 하루 금액) 기록 → ``start`` ~ ``today`` (양 끝 포함) 쌓인 돈. 끈 기간은 0."""
    if today < start:
        return 0.0
    rows = sorted(history)
    total = 0.0
    for i, (day, rate) in enumerate(rows):
        seg_from = max(day, start)
        seg_to = rows[i + 1][0] if i + 1 < len(rows) else None
        last = today if seg_to is None else min(today, _prev(seg_to))
        if seg_from <= last:
            total += rate * _days(seg_from, last)
    return total


def _prev(day: str) -> str:
    from datetime import date, timedelta

    return (date.fromisoformat(day) - timedelta(days=1)).isoformat()


@dataclass(slots=True)
class KrOrder:
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
        return sum(float(b["qty"]) for b in self.buys)

    @property
    def spent(self) -> float:
        return sum(float(b["cost_krw"]) for b in self.buys)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["last_qty"] = self.qty  # 대시보드 · 보고가 읽는 이름 (미국 적립 장부와 같게)
        return d

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> KrState:
        return cls(
            symbol=str(d["symbol"]),
            name=str(d.get("name") or d["symbol"]),
            etf=bool(d.get("etf")),
            start_date=str(d["start_date"]),
            buys=[dict(b) for b in d.get("buys") or []],
            orders=[KrOrder(**o) for o in d.get("orders") or []],
            market=str(d.get("market") or ""),
        )


@dataclass(frozen=True, slots=True)
class KrAdvice:
    status: str
    """BUY · WAIT · OPEN(어제 주문 확인 전) · OFF."""
    accrued: float
    limit: int
    need: float
    """1 주 + 비용 여유."""


def advise(
    state: KrState, history: Sequence[tuple[str, float]], today: str, price: float
) -> KrAdvice:
    accrued = accrual(history, state.start_date, today) - state.spent
    limit = limit_for(price, state.etf)
    need = limit * (1 + COST)
    if any(o.status in ("SENT", "UNKNOWN") for o in state.orders):
        return KrAdvice("OPEN", accrued, limit, need)
    if not history or sorted(history)[-1][1] <= 0:
        return KrAdvice("OFF", accrued, limit, need)
    if any(o.date == today for o in state.orders):
        return KrAdvice("WAIT", accrued, limit, need)  # 하루 한 건
    return KrAdvice("BUY" if accrued >= need else "WAIT", accrued, limit, need)


def settle(
    order: KrOrder, status: str, filled_qty: float, filled_krw: float, commission: float
) -> dict[str, Any] | None:
    """주문 상세 → 주문 상태를 고치고, 체결이 있으면 장부에 넣을 매수 한 줄."""
    if status in ("PENDING", "PENDING_CANCEL", "PENDING_REPLACE", "PARTIAL_FILLED"):
        return None  # 아직 장중
    order.filled_qty, order.filled_krw, order.commission = filled_qty, filled_krw, commission
    if filled_qty > 0:
        order.status = "FILLED"
        return {"date": order.date, "qty": filled_qty, "cost_krw": filled_krw + commission}
    order.status = "REJECTED" if status == "REJECTED" else "EXPIRED"
    return None


# ---------------------------------------------------------------- 종목 찾기


def _norm(text: str) -> str:
    return re.sub(r"[\s·\-_.()]+", "", text).casefold()


@dataclass(frozen=True, slots=True)
class Listed:
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
    out = []
    for market, rows in by_market.items():
        for r in rows:
            sym, name = str(r.get("symbol") or ""), str(r.get("name") or "")
            if sym and name:
                out.append(Listed(sym.upper(), name, market, str(r.get("securityType") or "")))
    return out


def find(query: str, universe: Sequence[Listed], country: str) -> list[Listed]:
    """기호 · 코드 · 한글 이름(띄어쓰기 무시)으로 찾는다.

    정확히 맞는 것 하나, 없으면 이름에 포함된 것 5 개."""
    q = query.strip()
    pool = [x for x in universe if x.country == country]
    exact = [x for x in pool if x.symbol == q.upper() or _norm(x.name) == _norm(q)]
    if exact:
        return exact[:1]
    key = _norm(q)
    return [x for x in pool if key and key in _norm(x.name)][:5]


def read_universe(path: Any) -> list[Listed]:
    """``data/toss/universe.json`` ({"markets": {시장: [행]}}) → 목록. 없거나 깨지면 빈 목록."""
    import json
    from pathlib import Path

    try:
        d = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    markets = d.get("markets") if isinstance(d, dict) else None
    return universe_rows(markets) if isinstance(markets, dict) else []
