"""문서 87 — RD: SPY·QQQ 를 RSI(14) ≤ 35 일 때만 사서 계속 보유 vs 매달 바로 사기. 1회 판정.

사용 예::

    python -m scripts.run_rd_dip fetch   # 참고 줄용 미국 3개월 국채 금리(FRED DTB3) 적재
    python -m scripts.run_rd_dip run     # 판정 (작업 트리 깨끗 · 판정 파일 없음일 때만)

자료: ``data/research/tradfi/{SPY,QQQ}_{adj,raw}.csv`` (한국투자증권, 문서 86 감사 통과).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import statistics
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scripts.run_a1_train import git_state

from auto_trading.backtest.indicator_signals import rsi

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "data" / "research" / "tradfi"
OUTPUT = REPO / "reports" / "rd_dip"
SYMBOLS = ("SPY", "QQQ")
CONTRIBUTION = 1_000.0
FEE = 0.0025
RSI_PERIOD, RSI_LEVEL = 14, 35.0
FROM, TO = "2008-06-01", "2026-08-31"
WINDOW_YEARS = 10
WINDOW_LAST_START = "2016-08-31"
R2_SHARE = 0.60
DAY = 86_400_000


def _ms(text: str) -> int:
    return int(datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=UTC).timestamp() * 1000)


def _date(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m-%d")


# ------------------------------------------------------------------ 순수 계산


@dataclass(slots=True)
class Account:
    final: float = 0.0
    invested: float = 0.0
    shares: float = 0.0
    cash: float = 0.0
    buys: int = 0
    fees: float = 0.0
    cash_share_sum: float = 0.0
    days: int = 0
    longest_wait_days: int = 0
    flows: list[tuple[int, float]] = field(default_factory=list)
    """(날짜 ms, 금액) — 입금은 음수, 마지막 평가액은 양수 (IRR 용)."""


def simulate(
    days: Sequence[int],
    adj: Mapping[int, float],
    rsi_by_day: Mapping[int, float | None],
    *,
    mode: str,
    start: int,
    end: int,
    cash_rate: Mapping[int, float] | None = None,
) -> Account:
    """``mode`` = "D"(입금한 날 바로 전액) 또는 "R"(전날 RSI ≤ 35 면 그날 종가에 현금 전액).

    ``cash_rate`` 가 있으면 현금에 {날짜: 연 금리} 로 역일 이자를 준다(참고 줄).
    """
    acc = Account()
    period = [d for d in days if start <= d <= end]
    prev_day: int | None = None
    prev_month = ""
    wait_start: int | None = None
    rate = 0.0
    for d in period:
        if cash_rate is not None and prev_day is not None and acc.cash > 0:
            rate = cash_rate.get(d, rate)
            acc.cash *= 1 + rate * ((d - prev_day) // DAY) / 365
        month = _date(d)[:7]
        if month != prev_month:  # 달의 첫 거래일에 입금
            acc.cash += CONTRIBUTION
            acc.invested += CONTRIBUTION
            acc.flows.append((d, -CONTRIBUTION))
            prev_month = month
            if wait_start is None:
                wait_start = d
        level = rsi_by_day.get(prev_day) if prev_day is not None else None
        signal = level is not None and level <= RSI_LEVEL
        if acc.cash > 0 and (mode == "D" or signal):
            fee = acc.cash * FEE
            acc.shares += (acc.cash - fee) / adj[d]
            acc.fees += fee
            acc.buys += 1
            acc.cash = 0.0
            if wait_start is not None:
                acc.longest_wait_days = max(acc.longest_wait_days, (d - wait_start) // DAY)
            wait_start = None
        value = acc.shares * adj[d]
        acc.cash_share_sum += acc.cash / (value + acc.cash) if value + acc.cash > 0 else 0.0
        acc.days += 1
        prev_day = d
    if wait_start is not None and period:
        acc.longest_wait_days = max(acc.longest_wait_days, (period[-1] - wait_start) // DAY)
    acc.final = acc.shares * adj[period[-1]] + acc.cash if period else 0.0
    if period:
        acc.flows.append((period[-1], acc.final))
    return acc


def irr(flows: Sequence[tuple[int, float]]) -> float:
    """돈 가중 연수익 (이분법)."""
    t0 = flows[0][0]

    def npv(r: float) -> float:
        return float(sum(v / (1 + r) ** ((d - t0) / DAY / 365.25) for d, v in flows))

    lo, hi = -0.99, 1.0
    for _ in range(200):
        mid = (lo + hi) / 2
        if npv(mid) > 0:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def summary(acc: Account) -> dict[str, Any]:
    return {
        "final": acc.final,
        "invested": acc.invested,
        "multiple": acc.final / acc.invested if acc.invested else None,
        "irr": irr(acc.flows) if len(acc.flows) > 1 else None,
        "buys": acc.buys,
        "fees": acc.fees,
        "avg_cash_share": acc.cash_share_sum / acc.days if acc.days else None,
        "longest_wait_days": acc.longest_wait_days,
        "cash_left": acc.cash,
    }


def window_starts(days: Sequence[int], first: str, last: str) -> list[int]:
    """``first``~``last`` 사이 매달 첫 거래일."""
    out, seen = [], set()
    for d in days:
        month = _date(d)[:7]
        if _ms(first) <= d <= _ms(last) and month not in seen:
            seen.add(month)
            out.append(d)
    return out


def _add_years(ms: int, years: int) -> int:
    d = datetime.fromtimestamp(ms / 1000, UTC)
    try:
        e = d.replace(year=d.year + years)
    except ValueError:  # 2월 29일
        e = d.replace(year=d.year + years, day=28)
    return int(e.timestamp() * 1000) - DAY


# ------------------------------------------------------------------ 자료


def _read(name: str) -> tuple[dict[int, float], str]:
    text = (DATA / f"{name}.csv").read_text(encoding="utf-8")
    rows = {_ms(r["date"]): float(r["close"]) for r in csv.DictReader(io.StringIO(text))}
    return rows, hashlib.sha256(text.encode()).hexdigest()


def fetch() -> int:
    import httpx

    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id=DTB3&cosd=2007-08-01&coed={TO}"
    resp = httpx.get(url, timeout=120.0, headers={"User-Agent": "auto-trading-research"})
    resp.raise_for_status()
    rows = [
        (r["observation_date"], r["DTB3"])
        for r in csv.DictReader(io.StringIO(resp.text))
        if r["DTB3"] not in ("", ".")
    ]
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(("date", "close"))
    w.writerows(rows)
    DATA.mkdir(parents=True, exist_ok=True)
    (DATA / "DTB3.csv").write_text(buf.getvalue(), encoding="utf-8", newline="\n")
    print(f"DTB3 {len(rows)}줄 · {rows[0][0]} ~ {rows[-1][0]}")
    return 0


# ------------------------------------------------------------------ 판정


def run(output: Path) -> int:
    path = output / "run" / "verdict.json"
    if path.exists():
        print(f"이미 판정이 있다: {path}. 사전등록상 1회만 실행한다.")
        return 3
    sha, dirty = git_state()
    if dirty:
        print("작업 트리에 커밋되지 않은 변경이 있다. 재현 가능한 코드로만 실행한다.")
        return 3
    manifest = json.loads((DATA / "manifest.json").read_text(encoding="utf-8"))
    tbill: dict[int, float] | None = None
    if (DATA / "DTB3.csv").exists():
        rates, _ = _read("DTB3")
        tbill = {d: v / 100 for d, v in rates.items()}
    record: dict[str, Any] = {
        "git_sha": sha,
        "executed_at": datetime.now(tz=UTC).isoformat(),
        "rule": {
            "rsi": RSI_PERIOD,
            "level": RSI_LEVEL,
            "fee": FEE,
            "contribution": CONTRIBUTION,
            "from": FROM,
            "to": TO,
        },
    }
    status = "PASS"
    for s in SYMBOLS:
        adj, adj_sha = _read(f"{s}_adj")
        raw, raw_sha = _read(f"{s}_raw")
        for name, digest in ((f"{s}_adj", adj_sha), (f"{s}_raw", raw_sha)):
            if digest != manifest["files"][name]["sha256"]:
                raise SystemExit(f"{name}.csv 가 적재 기록(sha256)과 다르다")
        days = sorted(set(adj) & set(raw))
        values = rsi([raw[d] for d in days], RSI_PERIOD)
        rsi_by_day = dict(zip(days, values, strict=True))
        start, end = _ms(FROM), _ms(TO)
        d_acc = simulate(days, adj, rsi_by_day, mode="D", start=start, end=end)
        r_acc = simulate(days, adj, rsi_by_day, mode="R", start=start, end=end)
        r1 = r_acc.final > d_acc.final
        ratios = []
        for w0 in window_starts(days, FROM, WINDOW_LAST_START):
            w1 = _add_years(w0, WINDOW_YEARS)
            a = simulate(days, adj, rsi_by_day, mode="D", start=w0, end=w1)
            b = simulate(days, adj, rsi_by_day, mode="R", start=w0, end=w1)
            ratios.append(b.final / a.final)
        share = sum(r > 1 for r in ratios) / len(ratios)
        r2 = share >= R2_SHARE
        dips: dict[str, int] = {}
        for d in days:
            v = rsi_by_day[d]
            if start <= d <= end and v is not None and v <= RSI_LEVEL:
                dips[_date(d)[:4]] = dips.get(_date(d)[:4], 0) + 1
        refs: dict[str, Any] = {}
        if tbill is not None:
            for mode in ("D", "R"):
                acc = simulate(
                    days, adj, rsi_by_day, mode=mode, start=start, end=end, cash_rate=tbill
                )
                refs[f"{mode}_with_tbill"] = summary(acc)
        first_day = next(d for d in days if d >= start)
        lump_shares = d_acc.invested * (1 - FEE) / adj[first_day]
        refs["lump_sum_first_day"] = {
            "final": lump_shares * adj[max(d for d in days if d <= end)],
            "invested": d_acc.invested,
        }
        record[s] = {
            "D_monthly": summary(d_acc),
            "R_rsi_dip": summary(r_acc),
            "R_over_D": r_acc.final / d_acc.final,
            "windows": {
                "count": len(ratios),
                "share_R_better": share,
                "ratio_min": min(ratios),
                "ratio_median": statistics.median(ratios),
                "ratio_max": max(ratios),
            },
            "rsi_le_35_days_per_year": dips,
            "criteria": {"R1": r1, "R2": r2},
            "reference": refs,
        }
        if not (r1 and r2):
            status = "REJECT"
        print(
            f"{s}: R/D {r_acc.final / d_acc.final:.4f} (R1 {'통과' if r1 else '실패'})"
            f" · 10년 창 R 우세 {share:.0%} (R2 {'통과' if r2 else '실패'})"
            f" · IRR D {summary(d_acc)['irr']:.2%} / R {summary(r_acc)['irr']:.2%}"
        )
    record["status"] = status
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(record, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"RD: {status} · 기록 {path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("fetch", "run"))
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    return fetch() if args.stage == "fetch" else run(args.output)


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            _reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
