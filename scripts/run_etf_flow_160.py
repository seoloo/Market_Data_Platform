"""160 — 미국 현물 BTC ETF 흐름: 23 순유입 LONG · 24 공매도 거래량 SHORT (독립 2 트랙).

사용 예::

    python -m scripts.run_etf_flow_160 audit      # 공개 시각 · 수정 감사 — 수익 없음
    python -m scripts.run_etf_flow_160 count      # 사건 수 · 무조건 분산(검정력) — 신호별 수익 없음
    python -m scripts.run_etf_flow_160 verdict23  # 23 1 회 판정
    python -m scripts.run_etf_flow_160 verdict24  # 24 1 회 판정

* 사전 등록: docs/research/160-btc-etf-flow-preregistration.md. 규칙 · 상수는 결과 전에 고정.
* 자료: ``scripts/fetch_etf_flow.py`` (Farside · Wayback · FINRA · Yahoo 종가 · funding 꼬리),
  BTCUSDT 무기한 1분봉 · funding 은 ``data/trading.db``.
* 신호 · 체결: ``auto_trading.research.etf_flow``.
* 표본: 세션 2024-01-11 ~ 2026-10-02 (마지막 72 시간 청산이 1분봉 끝 10-06 안에 들어오게).
* 판정 SE: 평가 시각의 주(週) 묶음 Bartlett 시차 1 — 158 의 ``cluster_se``.
"""

from __future__ import annotations

import argparse
import bisect
import csv
import hashlib
import json
import math
import sqlite3
import statistics
import sys
from array import array
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from scripts.verdict_kr_patterns import cluster_se, summary_stats  # noqa: E402

from auto_trading.research.etf_flow import (  # noqa: E402
    HOUR_MS,
    FlowRow,
    Minutes,
    Trade,
    abnormal,
    complete_vs,
    is_session,
    net_return,
    parse_farside,
    short_ratio,
    signal_23,
    signal_24,
    simulate,
)

DATA = REPO_ROOT / "data" / "research" / "etf_flow"
DB = REPO_ROOT / "data" / "trading.db"
ET = ZoneInfo("America/New_York")
FIRST, LAST = date(2024, 1, 11), date(2026, 10, 2)
SPLIT = date(2025, 7, 1)
COST, COST_REF = 0.00095, 0.00023
Z = 2.24
"""두 주 가설(23 · 24) 각각 한쪽 1.25% — 가족 오류 2.5%."""
MIN_TRADES = 30
WEEK_MS = 7 * 24 * HOUR_MS
LAG_23_H = 24
"""23 가용 시각 = 세션 날 16:00 ET + LAG_23_H 시간 → 다음 정시. audit 결과로 결과 전 고정."""
CUTOFF_24 = time(19, 0)
TRACKS: dict[str, dict[str, Any]] = {
    "23": {"side": 1, "hold_h": 72},
    "24": {"side": -1, "hold_h": 24},
}
EXPECTED: dict[str, str] = {
    "data": "65fef5a139b57849858cdd1427ba081f3637a20ac3a04acd7e327ce694c9f5cb",
    "23": "3fdef1e67f0809261e5018bf9311da84d8b9a209606fa97a637b403b2a93e0af",
    "24": "1b0b10db6f98fd0df88e7b3c4d1957057fe7693a87cc7329b39df2863e6a7eb0",
}
"""count 뒤 고정(2026-10-07, 수익 미열람): 자료 · 사건 파일 해시."""


