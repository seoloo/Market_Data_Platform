"""전후 비교 기준 (리팩토링 7 단계) — 💹 개별주 · 매일 기록 · 파이어 보고.

바꾸기 전 코드로 기준(`tests/golden/expected/*.txt`)을 찍고, 바꾼 뒤 글자까지 같은지 본다.
가짜 토스(시세 · 정규장 · 호가 · 체결)와 고정 시계로 돌린다 — 주문 · 네트워크 없음.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from auto_trading.core.errors import AutoTradingError, OrderStateUnknownError
from auto_trading.ops import position_size as ps
from auto_trading.ops import size_order as so
from auto_trading.ops import stop_guard as sg
from auto_trading.ops.entry_events import EntryEvents
from tests.golden.test_live_golden import _check_golden

KST = ZoneInfo("Asia/Seoul")


def _ms(text: str) -> int:
    return int(datetime.fromisoformat(text).replace(tzinfo=KST).timestamp() * 1000)


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


# ------------------------------------------------------------------ 가짜 토스


@dataclass
class R:
    accepted: bool
    code: str
    message: str = ""


@dataclass
class S:
    status: str
    filled_qty: Decimal
    avg_price: Decimal | None
    filled_amount: Decimal | None
    commission: Decimal | None = Decimal(0)


@dataclass
class FakeTrader:
    ask: float = 100.0
    bid: float = 99.9
    fills: dict[str, tuple[str, float, float]] = field(default_factory=dict)
    placed: list[dict[str, Any]] = field(default_factory=list)
    canceled: list[str] = field(default_factory=list)
    fail: Exception | None = None
    reject: str = ""

    def asks(self, symbol: str) -> list[tuple[Any, Any]]:
        return [(Decimal(str(self.ask)), Decimal(100))] if self.ask else []

    def bids(self, symbol: str) -> list[tuple[Any, Any]]:
        return [(Decimal(str(self.bid)), Decimal(1000))] if self.bid else []

    def order(self, symbol: str, side: str, qty: int, limit: Any, **kw: Any) -> R:
        if self.fail:
            raise self.fail
        if self.reject:
            return R(False, self.reject, "거절 사유 시험")
        oid = f"o{len(self.placed)}"
        self.placed.append(
            {"id": oid, "symbol": symbol, "side": side, "qty": qty, "limit": str(limit), **kw}
        )
        self.fills.setdefault(oid, ("PENDING", 0.0, 0.0))
        return R(True, oid)

    def order_status(self, order_id: str) -> S:
        state, qty, price = self.fills[order_id]
        return S(state, Decimal(str(qty)), Decimal(str(price)), Decimal(str(qty * price)))

    def cancel(self, order_id: str) -> R:
        self.canceled.append(order_id)
        _state, qty, price = self.fills[order_id]
        self.fills[order_id] = ("CANCELED", qty, price)
        return R(True, order_id)

    def close(self) -> None:
        pass


@dataclass
class Pos:
    qty: Decimal
    last: Decimal
    avg_price: Decimal


@dataclass
class FakeReader:
    price: dict[str, float] = field(default_factory=dict)
    held: dict[str, Pos] = field(default_factory=dict)
    sell_qty: float = 0.0
    opening: float = 0.0

    def prices(self, symbols: list[str]) -> dict[str, Decimal]:
        return {s: Decimal(str(self.price[s])) for s in symbols if s in self.price}

    def usd_krw(self) -> Decimal:
        return Decimal("1400")

    def holdings(self) -> dict[str, Pos]:
        return dict(self.held)

    def buying_power(self, currency: str) -> Decimal:
        return Decimal("5000000") if currency == "KRW" else Decimal("3000")

    def daily_closes(self, symbol: str, count: int, *, before_day: str) -> list[tuple[str, Any]]:
        end = date.fromisoformat(before_day)
        base = 100.0 if symbol == "SPY" else 120.0
        return [
            ((end - timedelta(days=count - k)).isoformat(), Decimal(str(round(base + k * 0.3, 2))))
            for k in range(count)
        ]

    def checked_regular_session(self, day: str, *, kr: bool) -> tuple[str, str] | None:
        when = date.fromisoformat(day)
        if when.weekday() >= 5:
            return None
        if kr:
            return (f"{day}T09:00:00+09:00", f"{day}T15:30:00+09:00")
        nxt = (when + timedelta(days=1)).isoformat()
        return (f"{day}T22:30:00+09:00", f"{nxt}T05:00:00+09:00")

    def sellable(self, symbol: str) -> Decimal:
        return Decimal(str(self.sell_qty))

    def regular_open(self, symbol: str, start_ms: int) -> Decimal | None:
        return Decimal(str(self.opening)) if self.opening else None

    def close(self) -> None:
        pass


# ------------------------------------------------------------------ 1. 비중 추천


def _rising(n: int, start: float, step: float) -> list[float]:
    return [round(start + k * step, 2) for k in range(n)]


def _risk(days: int | None = 8, when: str | None = "") -> EntryEvents:
    return EntryEvents((), ("✅ 거시 일정 없음",), date(2026, 10, 15), when, days)


def test_golden_position_size(tmp_path: Path) -> None:
    base = {
        "symbol": "NVDA",
        "name": "엔비디아",
        "price": 180.0,
        "krw_cash": 5_000_000.0,
        "usd_cash": 3_000.0,
        "fx": 1_400.0,
        "closes": _rising(210, 120.0, 0.3),
        "index_name": "S&P500(SPY)",
        "index_closes": _rising(210, 100.0, 0.3),
        "event_risk": _risk(),
    }
    cases: dict[str, dict[str, Any]] = {
        "기본": {},
        "지수 200일선 아래": {"index_closes": _rising(210, 200.0, -0.3)},
        "실적 코앞": {"event_risk": _risk(2, "2026-10-17"), "earnings_in_days": 2},
        "실적 날짜 있음": {"earnings_in_days": 6, "earnings_date": "2026-10-23"},
        "실적 미확인": {"earnings_in_days": None},
        "추세 아님": {"closes": _rising(210, 200.0, -0.3)},
        "일봉 모자람": {"closes": _rising(50, 120.0, 0.3)},
        "자동 전략 종목": {"blocked_symbol": "Deneb"},
        "달러 모자람": {"usd_cash": 300.0},
        "이미 보유": {"held_qty": 2.0},
        "Deneb 이월 달러": {"deneb_usd": 2_500.0},
        "발표 위험 미확인": {"event_risk": None},
        "미국 주식 비중 큼": {"us_stock_krw": 20_000_000.0},
        "국내": {
            "symbol": "005930",
            "name": "삼성전자",
            "price": 71_234.0,
            "closes": _rising(210, 50_000.0, 100.0),
            "index_name": "KOSPI200(KODEX 200)",
        },
        "국내 돈 없음": {
            "symbol": "005930",
            "name": "삼성전자",
            "price": 71_234.0,
            "krw_cash": 0.0,
        },
    }
    out: list[str] = []
    for title, change in cases.items():
        values = {**base, "earnings_in_days": 8, "earnings_date": ""}
        values.update(change)
        risk = values["event_risk"]
        if risk is not None:
            values["event_risk"] = EntryEvents(
                risk.blocked,
                risk.lines,
                risk.entry_day,
                values["earnings_date"],
                values["earnings_in_days"],
            )
        advice = ps.recommend(ps.Inputs(**values))
        out += [f"## {title}", _dump(asdict(advice)), ps.advice_text(advice)]
        out += [ps.qty_prompt_text(advice)]
        for qty in (1, advice.qty + 2):
            out.append(ps.confirm_text(advice, qty))
    for price in (
        0.5,
        1.234,
        1_234.567,
        1_999.0,
        4_999.0,
        19_999.0,
        49_999.0,
        199_999.0,
        600_000.0,
    ):
        out.append(
            f"{price} → {ps.round_price(price, True, up=True)}"
            f" · {ps.round_price(price, False, up=False)}"
            f" · 지정가 {ps.buy_limit(price, False)} · 손절 {ps.stop_price(price, True)}"
        )
    out.append(str(ps.trend_check(_rising(121, 10.0, 0.1))))
    out.append(str(ps.base_krw(1_000.0, 50.0, 1_400.0, 80.0)))
    _check_golden("stock_position_size", "\n".join(out), tmp_path)


# ------------------------------------------------------------------ 2. 예약 매수 단계


def _ticket(**over: Any) -> so.Ticket:
    values: dict[str, Any] = {
        "id": "1015220000",
        "symbol": "NVDA",
        "name": "엔비디아",
        "currency": "USD",
        "qty": 10,
        "ref_price": 100.0,
        "created_ms": 0,
    }
    values.update(over)
    return so.Ticket(**values)


def test_golden_size_order(tmp_path: Path) -> None:
    out: list[str] = []

    def show(title: str, ticket: so.Ticket, msgs: list[str], trader: FakeTrader) -> None:
        out.extend([f"## {title}", _dump(msgs), _dump(ticket.to_dict()), _dump(trader.placed)])
        out.append(_dump(trader.canceled))

    # 정상: 장 밖 → 장 시작 → 체결
    ticket, trader = _ticket(), FakeTrader(ask=100.0)
    msgs = so.step_buy(ticket, trader, 1, session="US:2026-10-15", live=False)
    msgs += so.step_buy(ticket, trader, 2, session="US:2026-10-15", live=True, regular_open=100.0)
    trader.fills["o0"] = ("FILLED", 10.0, 100.2)
    msgs += so.step_buy(ticket, trader, 3, session="US:2026-10-15", live=True)
    show("정상 체결", ticket, msgs, trader)
    # 시가 갭 상승 · 추천 뒤 달아남 · 시가 미확인 · 호가 없음
    for title, opening, ask in (
        ("시가 갭 +4%", 104.0, 104.0),
        ("호가 달아남", 100.0, 103.5),
        ("시가 미확인", None, 100.0),
        ("호가 없음", 100.0, 0.0),
    ):
        ticket, trader = _ticket(), FakeTrader(ask=ask)
        msgs = so.step_buy(ticket, trader, 2, session="S", live=True, regular_open=opening)
        show(title, ticket, msgs, trader)
    # 2 분 안 덜 차면 다시 — 3 번 뒤 부분 체결
    ticket, trader = _ticket(), FakeTrader(ask=100.0)
    msgs = so.step_buy(ticket, trader, 0, session="S", live=True, regular_open=100.0)
    for k in range(3):
        oid = f"o{k}"
        trader.fills[oid] = ("PENDING", 2.0, 100.1)
        msgs += so.step_buy(ticket, trader, (k + 1) * so.REPRICE_MS, session="S", live=True)
        trader.fills[oid] = ("CANCELED", 2.0, 100.1)
        msgs += so.step_buy(ticket, trader, (k + 1) * so.REPRICE_MS + 1, session="S", live=True)
    show("세 번 뒤 부분 체결", ticket, msgs, trader)
    # 예약한 장이 지남 · 막힘 · 결과 모름 · 거절 · 주문 막힘
    ticket, trader = _ticket(session="US:2026-10-14"), FakeTrader()
    show("만료", ticket, so.step_buy(ticket, trader, 5, session="US:2026-10-15", live=True), trader)
    ticket, trader = _ticket(), FakeTrader()
    msgs = so.step_buy(ticket, trader, 5, session="S", live=True, block="실적 발표 2 거래일 뒤")
    show("막힘", ticket, msgs, trader)
    for title, fail, reject in (
        ("결과 모름", OrderStateUnknownError("시간 초과"), ""),
        ("주문 막힘", AutoTradingError("토스 주문 꺼짐"), ""),
        ("거절", None, "E123"),
    ):
        ticket, trader = _ticket(), FakeTrader(fail=fail, reject=reject)
        msgs = so.step_buy(ticket, trader, 5, session="S", live=True, regular_open=100.0)
        show(title, ticket, msgs, trader)
    # 매수 중 막힘 → 남은 주문 취소 확인 → 부분 체결로 끝
    ticket, trader = _ticket(), FakeTrader()
    msgs = so.step_buy(ticket, trader, 0, session="S", live=True, regular_open=100.0)
    trader.fills["o0"] = ("PARTIAL_FILLED", 4.0, 100.1)
    msgs += so.step_buy(ticket, trader, 10, session="S", live=True, block="일정 바뀜")
    msgs += so.step_buy(ticket, trader, 20, session="S", live=True)
    show("매수 중 막힘", ticket, msgs, trader)
    # 상태 모름은 새 주문 금지
    ticket, trader = _ticket(), FakeTrader()
    so.step_buy(ticket, trader, 0, session="S", live=True, regular_open=100.0)
    trader.fills["o0"] = ("WEIRD", 0.0, 0.0)
    try:
        so.step_buy(ticket, trader, 10, session="S", live=True)
    except OrderStateUnknownError as exc:
        out.append(f"상태 모름: {exc}")
    # PEAD — 추격 방지 없이 시가에, 손절 없음
    ticket, trader = _ticket(kind=so.PEAD), FakeTrader(ask=110.0)
    msgs = so.step_buy(ticket, trader, 0, session="S", live=True, regular_open=109.0)
    trader.fills["o0"] = ("FILLED", 10.0, 109.5)
    msgs += so.step_buy(ticket, trader, 10, session="S", live=True)
    show("PEAD", ticket, msgs, trader)
    # 국내 · 추가 매수 합치기
    parent = _ticket(symbol="005930", name="삼성전자", currency="KRW", qty=5, ref_price=70_000.0)
    parent.status, parent.filled, parent.avg = "holding", 5.0, 70_000.0
    parent.stop = ps.stop_price(70_000.0, True)
    add, trader = (
        _ticket(
            id="1016100000",
            symbol="005930",
            name="삼성전자",
            currency="KRW",
            qty=4,
            ref_price=72_000.0,
        ),
        FakeTrader(ask=72_100.0),
    )
    add.add_to = parent.id
    msgs = so.step_buy(add, trader, 0, session="KR", live=True, regular_open=72_000.0)
    trader.fills["o0"] = ("FILLED", 3.0, 72_050.0)
    trader_msgs = so.step_buy(add, trader, 10, session="KR", live=False)
    msgs += trader_msgs + so.merge_into(parent, add, 20)
    show("국내 추가 매수", add, msgs, trader)
    out.append(_dump(parent.to_dict()))
    out.append(_dump(so.Ticket.from_dict(json.loads(json.dumps(parent.to_dict()))).to_dict()))
    _check_golden("stock_size_order", "\n".join(out), tmp_path)


# ------------------------------------------------------------------ 3. 손절 감시


def test_golden_stop_guard(tmp_path: Path) -> None:
    out: list[str] = []

    def show(title: str, ticket: so.Ticket, msgs: list[str], trader: FakeTrader) -> None:
        out.extend([f"## {title}", _dump(msgs), _dump(ticket.to_dict()), _dump(trader.placed)])

    app = sg.register_app("1016000000", "NVDA", "엔비디아", 3.7, 180.0, 5)
    out.append(_dump(app.to_dict()))
    for last in (None, 0.0, 175.0, 174.6, 170.0):
        probe = sg.register_app("1", "NVDA", "엔비디아", 3, 180.0, 5)
        sg.check_stop(probe, last)
        out.append(f"가격 {last} → {probe.status} {probe.note}")
    # 손절: 팔 수 있는 수량만큼, 2 분 뒤 덜 차면 다시, 다 팔면 끝
    ticket, trader = sg.register_app("2", "NVDA", "엔비디아", 5, 180.0, 0), FakeTrader(bid=174.0)
    sg.check_stop(ticket, 174.0)
    msgs = sg.step_sell(ticket, trader, 1, live=True, sellable=4, fx=1_400.0)
    trader.fills["o0"] = ("PENDING", 2.0, 174.0)
    msgs += sg.step_sell(ticket, trader, 1 + so.REPRICE_MS, live=True, sellable=4, fx=1_400.0)
    trader.fills["o0"] = ("CANCELED", 2.0, 174.0)
    msgs += sg.step_sell(ticket, trader, 2 + so.REPRICE_MS, live=True, sellable=3, fx=1_400.0)
    trader.fills["o1"] = ("FILLED", 3.0, 173.5)
    msgs += sg.step_sell(ticket, trader, 3 + so.REPRICE_MS, live=True, sellable=0, fx=1_400.0)
    show("손절 미국", ticket, msgs, trader)
    # 국내 손절 · 매도 신호(월간 추천) · PEAD 보유 끝
    kr, trader = (
        sg.register_app("3", "005930", "삼성전자", 10, 70_000.0, 0),
        FakeTrader(bid=67_800.0),
    )
    sg.check_stop(kr, 67_800.0)
    msgs = sg.step_sell(kr, trader, 1, live=True, sellable=10)
    trader.fills["o0"] = ("FILLED", 10.0, 67_800.0)
    msgs += sg.step_sell(kr, trader, 2, live=True, sellable=0)
    show("손절 국내", kr, msgs, trader)
    signal, trader = sg.register_app("4", "MU", "마이크론", 4, 100.0, 0), FakeTrader(bid=120.0)
    signal.status, signal.note = "selling", "매도 신호 — 추세 꺾임"
    msgs = sg.step_sell(signal, trader, 1, live=True, sellable=4, fx=1_400.0)
    trader.fills["o0"] = ("FILLED", 4.0, 120.0)
    msgs += sg.step_sell(signal, trader, 2, live=True, sellable=0, fx=1_400.0)
    show("매도 신호", signal, msgs, trader)
    pead, trader = sg.register_app("5", "AAA", "에이", 6, 15.0, 0), FakeTrader(bid=16.2)
    pead.kind = so.PEAD
    for k in range(so.PEAD_HOLD):
        sg.check_hold_exit(pead, f"US:{k}", sell_live=True, closing=False)
    sg.check_hold_exit(pead, "US:last", sell_live=True, closing=True)
    out.append(f"PEAD 보유 {len(pead.hold_days)} · {pead.status} · {pead.note}")
    msgs = sg.step_sell(pead, trader, 1, live=True, sellable=6, fx=1_400.0)
    trader.fills["o0"] = ("FILLED", 6.0, 16.2)
    msgs += sg.step_sell(pead, trader, 2, live=True, sellable=0, fx=1_400.0)
    show("PEAD 매도", pead, msgs, trader)
    # 장 닫힘 · 팔 수량 0 · 호가 없음 · 12 번 실패 · 결과 모름 · 거절
    for title, live, sellable, bid, fail, reject, sells in (
        ("장 닫힘", False, 5, 170.0, None, "", 0),
        ("팔 수량 0", True, 0, 170.0, None, "", 0),
        ("호가 없음", True, 5, 0.0, None, "", 0),
        ("12 번 실패", True, 5, 170.0, None, "", 12),
        ("결과 모름", True, 5, 170.0, OrderStateUnknownError("시간 초과"), "", 0),
        ("주문 막힘", True, 5, 170.0, AutoTradingError("꺼짐"), "", 0),
        ("거절", True, 5, 170.0, None, "E9", 0),
    ):
        ticket = sg.register_app("6", "NVDA", "엔비디아", 5, 180.0, 0)
        ticket.status = "selling"
        ticket.sells = [
            so.Order("", f"c{k}", "SELL", 1, 170.0, 0, closed=True, status="E")
            for k in range(sells)
        ]
        trader = FakeTrader(bid=bid, fail=fail, reject=reject)
        show(title, ticket, sg.step_sell(ticket, trader, 9, live=live, sellable=sellable), trader)
    # 현황 줄
    reserved, buying = (
        so.Ticket("7", "AMD", "AMD", "USD", 3, 150.0, 0),
        so.Ticket("8", "TSLA", "테슬라", "USD", 2, 250.0, 0, status="buying"),
    )
    holding = sg.register_app("9", "NVDA", "엔비디아", 5, 180.0, 0)
    selling = sg.register_app("10", "005930", "삼성전자", 10, 70_000.0, 0)
    selling.status = "selling"
    pead_hold = sg.register_app("11", "AAA", "에이", 6, 15.0, 0)
    pead_hold.kind, pead_hold.hold_days = so.PEAD, ["US:1", "US:2"]
    pead_sell = sg.register_app("12", "BBB", "비", 3, 20.0, 0)
    pead_sell.kind, pead_sell.status = so.PEAD, "selling"
    closed = sg.register_app("13", "CCC", "씨", 1, 10.0, 0)
    closed.status = "closed"
    tickets = [reserved, buying, holding, selling, pead_hold, pead_sell, closed]
    out += sg.status_lines(tickets, {"NVDA": 185.0, "AAA": 16.0})
    out += sg.status_lines(tickets, {})
    out += sg.status_lines([closed], {})
    _check_golden("stock_stop_guard", "\n".join(out), tmp_path)


# ------------------------------------------------------------------ 4. 개별주 실행기 흐름


@pytest.fixture
def guard(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    from scripts import size_guard as m

    data = tmp_path / "size_guard"
    toss = tmp_path / "toss"
    toss.mkdir()
    (toss / "DENEB.json").write_text(json.dumps({"cash_usd": 0.0}), encoding="utf-8")
    (toss / "BTQ.json").write_text("{}", encoding="utf-8")
    for name, value in {
        "REPO": tmp_path,
        "DATA": data,
        "STATE": data / "state.json",
        "JOURNAL": data / "journal.jsonl",
        "REVIEW": data / "review.json",
        "LOCK": data / "lock",
        "OUTBOX": tmp_path / "outbox.log",
        "HALT": tmp_path / "HALT",
        "TOSS_DATA": toss,
        "PEAD_OFFERS": data / "pead_offers.json",
    }.items():
        monkeypatch.setattr(m, name, value)
    clock = {"now": _ms("2026-10-15T20:00:00")}
    reader = FakeReader(price={"NVDA": 180.0, "SPY": 600.0}, opening=180.5)
    trader = FakeTrader(ask=180.6, bid=180.4)
    monkeypatch.setattr(m, "_now_ms", lambda: clock["now"])
    monkeypatch.setattr(m, "_reader", lambda: reader)
    monkeypatch.setattr(m, "_trader", lambda tickets, last: trader)
    monkeypatch.setattr(m, "event_risk", lambda r, s, now, force=False: _risk())
    names = {"NVDA": "엔비디아", "005930": "삼성전자", "AAA": "AAA"}
    monkeypatch.setattr(m, "resolve", lambda q: (q.upper(), names.get(q.upper(), q.upper())))
    return {"m": m, "clock": clock, "reader": reader, "trader": trader, "tmp": tmp_path}


def test_golden_size_guard_flow(guard: dict[str, Any], tmp_path: Path) -> None:
    m, clock, reader, trader = guard["m"], guard["clock"], guard["reader"], guard["trader"]
    out: list[str] = []

    def at(text: str) -> int:
        clock["now"] = _ms(text)
        return clock["now"]

    def state(title: str, result: Any) -> None:
        out.extend([f"## {title}", _dump(result)])
        out.append(_dump([t.to_dict() for t in m.load_tickets()]))
        out.append(_dump(trader.placed))

    def fill_last(price: float) -> None:
        order = trader.placed[-1]
        trader.fills[order["id"]] = ("FILLED", float(order["qty"]), price)

    out.append(_dump(m._blocked()))
    out.append(str(m.session(reader, False, _ms("2026-10-15T22:31:00"))))
    out.append(str(m.session(reader, True, _ms("2026-10-16T15:20:00"))))
    out.append(str(m._session_days(reader, False, _ms("2026-10-16T20:00:00"))))
    out.append(_dump([m.pead_qty(price) for price in (0, 30.0, 120.0, 200.0)]))
    # 장 밖 예약 → 장 시작 매수 → 체결
    state("예약", m.reserve("NVDA"))
    at("2026-10-15T22:31:30")
    state("장 시작", m.tick(send=False))
    fill_last(180.55)
    at("2026-10-15T22:34:00")
    state("체결", m.tick(send=False))
    # 장중 추가 매수(추천대로) → 체결 → 합치기
    at("2026-10-15T22:40:00")
    state("추가 예약", m.reserve("NVDA"))
    fill_last(181.0)
    at("2026-10-15T22:43:00")
    state("추가 체결", m.tick(send=False))
    # 사람이 정한 수량으로 또 추가 → 매수 중에 또 누르면 막힘 → 체결 · 합치기
    state("1주 추가", m.reserve("NVDA", 1))
    state("이미 매수 중", m.reserve("NVDA"))
    fill_last(180.9)
    at("2026-10-15T22:46:00")
    state("1주 체결", m.tick(send=False))
    # 손절
    reader.price["NVDA"] = 174.0
    reader.sell_qty = 7.0
    trader.bid = 174.0
    at("2026-10-15T23:00:00")
    state("손절 시작", m.tick(send=False))
    fill_last(174.0)
    at("2026-10-15T23:03:00")
    state("손절 끝", m.tick(send=False))
    state("할 일 없음", m.tick(send=False))
    # 규칙 밖 수량 · 정지 중
    reader.price["NVDA"] = 180.0
    state("수량 너무 많음", m.reserve("NVDA", 999))
    (tmp_path / "HALT").write_text("시험 정지", encoding="utf-8")
    state("정지 중", m.reserve("NVDA"))
    (tmp_path / "HALT").unlink()
    # 앱에서 산 국내 종목에 손절만
    reader.held["005930"] = Pos(Decimal(10), Decimal("70500"), Decimal("70000"))
    reader.price["005930"] = 70_500.0
    state("앱 종목 손절 등록", m.attach("005930"))
    state("두 번 등록", m.attach("005930"))
    state("없는 종목", m.attach("AAA"))
    out.append(m.status())
    out += m.summary_lines()
    out.append(m._set_status("nope", ("holding",), "released"))
    tid = next(t.id for t in m.load_tickets() if t.symbol == "005930")
    out.append(m._set_status(tid, ("reserved",), "canceled"))
    out.append(m._set_status(tid, ("holding",), "released"))
    out.append(m.status())
    # PEAD 알림 → 버튼 → 예약
    sigs = [
        {"sym": "AAA", "r_day": "2026-10-15", "gap": 0.14, "r_close": 22.0},
        {"sym": "BBB", "r_day": "2026-10-15", "gap": 0.06, "r_close": 10.0},
        {"sym": "CCC", "r_day": "2026-10-15", "gap": 0.2, "r_close": 300.0},
        {"sym": "BTQ", "r_day": "2026-10-15", "gap": 0.3, "r_close": 2.5},
    ]
    at("2026-10-16T08:00:00")
    out += m.offer_pead(sigs, send=False)
    out += m.offer_pead(sigs, send=False)  # 같은 신호는 한 번만
    offers = m._load_offers()
    out.append(_dump(offers))
    nonce = {o["sym"]: k for k, o in offers.items()}
    out.append(m.reserve_pead(nonce["CCC"]))
    out.append(m.reserve_pead(nonce["BTQ"]))
    out.append(m.skip_pead("nope"))
    state("PEAD 예약", m.reserve_pead(nonce["AAA"]))
    out.append(m.reserve_pead(nonce["AAA"]))
    reader.price["AAA"] = 22.5
    trader.ask = 22.6
    at("2026-10-16T22:31:30")
    state("PEAD 장 시작", m.tick(send=False))
    fill_last(22.55)
    at("2026-10-16T22:34:00")
    state("PEAD 체결", m.tick(send=False))
    out.append(m.skip_pead(nonce["CCC"]))
    at("2026-10-19T08:00:00")
    out.append(m.reserve_pead(nonce["BTQ"]))
    out.append(str(m.load_journal()))
    out.append(m.review("2026-10"))
    _check_golden("stock_size_guard", "\n".join(out), tmp_path)


# ------------------------------------------------------------------ 5. 매일 기록 · 파이어 보고


def _write(root: Path, rel: str, data: object) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def test_golden_snapshot_and_fire(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:

    from auto_trading.ui import live_home
    from auto_trading.ui.live_home import Quotes
    from scripts import fire_report, live_snapshot

    root = tmp_path / "repo"
    monkeypatch.setattr(live_home, "SHOW_UPBIT", True)
    quotes = Quotes(
        {"BTC": 100_000_000.0}, {"TQQQ": 50.0, "SCHD": 27.0, "BTQ": 2.5}, 1_400.0, 7, ()
    )

    def fake_quotes(coins: Any, tickers: Any) -> Quotes:
        return quotes

    for module in (live_snapshot, fire_report):
        monkeypatch.setattr(module, "fetch_quotes", fake_quotes)
    out: list[str] = [str(live_snapshot.snapshot_row(root, 1_792_000_000_000))]
    _write(
        root,
        "data/toss/DENEB.json",
        {
            "cash_usd": 12.5,
            "qty": {"TQQQ": 2, "SCHD": 3},
            "deposits": [["2026-10-01", 300000, 214.3, 1400.0]],
            "orders": [
                {
                    "symbol": "TQQQ",
                    "side": "BUY",
                    "filled_qty": 2,
                    "filled_usd": 96.0,
                    "date": "2026-10-02",
                }
            ],
        },
    )
    _write(
        root,
        "data/upbit_t2/state.json",
        {"expected": {"KRW": "100000", "BTC": "0.001"}, "deposits": [["2026-10-05", "200000"]]},
    )
    _write(
        root,
        "data/toss/BTQ.json",
        {
            "symbol": "BTQ",
            "baseline_qty": 0,
            "last_qty": 4,
            "buys": [{"date": "2026-10-10", "qty": 4, "cost_usd": 9.6, "fx_rate": 1390.0}],
        },
    )
    _write(root, "data/toss/KR_005380.json", {"buys": [{"date": "2026-10-12", "cost_krw": 25000}]})
    row = live_snapshot.snapshot_row(root, 1_792_000_000_000)
    out.append(_dump(row))
    assert row is not None
    path = live_snapshot.write(root, row)
    live_snapshot.write(root, {**row, "value": 1})
    out.append(path.read_text(encoding="utf-8"))
    monkeypatch.setattr(live_snapshot, "REPO", root)
    out.append(str(live_snapshot.main(["show"])))
    # 파이어 — 업비트 입금 · 옛 바이낸스 장부가 있을 때
    out.append(_dump(fire_report.dated_deposits(root)))
    text, now = fire_report.build(root, date(2026, 11, 1))
    out += [text, _dump(asdict(now))]
    _write(
        root,
        "data/vega/state.json",
        {"deposits": [["2026-10-15", "100"]], "altair": {"transfers": [["2026-10-16", "50"]]}},
    )
    out.append(_dump(fire_report.dated_deposits(root)))
    _write(root, "data/fire/state.json", {"deposits": 100000.0, "value": 90000.0})
    out.append(fire_report.build(root, date(2026, 11, 1))[0])
    monkeypatch.setattr(fire_report, "_today", lambda: date(2026, 11, 1))
    monkeypatch.setattr(fire_report, "load_telegram_credentials", lambda path: None)
    out.append(str(fire_report.run(root)))
    saved = json.loads((root / "data/fire/state.json").read_text(encoding="utf-8"))
    saved.pop("at_ms")
    out.append(_dump(saved))
    _check_golden("stock_snapshot_fire", "\n".join(out), tmp_path)
