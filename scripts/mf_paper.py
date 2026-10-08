"""나스닥 + 추세 추종 소매 Paper (문서 177, 176 B 의 앞으로의 확인). 돈 · 주문 없음.

사용 예::

    python -m scripts.mf_paper run      # 매일 1 회 (권장 화 ~ 토 07:40 KST, 미국 마감 뒤)
    python -m scripts.mf_paper status   # 지금까지 요약 (네트워크 없음)
    python -m scripts.mf_paper judge    # 등록 §4 판단 (2027-10 · 2028-10, AQR 월 자료 다시 받음)

* MIX = QNDX 80% + DBMF · KMLM · CTA 각 6.67%(소매 20%) · BASE = QNDX 100%. 시작 1,000 USD.
* 시세: Yahoo 일봉 배당 조정 종가(키 없음). 빈 날은 직전 값. 모두 QNDX 거래일 줄로 맞춘다.
* 매달 첫 거래일 종가에 목표로 다시 맞춘다. 비용 편도 0.15%(수수료 0.10% + 환전 0.05%, 176 과 같음).
* 상태 ``data/mf_paper/state.json`` · 보고 ``reports/mf_paper/latest.json``.
"""

from __future__ import annotations

import json
import sys
import time
from dataclasses import asdict
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path
from typing import Any

from auto_trading.ops.mf_paper import (
    Book,
    judge,
    mark,
    monthly_returns,
    new_month,
    open_book,
    rebalance,
)

REPO = Path(__file__).resolve().parent.parent
ROOT = REPO / "data" / "mf_paper"
STATE = ROOT / "state.json"
REPORTS = REPO / "reports" / "mf_paper"
EQUITY = "QNDX"
SLEEVE = ("DBMF", "KMLM", "CTA")
MIX = {EQUITY: 0.8, **{s: 0.2 / len(SLEEVE) for s in SLEEVE}}
BASE = {EQUITY: 1.0}
CAPITAL = 1_000.0
COST = 0.0015
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36"


def _prices(span: str = "3mo") -> dict[str, dict[str, float]]:
    import httpx

    out: dict[str, dict[str, float]] = {}
    for sym in (EQUITY, *SLEEVE):
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range={span}&interval=1d"
        res = httpx.get(url, headers={"User-Agent": UA}, timeout=60).json()["chart"]["result"][0]
        adj = res["indicators"]["adjclose"][0]["adjclose"]
        # 미국 종가 시각(UTC 20:00 무렵) → 그 거래일 날짜
        out[sym] = {
            datetime.fromtimestamp(t, UTC).strftime("%Y-%m-%d"): a
            for t, a in zip(res["timestamp"], adj, strict=True)
            if a
        }
        time.sleep(0.3)
    return out


def _book_from(d: dict[str, Any]) -> Book:
    return Book(
        units=d["units"],
        peak=d["peak"],
        mdd=d["mdd"],
        costs=d["costs"],
        history=[tuple(x) for x in d["history"]],  # type: ignore[misc]
    )


