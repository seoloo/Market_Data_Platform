from __future__ import annotations

import pytest
from scripts.audit_dmn_phase0 import lstm_params, mde, newey_west_sd, required_months


def test_lstm_parameter_count_matches_pytorch_layout() -> None:
    # 4 게이트 × (16×4 입력 + 16×16 순환 + 편향 2 벌 16) + 출력 16 + 1
    assert lstm_params(4, 16, 1) == 4 * (64 + 256 + 32) + 17 == 1425


def test_newey_west_equals_plain_sd_without_autocorrelation_terms() -> None:
    xs = [0.01, -0.01] * 50
    plain = (sum(x * x for x in xs) / len(xs)) ** 0.5
    assert newey_west_sd(xs, 0) == pytest.approx(plain)
    assert newey_west_sd(xs, 3) < plain  # 음의 자기상관이면 장기 분산이 작아진다


def test_required_months_and_mde_are_inverse() -> None:
    n = required_months(0.04, 0.0025, 0.8416)
    assert n == 2068
    assert mde(0.04, n, 0.8416) == pytest.approx(0.0025, rel=1e-3)
