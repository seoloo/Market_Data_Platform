"""WA 결과를 보지 않는 EW-0 4h 후보 창 입력 가용성 집계."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from statistics import median

from auto_trading.core.errors import DataIntegrityError
from auto_trading.core.types import Candle
from auto_trading.features.structure import SwingKind
from auto_trading.research.elliott.h4_evidence import EvidenceStatus, FourHourEvidence
from auto_trading.research.elliott.pivots import closed_ew0_candles


@dataclass(frozen=True, slots=True)
class YearWindowCoverage:
    year: int
    bars_with_four_anchors: int
    bars_with_six_anchors: int


@dataclass(frozen=True, slots=True)
class WindowCoverage:
    four_hour_bars: int
    unknown_bars: int
    non_unknown_segments: int
    segment_anchor_count_median: float
    segment_anchor_count_max: int
    segments_reaching_four: int
    segments_reaching_six: int
    bars_with_two_anchors: int
    bars_with_four_anchors: int
    bars_with_six_anchors: int
    cross_unknown_candidates: int
    bars_blocked_by_cross_unknown: int
    by_calendar_year: tuple[YearWindowCoverage, ...]


def summarize_window_coverage(
    four_hour_candles: Sequence[Candle], evidence: Sequence[FourHourEvidence]
) -> WindowCoverage:
    """한 번의 시간순 통과로 창 입력의 가용성만 센다.

    같은 창이 여러 마감에 반복되므로 반환된 마감 수는 독립 표본 수가 아니다.
    """
    if not four_hour_candles:
        raise DataIntegrityError("4h 봉이 없다")
    closed = closed_ew0_candles(four_hour_candles, four_hour_candles[-1].close_ms)
    if len(evidence) != len(closed):
        raise DataIntegrityError("4h 봉과 증거 수가 다르다")
    bars_by_open_ms = {bar.open_ms: bar for bar in closed}
    unknown_bars = 0
    non_unknown_segments = 0
    segment_anchor_counts: list[int] = []
    segments_reaching_four = 0
    segments_reaching_six = 0
    bars_with_two = 0
    bars_with_four = 0
    bars_with_six = 0
    cross_unknown_candidates = 0
    blocked_bars = 0
    yearly: dict[int, list[int]] = defaultdict(lambda: [0, 0])
    last_unknown_close_ms: int | None = None
    segment_open = False
    segment_anchors = 0
    segment_invalid = False
    reached_four = False
    reached_six = False

    for bar, item in zip(closed, evidence, strict=True):
        if item.close_ms != bar.close_ms or not isinstance(item.status, EvidenceStatus):
            raise DataIntegrityError("4h 증거 시각·상태가 봉과 대응하지 않는다")
        year = datetime.fromtimestamp(bar.close_ms / 1000, tz=UTC).year
        yearly[year]
        if item.status is EvidenceStatus.UNKNOWN:
            if item.candidate is not None:
                raise DataIntegrityError("UNKNOWN에 후보가 들어 있다")
            unknown_bars += 1
            if segment_open:
                segment_anchor_counts.append(segment_anchors)
                segments_reaching_four += int(reached_four)
                segments_reaching_six += int(reached_six)
            segment_open = False
            segment_anchors = 0
            segment_invalid = False
            reached_four = reached_six = False
            last_unknown_close_ms = item.close_ms
            continue
        if not segment_open:
            non_unknown_segments += 1
            segment_open = True
        if item.status is EvidenceStatus.CANDIDATE:
            candidate = item.candidate
            if (
                candidate is None
                or not isinstance(candidate.kind, SwingKind)
                or candidate.confirmed_four_hour_close_ms != item.close_ms
            ):
                raise DataIntegrityError("후보 확정 시각이 4h 증거와 다르다")
            extreme_bar = bars_by_open_ms.get(candidate.extreme_four_hour_open_ms)
            if extreme_bar is None or extreme_bar.open_ms >= bar.open_ms:
                raise DataIntegrityError("후보 극값이 이전 완성 4h에 없다")
            if candidate.price != (
                extreme_bar.high if candidate.kind is SwingKind.HIGH else extreme_bar.low
            ):
                raise DataIntegrityError("후보 가격이 4h 극값과 다르다")
            if (
                last_unknown_close_ms is not None
                and candidate.extreme_four_hour_open_ms <= last_unknown_close_ms
            ):
                cross_unknown_candidates += 1
                segment_invalid = True
            segment_anchors += 1
        elif item.candidate is not None:
            raise DataIntegrityError("NO_EVENT에 후보가 들어 있다")

        if segment_invalid:
            blocked_bars += 1
            continue
        if segment_anchors >= 2:
            bars_with_two += 1
        if segment_anchors >= 4:
            bars_with_four += 1
            yearly[year][0] += 1
            reached_four = True
        if segment_anchors >= 6:
            bars_with_six += 1
            yearly[year][1] += 1
            reached_six = True

    if segment_open:
        segment_anchor_counts.append(segment_anchors)
        segments_reaching_four += int(reached_four)
        segments_reaching_six += int(reached_six)
    return WindowCoverage(
        four_hour_bars=len(closed),
        unknown_bars=unknown_bars,
        non_unknown_segments=non_unknown_segments,
        segment_anchor_count_median=float(median(segment_anchor_counts))
        if segment_anchor_counts
        else 0.0,
        segment_anchor_count_max=max(segment_anchor_counts, default=0),
        segments_reaching_four=segments_reaching_four,
        segments_reaching_six=segments_reaching_six,
        bars_with_two_anchors=bars_with_two,
        bars_with_four_anchors=bars_with_four,
        bars_with_six_anchors=bars_with_six,
        cross_unknown_candidates=cross_unknown_candidates,
        bars_blocked_by_cross_unknown=blocked_bars,
        by_calendar_year=tuple(
            YearWindowCoverage(year, counts[0], counts[1])
            for year, counts in sorted(yearly.items())
        ),
    )
