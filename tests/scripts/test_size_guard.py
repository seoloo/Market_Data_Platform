from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from auto_trading.ops import user_settings
from auto_trading.ops.size_order import Ticket
from scripts import size_guard as m

KST = ZoneInfo("Asia/Seoul")


def _ms(s: str) -> int:
    return int(datetime.fromisoformat(s).replace(tzinfo=KST).timestamp() * 1000)


class Reader:
    def __init__(self, kr_open: bool = True) -> None:
        self.kr_open = kr_open

    def kr_open_today(self, day: str) -> bool:
        return self.kr_open

    def us_regular_session(self, us_date: str) -> tuple[str, str] | None:
        if us_date == "2026-10-17":  # 토요일
            return None
        return (f"{us_date}T22:30:00+09:00", f"{_next(us_date)}T05:00:00+09:00")

    def checked_regular_session(self, day: str, *, kr: bool) -> tuple[str, str] | None:
        if date.fromisoformat(day).weekday() >= 5:
            return None
        if kr:
            return (f"{day}T09:00:00+09:00", f"{day}T15:30:00+09:00") if self.kr_open else None
        return self.us_regular_session(day)


def _next(d: str) -> str:
    return date.fromordinal(date.fromisoformat(d).toordinal() + 1).isoformat()


def test_us_session_window() -> None:
    r = Reader()
    assert m.session(r, False, _ms("2026-10-15T22:30:30")) == ("US:2026-10-15", False, False)
    assert m.session(r, False, _ms("2026-10-15T22:31:00")) == ("US:2026-10-15", True, True)
    assert m.session(r, False, _ms("2026-10-16T04:56:00")) == ("US:2026-10-15", False, True)
    assert m.session(r, False, _ms("2026-10-16T04:59:00")) == ("US:2026-10-15", False, False)


def test_kr_session_window() -> None:
    r = Reader()
    assert m.session(r, True, _ms("2026-10-16T09:00:30"))[1:] == (False, False)
    assert m.session(r, True, _ms("2026-10-16T09:01:00")) == ("KR:2026-10-16", True, True)
    assert m.session(r, True, _ms("2026-10-16T15:16:00"))[1:] == (False, True)
    assert m.session(Reader(kr_open=False), True, _ms("2026-10-16T10:00:00"))[1:] == (
        False,
        False,
    )


def test_session_days_skip_weekend() -> None:
    assert m._session_days(Reader(), False, _ms("2026-10-16T20:00:00")) == [  # type: ignore[arg-type]
        date(2026, 10, 16),
        date(2026, 10, 19),
        date(2026, 10, 20),
        date(2026, 10, 21),
    ]


