"""QS Paper (문서 116) — QS · 비교 A · AG-US 세 장부를 가상으로 매일 기록한다. 주문 없음.

사용 예::

    python -m scripts.qs_paper init     # 처음 한 번: 110 자료(QQQ·TQQQ)를 Paper 폴더로 복사
    python -m scripts.qs_paper update   # 한국투자증권에서 QQQ·TQQQ 최근 일봉 (읽기 전용)
    python -m scripts.qs_paper run      # 처리 안 한 거래일을 엔진으로 처리, 보고서
    python -m scripts.qs_paper daily    # update + run (예약 작업용)
    python -m scripts.qs_paper rebuild  # 시세를 고친 뒤 같은 시작일부터 다시 계산
    python -m scripts.qs_paper status

자료 ``data/qs_paper/bars/{QQQ,TQQQ}.csv`` (110 연구 자료는 그대로),
상태 ``data/qs_paper/state.json``,
보고 ``reports/qs_paper/<날짜>.json`` · ``reports/qs_paper/latest.json``.
"""

from __future__ import annotations

import json
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scripts.lt_paper import _read, fetch_merge

from auto_trading.ops.qs_paper import LOOKBACKS, QsState, settle_year, step, trend_on
from auto_trading.ops.us_daily_bars import last_settled_day

REPO = Path(__file__).resolve().parent.parent
RESEARCH = REPO / "data" / "research" / "tradfi"
ROOT = REPO / "data" / "qs_paper"
BARS = ROOT / "bars"
STATE = ROOT / "state.json"
REPORTS = REPO / "reports" / "qs_paper"
SYMBOLS = {"QQQ": "QQQ_adj", "TQQQ": "TQQQ_adj"}
DEPOSIT = 1_000.0
FULL_SINCE = "2007-08-01"


def init() -> dict[str, object]:
    if STATE.exists():
        raise SystemExit(f"이미 시작했다: {STATE}")
    BARS.mkdir(parents=True, exist_ok=True)
    for sym, src in SYMBOLS.items():
        shutil.copyfile(RESEARCH / f"{src}.csv", BARS / f"{sym}.csv")
    state = QsState.new(DEPOSIT)
    data = {**state.to_json(), "started": datetime.now(UTC).isoformat(timespec="seconds")}
    STATE.write_text(json.dumps(data, indent=1), encoding="utf-8")
    return {"symbols": list(SYMBOLS), "deposit": DEPOSIT}


def update() -> dict[str, object]:
    from auto_trading.config.secrets import load_kis_credentials
    from auto_trading.exchange.kis import KisOverseasReadOnly, KisTokenCache

    broker = KisOverseasReadOnly(
        load_kis_credentials(REPO / ".env"), KisTokenCache(REPO / "data" / "kis_token.json")
    )
    last_ok = last_settled_day()
    out: dict[str, object] = {"settled_through": last_ok}
    try:
        for sym in SYMBOLS:
            m = fetch_merge(broker, sym, "NAS", BARS / f"{sym}.csv", last_ok)
            out[sym] = {
                "appended": m.appended,
                "repaired": m.repaired,
                "dropped_unsettled": m.dropped_unsettled,
                "refetched": m.needs_full,
                "last": m.rows[-1]["date"],
            }
    finally:
        broker.close()
    return out


def _panel() -> tuple[list[str], list[float], list[float]]:
    q = {r["date"]: float(r["close"]) for r in _read(BARS / "QQQ.csv")}
    tq = {r["date"]: float(r["close"]) for r in _read(BARS / "TQQQ.csv")}
    days = sorted(set(q) & set(tq))
    return days, [q[d] for d in days], [tq[d] for d in days]


def run(*, rebuild_from: str | None = None) -> dict[str, object]:
    """처리 안 한 거래일을 처리한다. ``rebuild_from`` 이면 새 장부로 그날부터 다시 계산한다."""
    raw = json.loads(STATE.read_text(encoding="utf-8"))
    started = raw.pop("started", None)
    state = QsState.new(DEPOSIT) if rebuild_from else QsState.from_json(raw)
    days, qqq, tqqq = _panel()
    if rebuild_from:
        todo = [i for i, d in enumerate(days) if d >= rebuild_from]
    elif state.last_date is None:
        todo = [len(days) - 1]  # 첫 실행: 가장 최근 거래일 (그달 입금 → 전날 추세로 처리)
    else:
        todo = [i for i, d in enumerate(days) if d > state.last_date]
    REPORTS.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {}
    for t in todo:
        # 그해 마지막 날인지는 다음 거래일 자료가 와야 안다 → 모르고 넘겼으면 새해 첫날 앞에 정산.
        if state.last_date and state.last_date[:4] != days[t][:4]:
            settle_year(state)
        last = t + 1 < len(days) and days[t + 1][:4] != days[t][:4]
        actions = step(state, days, qqq, tqqq, t, last_of_year=last)
        report = _report(state, actions, days, qqq, tqqq, t)
        (REPORTS / f"{days[t]}.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8"
        )
    STATE.write_text(
        json.dumps({**state.to_json(), "started": started}, indent=1), encoding="utf-8"
    )
    if report:
        (REPORTS / "latest.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8"
        )
    return {
        "processed_days": len(todo),
        "last_date": state.last_date,
        "values": {k: b.equity[-1][1] for k, b in state.books.items() if b.equity},
    }


