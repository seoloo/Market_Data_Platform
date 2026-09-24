"""고정 Elliott 4h 후보 창의 시장 입력 가용성만 읽기 전용으로 감사한다.

WA 거래·손익, 전략 경로, Elliott 하위파 라벨을 조회하지 않는다.
"""

from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import asdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from auto_trading.research.elliott.coverage import summarize_window_coverage  # noqa: E402
from auto_trading.research.elliott.h4_evidence import (  # noqa: E402
    map_minute_path_to_four_hour_evidence,
)
from scripts import audit_ew0_minute_path as market_audit  # noqa: E402

DB_PATH = market_audit.DB_PATH


def main() -> int:
    four_hour, replay = market_audit.load_market_replay(DB_PATH)
    evidence = map_minute_path_to_four_hour_evidence(four_hour, replay, four_hour[-1].close_ms)
    coverage = summarize_window_coverage(four_hour, evidence)
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
        "note": "4h 후보 창의 입력 가용성만; 파동·독립 표본·WA 거래·수익 검정 아님.",
    }
    sys.stdout.write(json.dumps(result, ensure_ascii=True, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