def test_blocked_symbols_from_toss_ledgers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("DENEB", "BTQ", "KR_005380", "universe"):
        (tmp_path / f"{name}.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(m, "TOSS_DATA", tmp_path)
    b = m._blocked()
    assert b["TQQQ"].startswith("Deneb") and b["SCHD"] == "Deneb"
    assert b["BTQ"] == "자동 적립" and b["005380"] == "자동 적립"
    assert "DENEB" not in b and "universe" not in b


def test_tick_without_active_tickets_does_not_call_toss(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(m, "DATA", tmp_path)
    monkeypatch.setattr(m, "STATE", tmp_path / "state.json")

    def boom() -> None:
        raise AssertionError("토스를 부르면 안 됨")

    monkeypatch.setattr(m, "_reader", boom)
    assert m.tick(send=False) == []
    t = Ticket("1", "NVDA", "NVDA", "USD", 1, 1.0, 0, status="closed")
    m.save_tickets([t])
    assert m.tick(send=False) == []
    assert m.load_tickets() == [t]


def test_reserve_with_qty_overrides_soft_rules_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from auto_trading.ops.position_size import Advice

    def advice(blocked: list[str], hard: list[str], max_qty: int) -> Advice:
        return Advice(
            symbol="005930",
            name="삼성전자",
            currency="KRW",
            price=214_000.0,
            base_krw=16_000_000.0,
            weight=0.12,
            limit=215_000.0,
            qty=9,
            amount_krw=1_935_000.0,
            stop=208_500.0,
            risk_krw=58_000.0,
            blocked=blocked,
            hard=hard,
            max_qty=max_qty,
        )

    monkeypatch.setattr(m, "DATA", tmp_path)
    monkeypatch.setattr(m, "STATE", tmp_path / "state.json")
    monkeypatch.setattr(m, "LOCK", tmp_path / "lock")
    monkeypatch.setattr(m, "halted_for", lambda *_: None)
    monkeypatch.setattr(m, "tick", lambda now=None, **_: [])
    soft = advice(["추세 아님"], [], 19)
    monkeypatch.setattr(m, "advise", lambda q: soft)
    assert "1 ~ 19주만" in m.reserve("삼성전자", qty=20)
    assert m.load_tickets() == []
    out = m.reserve("삼성전자", qty=5)
    t = m.load_tickets()[0]
    assert (t.qty, t.override) == (5, True) and "규칙 밖 매수" in t.note
    assert "5주 예약했어요" in out and "규칙 밖" in out
    assert "비추천" in m.reserve("삼성전자")  # 수량 없이(추천대로)는 규칙에 걸리면 안 삼
    monkeypatch.setattr(m, "advise", lambda q: advice(["Deneb 종목"], ["Deneb 종목"], 0))
    assert "예약했어요" not in m.reserve("TQQQ", qty=1)


def test_summary_lines_for_status_report(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(m, "DATA", tmp_path)
    monkeypatch.setattr(m, "STATE", tmp_path / "state.json")
    assert m.summary_lines() == []  # 장부 비면 현황에 칸 없음

    class R:
        def prices(self, syms: list[str]) -> dict[str, float]:
            return {"NVDA": 103.0}

        def close(self) -> None:
            pass

    held = Ticket("1", "NVDA", "엔비디아", "USD", 10, 100.0, 0, status="holding")
    held.filled, held.avg, held.stop = 10.0, 100.0, 97.0
    m.save_tickets([held, Ticket("2", "TSLA", "테슬라", "USD", 3, 200.0, 0)])
    monkeypatch.setattr(m, "_reader", R)
    lines = m.summary_lines()
    assert lines[0] == "💹 개별주" and "엔비디아 10주" in lines[1] and "+3.0%" in lines[1]
    assert "장 시작에 매수 테슬라 3주" in lines[2]

    def boom() -> None:
        raise OSError("down")

    monkeypatch.setattr(m, "_reader", boom)
    assert "엔비디아 10주" in m.summary_lines()[1]  # 시세 없어도 칸은 보임


def test_reserve_on_held_symbol_is_add_on_and_pending_buy_blocks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from auto_trading.ops.position_size import Advice

    a = Advice(
        symbol="CRCL",
        name="써클",
        currency="USD",
        price=87.0,
        base_krw=16_000_000.0,
        weight=0.25,
        limit=87.5,
        qty=21,
        amount_krw=2_600_000.0,
        stop=84.8,
        risk_krw=50_000.0,
        max_qty=21,
    )
    monkeypatch.setattr(m, "DATA", tmp_path)
    monkeypatch.setattr(m, "STATE", tmp_path / "state.json")
    monkeypatch.setattr(m, "LOCK", tmp_path / "lock")
    monkeypatch.setattr(m, "halted_for", lambda *_: None)
    monkeypatch.setattr(m, "tick", lambda now=None, **_: [])
    monkeypatch.setattr(m, "advise", lambda q: a)
    held = Ticket("1", "CRCL", "써클", "USD", 10, 84.83, 0, status="holding")
    held.filled, held.avg, held.stop = 10.0, 86.226, 83.63
    m.save_tickets([held])
    out = m.reserve("CRCL")
    add = m.load_tickets()[1]
    assert (add.add_to, add.qty, add.status) == ("1", 21, "reserved")
    assert "추가 매수" in out and "기존 10주와 합쳐" in out
    out2 = m.reserve("CRCL")  # 추가 매수가 아직 안 끝났으면 또 예약하지 않음
    assert "이미 매수 중" in out2 and len(m.load_tickets()) == 2


def test_pead_qty() -> None:
    assert m.pead_qty(30.0) == 3  # $90
    assert m.pead_qty(120.0) == 1  # $100 넘지만 $150 까지는 1 주
    assert m.pead_qty(400.0) == 0  # 알림만
    assert m.pead_qty(0.0) == 0


def _pead_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(m, "DATA", tmp_path)
    monkeypatch.setattr(m, "STATE", tmp_path / "state.json")
    monkeypatch.setattr(m, "LOCK", tmp_path / "lock")
    monkeypatch.setattr(m, "PEAD_OFFERS", tmp_path / "pead_offers.json")
    monkeypatch.setattr(m, "OUTBOX", tmp_path / "outbox.log")
    monkeypatch.setattr(m, "halted_for", lambda *_: None)
    monkeypatch.setattr(m, "tick", lambda now=None, **_: [])
    monkeypatch.setattr(m, "_blocked", dict)
    monkeypatch.setattr(m, "resolve", lambda q: (q, f"{q} Inc"))
    monkeypatch.setattr(user_settings, "get", lambda root, key: True)


def test_offer_pead_filters_gap_and_dedupes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _pead_env(tmp_path, monkeypatch)
    sigs = [
        {"sym": "ABC", "r_day": "2026-10-15", "gap": 0.12, "r_close": 30.0},
        {"sym": "LOW", "r_day": "2026-10-15", "gap": 0.07, "r_close": 30.0},  # 10% 미만
    ]
    out = m.offer_pead(sigs, _ms("2026-10-16T21:40:00"), send=False)
    assert len(out) == 1 and "ABC" in out[0] and "3주" in out[0]
    assert m.offer_pead(sigs, _ms("2026-10-16T21:45:00"), send=False) == []  # 같은 신호 한 번만
    monkeypatch.setattr(user_settings, "get", lambda root, key: key != "stock.pead")
    sigs2 = [{"sym": "NEW", "r_day": "2026-10-15", "gap": 0.2, "r_close": 10.0}]
    assert m.offer_pead(sigs2, send=False) == []  # 설정 끄면 안 보냄


def test_reserve_pead_session_and_expiry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _pead_env(tmp_path, monkeypatch)
    monkeypatch.setattr(m, "_reader", lambda: _ClosingReader())
    sigs = [{"sym": "ABC", "r_day": "2026-10-15", "gap": 0.12, "r_close": 30.0}]
    m.offer_pead(sigs, _ms("2026-10-16T21:40:00"), send=False)
    nonce = next(iter(m._load_offers()))
    msg = m.reserve_pead(nonce, _ms("2026-10-16T21:50:00"))
    assert "PEAD 예약" in msg and "2026-10-16" in msg
    t = m.load_tickets()[0]
    assert (t.kind, t.qty, t.session, t.ref_price) == ("pead", 3, "US:2026-10-16", 30.0)
    assert "지난 버튼" in m.reserve_pead(nonce, _ms("2026-10-16T21:51:00"))  # 한 번만
    # 반응일 다음 장이 끝난 뒤 누르면 사지 않음
    m.offer_pead(
        [{"sym": "LATE", "r_day": "2026-10-15", "gap": 0.15, "r_close": 20.0}],
        _ms("2026-10-16T21:40:00"),
        send=False,
    )
    late = next(k for k, o in m._load_offers().items() if o["sym"] == "LATE")
    assert "이미 끝나" in m.reserve_pead(late, _ms("2026-10-17T06:00:00"))
    assert len(m.load_tickets()) == 1


class _ClosingReader(Reader):
    def close(self) -> None:
        pass
