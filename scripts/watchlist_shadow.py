"""관찰 바구니 그림자 기록 — 방향은 + 였지만 표본이 모자라 증명 못 한 규칙을 앞으로의 자료로 쌓는다.

사용 예::

    python -m scripts.watchlist_shadow run       # 매일 1 회 (권장 21:40 KST): 두 규칙 모두
    python -m scripts.watchlist_shadow status    # 지금까지 기록 요약 (네트워크 없음)

등록: docs/research/171-watchlist-shadow-registration.md. **돈 · 주문 없음.** 규칙은 원 판정 그대로.

* **W1 = 160 의 23 (ETF 순유입 LONG)**: Farside 표의 세션 순유입이 0 보다 크고 직전 60 세션 80%
  분위보다 크면, 그 세션 16:00 ET + 24 시간 → 다음 정시에 BTCUSDT 무기한 롱(지정가 IOC +0.3%,
  최대 3 번), 72 시간 보유, −10% 보호 스톱. 비용 편도 0.095%(참고 0.023%), funding 반영.
  시세는 Binance 공개 1 분봉 · funding(키 없음).
* **W2 = 154 의 PANIC-KQ**: 코스닥 거래대금 상위 200(직전 월말, 63 거래일 평균) 가운데 10% 이상이
  25 일선 −20% 아래인 날, 20 거래일 넘게 떨어진 첫날 → 다음 거래일 종가에 코스닥 지수 매수,
  20 거래일 뒤 종가 매도, 편도 0.1%. 자료는 KRX 일별(원시 종가)과 KIS 코스닥 지수.
  원 판정은 KIS 수정 종가였다 — 앞으로는 원시 종가라, 상장 주식 수가 창 안에서 10% 넘게 바뀐
  종목(분할 · 병합 의심)은 그날 세지 않는다(등록 §3).
* 앞으로의 자료는 ``data/watchlist/`` 에만 둔다(158 · 160 · 161 의 고정 자료 해시를 건드리지 않음).
"""

from __future__ import annotations

import argparse
import csv
import gzip
import io
import json
import math
import statistics
import sys
import time as _time
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from auto_trading.research.etf_flow import (  # noqa: E402
    HOUR_MS,
    Minutes,
    is_session,
    net_return,
    parse_farside,
    signal_23,
    simulate,
)

DATA = REPO_ROOT / "data" / "watchlist"
REPORTS = REPO_ROOT / "reports" / "watchlist"
STATE = DATA / "state.json"
ET = ZoneInfo("America/New_York")
KST = ZoneInfo("Asia/Seoul")
START = date(2026, 9, 1)  # 전향 확인 구간(DATA_EXPOSURE_LEDGER) 첫날
W1_LAG_H, W1_HOLD_H, W1_COST, W1_COST_REF = 24, 72, 0.00095, 0.00023
W2_DEV, W2_SHARE, W2_GAP, W2_HOLD, W2_FEE = 0.20, 0.10, 20, 20, 0.001
W2_POOL, W2_LIQ_DAYS, W2_MA, W2_SHARES_JUMP = 200, 63, 25, 0.10
KRX_SEED = REPO_ROOT / "data" / "research" / "krx" / "ksq"  # 2026-08-31 까지(읽기만)


# ---------------------------------------------------------------- 순수 함수


