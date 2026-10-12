"""SN20 Paper — S&P 500 상위 10 + 나스닥 100 상위 10 (모멘텀, 겹치면 다음 순위) 20 종목. 주문 없음.

장부 두 개를 같은 날 같은 시세로 돌린다:

* ``SN20``  — 각 5%, 판단일 교체 · T/v2 추세 손절 (:mod:`auto_trading.ops.nq_paper`).
* ``SN20C`` — SN20 + 판단일마다 10% 넘은 종목을 5% 로 덜고 5% 미만을 채움 (``cap_and_fill``).

둘 다 양도세를 추정한다 (:mod:`auto_trading.ops.paper_tax`):
장부 1,000 USD = 실제 ``SCALE_KRW`` 원으로 보고
연 250 만 원 공제 · 22%, 12 월 15 일 뒤 첫 처리일에 남은 공제만큼 이익 실현 후 재매수(D).
시세는 새로 받지 않는다 — S&P 500 은 LT Paper, 나스닥 100 은 NQ Paper 의 일봉(끝난 날만).

사용 예::

    python -m scripts.sn20_paper run      # 두 장부 처리 안 한 거래일
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
from auto_trading.ops.paper_tax import TaxBook, add_trades, estimate, harvest, roll_year

REPO = Path(__file__).resolve().parent.parent
LT_BARS = REPO / "data" / "lt_paper" / "bars"
LT_META = REPO / "data" / "lt_paper" / "meta.json"
NQ_BARS = REPO / "data" / "nq_paper" / "bars"
NQ_META = REPO / "data" / "nq_paper" / "meta.json"
ROOT = REPO / "data" / "sn20_paper"
START_CASH = 1_000.0
N_EACH, SLOT = 10, 0.05
SCALE_KRW = 10_000_000
"""세금 환산: 장부 1,000 USD 를 실제 1,000 만 원으로 본다 (사용자가 금액을 정하면 바꾼다)."""
BOOKS: dict[str, dict[str, Any]] = {
    "SN20": {"state": ROOT / "state.json", "reports": REPO / "reports" / "sn20_paper", "cap": None},
    "SN20C": {
        "state": ROOT / "state_c.json",
        "reports": REPO / "reports" / "sn20c_paper",
        "cap": 0.10,
    },
}


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


def _load(path: Path, fresh: bool) -> tuple[PaperState, TaxBook]:
    if fresh or not path.exists():
        return PaperState(cash=START_CASH), TaxBook(SCALE_KRW, START_CASH)
    raw = json.loads(path.read_text(encoding="utf-8"))
    tax = raw.pop("tax", None)
    book = TaxBook.from_json(tax) if tax else TaxBook(SCALE_KRW, START_CASH)
    return PaperState.from_json(raw), book


def run(*, fresh: bool = False) -> dict[str, object]:
    ROOT.mkdir(parents=True, exist_ok=True)
    days, sp, nq = _panel()
    closes = {**nq, **sp}  # 둘 다 있는 종목은 S&P(LT) 쪽 일봉
    rebal = rebalance_days(days)
    out: dict[str, object] = {}
    for name, cfg in BOOKS.items():
        state, tax = _load(cfg["state"], fresh)
        after = state.last_date or max(d for d in days if d < START)
        todo = [t for t, d in enumerate(days) if d > after and t >= 1]
        reports: Path = cfg["reports"]
        reports.mkdir(parents=True, exist_ok=True)
        report: dict[str, Any] = {}
        for t in todo:
            settled = roll_year(tax, days[t])
            actions = step(
                state,
                days,
                closes,
                t,
                t in rebal,
                pick=lambda i: combined_top(sp, nq, i, N_EACH),
                slot=SLOT,
                cap=cfg["cap"],
            )
            add_trades(tax, actions["sold"] + actions["trimmed"])
            actions["harvested"] = harvest(tax, state, closes, t, days[t])
            actions["tax_settled"] = settled
            report = _report(name, state, tax, actions, days, closes, t)
            (reports / f"{days[t]}.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8"
            )
        cfg["state"].write_text(
            json.dumps({**state.to_json(), "tax": tax.to_json()}, indent=1), encoding="utf-8"
        )
        if report:
            (reports / "latest.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8"
            )
        out[name] = {
            "processed_days": len(todo),
            "last_date": state.last_date,
            "equity": state.equity[-1][1] if state.equity else None,
        }
    return out


def rebuild() -> dict[str, object]:
    for cfg in BOOKS.values():
        for f in cfg["reports"].glob("2026-*.json"):
            f.unlink()
    return run(fresh=True)


def _report(
    name: str,
    state: PaperState,
    tax: TaxBook,
    actions: dict[str, Any],
    days: list[str],
    closes: dict[str, list[float | None]],
    t: int,
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
    tax_ytd = estimate(tax)
    after_tax = actions["equity"] - tax.tax_paid - tax_ytd
    return {
        "book": name,
        "date": days[t],
        "equity_usd": actions["equity"],
        "after_tax_equity_usd": round(after_tax, 2),
        "tax": {
            "scale_krw": tax.scale_krw,
            "exemption_usd_in_book": round(tax.exemption, 2),
            "realized_ytd_usd": round(tax.realized, 2),
            "tax_estimate_ytd_usd": round(tax_ytd, 2),
            "tax_paid_prior_years_usd": round(tax.tax_paid, 2),
            "realized_ytd_krw": round(tax.realized / START_CASH * tax.scale_krw),
            "tax_estimate_ytd_krw": round(tax_ytd / START_CASH * tax.scale_krw),
            "history": tax.history,
        },
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
            "trimmed": actions["trimmed"],
            "filled": actions["filled"],
            "harvested": actions["harvested"],
        },
        "pending_sell_next_session": actions["pending_sell"],
        "top_as_of": actions["ranking_as_of"],
        "sp500_top": [{"symbol": s, "score": sc} for s, sc in top[:N_EACH]],
        "nasdaq_top_after_overlap": [{"symbol": s, "score": sc} for s, sc in top[N_EACH:]],
        "next_rebalance": next_rebalance(days, t),
        "rules": (
            "판단일마다 10% 넘은 종목을 5% 로 덜고 5% 미만 채움 · " if name == "SN20C" else ""
        )
        + "S&P 500 상위 10 + 나스닥 100 상위 10(겹치면 다음 순위) · 각 5% · "
        "판단일(그달 첫 거래일 · 14 일 뒤) 교체 · "
        "T/v2 추세(20·60·120 부호 합 ≤ 0) 꺼지면 다음 날 매도",
        "note": "가상 계좌, 주문 없음. 지금 지수 명단(생존 편향) — "
        "실행 확인용 기록, 우위 판정 근거 아님 (225).",
    }


def status() -> dict[str, object]:
    out: dict[str, object] = {}
    for name, cfg in BOOKS.items():
        latest = cfg["reports"] / "latest.json"
        out[name] = (
            json.loads(latest.read_text(encoding="utf-8"))
            if latest.exists()
            else {"note": "아직 실행 전"}
        )
    return out


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
