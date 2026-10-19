"""월 입금 나누기 — 합계가 정확히 맞고 · 안내 문구 (2026-10-19 Deneb 90 · Vega 10 업비트)."""

from __future__ import annotations

from auto_trading.ops.deposit_split import ask_text, default_total, split, split_text


def test_split_sums_exactly_and_rounds_others() -> None:
    assert split(1_000_000) == {"Deneb": 900_000.0, "Vega": 100_000.0}
    odd = split(333_333)
    assert odd["Vega"] == 33_000.0
    assert sum(odd.values()) == 333_333
    assert split(0) == {"Deneb": 0.0, "Vega": 0.0}


def test_default_total_and_texts() -> None:
    assert round(default_total(300_000)) == 333_333
    ask = ask_text("2026-11-03", 300_000)
    assert "2026년 11월 입금 안내" in ask and "합계 333,333원" in ask
    text = split_text("2026-11", 1_000_000, 1_400.0)
    assert "Deneb · 토스 증권  900,000원" in text
    assert "Vega · 업비트 원화  100,000원" in text
    assert "≈" not in text and "바이낸스" not in text
    only_deneb = split_text("2026-11", 0, 1_400.0)
    assert "Vega · 업비트 원화  0원" in only_deneb
