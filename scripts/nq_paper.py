"""NQ Paper — 나스닥 100 상위 10 교체판을 가상 1,000 USD 로 매일 기록한다. 주문 없음.

규칙은 :mod:`auto_trading.ops.nq_paper`.
LT Paper 와 같은 시세 경로(한국투자증권 일봉, 끝난 날만)를 쓴다.

사용 예::

    python -m scripts.nq_paper init     # 처음 한 번: 나스닥 100 명단(위키백과 판 고정) · 일봉 받기
    python -m scripts.nq_paper update   # 최근 일봉 이어 붙이기 (읽기 전용)
    python -m scripts.nq_paper run      # 처리 안 한 거래일을 엔진으로, 보고서
    python -m scripts.nq_paper rebuild  # 상태를 지우고 START 부터 다시 계산 (시세를 고친 뒤)
    python -m scripts.nq_paper daily    # update + run (LT Paper daily 안에서 함께 돈다)
    python -m scripts.nq_paper status

자료 ``data/nq_paper/bars/<SYM>.csv`` · 명단 ``data/nq_paper/meta.json``
· 상태 ``data/nq_paper/state.json``,
보고 ``reports/nq_paper/<날짜>.json`` · ``latest.json``.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from scripts.lt_paper import UA, _read, fetch_merge

from auto_trading.ops.lt_paper import PaperState
from auto_trading.ops.nq_paper import TOP_N, next_rebalance, rebalance_days, step
from auto_trading.ops.us_daily_bars import last_settled_day

REPO = Path(__file__).resolve().parent.parent
ROOT = REPO / "data" / "nq_paper"
BARS = ROOT / "bars"
STATE = ROOT / "state.json"
META = ROOT / "meta.json"
REPORTS = REPO / "reports" / "nq_paper"
QQQ = REPO / "data" / "qs_paper" / "bars" / "QQQ.csv"
START = "2026-10-01"
"""10 월 첫 거래일. LT Paper(10/02 시작)와 나란히 비교하려고 그달 첫 판단일부터 계산한다."""
START_CASH = 1_000.0
HISTORY_SINCE = "2025-06-01"
"""점수(126 거래일) · 추세(120 거래일)에 필요한 만큼만."""
WIKI_API = "https://en.wikipedia.org/w/api.php"
WIKI_TITLE = "List_of_NASDAQ-100_companies"
SKIP = {"GOOG": "GOOGL 과 같은 회사(의결권 없는 C 주) — 한 회사 두 번 사지 않게 뺀다"}


def _constituents() -> dict[str, Any]:
    r = httpx.get(
        WIKI_API,
        params={
            "action": "query",
            "prop": "revisions",
            "titles": WIKI_TITLE,
            "rvprop": "ids|timestamp|content",
            "rvslots": "main",
            "format": "json",
        },
        headers={"User-Agent": UA},
        timeout=30,
    )
    r.raise_for_status()
    page = next(iter(r.json()["query"]["pages"].values()))
    rev = page["revisions"][0]
    text = rev["slots"]["main"]["*"]
    i = text.index('id="constituents"')
    table = text[i : text.index("|}", i)]
    rows: dict[str, dict[str, str]] = {}
    for block in table.split("|-")[1:]:
        cells = [c.strip() for c in re.split(r"\|\|", block.strip().lstrip("|"))]
        if len(cells) < 4 or not re.fullmatch(r"[A-Z.]{1,6}", cells[0]):
            continue
        name = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]*)\]\]", r"\1", cells[1])
        rows[cells[0]] = {"name": name, "industry": cells[2], "subsector": cells[3]}
    return {"revid": rev["revid"], "timestamp": rev["timestamp"], "symbols": rows}


def init() -> dict[str, object]:
    if META.exists():
        raise SystemExit(f"이미 시작했다: {META}")
    BARS.mkdir(parents=True, exist_ok=True)
    src = _constituents()
    symbols = {s: v for s, v in src["symbols"].items() if s not in SKIP}
    META.write_text(
        json.dumps(
            {
                "source": f"en.wikipedia.org {WIKI_TITLE} 판 {src['revid']} ({src['timestamp']})",
                "skipped": SKIP,
                "symbols": symbols,
                "started": datetime.now(UTC).isoformat(timespec="seconds"),
            },
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    STATE.write_text(json.dumps(PaperState(cash=START_CASH).to_json(), indent=1), encoding="utf-8")
    return {"symbols": len(symbols), "revid": src["revid"], **update()}


def update() -> dict[str, object]:
    from auto_trading.config.secrets import load_kis_credentials
    from auto_trading.exchange.kis import KisOverseasReadOnly, KisTokenCache

    meta = json.loads(META.read_text(encoding="utf-8"))
    broker = KisOverseasReadOnly(
        load_kis_credentials(REPO / ".env"), KisTokenCache(REPO / "data" / "kis_token.json")
    )
    last_ok = last_settled_day()
    failed: list[str] = []
    changed = 0
    try:
        for sym in sorted(meta["symbols"]):
            path = BARS / f"{sym}.csv"
            if not path.exists():
                path.write_text("date,open,high,low,close,volume\n", encoding="utf-8")
            try:
                m = fetch_merge(
                    broker,
                    sym.replace(".", "/"),
                    "NAS",
                    path,
                    last_ok,
                    since_if_empty=HISTORY_SINCE,
                )
            except Exception:
                failed.append(sym)
                continue
            changed += bool(m.appended or m.repaired or m.dropped_unsettled or m.needs_full)
    finally:
        broker.close()
    return {"settled_through": last_ok, "changed_symbols": changed, "failed": failed}


def _panel() -> tuple[list[str], dict[str, list[float | None]]]:
    meta = json.loads(META.read_text(encoding="utf-8"))
    series = {
        s: {r["date"]: float(r["close"]) for r in _read(BARS / f"{s}.csv")}
        for s in meta["symbols"]
        if (BARS / f"{s}.csv").exists()
    }
    qqq_days = [r["date"] for r in _read(QQQ)]
    days = [d for d in qqq_days if d >= HISTORY_SINCE]
    return days, {s: [m.get(d) for d in days] for s, m in series.items() if m}


def run(*, fresh: bool = False) -> dict[str, object]:
    state = (
        PaperState(cash=START_CASH)
        if fresh
        else PaperState.from_json(json.loads(STATE.read_text(encoding="utf-8")))
    )
    days, closes = _panel()
    rebal = rebalance_days(days)
    after = state.last_date or (
        max(d for d in days if d < START) if any(d < START for d in days) else ""
    )
    todo = [t for t, d in enumerate(days) if d > after and t >= 1]
    REPORTS.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {}
    for t in todo:
        actions = step(state, days, closes, t, t in rebal)
        report = _report(state, actions, days, closes, t, rebal)
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
    rebal: set[int],
) -> dict[str, Any]:
    def px(sym: str) -> float:
        xs, i = closes[sym], t
        while xs[i] is None:
            i -= 1
        v = xs[i]
        assert v is not None
        return v

    qqq = {r["date"]: float(r["close"]) for r in _read(QQQ)}
    start_day = state.equity[0][0] if state.equity else days[t]
    base = qqq.get(days[days.index(start_day) - 1]) if start_day in days else None
    positions = [
        {
            "symbol": s,
            "entry": p.entry,
            "cost_usd": round(p.cost, 2),
            "value_usd": round(p.qty * px(s), 2),
            "return": round(p.qty * px(s) / p.cost - 1, 4),
        }
        for s, p in sorted(state.positions.items())
    ]
    return {
        "date": days[t],
        "equity_usd": actions["equity"],
        "start_usd": START_CASH,
        "return_since_start": round(actions["equity"] / START_CASH - 1, 4),
        "qqq_since_start": round(qqq[days[t]] / base - 1, 4) if base and days[t] in qqq else None,
        "cash_usd": round(state.cash, 2),
        "positions": positions,
        "today": {
            "rebalance": actions["rebalance"],
            "sold": actions["sold"],
            "bought": actions["bought"],
        },
        "pending_sell_next_session": actions["pending_sell"],
        "top_as_of": actions["ranking_as_of"],
        "top": [{"symbol": s, "score": sc} for s, sc in actions["top"]],
        "next_rebalance": next_rebalance(days, t),
        "rules": f"나스닥 100 상위 {TOP_N} · 판단일(그달 첫 거래일 · 14 일 뒤) 교체 · 각 10% · "
        "T/v2 추세(20·60·120 부호 합 ≤ 0) 꺼지면 다음 날 매도",
        "note": "가상 계좌, 주문 없음. 현재 나스닥 100 명단을 쓴다"
        "(생존 편향 — 앞으로의 기록만 판단 근거).",
    }


def status() -> dict[str, object]:
    latest = REPORTS / "latest.json"
    return (
        json.loads(latest.read_text(encoding="utf-8"))
        if latest.exists()
        else {"note": "아직 실행 전"}
    )


def main(argv: list[str]) -> int:
    commands = {"init": init, "update": update, "run": run, "rebuild": rebuild, "status": status}
    if argv == ["daily"]:
        result: dict[str, object] = {"update": update(), "run": run()}
    elif len(argv) == 1 and argv[0] in commands:
        result = commands[argv[0]]()
    else:
        raise SystemExit(
            "usage: python -m scripts.nq_paper init | update | run | rebuild | daily | status"
        )
    sys.stdout.write(json.dumps(result, ensure_ascii=True, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
