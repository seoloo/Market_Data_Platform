"""Paper 장부의 해외 주식 양도세 추정 — 실현 이익 · 연 250 만 원 공제 · 22% · 12 월 공제 실현(D).

가상 계좌 금액을 실제 투자 금액으로 바꿔 생각한다: ``scale_krw`` 원 = 장부 시작 금액.
그러면 공제 250 만 원은 장부 단위로 ``EXEMPTION_KRW / scale_krw × start`` 다
(환율 변화는 무시 — 근사).

* 매도 · 덜어내기의 실현 이익(``gain``)을 그해에 더한다.
* 해가 바뀌는 첫 처리일에 지난해 세금 = 22% × max(0, 실현 이익 − 공제) 를 쌓는다
  (장부 현금은 그대로 두고
  세후 평가액은 따로 보인다 — 세전 장부와 비교하려고).
* D: 12 월 15 일 이후 첫 처리일에, 그해 실현 이익이 공제보다 작으면
  남은 공제만큼 평가 이익을 실현하고
  같은 날 다시 산 것으로 친다(원가가 올라가 다음 해 세금이 준다, 사고파는 비용은 낸다).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, field
from typing import Any

from auto_trading.ops.lt_paper import FEE, Closes, PaperState, _px

EXEMPTION_KRW = 2_500_000
RATE = 0.22
HARVEST_FROM = "12-15"


@dataclass
class TaxBook:
    scale_krw: float
    start: float
    year: str = ""
    realized: float = 0.0
    """그해 실현 이익 (장부 단위)."""
    tax_paid: float = 0.0
    """지난해까지 쌓인 세금 (장부 단위)."""
    harvested_years: list[str] = field(default_factory=list)
    history: list[dict[str, Any]] = field(default_factory=list)

    @property
    def exemption(self) -> float:
        return EXEMPTION_KRW / self.scale_krw * self.start

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> TaxBook:
        return cls(**dict(data))


def estimate(book: TaxBook) -> float:
    """그해 지금까지 실현 이익으로 본 세금 (장부 단위)."""
    return RATE * max(0.0, book.realized - book.exemption)


def roll_year(book: TaxBook, d: str) -> dict[str, Any] | None:
    """``d`` 가 새해의 첫 처리일이면 지난해를 정산한다."""
    if not book.year:
        book.year = d[:4]
        return None
    if d[:4] == book.year:
        return None
    tax = estimate(book)
    row = {"year": book.year, "realized": round(book.realized, 4), "tax": round(tax, 4)}
    book.history.append(row)
    book.tax_paid += tax
    book.year, book.realized = d[:4], 0.0
    return row


def add_trades(book: TaxBook, trades: Iterable[Mapping[str, Any]]) -> None:
    for tr in trades:
        book.realized += float(tr.get("gain", 0.0))


def harvest(
    book: TaxBook, state: PaperState, closes: Mapping[str, Closes], t: int, d: str
) -> list[dict[str, Any]]:
    """D: 그해 처음 12 월 15 일 이후 처리일에 남은 공제만큼 평가 이익을 실현하고 다시 산다."""
    if d[5:] < HARVEST_FROM or d[:4] in book.harvested_years:
        return []
    book.harvested_years.append(d[:4])
    room = book.exemption - book.realized
    done: list[dict[str, Any]] = []
    gains = []
    for sym, pos in state.positions.items():
        value = pos.qty * _px(closes[sym], t)
        if value > pos.cost:
            gains.append((value - pos.cost, sym, value))
    for unreal, sym, value in sorted(gains, reverse=True):
        if room <= 0:
            break
        g = min(unreal, room)
        part = value * g / unreal  # 이만큼 팔고 같은 날 다시 산다
        fee = part * FEE * 2
        pos = state.positions[sym]
        pos.cost += g  # 원가가 실현한 이익만큼 오른다
        pos.qty -= fee / _px(closes[sym], t)
        book.realized += g
        room -= g
        done.append({"symbol": sym, "realized": round(g, 4), "fee": round(fee, 4)})
    return done
