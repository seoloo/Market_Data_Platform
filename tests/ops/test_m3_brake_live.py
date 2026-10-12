"""M3 T 브레이크 실행 (문서 183 ~ 188) — 가짜 토스로만. 실제 주문 없음."""

from __future__ import annotations

import copy
import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from auto_trading.exchange.toss import TossOrderStatus
from auto_trading.ops.m3_live import M3Order, M3State
from tests.ops.test_toss_accumulate import FakeToss, _FixedDT, _mod, _seed

TQQQ_CLOSES = [
    ("2026-09-30", 95.0),
    ("2026-10-01", 98.0),
    ("2026-10-02", 100.0),
    ("2026-10-05", 84.0),
]


def _setup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> tuple[Any, FakeToss, dict[str, Any], list[str]]:
    sent_msgs: list[str] = []
    fake = FakeToss(usd="2000")
    mod = _mod(monkeypatch, tmp_path, fake)
    monkeypatch.setattr(mod, "M3_ENABLED", True)
    monkeypatch.setattr(mod, "M3_START", "2026-10-01")
    monkeypatch.setattr(mod, "QS_LIVE", False)
    monkeypatch.setattr(mod, "datetime", _FixedDT)
    monkeypatch.setattr(mod, "_notify", lambda text, telegram: sent_msgs.append(text))
    ctl: dict[str, Any] = {"qqq_up": True, "tqqq": list(TQQQ_CLOSES)}

    def closes(symbol: str, count: int = 200) -> list[tuple[str, Decimal]]:
        if symbol == "TQQQ":
            return [(d, Decimal(str(p))) for d, p in ctl["tqqq"]]
        out = []
        d0 = date(2026, 10, 5)
        for k in range(150):
            px = 500 + k if ctl["qqq_up"] else 700 - k
            out.append(((d0 - timedelta(days=149 - k)).isoformat(), Decimal(px)))
        return out

    monkeypatch.setattr(fake, "daily_closes", closes)
    _seed(mod)
    m3 = M3State("2026-10-01", cash_usd=0.0, qty={"TQQQ": 10.0, "SCHD": 5.0}, done_month="2026-10")
    m3.orders.append(
        M3Order(
            "2026-10-01",
            "TQQQ",
            "BUY",
            10,
            101.0,
            "M3TQQQ-2026-10-01-1",
            "OID-X",
            "FILLED",
            "",
            10.0,
            980.0,
            1.0,
        )
    )
    mod._save_m3(m3)
    return mod, fake, ctl, sent_msgs


def _state(tmp_path: Path) -> dict[str, Any]:
    state: dict[str, Any] = json.loads((tmp_path / "data" / "M3.json").read_text(encoding="utf-8"))
    return state


def _at(monkeypatch: pytest.MonkeyPatch, mod: Any, d: str, fake: FakeToss | None = None) -> None:
    monkeypatch.setattr(mod, "_kst_now", lambda: datetime.fromisoformat(f"{d}T21:00:00+00:00"))
    if fake is not None:  # 다른 적립 주문은 1 주씩 체결된 것으로
        for k in range(1, len(fake.sent) + 1):
            fake.statuses.setdefault(
                f"OID-{k}",
                TossOrderStatus("FILLED", Decimal(1), Decimal(1), Decimal(1), Decimal(0)),
            )


