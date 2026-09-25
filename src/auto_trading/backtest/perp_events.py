"""BTCUSDT 무기한 포지셔닝 이벤트 — 탐지와 거래 결과 (docs/research/48). 순수 함수, I/O 없음.

두 가설의 이벤트를 5분봉 격자에서 찾는다.

* **FB (실패한 돌파 + OI 증가 + funding 과열):** 24시간 고점을 새로 넘는 5분 종가(돌파).
  그 1시간 OI 증가가 과거 30일의 상위 20%, 마지막 정산 funding 이 과거 90일의 상위 20%이고 양수.
  6시간 안에 종가가 돌파 기준선 아래로 되돌아오면 **다음 봉 시가에 숏**.
  아래쪽은 대칭(funding 하위 20%, 롱). 스톱은 돌파 뒤 극값(숏이면 돌파~진입 사이 최고가).
* **LQ (대규모 청산 추정 + OI 급감):** 1시간 OI 변화가 과거 30일의 하위 1%이고
  1시간 가격 변화의 크기가 과거 30일의 상위 1% → 강제 청산이 몰린 시간으로 본다.
  다음 봉 시가에 진입. 방향은 판정에서 정한다(``direction=-1`` 이면 급변의 반대 = 반전,
  ``+1`` 이면 같은 방향 = 지속). 스톱은 그 1시간의 극값.

**미래 자료 없음:** 백분위 기준은 그날 0시 이전 창으로만 매일 한 번 정한다.
OI 는 스냅숏 시각이 봉 마감 이하인 값만, funding 은 봉 마감 이전에 정산된 값만 쓴다.
이벤트는 봉 ``i`` 마감까지의 자료로 정하고 ``i+1`` 봉 시가에 들어간다.

겹침 방지: 한 가설 안에서 거래가 끝나기 전에는 새 이벤트를 받지 않는다.
LQ 는 이벤트 뒤 24시간 추가로 쉰다.
"""

from __future__ import annotations

import bisect
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass

__all__ = [
    "BAR_MS",
    "FB_DEFAULT",
    "LQ_DEFAULT",
    "Bars",
    "EventTrade",
    "FbConfig",
    "LqConfig",
    "PerpEvent",
    "daily_thresholds",
    "detect_fb",
    "detect_lq",
    "prior_extremes",
    "settle_trade",
]

BAR_MS = 300_000
DAY_MS = 86_400_000
_BARS_PER_HOUR = 12
_BARS_PER_DAY = 288


@dataclass(frozen=True, slots=True)
class Bars:
    """빈틈 없는 5분봉 격자. ``oi[i]`` 는 봉 ``i`` 마감 시각 스냅숏(없으면 None),
    ``funding[i]`` 는 봉 ``i`` 마감 전에 마지막으로 정산된 funding (없으면 None)."""

    open_ms: Sequence[int]
    open: Sequence[float]
    high: Sequence[float]
    low: Sequence[float]
    close: Sequence[float]
    oi: Sequence[float | None]
    funding: Sequence[float | None]

    def __post_init__(self) -> None:
        n = len(self.open_ms)
        for name in ("open", "high", "low", "close", "oi", "funding"):
            if len(getattr(self, name)) != n:
                raise ValueError(f"길이가 다르다: {name}")
        for a, b in zip(self.open_ms, self.open_ms[1:], strict=False):
            if b - a != BAR_MS:
                raise ValueError(f"5분 격자에 빈틈: {a} → {b}")


@dataclass(frozen=True, slots=True)
class PerpEvent:
    hypothesis: str
    signal_index: int
    """이벤트를 확정한 봉 (마감까지의 자료)."""
    entry_index: int
    """진입 봉 (시가 체결)."""
    side: int
    """FB: 가설 방향(+1 롱 / −1 숏). LQ: 급변 방향(+1 급등 / −1 급락)."""
    extreme_high: float
    """구조 극값 — 숏의 스톱 (FB: 돌파~확정 봉 최고가, LQ: 급변 1시간 최고가)."""
    extreme_low: float
    """구조 극값 — 롱의 스톱 (FB: 돌파~확정 봉 최저가, LQ: 급변 1시간 최저가)."""


@dataclass(frozen=True, slots=True)
class EventTrade:
    entry_ms: int
    exit_ms: int
    direction: int
    entry: float
    exit: float
    gross: float
    funding: float
    cost: float
    stopped: bool

    @property
    def net(self) -> float:
        return self.gross + self.funding - self.cost


def _percentile(sorted_values: Sequence[float], q: float) -> float:
    """선형 보간 백분위 (0 ≤ q ≤ 1)."""
    pos = q * (len(sorted_values) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(sorted_values) - 1)
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (pos - lo)


