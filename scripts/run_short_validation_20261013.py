"""Execution-data audit and frozen Laya/no-Laya pilot. No accounts, orders or operating writes."""

from __future__ import annotations

import bisect
import csv
import hashlib
import io
import json
import math
import random
import statistics
import subprocess
import sys
import time
import zipfile
from dataclasses import asdict
from decimal import Decimal
from typing import Any

import httpx

from auto_trading.backtest.power import detectable_effect_bps, required_samples
from auto_trading.config.secrets import read_env_file
from auto_trading.research import complement_20261013 as roles
from auto_trading.research import short_validation_20261013 as pure
from auto_trading.research.short_search_229 import H4, Coin, build_coin
from scripts import run_complement_pilot_20261013 as complement
from scripts import run_rsi_stoch_pilot_20261012 as base

OUT = base.REPO / "reports/short_validation_laya_20261013"
DOC = base.REPO / "docs/research/SHORT_EXECUTION_LAYA_VALIDATION_20261013.md"
FILES = (
    *complement.CODE_FILES,
    "scripts/run_short_validation_20261013.py",
    "src/auto_trading/research/short_validation_20261013.py",
)
CALL_CAP = 512
CALL_PRICE = 0.05 / 1000  # current published estimate, not a measured account debit
SEED = 20261013


def code_hash() -> str:
    h = hashlib.sha256()
    for file in FILES:
        h.update(file.encode())
        h.update((base.REPO / file).read_bytes())
    return h.hexdigest()


def frozen(rule: str) -> base.Config:
    if rule == "RSI":
        return base.Config("RSI", 7, 70, 0, 0, "R5", "48H", "0.03")
    if rule == "KELTNER":
        return complement.ledger_config(
            roles.RoleConfig("RSI7_70", "R5", "KELTNER", "NONE", "NONE")
        )
    raise ValueError(rule)


def opportunities(coins: list[Coin]) -> dict[tuple[str, int], dict[str, Any]]:
    engine = complement.Engine(coins)
    out: dict[tuple[str, int], dict[str, Any]] = {}
    cfg = frozen("RSI")
    for c in coins:
        for i in engine.signals(c, cfg):
            if c.regime[cfg.regime][i] and base.START <= c.t[i] + H4 < base.END - base.PURGE:
                fields = pure.snapshot_fields(c, i)
                text = pure.state_text(fields)
                out[c.name, i] = {
                    "symbol": c.name,
                    "i": i,
                    "as_of_ms": c.t[i] + H4,
                    "fields": fields,
                    "state": text,
                    "key": pure.request_key(text),
                }
    return out


def month_names() -> list[str]:
    return [
        f"{y:04d}-{m:02d}"
        for y in range(2020, 2027)
        for m in range(1, 13)
        if "2020-06" <= f"{y:04d}-{m:02d}" <= "2026-08"
    ]


def load_canonical() -> tuple[list[Coin], dict[str, Any]]:
    coins, quality = base.load()
    path = OUT / "canonical4h.json"
    data = json.loads(path.read_text(encoding="utf8"))
    if data["legacy_source_sha256"] != quality["source_sha256"]:
        raise ValueError("canonical preparation source changed")
    combined = []
    for c in coins:
        preparation = [
            (t, c.o[i], c.h[i], c.lo[i], c.c[i], c.v[i])
            for i, t in enumerate(c.t)
            if t < base.START
        ]
        records = [
            (int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5]))
            for r in data["bars"][c.name]
        ]
        combined.append(
            build_coin(c.name, [*preparation, *records], list(zip(c.fund_t, c.fund_r, strict=True)))
        )
    quality["legacy_source_sha256"] = quality["source_sha256"]
    quality["canonical4h_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    quality["source_sha256"] = hashlib.sha256(
        (quality["source_sha256"] + quality["canonical4h_sha256"]).encode()
    ).hexdigest()
    return combined, quality


