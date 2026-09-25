"""EW-3d′ 인과적 스캐너 — 문서 34 §3 (순수 함수, 수익률을 계산하지 않는다).

모든 4h 마감 t 에서 t 까지 알려진 1m 경로 사건만으로 1h 꼭짓점 열을 갱신하고, 새 4h 가시
꼭짓점 E 가 구간 끝에 들어오면 E 로 끝나는 모든 해석을 모아 1순위를 정한다.
꼭짓점 제거·구간 끊김은 알려진 시점에만 반영한다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from auto_trading.core.enums import Timeframe
from auto_trading.core.types import Candle
from auto_trading.features.structure import SwingKind
from auto_trading.research.elliott.minute_path import MinutePathReplay
from auto_trading.research.elliott.parser import (
    FinePivot,
    Label,
    Parse,
    WaveParser,
    as_grid_anchor,
    counted_at_four_hour,
)
from auto_trading.research.elliott.ranking import RankKey, rank_key

__all__ = [
    "LOOKBACK_BARS",
    "SCENARIO_SET_SIZE",
    "ScenarioList",
    "Trigger",
    "TriggerResult",
    "causal_triggers",
    "evaluate_trigger",
    "evaluate_triggers",
    "scenario_list",
    "scenario_lists",
    "trigger_candidates",
]

LOOKBACK_BARS = 360
"""시작 후보는 E 의 4h 봉에서 360 봉(60일) 이내 (문서 34 §3)."""
SCENARIO_SET_SIZE = 50
"""JEV 에 넘길 시나리오 묶음 크기 — 연구용 기본값, 최종 아님 (문서 39 §6)."""


@dataclass(frozen=True, slots=True)
class Trigger:
    close_ms: int
    """발동 4h 마감 (= E 의 가용 시각)."""
    segment: tuple[FinePivot, ...]
    """t 시점의 현재 구간 꼭짓점 열. 마지막이 E."""
    direction: int
    """+1: 상승이 E(고점)에서 끝남, −1: 하락이 E(저점)에서 끝남."""


@dataclass(frozen=True, slots=True)
class TriggerResult:
    close_ms: int
    direction: int
    is_event: bool
    top_patterns: tuple[str, ...]
    top_bounds: tuple[tuple[int, ...], ...]
    candidates: int
    starts: int


def _extreme(bar: Candle, kind: SwingKind) -> object:
    return bar.high if kind is SwingKind.HIGH else bar.low


def causal_triggers(
    replay: MinutePathReplay,
    hour_bars: Sequence[Candle],
    four_hour_bars: Sequence[Candle],
    *,
    all_new_pivots: bool = False,
) -> list[Trigger]:
    """33 §26 의 1h 꼭짓점 규칙을 사건이 알려지는 순서대로 적용하고 발동 시점을 모은다.

    기본(EW-3d′)은 한 4h 마감에서 **마지막** 꼭짓점이 4h 가시일 때만 발동한다.
    ``all_new_pivots`` (문서 58 EW-3e 후보): 그 마감에 새로 확정된 4h 가시 꼭짓점
    **각각**에서 발동한다 — 고점과 뒤따른 작은 저점이 같은 마감에 확정되면
    고점으로 끝나는 해석이 평가되지 않던 문제.
    모두 그 마감에 알려진 정보라 인과성은 같다.
    """
    hours = {bar.open_ms: bar for bar in hour_bars}
    fours = {bar.open_ms: bar for bar in four_hour_bars}
    events = sorted(
        ((s, k, e) for s, seg in enumerate(replay.segments) for k, e in enumerate(seg)),
        key=lambda x: (x[2].available_at_ms, x[0], x[1]),
    )
    current: list[FinePivot] = []
    current_path_segment = -1
    triggers: list[Trigger] = []
    index = 0
    while index < len(events):
        close_ms = events[index][2].available_at_ms
        while index < len(events) and events[index][2].available_at_ms == close_ms:
            path_segment, _, event = events[index]
            index += 1
            if path_segment != current_path_segment:
                current = []
                current_path_segment = path_segment
            hour_open = Timeframe.H1.open_ms_of(event.extreme_minute_ms)
            four_open = Timeframe.H4.open_ms_of(event.extreme_minute_ms)
            hour, four = hours.get(hour_open), fours.get(four_open)
            if hour is None or four is None or event.price != _extreme(hour, event.kind):
                current = []
                continue
            if current and current[-1].hour_open_ms == hour_open:
                current = []
                continue
            current.append(
                FinePivot(
                    kind=event.kind,
                    price=event.price,
                    hour_open_ms=hour_open,
                    four_hour_open_ms=four_open,
                    available_at_ms=event.available_at_ms,
                    four_hour_visible=event.price == _extreme(four, event.kind),
                )
            )
        if all_new_pivots:
            for k, pivot in enumerate(current):
                if pivot.available_at_ms == close_ms and pivot.four_hour_visible:
                    direction = 1 if pivot.kind is SwingKind.HIGH else -1
                    triggers.append(Trigger(close_ms, tuple(current[: k + 1]), direction))
        elif current and current[-1].available_at_ms == close_ms and current[-1].four_hour_visible:
            last = current[-1]
            triggers.append(
                Trigger(close_ms, tuple(current), 1 if last.kind is SwingKind.HIGH else -1)
            )
    return triggers


def trigger_candidates(
    trigger: Trigger,
    hour_bars: Sequence[Candle],
    *,
    parser: WaveParser | None = None,
) -> tuple[list[Parse], int]:
    """E 로 끝나는 모든 시작 후보의 EW-3d′ 후보 전부와 시작 후보 수 (문서 34 §3 3~4)."""
    fine = trigger.segment
    j = len(fine) - 1
    end = fine[j]
    if parser is None:
        closed = [bar for bar in hour_bars if bar.close_ms <= trigger.close_ms]
        parser = WaveParser([as_grid_anchor(p) for p in fine], closed)
    starts = [
        i
        for i in range(j - 3, -1, -2)
        if fine[i].four_hour_visible
        and (end.four_hour_open_ms - fine[i].four_hour_open_ms) // Timeframe.H4.ms <= LOOKBACK_BARS
        and parser.relaxed_span(i, j)
    ]
    candidates = [
        c
        for i in starts
        for c in parser.top_candidates(i, j, relaxed=True)
        if counted_at_four_hour(c, fine)
    ]
    return candidates, len(starts)


@dataclass(frozen=True, slots=True)
class ScenarioList:
    """발동 하나의 시나리오 묶음 (문서 39): 순위순 상위 ``size`` 개와 목록 밖 후보 수."""

    ranked: tuple[tuple[Parse, RankKey], ...]
    size: int
    total: int

    @property
    def top(self) -> tuple[tuple[Parse, RankKey], ...]:
        return self.ranked[: self.size]

    @property
    def outside(self) -> int:
        return max(0, self.total - self.size)

    def pessimistic_rank(self, parse: Parse) -> int | None:
        key = next((k for c, k in self.ranked if c == parse), None)
        if key is None:
            return None
        return 1 + sum(1 for c, k in self.ranked if c != parse and k <= key)


def scenario_list(
    trigger: Trigger,
    hour_bars: Sequence[Candle],
    *,
    size: int = SCENARIO_SET_SIZE,
    parser: WaveParser | None = None,
) -> ScenarioList:
    """모든 시작 후보의 후보를 ``rank_key`` 로 정렬한 시나리오 묶음. 확률이 아니다."""
    candidates, _ = trigger_candidates(trigger, hour_bars, parser=parser)
    ranked = sorted(
        ((c, rank_key(c, trigger.segment)) for c in candidates),
        key=lambda item: (item[1], item[0].pattern.value, item[0].bounds),
    )
    return ScenarioList(tuple(ranked), size, len(ranked))


def evaluate_trigger(
    trigger: Trigger,
    hour_bars: Sequence[Candle],
    *,
    parser: WaveParser | None = None,
) -> TriggerResult:
    """E 로 끝나는 모든 시작 후보의 해석을 한 목록에서 순위 매긴다 (문서 34 §3 4~5).

    ``parser`` 는 같은 구간의 더 긴 꼭짓점 열로 만든 파서를 재사용할 때 넘긴다.
    E 까지의 접두가 같아야 한다 — :func:`evaluate_triggers` 가 그렇게 묶는다.
    """
    fine = trigger.segment
    candidates, starts = trigger_candidates(trigger, hour_bars, parser=parser)
    if not candidates:
        return TriggerResult(trigger.close_ms, trigger.direction, False, (), (), 0, starts)
    keys = [rank_key(c, fine) for c in candidates]
    best = min(keys)
    top = [c for c, k in zip(candidates, keys, strict=True) if k == best]
    patterns = tuple(sorted({c.pattern.value for c in top}))
    return TriggerResult(
        close_ms=trigger.close_ms,
        direction=trigger.direction,
        is_event=patterns == (Label.IMPULSE.value,),
        top_patterns=patterns,
        top_bounds=tuple(c.bounds for c in top),
        candidates=len(candidates),
        starts=starts,
    )


def evaluate_triggers(
    triggers: Sequence[Trigger], hour_bars: Sequence[Candle]
) -> list[TriggerResult]:
    """같은 첫 꼭짓점으로 시작하는 발동은 서로의 접두다.

    가장 긴 열로 만든 파서 하나를 함께 써서 기억(메모)을 나눈다.
    """
    groups: dict[tuple[int, int], list[Trigger]] = {}
    for trig in triggers:
        head = trig.segment[0]
        groups.setdefault((head.hour_open_ms, head.available_at_ms), []).append(trig)
    results: dict[int, TriggerResult] = {}
    order = {id(trig): n for n, trig in enumerate(triggers)}
    for members in groups.values():
        longest = max(members, key=lambda trig: len(trig.segment))
        for trig in members:
            if trig.segment != longest.segment[: len(trig.segment)]:
                raise AssertionError("같은 구간의 발동이 접두 관계가 아니다")
        parser = WaveParser([as_grid_anchor(p) for p in longest.segment], list(hour_bars))
        for trig in members:
            results[order[id(trig)]] = evaluate_trigger(trig, hour_bars, parser=parser)
    return [results[n] for n in range(len(triggers))]


def scenario_lists(
    triggers: Sequence[Trigger], hour_bars: Sequence[Candle], *, size: int = SCENARIO_SET_SIZE
) -> list[ScenarioList]:
    """:func:`evaluate_triggers` 와 같이 구간별 파서를 재사용해 발동마다 시나리오 묶음을 만든다."""
    groups: dict[tuple[int, int], list[int]] = {}
    for n, trig in enumerate(triggers):
        head = trig.segment[0]
        groups.setdefault((head.hour_open_ms, head.available_at_ms), []).append(n)
    out: dict[int, ScenarioList] = {}
    for members in groups.values():
        longest = max((triggers[n] for n in members), key=lambda trig: len(trig.segment))
        parser = WaveParser([as_grid_anchor(p) for p in longest.segment], list(hour_bars))
        for n in members:
            out[n] = scenario_list(triggers[n], hour_bars, size=size, parser=parser)
    return [out[n] for n in range(len(triggers))]
