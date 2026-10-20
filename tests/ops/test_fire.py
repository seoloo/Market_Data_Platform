"""파이어 진행 보고: 복리 달 수 · 입금 속도 · 문구."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from auto_trading.ops.fire import Snapshot, monthly_rate, months_to_target, report_text


def test_months_to_target() -> None:
    assert months_to_target(100.0, 0.0, 0.0, 50.0) == 0
    assert months_to_target(0.0, 10.0, 0.0, 100.0) == 10  # 수익 0 이면 넣은 만큼
    assert months_to_target(0.0, 10.0, 0.12, 100.0) == 10  # 1 년 안이라 거의 같음
    assert months_to_target(0.0, 0.0, 0.1, 100.0) is None


def test_monthly_rate_uses_months_since_first_deposit() -> None:
    deps = [("2026-01-01", 300_000.0), ("2026-07-01", 300_000.0), ("2026-07-01", 0.0)]
    assert abs(monthly_rate(deps, date(2026, 7, 1)) - 600_000 / (181 / 30.44)) < 1
    assert monthly_rate([("2026-10-09", 600_000.0)], date(2026, 10, 10)) == 600_000  # 최소 1 달
    assert monthly_rate([], date(2026, 10, 10)) == 0.0


def test_report_text() -> None:
    text = report_text(
        date(2026, 11, 1),
        Snapshot(1_000_000.0, 1_100_000.0),
        600_000.0,
        Snapshot(600_000.0, 640_000.0),
    )
    assert "목표 1억 원" in text and "진행 ░░░░░░░░░░ 1.1%" in text
    assert "손익 +100,000원 (+10.0%)" in text
    assert "지난 보고보다 +460,000원 (입금 +400,000 · 시장 +60,000)" in text
    assert "⚪ 보통(연 12%)" in text and "년" in text
    empty = report_text(date(2026, 11, 1), Snapshot(0.0, 0.0), 0.0, None)
    assert "계산하지 못했어요" in empty


def test_vega_deposits_come_from_upbit_ledger_even_if_old_binance_ledger_remains(
    tmp_path: Path,
) -> None:
    """2026-10-21 사용자 "그렇게 고쳐줘" — Vega 입금은 업비트 장부만.
    옛 바이낸스 장부 · Altair 이체는 세지 않는다(이중 셈 방지)."""
    import json

    from scripts.fire_report import dated_deposits

    def write(rel: str, data: object) -> None:
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")

    write(
        "data/upbit_t2/state.json",
        {
            "deposits": [["2026-10-19", "471507"]],
            "deposits_archive": {"deposits": [["2026-10-08", "300000"]]},
        },
    )
    write(
        "data/vega/state.json",
        {"deposits": [["2026-10-15", "160"]], "altair": {"transfers": [["2026-10-16", "50"]]}},
    )
    assert dated_deposits(tmp_path) == [("2026-10-19", 471507.0)]
