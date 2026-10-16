"""250 — 손절 폭별 비중 · 종목 수 · 판정 (순수)."""

from __future__ import annotations

from scripts import run_stopwidth_250 as m


def test_weight_and_slots_keep_risk_and_full_investment() -> None:
    assert [m.arm_name(w) for w in m.WIDTHS] == ["S3", "S5", "S8"]
    assert [m.slots(w) for w in m.WIDTHS] == [10, 17, 25]
    assert [round(m.RISK / w, 4) for w in m.WIDTHS] == [0.1, 0.06, 0.0375]


def test_judge() -> None:
    def row(sh: float, mdd: float, h1: float, h2: float) -> dict[str, dict[str, float]]:
        return {
            "full": {"sharpe": sh, "mdd": mdd},
            "first": {"sharpe": h1},
            "second": {"sharpe": h2},
        }

    kr = {
        "S3": row(0.34, 0.65, 0.14, 0.47),
        "S5": row(0.46, 0.68, 0.20, 0.50),
        "S8": row(0.50, 0.75, 0.20, 0.60),  # 낙폭 +10%p
    }
    assert m.judge(kr) == "ADOPT_S5"
    kr["S5"] = row(0.40, 0.60, 0.20, 0.50)
    assert m.judge(kr) == "KEEP_S3"
