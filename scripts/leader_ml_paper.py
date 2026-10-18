"""문서 277 — 미장 주도주 GBM Paper (274 고르기 · 275/276 6 달 보유, 1 주 단위). 주문 · 알림 없음.

사용 예 (numpy · scikit-learn 이 있는 연구 venv)::

    build/tcn-venv/Scripts/python.exe -m scripts.leader_ml_paper run      # 예약 작업
    build/tcn-venv/Scripts/python.exe -m scripts.leader_ml_paper status

* 일봉 ``data/lt_paper/bars`` (LT Paper 가 매일 갱신, 읽기만).
* 상태 ``data/leader_ml_paper/state.json``, 보고 ``reports/leader_ml_paper/latest.json``.
* 장부: GBM10(원래 10 종목 × 6 묶음) · GBM5(276 R5x6) · BASE10(113) · SPY.
"""

from __future__ import annotations

import json
import math
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
for _p in (REPO / "src", REPO):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
_SITE = REPO / ".venv" / "Lib" / "site-packages"  # httpx · yaml 등 (numpy 는 연구 venv 것이 먼저)
if _SITE.exists() and str(_SITE) not in sys.path:
    sys.path.append(str(_SITE))

BARS = REPO / "data" / "lt_paper" / "bars"
ROOT = REPO / "data" / "leader_ml_paper"
SEC = ROOT / "sec"
STATE = ROOT / "state.json"
REPORTS = REPO / "reports" / "leader_ml_paper"
SECTORS = REPO / "data" / "research" / "stocks" / "sectors.json"
START = "2026-10-01"
"""이 날 이후의 월말부터 결정 (첫 결정 = 2026-10 월말)."""
KRW_PER_USD = 1400.0
BOOKS: dict[str, tuple[str, int, float, bool]] = {
    "GBM10": ("GBM", 10, 80_000_000 / KRW_PER_USD, False),
    "GBM5": ("GBM", 5, 50_000_000 / KRW_PER_USD, False),
    "BASE10": ("BASE", 10, 80_000_000 / KRW_PER_USD, False),
    # 소수점 장부 (사용자 2026-10-18 "gbm10와 gbm5를 소수점으로 매수하는 것도 한번 확인") —
    # 토스 소수점은 금액 시장가만 · 정규장(마감 1 시간 전까지) → 체결일 시가로 사고 판다.
    "GBM10F": ("GBM", 10, 10_000_000 / KRW_PER_USD, True),
    "GBM5F": ("GBM", 5, 10_000_000 / KRW_PER_USD, True),
}
"""장부 → (고르기, 종목 수, 가상 자본 $, 소수점)."""
SPY_CAPITAL = 80_000_000 / KRW_PER_USD
FEE = 0.0025
HOLD = 126
SLOTS = 6

Picks = dict[str, list[str]]


# ------------------------------------------------------------------ 순수 장부


def new_state(start: str = START) -> dict[str, Any]:
    return {
        "start": start,
        "last_day": None,
        "decisions": {},
        "books": {
            name: {"cash": cap, "lots": [], "closed": [], "skipped": [], "equity": {}}
            for name, (_src, _n, cap, _f) in BOOKS.items()
        },
        "spy": {"cash": SPY_CAPITAL, "qty": 0, "equity": {}},
    }


def ensure_books(state: dict[str, Any]) -> None:
    """나중에 더한 장부를 기존 상태에 넣는다(첫 결정 전에만 — 결정 뒤면 다른 시작이 됨)."""
    for name, (_src, _n, cap, _f) in BOOKS.items():
        if name not in state["books"]:
            if state["decisions"]:
                raise SystemExit(f"{name}: 결정이 시작된 뒤에는 장부를 더하지 않는다 — 새 등록으로")
            state["books"][name] = {
                "cash": cap,
                "lots": [],
                "closed": [],
                "skipped": [],
                "equity": {},
            }


def _px(xs: Sequence[float | None], i: int) -> float | None:
    while i >= 0 and xs[i] is None:
        i -= 1
    return xs[i] if i >= 0 else None


def _value(book: Mapping[str, Any], close: Mapping[str, Sequence[float | None]], i: int) -> float:
    held = sum(lot["qty"] * (_px(close[lot["sym"]], i) or 0.0) for lot in book["lots"])
    return float(book["cash"]) + float(held)


