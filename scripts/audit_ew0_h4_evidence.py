"""고정된 EW-0 1m→4h 후보 증거 규칙의 시장자료 coverage 감사.

Binance 대용 4h/1m 시장 봉만 읽는다. WA 거래·손익, Elliott 라벨, 전략·주문
경로를 열지 않고 DB/보고서 파일을 수정하지 않는다.
"""

from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from statistics import median

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from auto_trading.core.enums import Timeframe  # noqa: E402
from auto_trading.research.elliott.h4_evidence import (  # noqa: E402
    EvidenceStatus,
    map_minute_path_to_four_hour_evidence,
)
from scripts import audit_ew0_minute_path as market_audit  # noqa: E402

DB_PATH = market_audit.DB_PATH


def main() -> int:
    four_hour, replay = market_audit.load_market_replay(DB_PATH)
    evidence = map_minute_path_to_four_hour_evidence(four_hour, replay, four_hour[-1].close_ms)
    assert len(evidence) == len(four_hour)

    status = Counter(item.status.value for item in evidence)
    unknown_reasons = Counter(
        item.reason.value
        for item in evidence
        if item.status is EvidenceStatus.UNKNOWN
        if item.reason is not None
    )
    kinds = Counter(
        item.candidate.kind.value
        for item in evidence
        if item.status is EvidenceStatus.CANDIDATE and item.candidate is not None
    )
    delay_bars = sorted(
        (
            item.candidate.confirmed_four_hour_close_ms
            - Timeframe.H4.close_ms_of(item.candidate.extreme_four_hour_open_ms)
        )
        // Timeframe.H4.ms
        for item in evidence
        if item.status is EvidenceStatus.CANDIDATE and item.candidate is not None
    )
    by_year: dict[int, Counter[str]] = defaultdict(Counter)
    for item in evidence:
        year = datetime.fromtimestamp(item.close_ms / 1000, tz=UTC).year
        by_year[year][item.status.value] += 1
    assert sum(status.values()) == len(four_hour)
    assert sum(unknown_reasons.values()) == status[EvidenceStatus.UNKNOWN.value]
    assert sum(kinds.values()) == status[EvidenceStatus.CANDIDATE.value]

    result = {
        "period_utc": "2020-01-01/2022-01-01",
        "source": "binance_usdm:BTCUSDT",
        "four_hour_bars": len(four_hour),
        "status": dict(sorted(status.items())),
        "unknown_reasons": dict(sorted(unknown_reasons.items())),
        "candidate_kinds": dict(sorted(kinds.items())),
        "candidate_confirmation_delay_4h_bars_median": median(delay_bars) if delay_bars else None,
        "candidate_confirmation_delay_4h_bars_max": max(delay_bars) if delay_bars else None,
        "status_by_calendar_year": {
            str(year): dict(sorted(counts.items())) for year, counts in sorted(by_year.items())
        },
        "note": "4h 피벗 후보 증거 coverage만; Elliott 파동/WA 거래/수익 검정 아님.",
    }
    sys.stdout.write(json.dumps(result, ensure_ascii=True, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
