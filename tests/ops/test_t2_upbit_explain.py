"""Cruise 알림 설명 (2026-10-12 시안 C+) — 다음 가격 · 5 %p 필터 · 가격 표기 · 바뀐 추세."""

from __future__ import annotations

from decimal import Decimal

from auto_trading.ops.t2_upbit import (
    CoinSignal,
    krw_price,
    max_weight,
    next_flips,
    next_lines,
    size_text,
    why_text,
)

D = Decimal


def _closes(today: str, ref20: str, ref60: str, ref120: str) -> list[Decimal]:
    """길이 130. closes[-n] = 내일 기준(ref), closes[-1-n] = 오늘의 n 일 전."""
    xs = [D(100)] * 130
    xs[-20], xs[-60], xs[-120] = D(ref20), D(ref60), D(ref120)
    xs[-1] = D(today)
    return xs


def _sig(parts: tuple[str, str, str], vol: str, weight: str) -> CoinSignal:
    p = tuple(D(x) for x in parts)
    return CoinSignal(
        sum(1 for x in p if x > 0), (p[0], p[1], p[2]), D(vol), D(weight), D(weight), D(0)
    )


def test_krw_price_units() -> None:
    assert krw_price(D("115650000")) == "1억 1,565만"
    assert krw_price(D("199995000")) == "2억"
    assert krw_price(D("99190000")) == "9,919만"
    assert krw_price(D("3695000")) == "3,695,000"


def test_next_flips_nearest_on_and_off_like_btc_2026_10_11() -> None:
    # BTC 10-11: 20 일 하락 · 60 · 120 일 상승
    # 내일 기준 20 일 +2.0 % · 60 일 −21 % · 120 일 −12.5 %
    cl = _closes("113364000", "115650000", "89554000", "99190000")
    sg = _sig(("-0.0321", "0.2646", "0.1630"), "0.368", "0.1359")
    up, down = next_flips(cl, sg)
    assert up is not None and up.lookback == 20 and up.turns_on and up.price == D("115650000")
    assert abs(up.new_weight - max_weight(D("0.368"))) < D("1e-9")  # 3/3 → 최대 비중
    assert down is not None and down.lookback == 120 and not down.turns_on  # 60 일보다 먼저 꺼짐
    assert down.new_weight == 0  # 점수 −1/3 → 롱만이라 0


def test_next_lines_only_big_hides_small_changes() -> None:
    cl = _closes("100", "105", "80", "90")
    tiny = _sig(
        ("-0.05", "0.2", "0.1"), "3.0", "0.0167"
    )  # 최대 5 % → 1/3 = 1.7 % · 바뀌어도 5 %p 안 됨
    assert next_lines(cl, tiny, only_big=True) == []
    lines = next_lines(cl, tiny, only_big=False)
    assert (
        len(lines) == 2
        and "20일 추세 켜져" in lines[0]
        and "120일 추세 꺼져 0% (전량 매도)" in lines[1]
    )
    big = _sig(("-0.05", "0.2", "0.1"), "0.4", "0.125")  # 최대 37.5 % → 켜지면 +25 %p
    up = next_lines(cl, big, only_big=True)
    assert up[0] == "다음 🔺 105 원(+5.0%) 위로 마감 → 20일 추세 켜져 약 38%"


def test_why_text_names_the_flipped_lookback_with_prices() -> None:
    cl = _closes("113364000", "115650000", "89554000", "99190000")
    cl[-21] = D("117120000")  # 오늘의 20 일 전
    sg = _sig(("-0.0321", "0.2646", "0.1630"), "0.368", "0.1359")
    text = why_text(cl, sg, [D("0.05"), D("0.3"), D("0.2")])
    assert text == "20일 전 1억 1,712만 → 어제 1억 1,336만 (-3.2%) — 짧은 추세가 꺼짐"
    assert why_text(cl, sg, None) is None
    assert why_text(cl, sg, [D("-0.01"), D("0.3"), D("0.2")]) is None  # 바뀐 추세 없음


def test_size_text_shows_cap_and_score() -> None:
    sg = _sig(("-0.0321", "0.2646", "0.1630"), "0.368", "0.1359")
    t = size_text(sg)
    assert "최대 비중 15% ÷ 36.8% = 40.8%" in t and "× 점수 1/3 = 13.6%" in t
    off = _sig(("-0.1", "-0.2", "0.1"), "0.368", "0")
    assert "롱만이라 0" in size_text(off)
