"""문서 124 VIX 회복 신호 — T 규칙 또는 (60 일 최고 ≥ X 이고 오늘 ≤ r × 최고)."""

from __future__ import annotations

from scripts.run_qs_vix import vix_signal


def test_vix_recovery_turns_on_only_after_spike_eases() -> None:
    days = [f"d{i:02d}" for i in range(6)]
    trend: dict[str, bool | None] = dict.fromkeys(days, False)
    trend["d00"] = None
    vix = [20.0, 35.0, 33.0, 26.0, 24.0, 20.0]
    s = vix_signal(days, trend, vix, 30.0, 0.8)
    assert [s[d] for d in days] == [None, False, False, True, True, True]  # 35 × 0.8 = 28
    trend["d02"] = True
    assert vix_signal(days, trend, vix, 30.0, 0.8)["d02"] is True  # T 규칙 켜짐이면 그대로
    assert not any(vix_signal(days, trend | {"d02": False}, vix, 40.0, 0.8)[d] for d in days[1:])
