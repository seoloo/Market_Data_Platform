from __future__ import annotations

from auto_trading.ui.kr_shadow_render import _acc_panel

EVIL = "<script>alert(1)</script>"


def test_accumulation_panel_before_first_month_end() -> None:
    html = _acc_panel(None)
    assert "2026-10-30" in html and "2028-10" in html


def test_accumulation_panel_shows_ledgers_and_escapes() -> None:
    acc = {
        "start": EVIL,
        "monthly_krw": 1_000_000,
        "ledgers": {
            "ai": {
                "contributed": 2_000_000,
                "value": 2_100_000,
                "return": 0.05,
                "irr": 0.31,
                "holdings": 14,
            },
            "kospi": {
                "contributed": 2_000_000,
                "value": 1_950_000,
                "return": -0.025,
                "irr": None,
                "holdings": 1,
            },
        },
        "ai_missing_month_ends": ["2026-11-30"],
    }
    html = _acc_panel(acc)
    assert "AI 주도주 적립" in html and "KOSPI 지수 적립" in html
    assert "수급 주도주 적립" not in html  # 없는 장부는 줄을 만들지 않는다
    assert "+5.00%" in html and "AI 기록이 없던 월말 1 번" in html
    assert EVIL not in html