def test_brake_sells_half_quietly_books_reserve_then_rebuys_after_trend_cycle(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    mod, fake, ctl, msgs = _setup(monkeypatch, tmp_path)
    # 1) 10-06 밤: 고점 100 → 84 (−16%) → TQQQ 10 주 중 5 주 장 마감 지정가 매도
    _at(monkeypatch, mod, "2026-10-06")
    assert mod.run(telegram=True) == 0
    brake = [o for o in fake.sent if o[1] == "SELL" and o[0] == "TQQQ"]
    assert [(o[2], o[3]) for o in brake] == [
        (5, Decimal("78.64"))
    ]  # 81.07 × 0.97 올림 (매도 지정가)
    st = _state(tmp_path)
    assert st["brake"]["stage"] == 1 and st["brake"]["lev_peak"] == 100.0 and st["brake"]["pending"]
    assert not any("브레이크" in m for m in msgs)  # 팔 때 알리지 않는다
    oid = st["orders"][-1]["order_id"]
    # 2) 10-07 밤: 체결 → 보관금, 전략 현금 그대로. 추세 켜짐이지만 꺼진 적이 없어 되사지 않음
    fake.statuses[oid] = TossOrderStatus(
        "FILLED", Decimal(5), Decimal(85), Decimal(425), Decimal("0.43")
    )
    _at(monkeypatch, mod, "2026-10-07", fake)
    n0 = len(fake.sent)
    assert mod.run(telegram=True) == 0
    st = _state(tmp_path)
    assert abs(st["brake"]["reserve_usd"] - 424.57) < 1e-9 and abs(st["cash_usd"]) < 1e-9
    assert st["qty"]["TQQQ"] == 5.0 and not st["brake"]["pending"]
    assert not [o for o in fake.sent[n0:] if o[0] == "TQQQ"]
    assert not any("브레이크" in m or "보관금" in m for m in msgs)
    report = json.loads(
        (tmp_path / "reports" / "M3_BRAKE" / "2026-10-07.json").read_text(encoding="utf-8")
    )
    assert any("보관금 $424.57" in x for x in report["lines"])
    # 3) 10-08 추세 꺼짐 → 기억. 10-09 다시 켜짐 → 보관금으로 TQQQ 되사기
    ctl["qqq_up"] = False
    _at(monkeypatch, mod, "2026-10-08", fake)
    assert mod.run(telegram=True) == 0
    assert _state(tmp_path)["brake"]["off_seen"] is True
    ctl["qqq_up"] = True
    _at(monkeypatch, mod, "2026-10-09", fake)
    n1 = len(fake.sent)
    assert mod.run(telegram=True) == 0
    rebuy = [o for o in fake.sent[n1:] if o[0] == "TQQQ"]
    assert [(o[1], o[2]) for o in rebuy] == [("BUY", 5)]  # 424.57 / (83.50 × 1.001) = 5.08
    oid2 = _state(tmp_path)["orders"][-1]["order_id"]
    # 4) 10-12 체결 → 브레이크 끝, 남은 보관금은 전략 현금, 고점은 되산 값부터
    fake.statuses[oid2] = TossOrderStatus(
        "FILLED", Decimal(5), Decimal(82), Decimal(410), Decimal("0.41")
    )
    _at(monkeypatch, mod, "2026-10-12", fake)
    assert mod.run(telegram=True) == 0
    st = _state(tmp_path)
    b = st["brake"]
    assert b["stage"] == 0 and b["reserve_usd"] == 0 and b["sales"] == [] and b["lev_peak"] == 82.0
    assert st["qty"]["TQQQ"] == 10.0 and abs(st["cash_usd"] - (424.57 - 410.41)) < 1e-9
    assert b["sold_total_usd"] == 424.57
    assert not any("브레이크" in m for m in msgs)


def test_monthly_target_halves_in_stage_one_and_preview_shows_brake(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    mod, fake, _, _ = _setup(monkeypatch, tmp_path)
    m3 = mod._load_m3()
    day = mod.Day(fake, datetime(2026, 11, 2, 21, 0, tzinfo=UTC), dry=True)
    _, _, base, _ = mod._m3_plan(day, copy.deepcopy(m3), [])
    m3.brake = {
        "ref": "T",
        "stage": 1,
        "lev_peak": 100.0,
        "reserve_usd": 400.0,
        "sales": [{"date": "2026-10-06", "dd": 0.16, "qty": 5, "usd": 400.0}],
    }
    _, _, half, _ = mod._m3_plan(day, copy.deepcopy(m3), [])
    assert abs(half - base * 0.5) < 1e-12
    m3.brake["stage"] = 2
    _, _, zero, _ = mod._m3_plan(day, copy.deepcopy(m3), [])
    assert zero == 0
    m3.brake["stage"] = 1
    mod._save_m3(m3)
    text = mod._m3_preview(datetime(2026, 11, 2, 9, 0, tzinfo=UTC))
    assert (
        "🛑 브레이크 매도 내역" in text
        and "고점 대비 −16.0%" in text
        and "매도 총액 $400.00" in text
    )


def test_rejected_brake_sale_reverts_stage_and_alerts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    mod, fake, _, msgs = _setup(monkeypatch, tmp_path)
    fake.outcome = "order-hours-closed"
    _at(monkeypatch, mod, "2026-10-06")
    assert mod.run(telegram=True) == 0
    st = _state(tmp_path)
    assert st["brake"]["stage"] == 0 and not st["brake"]["pending"]
    assert any("브레이크 매도 거절" in m for m in msgs)  # 실패는 알린다


def test_retry_command_does_not_run_brake(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    mod, _, _, _ = _setup(monkeypatch, tmp_path)
    st = mod._load_m3()
    st.done_month = ""
    st.orders.append(M3Order("2026-10-06", "SCHD", "BUY", 1, 34.0, "c", "", "REJECTED"))
    mod._save_m3(st)
    _at(monkeypatch, mod, "2026-10-06")
    monkeypatch.setattr(mod, "_kst_now", lambda: datetime(2026, 10, 6, 23, 40, tzinfo=UTC))
    mod.m3_only(telegram=False)
    st2 = _state(tmp_path)
    assert not [o for o in st2["orders"] if o["kind"]]  # 매달 맞추기만, 브레이크 주문 없음
    assert not st2["brake"]  # 브레이크 판단 자체를 하지 않았다


def test_late_run_after_midnight_ignores_unfinished_us_bar(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """21:00 작업이 자정 넘어(미국 장중) 늦게 돌아도
    그날 진행 중인 TQQQ 봉으로 브레이크를 걸지 않는다.
    """
    mod, fake, ctl, _msgs = _setup(monkeypatch, tmp_path)
    # 10-05 종가 97 (고점 100 대비 −3%) · 10-06 은 장중 60 (끝나지 않은 봉)
    ctl["tqqq"] = [*TQQQ_CLOSES[:3], ("2026-10-05", 97.0), ("2026-10-06", 60.0)]
    # 한국 10-07 00:30 = 뉴욕 10-06 11:30 (장중)
    monkeypatch.setattr(mod, "_kst_now", lambda: datetime(2026, 10, 7, 0, 30, tzinfo=UTC))
    assert mod.run(telegram=False) == 0
    assert not [o for o in fake.sent if o[0] == "TQQQ" and o[1] == "SELL"]
    assert _state(tmp_path)["brake"]["stage"] == 0
    report = json.loads(
        (tmp_path / "reports" / "M3_BRAKE" / "2026-10-07.json").read_text(encoding="utf-8")
    )
    assert any("TQQQ 2026-10-05 종가" in x for x in report["lines"])


def test_morning_catch_up_uses_finished_us_bar(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """한국 다음 날 아침(뉴욕 장 마감 뒤)에 돌면 끝난 봉은 쓴다."""
    mod, fake, ctl, _msgs = _setup(monkeypatch, tmp_path)
    ctl["tqqq"] = [*TQQQ_CLOSES[:3], ("2026-10-05", 97.0), ("2026-10-06", 60.0)]
    # 한국 10-07 10:00 = 뉴욕 10-06 21:00 (마감 뒤)
    monkeypatch.setattr(mod, "_kst_now", lambda: datetime(2026, 10, 7, 10, 0, tzinfo=UTC))
    assert mod.run(telegram=False) == 0
    assert [o[2] for o in fake.sent if o[0] == "TQQQ" and o[1] == "SELL"] == [10]