def buy(book: dict[str, Any], syms: Sequence[str], prices: Mapping[str, float | None], day: str,
        equity: float, fractional: bool = False) -> None:  # fmt: skip
    """한 묶음: min(현금, 평가액 ÷ 6) 을 종목마다 같게. 1 주 단위 내림(수수료 포함) 또는 소수점."""
    if not syms:
        return
    budget = min(book["cash"], equity / SLOTS)
    target = budget / len(syms)
    for s in syms:
        p = prices.get(s)
        if not p:
            qty: float = 0
        elif fractional:
            qty = round(target / (p * (1 + FEE)), 6)
        else:
            qty = math.floor(target / (p * (1 + FEE)))
        if qty <= 0:
            book["skipped"].append({"sym": s, "day": day, "price": p})
            continue
        book["cash"] -= qty * p * (1 + FEE)  # type: ignore[operator]
        book["lots"].append({"sym": s, "qty": qty, "buy_day": day, "buy_px": p})


def process(
    state: dict[str, Any],
    days: Sequence[str],
    close: Mapping[str, Sequence[float | None]],
    spy: Sequence[float],
    decide: Callable[[int], Picks],
    opens: Mapping[str, Sequence[float | None]] | None = None,
) -> list[str]:
    """처리 안 한 거래일을 차례로.

    팔기(126 거래일) → 새 달 첫날이면 직전 월말 결정 · 사기 → 평가.
    1 주 단위 장부는 그날 종가, 소수점 장부는 그날 시가(``opens`` 없으면 종가)."""
    op = opens if opens is not None else close
    idx = {d: i for i, d in enumerate(days)}
    last = state["last_day"]
    first = (
        idx[last] + 1 if last in idx else next(i for i, d in enumerate(days) if d >= state["start"])
    )
    log: list[str] = []
    for i in range(first, len(days)):
        d = days[i]
        for name, book in state["books"].items():
            src_px = op if BOOKS[name][3] else close
            keep = []
            for lot in book["lots"]:
                if i - idx[lot["buy_day"]] >= HOLD:
                    px = _px(src_px[lot["sym"]], i) or _px(close[lot["sym"]], i) or 0.0
                    book["cash"] += lot["qty"] * px * (1 - FEE)
                    cost = lot["qty"] * lot["buy_px"] * (1 + FEE)
                    book["closed"].append(
                        {"sym": lot["sym"], "buy_day": lot["buy_day"], "sell_day": d,
                         "ret": lot["qty"] * px * (1 - FEE) / cost - 1}
                    )  # fmt: skip
                else:
                    keep.append(lot)
            book["lots"] = keep
        prev = days[i - 1] if i > 0 else None
        if prev and prev >= state["start"] and prev[:7] != d[:7] and prev not in state["decisions"]:
            picks = decide(i - 1)
            state["decisions"][prev] = {"fill_day": d, **picks}
            names = [s for k in ("GBM", "BASE") for s in picks.get(k, []) if s in close]
            at_close = {s: close[s][i] for s in names}
            at_open = {s: op[s][i] for s in names}
            for name, book in state["books"].items():
                src, n, _cap, frac = BOOKS[name]
                prices = at_open if frac else at_close
                buy(book, picks.get(src, [])[:n], prices, d, _value(book, close, i), frac)
            sp = state["spy"]
            if sp["qty"] == 0 and spy[i] > 0:
                q = math.floor(sp["cash"] / (spy[i] * (1 + FEE)))
                sp["qty"], sp["cash"] = q, sp["cash"] - q * spy[i] * (1 + FEE)
            log.append(f"{prev} 결정 → {d} 종가 체결: GBM {picks.get('GBM')}")
        for book in state["books"].values():
            book["equity"][d] = round(_value(book, close, i), 2)
        sp = state["spy"]
        sp["equity"][d] = round(sp["cash"] + sp["qty"] * spy[i], 2)
        state["last_day"] = d
    return log


