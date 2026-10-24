"""272 그림자 기록 — 요약 계산 (네트워크 없음)."""

from __future__ import annotations

from scripts import shadow_272 as m


def test_summary_counts_filled_and_compounds_funding_diff() -> None:
    state = {
        "updated": "x",
        "h1": [
            {"status": "filled", "real": 0.01},
            {"status": "filled", "real": -0.005},
            {"status": "missed"},
        ],
        "funding": [
            {"diff": 0.01, "coins": {"BTC": {"gated": True}, "ETH": {"gated": False}}},
            {"diff": -0.005, "coins": {"BTC": {"gated": False}, "ETH": {"gated": False}}},
        ],
    }
    s = m.summary(state)
    assert s["h1_filled"] == 2 and s["h1_missed"] == 1
    assert abs(s["h1_mean"] - 0.0025) < 1e-12
    assert s["funding_gated_coin_days"] == 1
    assert abs(s["funding_cum_diff"] - (1.01 * 0.995 - 1)) < 1e-12


def test_run_waits_four_hours(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(m, "DATA", tmp_path)
    (tmp_path / "state.json").write_text('{"ran_ms": 1000}', encoding="utf-8")
    assert m.run(1000 + m.EVERY - 1) is None  # 아직 4 시간 안 됨 → 네트워크 안 씀
