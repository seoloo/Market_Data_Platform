"""문서 86 §3.1·§3.2 — X 판정 자료 적재와 감사 A1~A3. 수익(전략)은 계산하지 않는다.

사용 예::

    python -m scripts.audit_x_data fetch   # KIS 일봉(수정·원 가격) + FRED 지수 적재
    python -m scripts.audit_x_data audit   # A1~A3 → reports/x_tradfi/audit.json

자료는 ``data/research/tradfi/<이름>.csv`` (date,open,high,low,close,volume)
와 ``manifest.json``(sha256).
KIS 는 읽기 전용 어댑터(:mod:`auto_trading.exchange.kis`)로만 읽는다.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import itertools
import json
import math
import statistics
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "data" / "research" / "tradfi"
OUT = REPO / "reports" / "x_tradfi"
SYMBOLS = ("SPY", "QQQ", "GLD", "SLV")
SINCE, UNTIL = "2007-08-01", "2026-08-31"
AUDIT_FROM = "2007-08-21"
FRED = ("NASDAQ100", "SP500")


def _write_csv(path: Path, rows: Sequence[Sequence[Any]], header: Sequence[str]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(header)
    w.writerows(rows)
    text = buf.getvalue()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return hashlib.sha256(text.encode()).hexdigest()


def read_series(name: str) -> dict[str, float]:
    """{날짜: 종가}."""
    with (DATA / f"{name}.csv").open(encoding="utf-8") as fh:
        return {row["date"]: float(row["close"]) for row in csv.DictReader(fh)}


def fetch() -> int:
    from auto_trading.config.secrets import load_kis_credentials
    from auto_trading.exchange.kis import KisOverseasReadOnly, KisTokenCache

    broker = KisOverseasReadOnly(
        load_kis_credentials(REPO / ".env"), KisTokenCache(REPO / "data" / "kis_token.json")
    )
    manifest = _load_manifest()
    try:
        for symbol in SYMBOLS:
            for adjusted, tag in ((True, "adj"), (False, "raw")):
                if (
                    f"{symbol}_{tag}" in manifest["files"]
                    and (DATA / f"{symbol}_{tag}.csv").exists()
                ):
                    continue  # 이미 받았다 (다시 받으려면 manifest.json 을 지운다)
                bars = broker.daily_history(symbol, since=SINCE, until=UNTIL, adjusted=adjusted)
                rows = [
                    (
                        datetime.fromtimestamp(b.open_ms / 1000, UTC).strftime("%Y-%m-%d"),
                        b.open,
                        b.high,
                        b.low,
                        b.close,
                        b.volume,
                    )
                    for b in bars
                ]
                name = f"{symbol}_{tag}"
                digest = _write_csv(
                    DATA / f"{name}.csv", rows, ("date", "open", "high", "low", "close", "volume")
                )
                manifest["files"][name] = {
                    "source": f"KIS HHDFS76240000 MODP={'1' if adjusted else '0'}",
                    "rows": len(rows),
                    "first": rows[0][0] if rows else None,
                    "last": rows[-1][0] if rows else None,
                    "sha256": digest,
                }
                print(name, manifest["files"][name])
                _save_manifest(manifest)
    finally:
        broker.close()
    for sid in FRED:
        if sid in manifest["files"] and (DATA / f"{sid}.csv").exists():
            continue
        fred_rows: list[tuple[str, float, float, float, float, int]] = []
        for day, value in _fred_observations(sid):
            if value not in ("", ".") and SINCE <= day <= UNTIL:
                v = float(value)
                fred_rows.append((day, v, v, v, v, 0))
        digest = _write_csv(
            DATA / f"{sid}.csv", fred_rows, ("date", "open", "high", "low", "close", "volume")
        )
        manifest["files"][sid] = {
            "source": f"FRED {sid}",
            "rows": len(fred_rows),
            "first": fred_rows[0][0] if fred_rows else None,
            "last": fred_rows[-1][0] if fred_rows else None,
            "sha256": digest,
        }
        print(sid, manifest["files"][sid])
        _save_manifest(manifest)
    return 0


def _fred_observations(sid: str) -> list[tuple[str, str]]:
    """FRED 공개 CSV (키 불필요). 기간을 붙여 받는다 — 전체 파일은 느려 시간 초과가 났다."""
    import httpx

    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}&cosd={SINCE}&coed={UNTIL}"
    resp = httpx.get(url, timeout=120.0, headers={"User-Agent": "auto-trading-research"})
    resp.raise_for_status()
    return [(r["observation_date"], r[sid]) for r in csv.DictReader(io.StringIO(resp.text))]


def _load_manifest() -> dict[str, Any]:
    path = DATA / "manifest.json"
    manifest: dict[str, Any] = (
        json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"files": {}}
    )
    manifest["fetched"] = datetime.now(tz=UTC).isoformat()
    return manifest


def _save_manifest(manifest: Mapping[str, Any]) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    (DATA / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )


# ------------------------------------------------------------------ 감사 (순수 함수)


def missing_days(calendar: Sequence[str], series: Mapping[str, float]) -> dict[str, Any]:
    """A1: 달력에 있는데 자료에 없는 날, 가장 긴 연속 빠짐."""
    missing = [d for d in calendar if d not in series]
    longest = run = 0
    have = set(series)
    for d in calendar:
        run = run + 1 if d not in have else 0
        longest = max(longest, run)
    extra = sorted(set(series) - set(calendar))
    return {
        "calendar_days": len(calendar),
        "missing": len(missing),
        "missing_share": len(missing) / len(calendar) if calendar else math.nan,
        "longest_run": longest,
        "missing_sample": missing[:10],
        "extra_days": len(extra),
        "extra_sample": extra[:10],
    }


def return_match(a: Mapping[str, float], b: Mapping[str, float]) -> dict[str, Any]:
    """A2: 같은 날끼리 단순 일간 수익 상관과 연도별 수익 차이(a − b)."""
    days = sorted(set(a) & set(b))
    ra, rb = [], []
    for prev, day in itertools.pairwise(days):
        ra.append(a[day] / a[prev] - 1)
        rb.append(b[day] / b[prev] - 1)
    corr = statistics.correlation(ra, rb) if len(ra) > 2 else math.nan
    years: dict[str, list[str]] = {}
    for d in days:
        years.setdefault(d[:4], []).append(d)
    diffs = {}
    for y, ds in sorted(years.items()):
        if len(ds) < 200:  # 완결되지 않은 해는 뺀다
            continue
        diffs[y] = (a[ds[-1]] / a[ds[0]] - 1) - (b[ds[-1]] / b[ds[0]] - 1)
    return {
        "days": len(days),
        "corr": corr,
        "annual_diff": diffs,
        "annual_diff_median": statistics.median(diffs.values()) if diffs else math.nan,
    }


def ratio_steps(adj: Mapping[str, float], raw: Mapping[str, float]) -> dict[str, Any]:
    """A3: MODP=1 / MODP=0 비율 — 중앙값 대비 최대 이탈, 하루 0.05% 넘게 바뀐 날 수(해마다)."""
    days = sorted(set(adj) & set(raw))
    ratio = [adj[d] / raw[d] for d in days]
    med = statistics.median(ratio)
    steps: dict[str, int] = {}
    counts: dict[str, int] = {}
    for i in range(1, len(days)):
        y = days[i][:4]
        counts[y] = counts.get(y, 0) + 1
        if abs(ratio[i] / ratio[i - 1] - 1) > 0.0005:
            steps[y] = steps.get(y, 0) + 1
    full_years = [y for y, n in counts.items() if n >= 200]
    per_year = [steps.get(y, 0) for y in full_years]
    return {
        "days": len(days),
        "ratio_first": ratio[0] if ratio else math.nan,
        "ratio_last": ratio[-1] if ratio else math.nan,
        "max_dev_from_median": max(abs(r / med - 1) for r in ratio) if ratio else math.nan,
        "steps_per_year": {y: steps.get(y, 0) for y in sorted(full_years)},
        "steps_per_year_median": statistics.median(per_year) if per_year else math.nan,
    }


def audit() -> int:
    ndx = read_series("NASDAQ100")
    spx = read_series("SP500")
    calendar = sorted(d for d in ndx if AUDIT_FROM <= d <= UNTIL)
    result: dict[str, Any] = {"A1": {}, "A2": {}, "A3": {}}
    ok = {"A1": True, "A2": True, "A3": True}
    for s in SYMBOLS:
        adj = {d: v for d, v in read_series(f"{s}_adj").items() if AUDIT_FROM <= d <= UNTIL}
        raw = {d: v for d, v in read_series(f"{s}_raw").items() if AUDIT_FROM <= d <= UNTIL}
        a1 = missing_days(calendar, adj)
        a1["pass"] = a1["missing_share"] <= 0.005 and a1["longest_run"] <= 5
        result["A1"][s] = a1
        ok["A1"] &= a1["pass"]
        a3 = ratio_steps(adj, raw)
        if s in ("GLD", "SLV"):
            a3["pass"] = a3["max_dev_from_median"] <= 0.01
        else:
            a3["pass"] = a3["steps_per_year_median"] <= 8
        result["A3"][s] = a3
        ok["A3"] &= a3["pass"]
        if s == "QQQ":
            m = return_match(raw, ndx)
            m["pass"] = m["corr"] >= 0.99 and abs(m["annual_diff_median"]) <= 0.03
            m["adj_vs_index"] = return_match(adj, ndx)["annual_diff_median"]
            result["A2"]["QQQ_vs_NASDAQ100"] = m
            ok["A2"] &= m["pass"]
        if s == "SPY":
            m = return_match(raw, spx)
            m["pass"] = m["corr"] >= 0.99 and abs(m["annual_diff_median"]) <= 0.03
            m["adj_vs_index"] = return_match(adj, spx)["annual_diff_median"]
            result["A2"]["SPY_vs_SP500"] = m
            ok["A2"] &= m["pass"]
    result["pass"] = ok
    result["verdict"] = "PASS" if all(ok.values()) else "FAIL"
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "audit.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    for k in ("A1", "A2", "A3"):
        for name, r in result[k].items():
            brief = {
                key: r[key]
                for key in (
                    "missing",
                    "missing_share",
                    "longest_run",
                    "extra_days",
                    "corr",
                    "annual_diff_median",
                    "adj_vs_index",
                    "max_dev_from_median",
                    "steps_per_year_median",
                    "ratio_first",
                    "ratio_last",
                    "pass",
                )
                if key in r
            }
            print(k, name, brief)
    print("감사:", result["verdict"], ok)
    return 0 if result["verdict"] == "PASS" else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("fetch", "audit"))
    args = parser.parse_args()
    return fetch() if args.stage == "fetch" else audit()


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            _reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
