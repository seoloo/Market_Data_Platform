"""문서 241 — 실적 발표 갭 따라가기 Paper (240 규칙 그대로, 돈 · 주문 없음).

사용 예::

    python -m scripts.pead_paper run      # SEC 공시 갱신 → 처음부터 다시 계산 → 보고
    python -m scripts.pead_paper status   # 마지막 보고 (네트워크 없음)

* 종목 · 일봉: ``data/lt_paper/bars``(LT Paper 가 매일 갱신). SPY 도 같은 곳.
* 발표: SEC submissions 8-K Item 2.02 접수 시각(``.env`` ``SEC_CONTACT_EMAIL`` 연락처).
* 가상 10,000 USD · 신호마다 500 USD · 동시 최대 20 자리.
* R + 1 시가 매수 · R + 20 종가 매도 · 편도 0.15%.
* Paper 자체는 텔레그램 없음. (예전에는 새 신호를 실거래 버튼 알림으로 넘겼다 — 실주문 코드를
  걷어 낼 때 그 부분만 뺐다, README 6-3. Paper 계산은 그대로.)
"""

from __future__ import annotations

import csv
import json
import statistics
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

REPO = Path(__file__).resolve().parent.parent
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

from auto_trading.config.secrets import read_env_file  # noqa: E402
from auto_trading.research.pead_240 import dedupe, is_signal, reaction_index  # noqa: E402

BARS = REPO / "data" / "lt_paper" / "bars"
ROOT = REPO / "data" / "pead_paper"
REPORTS = REPO / "reports" / "pead_paper"
START = "2026-10-16"
CAPITAL = 10_000.0
SLOT = 500.0
MAX_SLOTS = 20
HOLD = 20
FEE = 0.0015


def _hdr() -> dict[str, str]:
    email = read_env_file(REPO / ".env").get("SEC_CONTACT_EMAIL", "").strip()
    if not email:
        raise SystemExit("SEC_CONTACT_EMAIL 없음")
    return {"User-Agent": f"AutoTrading PEAD paper {email}", "Accept-Encoding": "gzip, deflate"}


def refresh_sec(tickers: list[str]) -> dict[str, list[str]]:
    """종목 → 8-K 2.02 접수 시각 (최근 공시 묶음만 다시 받아 합친다)."""
    ROOT.mkdir(parents=True, exist_ok=True)
    path = ROOT / "earnings.json"
    have: dict[str, list[str]] = (
        json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    )
    errors = 0
    with httpx.Client(timeout=30.0, headers=_hdr()) as c:
        cmap = c.get("https://www.sec.gov/files/company_tickers.json").json()
        cik = {v["ticker"].upper(): int(v["cik_str"]) for v in cmap.values()}
        for t in tickers:
            key = t.replace(".", "-").upper()
            if key not in cik:
                continue
            time.sleep(0.12)
            try:
                r = c.get(f"https://data.sec.gov/submissions/CIK{cik[key]:010d}.json")
                rec = r.json()["filings"]["recent"]
            except (httpx.HTTPError, ValueError, KeyError):
                errors += 1
                continue
            acc = [
                a
                for f, it, a in zip(
                    rec["form"], rec["items"], rec["acceptanceDateTime"], strict=False
                )
                if f == "8-K" and "2.02" in (it or "") and a[:10] >= "2026-09-01"
            ]
            have[t] = sorted(set(have.get(t, [])) | set(acc))
    path.write_text(json.dumps(have), encoding="utf-8")
    if errors:
        print(f"SEC 실패 {errors} 종목", flush=True)
    return have


def _bars(p: Path) -> tuple[list[str], list[float], list[float], list[float]]:
    with p.open(encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if r.get("close")]
    return (
        [r["date"][:10] for r in rows],
        [float(r["open"]) for r in rows],
        [float(r["close"]) for r in rows],
        [float(r["volume"]) for r in rows],
    )