def summary(state: Mapping[str, Any], close: Mapping[str, Sequence[float | None]],
            days: Sequence[str]) -> dict[str, Any]:  # fmt: skip
    out: dict[str, Any] = {"last_day": state["last_day"], "decisions": len(state["decisions"])}
    books = {**state["books"], "SPY": state["spy"]}
    for name, b in books.items():
        eq = list(b["equity"].values())
        cap = BOOKS[name][2] if name in BOOKS else SPY_CAPITAL
        peak, mdd = cap, 0.0
        for v in eq:
            peak = max(peak, v)
            mdd = max(mdd, 1 - v / peak)
        row: dict[str, Any] = {
            "equity": eq[-1] if eq else cap,
            "return": (eq[-1] / cap - 1) if eq else 0.0,
            "mdd": mdd,
        }
        if name in BOOKS:
            i = len(days) - 1
            row["open"] = [
                {"sym": lot["sym"], "qty": lot["qty"], "buy_day": lot["buy_day"],
                 "ret": (_px(close[lot["sym"]], i) or 0) / lot["buy_px"] - 1}
                for lot in b["lots"]
            ]  # fmt: skip
            closed = [c["ret"] for c in b["closed"]]
            row["closed_n"] = len(closed)
            row["closed_mean"] = sum(closed) / len(closed) if closed else None
            row["skipped"] = len(b["skipped"])
        out[name] = row
    if state["decisions"]:
        k = max(state["decisions"])
        out["last_decision"] = {"day": k, **state["decisions"][k]}
    return out


# ------------------------------------------------------------------ 자료 · 모델 (예약 실행)


Series = dict[str, list[float | None]]


def load_panel() -> tuple[list[str], Series, Series, list[float], Series]:
    from scripts.run_sb_breakout import load

    spy_s = load(BARS / "SPY.csv")
    days = list(spy_s.dates)
    spy = list(spy_s.close)
    close: dict[str, list[float | None]] = {}
    dvol: dict[str, list[float | None]] = {}
    opens: dict[str, list[float | None]] = {}
    for p in sorted(BARS.glob("*.csv")):
        if p.stem == "SPY":
            continue
        s = load(p)
        c = dict(zip(s.dates, s.close, strict=True))
        v = {d: x * y for d, x, y in zip(s.dates, s.close, s.volume, strict=True)}
        o = dict(zip(s.dates, s.open, strict=True))
        close[p.stem] = [c.get(d) for d in days]
        opens[p.stem] = [o.get(d) for d in days]
        dvol[p.stem] = [v.get(d) for d in days]
    return days, close, dvol, spy, opens


def refresh_sec(symbols: Sequence[str], asof: str) -> dict[str, Any]:
    """결정일마다 한 번 SEC companyfacts 갱신(261 과 같은 추출). 실패한 종목은 직전 파일 유지.

    같은 날짜로 이미 받았으면 건너뛴다(달 단위로 하면 월 중 점검이 월말 결정의 갱신을 막는다)."""
    import httpx
    from scripts.fetch_sec_facts_261 import extract

    from auto_trading.config.secrets import read_env_file

    stamp = SEC / "_stamp.json"
    if stamp.exists() and json.loads(stamp.read_text(encoding="utf-8")).get("asof") == asof:
        return {"skipped": "already refreshed"}
    email = read_env_file(REPO / ".env").get("SEC_CONTACT_EMAIL", "").strip()
    if not email:
        return {"error": "SEC_CONTACT_EMAIL 없음 — 직전 파일 사용"}
    SEC.mkdir(parents=True, exist_ok=True)
    hdr = {"User-Agent": f"AutoTrading paper 277 {email}", "Accept-Encoding": "gzip, deflate"}
    failed: list[str] = []
    with httpx.Client(timeout=60.0, headers=hdr) as c:
        try:
            cmap = c.get("https://www.sec.gov/files/company_tickers.json").json()
        except (httpx.HTTPError, ValueError) as exc:
            return {"error": f"티커 목록 실패 {type(exc).__name__} — 직전 파일 사용"}
        cik = {v["ticker"].upper(): int(v["cik_str"]) for v in cmap.values()}
        for sym in symbols:
            key = sym.replace(".", "-").upper()
            if key not in cik:
                failed.append(sym)
                continue
            time.sleep(0.15)
            try:
                r = c.get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik[key]:010d}.json")
                r.raise_for_status()
                (SEC / f"{sym}.json").write_text(json.dumps(extract(r.json())), encoding="utf-8")
            except (httpx.HTTPError, ValueError) as exc:
                failed.append(f"{sym}:{type(exc).__name__}")
    stamp.write_text(json.dumps({"asof": asof, "failed": failed}), encoding="utf-8")
    return {"refreshed": len(symbols) - len(failed), "failed": failed}


