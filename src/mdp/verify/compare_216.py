"""Silver 체결 감사 ↔ 연구 216 추출기 비교.

언제 쓰나: Silver 체결(:mod:`mdp.silver.agg_trades`)을 만든 뒤, 같은 월 파일을 연구 216 의
           추출기로도
          처리해 값이 같은지 본다. 호스트에서 돈다(Spark 없음)::

    python -m mdp.verify.compare_216 --symbol BTCUSDT --month 2024-03

읽고 쓰는 것:
- 읽기: Bronze 월 zip(연구 추출기가 읽음) ·
        Silver 비교 보고 ``<lake>/silver/_reports/agg_trades/*.json``
- 쓰기: 연구 추출기 결과 ``<lake>/silver/_reports/research216/<종목>-<달>.<fast|full>.json``
        (이미 있으면 다시 돌리지 않음) · 비교 결과 ``…/research216/<종목>-<달>.compare.json``

비교하는 것 (월 파일 하나):
- 행 수 · 첫/끝 체결 번호 · 첫/끝 시각 · 머리줄 · 시각 단위 · 빈 곳 수 · 시각 역행 수
- 매시 00 분 [거래량 · 공격 매수량 · 체결 수] 744 개 — 연구 216 이 공식 1 분봉과 대조하던 값

정답지는 둘이다. ``fast`` = 연구 때 실제로 쓴 빠른 추출(``extract``),
``full`` = 한 줄씩 다 읽는 검산용(``extract_full``). 거래량은 연구 쪽이 float 합, Silver 는
decimal 합이라 상대 오차 1e-9 안이면 같다고 본다.

[이유] 새 파이프라인이 '옛 결과를 재현한다' 를 숫자로 보여야 결과를 믿을 수 있다. 연구 스크립트는
       고치지 않는다(절대 규칙 6) — 다른 값이 나오면 원인을 따로 기록한다.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any, Final

from mdp.bronze.archive import ArchiveFile, Dataset
from mdp.bronze.fetch import REPO_ROOT, default_bronze_root, log_json, setup_cli_logging
from mdp.spark.session import lake_root

__all__ = ["Comparison", "compare_month", "main", "run_research_extract"]

RELATIVE_TOLERANCE: Final = 1e-9


@dataclass(frozen=True, slots=True)
class Comparison:
    """[쓰임] 정답지 하나와의 비교 결과."""

    reference: str
    """``fast`` 또는 ``full``."""
    items: dict[str, tuple[object, object, bool]]
    """항목 → (연구 값, Silver 값, 같은가)."""
    minute_mismatches: list[dict[str, object]]
    """매시 00 분 중 다른 분들 (분 ms · 연구 값 · Silver 값)."""

    @property
    def all_equal(self) -> bool:
        """[쓰임] 모든 항목과 744 개 분이 같은가."""
        return all(equal for *_, equal in self.items.values()) and not self.minute_mismatches


def run_research_extract(zip_path: Path, out_path: Path, *, full: bool) -> dict[str, Any]:
    """[쓰임] 연구 216 추출기로 월 zip 을 처리해 결과를 JSON 으로 남긴다(이미 있으면 읽기만).
    [이유] 검산용 전수 추출은 6,651 만 줄에 4 분쯤 걸린다 — 한 번 돌린 결과를 다시 쓴다."""
    if out_path.exists():
        loaded: dict[str, Any] = json.loads(out_path.read_text(encoding="utf-8"))
        return loaded
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    from scripts.run_qflow_216 import extract, extract_full  # 연구 코드 — 그대로 부른다

    started = time.perf_counter()
    result = (extract_full if full else extract)(zip_path)
    payload = asdict(result)
    payload["windows"] = {str(k): v for k, v in payload["windows"].items()}
    payload["check_min"] = {str(k): v for k, v in payload["check_min"].items()}
    payload["elapsed_s"] = round(time.perf_counter() - started, 1)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload), encoding="utf-8")
    return payload


def _close(research_value: float, silver_value: float) -> bool:
    """[쓰임] float 합(연구) 과 decimal 합(Silver) 이 상대 오차 안에서 같은가."""
    scale = max(abs(research_value), abs(silver_value), 1e-12)
    return abs(research_value - silver_value) / scale <= RELATIVE_TOLERANCE


def compare_month(
    report: Mapping[str, Any], research: Mapping[str, Any], reference: str, source_path: str
) -> Comparison:
    """[쓰임] Silver 보고(그 월 파일 부분)와 연구 추출 결과 하나를 항목별로 비교한다.
    [이유] 연구 쪽 '첫/끝 번호' 는 파일 줄 순서, Silver 는 최소/최대다 — 번호가 줄 순서대로 늘어나는
           깨끗한 파일이면 같고, 다르면 그것 자체가 결함 신호라 그대로 '다름' 으로 남긴다."""
    audit = next(row for row in report["audit_file"] if row["source_path"] == source_path)
    silver_gaps = [
        g for g in report["gaps"] if g["source_path"] == source_path and g["kind"] == "gap"
    ]
    items: dict[str, tuple[object, object, bool]] = {}

    def item(name: str, research_value: object, silver_value: object) -> None:
        items[name] = (research_value, silver_value, research_value == silver_value)

    item("rows", research["rows"], audit["rows"])
    item("first_id", research["first_id"], audit["min_id"])
    item("last_id", research["last_id"], audit["max_id"])
    item("first_ms", research["first_ts"], audit["min_ms"])
    item("last_ms", research["last_ts"], audit["max_ms"])
    item("header", research["header"], audit["header_rows"] > 0)
    item("time_units", [research["ts_unit"]], audit["time_units"])
    item("gaps", len([g for g in research["gaps"] if g[2] > 0]), len(silver_gaps))
    item("time_reversed", research["nonmono"], audit["time_reversed_rows"])

    silver_minutes = {
        int(row["minute_ms"]): row
        for row in report["check_minutes"]
        if row["source_path"] == source_path
    }
    mismatches: list[dict[str, object]] = []
    for minute_text, (quantity, taker_buy, trades) in research["check_min"].items():
        silver = silver_minutes.get(int(minute_text))
        same = (
            silver is not None
            and int(trades) == int(silver["trades"])
            and _close(quantity, float(silver["quantity"]))
            and _close(taker_buy, float(silver["taker_buy_quantity"]))
        )
        if not same:
            mismatches.append(
                {
                    "minute_ms": int(minute_text),
                    "research": [quantity, taker_buy, int(trades)],
                    "silver": None
                    if silver is None
                    else [
                        str(silver["quantity"]),
                        str(silver["taker_buy_quantity"]),
                        silver["trades"],
                    ],
                }
            )
    item("check_minutes", len(research["check_min"]), len(silver_minutes))
    return Comparison(reference, items, mismatches)


def main(argv: Sequence[str] | None = None) -> int:
    """[쓰임] 명령줄 — 연구 추출기 두 가지를 돌리고(없으면) Silver 보고와 비교해 결과를 남긴다.
    [이유] 다르면 종료 코드 1 — 오케스트레이터(4-6)가 '검증 실패' 로 멈출 수 있게. 합격 기준은
           검산용 전수 추출(``full``)이다. 빠른 추출(``fast``)은 덩어리 경계 버그가 알려져 있어
           (docs/findings/2026-10-21-qflow216-check-minute-chunk-boundary.md) 차이를 기록만 한다."""
    setup_cli_logging()
    parser = argparse.ArgumentParser(prog="python -m mdp.verify.compare_216")
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--month", required=True, help="YYYY-MM")
    parser.add_argument("--reference", choices=["fast", "full", "both"], default="both")
    args = parser.parse_args(argv)
    year_text, month_text = args.month.split("-")
    month = date(int(year_text), int(month_text), 1)
    monthly = ArchiveFile(Dataset.AGG_TRADES, args.symbol, month)
    zip_path = default_bronze_root().joinpath(*monthly.relative_path.split("/"))
    reports = lake_root() / "silver" / "_reports"
    report = json.loads(
        (reports / "agg_trades" / f"{args.symbol}-{args.month}.json").read_text(encoding="utf-8")
    )
    references = ["fast", "full"] if args.reference == "both" else [args.reference]
    results: dict[str, Any] = {}
    gate = "full" if args.reference in ("full", "both") else "fast"  # 합격 기준
    all_equal = True
    for reference in references:
        research = run_research_extract(
            zip_path,
            reports / "research216" / f"{args.symbol}-{args.month}.{reference}.json",
            full=reference == "full",
        )
        comparison = compare_month(report, research, reference, monthly.relative_path)
        if reference == gate:
            all_equal = comparison.all_equal
        results[reference] = {
            "all_equal": comparison.all_equal,
            "items": {k: list(v) for k, v in comparison.items.items()},
            "minute_mismatches": comparison.minute_mismatches,
            "research_elapsed_s": research.get("elapsed_s"),
        }
        log_json(
            event="compare",
            reference=reference,
            all_equal=comparison.all_equal,
            minute_mismatches=len(comparison.minute_mismatches),
            differing_items=[k for k, (*_, eq) in comparison.items.items() if not eq],
        )
    out = reports / "research216" / f"{args.symbol}-{args.month}.compare.json"
    out.write_text(json.dumps(results, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    log_json(event="compare_written", path=str(out))
    return 0 if all_equal else 1


if __name__ == "__main__":
    raise SystemExit(main())