def _report(
    state: QsState,
    actions: dict[str, Any],
    days: list[str],
    qqq: list[float],
    tqqq: list[float],
    t: int,
) -> dict[str, Any]:
    on_today = trend_on(qqq, t)
    parts = {f"r{k}": round(qqq[t] / qqq[t - k] - 1, 4) for k in LOOKBACKS}
    books: dict[str, Any] = {}
    for k, b in state.books.items():
        value = b.value(qqq[t], tqqq[t])
        if k == "QS":
            if on_today and b.prev_on is False and b.cash > 0:
                nxt = f"현금 {b.cash:,.2f} 전액 TQQQ 매수 (추세 회복)"
            elif not on_today and b.prev_on and b.tqqq > 0:
                nxt = "TQQQ 전부 매도 (추세 꺾임)"
            else:
                nxt = "없음 (그달 첫 거래일이면 입금)"
        elif k == "AG":
            if on_today and b.tqqq == 0 and b.cash > 0:
                nxt = "현금 전액 TQQQ 매수"
            elif not on_today and b.tqqq > 0:
                nxt = "TQQQ 전부 매도"
            else:
                nxt = "없음"
        else:
            nxt = "그달 첫 거래일에 입금 → QQQ 매수"
        books[k] = {
            "value_usd": round(value, 2),
            "invested_usd": round(b.invested, 2),
            "profit_usd": round(value - b.invested, 2),
            "return_on_invested": round(value / b.invested - 1, 4) if b.invested else None,
            "qqq_shares": round(b.qqq, 6),
            "qqq_usd": round(b.qqq * qqq[t], 2),
            "tqqq_shares": round(b.tqqq, 6),
            "tqqq_usd": round(b.tqqq * tqqq[t], 2),
            "cash_usd": round(b.cash, 2),
            "tax_paid_usd": round(b.tax_paid, 2),
            "realized_this_year_usd": round(b.year_gain, 2),
            "today": actions["books"][k]["actions"],
            "next_session_if_trend_holds": nxt,
            "trades": b.trades[-10:],
        }
    return {
        "date": days[t],
        "qqq_close": round(qqq[t], 4),
        "tqqq_close": round(tqqq[t], 4),
        "trend_used_today": {"as_of": actions["trend_on_as_of"], "on": actions["trend_on"]},
        "trend_for_next_session": {"as_of": days[t], "on": on_today, **parts},
        "books": books,
    }


def rebuild() -> dict[str, object]:
    """시세를 고친 뒤: 같은 시작일부터 새 장부로 다시 계산 (2026-10-12 미완성 일봉 결함)."""
    raw = json.loads(STATE.read_text(encoding="utf-8"))
    raw.pop("started", None)
    old = QsState.from_json(raw)
    first = old.books["QS"].equity[0][0]
    before = {k: b.equity[-1][1] for k, b in old.books.items() if b.equity}
    return {"from": first, "before": before, **run(rebuild_from=first)}


def status() -> dict[str, object]:
    latest = REPORTS / "latest.json"
    return (
        json.loads(latest.read_text(encoding="utf-8"))
        if latest.exists()
        else {"note": "아직 실행 전"}
    )


def _nq_daily() -> dict[str, object]:
    """같은 예약 작업 안에서 NQ Paper(나스닥 100 상위 10)와
    SN20 Paper(S&P 10 + 나스닥 10)도 진행한다.

    두 장부의 날짜 축은 이 작업이 갱신하는 QQQ 일봉이라 QS 갱신 **뒤** 에 돈다.
    예전엔 LT 작업(21:10) 안에서 돌아 QQQ(21:15 갱신)가 아직 전날까지라 늘 하루씩 밀렸다
    (2026-10-13 발견: 10-12 일봉이 있는데 장부는 10-09).
    SN20 이 쓰는 LT 일봉은 21:10 작업이 먼저 받는다.
    실패해도 QS 는 그대로 끝난다.
    """
    try:
        from scripts import nq_paper, sn20_paper

        return {"update": nq_paper.update(), "run": nq_paper.run(), "sn20": sn20_paper.run()}
    except Exception as exc:  # NQ 문제로 QS 작업을 실패시키지 않는다
        return {"error": f"{type(exc).__name__}: {exc}"}


def main(argv: list[str]) -> int:
    commands = {
        "init": init,
        "update": update,
        "run": run,
        "rebuild": rebuild,
        "status": status,
    }
    if argv == ["daily"]:
        result: dict[str, object] = {"update": update(), "run": run()}
        result["nq"] = _nq_daily()
    elif len(argv) == 1 and argv[0] in commands:
        result = commands[argv[0]]()
    else:
        raise SystemExit(
            "usage: python -m scripts.qs_paper init | update | run | daily | rebuild | status"
        )
    sys.stdout.write(json.dumps(result, ensure_ascii=True, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
