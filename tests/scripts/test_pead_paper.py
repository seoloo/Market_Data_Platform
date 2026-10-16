"""문서 241 — PEAD Paper 가상 계좌: 자리 · 현금 · 초과 수익 · 열린 자리."""

from __future__ import annotations

from scripts import pead_paper as pp


def _sig(
    sym: str,
    entry_day: str,
    exit_day: str | None,
    entry: float,
    exit_px: float | None,
    gap: float = 0.06,
) -> dict[str, object]:
    return {
        "sym": sym,
        "r_day": entry_day,
        "gap": gap,
        "entry_day": entry_day,
        "entry": entry,
        "exit_day": exit_day,
        "exit": exit_px,
    }


def test_simulate_closed_open_and_excess() -> None:
    spy = {"d1": (100.0, 100.0), "d9": (100.0, 101.0)}
    sigs = [_sig("A", "d1", "d9", 10.0, 11.0), _sig("B", "d2", None, 20.0, None)]
    rep = pp.simulate(sigs, spy)
    assert rep["closed_n"] == 1
    c = rep["closed"][0]
    gross = 500 * (1 - pp.FEE) * 1.1 * (1 - pp.FEE)
    assert abs(c["ret"] - (gross / 500 - 1)) < 1e-12
    assert abs(c["excess"] - (gross / 500 - 1 - 0.01)) < 1e-12
    assert [o["sym"] for o in rep["open"]] == ["B"]
    assert abs(rep["cash"] - (10_000 - 500 + gross - 500)) < 1e-9


def test_simulate_slot_limit(monkeypatch: object) -> None:
    sigs = [_sig(f"S{i}", "d1", None, 1.0, None, gap=0.05 + i / 100) for i in range(25)]
    rep = pp.simulate(sigs, {})
    assert len(rep["open"]) == pp.MAX_SLOTS
    assert rep["skipped_no_slot"] == 5
    assert rep["open"][0]["sym"] == "S24"  # 갭 큰 순
