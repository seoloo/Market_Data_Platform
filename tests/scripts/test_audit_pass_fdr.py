from __future__ import annotations

import math

from scripts.audit_pass_fdr import bh, phi, t_test


def test_phi_matches_known_values() -> None:
    assert abs(phi(0.0) - 0.5) < 1e-12
    assert abs(phi(1.959964) - 0.975) < 1e-5


def test_bh_step_up_with_other_hypotheses_larger() -> None:
    p = {"a": 0.0005, "b": 0.0015, "c": 0.02}
    keep = bh(p, m=100, q=0.10)
    assert keep == {"a": True, "b": True, "c": False}  # 0.0015 ≤ 2·0.1/100, 0.02 > 3·0.1/100
    assert bh({"a": 0.002}, m=100, q=0.10) == {"a": False}  # 0.002 > 1·0.1/100


def test_t_test_sign_and_p() -> None:
    xs = [0.01, 0.02, 0.0, 0.015, 0.005] * 20
    r = t_test(xs, 0)
    assert r["t"] > 0 and r["p_one_sided"] < 0.01
    assert math.isclose(r["mean"], 0.01)
