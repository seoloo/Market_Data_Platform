"""SN20 Paper — S&P 500 상위 10 + 나스닥 100 상위 10 (모멘텀, 겹치면 다음 순위) 20 종목. 주문 없음.

규칙은 :mod:`auto_trading.ops.nq_paper` (``combined_top``, 각 5%,
판단일 · T/v2 추세 손절은 NQ 와 같다). 시세는 새로 받지 않는다 —
S&P 500 은 LT Paper(``data/lt_paper/bars``), 나스닥 100 은 NQ Paper 의 일봉을 쓴다
(둘 다 끝난 날만, ``ops/us_daily_bars``).

사용 예::

    python -m scripts.sn20_paper run      # 처리 안 한 거래일
    python -m scripts.sn20_paper rebuild  # START 부터 다시
    python -m scripts.sn20_paper status
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from scripts.lt_paper import _read
from scripts.nq_paper import HISTORY_SINCE, QQQ, START

from auto_trading.ops.lt_paper import PaperState
from auto_trading.ops.nq_paper import combined_top, next_rebalance, rebalance_days, step

REPO = Path(__file__).resolve().parent.parent
LT_BARS = REPO / "data" / "lt_paper" / "bars"
LT_META = REPO / "data" / "lt_paper" / "meta.json"
NQ_BARS = REPO / "data" / "nq_paper" / "bars"
NQ_META = REPO / "data" / "nq_paper" / "meta.json"
ROOT = REPO / "data" / "sn20_paper"
STATE = ROOT / "state.json"
REPORTS = REPO / "reports" / "sn20_paper"
START_CASH = 1_000.0
N_EACH, SLOT = 10, 0.05


def _series(folder: Path, syms: list[str], days: list[str]) -> dict[str, list[float | None]]:
    out: dict[str, list[float | None]] = {}
    for s in syms:
        p = folder / f"{s}.csv"
        if p.exists():
            m = {r["date"]: float(r["close"]) for r in _read(p)}
            out[s] = [m.get(d) for d in days]
    return out


def _panel() -> tuple[list[str], dict[str, list[float | None]], dict[str, list[float | None]]]:
    days = [r["date"] for r in _read(QQQ) if r["date"] >= HISTORY_SINCE]
    sp_syms = [
        s for s in json.loads(LT_META.read_text(encoding="utf-8"))["exchanges"] if s != "SPY"
    ]
    nq_syms = list(json.loads(NQ_META.read_text(encoding="utf-8"))["symbols"])
    return days, _series(LT_BARS, sp_syms, days), _series(NQ_BARS, nq_syms, days)


def run(*, fresh: bool = False) -> dict[str, object]:
    ROOT.mkdir(parents=True, exist_ok=True)
    state = (
        PaperState(cash=START_CASH)
        if fresh or not STATE.exists()
        else PaperState.from_json(json.loads(STATE.read_text(encoding="utf-8")))
    )
    days, sp, nq = _panel()
    closes = {**nq, **sp}  # 둘 다 있는 종목은 S&P(LT) 쪽 일봉
    rebal = rebalance_days(days)
    after = state.last_date or max(d for d in days if d < START)
    todo = [t for t, d in enumerate(days) if d > after and t >= 1]
    REPORTS.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {}
    for t in todo:
        actions = step(
            state,
            days,
            closes,
            t,
            t in rebal,
            pick=lambda i: combined_top(sp, nq, i, N_EACH),
            slot=SLOT,
        )
        report = _report(state, actions, days, closes, t, sp)
        (REPORTS / f"{days[t]}.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8"
        )
    STATE.write_text(json.dumps(state.to_json(), indent=1), encoding="utf-8")
    if report:
        (REPORTS / "latest.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8"
        )
    return {
        "processed_days": len(todo),
        "last_date": state.last_date,
        "equity": state.equity[-1][1] if state.equity else None,
    }


def rebuild() -> dict[str, object]:
    for f in REPORTS.glob("2026-*.json"):
        f.unlink()
    return run(fresh=True)


def _report(
    state: PaperState,
    actions: dict[str, Any],
    days: list[str],
    closes: dict[str, list[float | None]],
    t: int,
    sp: dict[str, list[float | None]],
) -> dict[str, Any]:
    def px(sym: str) -> float:
        xs, i = closes[sym], t
        while xs[i] is None:
            i -= 1
        v = xs[i]
        assert v is not None
        return v

    qqq = {r["date"]: float(r["close"]) for r in _read(QQQ)}
    first = state.equity[0][0] if state.equity else days[t]
    base = qqq.get(days[days.index(first) - 1]) if first in days else None
    top = actions["top"]
    return {
        "date": days[t],
        "equity_usd": actions["equity"],
        "start_usd": START_CASH,
        "return_since_start": round(actions["equity"] / START_CASH - 1, 4),
        "qqq_since_start": round(qqq[days[t]] / base - 1, 4) if base and days[t] in qqq else None,
        "cash_usd": round(state.cash, 2),
        "positions": [
            {
                "symbol": s,
                "entry": p.entry,
                "cost_usd": round(p.cost, 2),
                "value_usd": round(p.qty * px(s), 2),
                "return": round(p.qty * px(s) / p.cost - 1, 4),
            }
            for s, p in sorted(state.positions.items())
        ],
        "today": {
            "rebalance": actions["rebalance"],
            "sold": actions["sold"],
            "bought": actions["bought"],
        },
        "pending_sell_next_session": actions["pending_sell"],
        "top_as_of": actions["ranking_as_of"],
        "sp500_top": [{"symbol": s, "score": sc} for s, sc in top[:N_EACH]],
        "nasdaq_top_after_overlap": [{"symbol": s, "score": sc} for s, sc in top[N_EACH:]],
        "next_rebalance": next_rebalance(days, t),
        "rules": "S&P 500 상위 10 + 나스닥 100 상위 10(겹치면 다음 순위) · 각 5% · "
        "판단일(그달 첫 거래일 · 14 일 뒤) 교체 · "
        "T/v2 추세(20·60·120 부호 합 ≤ 0) 꺼지면 다음 날 매도",
        "note": "가상 계좌, 주문 없음. 지금 지수 명단(생존 편향) — "
        "실행 확인용 기록, 우위 판정 근거 아님 (225).",
    }


def status() -> dict[str, object]:
    latest = REPORTS / "latest.json"
    return (
        json.loads(latest.read_text(encoding="utf-8"))
        if latest.exists()
        else {"note": "아직 실행 전"}
    )


def main(argv: list[str]) -> int:
    commands = {"run": run, "rebuild": rebuild, "status": status}
    if len(argv) == 1 and argv[0] in commands:
        result = commands[argv[0]]()
    else:
        raise SystemExit("usage: python -m scripts.sn20_paper run | rebuild | status")
    sys.stdout.write(json.dumps(result, ensure_ascii=True, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