def funding_archive() -> dict[str, list[dict[str, Any]]]:
    path = OUT / "funding_rest.json"
    if path.exists():
        return dict(json.loads(path.read_text(encoding="utf8")))
    result: dict[str, list[dict[str, Any]]] = {}
    with httpx.Client(timeout=30) as client:
        for symbol in base.SYMBOLS:
            rows: list[dict[str, Any]] = []
            start = base.START
            for _ in range(12):
                response = client.get(
                    "https://fapi.binance.com/fapi/v1/fundingRate",
                    params={
                        "symbol": symbol,
                        "startTime": start,
                        "endTime": base.END - 1,
                        "limit": 1000,
                    },
                )
                if response.status_code != 200:
                    raise ValueError(f"public funding archive HTTP {response.status_code}")
                batch = response.json()
                if not isinstance(batch, list):
                    raise ValueError("public funding response schema")
                if not batch:
                    break
                if any(not start <= int(x["fundingTime"]) < base.END for x in batch):
                    raise ValueError("public funding response outside cutoff")
                rows.extend(batch)
                start = int(batch[-1]["fundingTime"]) + 1
                if len(batch) < 1000:
                    break
                time.sleep(0.15)
            result[symbol] = rows
    base.save(path, result)
    return result


def merged_windows(events: list[int]) -> list[tuple[int, int]]:
    windows: list[tuple[int, int]] = []
    for at in sorted(events):
        lo, hi = at - pure.MINUTE, min(at + base.PURGE, base.END)
        if windows and lo <= windows[-1][1]:
            windows[-1] = windows[-1][0], max(hi, windows[-1][1])
        else:
            windows.append((lo, hi))
    return windows


def supplement_mark(symbol: str, hour_ms: int) -> tuple[Decimal, Decimal, Decimal]:
    path = OUT / "mark_hour_supplement.json"
    cache = json.loads(path.read_text(encoding="utf8")) if path.exists() else {}
    key = f"{symbol}:{hour_ms}"
    if key not in cache:
        if len(cache) >= 64 or symbol not in base.SYMBOLS or not base.START <= hour_ms < base.END:
            raise ValueError("mark supplement scope/budget")
        with httpx.Client(timeout=30) as client:
            r = client.get(
                "https://fapi.binance.com/fapi/v1/markPriceKlines",
                params={
                    "symbol": symbol,
                    "interval": "1h",
                    "startTime": hour_ms,
                    "endTime": hour_ms + 3600000 - 1,
                    "limit": 1,
                },
            )
            if r.status_code != 200:
                raise ValueError(f"public mark supplement HTTP {r.status_code}")
            rows = r.json()
            if not isinstance(rows, list) or len(rows) != 1 or int(rows[0][0]) != hour_ms:
                raise ValueError("public mark supplement returned wrong hour")
            cache[key] = rows[0]
            base.save(path, cache)
    row = cache[key]
    return Decimal(row[1]), Decimal(row[3]), Decimal(row[2])


