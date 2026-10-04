"""문서 137 §10 — 참고 계산(판정 아님): 그리드를 Binance 수수료로, 그리고 영상 기간 재현.

사용 예::

    python -m scripts.ref_grid_binance

1. 2018-01 ~ 2026-08 BTC·ETH 합친 계좌: Binance 현물 0.1% (BNB 할인 0.075% 참고).
   GR 판정(137 §9 REJECT)은 바뀌지 않는다 — 수수료만 바꾼 참고 줄이다.
2. 영상 기간 재현: 유튜브 영상(9월 6일 촬영, "15일 전 시작", 원금 3,800 만 원, 빗썸 ETH 그리드)에
   맞춰 2026-08-22 ~ 09-05 ETH 를 영상식 그리드(필터·손절 없음)로 돌리고,
   날마다 **화면에 찍히는 '오늘 수익'(끝난 왕복 차익)** 과 **실제 계좌 변화**를 나란히 적는다.
   영상의 칸 설정은 알 수 없어 GR 과 같은 칸(간격 0.5σ·아래 10 칸)을 쓴다.
결과: ``reports/grid_upbit/ref_binance.json``.
"""

from __future__ import annotations

import json
import sys
import zipfile
from array import array
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scripts.run_grid_upbit import BASE, FROM, SLEEVE, TO, load_minutes, view

from auto_trading.backtest.grid import (
    DAY_MS,
    GridResult,
    MinuteBars,
    daily_closes,
    hold_trend,
    simulate_grid,
    trend_and_sigma,
)

REPO = Path(__file__).resolve().parent.parent
DAILY = REPO / "data" / "external" / "binance_vision" / "spot_daily"
OUT = REPO / "reports" / "grid_upbit" / "ref_binance.json"
VIDEO_CAPITAL = 38_000_000.0
_MICROS = 10**14


def _ms(text: str) -> int:
    return int(datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=UTC).timestamp() * 1000)


