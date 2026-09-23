"""워뇨띠 BitMEX 내보내기의 1차 기술 통계 — 2차 자료(유튜브 요약) 주장 재현용.

탐색 단계다. 여기서 나온 관찰은 **가설 후보**일 뿐이며, 전략으로 쓰려면 별도 사전등록과
노출되지 않은 구간(2022 이후·전향)의 검증을 거친다 (docs/research/06-wonyotti-exploration.md).

원자료(`data/external/wonyotti/`, gitignore)는 읽기만 하고, 요약을 표준 출력과
`reports/wonyotti/summary_v1.json`(gitignore)에 남긴다. 표준 라이브러리만 쓴다.
금액 단위: BitMEX ``XBt`` = 1e-8 BTC.
"""

from __future__ import annotations

import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

SOURCE = Path("data/external/wonyotti")
OUTPUT = Path("reports/wonyotti/summary_v1.json")
_SATOSHI = 100_000_000


def _year(row: dict[str, str]) -> str:
    return row["date"][:4]


def executions(directory: Path) -> dict[str, object]:
    """체결 CSV 전체를 한 번 훑어 종류·상품·주문 방식·수수료를 연도별로 센다."""
    exec_types: Counter[tuple[str, str]] = Counter()
    trade_symbols: Counter[tuple[str, str]] = Counter()
    ord_types: Counter[tuple[str, str]] = Counter()
    liquidity: Counter[tuple[str, str]] = Counter()
    liquidations: Counter[tuple[str, str]] = Counter()
    commission_xbt: defaultdict[str, int] = defaultdict(int)
    orders_by_type: defaultdict[tuple[str, str], set[str]] = defaultdict(set)
    rows = 0
    for path in sorted(directory.glob("aoa-execution-*.csv")):
        with path.open(encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                rows += 1
                year = _year(row)
                exec_types[(year, row["exectype"])] += 1
                if row["exectype"] != "Trade":
                    continue
                trade_symbols[(year, row["symbol"])] += 1
                ord_types[(year, row["ordtype"])] += 1
                orders_by_type[(year, row["ordtype"])].add(row["orderid"])
                liquidity[(year, row["lastliquidityind"])] += 1
                if "liquidat" in (row["text"] + row["ordtype"] + row["execinst"]).lower():
                    liquidations[(year, row["symbol"])] += 1
                if row["settlcurrency"] == "XBt" and row["execcomm"]:
                    commission_xbt[year] += int(float(row["execcomm"]))
    return {
        "rows": rows,
        "exec_types": _nest(exec_types),
        "trade_symbols_top": {
            year: dict(
                Counter({s: n for (y, s), n in trade_symbols.items() if y == year}).most_common(8)
            )
            for year in sorted({y for y, _ in trade_symbols})
        },
        "ord_types_fills": _nest(ord_types),
        "ord_types_orders": {
            f"{year}/{kind}": len(ids) for (year, kind), ids in sorted(orders_by_type.items())
        },
        "liquidity_fills": _nest(liquidity),
        "liquidation_fills": _nest(liquidations),
        "commission_btc_xbt_settled": {y: v / _SATOSHI for y, v in sorted(commission_xbt.items())},
    }


def wallet(path: Path) -> dict[str, object]:
    """지갑 기록: 거래 유형별 합계와 일별 실현손익 집중도."""
    by_type: defaultdict[tuple[str, str], int] = defaultdict(int)
    daily: defaultdict[str, int] = defaultdict(int)
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if row["currency"] != "XBt" or row["transactstatus"] != "Completed":
                continue
            amount = int(row["amount"])
            by_type[(_year(row), row["transacttype"])] += amount
            if row["transacttype"] == "RealisedPNL":
                daily[row["date"]] += amount
    pnl_days = sorted(daily.values(), reverse=True)
    total = sum(pnl_days)
    monthly: defaultdict[str, int] = defaultdict(int)
    for day, amount in daily.items():
        monthly[day[:7]] += amount
    return {
        "btc_by_year_type": {
            f"{year}/{kind}": amount / _SATOSHI for (year, kind), amount in sorted(by_type.items())
        },
        "realised_pnl_btc_total": total / _SATOSHI,
        "pnl_days": len(pnl_days),
        "top20_day_share": sum(pnl_days[:20]) / total if total else None,
        "rest_after_top50_btc": sum(pnl_days[50:]) / _SATOSHI,
        "months": len(monthly),
        "positive_months": sum(1 for v in monthly.values() if v > 0),
    }


def _nest(counter: Counter[tuple[str, str]]) -> dict[str, dict[str, int]]:
    out: defaultdict[str, dict[str, int]] = defaultdict(dict)
    for (year, key), count in sorted(counter.items()):
        out[year][key or "(blank)"] = count
    return dict(out)


def main() -> int:
    summary = {
        "executions": executions(SOURCE),
        "wallet": wallet(SOURCE / "aoa-wallet-2018-03-01-2021-12-31.csv"),
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(summary, ensure_ascii=False, indent=1, sort_keys=True)
    OUTPUT.write_text(text + "\n", encoding="utf-8")
    sys.stdout.write(text + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
