"""토스 적립 + QS 실행 (문서 118 §7) — 가짜 토스로만. 실제 주문 없음."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from auto_trading.exchange.toss import TossOrderResult, TossOrderStatus, TossPosition
from auto_trading.ops.qndx_alert import Order, State
from auto_trading.ops.qs_live import QsLiveState


class FakeToss:
    def __init__(self, *, closes_up: bool = True, usd: str = "2.05", outcome: str = "ok") -> None:
        self.closes_up, self.usd, self.outcome = closes_up, usd, outcome
        self.sent: list[tuple[str, str, int, Decimal, str]] = []
        self.holding: dict[str, TossPosition] = {}
        self.statuses: dict[str, TossOrderStatus] = {}

        class _C:
            trading_enabled = True

        self.credentials = _C()

    def holdings(self) -> dict[str, TossPosition]:
        return self.holding

    def prices(self, symbols: list[str]) -> dict[str, Decimal]:
        table = {
            "QNDX": "25.4",
            "BTQ": "3.17",
            "WQTM": "32.5",
            "QS": "4.57",
            "TQQQ": "81.07",
            "SCHD": "33.15",
        }
        return {s: Decimal(table[s]) for s in symbols}

    def usd_krw(self) -> Decimal:
        return Decimal("1350")

    def us_commission(self) -> Decimal:
        return Decimal("0.001")

    def buying_power(self, currency: str) -> Decimal:
        return Decimal(self.usd)

    def daily_closes(self, symbol: str, count: int = 200) -> list[tuple[str, Decimal]]:
        from datetime import date, timedelta

        out = []
        d = date(2026, 10, 5)
        for k in range(150):
            px = 500 + k if self.closes_up else 700 - k
            out.append(((d - timedelta(days=149 - k)).isoformat(), Decimal(px)))
        return out

    def order_status(self, order_id: str) -> TossOrderStatus:
        return self.statuses[order_id]

    def stocks_all(self, market: str) -> list[dict[str, Any]]:
        return []

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
        self.sent.append((symbol, side, qty, limit, client_order_id))
        if self.outcome == "ok":
            return TossOrderResult(True, f"OID-{len(self.sent)}", "접수")
        return TossOrderResult(False, self.outcome, "거절")

    def close(self) -> None:
        pass


NOW_KST = datetime(2026, 10, 6, 21, 0, tzinfo=UTC)  # _kst_now 꼴 (화요일 밤 9 시)
NOW_UTC = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)


def _mod(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake: FakeToss) -> Any:
    import scripts.toss_accumulate as mod

    monkeypatch.setattr(mod, "REPO", tmp_path)  # 실제 .env(ECOS · 텔레그램 키)를 읽지 않게
    monkeypatch.setattr(mod, "DATA", tmp_path / "data")
    monkeypatch.setattr(mod, "REPORTS", tmp_path / "reports")
    monkeypatch.setattr(mod, "HALT", tmp_path / "HALT")
    monkeypatch.setattr(mod, "_trader", lambda: fake)
    monkeypatch.setattr(mod, "_kst_now", lambda: NOW_KST)
    monkeypatch.setattr(mod, "_notify", lambda text, telegram: None)
    monkeypatch.setattr(mod, "STOPPED", {})
    monkeypatch.setattr(mod, "QS_LIVE", True)
    monkeypatch.setattr(mod, "DENEB_ENABLED", False)
    monkeypatch.setattr(mod, "_dividend_events", lambda: [])
    return mod


def _seed(mod: Any, *, qndx_start: str = "2026-09-26", qs: QsLiveState | None = None) -> None:
    mod._save_plan_state(State("QNDX", qndx_start, 0.0, 0.0, 0.0))
    mod._save_plan_state(State("BTQ", "2026-10-06", 0.0, 0.0, 0.0))
    mod._save_qs(qs or QsLiveState("2026-10-05", 0.0, 0.0, 0.0, prev_on=True))


def test_signal_drops_unfinished_day(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake = FakeToss()
    mod = _mod(monkeypatch, tmp_path, fake)
    # 뉴욕 10-05 11:00 — 10-05 봉은 아직 진행 중
    on, as_of, _ = mod.qs_signal(fake, datetime(2026, 10, 5, 15, 0, tzinfo=UTC))
    assert on and as_of == "2026-10-04"
    _, as_of2, _ = mod.qs_signal(fake, datetime(2026, 10, 5, 21, 0, tzinfo=UTC))  # 뉴욕 17:00
    assert as_of2 == "2026-10-05"


def test_run_buys_qndx_with_loc_when_accrued(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = FakeToss()
    mod = _mod(monkeypatch, tmp_path, fake)
    _seed(mod)  # 9-26 부터 11 일 = 11 만 원 → 1 주 살 차례
    monkeypatch.setattr(mod, "datetime", _FixedDT)
    assert mod.run(telegram=False) == 0
    assert [(s, side, q) for s, side, q, _, _ in fake.sent] == [("QNDX", "BUY", 1)]
    assert fake.sent[0][3] == Decimal("26.16")  # 25.4 × 1.03 내림
    saved = json.loads((tmp_path / "data" / "QNDX.json").read_text(encoding="utf-8"))
    assert saved["orders"][-1]["status"] == "SENT" and saved["orders"][-1]["code"] == "OID-1"
    report = json.loads((tmp_path / "reports" / "2026-10-06.json").read_text(encoding="utf-8"))
    assert "장 마감 지정가 매수 접수" in report["message"] and "추세 🟢 켜짐" in report["message"]


def test_trend_off_pauses_qndx_and_sells_qs_tqqq(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = FakeToss(closes_up=False)
    fake.holding = {"TQQQ": TossPosition("TQQQ", Decimal(3), Decimal(80), Decimal(81), "USD")}
    mod = _mod(monkeypatch, tmp_path, fake)
    _seed(mod, qs=QsLiveState("2026-10-05", 0.0, 3.0, 240.0, prev_on=True))
    monkeypatch.setattr(mod, "datetime", _FixedDT)
    assert mod.run(telegram=False) == 0
    assert [(s, side, q) for s, side, q, _, _ in fake.sent] == [("TQQQ", "SELL", 3)]
    assert fake.sent[0][3] == Decimal("78.63")  # 81.07 × 0.97 내림
    qs = json.loads((tmp_path / "data" / "QS_SWITCH.json").read_text(encoding="utf-8"))
    assert qs["prev_on"] is False and qs["want"] == "SELL"


def test_qs_buy_short_of_dollars_is_retried_next_day(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = FakeToss(outcome="insufficient-buying-power")
    mod = _mod(monkeypatch, tmp_path, fake)
    _seed(mod, qndx_start="2026-08-01", qs=QsLiveState("2026-10-05", 0.0, 0.0, 0.0, prev_on=False))
    monkeypatch.setattr(mod, "datetime", _FixedDT)
    mod.run(telegram=False)
    qs = json.loads((tmp_path / "data" / "QS_SWITCH.json").read_text(encoding="utf-8"))
    assert [s for s, *_ in fake.sent] == ["TQQQ"]  # QNDX 는 멈춤, TQQQ 만
    assert qs["want"] == "BUY"  # 달러 부족은 다음 날 다시


def test_qs_buy_prerequisite_rejection_is_not_retried(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = FakeToss(outcome="prerequisite-required")
    mod = _mod(monkeypatch, tmp_path, fake)
    _seed(mod, qndx_start="2026-08-01", qs=QsLiveState("2026-10-05", 0.0, 0.0, 0.0, prev_on=False))
    monkeypatch.setattr(mod, "datetime", _FixedDT)
    mod.run(telegram=False)
    qs = json.loads((tmp_path / "data" / "QS_SWITCH.json").read_text(encoding="utf-8"))
    assert qs["want"] == "NONE"


def test_sell_fill_uses_exact_proceeds(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake = FakeToss(closes_up=False)
    mod = _mod(monkeypatch, tmp_path, fake)
    qs = QsLiveState("2026-10-01", 0.0, 3.0, 240.0, prev_on=False, want="SELL")
    qs.orders.append(Order("2026-10-05", 78.0, "SENT", "OID-9", "SELL x"))
    _seed(mod, qs=qs)
    fake.statuses["OID-9"] = TossOrderStatus(
        "FILLED", Decimal(3), Decimal("79"), Decimal("237"), Decimal("0.24")
    )
    monkeypatch.setattr(mod, "datetime", _FixedDT)
    mod.run(telegram=False)
    saved = json.loads((tmp_path / "data" / "QS_SWITCH.json").read_text(encoding="utf-8"))
    sell = saved["trades"][-1]
    assert sell["side"] == "SELL" and sell["usd"] == pytest.approx(236.76)
    assert sell["estimated"] is False and saved["want"] == "NONE"


def test_fx_reminder_amount(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake = FakeToss(usd="2.05")
    mod = _mod(monkeypatch, tmp_path, fake)
    sent: list[str] = []
    monkeypatch.setattr(mod, "_notify", lambda text, telegram: sent.append(text))
    monkeypatch.setattr(mod, "datetime", _FixedDT)
    _seed(mod)
    assert mod.fx(telegram=False) == 0
    assert sent and "QNDX 1주" in sent[0] and "환전" in sent[0]
    need = 26.16 * 1.001 * 1.02 - 2.05
    assert f"${int(need) + 1:,}" in sent[0]
    assert fake.sent == []  # 알림은 주문하지 않는다
    assert json.loads((tmp_path / "data" / "QNDX.json").read_text(encoding="utf-8"))["orders"] == []


def test_fx_silent_when_enough_dollars(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake = FakeToss(usd="500")
    mod = _mod(monkeypatch, tmp_path, fake)
    sent: list[str] = []
    monkeypatch.setattr(mod, "_notify", lambda text, telegram: sent.append(text))
    monkeypatch.setattr(mod, "datetime", _FixedDT)
    _seed(mod)
    mod.fx(telegram=False)
    assert sent == []


class _FixedDT(datetime):
    @classmethod
    def now(cls, tz: Any = None) -> datetime:  # type: ignore[override]
        return NOW_UTC


def test_commission_change_is_flagged(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake = FakeToss()
    monkeypatch.setattr(fake, "us_commission", lambda: Decimal("0.0025"))
    mod = _mod(monkeypatch, tmp_path, fake)
    _seed(mod)
    monkeypatch.setattr(mod, "datetime", _FixedDT)
    mod.run(telegram=False)
    report = json.loads((tmp_path / "reports" / "2026-10-06.json").read_text(encoding="utf-8"))
    assert "수수료가 0.250%" in report["message"]


def test_quantumscape_accumulates_apart_from_qs_strategy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """종목 QS(퀀텀스케이프) 적립은 QS.json, QS 전략은 QS_SWITCH.json — 섞이지 않는다."""
    fake = FakeToss()
    mod = _mod(monkeypatch, tmp_path, fake)
    _seed(mod)
    mod._save_plan_state(State("QS", "2026-10-01", 0.0, 0.0, 0.0))  # 6 일 × 1 달러 ≥ 4.57
    monkeypatch.setattr(mod, "datetime", _FixedDT)
    assert mod.run(telegram=False) == 0
    sent = [(s, side, q) for s, side, q, _, _ in fake.sent]
    assert ("QS", "BUY", 1) in sent and all(s != "TQQQ" for s, _, _ in sent)
    stock = json.loads((tmp_path / "data" / "QS.json").read_text(encoding="utf-8"))
    strategy = json.loads((tmp_path / "data" / "QS_SWITCH.json").read_text(encoding="utf-8"))
    assert stock["symbol"] == "QS" and stock["orders"][-1]["status"] == "SENT"
    assert "prev_on" in strategy and "symbol" not in strategy
    assert (tmp_path / "reports" / "QS" / "2026-10-06.json").exists()
    assert (tmp_path / "reports" / "QS_SWITCH" / "2026-10-06.json").exists()


def test_old_qs_strategy_file_is_moved_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    mod = _mod(monkeypatch, tmp_path, FakeToss())
    old = tmp_path / "data" / "QS.json"
    old.parent.mkdir(parents=True)
    body = QsLiveState("2026-10-04", 0.0, 0.0, 0.0, prev_on=None).to_dict()
    old.write_text(json.dumps(body), encoding="utf-8")
    assert mod._load_qs() is not None
    assert not old.exists() and (tmp_path / "data" / "QS_SWITCH.json").exists()
    plan = next(p for p in mod.PLANS if p.symbol == "QS")
    assert mod._load_plan_state(plan) is None  # 종목 QS 적립은 run 이 그날 새로 시작
    # 종목 QS 적립 상태(QS.json)는 전략 형식이 아니므로 옮기지 않는다
    mod._save_plan_state(State("QS", "2026-10-05", 0.0, 0.0, 0.0))
    mod._migrate_qs_switch()
    assert old.exists()


def test_stopped_plan_sends_no_order_and_no_qs_funding(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = FakeToss()
    mod = _mod(monkeypatch, tmp_path, fake)
    monkeypatch.setattr(mod, "STOPPED", {"QNDX": "시험"})
    _seed(mod)
    assert mod.run(telegram=False) == 0
    assert all(o[0] != "QNDX" for o in fake.sent)


def test_qs_live_off_sends_no_tqqq(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake = FakeToss()
    mod = _mod(monkeypatch, tmp_path, fake)
    monkeypatch.setattr(mod, "QS_LIVE", False)
    _seed(mod)
    assert mod.run(telegram=False) == 0
    assert all(o[0] != "TQQQ" for o in fake.sent)


def test_routine_run_is_quiet_but_rejection_alerts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    sent: list[str] = []
    fake = FakeToss()
    mod = _mod(monkeypatch, tmp_path, fake)
    monkeypatch.setattr(mod, "_notify", lambda text, telegram: sent.append(text))
    monkeypatch.setattr(mod, "QUIET_ROUTINE", True)
    _seed(mod)
    assert mod.run(telegram=False) == 0
    assert all(any(m in x for m in mod.ALERT_MARKS) for x in sent)


def test_deneb_first_month_buys_whole_shares_then_settles(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = FakeToss(usd="500")
    mod = _mod(monkeypatch, tmp_path, fake)
    monkeypatch.setattr(mod, "DENEB_ENABLED", True)
    monkeypatch.setattr(mod, "DENEB_START", "2026-10-06")
    _seed(mod)
    assert mod.run(telegram=False) == 0
    deneb = [o for o in fake.sent if o[4].startswith("M3")]
    # 30 만 원 / 1350 = 222.2 USD, 변동성 작음 → TQQQ 60%: TQQQ 1 주 + SCHD 3 주
    assert sorted((o[0], o[1], o[2]) for o in deneb) == [("SCHD", "BUY", 3), ("TQQQ", "BUY", 1)]
    state = json.loads((tmp_path / "data" / "DENEB.json").read_text(encoding="utf-8"))
    assert state["done_month"] == "2026-10"
    # 다음 날: 체결 반영, 같은 달 다시 사지 않음
    for o in state["orders"]:
        fake.statuses[o["order_id"]] = TossOrderStatus(
            "FILLED",
            Decimal(o["qty"]),
            Decimal("80") if o["symbol"] == "TQQQ" else Decimal("33"),
            Decimal("80") if o["symbol"] == "TQQQ" else Decimal("99"),
            Decimal("0.1"),
        )
    for k in range(1, len(fake.sent) + 1):
        fake.statuses.setdefault(
            f"OID-{k}", TossOrderStatus("FILLED", Decimal(1), Decimal(1), Decimal(1), Decimal(0))
        )
    monkeypatch.setattr(mod, "_kst_now", lambda: datetime(2026, 10, 7, 21, 0, tzinfo=UTC))
    n_sent = len(fake.sent)
    assert mod.run(telegram=False) == 0
    assert not any(o[4].startswith("M3") for o in fake.sent[n_sent:])
    state = json.loads((tmp_path / "data" / "DENEB.json").read_text(encoding="utf-8"))
    assert state["qty"] == {"TQQQ": 1.0, "SCHD": 3.0}
    assert abs(state["cash_usd"] - (300_000 / 1350 - 179 - 0.2)) < 1e-6


def test_deneb_insufficient_usd_retries_next_day(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = FakeToss(outcome="insufficient-buying-power")
    mod = _mod(monkeypatch, tmp_path, fake)
    monkeypatch.setattr(mod, "DENEB_ENABLED", True)
    monkeypatch.setattr(mod, "DENEB_START", "2026-10-06")
    _seed(mod)
    mod.run(telegram=False)
    state = json.loads((tmp_path / "data" / "DENEB.json").read_text(encoding="utf-8"))
    assert state["done_month"] == ""
    assert len(state["deposits"]) == 1


def test_deneb_telegram_amount_sets_month_and_asks_on_buy_day(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from auto_trading.notify import inbox as inbox_mod
    from auto_trading.notify.inbox import InboxMessage

    sent: list[str] = []
    fake = FakeToss(usd="2000")
    mod = _mod(monkeypatch, tmp_path, fake)
    monkeypatch.setattr(mod, "_notify", lambda text, telegram: sent.append(text))
    monkeypatch.setattr(mod, "DENEB_ENABLED", True)
    monkeypatch.setattr(mod, "DENEB_START", "2026-10-06")
    monkeypatch.setattr(mod, "load_telegram_credentials", lambda path: object())
    inbox: list[InboxMessage] = []

    class FakeInbox:
        def __init__(self, creds: object) -> None:
            pass

        def fetch(self, offset: int) -> tuple[list[InboxMessage], int]:
            out = list(inbox)
            inbox.clear()
            return out, offset + len(out)

    monkeypatch.setattr(inbox_mod, "TelegramInbox", FakeInbox)
    _seed(mod)
    # 매수 당일(10-06) 아침 09:00 환전 알림 실행: 금액을 묻는다
    monkeypatch.setattr(mod, "_kst_now", lambda: datetime(2026, 10, 6, 9, 0, tzinfo=UTC))
    assert mod.fx(telegram=True) == 0
    assert any("입금 안내" in x and "예시 답: 100만" in x and "Deneb 80" in x for x in sent)
    state = json.loads((tmp_path / "data" / "DENEB.json").read_text(encoding="utf-8"))
    assert state["ask_month"] == "2026-10"
    # 답 "50만"(세 곳 합계) → Deneb 몫 40만 · Vega 5만 · AG4S2 5만 안내 + 오늘 밤 계획 답장
    monkeypatch.setattr(mod, "_usdt_krw", lambda: 1_400.0)
    sent.clear()
    inbox.append(InboxMessage(1, 9_999_999_999, "50만"))
    monkeypatch.setattr(mod, "_kst_now", lambda: datetime(2026, 10, 6, 9, 5, tzinfo=UTC))
    assert mod.deneb_chat(telegram=True) == 0
    plan = [x for x in sent if "오늘(2026-10-06) 밤 매수 예정" in x and "비율 TQQQ" in x]
    assert plan, sent
    assert "이유: QQQ 20일 변동성" in plan[0] and "현재 보유 달러 $2,000.00 → 필요 달러" in plan[0]
    assert "📦 현재 보유 (Deneb 몫)" in plan[0] and "💰 배당" in plan[0]
    state = json.loads((tmp_path / "data" / "DENEB.json").read_text(encoding="utf-8"))
    assert state["amounts"] == {"2026-10": 400_000.0}
    split = [x for x in sent if "입금 500,000원 → 이렇게 넣어 주세요" in x]
    assert split and "Deneb · 토스 증권  400,000원" in split[0]
    assert "Vega · 바이낸스 현물  50,000원 ≈ 36 USDT" in split[0]
    assert not state["deposits"] and not fake.sent  # 답장은 계산만 — 주문 · 납입 없음
    # 밤 21:00 실행이 그 금액으로 산다
    monkeypatch.setattr(mod, "_kst_now", lambda: NOW_KST)
    assert mod.run(telegram=True) == 0
    state = json.loads((tmp_path / "data" / "DENEB.json").read_text(encoding="utf-8"))
    assert state["deposits"][0][1] == 400_000.0
    # 같은 달은 다시 묻지 않는다 · 대화 실행도 할 일 없음
    sent.clear()
    monkeypatch.setattr(mod, "_kst_now", lambda: datetime(2026, 10, 7, 9, 0, tzinfo=UTC))
    for k in range(1, len(fake.sent) + 1):
        fake.statuses.setdefault(
            f"OID-{k}", TossOrderStatus("FILLED", Decimal(1), Decimal(1), Decimal(1), Decimal(0))
        )
    assert mod.fx(telegram=True) == 0
    assert not any("입금 안내" in x for x in sent)
    assert mod.deneb_chat(telegram=True) == 0


def test_deneb_retry_command_buys_after_funds_arrive(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = FakeToss(outcome="insufficient-buying-power")
    mod = _mod(monkeypatch, tmp_path, fake)
    monkeypatch.setattr(mod, "DENEB_ENABLED", True)
    monkeypatch.setattr(mod, "DENEB_START", "2026-10-06")
    _seed(mod)
    mod.run(telegram=False)
    fake.outcome = "ok"
    # 같은 날 23:40, 그리고 자정 넘은 03:30 은 전날 일로 본다
    monkeypatch.setattr(mod, "_kst_now", lambda: datetime(2026, 10, 7, 3, 30, tzinfo=UTC))
    n = len(fake.sent)
    assert mod.deneb_only(telegram=False) == 0
    deneb = [o for o in fake.sent[n:] if o[4].startswith("M3")]
    assert sorted((o[0], o[2]) for o in deneb) == [("SCHD", 3), ("TQQQ", 1)]
    state = json.loads((tmp_path / "data" / "DENEB.json").read_text(encoding="utf-8"))
    assert state["done_month"] == "2026-10" and len(state["deposits"]) == 1
    # 한 번 더 불러도 같은 달은 다시 사지 않는다
    n = len(fake.sent)
    assert mod.deneb_only(telegram=False) == 0
    assert len(fake.sent) == n


def test_deneb_no_ask_when_amount_already_set(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    sent: list[str] = []
    fake = FakeToss(usd="2000")
    mod = _mod(monkeypatch, tmp_path, fake)
    monkeypatch.setattr(mod, "_notify", lambda text, telegram: sent.append(text))
    monkeypatch.setattr(mod, "DENEB_ENABLED", True)
    monkeypatch.setattr(mod, "DENEB_START", "2026-10-06")
    _seed(mod)
    deneb = mod._load_deneb()
    deneb.amounts["2026-10"] = 300_000.0
    mod._save_deneb(deneb)
    assert mod._deneb_ask_today("2026-10-06", True) is False
    assert not sent
