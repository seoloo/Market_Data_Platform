"""ETH·SOL 헤지 연구용 Binance USD-M mark 1h 봉 가용성 감사 (문서 90).

`python -m scripts.audit_staking_mark_klines` — 공개 GET만 사용한다.
mark 고저 범위는 청산 위험의 거친 상한 검사 자료일 뿐 틱 경로나 청산 판정이 아니다.
"""

from __future__ import annotations

import hashlib
import json
import sys
import time
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Protocol

from scripts.audit_staking_funding import DATA, END_MS, START_MS

from auto_trading.core.errors import AutoTradingError
from auto_trading.exchange.binance_usdm_public import BinanceUsdmPublic

HOUR_MS = 3_600_000
LIMIT = 1000
PATH = "/fapi/v1/markPriceKlines"


class MarkReader(Protocol):
    def _get(self, path: str, params: Mapping[str, Any]) -> Any: ...


def parse_page(payload: Any, begin_ms: int, end_ms: int) -> list[dict[str, int | str]]:
    if not isinstance(payload, list) or len(payload) > LIMIT:
        raise ValueError("mark 봉 페이지 형식/크기 불일치")
    parsed: list[dict[str, int | str]] = []
    previous = begin_ms - HOUR_MS
    for raw in payload:
        if not isinstance(raw, list) or len(raw) != 12:
            raise ValueError("mark 봉 행 형식 불일치")
        open_ms, close_ms = raw[0], raw[6]
        if (
            isinstance(open_ms, bool)
            or not isinstance(open_ms, int)
            or isinstance(close_ms, bool)
            or not isinstance(close_ms, int)
            or open_ms % HOUR_MS
            or not begin_ms <= open_ms < end_ms
            or open_ms <= previous
            or close_ms != open_ms + HOUR_MS - 1
        ):
            raise ValueError("mark 봉 시각/정렬/중복 불일치")
        values = raw[1:5]
        if any(not isinstance(value, str) for value in values):
            raise ValueError("mark 봉 가격 형식 불일치")
        try:
            opened, high, low, close = (Decimal(value) for value in values)
        except InvalidOperation as error:
            raise ValueError("mark 봉 가격 변환 실패") from error
        if (
            not all(value.is_finite() and value > 0 for value in (opened, high, low, close))
            or low > min(opened, close)
            or high < max(opened, close)
        ):
            raise ValueError("mark 봉 OHLC 범위 불일치")
        parsed.append(
            {
                "open_ms": open_ms,
                "open": values[0],
                "high": values[1],
                "low": values[2],
                "close": values[3],
            }
        )
        previous = open_ms
    return parsed


def fetch_asset(reader: MarkReader, asset: str) -> list[dict[str, int | str]]:
    if asset not in ("ETH", "SOL"):
        raise ValueError("ETH·SOL만 허용")
    cursor = START_MS
    rows: list[dict[str, int | str]] = []
    while cursor < END_MS:
        page = parse_page(
            reader._get(
                PATH,
                {
                    "symbol": f"{asset}USDT",
                    "interval": "1h",
                    "startTime": cursor,
                    "endTime": END_MS - 1,
                    "limit": LIMIT,
                },
            ),
            cursor,
            END_MS,
        )
        if not page:
            break
        rows.extend(page)
        cursor = int(page[-1]["open_ms"]) + HOUR_MS
        if len(rows) > (END_MS - START_MS) // HOUR_MS:
            raise ValueError("mark 봉 개수가 요청 기간을 초과함")
        if len(page) < LIMIT:
            break
    if not rows:
        raise ValueError(f"{asset} mark 봉 0건")
    return rows


def missing_hours(rows: list[dict[str, int | str]]) -> int:
    timestamps = {int(row["open_ms"]) for row in rows}
    return (END_MS - START_MS) // HOUR_MS - len(timestamps)


def save_snapshot(
    history: Mapping[str, list[dict[str, int | str]]],
    *,
    destination: Path = DATA,
    fetched_at_ms: int | None = None,
) -> Path:
    if set(history) != {"ETH", "SOL"}:
        raise ValueError("ETH·SOL mark 이력 모두 필요")
    fetched_ms = int(time.time() * 1000) if fetched_at_ms is None else fetched_at_ms
    manifest: dict[str, Any] = {
        "fetched_at_ms": fetched_ms,
        "start_ms": START_MS,
        "end_exclusive_ms": END_MS,
        "source": "Binance public /fapi/v1/markPriceKlines",
        "interval": "1h",
        "data_role": "research availability audit; not a liquidation replay",
        "assets": {},
    }
    blobs: dict[str, bytes] = {}
    for asset in ("ETH", "SOL"):
        rows = history[asset]
        if not rows:
            raise ValueError(f"{asset} mark 봉 0건")
        blob = (
            json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode()
        digest = hashlib.sha256(blob).hexdigest()
        filename = f"{asset.lower()}-mark-1h-{digest[:16]}.json"
        blobs[filename] = blob
        manifest["assets"][asset] = {
            "path": filename,
            "sha256": digest,
            "rows": len(rows),
            "first_ms": rows[0]["open_ms"],
            "last_ms": rows[-1]["open_ms"],
            "missing_hours": missing_hours(rows),
        }
    destination.mkdir(parents=True, exist_ok=True)
    manifest_file = destination / f"mark-manifest-{fetched_ms}.json"
    if manifest_file.exists():
        raise ValueError("동일 수집 시각 mark manifest 존재")
    for filename, blob in blobs.items():
        target = destination / filename
        if target.exists():
            if hashlib.sha256(target.read_bytes()).hexdigest() != hashlib.sha256(blob).hexdigest():
                raise ValueError("기존 mark 스냅숏 체크섬 불일치")
            continue
        target.write_bytes(blob)
    manifest_file.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return manifest_file


def main() -> int:
    try:
        reader = BinanceUsdmPublic()
        try:
            history = {asset: fetch_asset(reader, asset) for asset in ("ETH", "SOL")}
        finally:
            reader.close()
        manifest = save_snapshot(history)
    except (AutoTradingError, OSError, TypeError, ValueError) as error:
        print(f"mark 봉 감사 중단: {type(error).__name__}", file=sys.stderr)
        return 2
    print(f"mark 1h 스냅숏: {manifest}")
    for asset in ("ETH", "SOL"):
        print(f"{asset}: {len(history[asset])}행, 누락 시간 {missing_hours(history[asset])}개")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
