# ruff: noqa: E501 — 한국어 설명 · 표 문자열이 길다
"""문서 202 금 돌파 — 체결 규칙(상한 · 손절 우선 · 익절 통과 · 시간 정리 · C 종가 확인) 합성 봉 시험."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

from scripts.run_gold_breakout_202 import NY, VERDICT, Costs, Rules, simulate_day, summary

DAY = date(2024, 3, 6)  # 수요일
R = Rules()


def ms(d: date, t: time) -> int:
    return int(datetime.combine(d, t, NY).astimezone(UTC).timestamp() * 1000)


def flat_range(
    lo: float = 1990.0, hi: float = 2010.0
) -> list[tuple[int, float, float, float, float, float]]:
    """전날 19:00 ~ 당일 07:00 매 분, 폭 = hi/lo − 1 (≈ 100bp)."""
    out = []
    t = ms(DAY - timedelta(days=1), time(19, 0))
    end = ms(DAY, time(7, 0))
    while t < end:
        out.append((t, 2000.0, hi, lo, 2000.0, 1.0))
        t += 60_000
    return out


def bar(
    t: time, o: float, h: float, lo: float, c: float
) -> tuple[int, float, float, float, float, float]:
    return (ms(DAY, t), o, h, lo, c, 1.0)


def test_b_long_target_and_costs() -> None:
    up = 2010 * 1.0002
    w = 20.0
    sess = [
        bar(time(7, 0), 2005, 2008, 2004, 2007),
        bar(time(7, 1), 2008, up + 1, 2007, up + 0.5),  # 돌파 → up 에 진입
        bar(time(7, 2), up, up + w * 1.0 + 0.5, up - 1, up + 15),  # 익절가 + 1bp 넘김
    ]
    t = simulate_day(DAY, flat_range(), sess, R, "B")
    assert t is not None and t.side == 1 and t.reason == "target"
    assert abs(t.entry - up) < 1e-9 and abs(t.exit - (up + w)) < 1e-9
    assert abs(t.net(VERDICT) - (w / up - 0.0005)) < 1e-12  # 익절은 지정가 0%


def test_b_same_bar_stop_first_and_gap_stop() -> None:
    up = 2010 * 1.0002
    sess = [
        bar(time(7, 1), 2008, up + 1, 2007, up),
        bar(time(7, 2), up, up + 30, up - 11, up),  # 손절(−10)과 익절 둘 다 → 손절
    ]
    t = simulate_day(DAY, flat_range(), sess, R, "B")
    assert t is not None and t.reason == "stop" and abs(t.exit - (up - 10)) < 1e-9
    gap = [
        bar(time(7, 1), 2008, up + 1, 2007, up),
        bar(time(7, 2), up - 15, up - 14, up - 16, up - 15),
    ]
    t2 = simulate_day(DAY, flat_range(), gap, R, "B")
    assert t2 is not None and t2.reason == "stop" and t2.exit == up - 15  # 갭이면 시가


def test_b_cap_skips_gap_entry_and_time_exit() -> None:
    up = 2010 * 1.0002
    gapped = [
        bar(time(7, 1), up * 1.001, up * 1.002, up * 1.0005, up * 1.001)
    ]  # 시가가 상한 5bp 밖
    assert simulate_day(DAY, flat_range(), gapped, R, "B") is None
    sess = [
        bar(time(7, 1), 2008, up + 1, 2007, up),
        bar(time(10, 0), up + 2, up + 3, up + 1, up + 2),
        bar(time(15, 0), up + 4, up + 5, up + 3, up + 4),
    ]
    t = simulate_day(DAY, flat_range(), sess, R, "B")
    assert t is not None and t.reason == "time" and t.exit == up + 4


def test_narrow_range_and_late_signal_skip() -> None:
    assert (
        simulate_day(DAY, flat_range(1999, 2001), [bar(time(7, 1), 2000, 2010, 2000, 2005)], R, "B")
        is None
    )  # 10bp 폭
    late = [bar(time(11, 0), 2008, 2020, 2007, 2015)]
    assert simulate_day(DAY, flat_range(), late, R, "B") is None


def test_c_needs_15min_close() -> None:
    up = 2010 * 1.0002
    wick = [
        bar(time(7, m), 2008, up + 3 if m == 5 else 2009, 2007, 2009) for m in range(15)
    ]  # 꼬리만
    assert simulate_day(DAY, flat_range(), wick, R, "C") is None
    sess = [bar(time(7, m), 2009, up + 2, 2008, up + 1 if m == 14 else 2009) for m in range(15)]
    sess.append(bar(time(7, 15), up + 1.5, up + 2, up + 1, up + 1.8))
    sess.append(bar(time(15, 0), up + 3, up + 3, up + 3, up + 3))
    t = simulate_day(DAY, flat_range(), sess, R, "C")
    assert t is not None and t.side == 1 and t.entry == up + 1.5 and t.reason == "time"


def test_summary_and_double_cost() -> None:
    up = 2010 * 1.0002
    sess = [bar(time(7, 1), 2008, up + 1, 2007, up), bar(time(15, 0), up, up, up, up)]
    t = simulate_day(DAY, flat_range(), sess, R, "B")
    assert t is not None
    s = summary([t, t], VERDICT)
    assert (
        s["n"] == 2 and s["mean_bp"] == -11.2
    )  # 진입 5 · 시간 정리 5 · 16:00 UTC 넘긴 롱 funding 1.2
    assert summary([t, t], Costs(0.001, 0.001, 0.001, 0.0, 0.0))["mean_bp"] == -20.0  # funding 0
