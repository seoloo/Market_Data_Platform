from scripts import run_kr_gate_237 as m


def test_gate_moving_average() -> None:
    xs = [1.0, 2.0, 3.0, 2.0, 1.0]
    assert m.gate(xs, 0) == [True] * 5
    # 3 일 평균: -, -, 2.0, 2.33, 2.0 → 3 > 2 만 위
    assert m.gate(xs, 3) == [False, False, True, False, False]


def test_index_gated_next_day_and_fee() -> None:
    xs = [100.0, 110.0, 121.0, 110.0]
    eq = m.index_gated(xs, [True, True, False, False], fee=0.0)
    # t=2 종가에 '아래' 신호 → t=3 종가에 팖 (t=3 수익까지는 들고 있음)
    assert [round(v, 4) for v in eq] == [1.0, 1.1, 1.21, round(1.21 * 110 / 121, 4)]
    eq2 = m.index_gated(xs, [True, False, False, False], fee=0.01)
    assert round(eq2[2], 4) == round(1.21 * 0.99, 4)
    assert eq2[3] == eq2[2]


def test_judge_needs_both_rules() -> None:
    def row(sh: float, c1: float, c2: float, mdd: float) -> dict[str, dict[str, float]]:
        return {
            "full": {"sharpe": sh, "mdd": mdd, "cagr": 0.0},
            "first": {"cagr": c1},
            "second": {"cagr": c2},
        }

    rows = {
        "LT/G200": row(0.2, 0.0, 0.0, 0.4),
        "FLOW/G200": row(0.3, 0.0, 0.0, 0.4),
        "LT/G0": row(0.35, 0.01, 0.01, 0.44),
        "FLOW/G0": row(0.45, 0.01, 0.01, 0.46),
    }
    assert m.judge(rows, "G0")["pass"] is False  # FLOW 낙폭 +6%p
    rows["FLOW/G0"] = row(0.45, 0.01, 0.01, 0.45)
    assert m.judge(rows, "G0")["pass"] is True
