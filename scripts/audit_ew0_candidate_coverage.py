"""고정 Elliott 4h 후보 창의 시장 입력 가용성만 읽기 전용으로 감사한다.

WA 거래·손익, 전략 경로, Elliott 하위파 라벨을 조회하지 않는다.
"""

from __future__ import annotations

import hashlib
import json
import sys
from collections import Counter
from dataclasses import asdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from auto_trading.research.elliott.coverage import (  # noqa: E402
    CrossUnknownCase,
    classify_cross_unknown,
    summarize_window_coverage,
)
from auto_trading.research.elliott.h4_evidence import (  # noqa: E402
    map_minute_path_to_four_hour_evidence,
)
from scripts import audit_ew0_minute_path as market_audit  # noqa: E402

DB_PATH = market_audit.DB_PATH


def _describe(cases: tuple[CrossUnknownCase, ...]) -> dict[str, object]:
    """33 §14 교차표: 경계 사유 × 극값 위치(0/1/≥2) × 구간 첫 후보, 확인 지연 분포."""
    table: Counter[str] = Counter()
    for case in cases:
        offset = (
            "0"
            if case.extreme_offset_bars == 0
            else ("1" if case.extreme_offset_bars == 1 else ">=2")
        )
        reason = case.boundary_reason.value if case.boundary_reason else "NONE"
        table[f"{reason}|offset={offset}|first={case.first_in_segment}"] += 1
    lags = sorted(case.confirm_lag_bars for case in cases)
    return {
        "count": len(cases),
        "crosstab": dict(sorted(table.items())),
        "first_in_segment": sum(case.first_in_segment for case in cases),
        "confirm_lag_bars_median": lags[len(lags) // 2] if lags else None,
        "confirm_lag_bars_max": lags[-1] if lags else None,
        "extreme_offset_bars_max": max((c.extreme_offset_bars for c in cases), default=None),
    }


def main() -> int:
    four_hour, replay = market_audit.load_market_replay(DB_PATH)
    evidence = map_minute_path_to_four_hour_evidence(four_hour, replay, four_hour[-1].close_ms)
    coverage = summarize_window_coverage(four_hour, evidence)
    cases = classify_cross_unknown(four_hour, evidence)
    if len(cases) != coverage.cross_unknown_candidates:
        raise SystemExit("교차-UNKNOWN 분류 건수가 집계와 다르다")
    digest = hashlib.sha256()
    for item in evidence:
        digest.update(
            json.dumps(
                asdict(item), ensure_ascii=True, sort_keys=True, default=str, separators=(",", ":")
            ).encode("utf-8")
        )
        digest.update(b"\n")
    result = {
        "period_utc": "2020-01-01/2022-01-01",
        "source": "binance_usdm:BTCUSDT",
        "mapped_evidence_sha256": digest.hexdigest(),
        "coverage": asdict(coverage),
        "cross_unknown": _describe(cases),
        "note": "4h 후보 창의 입력 가용성만; 파동·독립 표본·WA 거래·수익 검정 아님.",
    }
    sys.stdout.write(json.dumps(result, ensure_ascii=True, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