def _ceil_hour(ms: int) -> int:
    return -(-ms // HOUR_MS) * HOUR_MS


def _ms(d: date, t: time) -> int:
    return int(datetime.combine(d, t, ET).timestamp() * 1000)


# ---------------------------------------------------------------- 자료


def farside_final() -> list[FlowRow]:
    return parse_farside((DATA / "farside_all.html").read_text(encoding="utf-8", errors="replace"))


def finra_rows() -> tuple[dict[date, list[tuple[str, float, float]]], dict[date, int]]:
    by_key: dict[tuple[date, str], tuple[float, float]] = {}
    with (DATA / "finra" / "cnms_btc_etf.csv").open() as f:
        for r in csv.DictReader(f):
            if r["symbol"] == "DEFI":
                continue  # Farside 에도 없는 1 일짜리 행
            # 받기를 다시 시작하면 같은 (날짜, 종목) 행이 두 번 있을 수 있다 — 하나만
            by_key[(date.fromisoformat(r["date"]), r["symbol"])] = (
                float(r["short_volume"]),
                float(r["total_volume"]),
            )
    rows: dict[date, list[tuple[str, float, float]]] = defaultdict(list)
    for (d, sym), (sv, tv) in sorted(by_key.items()):
        rows[d].append((sym, sv, tv))
    lm: dict[date, int] = {}
    with (DATA / "finra" / "cnms_meta.csv").open() as f:
        for r in csv.DictReader(f):
            if r["status"] == "200" and r["last_modified_utc"]:
                lm[date.fromisoformat(r["date"])] = int(
                    datetime.fromisoformat(r["last_modified_utc"]).timestamp() * 1000
                )
    return dict(rows), lm


def prev_closes() -> dict[date, dict[str, float]]:
    """날짜 d → ETF 별 d 이전 마지막 종가."""
    series: dict[str, list[tuple[date, float]]] = {}
    for p in sorted((DATA / "prices").glob("*.csv")):
        with p.open() as f:
            series[p.stem] = [
                (date.fromisoformat(r["date"]), float(r["close"])) for r in csv.DictReader(f)
            ]
    out: dict[date, dict[str, float]] = {}
    d = FIRST - timedelta(days=7)
    while d <= LAST + timedelta(days=7):
        cur: dict[str, float] = {}
        for s, xs in series.items():
            k = bisect.bisect_left(xs, (d, -math.inf)) - 1
            if k >= 0:
                cur[s] = xs[k][1]
        out[d] = cur
        d += timedelta(days=1)
    return out


def load_minutes() -> Minutes:
    lo = int(datetime(2024, 1, 1, tzinfo=UTC).timestamp() * 1000)
    t, o, h, low, c = array("q"), array("d"), array("d"), array("d"), array("d")
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    q = (
        "select open_ms, open, high, low, close from candles where exchange='binance_usdm' "
        "and symbol='BTCUSDT' and timeframe='1m' and open_ms >= ? order by open_ms"
    )
    for r in con.execute(q, (lo,)):
        t.append(r[0])
        o.append(float(r[1]))
        h.append(float(r[2]))
        low.append(float(r[3]))
        c.append(float(r[4]))
    return Minutes(t, o, h, low, c)


def load_funding() -> list[tuple[int, float]]:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    lo = int(datetime(2024, 1, 1, tzinfo=UTC).timestamp() * 1000)
    out = {
        int(r[0]): float(r[1])
        for r in con.execute(
            "select funding_ms, rate from funding_rates where exchange='binance_usdm' "
            "and symbol='BTCUSDT' and funding_ms >= ?",
            (lo,),
        )
    }
    with (DATA / "funding_tail.csv").open() as f:
        for r in csv.DictReader(f):
            out.setdefault(int(r["funding_ms"]), float(r["rate"]))
    return sorted(out.items())


def data_hash() -> str:
    h = hashlib.sha256()
    for p in [
        DATA / "farside_all.html",
        DATA / "finra" / "cnms_btc_etf.csv",
        DATA / "finra" / "cnms_meta.csv",
        DATA / "funding_tail.csv",
        *sorted((DATA / "prices").glob("*.csv")),
    ]:
        h.update(p.name.encode())
        h.update(p.read_bytes())
    return h.hexdigest()


# ---------------------------------------------------------------- 감사 (수익 없음)


def audit() -> dict[str, Any]:
    final = {r.day: r for r in farside_final() if is_session(r)}
    snaps = sorted((DATA / "wayback").glob("*.html"))
    seen: dict[date, list[tuple[int, bool, float | None]]] = defaultdict(list)
    for p in snaps:
        ts = datetime.strptime(p.stem.split("_")[1], "%Y%m%d%H%M%S").replace(tzinfo=UTC)
        ms = int(ts.timestamp() * 1000)
        for r in parse_farside(p.read_text(encoding="utf-8", errors="replace")):
            if r.day in final:
                seen[r.day].append((ms, complete_vs(r, final[r.day]), r.total))
    lat: list[dict[str, Any]] = []
    revised = Counter[str]()
    for d, obs in sorted(seen.items()):
        obs.sort()
        close_ms = _ms(d, time(16, 0))
        first_done = next((o for o in obs if o[1]), None)
        last_open = max((o[0] for o in obs if not o[1]), default=None)
        if first_done is not None:
            diff = abs((first_done[2] or 0.0) - final[d].total)
            revised["same" if diff < 0.05 else "small" if diff < 5 else "large"] += 1
            lat.append(
                {
                    "day": d.isoformat(),
                    "weekday": d.weekday(),
                    "done_by_h": round((first_done[0] - close_ms) / HOUR_MS, 1),
                    "open_at_h": round((last_open - close_ms) / HOUR_MS, 1) if last_open else None,
                    "first_total": first_done[2],
                    "final_total": final[d].total,
                }
            )
    # 묶인 구간(완성 전 · 후 관측 간격 ≤ 12 시간)만 지연 추정에 쓴다
    tight = [x for x in lat if x["open_at_h"] is not None and x["done_by_h"] - x["open_at_h"] <= 12]
    _, lm = finra_rows()
    lm_lag = sorted(
        (v - _ms(d, time(16, 0))) / HOUR_MS for d, v in lm.items() if FIRST <= d <= LAST
    )
    return {
        "wayback_snapshots": len(snaps),
        "days_seen_complete": len(lat),
        "revision_first_complete_vs_final": dict(revised),
        "tight_bounds": len(tight),
        "tight_done_by_h_quantiles": _q([x["done_by_h"] for x in tight]),
        "tight_open_at_h_max": max((x["open_at_h"] for x in tight), default=None),
        "large_revisions": [x for x in lat if abs((x["first_total"] or 0) - x["final_total"]) >= 5],
        "tight_rows": tight,
        "finra_files": len(lm),
        "finra_last_modified_after_close_h_quantiles": _q(lm_lag),
        "finra_files_modified_after_next_day": sum(1 for v in lm_lag if v > 24),
    }


def _q(xs: Sequence[float]) -> dict[str, float] | None:
    if len(xs) < 5:
        return None
    s = sorted(xs)
    return {
        f"p{p}": round(s[min(len(s) - 1, int(p / 100 * len(s)))], 2)
        for p in (0, 10, 50, 90, 95, 100)
    }


# ---------------------------------------------------------------- 사건


def events_23() -> list[dict[str, Any]]:
    rows = [r for r in farside_final() if is_session(r) and r.day <= LAST]
    totals = [r.total for r in rows]
    out = []
    for i, r in enumerate(rows):
        s = signal_23(totals, i)
        if s is None or r.day < FIRST:
            continue
        ev = _ceil_hour(_ms(r.day, time(16, 0)) + LAG_23_H * HOUR_MS)
        out.append({"day": r.day.isoformat(), "eval_ms": ev, "signal": s, "x": r.total})
    return out


def events_24() -> list[dict[str, Any]]:
    rows, lm = finra_rows()
    pc = prev_closes()
    # 다음 날 이후에 고친 파일은 그 날 알 수 있던 버전이 아니다 → 세션에서 뺀다 (감사 §2.2)
    days = [
        d
        for d in sorted(rows)
        if FIRST <= d <= LAST and lm.get(d, 0) - _ms(d, time(16, 0)) <= 24 * HOUR_MS
    ]
    ratios: list[float] = []
    kept: list[date] = []
    for d in days:
        s = short_ratio(rows[d], pc[d])
        if s is not None:
            ratios.append(s)
            kept.append(d)
    a = abnormal(ratios)
    out = []
    for i, d in enumerate(kept):
        s = signal_24(a, i)
        if s is None:
            continue
        avail = max(_ms(d, CUTOFF_24), lm.get(d, 0))
        ev = _ceil_hour(avail)
        out.append({"day": d.isoformat(), "eval_ms": ev, "signal": s, "x": round(a[i] or 0.0, 6)})
    return out


def count(out_dir: Path) -> dict[str, Any]:
    m = load_minutes()
    fund = load_funding()
    res: dict[str, Any] = {"data_sha": data_hash()}
    for name, evs in (("23", events_23()), ("24", events_24())):
        tr = TRACKS[name]
        path = out_dir / f"events_{name}.jsonl"
        path.write_text("".join(json.dumps(e) + "\n" for e in evs), encoding="utf-8")
        sig = [e for e in evs if e["signal"]]
        # 겹치지 않게 고른 신호 수 — 보유 시간만으로 어림(수익 · 체결 결과 안 씀)
        busy_until, chosen = -1, 0
        for e in sig:
            if e["eval_ms"] >= busy_until:
                chosen += 1
                busy_until = e["eval_ms"] + tr["hold_h"] * HOUR_MS
        # 검정력 입력: 신호와 무관한 모든 세션의 순수익 SD (평균은 내지 않는다)
        nets = []
        for e in evs:
            t = _trade(m, e["eval_ms"], tr)
            if t is not None:
                nets.append(net_return(t, fund, COST)[0])
        sd = statistics.stdev(nets)
        n_sig = len(sig)
        res[name] = {
            "sessions": len(evs),
            "signals": n_sig,
            "non_overlapping_trades_est": chosen,
            "first_session": evs[0]["day"],
            "unconditional_net_sd": round(sd, 5),
            "mde80_trade_mean": round((Z + 0.84) * sd / math.sqrt(max(chosen, 1)), 5),
            "mde80_signal_minus_rest": round(
                (Z + 0.84) * sd * math.sqrt(1 / max(n_sig, 1) + 1 / max(len(evs) - n_sig, 1)), 5
            ),
            "signals_by_year": dict(Counter(e["day"][:4] for e in sig)),
            "events_sha": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
    return res


# ---------------------------------------------------------------- 판정


def _trade(m: Minutes, eval_ms: int, tr: Mapping[str, Any]) -> Trade | None:
    k0 = bisect.bisect_left(m.t, eval_ms)
    if k0 >= len(m.t) or m.t[k0] != eval_ms:
        return None
    return simulate(m, k0, tr["side"], tr["hold_h"] * HOUR_MS)


def verdict(name: str, evs: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    tr = TRACKS[name]
    m = load_minutes()
    fund = load_funding()
    allrows: list[dict[str, Any]] = []
    status = Counter[str]()
    for e in evs:
        t = _trade(m, e["eval_ms"], tr)
        if t is None:
            status["no_fill_or_data"] += 1
            continue
        net, gross, f = net_return(t, fund, COST)
        allrows.append(
            {
                "day": e["day"],
                "signal": e["signal"],
                "eval_ms": e["eval_ms"],
                "fill_ms": t.fill_ms,
                "exit_ms": t.exit_ms,
                "net": net,
                "net2x": net - 2 * COST,
                "net_ref": net + 2 * (COST - COST_REF),
                "gross": gross,
                "funding": f,
                "stopped": t.stopped,
                "block": e["eval_ms"] // WEEK_MS,
            }
        )
    # 매매: 신호 순서대로, 보유 중(실제 청산 전) 신호는 건너뜀
    trades: list[dict[str, Any]] = []
    busy = -1
    for r in allrows:
        if r["signal"] and r["eval_ms"] >= busy:
            trades.append(r)
            busy = r["exit_ms"]
    status["signals"] = sum(1 for e in evs if e["signal"])
    status["trades"] = len(trades)

    def stats(rows: Sequence[Mapping[str, Any]], key: str = "net") -> dict[str, Any]:
        return summary_stats([r[key] for r in rows], [r["block"] for r in rows], Z)

    def info(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        s = [r for r in rows if r["signal"]]
        n = [r for r in rows if not r["signal"]]
        if not s or len(n) < 2:
            return {"n_signal": len(s)}
        b = statistics.fmean(r["net"] for r in s) - statistics.fmean(r["net"] for r in n)
        se = math.hypot(
            cluster_se([r["net"] for r in s], [r["block"] for r in s]),
            cluster_se([r["net"] for r in n], [r["block"] for r in n]),
        )
        return {
            "n_signal": len(s),
            "n_rest": len(n),
            "diff": round(b, 5),
            "se": round(se, 5),
            "lower": round(b - Z * se, 5),
        }

    if len(trades) < MIN_TRADES:
        return {"verdict": "DATA_INSUFFICIENT", "status": dict(status)}
    t1 = stats(trades)
    t2 = info(allrows)
    first = [r for r in trades if r["day"] < SPLIT.isoformat()]
    second = [r for r in trades if r["day"] >= SPLIT.isoformat()]
    h_info = {
        "first": info([r for r in allrows if r["day"] < SPLIT.isoformat()]),
        "second": info([r for r in allrows if r["day"] >= SPLIT.isoformat()]),
    }
    t3 = (
        bool(first and second)
        and statistics.fmean(r["net"] for r in first) > 0
        and statistics.fmean(r["net"] for r in second) > 0
        and h_info["first"].get("diff", -1) > 0
        and h_info["second"].get("diff", -1) > 0
    )
    t4 = statistics.fmean(r["net2x"] for r in trades) > 0
    ok = t1["lower"] > 0 and t2.get("lower", -1) > 0 and t3 and t4
    by_year: dict[str, list[float]] = defaultdict(list)
    for r in trades:
        by_year[r["day"][:4]].append(r["net"])
    worst = sorted(trades, key=lambda r: r["net"])
    return {
        "verdict": "HISTORICAL_SUPPORT" if ok else "REJECT",
        "T1_trade_net_lower_gt0": t1["lower"] > 0,
        "T2_signal_minus_rest_lower_gt0": t2.get("lower", -1) > 0,
        "T3_both_halves_positive": t3,
        "T4_cost2x_mean_gt0": t4,
        "trades": t1,
        "information": t2,
        "halves_trades": {"first": stats(first), "second": stats(second)},
        "halves_information": h_info,
        "trades_gross": stats(trades, "gross"),
        "trades_cost_ref_0.023pct": stats(trades, "net_ref"),
        "trades_cost2x": stats(trades, "net2x"),
        "funding_mean": round(statistics.fmean(r["funding"] for r in trades), 6),
        "stopped": sum(1 for r in trades if r["stopped"]),
        "worst_trade": {k: worst[0][k] for k in ("day", "net", "stopped")},
        "best_trade": {k: worst[-1][k] for k in ("day", "net", "stopped")},
        "median_net": round(statistics.median(r["net"] for r in trades), 5),
        "win_rate": round(sum(1 for r in trades if r["net"] > 0) / len(trades), 3),
        "by_year_mean_net": {
            y: [len(v), round(statistics.fmean(v), 5)] for y, v in sorted(by_year.items())
        },
        "status": dict(status),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("audit", "count", "verdict23", "verdict24"))
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "reports/etf_flow_160")
    args = parser.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    if args.mode == "audit":
        res = audit()
    elif args.mode == "count":
        res = count(args.out)
    else:
        name = args.mode[-2:]
        path = args.out / f"events_{name}.jsonl"
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        if EXPECTED.get(name) != sha:
            raise SystemExit(f"사건 파일 해시 불일치 {sha}")
        if EXPECTED.get("data") != data_hash():
            raise SystemExit("자료 해시 불일치")
        evs = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x]
        res = verdict(name, evs)
        res.update({"events_sha": sha})
    (args.out / f"{args.mode}.json").write_text(
        json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    sys.stdout.write(
        json.dumps(
            {k: v for k, v in res.items() if k not in ("tight_rows",)}, ensure_ascii=False, indent=1
        )
        + "\n"
    )
    return 0


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            _reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
