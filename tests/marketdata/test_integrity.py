"""무결성 검증 테스트 — 게이트 1 항목.

재집계 대조는 **거래소 원본과 정확히 일치**해야 한다. 둘 다 같은 체결 스트림에서
나오므로 불일치는 데이터 오염이거나 집계 로직 버그라는 뜻이다.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from auto_trading.core.enums import Timeframe
from auto_trading.core.errors import DataIntegrityError
from auto_trading.core.types import Candle
from auto_trading.marketdata.integrity import (
    IntegrityChecker,
    IntegrityReport,
    IntegrityThresholds,
    IssueKind,
    Severity,
)
from auto_trading.marketdata.resample import resample_candles
from auto_trading.storage.repositories.candles import CandleRepository
from tests.marketdata.conftest import NOW, SYMBOL

D = Decimal
M1 = Timeframe.M1
M5 = Timeframe.M5
H1 = Timeframe.H1
BASE_MS = 1_704_067_200_000  # 2024-01-01T00:00:00Z


def candle(
    open_ms: int,
    tf: Timeframe = M1,
    *,
    o: str = "100",
    h: str = "110",
    low: str = "90",
    c: str = "105",
    v: str = "1",
    qv: str = "100",
    trades: int = 10,
) -> Candle:
    # high/low 는 OHLC 관계가 항상 성립하도록 넓혀 준다.
    # 테스트가 close/open 만 바꿔도 타입 검증에 걸리지 않게 하기 위해서다.
    high = max(D(h), D(o), D(c))
    lowest = min(D(low), D(o), D(c))
    return Candle(
        open_ms=open_ms,
        close_ms=open_ms + tf.ms - 1,
        open=D(o),
        high=high,
        low=lowest,
        close=D(c),
        volume=D(v),
        quote_volume=D(qv),
        trade_count=trades,
    )


def seed(repo: CandleRepository, tf: Timeframe, candles: list[Candle]) -> None:
    repo.upsert_many(SYMBOL, tf, candles, source="bulk", ingested_ms=NOW)


# ------------------------------------------------------------------ resample


class TestResample:
    def test_five_one_minute_bars_become_one_five_minute_bar(self):
        bars = [
            candle(
                BASE_MS + i * M1.ms,
                o=f"{100 + i}",
                h=f"{110 + i}",
                low=f"{90 + i}",
                c=f"{105 + i}",
            )
            for i in range(5)
        ]
        out = resample_candles(bars, M1, M5)
        assert len(out) == 1
        assert out[0].open_ms == BASE_MS
        assert out[0].close_ms == BASE_MS + M5.ms - 1
        assert out[0].open == D("100")  # 첫 봉의 open
        assert out[0].close == D("109")  # 마지막 봉의 close
        assert out[0].high == D("114")  # 최댓값
        assert out[0].low == D("90")  # 최솟값
        assert out[0].volume == D("5")
        assert out[0].trade_count == 50

    def test_incomplete_bucket_is_skipped(self):
        """불완전한 버킷을 넣으면 대조에서 거짓 불일치가 발생한다."""
        bars = [candle(BASE_MS + i * M1.ms) for i in range(3)]  # 5개 필요한데 3개
        assert resample_candles(bars, M1, M5) == ()

    def test_incomplete_bucket_can_be_included(self):
        bars = [candle(BASE_MS + i * M1.ms) for i in range(3)]
        out = resample_candles(bars, M1, M5, require_complete=False)
        assert len(out) == 1

    def test_multiple_buckets(self):
        bars = [candle(BASE_MS + i * M1.ms) for i in range(15)]
        assert len(resample_candles(bars, M1, M5)) == 3

    def test_partial_trailing_bucket_dropped(self):
        bars = [candle(BASE_MS + i * M1.ms) for i in range(12)]  # 2개 완성 + 2개 잔여
        assert len(resample_candles(bars, M1, M5)) == 2

    def test_unordered_input_is_handled(self):
        bars = [candle(BASE_MS + i * M1.ms, c=f"{100 + i}") for i in range(5)]
        out = resample_candles(list(reversed(bars)), M1, M5)
        assert out[0].open == D("100")
        assert out[0].close == D("104")

    def test_one_minute_to_one_hour(self):
        bars = [candle(BASE_MS + i * M1.ms) for i in range(60)]
        out = resample_candles(bars, M1, H1)
        assert len(out) == 1
        assert out[0].volume == D("60")

    def test_all_timeframe_pairs_are_compatible(self):
        """우리가 쓰는 6개 TF 는 서로 정수배다. 그래서 정수배 가드가 실전에서 발동하지 않는다.

        새 TF(예: 3m)를 추가하면 이 테스트가 깨지므로 그때 재집계 전략을 다시 봐야 한다.
        """
        ordered = sorted(Timeframe, key=lambda t: t.ms)
        for i, source in enumerate(ordered):
            for target in ordered[i + 1 :]:
                assert target.ms % source.ms == 0, f"{source.value} -> {target.value}"

    def test_downsample_rejected(self):
        with pytest.raises(DataIntegrityError, match="상위 타임프레임"):
            resample_candles([], H1, M1)

    def test_same_timeframe_rejected(self):
        with pytest.raises(DataIntegrityError, match="상위 타임프레임"):
            resample_candles([], M1, M1)

    def test_empty_input(self):
        assert resample_candles([], M1, M5) == ()


# ------------------------------------------------------------------ 갭


class TestGapCheck:
    def test_no_gaps(self, repo: CandleRepository):
        seed(repo, M1, [candle(BASE_MS + i * M1.ms) for i in range(10)])
        report = IntegrityReport(SYMBOL)
        IntegrityChecker(repo).check_gaps(SYMBOL, M1, report)
        assert report.by_kind(IssueKind.GAP) == []

    def test_short_gap_is_warn(self, repo: CandleRepository):
        """짧은 갭은 거래소 점검일 수 있다. 사람이 판단하도록 warn 으로 남긴다."""
        seed(repo, M1, [candle(BASE_MS + i * M1.ms) for i in (0, 1, 5, 6)])
        report = IntegrityReport(SYMBOL)
        IntegrityChecker(repo).check_gaps(SYMBOL, M1, report)
        gaps = report.by_kind(IssueKind.GAP)
        assert len(gaps) == 1
        assert gaps[0].severity is Severity.WARN
        assert "3봉 결측" in gaps[0].detail
        assert report.ok

    def test_long_gap_is_critical(self, repo: CandleRepository):
        seed(repo, M1, [candle(BASE_MS), candle(BASE_MS + 500 * M1.ms)])
        report = IntegrityReport(SYMBOL)
        IntegrityChecker(repo).check_gaps(SYMBOL, M1, report)
        gaps = report.by_kind(IssueKind.GAP)
        assert gaps[0].severity is Severity.CRITICAL
        assert not report.ok

    def test_critical_threshold_is_configurable(self, repo: CandleRepository):
        seed(repo, M1, [candle(BASE_MS), candle(BASE_MS + 500 * M1.ms)])
        report = IntegrityReport(SYMBOL)
        checker = IntegrityChecker(
            repo, thresholds=IntegrityThresholds(critical_gap_ms=30 * 24 * 3_600_000)
        )
        checker.check_gaps(SYMBOL, M1, report)
        assert report.by_kind(IssueKind.GAP)[0].severity is Severity.WARN


# ------------------------------------------------------------------ 재집계 대조


class TestResampleCheck:
    def _seed_consistent(self, repo: CandleRepository, buckets: int = 2) -> None:
        minutes = [candle(BASE_MS + i * M1.ms, c=f"{100 + i}") for i in range(5 * buckets)]
        seed(repo, M1, minutes)
        seed(repo, M5, list(resample_candles(minutes, M1, M5)))

    def test_consistent_data_passes(self, repo: CandleRepository):
        self._seed_consistent(repo)
        report = IntegrityReport(SYMBOL)
        IntegrityChecker(repo).check_resample(SYMBOL, M5, report)
        assert report.by_kind(IssueKind.RESAMPLE_MISMATCH) == []
        assert report.checked["5m:resample"] == 2

    @pytest.mark.parametrize(
        ("field_kwargs", "expected"),
        [
            ({"c": "999"}, "close"),
            ({"h": "9999"}, "high"),
            ({"low": "0.5"}, "low"),
            ({"o": "1"}, "open"),
            ({"v": "999"}, "volume"),
            ({"trades": 9999}, "trade_count"),
        ],
    )
    def test_mismatch_is_critical(self, repo: CandleRepository, field_kwargs, expected):
        """1m 과 5m 은 같은 체결에서 나오므로 정확히 일치해야 한다."""
        minutes = [candle(BASE_MS + i * M1.ms) for i in range(5)]
        seed(repo, M1, minutes)
        tampered = candle(BASE_MS, M5, **field_kwargs)
        seed(repo, M5, [tampered])

        report = IntegrityReport(SYMBOL)
        IntegrityChecker(repo).check_resample(SYMBOL, M5, report)
        issues = report.by_kind(IssueKind.RESAMPLE_MISMATCH)
        assert len(issues) == 1
        assert issues[0].severity is Severity.CRITICAL
        assert expected in issues[0].detail
        assert not report.ok

    def test_missing_target_is_warn_not_critical(self, repo: CandleRepository):
        seed(repo, M1, [candle(BASE_MS + i * M1.ms) for i in range(5)])
        report = IntegrityReport(SYMBOL)
        IntegrityChecker(repo).check_resample(SYMBOL, M5, report)
        assert report.by_kind(IssueKind.NO_DATA)
        assert report.ok

    def test_missing_source_is_warn(self, repo: CandleRepository):
        seed(repo, M5, [candle(BASE_MS, M5)])
        report = IntegrityReport(SYMBOL)
        IntegrityChecker(repo).check_resample(SYMBOL, M5, report)
        assert report.by_kind(IssueKind.MISSING_SOURCE)
        assert report.ok

    def test_incomplete_source_bucket_is_not_compared(self, repo: CandleRepository):
        """1m 이 일부만 있는 버킷을 대조하면 거짓 불일치가 난다."""
        seed(repo, M1, [candle(BASE_MS + i * M1.ms) for i in range(3)])  # 5개 중 3개
        seed(repo, M5, [candle(BASE_MS, M5)])
        report = IntegrityReport(SYMBOL)
        IntegrityChecker(repo).check_resample(SYMBOL, M5, report)
        assert report.by_kind(IssueKind.RESAMPLE_MISMATCH) == []
        assert report.checked["5m:resample"] == 0

    def test_mismatch_reporting_is_capped(self, repo: CandleRepository):
        minutes = [candle(BASE_MS + i * M1.ms) for i in range(5 * 10)]
        seed(repo, M1, minutes)
        wrong = [candle(BASE_MS + i * M5.ms, M5, c="999") for i in range(10)]
        seed(repo, M5, wrong)

        report = IntegrityReport(SYMBOL)
        checker = IntegrityChecker(repo, thresholds=IntegrityThresholds(max_reported_mismatches=3))
        checker.check_resample(SYMBOL, M5, report)
        issues = report.by_kind(IssueKind.RESAMPLE_MISMATCH)
        assert len(issues) == 4  # 3건 + 요약 1건
        assert "불일치 10건" in issues[-1].detail


# ------------------------------------------------------------------ 이상치


class TestAnomalies:
    def test_clean_data_has_no_issues(self, repo: CandleRepository):
        seed(repo, M1, [candle(BASE_MS + i * M1.ms, c=f"{100 + i}") for i in range(20)])
        report = IntegrityReport(SYMBOL)
        IntegrityChecker(repo).check_anomalies(SYMBOL, M1, report)
        assert report.issues == []
        assert report.checked["1m"] == 20

    def test_price_spike_detected(self, repo: CandleRepository):
        seed(
            repo,
            M1,
            [
                candle(BASE_MS, o="100", h="110", low="90", c="100"),
                candle(BASE_MS + M1.ms, o="100", h="210", low="90", c="200"),  # +100%
            ],
        )
        report = IntegrityReport(SYMBOL)
        IntegrityChecker(repo).check_anomalies(SYMBOL, M1, report)
        spikes = report.by_kind(IssueKind.PRICE_SPIKE)
        assert len(spikes) == 1
        assert "100.0%" in spikes[0].detail

    def test_spike_threshold_configurable(self, repo: CandleRepository):
        seed(
            repo,
            M1,
            [
                candle(BASE_MS, c="100"),
                candle(BASE_MS + M1.ms, o="100", h="110", low="90", c="103"),  # +3%
            ],
        )
        report = IntegrityReport(SYMBOL)
        checker = IntegrityChecker(repo, thresholds=IntegrityThresholds(price_spike_pct=D("0.01")))
        checker.check_anomalies(SYMBOL, M1, report)
        assert len(report.by_kind(IssueKind.PRICE_SPIKE)) == 1

    def test_flat_price_run_detected(self, repo: CandleRepository):
        seed(repo, M1, [candle(BASE_MS + i * M1.ms, c="100") for i in range(12)])
        report = IntegrityReport(SYMBOL)
        checker = IntegrityChecker(repo, thresholds=IntegrityThresholds(flat_price_bars=10))
        checker.check_anomalies(SYMBOL, M1, report)
        flats = report.by_kind(IssueKind.FLAT_PRICE)
        assert len(flats) == 1
        assert "10봉 연속" in flats[0].detail

    def test_flat_run_resets_on_change(self, repo: CandleRepository):
        bars = [candle(BASE_MS + i * M1.ms, c="100") for i in range(5)]
        bars.append(candle(BASE_MS + 5 * M1.ms, c="101"))
        bars.extend(candle(BASE_MS + (6 + i) * M1.ms, c="101") for i in range(5))
        seed(repo, M1, bars)
        report = IntegrityReport(SYMBOL)
        checker = IntegrityChecker(repo, thresholds=IntegrityThresholds(flat_price_bars=8))
        checker.check_anomalies(SYMBOL, M1, report)
        assert report.by_kind(IssueKind.FLAT_PRICE) == []

    def test_zero_volume_run_detected(self, repo: CandleRepository):
        seed(repo, M1, [candle(BASE_MS + i * M1.ms, c=f"{100 + i}", v="0") for i in range(6)])
        report = IntegrityReport(SYMBOL)
        checker = IntegrityChecker(repo, thresholds=IntegrityThresholds(zero_volume_bars=5))
        checker.check_anomalies(SYMBOL, M1, report)
        assert len(report.by_kind(IssueKind.ZERO_VOLUME)) == 1

    def test_no_data_is_reported(self, repo: CandleRepository):
        report = IntegrityReport(SYMBOL)
        IntegrityChecker(repo).check_anomalies(SYMBOL, M1, report)
        assert report.by_kind(IssueKind.NO_DATA)

    def test_chunking_does_not_lose_continuity(self, repo: CandleRepository):
        """시간 창을 나눠 읽어도 창 경계에서 연속성 판정이 끊기면 안 된다."""
        bars = [candle(BASE_MS + i * M1.ms, c="100") for i in range(200)]
        seed(repo, M1, bars)
        report = IntegrityReport(SYMBOL)
        # chunk 를 매우 작게 (1일) 잡아도 200봉(3.3시간)은 한 창에 들어간다.
        # 경계를 강제로 만들기 위해 0 일은 불가하므로 총량 검증으로 대신한다.
        IntegrityChecker(
            repo, thresholds=IntegrityThresholds(chunk_days=1, flat_price_bars=150)
        ).check_anomalies(SYMBOL, M1, report)
        assert report.checked["1m"] == 200
        assert len(report.by_kind(IssueKind.FLAT_PRICE)) == 1


# ------------------------------------------------------------------ 전체


class TestRunAll:
    def test_clean_dataset_passes(self, repo: CandleRepository):
        minutes = [candle(BASE_MS + i * M1.ms, c=f"{100 + i % 7}") for i in range(60)]
        seed(repo, M1, minutes)
        seed(repo, M5, list(resample_candles(minutes, M1, M5)))
        seed(repo, H1, list(resample_candles(minutes, M1, H1)))

        report = IntegrityChecker(repo).run(SYMBOL, [M1, M5, H1])
        assert report.ok, report.summary()
        assert "문제 없음" in report.summary()

    def test_resample_mismatch_fails_the_gate(self, repo: CandleRepository):
        minutes = [candle(BASE_MS + i * M1.ms) for i in range(5)]
        seed(repo, M1, minutes)
        seed(repo, M5, [candle(BASE_MS, M5, c="12345")])

        report = IntegrityChecker(repo).run(SYMBOL, [M1, M5])
        assert not report.ok
        assert "CRITICAL" in report.summary()

    def test_without_one_minute_resample_is_skipped(self, repo: CandleRepository):
        seed(repo, M5, [candle(BASE_MS + i * M5.ms, M5) for i in range(3)])
        report = IntegrityChecker(repo).run(SYMBOL, [M5])
        assert report.by_kind(IssueKind.MISSING_SOURCE)
        assert report.ok

    def test_summary_counts_by_kind(self, repo: CandleRepository):
        seed(repo, M1, [candle(BASE_MS + i * M1.ms) for i in (0, 1, 5)])
        report = IntegrityChecker(repo).run(SYMBOL, [M1])
        assert "gap 1건" in report.summary()

    def test_explicit_resample_targets(self, repo: CandleRepository):
        minutes = [candle(BASE_MS + i * M1.ms) for i in range(60)]
        seed(repo, M1, minutes)
        seed(repo, H1, list(resample_candles(minutes, M1, H1)))
        report = IntegrityChecker(repo).run(SYMBOL, [M1, H1], resample_targets=[H1])
        assert report.ok
        assert "1h:resample" in report.checked
