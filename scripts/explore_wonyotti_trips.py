"""워뇨띠 v2 — XBTUSD 체결을 왕복 거래로 재구성해 보유·크기·방향·손익을 연도별로 낸다.

탐색 단계(docs/research/06-wonyotti-exploration.md). 표준 라이브러리만 쓰고 원자료는 읽기만 한다.

정의:
* **왕복 거래**: 순포지션이 0에서 벗어난 체결부터 다시 0이 되는 체결까지. 한 체결이 0을
  건너 반대 포지션이 되면 그 가격에서 청산분과 신규분으로 나눈다.
* **손익(BTC)**: XBTUSD는 인버스 계약(1계약=1달러)이므로 총손익 = Σ(부호 계약수 / 체결가),
  매수 양수. 순손익 = 총손익 − 체결 수수료 − 보유 중 funding(`execcomm`, 지불이 양수).
* **잔고 대비 배율**: 최대 |포지션| 계약(달러) / (진입 전날 말 지갑 잔고 BTC × 진입가).
  유튜브 요약의 "잔고 대비 포지션 크기"와 같은 뜻이다. 지갑은 모든 상품 합계이므로 근사다.
"""

from __future__ import annotations

import csv
import json
import statistics
import sys
from bisect import bisect_left
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

SOURCE = Path("data/external/wonyotti")
OUTPUT = Path("reports/wonyotti/summary_v2.json")
_SATOSHI = 100_000_000
_SIZE_BUCKETS = ((0.0, 1.0), (1.0, 2.0), (2.0, 4.0), (4.0, float("inf")))


@dataclass(frozen=True, slots=True)
class Fill:
    at: datetime
    qty: int
    """부호 있는 계약 수. 매수 양수."""
    price: float
    fee_xbt: int


@dataclass(slots=True)
class Trip:
    side: int
    opened: datetime
    closed: datetime | None = None
    gross_btc: float = 0.0
    cost_xbt: int = 0
    max_abs: int = 0
    open_price: float = 0.0

    @property
    def net_btc(self) -> float:
        return self.gross_btc - self.cost_xbt / _SATOSHI

    @property
    def hours(self) -> float:
        if self.closed is None:
            return 0.0
        return (self.closed - self.opened).total_seconds() / 3600


@dataclass(slots=True)
class _Book:
    trips: list[Trip] = field(default_factory=list)
    open_trip: Trip | None = None
    position: int = 0


def _apply(book: _Book, at: datetime, qty: int, price: float, fee: int) -> None:
    """부호 있는 수량 하나를 적용한다. 0을 넘는 부분은 새 거래로 넘긴다."""
    if qty == 0:
        return
    if book.open_trip is None:
        book.open_trip = Trip(side=1 if qty > 0 else -1, opened=at, open_price=price)
    trip = book.open_trip
    closing = book.position != 0 and (book.position > 0) != (qty > 0)
    if closing and abs(qty) > abs(book.position):
        part = -book.position
        share = round(fee * abs(part) / abs(qty))
        _apply(book, at, part, price, share)
        _apply(book, at, qty - part, price, fee - share)
        return
    trip.gross_btc += qty / price
    trip.cost_xbt += fee
    book.position += qty
    trip.max_abs = max(trip.max_abs, abs(book.position))
    if book.position == 0:
        trip.closed = at
        book.trips.append(trip)
        book.open_trip = None


def build_trips(fills: Iterable[Fill], funding: Sequence[tuple[datetime, int]]) -> list[Trip]:
    """시간순 체결로 왕복 거래를 만들고, 보유 구간의 funding을 비용에 더한다."""
    book = _Book()
    for fill in fills:
        _apply(book, fill.at, fill.qty, fill.price, fill.fee_xbt)
    times = [at for at, _ in funding]
    for trip in book.trips:
        assert trip.closed is not None
        start = bisect_left(times, trip.opened)
        end = bisect_left(times, trip.closed)
        trip.cost_xbt += sum(amount for _, amount in funding[start:end])
    return book.trips


def _parse_time(text: str) -> datetime:
    """BitMEX 내보내기 시각은 UTC다."""
    return datetime.fromisoformat(text).replace(tzinfo=UTC)