def _date(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m-%d")


def with_september(symbol: str) -> MinuteBars:
    """월별 zip(~2026-08) 뒤에 일별 zip(2026-09-01~06)을 잇는다."""
    base = load_minutes(symbol)
    t, o, h, lo, c = (array(x.typecode, x) for x in (base.t, base.o, base.h, base.low, base.c))  # type: ignore[attr-defined]
    for path in sorted((DAILY / symbol).glob(f"{symbol}-1m-2026-09-*.zip")):
        with zipfile.ZipFile(path) as archive:
            (name,) = archive.namelist()
            for line in archive.read(name).decode("ascii").splitlines():
                cells = line.split(",")
                if not cells[0].isdigit():
                    continue
                ms = int(cells[0])
                if ms > _MICROS:
                    ms //= 1000
                if ms <= t[-1]:
                    continue
                t.append(ms)
                o.append(float(cells[1]))
                h.append(float(cells[2]))
                lo.append(float(cells[3]))
                c.append(float(cells[4]))
    return MinuteBars(t, o, h, lo, c)


def part1() -> dict[str, Any]:
    out: dict[str, Any] = {}
    fees = {"binance_0.1": 0.001, "binance_bnb_0.075": 0.00075}
    runs: dict[str, list[GridResult]] = {}
    for symbol in ("BTCUSDT", "ETHUSDT"):
        bars = load_minutes(symbol)
        trend, sigma = trend_and_sigma(daily_closes(bars), BASE)
        kw: dict[str, Any] = {"from_ms": FROM, "to_ms": TO, "initial": SLEEVE}
        for label, fee in fees.items():
            cfg = replace(BASE, grid_fee=fee, stop_fee=fee, exit_fee=fee)
            ga = replace(cfg, use_trend=False, use_stop=False)
            runs.setdefault(f"GR_{label}", []).append(
                simulate_grid(bars, trend, sigma, cfg=cfg, **kw)
            )
            runs.setdefault(f"GA_{label}", []).append(
                simulate_grid(bars, trend, sigma, cfg=ga, **kw)
            )
            runs.setdefault(f"TH_{label}", []).append(hold_trend(bars, trend, fee=fee, **kw))
        runs.setdefault("BH", []).append(hold_trend(bars, None, fee=0.001, **kw))
        print(f"{symbol} 끝")
    for name, rs in runs.items():
        v = view(rs)
        out[name] = {
            "sharpe": v["full"]["sharpe"],
            "cagr": v["full"]["cagr"],
            "mdd": v["full"]["mdd"],
            "avg_exposure": v["avg_exposure"],
        }
        if name.startswith(("GR", "GA")):
            out[name]["realized_grid"] = sum(r.realized_grid for r in rs)
            out[name]["actual_pnl"] = sum(r.equity[-1] for r in rs) - SLEEVE * len(rs)
            out[name]["stops"] = sum(r.stops for r in rs)
    return out


def part2() -> dict[str, Any]:
    bars = with_september("ETHUSDT")
    trend, sigma = trend_and_sigma(daily_closes(bars), BASE)
    lo_ms, hi_ms = _ms("2026-08-22"), _ms("2026-09-05")
    result: dict[str, Any] = {}
    for label, fee in (("bithumb_0.04", 0.0004), ("binance_0.1", 0.001)):
        cfg = replace(
            BASE, grid_fee=fee, stop_fee=fee, exit_fee=fee, use_trend=False, use_stop=False
        )
        r = simulate_grid(
            bars, trend, sigma, from_ms=lo_ms, to_ms=hi_ms, initial=VIDEO_CAPITAL, cfg=cfg
        )
        bh = hold_trend(bars, None, from_ms=lo_ms, to_ms=hi_ms, initial=VIDEO_CAPITAL, fee=fee)
        rows = []
        prev, prev_bh = VIDEO_CAPITAL, VIDEO_CAPITAL
        for d, e, b in zip(r.days, r.equity, bh.equity, strict=True):
            rows.append(
                {
                    "date": _date(d),
                    "shown_today": r.realized_by_day.get(d, 0.0),
                    "account_change": e - prev,
                    "hold_change": b - prev_bh,
                    "equity": e,
                }
            )
            prev, prev_bh = e, b
        result[label] = {
            "days": rows,
            "shown_total": r.realized_grid,
            "account_total": r.equity[-1] - VIDEO_CAPITAL,
            "hold_total": bh.equity[-1] - VIDEO_CAPITAL,
            "round_trips": r.round_trips,
            "step": max(BASE.min_step, BASE.vol_mult * sigma[lo_ms]),
            "eth_start": next(c for t, c in zip(bars.t, bars.o, strict=True) if t >= lo_ms),
            "eth_end": next(
                c for t, c in zip(bars.t, bars.c, strict=True) if t >= hi_ms + DAY_MS - 60_000
            ),
        }
    return result


def part3() -> dict[str, Any]:
    """영상식 그리드(ETH, 빗썸 0.04%)를 2018~2026-08 전체로 돌려 15 일씩 끊어 본다:
    화면에 찍히는 수익(끝난 왕복 차익)은 몇 번 플러스였고, 실제 계좌는 몇 번 플러스였나."""
    bars = load_minutes("ETHUSDT")
    trend, sigma = trend_and_sigma(daily_closes(bars), BASE)
    fee = 0.0004
    cfg = replace(BASE, grid_fee=fee, stop_fee=fee, exit_fee=fee, use_trend=False, use_stop=False)
    r = simulate_grid(bars, trend, sigma, from_ms=FROM, to_ms=TO, initial=VIDEO_CAPITAL, cfg=cfg)
    shown, account = [], []
    for i in range(0, len(r.days) - 15, 15):
        span = r.days[i : i + 15]
        shown.append(sum(r.realized_by_day.get(d, 0.0) for d in span))
        start = r.equity[i - 1] if i else VIDEO_CAPITAL
        account.append(r.equity[i + 14] / start - 1)
    return {
        "windows": len(account),
        "shown_positive_share": sum(x > 0 for x in shown) / len(shown),
        "shown_negative": sum(x < 0 for x in shown),
        "account_positive_share": sum(x > 0 for x in account) / len(account),
        "account_median": sorted(account)[len(account) // 2],
        "account_worst": min(account),
        "account_best": max(account),
        "account_below_minus10_share": sum(x < -0.10 for x in account) / len(account),
    }


def main() -> int:
    record = {
        "executed_at": datetime.now(tz=UTC).isoformat(),
        "part1": part1(),
        "part2": part2(),
        "part3": part3(),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(record, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    for k, v in record["part1"].items():
        print(k, {kk: round(vv, 3) if isinstance(vv, float) else vv for kk, vv in v.items()})
    for k, v in record["part2"].items():
        print(
            k,
            "shown",
            round(v["shown_total"]),
            "account",
            round(v["account_total"]),
            "hold",
            round(v["hold_total"]),
            "trips",
            v["round_trips"],
            "step",
            round(v["step"], 4),
        )
        for row in v["days"]:
            print(
                "  ",
                row["date"],
                round(row["shown_today"]),
                round(row["account_change"]),
                round(row["hold_change"]),
            )
    print(
        "part3", {k: round(v, 3) if isinstance(v, float) else v for k, v in record["part3"].items()}
    )
    return 0


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            _reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
