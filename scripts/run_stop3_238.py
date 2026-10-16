"""문서 238 — 💹 개별주의 '−3% 손절 · 한 종목 25%' 가 주도주 추세 보유(113)보다 나은가. 1회 판정.

사용 예::

    python -m scripts.run_stop3_238 run   # 깨끗한 트리 · 판정 파일 없음일 때만

고르는 법 · 진입은 113(LT) 그대로(월말 주도 섹터 3 · 주도주 10 · 추세일 때만 · SPY 200 일선 위 ·
다음 거래일 종가 진입 · 0.25%), **나가기와 크기만** 바꾼다.

* BASE: 113 그대로 — 목표 10% · 최대 10 종목 · 매달 목표까지 추매 ·
  추세 꺾임 / 고점 −35% 면 다음 날 종가 매도.
* S3 (판정): 💹 개별주 규칙 — 목표 25% · 최대 4 종목 · 한 번 사면 추매 없음 ·
  **진입가 −3% 장중 손절만** (그날 저가가 손절가 이하면 손절가에, 시가가 이미 아래면 시가에
  판다 = 갭 반영).
* 참고(판정 아님): S3T(S3 + 추세 꺾임 매도) · S3_10(−3% 인데 10% · 10 종목) ·
  S5 · S8(25% · −5% · −8%).
"""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from scripts.run_a1_train import git_state
from scripts.run_lt_leaders import (
    FEE,
    FROM,
    MAX_POS,
    SPLIT,
    SPY_SMA,
    STOP_FROM_PEAK,
    TARGET,
    TO,
    Panel,
    build_panel,
    leaders,
    simulate,
    stats,
    trend_off,
    trend_up,
)
from scripts.run_sb_breakout import SPY, STOCKS, _sma, load

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "reports" / "stop3_238"
FIRST_END = "2016-12-31"
WHIPSAW_DAYS = 20


@dataclass(frozen=True, slots=True)
class Arm:
    name: str
    target: float
    max_pos: int
    stop: float
    trend_exit: bool


ARMS = (
    Arm("S3", 0.25, 4, 0.03, False),
    Arm("S3T", 0.25, 4, 0.03, True),
    Arm("S3_10", TARGET, MAX_POS, 0.03, False),
    Arm("S5", 0.25, 4, 0.05, False),
    Arm("S8", 0.25, 4, 0.08, False),
)


@dataclass(slots=True)
class Ohlc:
    open: dict[str, list[float | None]]
    low: dict[str, list[float | None]]


def build_ohlc(panel: Panel) -> Ohlc:
    op: dict[str, list[float | None]] = {}
    lo: dict[str, list[float | None]] = {}
    for sym in panel.close:
        s = load(STOCKS / f"{sym}.csv")
        mo = dict(zip(s.dates, s.open, strict=True))
        ml = dict(zip(s.dates, s.low, strict=True))
        op[sym] = [mo.get(d) for d in panel.days]
        lo[sym] = [ml.get(d) for d in panel.days]
    return Ohlc(op, lo)


@dataclass(slots=True)
class StopRun:
    equity: list[float] = field(default_factory=list)
    trades: list[dict[str, Any]] = field(default_factory=list)


def simulate_stop(panel: Panel, ohlc: Ohlc, spy_ok: Sequence[bool], arm: Arm) -> StopRun:
    """S 계열: 113 과 같은 진입, 고정 손절(진입가 × (1 − stop)) 장중 체결, 추매 없음."""
    days = panel.days
    start = next(i for i, d in enumerate(days) if d >= FROM)
    cash = 1.0
    pos: dict[str, list[float]] = {}  # sym -> [수량, 원가, 진입가, 진입 색인]
    run = StopRun()
    pending_buy: list[str] | None = None
    pending_sell: set[str] = set()

    def px(sym: str, t: int) -> float:
        xs = panel.close[sym]
        while xs[t] is None:
            t -= 1
        return xs[t]  # type: ignore[return-value]

    def close_pos(sym: str, t: int, price: float, reason: str) -> None:
        nonlocal cash
        q, cost, entry, i0 = pos.pop(sym)
        proceeds = q * price * (1 - FEE)
        cash += proceeds
        run.trades.append(
            {
                "symbol": sym,
                "entry_i": int(i0),
                "exit_i": t,
                "entry_px": entry,
                "exit_px": price,
                "ret": proceeds / cost - 1,
                "reason": reason,
            }
        )

    for t in range(start, len(days)):
        for sym in sorted(pending_sell):
            if sym in pos:
                close_pos(sym, t, px(sym, t), "trend_off")
        pending_sell = set()
        equity = cash + sum(q * px(s, t) for s, (q, *_r) in pos.items())
        if pending_buy is not None:
            for sym in pending_buy:
                if sym in pos or len(pos) >= arm.max_pos or cash <= 0:
                    continue
                amt = min(arm.target * equity, cash)
                p = px(sym, t)
                pos[sym] = [amt * (1 - FEE) / p, amt, p, t]
                cash -= amt
            pending_buy = None
        # 장중 손절 (진입 다음 날부터)
        for sym in sorted(pos):
            _q, _c, entry, i0 = pos[sym]
            if t <= i0:
                continue
            lo, op = ohlc.low[sym][t], ohlc.open[sym][t]
            level = entry * (1 - arm.stop)
            if lo is not None and lo <= level:
                fill = op if op is not None and op < level else level
                close_pos(sym, t, fill, "stop")
        if arm.trend_exit:
            for sym in pos:
                if trend_off(panel.close[sym], t):
                    pending_sell.add(sym)
        if t + 1 < len(days) and days[t + 1][:7] != days[t][:7]:
            lead = leaders(panel, t)
            if spy_ok[t]:
                pending_buy = [
                    s
                    for s, _ in lead
                    if s not in pos and s not in pending_sell and trend_up(panel.close[s], t)
                ]
        run.equity.append(cash + sum(q * px(s, t) for s, (q, *_r) in pos.items()))
    return run