def run() -> dict[str, Any]:
    px = _prices()
    days = sorted(px[EQUITY])
    today_utc = datetime.now(UTC).strftime("%Y-%m-%d")
    # 아직 끝나지 않은 오늘 봉(미국 장중)은 쓰지 않는다
    days = [d for d in days if d < today_utc or datetime.now(UTC).hour >= 21]
    filled: dict[str, dict[str, float]] = {}
    last: dict[str, float] = {}
    for d in days:
        for s in px:
            if d in px[s]:
                last[s] = px[s][d]
        if all(s in last for s in px):
            filled[d] = dict(last)
    ROOT.mkdir(parents=True, exist_ok=True)
    if STATE.exists():
        st = json.loads(STATE.read_text(encoding="utf-8"))
        books = {k: _book_from(v) for k, v in st["books"].items()}
        last_day = st["last_day"]
        start = st["start"]
    else:
        start = max(filled)
        books = {
            "MIX": open_book(CAPITAL, MIX, filled[start], COST),
            "BASE": open_book(CAPITAL, BASE, filled[start], COST),
        }
        for b in books.values():
            mark(b, start, filled[start])
        last_day = start
    new_days = [d for d in sorted(filled) if d > last_day]
    rebal = []
    for d in new_days:
        if new_month(last_day, d):
            for name, b in books.items():
                fee = rebalance(b, MIX if name == "MIX" else BASE, filled[d], COST)
                rebal.append([d, name, round(fee, 4)])
        for b in books.values():
            mark(b, d, filled[d])
        last_day = d
    state = {
        "start": start,
        "last_day": last_day,
        "books": {k: asdict(v) for k, v in books.items()},
    }
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    report = _report(state, filled, rebal)
    REPORTS.mkdir(parents=True, exist_ok=True)
    (REPORTS / "latest.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    return report


def _report(
    state: dict[str, Any], filled: dict[str, dict[str, float]], rebal: list[Any]
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "start": state["start"],
        "last_day": state["last_day"],
        "rebalanced_today": rebal,
    }
    for name, b in state["books"].items():
        hist = b["history"]
        v = hist[-1][1]
        out[name] = {
            "value": round(v, 2),
            "return": round(v / CAPITAL - 1, 4),
            "mdd": round(b["mdd"], 4),
            "costs": round(b["costs"], 2),
        }
    if filled and state["start"] in filled:
        s0, s1 = filled[state["start"]], filled[state["last_day"]]
        out["since_start"] = {s: round(s1[s] / s0[s] - 1, 4) for s in s0}
    return out


def status() -> dict[str, Any]:
    if not STATE.exists():
        return {"state": "아직 시작 전"}
    st = json.loads(STATE.read_text(encoding="utf-8"))
    return _report(st, {}, [])


def judge_now() -> dict[str, Any]:
    """등록 §4. AQR TSMOM 월 자료를 다시 받아 소매 월 수익과 비교한다."""
    import httpx

    from auto_trading.research.multi_asset import read_xlsx_sheet

    st = json.loads(STATE.read_text(encoding="utf-8"))
    url = "https://www.aqr.com/-/media/AQR/Documents/Insights/Data-Sets/Time-Series-Momentum-Factors-Monthly.xlsx"
    data = httpx.get(url, headers={"User-Agent": UA}, timeout=120, follow_redirects=True).content
    rows = read_xlsx_sheet(data, "TSMOM Factors")
    head = next(i for i, r in enumerate(rows) if "TSMOM" in r)
    ci = rows[head].index("TSMOM")
    from datetime import date, timedelta

    aqr = {}
    for r in rows[head + 1 :]:
        if len(r) > ci and r[0] and r[ci]:
            try:
                aqr[(date(1899, 12, 30) + timedelta(days=int(float(r[0])))).strftime("%Y-%m")] = (
                    float(r[ci])
                )
            except ValueError:
                continue
    px = _prices("5y")
    start = st["start"]

    def month_end(series: dict[str, float]) -> dict[str, float]:
        out: dict[str, float] = {}
        for d in sorted(series):
            if d >= start:
                out[d[:7]] = series[d]
        return out

    def rets(me: dict[str, float]) -> dict[str, float]:
        ms = sorted(me)
        return {b: me[b] / me[a] - 1 for a, b in pairwise(ms)}

    per = [rets(month_end(px[s])) for s in SLEEVE]
    months = sorted(set.intersection(*(set(r) for r in per)))
    sleeve_m = {m: sum(r[m] for r in per) / len(per) for m in months}
    equity_m = rets(month_end(px[EQUITY]))
    mix = st["books"]["MIX"]
    base = st["books"]["BASE"]
    return judge(
        sleeve_m,
        equity_m,
        aqr,
        mix["mdd"],
        base["mdd"],
        monthly_returns(mix["history"]),
        monthly_returns(base["history"]),
    )


def main(argv: list[str]) -> int:
    mode = argv[0] if argv else ""
    fn = {"run": run, "status": status, "judge": judge_now}.get(mode)
    if fn is None:
        raise SystemExit("usage: python -m scripts.mf_paper run|status|judge")
    sys.stdout.write(json.dumps(fn(), ensure_ascii=False, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            _reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main(sys.argv[1:]))
