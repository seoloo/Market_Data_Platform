"""문서 88 — 참고 계산(판정 아님): QQQ 매일 소액 사기 vs RSI 문턱 아래일 때 모아서 사기.

사용자 질문 (2026-09-26): "매일 만원씩 매수 vs RSI 40 에 도달할 때까지 기다려 그때 매수".
문서 87(RD, 매달 1,000 USD · RSI 35) 의 판정은 바꾸지 않는다 — 이것은 질문에 답하는 기술 계산이다.

사용: ``python -m scripts.ref_qqq_daily_dip`` → ``reports/ref_qqq_daily_dip.json`` 과 표 출력.
자료: ``data/research/tradfi/QQQ_{adj,raw}.csv`` (한국투자증권, 문서 86 감사 통과).
"""

from __future__ import annotations

import csv
import io
import json
import statistics
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from auto_trading.backtest.indicator_signals import rsi

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "data" / "research" / "tradfi"
OUT = REPO / "reports" / "ref_qqq_daily_dip.json"
AMOUNT = 10_000.0
FEE = 0.0025
FROM, TO = "2008-06-01", "2026-08-31"
THRESHOLDS = (40.0, 35.0, 30.0)
WINDOW_YEARS = (1, 3, 5, 10)
DAY = 86_400_000


def _ms(text: str) -> int:
    return int(datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=UTC).timestamp() * 1000)


def _date(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m-%d")


def _read(name: str) -> dict[int, float]:
    text = (DATA / f"{name}.csv").read_text(encoding="utf-8")
    return {_ms(r["date"]): float(r["close"]) for r in csv.DictReader(io.StringIO(text))}


def simulate(
    days: list[int], adj: dict[int, float], level: dict[int, float | None], threshold: float | None
) -> dict[str, float]:
    """``threshold`` 가 None 이면 매일 사기. 아니면 전날 RSI ≤ 문턱일 때 쌓인 현금 전부."""
    shares = cash = fees = 0.0
    buys = 0
    waited = longest = 0
    cash_share: list[float] = []
    prev: int | None = None
    for d in days:
        cash += AMOUNT
        signal = threshold is None or (
            prev is not None and (lv := level.get(prev)) is not None and lv <= threshold
        )
        if signal:
            fee = cash * FEE
            shares += (cash - fee) / adj[d]
            fees += fee
            cash = 0.0
            buys += 1
            waited = 0
        else:
            waited += 1
            longest = max(longest, waited)
        value = shares * adj[d]
        cash_share.append(cash / (value + cash))
        prev = d
    final = shares * adj[days[-1]] + cash
    return {
        "final": final,
        "invested": AMOUNT * len(days),
        "buys": buys,
        "fees": fees,
        "longest_wait_trading_days": longest,
        "avg_cash_share": statistics.fmean(cash_share),
        "cash_left": cash,
    }


def _windows(days: list[int], years: int) -> list[list[int]]:
    """매달 첫 거래일에 시작하는 ``years`` 년 창 (자료 끝을 넘지 않는 것만)."""
    out: list[list[int]] = []
    seen: set[str] = set()
    for i, d in enumerate(days):
        month = _date(d)[:7]
        if month in seen:
            continue
        seen.add(month)
        start = datetime.fromtimestamp(d / 1000, UTC)
        try:
            end_dt = start.replace(year=start.year + years)
        except ValueError:
            end_dt = start.replace(year=start.year + years, day=28)
        end = int(end_dt.timestamp() * 1000)
        if end > days[-1]:
            break
        out.append([x for x in days[i:] if x < end])
    return out


def main() -> int:
    adj, raw = _read("QQQ_adj"), _read("QQQ_raw")
    all_days = sorted(set(adj) & set(raw))
    values = rsi([raw[d] for d in all_days], 14)
    level = dict(zip(all_days, values, strict=True))
    days = [d for d in all_days if _ms(FROM) <= d <= _ms(TO)]
    record: dict[str, Any] = {
        "from": _date(days[0]),
        "to": _date(days[-1]),
        "trading_days": len(days),
    }
    base = simulate(days, adj, level, None)
    record["daily"] = base
    per_year: dict[str, dict[str, int]] = {}
    for d in days:
        lv = level[d]
        y = _date(d)[:4]
        row = per_year.setdefault(y, {"days": 0, **{f"le{int(t)}": 0 for t in THRESHOLDS}})
        row["days"] += 1
        for t in THRESHOLDS:
            row[f"le{int(t)}"] += lv is not None and lv <= t
    record["rsi_days_per_year"] = per_year
    for t in THRESHOLDS:
        full = simulate(days, adj, level, t)
        rec: dict[str, Any] = {"full": full, "ratio_full": full["final"] / base["final"]}
        for years in WINDOW_YEARS:
            ratios = []
            for w in _windows(days, years):
                d_ = simulate(w, adj, level, None)["final"]
                r_ = simulate(w, adj, level, t)["final"]
                ratios.append((r_ / d_, _date(w[0])))
            wins = sum(1 for r, _ in ratios if r > 1)
            rec[f"windows_{years}y"] = {
                "n": len(ratios),
                "wait_wins": wins,
                "share": wins / len(ratios),
                "min": min(ratios),
                "median": statistics.median(r for r, _ in ratios),
                "max": max(ratios),
            }
        record[f"rsi_le_{int(t)}"] = rec
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(record, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(
        f"{record['from']} ~ {record['to']} · 거래일 {record['trading_days']} · 매일 {AMOUNT:,.0f}"
    )
    print(f"매일 사기: 넣은 돈 {base['invested']:,.0f} → {base['final']:,.0f}")
    for t in THRESHOLDS:
        rec = record[f"rsi_le_{int(t)}"]
        f = rec["full"]
        print(
            f"RSI ≤ {t:.0f}: {f['final']:,.0f} (매일 대비 {rec['ratio_full']:.4f})"
            f" · 매수 {f['buys']}번 · 최장 대기 {f['longest_wait_trading_days']}거래일"
            f" · 평균 현금 {f['avg_cash_share']:.1%}"
        )
        for years in WINDOW_YEARS:
            w = rec[f"windows_{years}y"]
            lo, hi = w["min"], w["max"]
            print(
                f"   {years:>2}년 창 {w['n']}개: 기다리기 이김 {w['wait_wins']} ({w['share']:.0%})"
                f" · 최소 {lo[0]:.3f}({lo[1]}) · 중앙 {w['median']:.3f} · 최대 {hi[0]:.3f}({hi[1]})"
            )
    return 0


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        _r = getattr(_s, "reconfigure", None)
        if _r is not None:
            _r(encoding="utf-8", errors="replace")
    sys.exit(main())
