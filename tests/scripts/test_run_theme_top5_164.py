from __future__ import annotations

from scripts.run_theme_top5_164 import top_themes


def test_top_themes_by_value_then_name() -> None:
    v = {"반도체": 9.0, "바이오": 5.0, "금융": 5.0, "조선": 1.0, "게임": 7.0, "원전": 0.5}
    assert top_themes(v, 3) == ["반도체", "게임", "금융"]
    assert top_themes(v) == ["반도체", "게임", "금융", "바이오", "조선"]