def load_xbtusd(directory: Path) -> tuple[list[Fill], list[tuple[datetime, int]], Counter[str]]:
    """XBTUSD 체결·funding과, 전 상품의 연도별 청산 사건 수((날짜, 상품) 단위)를 읽는다."""
    fills: list[tuple[datetime, int, Fill]] = []
    funding: list[tuple[datetime, int]] = []
    liquidation_events: dict[str, set[tuple[str, str]]] = defaultdict(set)
    index = 0
    for path in sorted(directory.glob("aoa-execution-*.csv")):
        with path.open(encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                index += 1
                marker = (row["text"] + row["ordtype"] + row["execinst"]).lower()
                if row["exectype"] == "Trade" and "liquidat" in marker:
                    # 청산 표시는 text·ordtype·execinst에 흩어져 있고 주문 ID도 일정하지 않다.
                    liquidation_events[row["date"][:4]].add((row["date"], row["symbol"]))
                if row["symbol"] != "XBTUSD":
                    continue
                at = _parse_time(row["transacttime"])
                fee = int(float(row["execcomm"])) if row["execcomm"] else 0
                if row["exectype"] == "Funding":
                    funding.append((at, fee))
                elif row["exectype"] == "Trade":
                    qty = int(row["lastqty"]) * (1 if row["side"] == "Buy" else -1)
                    fills.append((at, index, Fill(at, qty, float(row["lastpx"]), fee)))
    fills.sort(key=lambda item: (item[0], item[1]))
    funding.sort()
    liquidations = Counter({year: len(days) for year, days in liquidation_events.items()})
    return [fill for _, _, fill in fills], funding, liquidations


def load_wallet(path: Path) -> tuple[dict[str, int], dict[str, int], int]:
    """(날짜별 말 잔고 XBt, 날짜별 전 상품 실현손익 XBt, XBTUSD 실현손익 합 XBt)."""
    closing: dict[str, int] = {}
    pnl: defaultdict[str, int] = defaultdict(int)
    xbtusd = 0
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = [r for r in csv.DictReader(handle) if r["date"] and r["currency"] == "XBt"]
    for row in sorted(rows, key=lambda r: r["date"]):
        if row["transactstatus"] != "Completed":
            continue
        if row["walletbalance"]:
            # 스프레드시트를 거친 잔고는 1.00334E+11 같은 지수 표기(유효숫자 약 6자리)가 섞여 있다.
            closing[row["date"]] = int(float(row["walletbalance"]))
        if row["transacttype"] == "RealisedPNL":
            pnl[row["date"]] += int(row["amount"])
            if row["address"] == "XBTUSD":
                xbtusd += int(row["amount"])
    return closing, dict(pnl), xbtusd


def _balance_before(closing: dict[str, int], dates: list[str], day: str) -> int | None:
    index = bisect_left(dates, day) - 1
    return closing[dates[index]] if index >= 0 else None


def _mean(values: Sequence[float]) -> float | None:
    return statistics.fmean(values) if values else None


def _median(values: Sequence[float]) -> float | None:
    return statistics.median(values) if values else None


def summarize(
    trips: list[Trip], closing: dict[str, int], daily_pnl: dict[str, int]
) -> dict[str, object]:
    dates = sorted(closing)
    by_year: dict[str, dict[str, object]] = {}
    size_pnl: defaultdict[str, float] = defaultdict(float)
    for year in sorted({t.opened.strftime("%Y") for t in trips}):
        chosen = [t for t in trips if t.opened.strftime("%Y") == year]
        days = (max(t.opened for t in chosen) - min(t.opened for t in chosen)).days + 1
        multiples: list[float] = []
        for trip in chosen:
            balance = _balance_before(closing, dates, trip.opened.strftime("%Y-%m-%d"))
            if balance and balance > 0:
                multiple = trip.max_abs / (balance / _SATOSHI * trip.open_price)
                multiples.append(multiple)
                for low, high in _SIZE_BUCKETS:
                    if low <= multiple < high:
                        size_pnl[f"{low:g}-{high:g}"] += trip.net_btc
        winners = [t.hours * 60 for t in chosen if t.net_btc > 0]
        losers = [t.hours * 60 for t in chosen if t.net_btc < 0]
        by_year[year] = {
            "trips": len(chosen),
            "trips_per_day": len(chosen) / days,
            "hold_minutes_mean": _mean([t.hours * 60 for t in chosen]),
            "hold_minutes_median": _median([t.hours * 60 for t in chosen]),
            "size_multiple_mean": _mean(multiples),
            "size_multiple_median": _median(multiples),
            "share_over_2x": sum(m > 2 for m in multiples) / len(multiples) if multiples else None,
            "win_hold_minutes_mean": _mean(winners),
            "loss_hold_minutes_mean": _mean(losers),
            "net_btc_long": sum(t.net_btc for t in chosen if t.side > 0),
            "net_btc_short": sum(t.net_btc for t in chosen if t.side < 0),
        }
    worst = sorted(trips, key=lambda t: t.net_btc)[:5]
    # 실현손익은 BitMEX 일일 정산(12:00 UTC)에 기록되므로 오후 손실은 다음 날 행에 나타난다.
    worst_day = min(
        (
            (amount / balance, day)
            for day, amount in daily_pnl.items()
            if (balance := _balance_before(closing, dates, day) or 0) > 0
        ),
        default=None,
    )
    return {
        "by_year": by_year,
        "net_btc_by_size_multiple": dict(size_pnl),
        "net_btc_long_total": sum(t.net_btc for t in trips if t.side > 0),
        "net_btc_short_total": sum(t.net_btc for t in trips if t.side < 0),
        "worst_trips": [
            {
                "opened": t.opened.isoformat(),
                "closed": t.closed.isoformat() if t.closed else None,
                "days": t.hours / 24,
                "side": "LONG" if t.side > 0 else "SHORT",
                "net_btc": t.net_btc,
            }
            for t in worst
        ],
        "worst_realised_day": (
            {"date": worst_day[1], "over_prev_balance": worst_day[0]} if worst_day else None
        ),
    }


def main() -> int:
    fills, funding, liquidations = load_xbtusd(SOURCE)
    trips = build_trips(fills, funding)
    closing, daily_pnl, wallet_xbtusd = load_wallet(SOURCE / "aoa-wallet-2018-03-01-2021-12-31.csv")
    summary = {
        "fills": len(fills),
        "trips": len(trips),
        "reconstructed_net_btc": sum(t.net_btc for t in trips),
        "wallet_xbtusd_realised_btc": wallet_xbtusd / _SATOSHI,
        "liquidation_events_all_symbols": dict(sorted(liquidations.items())),
        **summarize(trips, closing, daily_pnl),
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(summary, ensure_ascii=False, indent=1, sort_keys=True)
    OUTPUT.write_text(text + "\n", encoding="utf-8")
    sys.stdout.write(text + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
