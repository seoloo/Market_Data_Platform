"""시장 데이터 무결성 검증.

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
from auto_trading.marketdata.resample import resample_candles
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
    GAP = "gap"
    RESAMPLE_MISMATCH = "resample_mismatch"
    PRICE_SPIKE = "price_spike"
    FLAT_PRICE = "flat_price"
    ZERO_VOLUME = "zero_volume"
    NO_DATA = "no_data"
    MISSING_SOURCE = "missing_source"


class Severity(StrEnum):
    INFO = "info"
    WARN = "warn"
    CRITICAL = "critical"


@dataclass(frozen=True, slots=True)
class IntegrityIssue:
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
        self.issues.append(issue)

    @property
    def ok(self) -> bool:
        """critical 이 없으면 통과. warn 은 사람이 판단한다 (예: 거래소 점검 갭)."""
        return not any(i.severity is Severity.CRITICAL for i in self.issues)

    @property
    def critical(self) -> list[IntegrityIssue]:
        return [i for i in self.issues if i.severity is Severity.CRITICAL]

    def by_kind(self, kind: IssueKind) -> list[IntegrityIssue]:
        return [i for i in self.issues if i.kind is kind]

    def summary(self) -> str:
        if not self.issues:
            checked = ", ".join(f"{tf}={n:,}봉" for tf, n in sorted(self.checked.items()))
            return f"[OK] {self.symbol}: 문제 없음 ({checked})"
        counts: dict[str, int] = {}
        for issue in self.issues:
            counts[issue.kind.value] = counts.get(issue.kind.value, 0) + 1
        detail = ", ".join(f"{k} {v}건" for k, v in sorted(counts.items()))
        status = "OK(warn)" if self.ok else f"CRITICAL {len(self.critical)}건"
        return f"[{status}] {self.symbol}: {detail}"


class IntegrityChecker:
    """캔들 데이터 무결성 검증기."""

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
        """결측 구간을 찾아 보고한다.

        갭 자체가 곧 실패는 아니다 — 거래소 점검으로 실제로 봉이 없을 수 있다.
        긴 갭만 critical 로 올리고, 나머지는 사람이 판단하도록 warn 으로 남긴다.
        """
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
        """1m 을 합친 값과 거래소가 준 상위 TF 원본을 대조한다.

        둘 다 같은 체결 스트림에서 나오므로 **정확히 일치해야 한다.**
        불일치는 데이터 오염이거나 우리 집계 로직 버그라는 뜻이다.
        """
        target_rows = self._repo.get_range(symbol, target, from_ms=from_ms, to_ms=to_ms)
        stored = {c.open_ms: c for c in target_rows}
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

        derived = resample_candles(source_candles, source, target, require_complete=True)
        compared = 0
        mismatches = 0

        for candle in derived:
            original = stored.get(candle.open_ms)
            if original is None:
                continue  # 원본에 없는 버킷은 갭 검사가 담당한다
            compared += 1
            diffs = _compare(original, candle)
            if not diffs:
                continue
            mismatches += 1
            if mismatches <= self.thresholds.max_reported_mismatches:
                report.add(
                    IntegrityIssue(
                        kind=IssueKind.RESAMPLE_MISMATCH,
                        severity=Severity.CRITICAL,
                        timeframe=target,
                        from_ms=candle.open_ms,
                        to_ms=candle.close_ms,
                        detail=f"1m 재집계와 원본 불일치: {'; '.join(diffs)}",
                    )
                )

        report.checked[f"{target.value}:resample"] = compared
        if mismatches > self.thresholds.max_reported_mismatches:
            report.add(
                IntegrityIssue(
                    kind=IssueKind.RESAMPLE_MISMATCH,
                    severity=Severity.CRITICAL,
                    timeframe=target,
                    from_ms=from_ms or 0,
                    to_ms=to_ms or 0,
                    detail=(
                        f"불일치 {mismatches}건 (상위 "
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
        """가격 스파이크, 정지된 가격, 거래량 0 구간을 찾는다.

        OHLC 관계 위반은 :class:`Candle` 타입이 읽는 순간 막으므로 여기서 다루지 않는다.
        """
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
                previous, flat_run, zero_volume_run = self._inspect(
                    candle, previous, flat_run, zero_volume_run, timeframe, report
                )
            cursor = window_end + 1

        report.checked[timeframe.value] = total

    def _inspect(
        self,
        candle: Candle,
        previous: Candle | None,
        flat_run: int,
        zero_volume_run: int,
        timeframe: Timeframe,
        report: IntegrityReport,
    ) -> tuple[Candle, int, int]:
        t = self.thresholds

        if previous is not None:
            change = abs(safe_div(candle.close - previous.close, previous.close))
            if change > t.price_spike_pct:
                report.add(
                    IntegrityIssue(
                        kind=IssueKind.PRICE_SPIKE,
                        severity=Severity.WARN,
                        timeframe=timeframe,
                        from_ms=previous.open_ms,
                        to_ms=candle.open_ms,
                        detail=(
                            f"종가 {previous.close} -> {candle.close} "
                            f"({change:.1%} 변화, 임계 {t.price_spike_pct:.0%})"
                        ),
                    )
                )
            flat_run = flat_run + 1 if candle.close == previous.close else 0
            if flat_run == t.flat_price_bars:
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
            if zero_volume_run == t.zero_volume_bars:
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
        """게이트 1 검증 전체를 수행한다.

        Args:
            resample_targets: 1m 과 대조할 상위 TF. 기본은 ``timeframes`` 중 1m 의
                정수배인 것들. 1d 는 1m 1440개가 모두 있어야 하므로 갭이 있으면
                자연히 건너뛴다.
        """
        report = IntegrityReport(symbol=symbol)

        for timeframe in timeframes:
            self.check_gaps(symbol, timeframe, report, from_ms=from_ms, to_ms=to_ms)
            self.check_anomalies(symbol, timeframe, report, from_ms=from_ms, to_ms=to_ms)

        targets = (
            resample_targets
            if resample_targets is not None
            else [tf for tf in timeframes if tf is not Timeframe.M1]
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


def _compare(original: Candle, derived: Candle) -> list[str]:
    """두 봉의 차이를 사람이 읽을 수 있는 문자열로 돌려준다."""
    diffs: list[str] = []
    for field_name in ("open", "high", "low", "close", "volume", "quote_volume"):
        a: Decimal = getattr(original, field_name)
        b: Decimal = getattr(derived, field_name)
        if a != b:
            diffs.append(f"{field_name} 원본={a} 재집계={b}")
    if original.trade_count != derived.trade_count:
        diffs.append(f"trade_count 원본={original.trade_count} 재집계={derived.trade_count}")
    return diffs
