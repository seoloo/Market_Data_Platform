"""Keyless one-shot quote structure probe and deterministic synthetic accounting."""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from collections.abc import Mapping
from dataclasses import asdict
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import httpx

from auto_trading.core.errors import AutoTradingError, DataIntegrityError
from auto_trading.research.execution_evidence_20261013 import (
    Depth,
    FillEvidence,
    FundingCash,
    Mark,
    OrderEvidence,
    Rules,
    Scope,
    audit_orders,
    estimate_depth,
    replay_short,
)

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "reports" / "short_execution_evidence_20261013"
BASE = "https://fapi.binance.com"
SYMBOLS = ("BTCUSDT", "ETHUSDT")


def now_ms() -> int:
    return time.time_ns() // 1_000_000


def save_once(name: str, payload: object) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with (OUT / name).open("x", encoding="utf8") as out:
        out.write(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n")


def rules_from_info(info: Mapping[str, Any], symbol: str) -> Rules:
    row = next(r for r in info["symbols"] if r["symbol"] == symbol)
    if row["status"] != "TRADING" or row["contractType"] != "PERPETUAL":
        raise DataIntegrityError("contract unavailable")
    filters = {f["filterType"]: f for f in row["filters"]}
    return Rules(
        Decimal(filters["PRICE_FILTER"]["tickSize"]),
        Decimal(filters["LOT_SIZE"]["stepSize"]),
        Decimal(filters["LOT_SIZE"]["minQty"]),
        Decimal(filters["MIN_NOTIONAL"]["notional"]),
    )


def synthetic() -> dict[str, object]:
    scope = Scope("SYNTHETIC_USDM", "FIXED_SHORT_AUDIT", "BTCUSDT")
    orders = (
        OrderEvidence(
            scope,
            "open",
            "o1",
            "SELL",
            Decimal("0.05"),
            Decimal("0.05"),
            Decimal("0.05"),
            Decimal(49850),
            100,
            300,
            "FILLED",
        ),
        OrderEvidence(
            scope,
            "close",
            "o2",
            "BUY",
            Decimal("0.02"),
            Decimal("0.02"),
            Decimal("0.02"),
            Decimal(48150),
            400,
            600,
            "FILLED",
        ),
    )
    fills = (
        FillEvidence(
            scope,
            "o1",
            "f1",
            "SELL",
            Decimal("0.05"),
            Decimal(50000),
            Decimal("1.25"),
            "USDT",
            200,
            250,
        ),
        FillEvidence(
            scope,
            "o2",
            "f2",
            "BUY",
            Decimal("0.02"),
            Decimal(48000),
            Decimal("0.48"),
            "USDT",
            500,
            550,
        ),
    )
    evidence = audit_orders(orders, (*fills, fills[0]), as_of_ms=1000)
    ledger = replay_short(
        evidence,
        scope,
        (FundingCash(scope, "fund1", Decimal("0.5"), 350, 360),),
        initial_cash=Decimal(10000),
        mark=Mark("BTCUSDT", Decimal(49000), 1000),
        as_of_ms=1000,
    )
    expected = Decimal("10068.77")
    if ledger.equity != expected:
        raise DataIntegrityError("synthetic golden changed")
    return {
        "status": "SYNTHETIC_ACCOUNTING_ONLY",
        "ledger": asdict(ledger),
        "expected_equity": expected,
        "residual_usdt": ledger.equity - expected,
        "real_fills": 0,
        "new_performance_runs": 0,
    }


def probe() -> dict[str, object]:
    """At most six GETs; no account endpoint, credentials, order method, or retry."""
    if (OUT / "probe.json").exists():
        raise DataIntegrityError("completed probe exists: do not repeatedly sample")
    raw: list[dict[str, object]] = []

    def get(client: httpx.Client, path: str, params: dict[str, str | int]) -> tuple[Any, int]:
        if len(raw) >= 6 or path not in (
            "/fapi/v1/time",
            "/fapi/v1/exchangeInfo",
            "/fapi/v1/premiumIndex",
            "/fapi/v1/depth",
        ):
            raise DataIntegrityError("public endpoint/request budget")
        started_ms, monotonic_ns = now_ms(), time.perf_counter_ns()
        item: dict[str, object] = {"path": path, "params": params, "started_ms": started_ms}
        raw.append(item)
        response = client.get(BASE + path, params=params)
        received_ms = now_ms()
        item.update(
            status_code=response.status_code,
            received_ms=received_ms,
            latency_ms=(time.perf_counter_ns() - monotonic_ns) / 1_000_000,
        )
        response.raise_for_status()
        body = response.json()
        item["body"] = body
        return body, received_ms

    result: dict[str, object] = {
        "status": "PUBLIC_QUOTE_STRUCTURE_ONLY",
        "real_fills": 0,
        "history_read": False,
        "new_oos": False,
        "new_performance_runs": 0,
    }
    try:
        with httpx.Client(timeout=10, follow_redirects=False, trust_env=False) as client:
            clock, clock_received = get(client, "/fapi/v1/time", {})
            clock_started = int(str(raw[0]["started_ms"]))
            clock_rtt_ms = clock_received - clock_started
            if clock_rtt_ms < 0 or abs(clock_rtt_ms - float(str(raw[0]["latency_ms"]))) > 250:
                raise DataIntegrityError("wall clock changed")
            clock_offset_ms = int(clock["serverTime"]) - (clock_started + clock_received) // 2
            uncertainty_ms = (clock_rtt_ms + 1) // 2
            result.update(clock_offset_ms=clock_offset_ms, clock_uncertainty_ms=uncertainty_ms)
            info, _ = get(client, "/fapi/v1/exchangeInfo", {})
            measured: dict[str, object] = {}
            for symbol in SYMBOLS:
                rule = rules_from_info(info, symbol)
                mark_raw, mark_received = get(client, "/fapi/v1/premiumIndex", {"symbol": symbol})
                if mark_raw["symbol"] != symbol:
                    raise DataIntegrityError("mark response symbol")
                book, received = get(client, "/fapi/v1/depth", {"symbol": symbol, "limit": 100})
                mark = Mark(
                    symbol, Decimal(mark_raw["markPrice"]), mark_received, int(mark_raw["time"])
                )
                depth = Depth(
                    symbol,
                    int(book["T"]),
                    received,
                    clock_offset_ms,
                    uncertainty_ms,
                    tuple((Decimal(p), Decimal(q)) for p, q in book["bids"]),
                    tuple((Decimal(p), Decimal(q)) for p, q in book["asks"]),
                )
                targets: list[dict[str, object]] = []
                for side in ("BUY", "SELL"):
                    for usage in (Decimal(1), Decimal("0.25")):
                        targets.append(
                            asdict(
                                estimate_depth(
                                    depth,
                                    mark,
                                    rule,
                                    side,
                                    Decimal(2500) / mark.price,
                                    as_of_ms=received,
                                    visible_usage=usage,
                                )
                            )
                        )
                measured[symbol] = {"rules": asdict(rule), "estimates": targets}
            result["symbols"] = measured
    except (
        AutoTradingError,
        httpx.HTTPError,
        KeyError,
        ValueError,
        TypeError,
        StopIteration,
        InvalidOperation,
    ) as exc:
        result.update(status="PROBE_INCOMPLETE", error_type=type(exc).__name__)
    result["requests"] = len(raw)
    encoded = json.dumps(raw, sort_keys=True, default=str).encode()
    result["raw_sha256"] = hashlib.sha256(encoded).hexdigest()
    save_once("probe_raw.json", raw)
    save_once("probe.json", result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("synthetic", "probe"))
    args = parser.parse_args()
    if args.command == "synthetic":
        result = synthetic()
        save_once("synthetic.json", result)
    else:
        result = probe()
    print(json.dumps(result, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
