from __future__ import annotations

import pytest
from scripts.run_theme_etf_163 import basket_weights

THEMES = {"반도체": ["A", "A2"], "바이오": ["B"], "방산": ["C", "C2", "C3"], "원전": ["D"]}


def test_u_takes_first_eligible_per_theme_and_equal_themes() -> None:
    elig = {"A": False, "A2": True, "B": True, "C": True, "C2": True, "D": False}
    w = basket_weights(THEMES, elig, "U")
    assert w == pytest.approx({"A2": 1 / 3, "B": 1 / 3, "C": 1 / 3})


def test_w_splits_within_theme() -> None:
    elig = dict.fromkeys(["A", "A2", "B", "C", "C2", "C3", "D"], True)
    w = basket_weights(THEMES, elig, "W")
    assert w["A"] == pytest.approx(1 / 4 / 2)
    assert w["C3"] == pytest.approx(1 / 4 / 3)
    assert sum(w.values()) == pytest.approx(1.0)


def test_fewer_than_three_themes_is_empty() -> None:
    assert basket_weights(THEMES, {"A": True, "B": True}, "W") == {}
