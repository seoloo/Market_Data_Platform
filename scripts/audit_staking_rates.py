"""ETH·SOL Binance 스테이킹 비율 이력 수집 및 날짜 감사 (문서 90).

`python -m scripts.audit_staking_rates` — 서명 USER_DATA GET만 사용했다. 실계좌 키로 서명하는 받기는
실주문 · 실계좌 코드를 걷어 낼 때 뺐다(README 6-3) — 이미 받아 둔 스냅숏 검증 함수는 그대로다.
계좌/보유량/지급 내역이나 주문 API는 호출하지 않는다. APR은 수익으로 계산하지 않는다.
검증된 원시 비율 행은 Git 제외 `data/research/staking/`에 해시별로 보존한다.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Protocol

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "data" / "research" / "staking"
END_EXCLUSIVE = date(2026, 9, 1)
START = {"ETH": date(2023, 4, 1), "SOL": date(2024, 7, 1)}
PATH = {
    "ETH": "/sapi/v1/eth-staking/eth/history/rateHistory",
    "SOL": "/sapi/v1/sol-staking/sol/history/rateHistory",
}
DAY_MS = 86_400_000
WINDOW_DAYS = 80  # API의 3개월 상한과 한 페이지 100건 제한보다 짧게 요청한다.


class RateReader(Protocol):
    def _request(
        self, path: str, params: Mapping[str, Any] | None = None, *, signed: bool = False
    ) -> Any: ...


def midnight_ms(day: date) -> int:
    return int(datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp() * 1000)


def windows(start: date, end_exclusive: date) -> Iterable[tuple[int, int]]:
    current = start
    while current < end_exclusive:
        following = min(current + timedelta(days=WINDOW_DAYS), end_exclusive)
        yield midnight_ms(current), midnight_ms(following)
        current = following


def parse_page(payload: Any, start_ms: int, end_ms: int) -> list[dict[str, int | str]]:
    if not isinstance(payload, dict) or not isinstance(payload.get("rows"), list):
        raise ValueError("비율 이력 응답 형식 불일치")
    rows = payload["rows"]
    if len(rows) > 100 or str(payload.get("total")) != str(len(rows)):
        raise ValueError("비율 이력 페이지가 잘렸거나 페이지네이션이 필요함")
    validated: list[dict[str, int | str]] = []
    seen: set[int] = set()
    for raw in rows:
        if not isinstance(raw, dict):
            raise ValueError("비율 이력 행 형식 불일치")
        timestamp = raw.get("time")
        if isinstance(timestamp, bool) or not isinstance(timestamp, int):
            raise ValueError("비율 이력 시각 형식 불일치")
        if not (start_ms <= timestamp < end_ms) or timestamp in seen:
            raise ValueError("비율 이력 시각 중복/요청 범위 이탈")
        if timestamp % DAY_MS:
            raise ValueError("비율 이력 시각이 UTC 일 경계가 아님")
        seen.add(timestamp)
        apr = raw.get("annualPercentageRate")
        ratio = raw.get("exchangeRate")
        if not isinstance(apr, str) or not isinstance(ratio, str):
            raise ValueError("비율 이력 수치 형식 불일치")
        try:
            apr_value, ratio_value = Decimal(apr), Decimal(ratio)
        except InvalidOperation as error:
            raise ValueError("비율 이력 수치 변환 실패") from error
        if not apr_value.is_finite() or not ratio_value.is_finite() or ratio_value <= 0:
            raise ValueError("비율 이력 수치 범위 불일치")
        validated.append({"time": timestamp, "annualPercentageRate": apr, "exchangeRate": ratio})
    return validated


def fetch_asset(
    reader: RateReader,
    asset: str,
    *,
    end_exclusive: date = END_EXCLUSIVE,
    pause: Callable[[float], None] = time.sleep,
) -> list[dict[str, int | str]]:
    if asset not in PATH:
        raise ValueError("ETH·SOL만 허용")
    by_time: dict[int, dict[str, int | str]] = {}
    for start_ms, end_ms in windows(START[asset], end_exclusive):
        payload = reader._request(
            PATH[asset],
            {"startTime": start_ms, "endTime": end_ms - 1, "size": 100},
            signed=True,
        )
        for row in parse_page(payload, start_ms, end_ms):
            timestamp = int(row["time"])
            if timestamp in by_time:
                raise ValueError("여러 창에서 동일 시각 중복")
            by_time[timestamp] = row
        pause(0.35)
    if not by_time:
        raise ValueError(f"{asset} 비율 이력 0건")
    return [by_time[timestamp] for timestamp in sorted(by_time)]


def missing_dates(rows: list[dict[str, int | str]]) -> list[str]:
    timestamps = {int(row["time"]) for row in rows}
    return [
        datetime.fromtimestamp(timestamp / 1000, tz=UTC).date().isoformat()
        for timestamp in range(min(timestamps), max(timestamps) + DAY_MS, DAY_MS)
        if timestamp not in timestamps
    ]


def save_snapshot(
    history: Mapping[str, list[dict[str, int | str]]],
    *,
    destination: Path = DATA,
    fetched_at_ms: int | None = None,
) -> Path:
    if set(history) != {"ETH", "SOL"}:
        raise ValueError("ETH·SOL 두 이력 모두 필요")
    fetched_ms = int(time.time() * 1000) if fetched_at_ms is None else fetched_at_ms
    manifest: dict[str, Any] = {
        "fetched_at_ms": fetched_ms,
        "end_exclusive": END_EXCLUSIVE.isoformat(),
        "source": "Binance signed USER_DATA staking rateHistory",
        "data_role": "Binance WBETH/BNSOL only; not Upbit realized rewards",
        "assets": {},
    }
    blobs: dict[str, bytes] = {}
    for asset in ("ETH", "SOL"):
        rows = history[asset]
        if not rows:
            raise ValueError(f"{asset} 비율 이력 0건")
        blob = (
            json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode()
        digest = hashlib.sha256(blob).hexdigest()
        filename = f"{asset.lower()}-rates-{digest[:16]}.json"
        blobs[filename] = blob
        manifest["assets"][asset] = {
            "path": filename,
            "sha256": digest,
            "source_path": PATH[asset],
            "rows": len(rows),
            "first_ms": rows[0]["time"],
            "last_ms": rows[-1]["time"],
            "missing_dates": missing_dates(rows),
        }
    destination.mkdir(parents=True, exist_ok=True)
    for filename, blob in blobs.items():
        target = destination / filename
        if target.exists():
            if hashlib.sha256(target.read_bytes()).hexdigest() != hashlib.sha256(blob).hexdigest():
                raise ValueError("기존 스냅숏 체크섬 불일치")
            continue
        target.write_bytes(blob)
    manifest_file = destination / f"manifest-{fetched_ms}.json"
    if manifest_file.exists():
        raise ValueError("동일 수집 시각 manifest 존재")
    manifest_file.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return manifest_file


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DATA)
    args = parser.parse_args()
    print(
        "스테이킹 이력 받기 없음 — 실계좌 키로 서명하던 부분을 뺐다(README 6-3). "
        f"저장된 스냅숏: {args.output}",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