def load_execution(
    coins: list[Coin], obs: dict[tuple[str, int], dict[str, Any]]
) -> tuple[dict[str, dict[int, pure.MinuteBar]], dict[str, list[pure.Funding]], dict[str, Any]]:
    raw = funding_archive()
    minutes: dict[str, dict[int, pure.MinuteBar]] = {}
    funds: dict[str, list[pure.Funding]] = {}
    audit: dict[str, Any] = {}
    root = base.REPO / "data/external/binance_vision"
    legacy_coins, legacy_quality = base.load()
    legacy = {c.name: c for c in legacy_coins}
    canonical: dict[str, list[list[float | int]]] = {}
    for c in coins:
        windows = merged_windows([x["as_of_ms"] for (s, _), x in obs.items() if s == c.name])
        starts = [w[0] for w in windows]
        kept: dict[int, pure.MinuteBar] = {}
        marks: dict[int, tuple[Decimal, Decimal, Decimal]] = {}
        file_hashes: dict[str, str] = {}
        bar_rows = 0
        previous_ms: int | None = None
        gap = mismatches = 0
        mismatch_examples: list[dict[str, Any]] = []
        aggregate: list[float] | None = None
        aggregate_at = -1
        legacy_c = legacy[c.name]
        legacy_reference = {t: i for i, t in enumerate(legacy_c.t)}
        canonical[c.name] = []
        for kind, tf in (("um", "1m"), ("um_mark", "1h")):
            for month in month_names():
                path = root / kind / c.name / tf / f"{c.name}-{tf}-{month}.zip"
                if not path.is_file():
                    raise ValueError(f"required local ZIP missing {path.name}/{kind}")
                file_hashes[f"{kind}/{path.name}"] = hashlib.sha256(path.read_bytes()).hexdigest()
                with zipfile.ZipFile(path) as zipped, zipped.open(zipped.namelist()[0]) as stream:
                    for row in csv.reader(io.TextIOWrapper(stream, encoding="utf8")):
                        if not row[0].isdigit():
                            if row[0] in ("open_time", "open_time_ms"):
                                continue
                            raise ValueError("unexpected archive header")
                        at = int(row[0])
                        if not base.START <= at < base.END:
                            raise ValueError("ZIP row outside registered cutoff")
                        if kind == "um_mark":
                            marks[at] = Decimal(row[1]), Decimal(row[3]), Decimal(row[2])
                            continue
                        if previous_ms is not None and at != previous_ms + pure.MINUTE:
                            gap += 1
                        previous_ms, bar_rows = at, bar_rows + 1
                        o, h, lo, cl, vol = map(float, row[1:6])
                        if (
                            not all(math.isfinite(v) for v in (o, h, lo, cl, vol))
                            or not 0 < lo <= min(o, cl) <= max(o, cl) <= h
                            or vol < 0
                        ):
                            raise ValueError("invalid minute OHLCV")
                        h4 = at // H4 * H4
                        if aggregate_at != h4:
                            aggregate_at, aggregate = h4, [o, h, lo, cl, vol]
                        elif aggregate is not None:
                            aggregate[1], aggregate[2], aggregate[3], aggregate[4] = (
                                max(aggregate[1], h),
                                min(aggregate[2], lo),
                                cl,
                                aggregate[4] + vol,
                            )
                        if at % H4 == H4 - pure.MINUTE and aggregate is not None:
                            canonical[c.name].append([h4, *aggregate])
                            i = legacy_reference.get(h4)
                            if i is None or any(
                                abs(v - ref) > max(1e-7, abs(ref) * 1e-8)
                                for v, ref in zip(
                                    aggregate,
                                    (
                                        legacy_c.o[i],
                                        legacy_c.h[i],
                                        legacy_c.lo[i],
                                        legacy_c.c[i],
                                        legacy_c.v[i],
                                    ),
                                    strict=True,
                                )
                            ):
                                mismatches += 1
                                if i is not None:
                                    mismatch_examples.append(
                                        {
                                            "at_ms": h4,
                                            "minute_aggregate": list(aggregate),
                                            "legacy4h": [
                                                legacy_c.o[i],
                                                legacy_c.h[i],
                                                legacy_c.lo[i],
                                                legacy_c.c[i],
                                                legacy_c.v[i],
                                            ],
                                        }
                                    )
                        wi = bisect.bisect_right(starts, at) - 1
                        if wi >= 0 and at < windows[wi][1]:
                            kept[at] = pure.MinuteBar(
                                at,
                                Decimal(row[1]),
                                Decimal(row[2]),
                                Decimal(row[3]),
                                Decimal(row[4]),
                                Decimal(row[5]),
                            )
                sys.stdout.write(
                    json.dumps({"phase": "ARCHIVE", "symbol": c.name, "kind": kind, "month": month})
                    + "\n"
                )
                sys.stdout.flush()
        if gap or bar_rows != (base.END - base.START) // pure.MINUTE:
            base.save(
                OUT / f"minute_mismatch_{c.name}.json",
                {"gap": gap, "rows": bar_rows, "mismatches": mismatch_examples},
            )
            raise ValueError(
                f"minute completeness/parity failed {c.name}: {gap}/{mismatches}/{bar_rows}"
            )
        by_time = {int(x["fundingTime"]): x for x in raw[c.name]}
        events: list[pure.Funding] = []
        exact = rates_differ = rest_missing = supplement_count = 0
        for at, rate in zip(c.fund_t, c.fund_r, strict=True):
            if not base.START <= at < base.END:
                continue
            r = by_time.get(at)
            if r is None:
                rest_missing += 1
            elif Decimal(str(rate)) != Decimal(str(r["fundingRate"])):
                rates_differ += 1
            hourly = marks.get(at // 3600000 * 3600000)
            if hourly is None:
                hourly = supplement_mark(c.name, at // 3600000 * 3600000)
                supplement_count += 1
            mark, mark_low, mark_high = hourly
            is_exact = (
                r is not None and bool(r.get("markPrice")) and Decimal(str(r["markPrice"])) > 0
            )
            if is_exact and r is not None:
                mark = mark_low = mark_high = Decimal(str(r["markPrice"]))
                exact += 1
            events.append(pure.Funding(at, Decimal(str(rate)), mark, mark_low, mark_high, is_exact))
        if rates_differ:
            raise ValueError("REST funding rate differs from frozen source")
        minutes[c.name], funds[c.name] = kept, events
        audit[c.name] = {
            "minute_rows": bar_rows,
            "minute_gaps": gap,
            "legacy4h_mismatches": mismatches,
            "legacy_mismatch_examples": mismatch_examples,
            "canonical_reaggregate4h_mismatches": 0,
            "kept_execution_minutes": len(kept),
            "mark_hour_rows": len(marks),
            "funding_events": len(events),
            "funding_exact_mark": exact,
            "funding_hour_open_proxy": len(events) - exact,
            "rest_exact_timestamp_missing": rest_missing,
            "funding_mark_hour_supplements": supplement_count,
            "file_sha256": file_hashes,
        }
    payload = {"legacy_source_sha256": legacy_quality["source_sha256"], "bars": canonical}
    target = OUT / "canonical4h.json"
    if target.exists() and json.loads(target.read_text(encoding="utf8")) != payload:
        raise ValueError("canonical minute source changed")
    if not target.exists():
        base.save(target, payload)
    return minutes, funds, audit


def ensure_registered() -> None:
    files = [
        *FILES,
        str(DOC.relative_to(base.REPO)),
        "tests/scripts/test_short_validation_20261013.py",
    ]
    for f in files:
        subprocess.run(
            ["git", "ls-files", "--error-unmatch", f],
            cwd=base.REPO,
            check=True,
            capture_output=True,
        )
    if subprocess.check_output(["git", "diff", "HEAD", "--", *files], cwd=base.REPO):
        raise ValueError("commit registration/code/tests before predictions or PnL")


def ask() -> None:
    ensure_registered()
    coins, quality = load_canonical()
    obs = opportunities(coins)
    states = {x["key"]: x["state"] for x in obs.values()}
    keys = sorted(states)
    probes = keys[:16]
    variants = {
        pure.request_key(pure.state_text(obs_key["fields"], reverse=True)): pure.state_text(
            obs_key["fields"], reverse=True
        )
        for obs_key in sorted(obs.values(), key=lambda x: x["key"])[:32]
    }
    states.update(variants)
    if len(states) + len(probes) > CALL_CAP:
        raise ValueError("fixed Laya budget/call cap exceeded")
    env = read_env_file(base.REPO / ".env")
    if not env.get("LAYA_API_KEY") or env.get("LAYA_OFF", "").lower() == "true":
        raise ValueError("Laya unavailable/disabled; do not change operating env")
    if env.get("LAYA_BASE_URL", "https://api.laya-ai.com").rstrip("/") != "https://api.laya-ai.com":
        raise ValueError("research provider allowlist mismatch")
    file = OUT / "laya_answers.json"
    cache = json.loads(file.read_text(encoding="utf8")) if file.exists() else {}
    if cache and cache["code_sha256"] != code_hash():
        raise ValueError("Laya cache code changed")
    if cache and cache["source_sha256"] != quality["source_sha256"]:
        raise ValueError("Laya cache data changed")
    cache.setdefault("code_sha256", code_hash())
    cache.setdefault("source_sha256", quality["source_sha256"])
    cache.setdefault("registration_doc_sha256", hashlib.sha256(DOC.read_bytes()).hexdigest())
    if cache["registration_doc_sha256"] != hashlib.sha256(DOC.read_bytes()).hexdigest():
        raise ValueError("Laya cache registration changed")
    cache.setdefault("responses", {})
    cache.setdefault("batches", [])
    requests = [(k, s) for k, s in sorted(states.items()) if k not in cache["responses"]]
    if not cache.get("repeat_complete"):
        requests += [
            ("repeat:" + k, states[k]) for k in probes if "repeat:" + k not in cache["responses"]
        ]
    if sum(b["states"] for b in cache["batches"]) + len(requests) > CALL_CAP:
        raise ValueError("cumulative Laya call budget exceeded")
    with httpx.Client(timeout=30) as client:
        if "public_model_metadata" not in cache:
            metadata = client.get("https://api.laya-ai.com/v1/models")
            cache["public_model_metadata"] = (
                metadata.json() if metadata.status_code == 200 else None
            )
        for start in range(0, len(requests), 16):
            batch = requests[start : start + 16]
            at_ms = time.time_ns() // 1000000
            started = time.monotonic()
            try:
                response = client.post(
                    "https://api.laya-ai.com/v1/systemone/batch",
                    json={
                        "states": [s for _, s in batch],
                        "questions": pure.QUESTION,
                        "model": pure.MODEL,
                        "max_len": 1024,
                    },
                    headers={"Authorization": "Bearer " + env["LAYA_API_KEY"]},
                )
                data = response.json() if response.status_code == 200 else {}
                results = data.get("results") or []
                if not isinstance(results, list):
                    results = []
                status = response.status_code
            except (httpx.HTTPError, ValueError, TypeError):
                results, status = [], 0
            elapsed_ms = (time.monotonic() - started) * 1000
            cache["batches"].append(
                {
                    "request_ms": at_ms,
                    "response_ms": time.time_ns() // 1000000,
                    "elapsed_ms": elapsed_ms,
                    "status": status,
                    "states": len(batch),
                }
            )
            for index, (key, state) in enumerate(batch):
                raw = (
                    results[index]
                    if index < len(results) and isinstance(results[index], dict)
                    else None
                )
                cache["responses"][key] = {
                    "raw": raw,
                    "p_positive": pure.probability(raw),
                    "state": state,
                    "request_ms": at_ms,
                    "response_ms": time.time_ns() // 1000000,
                    "batch_elapsed_ms": elapsed_ms,
                }
            base.save(file, cache)
            sys.stdout.write(
                json.dumps(
                    {
                        "phase": "LAYA",
                        "done": min(start + 16, len(requests)),
                        "total": len(requests),
                        "status": status,
                    }
                )
                + "\n"
            )
            sys.stdout.flush()
    cache["repeat_complete"] = True
    cache["estimated_api_usd"] = sum(b["states"] for b in cache["batches"]) * CALL_PRICE
    base.save(file, cache)


class Engine(complement.Engine):
    def __init__(
        self,
        coins: list[Coin],
        funding: dict[str, list[pure.Funding]],
        answers: dict[str, Any],
        obs: dict[tuple[str, int], dict[str, Any]],
        *,
        market: bool,
    ) -> None:
        super().__init__(coins)
        self.market, self.answers, self.obs = market, answers, obs
        for c in coins:
            marks = {f.at_ms: f.mark for f in funding[c.name]}
            total, negative = [base.ZERO], [base.ZERO]
            for at, rate in zip(c.fund_t, c.fund_r, strict=True):
                value = marks.get(at, base.ZERO) * base.dec(rate)
                total.append(total[-1] + value)
                negative.append(negative[-1] + min(base.ZERO, value))
            self.fund_prefix[c.name] = total, negative

    def signals(self, c: Coin, cfg: base.Config) -> list[int]:
        rule, mode = cfg.mode.split("_")
        original = frozen(rule)
        result = []
        for i in super().signals(c, original):
            if not c.regime[original.regime][i]:
                continue
            if mode == "FULL":
                result.append(i)
                continue
            item = self.obs.get((c.name, i))
            answer = self.answers.get(item["key"]) if item else None
            p = answer["p_positive"] if answer else None
            if (
                p is not None
                and answer is not None
                and answer["batch_elapsed_ms"] <= 60000
                and (mode == "OFF" or p >= pure.THRESHOLD)
            ):
                result.append(i)
        return result

    def _episode(
        self, c: Coin, signal: int, cfg: base.Config, end_ms: int, cost: int, delay: int
    ) -> base.Episode | None:
        if not self.market:
            return super()._episode(c, signal, cfg, end_ms, cost, delay)
        j, end_i = signal + 1 + delay, bisect.bisect_left(c.t, end_ms)
        if j >= end_i or not math.isfinite(c.ind["atr"][signal]) or c.v[j - 1] <= 0:
            return None
        entry = base.dec(c.o[j]) * (base.ONE - base.SLIP * cost)
        stop = entry * base.dec("1.03")
        q, stopped, unresolved = j, False, False
        for q in range(j, end_i):
            if q >= j + 12 and c.v[q - 1] > 0:
                px, exit_ms = base.dec(c.o[q]) * (base.ONE + base.SLIP * cost), c.t[q]
                break
            if base.dec(c.h[q]) >= stop:
                px = max(base.dec(c.o[q]), stop) * (base.ONE + (base.STOP_SLIP + base.SLIP) * cost)
                exit_ms, stopped = c.t[q] + H4, True
                break
        else:
            px, exit_ms, unresolved = base.dec(c.c[q]), c.t[q] + H4, True
        a, b = bisect.bisect_right(c.fund_t, c.t[j]), bisect.bisect_right(c.fund_t, exit_ms)
        total, neg = self.fund_prefix[c.name]
        known = max(a, bisect.bisect_right(c.fund_t, c.t[q]))
        cash = total[known] - total[a] + neg[b] - neg[known] if stopped else total[b] - total[a]
        fee = base.FEE * cost * (entry + px) / entry
        raw_out = (
            px / (base.ONE + (base.STOP_SLIP + base.SLIP) * cost) if stopped else base.dec(c.o[q])
        )
        if unresolved:
            raw_out = px
        slip = ((base.dec(c.o[j]) - entry) + max(base.ZERO, px - raw_out)) / entry
        return base.Episode(
            c.name,
            signal,
            j,
            q,
            c.t[j],
            exit_ms,
            entry,
            px,
            (entry - px) / entry,
            fee,
            cash / entry,
            slip,
            stopped,
            unresolved,
            0,
        )


def config(rule: str, mode: str) -> base.Config:
    return base.Config(rule + "_" + mode, 0, 0, 0, 0, "R0", "48H", "0.03")


def run() -> None:
    ensure_registered()
    if (OUT / "result.json").exists():
        raise ValueError("completed one-shot pilot cannot be rerun")
    coins, quality = load_canonical()
    obs = opportunities(coins)
    minutes, funds, audit = load_execution(coins, obs)
    cache = json.loads((OUT / "laya_answers.json").read_text(encoding="utf8"))
    if cache["code_sha256"] != code_hash() or cache["source_sha256"] != quality["source_sha256"]:
        raise ValueError("Laya input/cache lineage differs")
    decisions = cache["responses"]
    lineage = {
        "code_sha256": code_hash(),
        "source_sha256": quality["source_sha256"],
        "audit": audit,
        "laya_sha256": hashlib.sha256((OUT / "laya_answers.json").read_bytes()).hexdigest(),
        "funding_rest_sha256": hashlib.sha256((OUT / "funding_rest.json").read_bytes()).hexdigest(),
        "mark_supplement_sha256": hashlib.sha256(
            (OUT / "mark_hour_supplement.json").read_bytes()
        ).hexdigest(),
        "registration_git": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, cwd=base.REPO
        ).strip(),
    }
    mf = OUT / "manifest.json"
    if mf.exists():
        original = json.loads(mf.read_text(encoding="utf8"))
        if any(original[k] != v for k, v in lineage.items() if k != "registration_git"):
            raise ValueError("resume source/code/answers changed")
        lineage = original
    base.save(mf, lineage)
    details: dict[str, Any] = {}
    paired: dict[str, Any] = {}
    execution: dict[str, Any] = {}
    for market in (False, True):
        engine = Engine(coins, funds, decisions, obs, market=market)
        order = "MARKET" if market else "IOC"
        for rule in ("RSI", "KELTNER"):
            for mode in ("FULL", "OFF", "ON"):
                key = f"{rule}_{mode}_{order}"
                checkpoint = OUT / f"detail_{key}.json"
                if checkpoint.exists():
                    details[key] = json.loads(checkpoint.read_text(encoding="utf8"))
                    continue
                result: dict[str, Any] = {}
                for label, cost, delay in (("base", 1, 0), ("cost2", 2, 0), ("delay4h", 1, 1)):
                    result[label] = {}
                    for period, bounds in base.PERIODS.items():
                        episodes, counts = engine.trades(config(rule, mode), *bounds, cost, delay)
                        result[label][period] = {
                            "trades": complement.summary(episodes, *bounds),
                            "counts": counts,
                            "account": complement.account(
                                engine, config(rule, mode), *bounds, cost, delay
                            ),
                        }
                details[key] = result
                base.save(checkpoint, result)
                sys.stdout.write(json.dumps({"phase": "ACCOUNT", "key": key}) + "\n")
                sys.stdout.flush()
            train_eps = engine.trades(config(rule, "OFF"), *base.PERIODS["train"])[0]
            train_win = statistics.fmean(int(e.net > 0) for e in train_eps) if train_eps else None
            for period, bounds in base.PERIODS.items():
                episodes = engine.trades(config(rule, "OFF"), *bounds)[0]
                probs = [decisions[obs[e.symbol, e.signal]["key"]]["p_positive"] for e in episodes]
                selected = [e for e, p in zip(episodes, probs, strict=True) if p >= pure.THRESHOLD]
                delta = [
                    -float(e.net) if p < pure.THRESHOLD else 0
                    for e, p in zip(episodes, probs, strict=True)
                ]
                sd = statistics.stdev(delta) * 1e4 if len(delta) > 1 else None
                rng = random.Random(SEED)
                nulls = []
                for _ in range(1000):
                    total = 0.0
                    for symbol in base.SYMBOLS:
                        pool = [e for e in episodes if e.symbol == symbol]
                        n = sum(e.symbol == symbol for e in selected)
                        total += sum(float(e.net) for e in rng.sample(pool, n))
                    nulls.append(total)
                retained = sum(float(e.net) for e in selected)
                paired[f"{rule}_{order}_{period}"] = {
                    "baseline_n": len(episodes),
                    "retained_n": len(selected),
                    "mean_increment_per_baseline_bps": statistics.fmean(delta) * 10000
                    if delta
                    else None,
                    "avoided_loss_unit_bps": -sum(
                        min(float(e.net), 0)
                        for e, p in zip(episodes, probs, strict=True)
                        if p < pure.THRESHOLD
                    )
                    * 10000,
                    "missed_profit_unit_bps": sum(
                        max(float(e.net), 0)
                        for e, p in zip(episodes, probs, strict=True)
                        if p < pure.THRESHOLD
                    )
                    * 10000,
                    "random_matched_percentile": sum(x < retained for x in nulls) / 1000
                    if selected
                    else None,
                    "brier": statistics.fmean(
                        (p - int(e.net > 0)) ** 2 for e, p in zip(episodes, probs, strict=True)
                    )
                    if episodes
                    else None,
                    "constant_train_probability": train_win,
                    "constant_train_brier": statistics.fmean(
                        (train_win - int(e.net > 0)) ** 2 for e in episodes
                    )
                    if episodes and train_win is not None
                    else None,
                    "paired_sd_bps": sd,
                    "power80_required_for_assumed25bps": required_samples(sd, 25, 0, z_alpha=2.6)
                    if sd and sd > 0
                    else None,
                    "detectable_increment_n100_bps": detectable_effect_bps(sd, 100, 0, z_alpha=2.6)
                    if sd and sd > 0
                    else None,
                }
            # Frozen baseline episodes, fixed 2,500-USDT opportunity diagnostics only.
            baseline = engine.trades(config(rule, "FULL"), base.START, base.END)[0]
            for label, cost, delay in (("base", 1, 0), ("cost2", 2, 0), ("delay4h", 1, 1)):
                values = []
                for e in baseline:
                    minute_result = pure.simulate_minute(
                        minutes[e.symbol],
                        funds[e.symbol],
                        coins[base.SYMBOLS.index(e.symbol)].t[e.signal] + H4,
                        bounded=not market,
                        cost=cost,
                        delay4h=delay,
                    )
                    prediction = decisions.get(obs[e.symbol, e.signal]["key"], {})
                    values.append(
                        {
                            "symbol": e.symbol,
                            "i": e.signal,
                            "p_positive": prediction.get("p_positive"),
                            "laya_valid": prediction.get("p_positive") is not None,
                            **json.loads(json.dumps(asdict(minute_result), default=str)),
                            "net_usdt": float(minute_result.net),
                            "net_worst_mark_usdt": float(
                                minute_result.gross
                                - minute_result.fee
                                + minute_result.funding_worst
                            ),
                            "net_best_mark_usdt": float(
                                minute_result.gross - minute_result.fee + minute_result.funding_best
                            ),
                        }
                    )
                execution[f"{rule}_{order}_{label}"] = values
                base.save(OUT / f"execution_{rule}_{order}_{label}.json", values)
    valid = [decisions[x["key"]]["p_positive"] is not None for x in obs.values()]
    stability = []
    for x in sorted(obs.values(), key=lambda v: v["key"])[:32]:
        p = decisions[x["key"]]["p_positive"]
        q = decisions[pure.request_key(pure.state_text(x["fields"], reverse=True))]["p_positive"]
        stability.append(
            {
                "key": x["key"],
                "p": p,
                "p_reversed_fields": q,
                "gate_changed": (p >= pure.THRESHOLD) != (q >= pure.THRESHOLD)
                if p is not None and q is not None
                else None,
            }
        )
    repeats = [
        {"key": k[7:], "p_original": decisions[k[7:]]["p_positive"], "p_repeat": v["p_positive"]}
        for k, v in decisions.items()
        if k.startswith("repeat:")
    ]
    report = {
        "status": "RETROSPECTIVE_PILOT_UNVERIFIED",
        "fresh_oos_n": 0,
        "model_training_cutoff": "UNKNOWN",
        "manifest": lineage,
        "coverage": {
            "opportunities": len(obs),
            "valid": sum(valid),
            "accepted": sum(
                decisions[x["key"]]["p_positive"] >= pure.THRESHOLD
                for x in obs.values()
                if decisions[x["key"]]["p_positive"] is not None
            ),
        },
        "estimated_api_usd": cache["estimated_api_usd"],
        "stability": stability,
        "repeats": repeats,
        "paired": paired,
        "details": details,
        "execution": execution,
    }
    base.save(OUT / "result.json", report)
    sys.stdout.write(json.dumps({"phase": "DONE", "result": str(OUT / "result.json")}) + "\n")


def main() -> None:
    if sys.argv[1:] == ["audit"]:
        coins, quality = base.load()
        obs = opportunities(coins)
        _, _, audit = load_execution(coins, obs)
        base.save(
            OUT / "audit.json",
            {
                "quality": quality,
                "execution": audit,
                "laya_opportunities": len(obs),
                "max_states": len({x["key"] for x in obs.values()}) + 48,
                "performance_runs": 0,
            },
        )
    elif sys.argv[1:] == ["ask"]:
        ask()
    elif sys.argv[1:] == ["run"]:
        run()
    else:
        raise ValueError("use audit, ask or run")


if __name__ == "__main__":
    main()
