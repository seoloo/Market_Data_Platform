"""문서 128 — GEM 규칙: 절대 모멘텀(SPY vs BIL) → 상대 모멘텀(SPY vs EFA), 아니면 AGG."""

from __future__ import annotations

from scripts.run_gem import gem_returns

from scripts import run_gem


def test_gem_switches_to_bonds_when_stocks_lose_to_bills(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(run_gem, "LOOK", 2)
    days = ["2026-01-28", "2026-01-29", "2026-01-30", "2026-02-02", "2026-02-27", "2026-03-02"]
    px = {
        "SPY": dict(zip(days, [100, 105, 110, 111, 90, 91], strict=True)),
        "EFA": dict(zip(days, [100, 102, 105, 106, 95, 96], strict=True)),
        "BIL": dict(zip(days, [100, 100.05, 100.1, 100.2, 100.3, 100.4], strict=True)),
        "AGG": dict(zip(days, [100, 100, 100, 101, 102, 103], strict=True)),
    }
    _rets, held = gem_returns(days, px)  # type: ignore[arg-type]
    # 1-30 월말: SPY(+10%) > BIL, SPY > EFA → 다음 날 SPY
    assert held[3] == "SPY"
    # 2-27 월말: SPY 2 일 전 대비 −18% < BIL → 다음 날 AGG
    assert held[5] == "AGG"