def signals(earn: dict[str, list[str]]) -> list[dict[str, Any]]:
    """반응일 ≥ START 인 갭 상승 신호 (일봉이 R 까지 있어야)."""
    out = []
    for p in sorted(BARS.glob("*.csv")):
        if p.stem == "SPY" or not earn.get(p.stem):
            continue
        days, opn, close, vol = _bars(p)
        r_idx = dedupe([i for a in earn[p.stem] if (i := reaction_index(days, a)) is not None])
        for i in r_idx:
            if days[i] < START or is_signal(close, vol, i) != 1:
                continue
            out.append(
                {
                    "sym": p.stem,
                    "r_day": days[i],
                    "gap": close[i] / close[i - 1] - 1,
                    "r_close": close[i],
                    "entry_day": days[i + 1] if i + 1 < len(days) else None,
                    "entry": opn[i + 1] if i + 1 < len(days) else None,
                    "exit_day": days[i + HOLD] if i + HOLD < len(days) else None,
                    "exit": close[i + HOLD] if i + HOLD < len(days) else None,
                }
            )
    return out


def simulate(sigs: list[dict[str, Any]], spy: dict[str, tuple[float, float]]) -> dict[str, Any]:
    """가상 계좌: 진입일 순(같은 날은 갭 큰 순), 자리 · 현금이 있으면 산다."""
    cash = CAPITAL
    open_pos: list[dict[str, Any]] = []
    closed: list[dict[str, Any]] = []
    skipped = []
    for s in sorted((s for s in sigs if s["entry_day"]), key=lambda s: (s["entry_day"], -s["gap"])):
        for p in [p for p in open_pos if p["exit_day"] and p["exit_day"] < s["entry_day"]]:
            cash += p["proceeds"]
            open_pos.remove(p)
            closed.append(p)
        if len(open_pos) >= MAX_SLOTS or cash < SLOT:
            skipped.append({"sym": s["sym"], "entry_day": s["entry_day"]})
            continue
        cash -= SLOT
        pos = {**s, "cost": SLOT}
        if s["exit"] is not None:
            gross = SLOT * (1 - FEE) * s["exit"] / s["entry"] * (1 - FEE)
            sp0, sp1 = spy.get(s["entry_day"]), spy.get(s["exit_day"])
            pos["proceeds"] = gross
            pos["ret"] = gross / SLOT - 1
            pos["excess"] = pos["ret"] - (sp1[1] / sp0[0] - 1) if sp0 and sp1 else None
        open_pos.append(pos)
    for p in [p for p in open_pos if p.get("proceeds") is not None]:
        cash += p["proceeds"]
        open_pos.remove(p)
        closed.append(p)
    ex = [p["excess"] for p in closed if p.get("excess") is not None]
    t = None
    if len(ex) >= 2 and statistics.stdev(ex) > 0:
        t = statistics.fmean(ex) / statistics.stdev(ex) * len(ex) ** 0.5
    return {
        "cash": cash,
        "open": [
            {k: p[k] for k in ("sym", "r_day", "gap", "entry_day", "entry")} for p in open_pos
        ],
        "closed_n": len(closed),
        "closed": [
            {k: p.get(k) for k in ("sym", "r_day", "entry_day", "exit_day", "ret", "excess")}
            for p in closed
        ],
        "mean_excess": statistics.fmean(ex) if ex else None,
        "t": t,
        "win": sum(x > 0 for x in ex) / len(ex) if ex else None,
        "skipped_no_slot": len(skipped),
        "open_cost": sum(p["cost"] for p in open_pos),
    }


def run() -> dict[str, Any]:
    tickers = sorted(p.stem for p in BARS.glob("*.csv") if p.stem != "SPY")
    earn = refresh_sec(tickers)
    d, o, c, _ = _bars(BARS / "SPY.csv")
    spy = {day: (oo, cc) for day, oo, cc in zip(d, o, c, strict=True)}
    sigs = signals(earn)
    rep = {
        "made_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "start": START,
        "last_bar": d[-1],
        "signals": len(sigs),
        **simulate(sigs, spy),
    }
    REPORTS.mkdir(parents=True, exist_ok=True)
    (REPORTS / "latest.json").write_text(
        json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    return rep


def status() -> dict[str, Any]:
    path = REPORTS / "latest.json"
    return (
        json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"state": "아직 시작 전"}
    )


def main(argv: list[str]) -> int:
    fn = {"run": run, "status": status}.get(argv[0] if argv else "")
    if fn is None:
        raise SystemExit("usage: python -m scripts.pead_paper run|status")
    rep = fn()
    short = {k: v for k, v in rep.items() if k not in ("closed",)}
    sys.stdout.write(json.dumps(short, ensure_ascii=False, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            _reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main(sys.argv[1:]))
