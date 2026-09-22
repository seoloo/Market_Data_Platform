"""후보 전략의 트리거 정의 (docs/research/01-candidate-comparison.md §2).

빈도를 세는 쪽과 SD 를 재는 쪽이 **같은 정의**를 써야 한다. 정의가 갈라지면
"연 282회" 와 "그 282회의 SD" 가 서로 다른 사건을 가리키게 되고, 검정력 게이트는
숫자만 남은 채 조용히 무효가 된다. 그래서 두 스크립트가 여기를 함께 쓴다.

여기 임계값은 **사전등록이 아니다.** 자릿수 확인용 기본값이며, 후보가 선택되면
사전등록에서 다시 고정한다.

전부 causal 이다 — 시점 ``i`` 의 판정에 ``i`` 이후 봉을 쓰지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "BARS_PER_HOUR",
    "CASCADE_MOVE_SD",
    "CASCADE_OI_DROP",
    "CASCADE_WINDOW",
    "CONTRACTION_QUANTILE",
    "RANGE_BARS",
    "RANK_WINDOW_BARS",
    "TREND_BARS",
    "VOL_WINDOW_BARS",
    "Trigger",
    "breakout_triggers",
    "cascade_triggers",
    "contraction_flags",
    "rolling_vol",
]

BARS_PER_HOUR = 12

VOL_WINDOW_BARS = 24 * BARS_PER_HOUR
"""실현변동성을 재는 창 (24시간). 더 짧으면 5분 잡음이 지배한다."""

RANK_WINDOW_BARS = 30 * 24 * BARS_PER_HOUR
"""변동성 순위를 매기는 causal 창 (30일).

절대 임계값을 쓰지 않는 이유: BTC 변동성 수준은 연도별로 수 배 차이가 난다.
2021 의 '조용함' 과 2024 의 '조용함' 은 다른 값이다.
"""

CONTRACTION_QUANTILE = 0.30
TREND_BARS = 4 * BARS_PER_HOUR * 50
"""HTF 방향 판단 구간 — 4시간봉 50개 상당."""
RANGE_BARS = 6 * BARS_PER_HOUR
"""돌파를 재는 직전 레인지 (6시간)."""

CASCADE_MOVE_SD = 3.0
"""30분 가격 변화가 직전 변동성의 몇 배여야 '급변' 인가."""
CASCADE_OI_DROP = 0.005
"""같은 30분에 미결제약정이 이 비율 이상 **줄었는가** — 강제 청산의 흔적."""
CASCADE_WINDOW = 6
"""30분 = 5분봉 6개."""


@dataclass(frozen=True)
class Trigger:
    """진입 시점과 의도 방향.

    ``direction`` 은 +1 롱 / −1 숏이다. **선행수익 SD 계산에는 쓰지 않는다** —
    부호를 곱해도 분산은 사실상 같고(평균이 작으므로), 방향별 수익을 들여다보면
    사전등록 전에 효과를 엿보는 것이 된다.
    """

    index: int
    direction: int


def rolling_vol(closes: list[float]) -> list[float | None]:
    """직전 ``VOL_WINDOW_BARS`` 5분 수익률의 표준편차. causal."""
    out: list[float | None] = [None] * len(closes)
    if len(closes) <= VOL_WINDOW_BARS:
        return out
    steps = [0.0] * len(closes)
    for i in range(1, len(closes)):
        prev = closes[i - 1]
        steps[i] = (closes[i] - prev) / prev if prev > 0.0 else 0.0
    n = VOL_WINDOW_BARS
    total = sum(steps[1 : n + 1])
    total_sq = sum(v * v for v in steps[1 : n + 1])
    for i in range(n, len(closes)):
        out[i] = max(total_sq / n - (total / n) ** 2, 0.0) ** 0.5
        if i + 1 < len(closes):
            leaving, entering = steps[i - n + 1], steps[i + 1]
            total += entering - leaving
            total_sq += entering * entering - leaving * leaving
    return out


def contraction_flags(vol: list[float | None]) -> list[bool]:
    """각 시점의 변동성이 직전 30일 분포의 하위 분위인가. causal."""
    flags = [False] * len(vol)
    history: list[float] = []
    for i, value in enumerate(vol):
        if value is None:
            continue
        if len(history) >= RANK_WINDOW_BARS:
            recent = history[-RANK_WINDOW_BARS:]
            below = sum(1 for v in recent if v < value)
            flags[i] = below / len(recent) <= CONTRACTION_QUANTILE
        history.append(value)
    return flags


def breakout_triggers(closes: list[float], flags: list[bool]) -> list[Trigger]:
    """후보 A — 수축 국면에서 직전 레인지를 HTF 추세 방향으로 돌파.

    같은 돌파를 여러 번 세지 않도록 트리거 후 ``RANGE_BARS`` 동안 잠근다.

    추세는 **돌파 봉 직전**으로 판정한다. 돌파 봉 자신을 넣으면 큰 역방향 스파이크가
    추세 판정까지 뒤집어, "추세 방향 돌파" 가 아니라 "아무 급변" 을 잡게 된다.
    """
    out: list[Trigger] = []
    blocked_until = 0
    for i in range(max(TREND_BARS, RANGE_BARS) + 1, len(closes)):
        if i < blocked_until or not flags[i]:
            continue
        up = closes[i - 1] > closes[i - 1 - TREND_BARS]
        window = closes[i - RANGE_BARS : i]
        if not window:
            continue
        if closes[i] > max(window) if up else closes[i] < min(window):
            out.append(Trigger(index=i, direction=1 if up else -1))
            blocked_until = i + RANGE_BARS
    return out


def cascade_triggers(
    times: list[int],
    closes: list[float],
    vol: list[float | None],
    open_interest: dict[int, float],
) -> tuple[list[Trigger], int]:
    """후보 B — 급변과 **동시에** 미결제약정이 줄어든 시점. 방향은 급변의 반대.

    OI 가 줄지 않았다면 신규 투기이지 강제 청산이 아니다. 그 구분이 이 후보의
    전부이므로 두 조건을 모두 요구한다.

    돌려주는 두 번째 값은 **지표가 있어 판정 가능했던 봉 수**다. OI 스냅샷은
    2021 부터라 전체 봉 수로 빈도를 계산하면 과소평가된다.
    """
    out: list[Trigger] = []
    usable = 0
    blocked_until = 0
    for i in range(CASCADE_WINDOW, len(closes)):
        value = vol[i]
        if value is None or value <= 0.0:
            continue
        before = open_interest.get(times[i - CASCADE_WINDOW])
        now = open_interest.get(times[i])
        if before is None or now is None or before <= 0.0:
            continue
        usable += 1
        if i < blocked_until:
            continue
        prev_close = closes[i - CASCADE_WINDOW]
        if prev_close <= 0.0:
            continue
        change = (closes[i] - prev_close) / prev_close
        # 30분 변화를 5분 변동성과 견주려면 √6 배로 환산한다
        if abs(change) < CASCADE_MOVE_SD * value * CASCADE_WINDOW**0.5:
            continue
        if (now - before) / before > -CASCADE_OI_DROP:
            continue
        out.append(Trigger(index=i, direction=-1 if change > 0 else 1))
        blocked_until = i + CASCADE_WINDOW * 2
    return out, usable
