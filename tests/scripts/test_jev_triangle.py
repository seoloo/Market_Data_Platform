from __future__ import annotations

from collections import Counter
from itertools import pairwise

from scripts.jev_triangle import DAYS, dataset, question, summary, swings


def test_dataset_is_balanced_and_shaped() -> None:
    paths = dataset(1, 40)
    assert Counter(p["truth"] for p in paths) == {True: 20, False: 20}
    assert Counter(p["shape"] for p in paths if not p["truth"]) == {
        "symmetric": 5,
        "descending": 5,
        "rectangle": 5,
        "uptrend": 5,
    }
    assert all(len(p["closes"]) == DAYS for p in paths)
    assert all(
        lo <= c <= hi
        for p in paths
        for lo, c, hi in zip(p["lows"], p["closes"], p["highs"], strict=True)
    )


def test_swings_alternate_and_question_has_no_label() -> None:
    p = dataset(2, 4)[0]
    sw = swings(p["highs"], p["lows"])
    kinds = [s["k"] for s in sw[:-1]]
    assert all(a != b for a, b in pairwise(kinds))
    assert sw[-1]["day"] == DAYS - 1
    assert set(question(p)["instructions"]) == {
        "question",
        "definition",
        "swings",
    }  # 모양 이름 없음


def test_summary_counts_pairs() -> None:
    rows = [
        {"shape": "ascending", "truth": True, "code": False, "jev": True},
        {"shape": "uptrend", "truth": False, "code": False, "jev": True},
        {"shape": "ascending", "truth": True, "code": True, "jev": True},
    ]
    s = summary(rows)
    assert (s["jev_only"], s["code_only"]) == (1, 1)
    assert s["jev_acc"] == round(2 / 3, 4) and s["code_acc"] == round(2 / 3, 4)
    assert s["by_shape"]["ascending"] == {"n": 2, "jev_acc": 1.0, "code_acc": 0.5}