def daily_thresholds(
    times_ms: Sequence[int],
    values: Sequence[float | None],
    *,
    window_days: int,
    q: float,
    min_coverage: float,
    expected_per_day: float,
) -> dict[int, float]:
    """UTC 날짜(자정 ms) → 그 이전 ``window_days`` 일 값들의 ``q`` 백분위.

    ``times_ms`` 는 오름차순. 창 안 값이 기대 개수의 ``min_coverage`` 미만이면 그날 기준이 없다.
    """
    if not times_ms:
        return {}
    pairs = [(t, v) for t, v in zip(times_ms, values, strict=True) if v is not None]
    ts = [t for t, _ in pairs]
    vs = [v for _, v in pairs]
    first_day = times_ms[0] // DAY_MS * DAY_MS
    last_day = times_ms[-1] // DAY_MS * DAY_MS
    need = min_coverage * expected_per_day * window_days
    out: dict[int, float] = {}
    day = first_day
    while day <= last_day:
        lo = bisect.bisect_left(ts, day - window_days * DAY_MS)
        hi = bisect.bisect_left(ts, day)
        if hi - lo >= need:
            out[day] = _percentile(sorted(vs[lo:hi]), q)
        day += DAY_MS
    return out


def _day(ms: int) -> int:
    return ms // DAY_MS * DAY_MS


def _oi_change(bars: Bars, i: int) -> float | None:
    if i < _BARS_PER_HOUR:
        return None
    a, b = bars.oi[i - _BARS_PER_HOUR], bars.oi[i]
    if a is None or b is None or a <= 0:
        return None
    return b / a - 1


def oi_changes(bars: Bars) -> list[float | None]:
    return [_oi_change(bars, i) for i in range(len(bars.open_ms))]


def abs_returns(bars: Bars) -> list[float | None]:
    return [
        None if i < _BARS_PER_HOUR else abs(bars.close[i] / bars.close[i - _BARS_PER_HOUR] - 1)
        for i in range(len(bars.open_ms))
    ]


@dataclass(frozen=True, slots=True)
class FbConfig:
    oi_q: float = 0.80
    funding_hi_q: float = 0.80
    funding_lo_q: float = 0.20
    fail_bars: int = 72
    """돌파 뒤 되돌아옴을 기다리는 최대 봉 수 (6시간)."""
    hold_bars: int = 144
    """진입 뒤 보유 봉 수 (12시간)."""


@dataclass(frozen=True, slots=True)
class LqConfig:
    oi_q: float = 0.01
    ret_q: float = 0.99
    hold_bars: int = 144
    cooldown_bars: int = _BARS_PER_DAY


FB_DEFAULT = FbConfig()
"""48 §3 에 고정한 FB 설정."""
LQ_DEFAULT = LqConfig()
"""48 §3 에 고정한 LQ 설정."""


def prior_extremes(values: Sequence[float], window: int, *, high: bool) -> list[float | None]:
    """``out[i]`` = ``values[i-window:i]`` 의 최댓값(``high``) 또는 최솟값. 앞부분은 None. O(n)."""
    out: list[float | None] = [None] * len(values)
    q: deque[int] = deque()
    for i, v in enumerate(values):
        if i >= window:
            out[i] = values[q[0]]
        # i 를 넣기 전에 창에서 벗어날 인덱스를 뺀다 (다음 i+1 의 창은 [i+1-window, i])
        while q and q[0] <= i - window:
            q.popleft()
        while q and ((values[q[-1]] <= v) if high else (values[q[-1]] >= v)):
            q.pop()
        q.append(i)
    return out


def detect_fb(
    bars: Bars, settlements: Sequence[tuple[int, float]], cfg: FbConfig = FB_DEFAULT
) -> list[PerpEvent]:
    """실패한 돌파 이벤트. 진입 뒤 ``hold_bars`` 동안은 새 이벤트를 받지 않는다(거래 겹침 방지).

    ``settlements`` 는 (정산 시각, funding). 과열 기준(90일 백분위)은 정산값 자체의 분포로 정한다.
    """
    n = len(bars.open_ms)
    close_ms = [t + BAR_MS for t in bars.open_ms]
    d_oi = oi_changes(bars)
    oi_thr = daily_thresholds(
        close_ms, d_oi, window_days=30, q=cfg.oi_q, min_coverage=0.8, expected_per_day=288
    )
    hi24 = prior_extremes(bars.high, _BARS_PER_DAY, high=True)
    lo24 = prior_extremes(bars.low, _BARS_PER_DAY, high=False)
    f_times = [t for t, _ in settlements]
    f_vals: list[float | None] = [v for _, v in settlements]
    f_hi = daily_thresholds(
        f_times, f_vals, window_days=90, q=cfg.funding_hi_q, min_coverage=0.8, expected_per_day=3
    )
    f_lo = daily_thresholds(
        f_times, f_vals, window_days=90, q=cfg.funding_lo_q, min_coverage=0.8, expected_per_day=3
    )
    events: list[PerpEvent] = []
    busy_until = -1
    i = _BARS_PER_DAY + 1
    while i < n - 1:
        if i <= busy_until:
            i += 1
            continue
        day = _day(close_ms[i])
        h, lo, ph, pl = hi24[i], lo24[i], hi24[i - 1], lo24[i - 1]
        if h is None or lo is None or ph is None or pl is None:
            i += 1
            continue
        side = 0
        level = 0.0
        if bars.close[i] > h and bars.close[i - 1] <= ph:
            side, level = -1, h  # 위로 돌파 → 실패하면 숏
        elif bars.close[i] < lo and bars.close[i - 1] >= pl:
            side, level = 1, lo  # 아래로 이탈 → 실패하면 롱
        if side == 0:
            i += 1
            continue
        doi, f = d_oi[i], bars.funding[i]
        thr = oi_thr.get(day)
        f_thr = (f_hi if side == -1 else f_lo).get(day)
        crowded = (
            doi is not None
            and thr is not None
            and doi >= thr
            and f is not None
            and f_thr is not None
            and ((side == -1 and f >= f_thr and f > 0) or (side == 1 and f <= f_thr))
        )
        if not crowded:
            i += 1
            continue
        fail = next(
            (
                j
                for j in range(i + 1, min(i + cfg.fail_bars, n - 2) + 1)
                if (side == -1 and bars.close[j] < level) or (side == 1 and bars.close[j] > level)
            ),
            None,
        )
        if fail is None:
            i += 1
            continue
        events.append(
            PerpEvent(
                "FB",
                fail,
                fail + 1,
                side,
                max(bars.high[i : fail + 1]),
                min(bars.low[i : fail + 1]),
            )
        )
        busy_until = fail + cfg.hold_bars
        i = fail + 1
    return events


