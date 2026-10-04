"""문서 122 점수 함수 — 52 주 신고가 근처 · 12-1 모멘텀 (미래 자료를 쓰지 않는다)."""

from __future__ import annotations

from scripts.run_classic_momentum import LOOK, SKIP, score_52wh, score_mom


def test_scores_use_only_past_and_skip_last_month() -> None:
    xs: list[float | None] = [100.0 + i for i in range(LOOK + 5)]
    t = LOOK + 2
    assert score_52wh(xs, t) == 1.0  # 오늘이 최고가
    xs2 = list(xs)
    xs2[t - 3] = 1_000.0  # 3 일 전 고점
    assert abs(score_52wh(xs2, t) - xs2[t] / 1_000.0) < 1e-12  # type: ignore[operator]
    assert score_mom(xs, t) == xs[t - SKIP] / xs[t - LOOK] - 1  # type: ignore[operator]
    xs3 = list(xs)
    xs3[t] = 1.0  # 오늘 폭락은 12-1 모멘텀에 들어가지 않는다
    assert score_mom(xs3, t) == score_mom(xs, t)
    assert score_mom(xs, LOOK - 1) is None and score_52wh(xs, LOOK - 1) is None
