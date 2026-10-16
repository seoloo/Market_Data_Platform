"""문서 242 — 💹 개별주의 '진입가 −3% 손절만' 을 대신할 나가기 규칙. 1회 판정.

사용 예::

    python -m scripts.run_exit_242 run   # 깨끗한 트리 · 자료 해시 일치 · 판정 파일 없음일 때만

고르는 법 · 진입은 113(미국) · 141(국장) 그대로 — 월말 주도 섹터 3 · 주도주 10 · 추세일 때만 ·
지수 200 일선 위 · 다음 거래일 종가 진입 · 0.25%. **크기와 나가기만** 💹 규칙으로 바꾼다.

* 크기(💹): 한 번에 잃어도 되는 돈 1% ÷ 첫 손절 폭, 한 종목 25% 상한, 추매 없음, 최대 10 종목.
* S3 (대조, 지금 💹): 진입가 −3% 장중 손절만.
* A_MA20: S3 + 종가가 20 일선 아래면 다음 거래일 종가 매도.
* B_ATR3: 첫 손절 = 진입가 − 3 × ATR(20), 그 뒤 '진입 뒤 최고 종가 − 3 × ATR' 로 따라 올림
  (내려가지 않음). 크기도 이 폭으로.
* C_S3ATR3: 첫 손절 −3%(지금 규칙) + 위의 ATR 따라 올림 — 둘 중 높은 쪽.

ATR(20) = 직전 20 거래일 종가 변화 절댓값 평균(국장 자료가 종가뿐이라 두 시장 같은 정의).
장중 손절: 미국은 그날 저가 ≤ 손절가면 손절가(시가가 이미 아래면 시가),
국장은 종가 ≤ 손절가면 그 종가.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from itertools import pairwise
from pathlib import Path
from typing import Any

from scripts.run_a1_train import git_state
from scripts.run_lt_kr import (
    EXPECTED_DATA,
    data_hash,
    leaders_pit,
    members,
    month_end_day,
    simulate_kr,
)
from scripts.run_lt_kr import build_panel as build_kr
from scripts.run_lt_leaders import (
    FEE,
    FROM,
    SPLIT,
    SPY_SMA,
    TO,
    leaders,
    simulate,
    stats,
    trend_up,
)
from scripts.run_lt_leaders import build_panel as build_us
from scripts.run_sb_breakout import SPY, STOCKS, _sma, load

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "reports" / "exit_242"
FIRST_END = "2016-12-31"
RISK = 0.01
"""한 번에 잃어도 되는 돈 — 계좌의 1% (💹 사용자 규칙)."""
CAP = 0.25
MAX_POS = 10
ATR_N = 20
WHIPSAW_DAYS = 20
SHARPE_GAIN = 0.15
"""후보 셋을 보므로 238(0.10)보다 엄격하게."""
MDD_CAP = 0.40


@dataclass(frozen=True, slots=True)
class Exit:
    name: str
    fixed: float | None
    """진입가 기준 첫 손절 폭 (0.03 = −3%). None 이면 ATR 로."""
    atr_k: float | None
    """'진입 뒤 최고 종가 − k × ATR' 따라 올림. None 이면 없음."""
    ma: int | None
    """종가 < n 일선이면 다음 거래일 종가 매도. None 이면 없음."""


ARMS = (
    Exit("S3", 0.03, None, None),
    Exit("A_MA20", 0.03, None, 20),
    Exit("B_ATR3", None, 3.0, None),
    Exit("C_S3ATR3", 0.03, 3.0, None),
)
CANDIDATES = ("A_MA20", "B_ATR3", "C_S3ATR3")


@dataclass(slots=True)
class Market:
    days: list[str]
    close: dict[str, list[float | None]]
    """거래한 날 종가, 거래 못 한 날 None."""
    low: dict[str, list[float | None]] | None
    open: dict[str, list[float | None]] | None
    index_ok: list[bool]
    pick: Callable[[int], list[str]]
    """월말 ``t`` 의 주도주(점수 높은 순)."""
    ends_at: Callable[[str, int], bool] = lambda _s, _t: False
    """자료가 기간 끝 전에 끊기는 종목의 마지막 거래일(상장폐지)."""


def atr(xs: Sequence[float | None], t: int, n: int = ATR_N) -> float | None:
    """t 까지 거래한 마지막 n + 1 종가의 변화 절댓값 평균. 2n 거래일 안에 모자라면 None."""
    got: list[float] = []
    k = t
    while k >= 0 and len(got) < n + 1 and t - k < 2 * n:
        v = xs[k]
        if v is not None:
            got.append(v)
        k -= 1
    if len(got) < n + 1:
        return None
    return sum(abs(a - b) for a, b in pairwise(got)) / n


def sma(xs: Sequence[float | None], t: int, n: int) -> float | None:
    w = xs[t - n + 1 : t + 1]
    if t + 1 < n or any(v is None for v in w):
        return None
    return sum(w) / n  # type: ignore[arg-type]


def first_stop(arm: Exit, price: float, a: float | None) -> float | None:
    """진입 때 손절가. ATR 이 필요한데 없으면 None(사지 않음)."""
    levels = []
    if arm.fixed is not None:
        levels.append(price * (1 - arm.fixed))
    if arm.atr_k is not None:
        if a is None:
            if arm.fixed is None:
                return None
        else:
            levels.append(price - arm.atr_k * a)
    return max(levels)


def weight(price: float, stop: float) -> float:
    """💹 크기: 1% ÷ 손절 폭, 25% 상한."""
    dist = 1 - stop / price
    return CAP if dist <= RISK / CAP else RISK / dist


@dataclass(slots=True)
class Pos:
    qty: float
    cost: float
    entry: float
    i0: int
    peak: float
    stop: float
    w: float


@dataclass(slots=True)
class ExitRun:
    equity: list[float] = field(default_factory=list)
    trades: list[dict[str, Any]] = field(default_factory=list)


def simulate_exit(mk: Market, arm: Exit, start_date: str = FROM) -> ExitRun:
    """어제 정한 매도 · 매수 → 오늘 종가, 장중 손절은 진입 다음 거래일부터,
    오늘 종가로 내일 할 일."""
    days = mk.days
    start = next(i for i, d in enumerate(days) if d >= start_date)
    cash = 1.0
    pos: dict[str, Pos] = {}
    run = ExitRun()
    pending_sell: set[str] = set()
    pending_buy: list[str] | None = None

    def px(sym: str, t: int) -> float:
        xs = mk.close[sym]
        while xs[t] is None:
            t -= 1
        return xs[t]  # type: ignore[return-value]

    def sell(sym: str, t: int, price: float, reason: str) -> None:
        nonlocal cash
        p = pos.pop(sym)
        proceeds = p.qty * price * (1 - FEE)
        cash += proceeds
        run.trades.append(
            {
                "symbol": sym,
                "entry_i": p.i0,
                "exit_i": t,
                "entry_px": p.entry,
                "exit_px": price,
                "ret": proceeds / p.cost - 1,
                "w": p.w,
                "reason": reason,
            }
        )

    for t in range(start, len(days)):
        for sym in sorted(pending_sell):
            c = mk.close[sym][t]
            if sym in pos and c is not None:
                sell(sym, t, c, "ma")
        pending_sell = {s for s in pending_sell if s in pos}
        equity = cash + sum(p.qty * px(s, t) for s, p in pos.items())
        if pending_buy is not None:
            for sym in pending_buy:
                c = mk.close[sym][t]
                if c is None or sym in pos or len(pos) >= MAX_POS or cash <= 0:
                    continue
                stop = first_stop(arm, c, atr(mk.close[sym], t))
                if stop is None:
                    continue
                w = weight(c, stop)
                amt = min(w * equity, cash)
                pos[sym] = Pos(amt * (1 - FEE) / c, amt, c, t, c, stop, w)
                cash -= amt
            pending_buy = None
        for sym in sorted(pos):
            if mk.ends_at(sym, t):
                pending_sell.discard(sym)
                sell(sym, t, px(sym, t), "delisted")
        # 장중 손절 (진입 다음 거래일부터)
        for sym in sorted(pos):
            p = pos[sym]
            c = mk.close[sym][t]
            if t <= p.i0 or c is None:
                continue
            lo = mk.low[sym][t] if mk.low is not None else None
            op = mk.open[sym][t] if mk.open is not None else None
            if lo is not None:
                if lo <= p.stop:
                    sell(sym, t, op if op is not None and op < p.stop else p.stop, "stop")
            elif c <= p.stop:
                sell(sym, t, c, "stop")
        # 오늘 종가로 손절가 따라 올림 · 20 일선 매도 예약
        for sym, p in pos.items():
            c = mk.close[sym][t]
            if c is None:
                continue
            p.peak = max(p.peak, c)
            if arm.atr_k is not None and (a := atr(mk.close[sym], t)) is not None:
                p.stop = max(p.stop, p.peak - arm.atr_k * a)
            if arm.ma is not None and t > p.i0:
                m = sma(mk.close[sym], t, arm.ma)
                if m is not None and c < m:
                    pending_sell.add(sym)
        if month_end_day(days, t) and mk.index_ok[t]:
            pending_buy = [
                s
                for s in mk.pick(t)
                if s not in pos and s not in pending_sell and trend_up(mk.close[s], t)
            ]
        run.equity.append(cash + sum(p.qty * px(s, t) for s, p in pos.items()))
    return run


def trade_stats(mk: Market, run: ExitRun) -> dict[str, Any]:
    tr = run.trades
    if not tr:
        return {"trades": 0}
    stops = [x for x in tr if x["reason"] == "stop"]
    back = 0
    for x in stops:
        xs = mk.close[x["symbol"]][x["exit_i"] + 1 : x["exit_i"] + 1 + WHIPSAW_DAYS]
        if any(v is not None and v >= x["entry_px"] for v in xs):
            back += 1
    years = (len(run.equity) - 1) / 252
    rets = sorted(x["ret"] for x in tr)
    return {
        "trades": len(tr),
        "trades_per_year": round(len(tr) / years, 1),
        "win_rate": round(sum(1 for r in rets if r > 0) / len(rets), 3),
        "avg_ret": round(sum(rets) / len(rets), 4),
        "best_ret": round(rets[-1], 4),
        "worst_ret": round(rets[0], 4),
        "avg_hold_days": round(sum(x["exit_i"] - x["entry_i"] for x in tr) / len(tr), 1),
        "avg_weight": round(sum(x["w"] for x in tr) / len(tr), 3),
        "stop_share": round(len(stops) / len(tr), 3),
        "stops_per_year": round(len(stops) / years, 1),
        "stop_recovered_20d": round(back / len(stops), 3) if stops else None,
    }


def _row(span: Sequence[str], eq: Sequence[float]) -> dict[str, Any]:
    end = span[-1]
    return {
        "full": stats(span, eq, FROM, end),
        "first": stats(span, eq, FROM, FIRST_END),
        "second": stats(span, eq, SPLIT, end),
    }


def passes_us(x: dict[str, Any], s3: dict[str, Any]) -> bool:
    """미국(주 판정): 샤프 S3 + 0.15 이상 · 낙폭 40% 이하 · 두 반쪽 샤프 모두 S3 이상."""
    return (
        x["full"]["sharpe"] >= s3["full"]["sharpe"] + SHARPE_GAIN
        and x["full"]["mdd"] <= MDD_CAP
        and all(x[h]["sharpe"] >= s3[h]["sharpe"] for h in ("first", "second"))
    )


def judge(us: dict[str, Any], kr: dict[str, Any]) -> dict[str, Any]:
    """사전 등록 §4 (순수). 미국 통과 + 국장 샤프가 S3 보다 높은 후보 중 미국 샤프 최고를 고른다."""
    us_ok = [c for c in CANDIDATES if passes_us(us[c], us["S3"])]
    both = [c for c in us_ok if kr[c]["full"]["sharpe"] > kr["S3"]["full"]["sharpe"]]
    if both:
        best = max(both, key=lambda c: us[c]["full"]["sharpe"])
        return {"verdict": f"ADOPT_{best}", "us_pass": us_ok, "both_pass": both}
    if us_ok:
        return {"verdict": "US_ONLY", "us_pass": us_ok, "both_pass": []}
    return {"verdict": "NONE", "us_pass": [], "both_pass": []}


def us_market() -> tuple[Market, list[float]]:
    """미국 판과 113 BASE 자산 곡선."""
    panel = build_us()  # 111 유니버스 해시 확인 포함
    spy = load(SPY)
    spy_map = dict(zip(spy.dates, spy.close, strict=True))
    spy_close = [spy_map[d] for d in panel.days]
    ma = _sma(spy_close, SPY_SMA)
    spy_ok = [m is not None and c > m for c, m in zip(spy_close, ma, strict=True)]
    op: dict[str, list[float | None]] = {}
    lo: dict[str, list[float | None]] = {}
    for sym in panel.close:
        s = load(STOCKS / f"{sym}.csv")
        mo = dict(zip(s.dates, s.open, strict=True))
        ml = dict(zip(s.dates, s.low, strict=True))
        op[sym] = [mo.get(d) for d in panel.days]
        lo[sym] = [ml.get(d) for d in panel.days]
    mk = Market(
        panel.days, panel.close, lo, op, spy_ok, lambda t: [s for s, _ in leaders(panel, t)]
    )
    return mk, simulate(panel, spy_ok).equity


def kr_market() -> tuple[Market, list[float]]:
    """국장 판과 141 BASE 자산 곡선."""
    got, _n = data_hash()
    if got != EXPECTED_DATA:
        raise SystemExit(f"국장 자료 해시가 141 과 다르다: {got}")
    panel, index_ok = build_kr()
    mk = Market(
        panel.days,
        panel.close,
        None,
        None,
        index_ok,
        lambda t: [s for s, _ in leaders_pit(panel, t, members(panel, t))],
        panel.ends_at,
    )
    return mk, simulate_kr(panel, index_ok).equity


def evaluate(mk: Market, base_equity: Sequence[float]) -> dict[str, Any]:
    start = next(i for i, d in enumerate(mk.days) if d >= FROM)
    span = mk.days[start:]
    rows: dict[str, Any] = {"BASE": _row(span, base_equity)}
    for arm in ARMS:
        r = simulate_exit(mk, arm)
        rows[arm.name] = {**_row(span, r.equity), "trade": trade_stats(mk, r)}
    return rows


def run() -> dict[str, Any]:
    target = OUT / "verdict.json"
    if target.exists():
        raise SystemExit(f"이미 판정했다: {target}")
    sha, dirty = git_state()
    if dirty:
        raise SystemExit("작업 트리가 깨끗하지 않다 — 커밋 뒤에 실행")
    us_rows = evaluate(*us_market())
    kr_rows = evaluate(*kr_market())
    out = {
        "git": sha,
        "period": [FROM, TO],
        **judge(us_rows, kr_rows),
        "us": us_rows,
        "kr": kr_rows,
        "note": (
            "문서 242 1 회 판정. 미국 = 113 의 지금 S&P 500 명단"
            "(생존 편향, 238 과 같은 자료 — 설명용 성격), "
            "국장 = 141 의 폐지 포함 그 시점 상위 200(종가만 — 손절은 종가로). "
            "BASE 는 113 · 141 추세 보유(참고)."
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
