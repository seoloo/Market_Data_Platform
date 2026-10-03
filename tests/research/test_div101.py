"""문서 101: 15 분 RSI 정규 다이버전스 규칙 (TradingView 기본과 같은 정의) · 한 시간축 겹침 제거."""

from __future__ import annotations

from scripts.audit_div101_15m import (
    HORIZON,
    PIVOT,
    STEP,
    WARMUP,
    divergences,
    rsi,
    thin,
)


def test_rsi_extremes_and_warmup() -> None:
    up = [100.0 + k for k in range(30)]
    values = rsi(up)
    assert all(v is None for v in values[:14])
    assert values[14] == 100.0 and values[-1] == 100.0
    down = [100.0 - k for k in range(30)]
    assert rsi(down)[-1] == 0.0


def _bars(
    closes: list[float], lows: list[float] | None = None
) -> list[tuple[int, float, float, float, float]]:
    lows = lows or closes
    return [
        (k * STEP, c, max(c, lo), lo, c) for k, (c, lo) in enumerate(zip(closes, lows, strict=True))
    ]


def _series_with_bull_divergence() -> tuple[list[float], int, int]:
    """예열 뒤 한 번 크게 떨어졌다가(저점 1) 반등, 다시 더 낮지만 완만하게 떨어진다(저점 2)."""
    closes = [100.0 + (0.3 if k % 2 else -0.3) for k in range(WARMUP)]
    k1 = len(closes) + 9
    closes += [100.0 - 2.0 * (i + 1) for i in range(10)]  # 급락 → RSI 아주 낮음
    closes += [closes[-1] + 1.5 * (i + 1) for i in range(10)]  # 반등
    start = closes[-1]
    k2 = len(closes) + 19
    closes += [start - 0.8 * (i + 1) for i in range(20)]  # 완만한 하락, 더 낮은 저점
    closes += [closes[-1] + 1.0 * (i + 1) for i in range(PIVOT + HORIZON + 5)]
    return closes, k1, k2


def test_bull_divergence_found_at_confirmation_bar() -> None:
    closes, k1, k2 = _series_with_bull_divergence()
    assert closes[k2] < closes[k1]
    events = [e for e in divergences(_bars(closes)) if e["side"] == +1]
    assert events, "강세 다이버전스가 나와야 한다"
    e = events[-1]
    assert e["k2_ms"] == k2 * STEP
    assert e["close_ms"] == (k2 + PIVOT) * STEP + STEP - 1  # k2 + 5 봉 마감에 확정
    assert float(str(e["rsi2"])) > float(str(e["rsi1"]))


def test_no_event_without_room_for_horizon() -> None:
    closes, _, k2 = _series_with_bull_divergence()
    cut = closes[: k2 + PIVOT + HORIZON]  # t + 16 이 구간 밖
    assert not [e for e in divergences(_bars(cut)) if e["k2_ms"] == k2 * STEP]


def test_thin_one_timeline_keeps_gap_of_horizon() -> None:
    rows = [(0, "A"), (0, "B"), (STEP, "A"), (HORIZON * STEP, "B"), (HORIZON * STEP + 1, "A")]
    kept = thin(rows)
    assert [c for c, _ in kept] == [0, HORIZON * STEP]
    assert len({s for c, s in kept if c == 0}) == 1