def w1_eval_ms(day: date) -> int:
    ms = int(datetime.combine(day, time(16, 0), ET).timestamp() * 1000) + W1_LAG_H * HOUR_MS
    return -(-ms // HOUR_MS) * HOUR_MS


def w1_signals(rows: Sequence[Any], since: date) -> list[dict[str, Any]]:
    """세션 행에서 since 이후 23 신호 (순수, 원 판정의 signal_23 그대로)."""
    sess = [r for r in rows if is_session(r)]
    totals = [r.total for r in sess]
    out = []
    for i, r in enumerate(sess):
        if r.day < since:
            continue
        if signal_23(totals, i):
            out.append({"day": r.day.isoformat(), "eval_ms": w1_eval_ms(r.day), "flow": r.total})
    return out


def panic_share(
    closes: Mapping[str, Sequence[float | None]],
    shares: Mapping[str, Sequence[float | None]],
    pool: Sequence[str],
    t: int,
) -> tuple[int, int]:
    """칸 t 에서 (25 일선 −20% 아래 종목 수, 센 종목 수) (순수). 창 안 상장 주식 수가 10% 넘게
    바뀐 종목은 원시 종가의 분할 · 병합 왜곡을 피하려고 세지 않는다."""
    below = n = 0
    for s in pool:
        w = closes[s][t - W2_MA + 1 : t + 1]
        sh = [x for x in shares[s][t - W2_MA + 1 : t + 1] if x]
        if t < W2_MA - 1 or len(w) < W2_MA or any(x is None for x in w) or len(sh) < W2_MA:
            continue
        if max(sh) / min(sh) - 1 > W2_SHARES_JUMP:
            continue
        n += 1
        vals = [float(x) for x in w if x is not None]
        if vals[-1] <= (1 - W2_DEV) * sum(vals) / W2_MA:
            below += 1
    return below, n


def first_days(hits: Sequence[int], gap: int = W2_GAP) -> list[int]:
    """공포 날 칸 목록 → gap 거래일 넘게 떨어진 첫날만 (순수, 원 판정과 같은 규칙)."""
    out, last = [], -(10**9)
    for t in hits:
        if t - last > gap:
            out.append(t)
        last = t
    return out


def pooled_lower(hist_mean: float, hist_sd: float, hist_n: int, fwd: Sequence[float]) -> float:
    """원 판정 표본과 앞으로의 표본을 합친 평균의 2.24 SE 하한 (순수, 등록 §4 판단 규칙)."""
    n = hist_n + len(fwd)
    if n < 2:
        return -math.inf
    ss_hist = (hist_n - 1) * hist_sd**2 + hist_n * hist_mean**2
    tot = hist_n * hist_mean + sum(fwd)
    mean = tot / n
    var = (ss_hist + sum(x * x for x in fwd) - n * mean**2) / (n - 1)
    return mean - 2.24 * math.sqrt(max(var, 0.0) / n)


# ---------------------------------------------------------------- 자료 · 상태


def _load_state() -> dict[str, Any]:
    if STATE.exists():
        state: dict[str, Any] = json.loads(STATE.read_text(encoding="utf-8"))
        return state
    return {"w1": {"pending": [], "done": []}, "w2": {"pending": [], "done": [], "hits": []}}


def _save_state(state: Mapping[str, Any]) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


def _minutes(public: Any, start_ms: int, end_ms: int) -> Minutes:
    t: list[int] = []
    o: list[float] = []
    h: list[float] = []
    lo: list[float] = []
    c: list[float] = []
    cur = start_ms
    while cur < end_ms:
        rows = public._get(
            "/fapi/v1/klines",
            {"symbol": "BTCUSDT", "interval": "1m", "startTime": cur, "limit": 1500},
        )
        if not rows:
            break
        for r in rows:
            ms = int(r[0])
            if ms >= end_ms or (t and ms <= t[-1]):
                continue
            t.append(ms)
            o.append(float(r[1]))
            h.append(float(r[2]))
            lo.append(float(r[3]))
            c.append(float(r[4]))
        cur = int(rows[-1][0]) + 60_000
        _time.sleep(0.2)
    return Minutes(t, o, h, lo, c)


def run_w1(state: dict[str, Any], now_ms: int) -> dict[str, Any]:
    from scripts.fetch_etf_flow import FARSIDE_ALL, _get

    from auto_trading.exchange.binance_usdm_public import BinanceUsdmPublic

    st, body, _ = _get(FARSIDE_ALL, referer="https://farside.co.uk/btc/")
    if st != 200:
        return {"error": f"Farside {st}"}
    (DATA / "etf").mkdir(parents=True, exist_ok=True)
    (DATA / "etf" / f"farside-{datetime.now(UTC).strftime('%Y%m%d')}.html").write_bytes(body)
    rows = parse_farside(body.decode("utf-8", errors="replace"))
    known = {s["day"] for s in state["w1"]["pending"]} | {s["day"] for s in state["w1"]["done"]}
    new = [s for s in w1_signals(rows, START) if s["day"] not in known]
    state["w1"]["pending"].extend(new)
    public = BinanceUsdmPublic()
    closed = []
    try:
        for s in list(state["w1"]["pending"]):
            end = s["eval_ms"] + (W1_HOLD_H + 6) * HOUR_MS
            if end > now_ms:
                continue  # 아직 보유 기간이 안 끝났다
            m = _minutes(public, s["eval_ms"] - 60_000, end + 24 * HOUR_MS)
            k0 = next((k for k, ms in enumerate(m.t) if ms == s["eval_ms"]), None)
            tr = simulate(m, k0, 1, W1_HOLD_H * HOUR_MS) if k0 is not None else None
            if tr is None:
                rec = {**s, "status": "NO_FILL_OR_DATA"}
            else:
                fund = public.funding("BTCUSDT", tr.fill_ms, tr.exit_ms)
                net, gross, f = net_return(tr, fund, W1_COST)
                rec = {
                    **s,
                    "status": "CLOSED",
                    "entry": tr.entry,
                    "exit": tr.exit,
                    "stopped": tr.stopped,
                    "net": net,
                    "gross": gross,
                    "funding": f,
                    "net_ref": net + 2 * (W1_COST - W1_COST_REF),
                }
            state["w1"]["pending"].remove(s)
            state["w1"]["done"].append(rec)
            closed.append(rec)
    finally:
        public.close()
    return {"new_signals": new, "closed": closed}


def _krx_day(day: str, key: str) -> list[dict[str, str]] | None:
    import httpx
    from scripts.fetch_krx import API, MARKETS

    folder = DATA / "krx_ksq"
    path = folder / f"{day}.csv.gz"
    if path.exists():
        return list(csv.DictReader(io.StringIO(gzip.decompress(path.read_bytes()).decode())))
    if (folder / f"{day}.empty").exists():
        return None
    with httpx.Client(timeout=60, headers={"AUTH_KEY": key}) as client:
        resp = client.get(f"{API}/{MARKETS['ksq']}", params={"basDd": day})
        resp.raise_for_status()
        rows = resp.json().get("OutBlock_1") or []
    folder.mkdir(parents=True, exist_ok=True)
    if not rows:
        (folder / f"{day}.empty").write_text("", encoding="utf-8")
        return None
    from scripts.fetch_krx import to_csv

    path.write_bytes(gzip.compress(to_csv(rows)))
    return [{k: str(v) for k, v in r.items()} for r in rows]


def _seed_rows(day: str) -> list[dict[str, str]] | None:
    p = KRX_SEED / f"{day}.csv.gz"
    if not p.exists():
        return None
    return list(csv.DictReader(io.StringIO(gzip.decompress(p.read_bytes()).decode())))


def run_w2(state: dict[str, Any], today: date) -> dict[str, Any]:
    from scripts.fetch_kq_stocks import _index_pages
    from scripts.fetch_kr_stocks import _kis
    from scripts.fetch_krx import _key

    key = _key()
    days: list[str] = []
    table: dict[str, dict[str, tuple[float, float, float]]] = {}
    d = START - timedelta(days=140)
    while d < today:
        if d.weekday() < 5:
            ds = d.strftime("%Y%m%d")
            rows = _seed_rows(ds) if d < START else _krx_day(ds, key)
            if rows:
                days.append(ds)
                table[ds] = {}
                for r in rows:
                    try:
                        c, v, sh = (float(r[k]) for k in ("TDD_CLSPRC", "ACC_TRDVAL", "LIST_SHRS"))
                    except (KeyError, ValueError):
                        continue
                    if c > 0:
                        table[ds][r["ISU_CD"]] = (c, v, sh)
        d += timedelta(days=1)
    codes = sorted({c for v in table.values() for c in v})
    closes = {s: [table[x].get(s, (None, 0, None))[0] for x in days] for s in codes}
    vals = {s: [table[x].get(s, (0, 0.0, 0))[1] for x in days] for s in codes}
    shares = {s: [table[x].get(s, (0, 0, None))[2] for x in days] for s in codes}
    hits: list[int] = []
    shares_log: dict[str, float] = {}
    for t, ds in enumerate(days):
        if ds < START.strftime("%Y%m%d"):
            continue
        prev_month_end = max((i for i, x in enumerate(days) if x[:6] < ds[:6]), default=None)
        if prev_month_end is None or prev_month_end < W2_LIQ_DAYS:
            continue
        lo = prev_month_end - W2_LIQ_DAYS + 1
        ranked = sorted(
            codes,
            key=lambda s: (-sum(vals[s][lo : prev_month_end + 1]), s),
        )[:W2_POOL]
        below, n = panic_share(closes, shares, ranked, t)
        shares_log[ds] = round(below / n, 4) if n else 0.0
        if n and below / n >= W2_SHARE:
            hits.append(t)
    episodes = [days[t] for t in first_days(hits)]
    known = {e["day"] for e in state["w2"]["pending"]} | {e["day"] for e in state["w2"]["done"]}
    new = [{"day": e} for e in episodes if e not in known]
    state["w2"]["pending"].extend(new)
    closed = []
    if state["w2"]["pending"]:
        kis = _kis()
        try:
            idx = dict(_index_pages(kis._get, START.strftime("%Y%m%d"), today.strftime("%Y%m%d")))
        finally:
            kis.close()
        idays = sorted(idx)
        for e in list(state["w2"]["pending"]):
            after = [x for x in idays if x > e["day"]]
            if len(after) < 1 + W2_HOLD:
                continue
            buy, sell = after[0], after[W2_HOLD]
            net = idx[sell] / idx[buy] - 1 - 2 * W2_FEE
            rec = {**e, "buy": buy, "sell": sell, "net": net, "status": "CLOSED"}
            state["w2"]["pending"].remove(e)
            state["w2"]["done"].append(rec)
            closed.append(rec)
    return {"days": len(days), "last_share": shares_log, "new_episodes": new, "closed": closed}


def summary(state: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for w in ("w1", "w2"):
        nets = [r["net"] for r in state[w]["done"] if r.get("status") == "CLOSED"]
        h = HIST[w]
        out[w] = {
            "closed": len(nets),
            "pending": len(state[w]["pending"]),
            "forward_mean": round(statistics.fmean(nets), 5) if nets else None,
            "pooled_lower_2.24se": round(pooled_lower(h["mean"], h["sd"], int(h["n"]), nets), 5),
            "decision_at": h["decision_n"],
        }
    return out


# 원 판정 표본(이미 본 자료) — 판단 규칙의 '합친 하한'에만 쓴다. 등록 §4 에 고정.
HIST: dict[str, dict[str, float]] = {
    "w1": {"mean": 0.0038, "sd": 0.0039 * math.sqrt(91), "n": 91, "decision_n": 30},
    "w2": {"mean": 0.020, "sd": 0.0115 * math.sqrt(33), "n": 33, "decision_n": 5},
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("run", "status"))
    args = parser.parse_args(argv)
    state = _load_state()
    report: dict[str, Any] = {"at": datetime.now(UTC).isoformat()}
    if args.mode == "run":
        now_ms = int(datetime.now(UTC).timestamp() * 1000)
        today = datetime.now(KST).date()
        jobs: dict[str, Callable[[], dict[str, Any]]] = {
            "w1": lambda: run_w1(state, now_ms),
            "w2": lambda: run_w2(state, today),
        }
        for name, fn in jobs.items():
            try:
                report[name] = fn()
            except Exception as exc:  # 한 규칙의 자료 실패가 다른 규칙을 막지 않게
                report[name] = {"error": f"{type(exc).__name__}: {exc}"}
        _save_state(state)
    report["summary"] = summary(state)
    REPORTS.mkdir(parents=True, exist_ok=True)
    text = json.dumps(report, ensure_ascii=False, indent=1, default=str)
    (REPORTS / f"{datetime.now(UTC).strftime('%Y-%m-%d')}.json").write_text(text, encoding="utf-8")
    sys.stdout.write(text + "\n")
    return 0


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            _reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
