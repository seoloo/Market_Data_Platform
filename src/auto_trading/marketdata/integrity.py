"""시장 데이터 무결성 검증 — 빈 봉 · 재집계 불일치 · 이상한 가격과 거래량을 찾아 보고서로 모은다.

언제 쓰나: ``scripts/backfill.py`` 가 백필 뒤 검사할 때 (사람이 직접 실행) — 연구에 쓰기 전 관문.
읽고 쓰는 것: ``candles`` 표 (읽기만). 결과는 :class:`IntegrityReport` 로 돌려줄 뿐 저장하지 않는다.

게이트 1 (10-roadmap.md) 의 마지막 항목이다:

* 전 타임프레임 캔들이 갭 없이 적재되었는가 (갭은 **설명 가능**해야 한다)
* 1m → 5m/15m/1h 재집계 결과가 거래소 원본과 **일치**하는가
* 비정상 가격·거래량이 섞여 있지 않은가

**잘못된 시장 데이터는 09-failure-modes.md F9 의 상위 위험이다.**
지표가 폭주해 비정상 사이즈 주문이 나가거나 스톱이 오발동할 수 있다.

수백만 행을 한 번에 메모리에 올리지 않도록 시간 창 단위로 나눠 처리한다.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum

from auto_trading.core.enums import Timeframe
from auto_trading.core.money import ZERO, safe_div
from auto_trading.core.types import Candle
from auto_trading.marketdata.resample import bucket_candles, resample_candles
from auto_trading.storage.repositories.candles import CandleRepository

__all__ = [
    "IntegrityChecker",
    "IntegrityIssue",
    "IntegrityReport",
    "IntegrityThresholds",
    "IssueKind",
    "Severity",
]

logger = logging.getLogger(__name__)

_DAY_MS = 86_400_000


class IssueKind(StrEnum):
    """찾은 문제의 종류."""

    GAP = "gap"
    RESAMPLE_MISMATCH = "resample_mismatch"
    """원인이 규명되지 않은 불일치. 우리 집계 로직이나 데이터 사본의 문제를 뜻한다."""
    SOURCE_FILLER_BAR = "source_filler_bar"
    """1m 아카이브의 거래량 0 채움봉 때문에 생긴 불일치 (거래소 데이터 특성)."""
    ARCHIVE_DISAGREEMENT = "archive_disagreement"
    """거래소의 두 아카이브(1m vs 상위TF)가 서로 다른 거래량을 보고함."""
    PRICE_SPIKE = "price_spike"
    FLAT_PRICE = "flat_price"
    ZERO_VOLUME = "zero_volume"
    NO_DATA = "no_data"
    MISSING_SOURCE = "missing_source"


class Severity(StrEnum):
    """심각도 — ``critical`` 이 하나라도 있으면 보고서가 통과하지 못한다."""

    INFO = "info"
    WARN = "warn"
    CRITICAL = "critical"


@dataclass(frozen=True, slots=True)
class IntegrityIssue:
    """문제 하나 (종류 · 심각도 · 타임프레임 · 구간 · 사람이 읽는 설명)."""

    kind: IssueKind
    severity: Severity
    timeframe: Timeframe
    from_ms: int
    to_ms: int
    detail: str

    def __str__(self) -> str:
        return f"[{self.severity.value}] {self.kind.value} {self.timeframe.value}: {self.detail}"


@dataclass(frozen=True, slots=True)
class IntegrityThresholds:
    """검증 임계값. 전부 설정 가능하며 임의의 '최적값'이 아니다."""

    #: 인접 봉 종가 변화율이 이보다 크면 스파이크로 본다 (1분 기준)
    price_spike_pct: Decimal = Decimal("0.10")
    #: 같은 종가가 이 개수 이상 연속되면 데이터가 멈춘 것으로 본다
    flat_price_bars: int = 60
    #: 거래량 0 인 봉이 이 개수 이상 연속되면 보고한다
    zero_volume_bars: int = 30
    #: 갭이 이 시간을 넘으면 critical (그 이하는 warn)
    critical_gap_ms: int = 4 * 3_600_000
    #: 한 번에 읽어들일 시간 창
    chunk_days: int = 30
    #: 재집계 대조에서 보고할 최대 불일치 건수 (로그 폭발 방지)
    max_reported_mismatches: int = 20


@dataclass(slots=True)
class IntegrityReport:
    """검증 결과. 문제를 예외로 던지지 않고 모아서 돌려준다.

    첫 번째 문제에서 멈추면 데이터 상태 전체를 파악할 수 없다.
    """

    symbol: str
    issues: list[IntegrityIssue] = field(default_factory=list)
    checked: dict[str, int] = field(default_factory=dict)

    def add(self, issue: IntegrityIssue) -> None:
        """[쓰임] 문제 하나를 보고서에 더한다."""
        self.issues.append(issue)

    @property
    def ok(self) -> bool:
        """[쓰임] critical 이 없으면 통과.
        [이유] warn 은 사람이 판단한다 (예: 거래소 점검으로 실제로 봉이 없는 갭)."""
        return not any(issue.severity is Severity.CRITICAL for issue in self.issues)

    @property
    def critical(self) -> list[IntegrityIssue]:
        """[쓰임] critical 문제만 모아서."""
        return [issue for issue in self.issues if issue.severity is Severity.CRITICAL]

    def by_kind(self, kind: IssueKind) -> list[IntegrityIssue]:
        """[쓰임] 한 종류의 문제만 모아서."""
        return [issue for issue in self.issues if issue.kind is kind]

    def summary(self) -> str:
        """[쓰임] 사람이 읽는 한 줄 요약 — 문제가 없으면 검사한 봉 수, 있으면 종류별 건수."""
        if not self.issues:
            checked = ", ".join(
                f"{checked_name}={bar_count:,}봉"
                for checked_name, bar_count in sorted(self.checked.items())
            )
            return f"[OK] {self.symbol}: 문제 없음 ({checked})"
        count_by_kind: dict[str, int] = {}
        for issue in self.issues:
            count_by_kind[issue.kind.value] = count_by_kind.get(issue.kind.value, 0) + 1
        detail = ", ".join(
            f"{kind_name} {issue_count}건"
            for kind_name, issue_count in sorted(count_by_kind.items())
        )
        status = "OK(warn)" if self.ok else f"CRITICAL {len(self.critical)}건"
        return f"[{status}] {self.symbol}: {detail}"


class IntegrityChecker:
    """캔들 데이터 무결성 검증기 — 갭 · 재집계 대조 · 이상치 세 가지 검사를 한다."""

    __slots__ = ("_repo", "thresholds")

    def __init__(
        self,
        repository: CandleRepository,
        *,
        thresholds: IntegrityThresholds | None = None,
    ) -> None:
        self._repo = repository
        self.thresholds = thresholds or IntegrityThresholds()

    # ------------------------------------------------------------- 갭

    def check_gaps(
        self,
        symbol: str,
        timeframe: Timeframe,
        report: IntegrityReport,
        *,
        from_ms: int | None = None,
        to_ms: int | None = None,
    ) -> None:
        """[쓰임] 빠진 봉 구간을 찾아 보고서에 더한다 — 4 시간 이상이면 critical, 짧으면 warn.
        [이유] 갭 자체가 곧 실패는 아니다 — 거래소 점검으로 실제로 봉이 없을 수 있다. 긴 갭만
               critical 로 올리고, 나머지는 사람이 판단하도록 warn 으로 남긴다."""
        gaps = self._repo.find_gaps(symbol, timeframe, from_ms=from_ms, to_ms=to_ms)
        for gap in gaps:
            span_ms = gap.missing_to_ms - gap.missing_from_ms + timeframe.ms
            severity = (
                Severity.CRITICAL if span_ms >= self.thresholds.critical_gap_ms else Severity.WARN
            )
            report.add(
                IntegrityIssue(
                    kind=IssueKind.GAP,
                    severity=severity,
                    timeframe=timeframe,
                    from_ms=gap.missing_from_ms,
                    to_ms=gap.missing_to_ms,
                    detail=f"{gap.bar_count}봉 결측 ({span_ms / 3_600_000:.1f}시간)",
                )
            )

    # ------------------------------------------------------------- 재집계 대조

    def check_resample(
        self,
        symbol: str,
        target: Timeframe,
        report: IntegrityReport,
        *,
        source: Timeframe = Timeframe.M1,
        from_ms: int | None = None,
        to_ms: int | None = None,
    ) -> None:
        """[쓰임] 1m 을 합친 값과 거래소가 준 상위 봉(``target``)을 봉마다 대조하고, 다른 곳은
                  원인까지 적는다.
        [이유] 둘 다 같은 체결 스트림에서 나오므로 **정확히 일치해야 한다.** 원인이 설명되지 않는
               불일치는 데이터 오염이거나 우리 집계 로직 버그라는 뜻이라 critical 로 올린다."""
        # 1 단계: 대조할 원본 상위 봉 · 1m 봉 읽기 (없으면 경고만 남기고 끝)
        target_rows = self._repo.get_range(symbol, target, from_ms=from_ms, to_ms=to_ms)
        stored = {candle.open_ms: candle for candle in target_rows}
        if not stored:
            report.add(
                IntegrityIssue(
                    kind=IssueKind.NO_DATA,
                    severity=Severity.WARN,
                    timeframe=target,
                    from_ms=from_ms or 0,
                    to_ms=to_ms or 0,
                    detail=f"{target.value} 원본이 없어 대조를 건너뛴다",
                )
            )
            return

        source_candles = self._repo.get_range(symbol, source, from_ms=from_ms, to_ms=to_ms)
        if not source_candles:
            report.add(
                IntegrityIssue(
                    kind=IssueKind.MISSING_SOURCE,
                    severity=Severity.WARN,
                    timeframe=target,
                    from_ms=from_ms or 0,
                    to_ms=to_ms or 0,
                    detail=f"{source.value} 데이터가 없어 대조 불가",
                )
            )
            return

        # 2 단계: 1m 을 합쳐 봉마다 원본과 비교 · 원인 분류 (종류별로 상위 N 건만 자세히)
        buckets = bucket_candles(source_candles, target)
        derived = resample_candles(source_candles, source, target, require_complete=True)
        compared = 0
        count_by_kind: dict[IssueKind, int] = {}

        for candle in derived:
            original = stored.get(candle.open_ms)
            if original is None:
                continue  # 원본에 없는 버킷은 갭 검사가 담당한다
            compared += 1
            diffs = _compare(original, candle)
            if not diffs:
                continue

            kind, cause = _classify(original, candle, buckets.get(candle.open_ms, []))
            count_by_kind[kind] = count_by_kind.get(kind, 0) + 1
            severity = Severity.CRITICAL if kind is IssueKind.RESAMPLE_MISMATCH else Severity.WARN
            if count_by_kind[kind] <= self.thresholds.max_reported_mismatches:
                report.add(
                    IntegrityIssue(
                        kind=kind,
                        severity=severity,
                        timeframe=target,
                        from_ms=candle.open_ms,
                        to_ms=candle.close_ms,
                        detail=f"{cause}: {'; '.join(diffs)}",
                    )
                )

        # 3 단계: 대조한 봉 수 기록 · 너무 많아 다 못 적은 종류는 합계 한 줄
        report.checked[f"{target.value}:resample"] = compared
        for kind, total in count_by_kind.items():
            if total > self.thresholds.max_reported_mismatches:
                report.add(
                    IntegrityIssue(
                        kind=kind,
                        severity=(
                            Severity.CRITICAL
                            if kind is IssueKind.RESAMPLE_MISMATCH
                            else Severity.WARN
                        ),
                        timeframe=target,
                        from_ms=from_ms or 0,
                        to_ms=to_ms or 0,
                        detail=(
                            f"총 {total}건 (상위 "
                            f"{self.thresholds.max_reported_mismatches}건만 표시)"
                        ),
                    )
                )

    # ------------------------------------------------------------- 이상치

    def check_anomalies(
        self,
        symbol: str,
        timeframe: Timeframe,
        report: IntegrityReport,
        *,
        from_ms: int | None = None,
        to_ms: int | None = None,
    ) -> None:
        """[쓰임] 가격 급등락 · 오래 멈춘 가격 · 거래량 0 이 이어지는 구간을 찾는다.
        [이유] 수백만 봉을 한 번에 읽지 않고 ``chunk_days`` 일씩 나눠 읽는다(메모리). 창 경계에서도
               '직전 봉' 과 '연속 개수' 를 이어 받아 같은 결과가 나온다. OHLC 관계 위반은
               :class:`Candle` 타입이 읽는 순간 막으므로 여기서 다루지 않는다."""
        start = from_ms if from_ms is not None else self._repo.first_open_ms(symbol, timeframe)
        end = to_ms if to_ms is not None else self._repo.last_open_ms(symbol, timeframe)
        if start is None or end is None:
            report.add(
                IntegrityIssue(
                    kind=IssueKind.NO_DATA,
                    severity=Severity.WARN,
                    timeframe=timeframe,
                    from_ms=0,
                    to_ms=0,
                    detail=f"{timeframe.value} 데이터 없음",
                )
            )
            return

        chunk_ms = self.thresholds.chunk_days * _DAY_MS
        previous: Candle | None = None
        flat_run = 0
        zero_volume_run = 0
        total = 0
        cursor = start

        while cursor <= end:
            window_end = min(cursor + chunk_ms - 1, end)
            candles = self._repo.get_range(symbol, timeframe, from_ms=cursor, to_ms=window_end)
            for candle in candles:
                total += 1
                previous, flat_run, zero_volume_run = self._inspect_candle(
                    candle, previous, flat_run, zero_volume_run, timeframe, report
                )
            cursor = window_end + 1

        report.checked[timeframe.value] = total

    def _inspect_candle(
        self,
        candle: Candle,
        previous: Candle | None,
        flat_run: int,
        zero_volume_run: int,
        timeframe: Timeframe,
        report: IntegrityReport,
    ) -> tuple[Candle, int, int]:
        """[쓰임] 봉 하나를 직전 봉과 비교해 이상치를 보고서에 더하고, 다음 봉에 넘길 상태
                  ``(이 봉, 같은 종가 연속 수, 거래량 0 연속 수)`` 를 돌려준다.
        [이유] 연속 구간은 임계 개수가 되는 순간 한 번만 보고한다 — 같은 구간을 두 번 적지 않게."""
        limits = self.thresholds

        if previous is not None:
            change = abs(safe_div(candle.close - previous.close, previous.close))
            if change > limits.price_spike_pct:
                report.add(
                    IntegrityIssue(
                        kind=IssueKind.PRICE_SPIKE,
                        severity=Severity.WARN,
                        timeframe=timeframe,
                        from_ms=previous.open_ms,
                        to_ms=candle.open_ms,
                        detail=(
                            f"종가 {previous.close} -> {candle.close} "
                            f"({change:.1%} 변화, 임계 {limits.price_spike_pct:.0%})"
                        ),
                    )
                )
            flat_run = flat_run + 1 if candle.close == previous.close else 0
            if flat_run == limits.flat_price_bars:
                report.add(
                    IntegrityIssue(
                        kind=IssueKind.FLAT_PRICE,
                        severity=Severity.WARN,
                        timeframe=timeframe,
                        from_ms=candle.open_ms - flat_run * timeframe.ms,
                        to_ms=candle.open_ms,
                        detail=f"종가 {candle.close} 가 {flat_run}봉 연속 동일 — 피드 정지 의심",
                    )
                )

        if candle.volume == ZERO:
            zero_volume_run += 1
            if zero_volume_run == limits.zero_volume_bars:
                report.add(
                    IntegrityIssue(
                        kind=IssueKind.ZERO_VOLUME,
                        severity=Severity.WARN,
                        timeframe=timeframe,
                        from_ms=candle.open_ms - zero_volume_run * timeframe.ms,
                        to_ms=candle.open_ms,
                        detail=f"거래량 0 이 {zero_volume_run}봉 연속",
                    )
                )
        else:
            zero_volume_run = 0

        return candle, flat_run, zero_volume_run

    # ------------------------------------------------------------- 전체

    def run(
        self,
        symbol: str,
        timeframes: Sequence[Timeframe],
        *,
        from_ms: int | None = None,
        to_ms: int | None = None,
        resample_targets: Sequence[Timeframe] | None = None,
    ) -> IntegrityReport:
        """[쓰임] 게이트 1 검증 전체 — 타임프레임마다 갭 · 이상치, 그다음 1m 과 상위 봉 대조.
        [이유] ``resample_targets`` 기본값은 ``timeframes`` 중 1m 이 아닌 것들. 1d 는 1m 1440개가
               모두 있어야 하므로 갭이 있으면 자연히 건너뛴다. 1m 이 검사 대상에 없으면 대조할 수
               없어 경고를 남긴다."""
        report = IntegrityReport(symbol=symbol)

        for timeframe in timeframes:
            self.check_gaps(symbol, timeframe, report, from_ms=from_ms, to_ms=to_ms)
            self.check_anomalies(symbol, timeframe, report, from_ms=from_ms, to_ms=to_ms)

        targets = (
            resample_targets
            if resample_targets is not None
            else [timeframe for timeframe in timeframes if timeframe is not Timeframe.M1]
        )
        if Timeframe.M1 in timeframes:
            for target in targets:
                self.check_resample(symbol, target, report, from_ms=from_ms, to_ms=to_ms)
        elif targets:
            report.add(
                IntegrityIssue(
                    kind=IssueKind.MISSING_SOURCE,
                    severity=Severity.WARN,
                    timeframe=Timeframe.M1,
                    from_ms=from_ms or 0,
                    to_ms=to_ms or 0,
                    detail="1m 이 검증 대상에 없어 재집계 대조를 건너뛴다",
                )
            )

        logger.info("%s", report.summary())
        return report


#: 가격 계열 — 여기가 어긋나면 전략 판단이 직접 영향을 받는다
_PRICE_FIELDS = ("open", "high", "low", "close")
#: 거래량 계열 — 우리는 상위 TF 를 거래소 원본으로 쓰므로 전략에 직접 쓰이지 않는다
_VOLUME_FIELDS = ("volume", "quote_volume", "trade_count")


def _classify(original: Candle, derived: Candle, members: list[Candle]) -> tuple[IssueKind, str]:
    """[쓰임] 원본 봉과 합친 봉이 다를 때 그 **원인**을 정해 ``(종류, 설명)`` 으로 돌려준다.
    [이유] 게이트 기준은 "불일치 0건" 이 아니라 "**원인 미규명** 불일치 0건" 이다. 거래소의 1m
           아카이브와 상위 TF 아카이브는 실제로 서로 어긋나는 구간이 있으므로 완전 일치는 달성
           불가능한 요구다 (2026-09-21 실데이터 확인). 우리 집계 로직이나 데이터 사본의 문제라면
           소수의 고립된 버킷이 아니라 전 구간에서 체계적으로 어긋난다. 그래서 아래 두 원인에
           해당하지 않는 불일치만 CRITICAL 로 올린다."""
    has_filler = any(member.volume == ZERO for member in members)
    price_differs = any(
        getattr(original, field_name) != getattr(derived, field_name)
        for field_name in _PRICE_FIELDS
    )

    if has_filler and price_differs:
        # 1m 아카이브는 거래 없는 분을 직전가 O=H=L=C, volume=0 으로 채운다.
        # 상위 TF 는 실제 체결부터 시작하므로 open/low/high 가 어긋난다.
        filler_count = sum(1 for member in members if member.volume == ZERO)
        return (IssueKind.SOURCE_FILLER_BAR, f"1m 거래량0 채움봉 {filler_count}개로 인한 차이")
    if original.volume != derived.volume:
        direction = "상위TF 누락" if derived.volume > original.volume else "1m 누락"
        return (
            IssueKind.ARCHIVE_DISAGREEMENT,
            f"거래소 아카이브 간 거래량 불일치 ({direction})",
        )
    if has_filler:
        return (IssueKind.SOURCE_FILLER_BAR, "1m 거래량0 채움봉으로 인한 차이")
    return (IssueKind.RESAMPLE_MISMATCH, "원인 미규명 불일치")


def _compare(original: Candle, derived: Candle) -> list[str]:
    """[쓰임] 두 봉에서 값이 다른 칸을 ``"칸 원본=… 재집계=…"`` 글로 모아 돌려준다 (같으면 빈
    목록)."""
    diffs: list[str] = []
    for field_name in ("open", "high", "low", "close", "volume", "quote_volume"):
        original_value: Decimal = getattr(original, field_name)
        derived_value: Decimal = getattr(derived, field_name)
        if original_value != derived_value:
            diffs.append(f"{field_name} 원본={original_value} 재집계={derived_value}")
    if original.trade_count != derived.trade_count:
        diffs.append(f"trade_count 원본={original.trade_count} 재집계={derived.trade_count}")
    return diffs
