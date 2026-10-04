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
        table = {"QNDX": "25.4", "BTQ": "3.17", "WQTM": "32.5", "TQQQ": "81.07"}
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

    monkeypatch.setattr(mod, "DATA", tmp_path / "data")
    monkeypatch.setattr(mod, "REPORTS", tmp_path / "reports")
    monkeypatch.setattr(mod, "HALT", tmp_path / "HALT")
    monkeypatch.setattr(mod, "_trader", lambda: fake)
    monkeypatch.setattr(mod, "_kst_now", lambda: NOW_KST)
    monkeypatch.setattr(mod, "_notify", lambda text, telegram: None)
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
    assert (
        "장 마감 지정가 매수 접수" in report["message"] and "QS: QQQ 추세 켜짐" in report["message"]
    )


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
    qs = json.loads((tmp_path / "data" / "QS.json").read_text(encoding="utf-8"))
    assert qs["prev_on"] is False and qs["want"] == "SELL"


def test_qs_buy_short_of_dollars_is_retried_next_day(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = FakeToss(outcome="insufficient-buying-power")
    mod = _mod(monkeypatch, tmp_path, fake)
    _seed(mod, qndx_start="2026-08-01", qs=QsLiveState("2026-10-05", 0.0, 0.0, 0.0, prev_on=False))
    monkeypatch.setattr(mod, "datetime", _FixedDT)
    mod.run(telegram=False)
    qs = json.loads((tmp_path / "data" / "QS.json").read_text(encoding="utf-8"))
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
    qs = json.loads((tmp_path / "data" / "QS.json").read_text(encoding="utf-8"))
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
    saved = json.loads((tmp_path / "data" / "QS.json").read_text(encoding="utf-8"))
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
