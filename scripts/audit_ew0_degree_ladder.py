"""4h degree 사다리 하위파 읽기의 시장 기술 통계 (33 §16 에서 사전 고정). 읽기 전용.

WA 거래·방향·손익, D22, 전략 경로·원장을 조회하지 않는다. 결과는 stdout JSON 뿐이다.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from collections import Counter
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from auto_trading.core.enums import Timeframe  # noqa: E402
from auto_trading.core.errors import DataIntegrityError  # noqa: E402
from auto_trading.core.types import Candle  # noqa: E402
from auto_trading.research.elliott.candidates import generate_bounded_candidates  # noqa: E402
from auto_trading.research.elliott.degrees import DEGREE_LADDER, read_leg  # noqa: E402
from auto_trading.research.elliott.grammar import (  # noqa: E402
    EXPECTED_SUBDIVISIONS,
    LegSubdivision,
    subdivision_matches,
)
from auto_trading.research.elliott.h4_evidence import (  # noqa: E402
    EvidenceStatus,
    FourHourEvidence,
    FourHourPivotEvidence,
    map_minute_path_to_four_hour_evidence,
)
from auto_trading.research.elliott.minute_path import replay_minute_groups_ladder  # noqa: E402
from scripts import audit_ew0_minute_path as market  # noqa: E402

EW0_DIGEST = "034a8ed68e5d67b343d65049ffcdd0274a58bcc6dc4894af55c9e406dd5ffe37"
ALIGNMENT_FLOOR = 0.90


def _digest(evidence: Sequence[FourHourEvidence]) -> str:
    digest = hashlib.sha256()
    for item in evidence:
        digest.update(
            json.dumps(
                asdict(item), ensure_ascii=True, sort_keys=True, default=str, separators=(",", ":")
            ).encode("utf-8")
        )
        digest.update(b"\n")
    return digest.hexdigest()


def load_ladder(db_path: Path) -> tuple[tuple[Candle, ...], list[tuple[FourHourEvidence, ...]]]:
    if not db_path.is_file():
        raise FileNotFoundError(db_path)
    connection = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        connection.execute("PRAGMA query_only=ON")
        four_hour = tuple(
            market._candle(row)
            for row in connection.execute(
                market._QUERY, ("binance_usdm", "BTCUSDT", "4h", market.FROM_MS, market.TO_MS)
            )
        )
        if not four_hour:
            raise ValueError("4h 시장자료가 없다")
        as_of = four_hour[-1].close_ms
        replays = replay_minute_groups_ladder(
            four_hour,
            market._minute_groups(connection, four_hour),
            as_of,
            tuple(d.threshold_pct for d in DEGREE_LADDER),
        )
    finally:
        connection.close()
    return four_hour, [map_minute_path_to_four_hour_evidence(four_hour, r, as_of) for r in replays]


def _key(c: FourHourPivotEvidence) -> tuple[str, int, str]:
    return (c.kind.value, c.extreme_four_hour_open_ms, str(c.price))


def _alignment(
    parent: Sequence[FourHourEvidence], child: Sequence[FourHourEvidence], origin: int
) -> dict[str, object]:
    child_keys = {_key(i.candidate) for i in child if i.candidate is not None}
    unknown_close = [i.status is EvidenceStatus.UNKNOWN for i in child]
    aligned = child_unknown = different = 0
    for item in parent:
        c = item.candidate
        if c is None:
            continue
        if _key(c) in child_keys:
            aligned += 1
            continue
        lo = (c.extreme_four_hour_open_ms - origin) // Timeframe.H4.ms
        hi = (item.close_ms + 1 - origin) // Timeframe.H4.ms
        if any(unknown_close[lo:hi]):
            child_unknown += 1
        else:
            different += 1
    total = aligned + child_unknown + different
    rate = aligned / total if total else None
    return {
        "parent_candidates": total,
        "aligned": aligned,
        "misaligned_child_unknown": child_unknown,
        "misaligned_different_extreme": different,
        "alignment_rate": None if rate is None else round(rate, 4),
        "nested_enough": None if rate is None else rate >= ALIGNMENT_FLOOR,
    }


def _legs(
    bars: tuple[Candle, ...],
    parent: Sequence[FourHourEvidence],
    child: Sequence[FourHourEvidence],
) -> dict[str, object]:
    as_of = bars[-1].close_ms
    readings: Counter[str] = Counter()
    counts: Counter[str] = Counter()
    child_later = 0
    previous: FourHourPivotEvidence | None = None
    for item in parent:
        if item.status is EvidenceStatus.UNKNOWN:
            previous = None
            continue
        if item.candidate is None:
            continue
        if previous is not None:
            r = read_leg(bars, child, previous, item.candidate, as_of)
            readings[r.shape.value if r.reason is None else r.reason.value] += 1
            counts["none" if r.child_leg_count is None else str(r.child_leg_count)] += 1
            child_later += r.available_at_ms > item.candidate.confirmed_four_hour_close_ms
        previous = item.candidate
    return {
        "legs": sum(readings.values()),
        "reading": dict(sorted(readings.items())),
        "child_leg_count": dict(sorted(counts.items())),
        "child_confirmed_after_parent": child_later,
    }


def _scenarios(
    bars: tuple[Candle, ...],
    parent: Sequence[FourHourEvidence],
    child: Sequence[FourHourEvidence],
    degree_id: str,
    child_degree_id: str,
) -> dict[str, object]:
    status: Counter[str] = Counter()
    complete_legs_consistent: Counter[str] = Counter()
    all_consistent_closes: Counter[str] = Counter()
    pass_windows: dict[str, set[int]] = {}
    blocked = 0
    for index in range(len(bars)):
        try:
            snap = generate_bounded_candidates(
                bars[: index + 1],
                parent[: index + 1],
                bars[index].close_ms,
                degree_id=degree_id,
                child_evidence=child[: index + 1],
                child_degree_id=child_degree_id,
            )
        except DataIntegrityError as exc:
            if "UNKNOWN 경계" not in str(exc):
                raise
            blocked += 1
            continue
        for record in snap.records:
            pattern = record.scenario.pattern
            status[f"{pattern.value}|{record.assessment.status.value}"] += 1
            expected = EXPECTED_SUBDIVISIONS[pattern]
            proofs = record.scenario.subdivisions
            if len(proofs) != len(expected):
                continue
            ok = sum(
                p.shape is not LegSubdivision.UNKNOWN and subdivision_matches(e, p.shape)
                for p, e in zip(proofs, expected, strict=True)
            )
            complete_legs_consistent[f"{pattern.value}|{ok}/{len(expected)}"] += 1
            if ok == len(expected):
                all_consistent_closes[pattern.value] += 1
            if record.assessment.status.value == "PASS":
                pass_windows.setdefault(pattern.value, set()).add(
                    record.scenario.evidence[0].close_ms
                )
    return {
        "closes": len(bars),
        "blocked_cross_unknown_closes": blocked,
        "grammar_status_closes": dict(sorted(status.items())),
        "complete_window_legs_consistent_closes": dict(sorted(complete_legs_consistent.items())),
        "all_legs_consistent_closes": dict(sorted(all_consistent_closes.items())),
        "distinct_pass_windows": {k: len(v) for k, v in sorted(pass_windows.items())},
        "pass_window_first_close_ms": {k: sorted(v) for k, v in sorted(pass_windows.items())},
    }


def main() -> int:
    bars, ladder = load_ladder(market.DB_PATH)
    digests = [_digest(ev) for ev in ladder]
    if digests[0] != EW0_DIGEST:
        raise SystemExit(f"D0 매핑 digest 가 §13 과 다르다: {digests[0]}")
    origin = bars[0].open_ms
    degrees: dict[str, object] = {}
    for k, (spec, evidence) in enumerate(zip(DEGREE_LADDER, ladder, strict=True)):
        statuses = Counter(item.status.value for item in evidence)
        reasons = Counter(item.reason.value for item in evidence if item.reason is not None)
        entry: dict[str, object] = {
            "threshold_pct": str(spec.threshold_pct),
            "mapped_evidence_sha256": digests[k],
            "status": dict(sorted(statuses.items())),
            "unknown_reason": dict(sorted(reasons.items())),
        }
        if k > 0:
            child = ladder[k - 1]
            child_id = DEGREE_LADDER[k - 1].degree_id
            entry["child_degree"] = child_id
            entry["alignment"] = _alignment(evidence, child, origin)
            entry["legs"] = _legs(bars, evidence, child)
            entry["scenarios"] = _scenarios(bars, evidence, child, spec.degree_id, child_id)
        degrees[spec.degree_id] = entry
    result = {
        "period_utc": "2020-01-01/2022-01-01",
        "source": "binance_usdm:BTCUSDT",
        "degrees": degrees,
        "note": "33 §16 사전 고정 기술 통계. 반복 마감은 독립 표본 아님. WA·성과 미조회.",
    }
    sys.stdout.write(json.dumps(result, ensure_ascii=True, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
