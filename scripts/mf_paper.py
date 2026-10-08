"""나스닥 + 추세 추종 소매 Paper (문서 177, 176 B 의 앞으로의 확인). 돈 · 주문 없음.

사용 예::

    python -m scripts.mf_paper run      # 매일 1 회 (권장 화 ~ 토 07:40 KST, 미국 마감 뒤)
    python -m scripts.mf_paper status   # 지금까지 요약 (네트워크 없음)
    python -m scripts.mf_paper judge    # 등록 §4 판단 (2027-10 · 2028-10, AQR 월 자료 다시 받음)

* MIX = QNDX 80% + DBMF · KMLM · CTA 각 6.67%(소매 20%) · BASE = QNDX 100%. 시작 1,000 USD.
* NBG = QNDX 60% + IEF 20% + GLDM 20% (178, 토스판, 1,000 USD).
* NBG_ISA = TIGER 미국나스닥100 60% + TIGER 미국채10년선물 20% + ACE KRX금현물 20%
  (178 의 ISA 판, 국내 상장 · 원화 1,000,000).
  국내 상장 매니지드 퓨처스 ETF 는 없어 B 의 ISA 판은 없다.
* 시세: Yahoo 일봉 배당 조정 종가(키 없음). 빈 날은 직전 값. 장부마다 첫 종목의 거래일 줄로 맞춘다.
* LD_M0 · LD_M2 · LD_M3 = TQQQ + SCHD (182, 1,000 USD 씩).
  - M0 고정 60/40.
  - M2 낙폭 브레이크: 최근 12 개 달 말 고점 대비 −15% 면 TQQQ 30%, −30% 면 0.
  - M3 변동성 맞춤: QQQ 20 일 변동성이 20% 를 넘으면 TQQQ 60% × 0.20/σ.
  - 몫은 직전 달 말 정보로 정해 매달 첫 거래일에 맞춘다.
  - M3 는 182 에서 REJECT 였지만 사용자 결정(2026-10-09 "m2 m3 로 해줘")으로 기록한다.
* 매달 첫 거래일 종가에 목표로 다시 맞춘다. 비용 편도: 미국 ETF 0.15%(수수료 0.10% + 환전 0.05%) ·
  국내 ETF(ISA) 0.02%.
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
    LEV_MAX,
    Book,
    judge,
    lev_share_dd,
    lev_share_vol,
    mark,
    month_end_values,
    monthly_returns,
    new_month,
    open_book,
    realized_vol,
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
NBG = {EQUITY: 0.6, "IEF": 0.2, "GLDM": 0.2}
NBG_ISA = {"133690.KS": 0.6, "305080.KS": 0.2, "411060.KS": 0.2}
BOOKS: dict[str, dict[str, Any]] = {
    "MIX": {"targets": MIX, "capital": CAPITAL, "cost": COST, "ccy": "USD"},
    "BASE": {"targets": BASE, "capital": CAPITAL, "cost": COST, "ccy": "USD"},
    "NBG": {"targets": NBG, "capital": CAPITAL, "cost": COST, "ccy": "USD"},
    "NBG_ISA": {"targets": NBG_ISA, "capital": 1_000_000.0, "cost": 0.0002, "ccy": "KRW"},
}
LD = {"TQQQ": LEV_MAX, "SCHD": 1 - LEV_MAX}
for _name, _rule in (("LD_M0", "M0"), ("LD_M2", "M2"), ("LD_M3", "M3")):
    BOOKS[_name] = {
        "targets": LD,
        "capital": CAPITAL,
        "cost": COST,
        "ccy": "USD",
        "rule": _rule,
    }
SIGNAL = "QQQ"
FIRST_START = "2026-10-08"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36"


def _prices(span: str = "3mo") -> dict[str, dict[str, float]]:
    import httpx

    out: dict[str, dict[str, float]] = {}
    for sym in sorted({SIGNAL} | {sym for b in BOOKS.values() for sym in b["targets"]}):
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
        history=[tuple(x) for x in d["history"]],
    )


def _filled(
    px: dict[str, dict[str, float]], syms: list[str], close_hour_utc: int
) -> dict[str, dict[str, float]]:
    """장부 종목들의 날짜 줄(첫 종목 거래일). 빈 날은 직전 값, 아직 안 끝난 오늘 봉은 뺀다."""
    now = datetime.now(UTC)
    today = now.strftime("%Y-%m-%d")
    ok = {d for d in px[syms[0]] if d < today or now.hour >= close_hour_utc}
    out: dict[str, dict[str, float]] = {}
    last: dict[str, float] = {}
    for d in sorted(set().union(*(px[s] for s in syms))):
        if d >= today and now.hour < close_hour_utc:
            continue
        for s in syms:
            if d in px[s]:
                last[s] = px[s][d]
        if d in ok and all(s in last for s in syms):
            out[d] = {s: last[s] for s in syms}
    return out


def targets_for(
    cfg: dict[str, Any], book: Book | None, qqq: dict[str, float], day: str
) -> dict[str, float]:
    """182 장부: ``day`` 가 든 달 직전 달 말까지의 정보로 레버리지 몫을 정한다. 다른 장부는 고정."""
    rule = cfg.get("rule")
    if rule not in ("M2", "M3"):
        return dict(cfg["targets"])
    if rule == "M2":
        share = lev_share_dd(month_end_values(book.history if book else [], day))
    else:
        closes = [qqq[d] for d in sorted(qqq) if d[:7] < day[:7]]
        share = lev_share_vol(realized_vol(closes))
    return {"TQQQ": share, "SCHD": 1 - share}


def run() -> dict[str, Any]:
    px = _prices()
    ROOT.mkdir(parents=True, exist_ok=True)
    st = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {"books": {}}
    if "last_day" in st:  # 장부 하나 날짜 → 장부마다 날짜로 옮긴다(2026-10-09)
        for v in st["books"].values():
            v.setdefault("start", st["start"])
            v.setdefault("last_day", st["last_day"])
        st = {"books": st["books"]}
    rebal: list[Any] = []
    for name, cfg in BOOKS.items():
        syms = list(cfg["targets"])
        filled = _filled(px, syms, 7 if cfg["ccy"] == "KRW" else 21)
        if not filled:
            continue
        share = None
        if name in st["books"]:
            raw = st["books"][name]
            book, start, last_day = _book_from(raw), raw["start"], raw["last_day"]
            share = raw.get("lev_share")
        else:
            start = min((d for d in filled if d >= FIRST_START), default=max(filled))
            tg = targets_for(cfg, None, px[SIGNAL], start)
            book = open_book(cfg["capital"], tg, filled[start], cfg["cost"])
            if "rule" in cfg:
                share = tg["TQQQ"]
            mark(book, start, filled[start])
            last_day = start
        for d in [d for d in sorted(filled) if d > last_day]:
            if new_month(last_day, d):
                tg = targets_for(cfg, book, px[SIGNAL], d)
                fee = rebalance(book, tg, filled[d], cfg["cost"])
                rebal.append([d, name, round(fee, 4)])
                if "rule" in cfg:
                    share = tg["TQQQ"]
            mark(book, d, filled[d])
            last_day = d
        st["books"][name] = {
            **asdict(book),
            "start": start,
            "last_day": last_day,
            "ccy": cfg["ccy"],
            **({"lev_share": share} if "rule" in cfg else {}),
        }
    STATE.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
    report = _report(st, rebal)
    REPORTS.mkdir(parents=True, exist_ok=True)
    (REPORTS / "latest.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    return report


def _report(state: dict[str, Any], rebal: list[Any]) -> dict[str, Any]:
    out: dict[str, Any] = {"rebalanced_today": rebal}
    for name, b in state["books"].items():
        hist = b["history"]
        v0 = BOOKS[name]["capital"] if name in BOOKS else hist[0][1]
        v = hist[-1][1]
        out[name] = {
            "ccy": b.get("ccy", "USD"),
            "start": b.get("start"),
            "last_day": b.get("last_day"),
            "value": round(v, 2),
            "return": round(v / v0 - 1, 4),
            "mdd": round(b["mdd"], 4),
            "costs": round(b["costs"], 2),
        }
        if b.get("lev_share") is not None:
            out[name]["tqqq_share"] = round(b["lev_share"], 3)
    return out


def status() -> dict[str, Any]:
    if not STATE.exists():
        return {"state": "아직 시작 전"}
    st = json.loads(STATE.read_text(encoding="utf-8"))
    return _report(st, [])


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
    start = st["books"]["MIX"]["start"]

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
