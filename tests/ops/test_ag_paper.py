"""AG Paper 계산 (문서 117): 환산·정리선·반반 재조정."""

from __future__ import annotations

from datetime import date, timedelta

from auto_trading.ops.ag_paper import apply_stop, mix, scale, summary


def _days(start: str, n: int) -> list[str]:
    d0 = date.fromisoformat(start)
    return [(d0 + timedelta(days=i)).isoformat() for i in range(n)]


def test_scale_keeps_returns() -> None:
    assert scale({"2026-10-02": 10_000.0, "2026-10-03": 11_000.0}) == {
        "2026-10-02": 1_000.0,
        "2026-10-03": 1_100.0,
    }


def test_stop_freezes_at_thirty_percent() -> None:
    path = {"a": 1_000.0, "b": 400.0, "c": 290.0, "d": 900.0}
    out, stopped = apply_stop(path)
    assert stopped == "c"
    assert out == {"a": 1_000.0, "b": 400.0, "c": 290.0, "d": 290.0}
    s = summary(path)
    assert s["value_usd"] == 290.0 and s["stopped"] == "c" and s["worst_vs_start"] == -0.71


def test_mix_half_half_then_yearly_rebalance() -> None:
    days = _days("2026-10-02", 400)
    cr = {d: 1_000.0 * (1.01 if i else 1.0) ** i for i, d in enumerate(days)}
    us = {d: 1_000.0 for i, d in enumerate(days) if date.fromisoformat(d).weekday() < 5}
    out = mix(us, cr)
    first = days[0]
    assert out[first] == 1_000.0
    # 1 년 전에는 미국 쪽 500 고정 + 코인 쪽 500 × 성장
    d = days[100]
    assert abs(out[d] - (500.0 + 500.0 * 1.01**100)) < 1e-6
    # 기념일 날 다시 반반 → 그 뒤 하루 변화는 코인 절반에만 걸린다
    anniv = "2027-10-02"
    i = days.index(anniv)
    v = out[anniv]
    assert abs(out[days[i + 1]] - (v / 2 + v / 2 * 1.01)) < 1e-6


def test_mix_carries_us_close_over_weekend() -> None:
    days = _days("2026-10-02", 4)  # 금·토·일·월
    cr = dict.fromkeys(days, 1_000.0)
    us = {"2026-10-02": 1_000.0, "2026-10-05": 1_100.0}
    out = mix(us, cr)
    assert out["2026-10-03"] == out["2026-10-04"] == 1_000.0
    assert abs(out["2026-10-05"] - 1_050.0) < 1e-9
