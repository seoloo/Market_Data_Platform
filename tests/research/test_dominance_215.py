# ruff: noqa: E501 — 한국어 설명이 길다
"""문서 215 합성 검산 — 도미넌스 식 · 시각 정렬 · 미래 정보 차단 · 신호 전환 · 주문 수량 · funding · 보호 · 24 시간 청산."""

from __future__ import annotations

import math
from array import array

from scripts.run_dominance_215 import H4, Bar, signals
from scripts.run_dominance_215_pilot import DELAY_MIN, P, atr20_4h, simulate_arm
from scripts.run_qb_dc_210 import DAY, MIN, WARM, Market

T0 = WARM + 10 * DAY  # 합성 평가 시작
DAYS = 6


def test_dominance_identity_and_decomposition() -> None:
    usdt, total = 150e9, 3000e9
    d0 = usdt / total * 100
    d1 = usdt * 1.0001 / (total * 1.01) * 100
    assert (
        abs(math.log(d1 / d0) - (math.log(1.0001) - math.log(1.01))) < 1e-12
    )  # Δln D = Δln 분자 − Δln TOTAL


def _bar(t: int, ud: float, bd: float, tot: float, spot: float) -> Bar:
    return Bar(t, ud, bd, tot, ud * tot / 100, bd * tot / 100, spot)


def test_signals_use_completed_bar_and_previous_bar() -> None:
    t = 1_735_689_600_000
    bars = [
        _bar(t, 5.0, 58.0, 3e12, 100.0),
        _bar(t + H4, 4.9, 58.1, 3.1e12, 101.0),
        _bar(t + 3 * H4, 4.8, 58.2, 3.2e12, 102.0),
    ]
    sg = signals(bars)
    assert sg == [
        (t + 2 * H4, True, True, True)
    ]  # 판단 시각 = 봉 완성 시각, 빠진 봉 다음은 신호 없음
    flat_usdt_drop = [
        _bar(t, 5.0, 58.0, 3e12, 100.0),
        Bar(t + H4, 4.99, 58.1, 2.99e12, 150e9 * 0.99, 58.1 * 2.99e12 / 100, 99.0),
    ]
    s2 = signals(flat_usdt_drop)
    assert (
        s2[0][1] is True and s2[0][3] is False
    )  # A 만: USDT 시총이 TOTAL 보다 더 줄어 USDT.D 하락 · TOTAL 도 하락


def _market(price) -> Market:
    n = (T0 - WARM) // MIN + DAYS * DAY // MIN
    xs = [price(i) for i in range(n)]
    a = array("d", xs)
    lo = array("d", [x * 0.999 for x in xs])
    hi = array("d", [x * 1.001 for x in xs])
    return Market(array("d", xs), hi, lo, a, array("d", [1e12] * n), 0)


def _run(m: Market, sig: list[tuple[int, bool]], p: P | None = None) -> dict:  # type: ignore[type-arg]
    return simulate_arm(m, sig, p or P(), start=T0, end=T0 + DAYS * DAY)


def test_transition_entry_size_and_24h_exit() -> None:
    m = _market(lambda i: 100_000.0)
    t_sig = T0 + 2 * H4
    sig = [(t_sig - H4, False), (t_sig, True), (t_sig + H4, True), (t_sig + 2 * H4, True)]
    r = _run(m, sig)
    bk = r["book"]
    assert len(bk.trades) == 1 and bk.trades[0]["reason"] == "시간 청산"
    i_entry = (t_sig - WARM) // MIN + DELAY_MIN
    assert bk.trades[0]["first_fill_i"] == i_entry  # 봉 완성 + 5 분, 신호 봉 종가로 소급 안 함
    assert bk.trades[0]["exit_i"] == i_entry + 24 * 60
    atr = atr20_4h(m, t_sig)
    assert atr is not None
    q = math.floor(min(0.0025 * 10_000 / (2 * atr), 10_000 / 100_000) / 0.001 + 1e-9) * 0.001
    assert (
        abs(bk.trades[0]["fees"] - q * 100_000 * 0.00095 * 2) < 1e-6
    )  # 단위 내림 수량 × 왕복 0.095%


def test_min_notional_skip_and_no_retry_while_on() -> None:
    m = _market(lambda i: 100_000.0)
    t_sig = T0 + 2 * H4
    r = _run(m, [(t_sig, True)], P(equity0=100.0))  # 0.25% 위험이면 수량 < 0.001
    assert r["book"].skips["수량 미달(사전 검사)"] == 1 and not r["book"].trades

    # 진입이 미체결이면 계속 ON 이어도 다시 시도하지 않는다
    def px(i: int) -> float:
        return (
            100_000.0 if i < (t_sig - WARM) // MIN + DELAY_MIN else 101_000.0
        )  # 시가가 한도(0.3%) 밖으로 튐

    r2 = _run(_market(px), [(t_sig, True), (t_sig + H4, True), (t_sig + 2 * H4, True)])
    assert r2["book"].skips["미체결"] == 1 and not r2["book"].trades


def test_stop_first_and_funding_sign() -> None:
    t_sig = T0 + 2 * H4
    i_entry = (t_sig - WARM) // MIN + DELAY_MIN
    m = _market(lambda i: 100_000.0 if i < i_entry + 120 else 90_000.0)
    ft = WARM + (i_entry + 60) * MIN
    m.funding = [(ft, 0.0001, 100_000.0)]
    r = _run(m, [(t_sig, True)])
    tr = r["book"].trades[0]
    assert tr["reason"] == "보호 청산" and tr["exit_i"] == i_entry + 120 and tr["extra"] > 0
    assert tr["funding"] > 0  # 롱: 양수 rate 면 냄
