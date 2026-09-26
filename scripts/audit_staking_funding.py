"""문서 90: ETH·SOL 공통 기간 funding 정산 mark 가격과 로컬 DB를 감사한다.

`python -m scripts.audit_staking_funding` — Binance 공개 GET과 로컬 DB 읽기만 수행.
원본 필드를 정규화해 Git 제외 `data/research/staking/`에 해시로 보존한다.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Protocol

from auto_trading.core.errors import AutoTradingError
from auto_trading.exchange.binance_usdm_public import BinanceUsdmPublic

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "data" / "research" / "staking"
DB = REPO / "data" / "trading.db"
START_MS = int(datetime(2024, 8, 28, tzinfo=UTC).timestamp() * 1000)
END_MS = int(datetime(2026, 9, 1, tzinfo=UTC).timestamp() * 1000)
LIMIT = 1000


class FundingReader(Protocol):
    def _get(self, path: str, params: Mapping[str, Any]) -> Any: ...


def parse_page(payload: Any, symbol: str, begin_ms: int, end_ms: int) -> list[dict[str, Any]]:
    if not isinstance(payload, list) or len(payload) > LIMIT:
        raise ValueError("funding 이력 페이지 형식/크기 불일치")
    parsed: list[dict[str, Any]] = []
    seen: set[int] = set()
    for raw in payload:
        if not isinstance(raw, dict) or raw.get("symbol") != symbol:
            raise ValueError("funding 이력 심볼/행 형식 불일치")
        timestamp = raw.get("fundingTime")
        if isinstance(timestamp, bool) or not isinstance(timestamp, int):
            raise ValueError("funding 시각 형식 불일치")
        if not begin_ms <= timestamp < end_ms or timestamp in seen:
            raise ValueError("funding 시각 범위/중복 불일치")
        seen.add(timestamp)
        rate, mark = raw.get("fundingRate"), raw.get("markPrice")
        if not isinstance(rate, str) or not isinstance(mark, str):
            raise ValueError("funding 비율/mark 필드 형식 불일치")
        try:
            rate_value = Decimal(rate)
            mark_value = Decimal(mark) if mark else None
        except InvalidOperation as error:
            raise ValueError("funding 비율/mark 수치 변환 실패") from error
        if not rate_value.is_finite() or (
            mark_value is not None and (not mark_value.is_finite() or mark_value <= 0)
        ):
            raise ValueError("funding 비율/mark 범위 불일치")
        rate_type = raw.get("rateType", "Regular")
        if rate_type != "Regular":
            raise ValueError("예상 밖 funding rateType")
        parsed.append(
            {
                "symbol": symbol,
                "fundingTime": timestamp,
                "fundingRate": rate,
                "markPrice": mark,
                "rateType": rate_type,
            }
        )
    if [row["fundingTime"] for row in parsed] != sorted(row["fundingTime"] for row in parsed):
        raise ValueError("funding 페이지 시각 정렬 불일치")
    return parsed


def fetch_asset(reader: FundingReader, asset: str) -> list[dict[str, Any]]:
    if asset not in ("ETH", "SOL"):
        raise ValueError("ETH·SOL만 허용")
    symbol = f"{asset}USDT"
    cursor = START_MS
    rows: list[dict[str, Any]] = []
    while cursor < END_MS:
        page = parse_page(
            reader._get(
                "/fapi/v1/fundingRate",
                {"symbol": symbol, "startTime": cursor, "endTime": END_MS - 1, "limit": LIMIT},
            ),
            symbol,
            cursor,
            END_MS,
        )
        if not page:
            break
        rows.extend(page)
        following = page[-1]["fundingTime"] + 1
        if following <= cursor:
            raise ValueError("funding 페이지 진행 불가")
        cursor = following
        if len(page) < LIMIT:
            break
    if not rows:
        raise ValueError(f"{asset} funding 이력 0건")
    return rows


def audit_local(db_path: Path, asset: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    with sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True) as conn:
        conn.execute("PRAGMA query_only=ON")
        local_rows = conn.execute(
            "SELECT funding_ms, rate, mark_price FROM funding_rates"
            " WHERE exchange = ? AND symbol = ? AND funding_ms >= ? AND funding_ms < ?",
            ("binance_usdm", f"{asset}USDT", START_MS, END_MS),
        ).fetchall()
    local = {int(timestamp): (Decimal(rate), mark) for timestamp, rate, mark in local_rows}
    remote = {int(row["fundingTime"]): Decimal(row["fundingRate"]) for row in rows}
    if len(local) != len(local_rows) or len(remote) != len(rows):
        raise ValueError("funding 이력 중복")
    common = local.keys() & remote.keys()
    return {
        "local_rows": len(local),
        "remote_rows": len(remote),
        "remote_missing_from_local": len(remote.keys() - local.keys()),
        "local_missing_from_remote": len(local.keys() - remote.keys()),
        "rate_mismatches": sum(local[timestamp][0] != remote[timestamp] for timestamp in common),
        "local_mark_present": sum(mark is not None for _, mark in local.values()),
        "remote_mark_missing": sum(not row["markPrice"] for row in rows),
    }


def save_snapshot(
    history: Mapping[str, list[dict[str, Any]]],
    audits: Mapping[str, dict[str, Any]],
    *,
    destination: Path = DATA,
    fetched_at_ms: int | None = None,
) -> Path:
    if set(history) != {"ETH", "SOL"} or set(audits) != {"ETH", "SOL"}:
        raise ValueError("ETH·SOL 이력과 감사가 모두 필요")
    fetched_ms = int(time.time() * 1000) if fetched_at_ms is None else fetched_at_ms
    manifest: dict[str, Any] = {
        "fetched_at_ms": fetched_ms,
        "start_ms": START_MS,
        "end_exclusive_ms": END_MS,
        "source": "Binance public /fapi/v1/fundingRate",
        "assets": {},
    }
    blobs: dict[str, bytes] = {}
    for asset in ("ETH", "SOL"):
        rows = history[asset]
        if not rows:
            raise ValueError(f"{asset} funding 이력 0건")
        blob = (
            json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode()
        digest = hashlib.sha256(blob).hexdigest()
        filename = f"{asset.lower()}-funding-{digest[:16]}.json"
        blobs[filename] = blob
        manifest["assets"][asset] = {"path": filename, "sha256": digest, **audits[asset]}
    destination.mkdir(parents=True, exist_ok=True)
    manifest_file = destination / f"funding-manifest-{fetched_ms}.json"
    if manifest_file.exists():
        raise ValueError("동일 시각 funding manifest 존재")
    for filename, blob in blobs.items():
        target = destination / filename
        if target.exists():
            if hashlib.sha256(target.read_bytes()).hexdigest() != hashlib.sha256(blob).hexdigest():
                raise ValueError("기존 funding 스냅숏 체크섬 불일치")
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
        audits = {asset: audit_local(DB, asset, history[asset]) for asset in ("ETH", "SOL")}
        manifest = save_snapshot(history, audits)
    except (AutoTradingError, OSError, sqlite3.Error, TypeError, ValueError) as error:
        print(f"funding mark 감사 중단: {type(error).__name__}", file=sys.stderr)
        return 2
    print(f"funding mark 스냅숏: {manifest}")
    for asset in ("ETH", "SOL"):
        print(f"{asset}: {json.dumps(audits[asset], ensure_ascii=False)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
