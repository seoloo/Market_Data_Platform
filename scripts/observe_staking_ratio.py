"""고정된 Binance WBETH·BNSOL 환산비율의 설명적 과거 관찰 (문서 91).

`python -m scripts.observe_staking_ratio` — 로컬 Git 제외 스냅숏만 읽는다.
계좌 수익·선물 손익·거래 가능 수익을 계산하지 않는다.
"""

from __future__ import annotations

import hashlib
import json
import sys
from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation, localcontext
from pathlib import Path
from typing import Any

from scripts.audit_staking_rates import DATA, DAY_MS

REPO = Path(__file__).resolve().parent.parent
MANIFEST = DATA / "manifest-1790416651513.json"
REPORT = REPO / "reports" / "research" / "staking" / "ratio-observation-38422a7.json"
EXPECTED_SHA = {
    "ETH": "f23fb91f79eb4c035f6f0f492e5298334a365b5892c99442a6c849bbf62623b4",
    "SOL": "5e701cba2ca003c366659d724bc7630bc27c708d809d218c07199e5b615e008d",
}
START = date(2024, 9, 13)
END = date(2026, 8, 31)
WINDOWS = (
    (START, date(2025, 1, 1)),
    (date(2025, 1, 1), date(2026, 1, 1)),
    (date(2026, 1, 1), END),
)
PCT_QUANTUM = Decimal("0.0001")


def validated_ratios(rows: Any, *, start: date = START, end: date = END) -> dict[date, Decimal]:
    if not isinstance(rows, list):
        raise ValueError("환산비율 행 형식 불일치")
    ratios: dict[date, Decimal] = {}
    for raw in rows:
        if not isinstance(raw, dict):
            raise ValueError("환산비율 행 형식 불일치")
        timestamp, value = raw.get("time"), raw.get("exchangeRate")
        if (
            isinstance(timestamp, bool)
            or not isinstance(timestamp, int)
            or timestamp % DAY_MS
            or not isinstance(value, str)
        ):
            raise ValueError("환산비율 시각/수치 형식 불일치")
        day = datetime.fromtimestamp(timestamp // 1000, tz=UTC).date()
        try:
            ratio = Decimal(value)
        except InvalidOperation as error:
            raise ValueError("환산비율 수치 변환 실패") from error
        if not ratio.is_finite() or ratio <= 0 or day in ratios:
            raise ValueError("환산비율 수치 범위/중복 불일치")
        ratios[day] = ratio
    expected = {start + timedelta(days=day) for day in range((end - start).days + 1)}
    if not expected.issubset(ratios):
        raise ValueError("고정 관찰 구간 환산비율 결측")
    return ratios


def load_ratios(
    manifest_path: Path = MANIFEST,
    *,
    expected_sha: Mapping[str, str] = EXPECTED_SHA,
    start: date = START,
    end: date = END,
) -> dict[str, dict[date, Decimal]]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or manifest.get("end_exclusive") != "2026-09-01":
        raise ValueError("환산비율 manifest 기간 불일치")
    assets = manifest.get("assets")
    if not isinstance(assets, dict) or set(assets) != {"ETH", "SOL"}:
        raise ValueError("환산비율 manifest 자산 불일치")
    result: dict[str, dict[date, Decimal]] = {}
    for asset in ("ETH", "SOL"):
        item = assets[asset]
        if not isinstance(item, dict):
            raise ValueError("환산비율 manifest 항목 불일치")
        filename, digest = item.get("path"), item.get("sha256")
        if (
            not isinstance(filename, str)
            or Path(filename).name != filename
            or digest != expected_sha[asset]
        ):
            raise ValueError("고정된 환산비율 파일/해시 불일치")
        blob = (manifest_path.parent / filename).read_bytes()
        if hashlib.sha256(blob).hexdigest() != digest:
            raise ValueError("환산비율 파일 SHA-256 불일치")
        rows = json.loads(blob)
        if not isinstance(rows, list) or len(rows) != item.get("rows"):
            raise ValueError("환산비율 행 수 불일치")
        result[asset] = validated_ratios(rows, start=start, end=end)
    return result


def period_gain_pct(ratios: Mapping[date, Decimal], start: date, end: date) -> Decimal:
    if start >= end or start not in ratios or end not in ratios:
        raise ValueError("환산비율 계산 구간 불일치")
    with localcontext() as context:
        context.prec = 40
        gain = (ratios[end] / ratios[start] - 1) * 100
        return gain.quantize(PCT_QUANTUM, rounding=ROUND_HALF_UP)


def annualized_pct(ratios: Mapping[date, Decimal], start: date, end: date) -> Decimal:
    if start >= end or start not in ratios or end not in ratios:
        raise ValueError("환산비율 계산 구간 불일치")
    with localcontext() as context:
        context.prec = 40
        factor = ratios[end] / ratios[start]
        days = Decimal((end - start).days)
        annual = ((factor.ln() * Decimal(365) / days).exp() - 1) * 100
        return annual.quantize(PCT_QUANTUM, rounding=ROUND_HALF_UP)


def summarize(ratios: Mapping[str, Mapping[date, Decimal]]) -> dict[str, Any]:
    if set(ratios) != {"ETH", "SOL"}:
        raise ValueError("ETH·SOL 두 상품 모두 필요")
    assets: dict[str, Any] = {}
    for asset in ("ETH", "SOL"):
        values = ratios[asset]
        assets[asset] = {
            "full_gain_pct": str(period_gain_pct(values, START, END)),
            "full_annualized_pct": str(annualized_pct(values, START, END)),
            "segments": [
                {
                    "start": left.isoformat(),
                    "end": right.isoformat(),
                    "elapsed_days": (right - left).days,
                    "gain_pct": str(period_gain_pct(values, left, right)),
                }
                for left, right in WINDOWS
            ],
        }
    return {
        "plan_commit": "38422a7",
        "scope": "Binance WBETH:ETH/BNSOL:SOL ratio accrual in underlying coin units only",
        "start": START.isoformat(),
        "end": END.isoformat(),
        "elapsed_days": (END - START).days,
        "source_sha256": EXPECTED_SHA,
        "assets": assets,
        "not_included": "market price, Upbit rewards, fees, redemption delay, futures, liquidation",
    }


def save_report(report: Mapping[str, Any], destination: Path = REPORT) -> Path:
    blob = (json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if destination.read_bytes() != blob:
            raise ValueError("기존 결과 파일과 불일치 — 덮어쓰지 않음")
    else:
        destination.write_bytes(blob)
    return destination


def main() -> int:
    try:
        report = summarize(load_ratios())
        path = save_report(report)
    except (KeyError, OSError, TypeError, ValueError) as error:
        print(f"환산비율 과거 관찰 중단: {type(error).__name__}", file=sys.stderr)
        return 2
    print(f"환산비율 기술통계: {path}")
    for asset in ("ETH", "SOL"):
        item = report["assets"][asset]
        print(f"{asset}: 코인 증가 {item['full_gain_pct']}%, 연환산 {item['full_annualized_pct']}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
