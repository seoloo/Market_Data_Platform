"""게이트 1 통합 검증 — 백필 → 무결성 검사가 함께 동작하는가.

모듈이 각각 통과해도 조립했을 때 깨지는 경우가 있다.
10-roadmap.md 게이트 1 의 실제 흐름을 축소판으로 재현한다.
"""

from __future__ import annotations

from datetime import date

from auto_trading.core.enums import Timeframe
from auto_trading.marketdata.backfill import Backfiller
from auto_trading.marketdata.integrity import IntegrityChecker, IssueKind
from auto_trading.storage.repositories.candles import CandleRepository
from tests.marketdata.conftest import NOW, SYMBOL, FakeArchiveServer

M1 = Timeframe.M1
M5 = Timeframe.M5
H1 = Timeframe.H1
JAN_MS = 1_704_067_200_000  # 2024-01-01T00:00:00Z

#: 1시간(=60분) 분량. 1m/5m/1h 가 서로 정합한 데이터를 만들기 위한 최소 단위
BARS_PER_HOUR = 60


def _consistent_csv(timeframe: Timeframe, hours: int = 2) -> str:
    """서로 정합한 OHLCV 를 만든다.

    한 시간 안에서 가격이 단조 증가하도록 구성해 1m 을 합친 값이
    5m/1h 원본과 정확히 일치하게 만든다.
    """
    lines: list[str] = []
    bars = hours * (Timeframe.H1.ms // timeframe.ms)
    per_bar_minutes = timeframe.ms // M1.ms

    for i in range(bars):
        open_ms = JAN_MS + i * timeframe.ms
        close_ms = open_ms + timeframe.ms - 1
        first_minute = i * per_bar_minutes
        last_minute = first_minute + per_bar_minutes - 1
        open_px = 60000 + first_minute
        close_px = 60000 + last_minute
        high_px = close_px
        low_px = open_px
        volume = per_bar_minutes  # 1분봉 1개당 1.0
        quote = per_bar_minutes * 100
        trades = per_bar_minutes * 10
        lines.append(
            f"{open_ms},{open_px}.0,{high_px}.0,{low_px}.0,{close_px}.0,{volume}.0,"
            f"{close_ms},{quote}.0,{trades},0,0,0"
        )
    return "\n".join(lines) + "\n"


def test_backfill_then_integrity_passes(server: FakeArchiveServer, repo: CandleRepository, monthly):
    """정합한 데이터를 받아 적재하면 무결성 검사가 통과해야 한다."""
    timeframes = [M1, M5, H1]
    for tf in timeframes:
        server.add(monthly(date(2024, 1, 1), tf), _consistent_csv(tf))

    with server.client() as client:
        results = Backfiller(client, repo).run_all(
            SYMBOL, timeframes, start=date(2024, 1, 1), end=date(2024, 2, 1), ingested_ms=NOW
        )
    assert all(r.ok for r in results), [r.summary() for r in results]
    assert repo.count(SYMBOL, M1) == 120
    assert repo.count(SYMBOL, M5) == 24
    assert repo.count(SYMBOL, H1) == 2

    report = IntegrityChecker(repo).run(SYMBOL, timeframes)
    assert report.ok, report.summary()
    assert report.by_kind(IssueKind.RESAMPLE_MISMATCH) == []
    assert report.by_kind(IssueKind.GAP) == []
    assert report.checked["5m:resample"] == 24
    assert report.checked["1h:resample"] == 2


def test_corrupted_higher_timeframe_is_caught(
    server: FakeArchiveServer, repo: CandleRepository, monthly
):
    """상위 TF 원본이 1m 과 어긋나면 게이트가 막아야 한다.

    이것이 게이트 1 의 핵심 검사다 — 데이터 출처가 오염되면
    이후 모든 백테스트 결론이 무의미해진다.
    """
    server.add(monthly(date(2024, 1, 1), M1), _consistent_csv(M1))
    # 5m 의 종가만 어긋나게 만든다
    tampered = _consistent_csv(M5).replace("60004.0,", "69999.0,", 1)
    server.add(monthly(date(2024, 1, 1), M5), tampered)

    with server.client() as client:
        Backfiller(client, repo).run_all(
            SYMBOL, [M1, M5], start=date(2024, 1, 1), end=date(2024, 2, 1), ingested_ms=NOW
        )

    report = IntegrityChecker(repo).run(SYMBOL, [M1, M5])
    assert not report.ok
    mismatches = report.by_kind(IssueKind.RESAMPLE_MISMATCH)
    assert len(mismatches) == 1
    assert "재집계" in mismatches[0].detail


def test_missing_month_produces_gap_report(
    server: FakeArchiveServer, repo: CandleRepository, monthly
):
    """1월만 있고 2월이 없으면 갭이 아니라 '데이터 없음' 이다.

    갭은 **적재된 구간 안의** 결측을 뜻한다. 범위 밖은 갭이 아니다.
    """
    server.add(monthly(date(2024, 1, 1), M1), _consistent_csv(M1))

    with server.client() as client:
        Backfiller(client, repo).run(
            SYMBOL, M1, start=date(2024, 1, 1), end=date(2024, 3, 1), ingested_ms=NOW
        )

    report = IntegrityChecker(repo).run(SYMBOL, [M1])
    assert report.by_kind(IssueKind.GAP) == []
    assert report.checked["1m"] == 120