def make_decider(
    days: Sequence[str],
    close: Mapping[str, Sequence[float | None]],
    dvol: Mapping[str, Sequence[float | None]],
) -> Callable[[int], Picks]:
    def decide(t: int) -> Picks:
        import numpy as np  # type: ignore[import-not-found]
        from scripts.run_leader_quality_261 import Fundamentals, pick_in

        from scripts import run_leader_ml_274 as m

        sec_log = refresh_sec(sorted(close), days[t])
        print(json.dumps({"sec": sec_log}, ensure_ascii=False), flush=True)
        sector = {
            k: str(v) for k, v in json.loads(SECTORS.read_text(encoding="utf-8"))["sectors"].items()
        }
        fund = Fundamentals(SEC)
        pdates, psets = m.pit_members()
        hist = m.rows_from(days, close, dvol, sector, fund, lambda d: m.member_at(pdates, psets, d))
        train = [r for r in hist if r["y"] is not None and r["t"] + m.HORIZON < t]
        now = m.rows_from(days, close, dvol, sector, fund, None, only=[t])
        model = m.gbm_model()
        model.fit(np.asarray([r["x"] for r in train]), np.asarray([r["y"] for r in train]))
        prob = model.predict_proba(np.asarray([r["x"] for r in now]))[:, 1]
        ranked = sorted(
            zip(prob.tolist(), [r["sym"] for r in now], strict=True), key=lambda x: (-x[0], x[1])
        )
        base = pick_in(close, sector, t, [r["sym"] for r in now])
        print(
            json.dumps({"decision": days[t], "train_rows": len(train), "pool": len(now)}),
            flush=True,
        )
        return {
            "GBM": [s for _p, s in ranked[:10]],
            "BASE": base,
            "GBM_prob": [round(p, 4) for p, _s in ranked[:10]],
        }

    return decide


def run() -> dict[str, Any]:
    days, close, dvol, spy, opens = load_panel()
    state = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else new_state()
    ensure_books(state)
    log = process(state, days, close, spy, make_decider(days, close, dvol), opens)
    ROOT.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    rep = summary(state, close, days)
    rep["log"] = log
    rep["generated_utc"] = datetime.now(UTC).isoformat()
    REPORTS.mkdir(parents=True, exist_ok=True)
    (REPORTS / "latest.json").write_text(
        json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    return rep


def text(rep: Mapping[str, Any]) -> str:
    lines = [f"주도주 GBM Paper — {rep.get('last_day')} 까지, 결정 {rep.get('decisions', 0)} 번"]
    for name in (*BOOKS, "SPY"):
        r = rep.get(name)
        if r:
            extra = ""
            if r.get("closed_n"):
                extra = f" · 끝난 {r['closed_n']} 종목 평균 {r['closed_mean'] * 100:+.1f}%"
            head = f"{name}: ${r['equity']:,.0f} ({r['return'] * 100:+.2f}%)"
            lines.append(f"{head} · 최대 낙폭 {r['mdd'] * 100:.1f}%{extra}")
    if rep.get("last_decision"):
        d = rep["last_decision"]
        lines.append(f"최근 결정 {d['day']} → {d['fill_day']} 체결: GBM {', '.join(d['GBM'])}")
    else:
        lines.append(
            "아직 결정 없음 — 첫 결정은 2026-10 마지막 거래일 종가 기준(다음 거래일 체결)."
        )
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    reconf = getattr(sys.stdout, "reconfigure", None)
    if reconf is not None:  # cp949 콘솔에서도 '—' 등을 찍을 수 있게
        reconf(encoding="utf-8", errors="replace")
    cmd = argv[1] if len(argv) > 1 else ""
    if cmd == "run":
        print(text(run()))
        return 0
    if cmd == "status":
        path = REPORTS / "latest.json"
        print(text(json.loads(path.read_text(encoding="utf-8"))) if path.exists() else "보고 없음")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
