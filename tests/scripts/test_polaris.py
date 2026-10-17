"""🧭 Polaris 실행 — 가짜 나무(실제 호출 없음)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from auto_trading.core.errors import OrderStateUnknownError
from auto_trading.exchange.namu_global import NamuOrderResult, NamuOrderStatus, NamuQuote
from auto_trading.ops.polaris import PolarisState, Round
from scripts import polaris as p

KST = ZoneInfo("Asia/Seoul")
NIGHT = datetime(2026, 11, 3, 0, 20, tzinfo=KST)  # 뉴욕 11-02 10:20 (서머타임 끝)
ASKS = {"SPYM": 89.68, "QQQM": 292.40, "IEF": 90.71, "GLDM": 82.60}


def _q(sym: str, day: str = "20261102") -> NamuQuote:
    a = ASKS[sym]
    return NamuQuote(sym, a, a, a - 0.02, 1_400.0, [], [], a, "2", trade_date=day)


@dataclass
class FakeNamu:
    avail: float = 5_000_000.0
    fill: bool = True
    unknown: bool = False
    day: str = "20261102"
    sent: list[tuple[str, int, Decimal]] = field(default_factory=list)
    canceled: list[str] = field(default_factory=list)

    def quote(self, s: str) -> NamuQuote:
        return _q(s, self.day)

    def buyable_krw(self, symbol: str = "", price: float = 0.0) -> float:
        return self.avail

    def order(self, s: str, side: str, n: int, limit: Decimal, **_: Any) -> NamuOrderResult:
        if self.unknown:
            raise OrderStateUnknownError("timeout")
        self.sent.append((s, n, limit))
        return NamuOrderResult(True, f"20261103:{len(self.sent)}", "접수")

    def order_status(self, oid: str) -> NamuOrderStatus:
        _s, n, limit = self.sent[int(oid.split(":")[1]) - 1]
        if self.fill:
            amt = Decimal(n) * limit
            return NamuOrderStatus("FILLED", Decimal(n), limit, amt, None)
        if oid in self.canceled:
            return NamuOrderStatus("CANCELED", Decimal(0), None, None, None)
        return NamuOrderStatus("PENDING", Decimal(0), None, None, None)

    def cancel(self, oid: str, symbol: str = "") -> NamuOrderResult:
        self.canceled.append(oid)
        return NamuOrderResult(True, oid, "취소")

    def sleep(self, s: float) -> None:
        pass

    def close(self) -> None:
        pass


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeNamu:
    fake = FakeNamu()
    monkeypatch.setattr(p, "STATE", tmp_path / "state.json")
    monkeypatch.setattr(p, "OUTBOX", tmp_path / "outbox.log")
    monkeypatch.setattr(p, "HALTS", (tmp_path / "HALT",))
    monkeypatch.setattr(p, "client", lambda: fake)
    sent: list[str] = []
    monkeypatch.setattr(p, "send", lambda text, buttons=None: sent.append(text))
    fake.messages = sent  # type: ignore[attr-defined]
    return fake


def test_in_session_needs_ny_hours_and_today_quote() -> None:
    assert p.in_session(NIGHT, _q("SPYM"))
    assert not p.in_session(NIGHT, _q("SPYM", "20261030"))  # 휴장일 — 시세가 앞 거래일
    assert not p.in_session(datetime(2026, 11, 2, 23, 40, tzinfo=KST), _q("SPYM"))  # 09:40
    assert not p.in_session(datetime(2026, 11, 3, 5, 50, tzinfo=KST), _q("SPYM"))  # 15:50


def test_offer_then_buy_button_then_round(env: FakeNamu, monkeypatch: pytest.MonkeyPatch) -> None:
    morning = datetime(2026, 11, 2, 9, 0, tzinfo=KST)
    st = PolarisState()
    assert p.offer(st, datetime(2026, 10, 30, 9, 0, tzinfo=KST)) == ""  # 11 월 전
    card = p.offer(st, morning)
    assert "Polaris 1/4회차 · 11월" in card
    assert p.offer(p.load(), morning) == ""  # 같은 달 두 번 안 보냄
    nonce = p.load().rounds[0].nonce
    monkeypatch.setattr(p, "_now", lambda: morning)
    assert "오늘 밤" in p.on_buy(nonce)
    assert "지난 버튼" in p.on_buy(nonce)
    st = p.load()
    msg = p.buy_round(st, st.rounds[0], NIGHT)
    assert "매수 끝" in msg and {s for s, _, _ in env.sent} == set(ASKS)
    st = p.load()
    r = st.rounds[0]
    assert r.status == "done" and 0 < r.spent_krw <= 1_000_000
    assert sum(st.qty.values()) > 0 and all(o["sent"] == 1 for o in st.sent)


def test_not_enough_krw_waits_and_keeps_user_cash(env: FakeNamu) -> None:
    env.avail = 300_000  # 사용자가 개별주를 사서 원화가 모자람
    st = PolarisState(rounds=[Round("2026-11", 1_000_000, status="approved")])
    p.save(st)
    msg = p.buy_round(st, st.rounds[0], NIGHT)
    assert "원화로 살 수 있는 돈" in msg and env.sent == []
    assert p.load().rounds[0].status == "approved"


def test_unfilled_is_canceled_and_retried_then_ends(env: FakeNamu) -> None:
    env.fill = False
    st = PolarisState(rounds=[Round("2026-11", 1_000_000, status="approved")])
    p.save(st)
    for _ in range(3):
        st = p.load()
        msg = p.buy_round(st, st.rounds[0], NIGHT)
        assert "못 삼" in msg
    assert len(env.canceled) == len(env.sent) and p.load().rounds[0].status == "done"
    assert sum(p.load().qty.values()) == 0


def test_unknown_order_result_halts(env: FakeNamu) -> None:
    env.unknown = True
    st = PolarisState(rounds=[Round("2026-11", 1_000_000, status="approved")])
    p.save(st)
    msg = p.buy_round(st, st.rounds[0], NIGHT)
    assert "멈춤" in msg and p.load().halted
    assert p.load().sent[0]["sent"] == 0  # 보내기 전에 적어 둔 줄이 남는다


def test_outside_session_does_nothing(env: FakeNamu) -> None:
    st = PolarisState(rounds=[Round("2026-11", 1_000_000, status="approved")])
    assert p.buy_round(st, st.rounds[0], datetime(2026, 11, 2, 21, 0, tzinfo=KST)) == ""
    assert env.sent == []


def test_skip_button(env: FakeNamu) -> None:
    p.save(PolarisState(rounds=[Round("2026-11", 1_000_000, nonce="n1")]))
    assert "건너뛰었어요" in p.on_skip("n1")
    assert p.load().rounds[0].status == "skipped" and "지난 버튼" in p.on_skip("n1")
