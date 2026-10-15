"""월 입금 나누기 시안 A — 합계가 정확히 맞고 · 안내 문구."""

from __future__ import annotations

from auto_trading.ops.deposit_split import ask_text, default_total, split, split_text


def test_split_sums_exactly_and_rounds_others() -> None:
    assert split(1_000_000) == {"Glide": 800_000.0, "Cruise": 100_000.0, "Falcon": 100_000.0}
    odd = split(333_333)
    assert odd["Cruise"] == odd["Falcon"] == 33_000.0
    assert sum(odd.values()) == 333_333
    assert split(0) == {"Glide": 0.0, "Cruise": 0.0, "Falcon": 0.0}


def test_default_total_and_texts() -> None:
    assert default_total(300_000) == 375_000
    ask = ask_text("2026-11-03", 300_000)
    assert "2026년 11월 입금 안내" in ask and "합계 375,000원" in ask
    text = split_text("2026-11", 1_000_000, 1_400.0)
    assert "Glide · 토스 증권  800,000원" in text
    assert "Falcon · 바이낸스 선물(USDⓈ-M)  100,000원 ≈ 71 USDT" in text
    assert "비율대로 자동으로 나눠요" in text
    no_rate = split_text("2026-11", 1_000_000, None)
    assert "≈" not in no_rate
    only_glide = split_text("2026-11", 0, 1_400.0)
    assert "바이낸스로 보내는 법" not in only_glide