def detect_lq(bars: Bars, cfg: LqConfig = LQ_DEFAULT) -> list[PerpEvent]:
    """청산 추정 이벤트. ``side`` 는 급변 방향이다(거래 방향은 판정 규칙이 정한다)."""
    n = len(bars.open_ms)
    close_ms = [t + BAR_MS for t in bars.open_ms]
    d_oi = oi_changes(bars)
    ar = abs_returns(bars)
    oi_thr = daily_thresholds(
        close_ms, d_oi, window_days=30, q=cfg.oi_q, min_coverage=0.8, expected_per_day=288
    )
    r_thr = daily_thresholds(
        close_ms, ar, window_days=30, q=cfg.ret_q, min_coverage=0.8, expected_per_day=288
    )
    events: list[PerpEvent] = []
    busy_until = -1
    for i in range(_BARS_PER_HOUR, n - 1):
        if i <= busy_until:
            continue
        day = _day(close_ms[i])
        doi, r = d_oi[i], ar[i]
        a, b = oi_thr.get(day), r_thr.get(day)
        if doi is None or r is None or a is None or b is None:
            continue
        if doi <= a and doi < 0 and r >= b:
            side = 1 if bars.close[i] > bars.close[i - _BARS_PER_HOUR] else -1
            window = slice(i - _BARS_PER_HOUR + 1, i + 1)
            events.append(
                PerpEvent("LQ", i, i + 1, side, max(bars.high[window]), min(bars.low[window]))
            )
            busy_until = i + max(cfg.hold_bars, cfg.cooldown_bars)
    return events


def settle_trade(
    bars: Bars,
    event: PerpEvent,
    *,
    direction: int,
    hold_bars: int,
    cost_per_side: float,
    settlements: Sequence[tuple[int, float]],
    use_stop: bool,
) -> EventTrade | None:
    """진입 봉 시가에 ``direction`` 으로 들어가 ``hold_bars`` 뒤 마감가(또는 스톱)에 나온다.

    스톱(``use_stop``)은 구조 극값이다 — 롱은 ``extreme_low``, 숏은 ``extreme_high``.
    봉 안에서 닿으면 그 가격, 시가가 이미 넘었으면 시가에 체결(갭은 더 불리).
    진입 봉 시가가 이미 스톱 너머면(스톱가가 진입가보다 유리한 쪽) 스톱을 쓰지 않는다.
    funding: 보유 중 정산마다 롱은 양수 funding 을 내고 숏은 받는다(명목 대비 비율).
    자료가 모자라면 None.
    """
    e = event.entry_index
    last = e + hold_bars - 1
    if last >= len(bars.open_ms):
        return None
    entry = bars.open[e]
    stop = event.extreme_low if direction == 1 else event.extreme_high
    stop_on = use_stop and ((direction == 1 and stop < entry) or (direction == -1 and stop > entry))
    exit_price, exit_i, stopped = bars.close[last], last, False
    if stop_on:
        for j in range(e, last + 1):
            if direction == 1 and bars.low[j] <= stop:
                exit_price, exit_i, stopped = min(stop, bars.open[j]), j, True
                break
            if direction == -1 and bars.high[j] >= stop:
                exit_price, exit_i, stopped = max(stop, bars.open[j]), j, True
                break
    entry_ms = bars.open_ms[e]
    exit_ms = bars.open_ms[exit_i] + BAR_MS
    times = [t for t, _ in settlements]
    lo = bisect.bisect_right(times, entry_ms)
    hi = bisect.bisect_right(times, exit_ms)
    funding = -direction * sum(r for _, r in settlements[lo:hi])
    gross = direction * (exit_price / entry - 1)
    return EventTrade(
        entry_ms, exit_ms, direction, entry, exit_price, gross, funding, 2 * cost_per_side, stopped
    )