def stop_stats(panel: Panel, run: StopRun, stop: float) -> dict[str, Any]:
    """손절 횟수 · 평균 손익 · 갭 손절 비율 · 손절 뒤 20 거래일 안에 진입가 회복한 비율."""
    stops = [tr for tr in run.trades if tr["reason"] == "stop"]
    if not stops:
        return {"stops": 0}
    gaps = [tr for tr in stops if tr["exit_px"] < tr["entry_px"] * (1 - stop) - 1e-9]
    back = 0
    for tr in stops:
        xs = panel.close[tr["symbol"]][tr["exit_i"] + 1 : tr["exit_i"] + 1 + WHIPSAW_DAYS]
        if any(x is not None and x >= tr["entry_px"] for x in xs):
            back += 1
    years = (len(run.equity) - 1) / 252
    return {
        "stops": len(stops),
        "stops_per_year": round(len(stops) / years, 1),
        "stop_share_of_exits": round(len(stops) / max(1, len(run.trades)), 3),
        "avg_stop_ret": round(sum(tr["ret"] for tr in stops) / len(stops), 4),
        "gap_share": round(len(gaps) / len(stops), 3),
        "worst_stop_ret": round(min(tr["ret"] for tr in stops), 4),
        "recovered_within_20d": round(back / len(stops), 3),
    }


def _row(span: Sequence[str], eq: Sequence[float]) -> dict[str, Any]:
    end = span[-1]
    return {
        "full": stats(span, eq, FROM, end),
        "first": stats(span, eq, FROM, FIRST_END),
        "second": stats(span, eq, SPLIT, end),
    }


def judge(base: dict[str, Any], s3: dict[str, Any]) -> str:
    """사전 등록 §4 (순수)."""
    b, s = base["full"], s3["full"]
    if s["sharpe"] < b["sharpe"] - 0.10 or s["cagr"] < b["cagr"] - 0.03:
        return "S3_HARMFUL"
    if (
        s["sharpe"] >= b["sharpe"] + 0.10
        and s["mdd"] <= b["mdd"]
        and all(s3[h]["cagr"] >= base[h]["cagr"] for h in ("first", "second"))
    ):
        return "S3_BETTER"
    return "S3_SIMILAR"


def run() -> dict[str, Any]:
    target = OUT / "verdict.json"
    if target.exists():
        raise SystemExit(f"이미 판정했다: {target}")
    sha, dirty = git_state()
    if dirty:
        raise SystemExit("작업 트리가 깨끗하지 않다 — 커밋 뒤에 실행")
    panel = build_panel()  # 111 유니버스 해시 확인 포함
    spy = load(SPY)
    spy_map = dict(zip(spy.dates, spy.close, strict=True))
    spy_close = [spy_map[d] for d in panel.days]
    sma = _sma(spy_close, SPY_SMA)
    spy_ok = [m is not None and c > m for c, m in zip(spy_close, sma, strict=True)]
    start = next(i for i, d in enumerate(panel.days) if d >= FROM)
    span = panel.days[start:]
    ohlc = build_ohlc(panel)
    base = simulate(panel, spy_ok)
    rows: dict[str, Any] = {"BASE": {**_row(span, base.equity), "trades": len(base.trades)}}
    for arm in ARMS:
        r = simulate_stop(panel, ohlc, spy_ok, arm)
        rows[arm.name] = {
            **_row(span, r.equity),
            "trades": len(r.trades),
            "stop": stop_stats(panel, r, arm.stop),
        }
    out = {
        "git": sha,
        "period": [FROM, TO],
        "verdict": judge(rows["BASE"], rows["S3"]),
        "rows": rows,
        "note": (
            "문서 238 1 회 판정. 113 과 같은 S&P 500 지금 명단(생존 편향) · 같은 진입, "
            f"나가기 · 크기만 다름. BASE 의 손절은 고점 −{STOP_FROM_PEAK:.0%}."
        ),
    }
    OUT.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def main(argv: list[str]) -> int:
    if argv[1:] != ["run"]:
        print(__doc__)
        return 2
    out = run()
    print(json.dumps({"verdict": out["verdict"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
