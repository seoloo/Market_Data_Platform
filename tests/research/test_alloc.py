"""문서 127 — 매달 비중을 맞추는 두 전략 섞기."""

from __future__ import annotations

from scripts.run_alloc import mix


def test_mix_rebalances_monthly() -> None:
    days = ["2026-01-30", "2026-01-31", "2026-02-01", "2026-02-02"]
    c = [0.0, 1.0, 0.0, 0.0]  # 코인 두 배
    s = [0.0, 0.0, 0.0, 0.10]
    out = mix(days, c, s, 0.5)
    assert abs(out[1] - 0.5) < 1e-12  # 반이 두 배 → 계좌 +50%
    # 2 월 첫날 다시 반반: 코인 0.75 · 주식 0.75 → 주식 +10% 는 계좌 +5%
    assert abs(out[3] - 0.05) < 1e-12
    assert all(abs(a - b) < 1e-12 for a, b in zip(mix(days, c, s, 1.0), c, strict=True))
    assert all(abs(a - b) < 1e-12 for a, b in zip(mix(days, c, s, 0.0), s, strict=True))
