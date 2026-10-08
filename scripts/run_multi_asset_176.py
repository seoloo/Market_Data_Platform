"""문서 176 — 여러 자산 추세 추종 A · B · C (주식은 미국 · 한국만).

사용 예::

    python -m scripts.run_multi_asset_176 fetch   # French · FRED · World Bank · AQR (로그인 없음)
    python -m scripts.run_multi_asset_176 build   # 월 자산 수익 표 · 자료 해시 (전략 수익 없음)
    python -m scripts.run_multi_asset_176 run     # A · B · C 각 1 회 판정 (reports/multi_asset_176)

* 사전 등록: docs/research/176-multi-asset-trend-preregistration.md.
* A: 미국 주식 · 미국채 10 년 · 금 · 원자재 (+ 한국 주식) 롱 추세, 꺼진 몫은 현금.
  1/N · 계좌 전체 위험 상한 12%.
* B: 나스닥 100 적립 옆에 추세 추종 소매(AQR TSMOM 요인 + 현금 − 연 1%) 10 · 20 · 30%.
* C: QS(110) 를 S&P 500 · 나스닥 100 · KOSPI(달러 환산)에 1/3 씩 나눠 걸기 vs 같은 나눔의 그냥 적립.
"""

from __future__ import annotations

import contextlib
import csv
import hashlib
import json
import statistics
import sys
import zipfile
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from itertools import pairwise
from pathlib import Path
from typing import Any

from scripts.run_qs_confirm import _windows
from scripts.run_qs_dotcom import _series
from scripts.run_qs_switch import TQQQ_FEE_YEAR, leveraged, simulate, trend_signals

from auto_trading.research.multi_asset import (
    bond_return,
    cap_weights,
    read_xlsx_sheet,
    sharpe_diff_z,
    stats,
    trend_fraction,
    turnover,
)

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "data" / "research" / "multi_asset"
XMKT = REPO / "data" / "research" / "qs_bottom_xmkt"
OUT = REPO / "reports" / "multi_asset_176"
END = "2026-08"
COST = 0.0015
"""편도 0.10% 수수료 + 0.05% 환전(토스 낮 우대)."""
RISK_CAP = 0.12
Z = 2.24
MDD_GAIN = 0.10
SLEEVE_FEE = 0.01
MIXES = (0.1, 0.2, 0.3)
EXPECTED_DATA_SHA = "d5185a9b7c44e600f6edde095ab7026c80071439fad73c1ad87bdf0915714771"
"""build 뒤 고정."""
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36"
SOURCES = {
    "french.zip": "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/F-F_Research_Data_Factors_CSV.zip",
    "wb.xlsx": "https://thedocs.worldbank.org/en/doc/74e8be41ceb20fa0da750cda2f6b9e4e-0050012026/related/CMO-Historical-Data-Monthly.xlsx",
    "aqr_tsmom.xlsx": "https://www.aqr.com/-/media/AQR/Documents/Insights/Data-Sets/Time-Series-Momentum-Factors-Monthly.xlsx",
}
FRED = ("DGS10", "DEXKOUS")


def fetch() -> None:
    import httpx

    DATA.mkdir(parents=True, exist_ok=True)
    for name, url in SOURCES.items():
        r = httpx.get(url, headers={"User-Agent": UA}, timeout=120, follow_redirects=True)
        r.raise_for_status()
        (DATA / name).write_bytes(r.content)
    for sid in FRED:
        r = httpx.get(
            f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}",
            headers={"User-Agent": "auto-trading-research"},
            timeout=120,
        )
        r.raise_for_status()
        (DATA / f"{sid}.csv").write_text(r.text, encoding="utf-8")
    print("fetched")


# ------------------------------------------------------------------ 자료 → 월 수익 표


def _month_end(daily: Mapping[str, float]) -> dict[str, float]:
    out: dict[str, float] = {}
    for d in sorted(daily):
        out[d[:7]] = daily[d]
    return out


def _fred_daily(sid: str) -> dict[str, float]:
    with (DATA / f"{sid}.csv").open(encoding="utf-8") as f:
        return {
            r["observation_date"]: float(r[sid])
            for r in csv.DictReader(f)
            if r[sid] not in ("", ".")
        }


def _yahoo(code: str) -> dict[str, float]:
    with (XMKT / f"{code}.csv").open(encoding="utf-8") as f:
        return {r["date"]: float(r["close"]) for r in csv.DictReader(f) if float(r["close"]) > 0}


def _french() -> tuple[dict[str, float], dict[str, float]]:
    with zipfile.ZipFile(DATA / "french.zip") as z:
        text = z.read(z.namelist()[0]).decode("latin-1")
    mkt: dict[str, float] = {}
    rf: dict[str, float] = {}
    started = False
    for line in text.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 5 and parts[0].isdigit() and len(parts[0]) == 6:
            started = True
            m = f"{parts[0][:4]}-{parts[0][4:]}"
            mkt[m] = (float(parts[1]) + float(parts[4])) / 100
            rf[m] = float(parts[4]) / 100
        elif started and not parts[0]:
            break  # 월간 다음은 연간 표
    return mkt, rf


def _wb() -> tuple[dict[str, float], dict[str, float]]:
    data = (DATA / "wb.xlsx").read_bytes()
    prices = read_xlsx_sheet(data, "Monthly Prices")
    head = next(r for r in prices if "Gold" in r)
    gi = head.index("Gold")
    gold = {}
    for r in prices:
        if r and len(r) > gi and len(r[0]) == 7 and r[0][4] == "M":
            with contextlib.suppress(ValueError):
                gold[f"{r[0][:4]}-{r[0][5:]}"] = float(r[gi])
    idx = read_xlsx_sheet(data, "Monthly Indices")
    ihead = next(r for r in idx if "Energy" in r)
    ei, ni = (
        ihead.index("Energy"),
        next(i for i, c in enumerate(ihead) if c.startswith("Non-energy")),
    )
    energy, non = {}, {}
    for r in idx:
        if r and len(r) > ni and len(r[0]) == 7 and r[0][4] == "M":
            try:
                energy[f"{r[0][:4]}-{r[0][5:]}"] = float(r[ei])
                non[f"{r[0][:4]}-{r[0][5:]}"] = float(r[ni])
            except ValueError:
                pass
    comm = {}
    for m in sorted(set(energy) & set(non)):
        comm[m] = (energy[m], non[m])
    # 원자재 지수 수준: 에너지 · 비에너지 월 수익의 평균을 이어 붙인다
    level, out = 100.0, {}
    prev = None
    for m in sorted(comm):
        if prev is not None:
            r = 0.5 * (comm[m][0] / comm[prev][0] - 1) + 0.5 * (comm[m][1] / comm[prev][1] - 1)
            level *= 1 + r
        out[m] = level
        prev = m
    return gold, out


def _aqr() -> dict[str, float]:
    rows = read_xlsx_sheet((DATA / "aqr_tsmom.xlsx").read_bytes(), "TSMOM Factors")
    head = next(i for i, r in enumerate(rows) if "TSMOM" in r)
    ci = rows[head].index("TSMOM")
    out = {}
    for r in rows[head + 1 :]:
        if len(r) > ci and r[0] and r[ci]:
            try:
                d = date(1899, 12, 30) + timedelta(days=int(float(r[0])))
                out[d.strftime("%Y-%m")] = float(r[ci])
            except ValueError:
                pass
    return out


def _returns(level: Mapping[str, float]) -> dict[str, float]:
    ms = sorted(level)
    return {b: level[b] / level[a] - 1 for a, b in pairwise(ms)}


def build_table() -> dict[str, dict[str, float]]:
    """월 → 자산 → 그 달 수익. 'avg' 표시 자산은 월평균 가격(신호 한 달 늦춤)."""
    us, rf = _french()
    y = _month_end(_fred_daily("DGS10"))
    ms = sorted(y)
    ust = {b: bond_return(y[a], y[b]) for a, b in pairwise(ms)}
    gold_lvl, comm_lvl = _wb()
    fx = _month_end(_fred_daily("DEXKOUS"))
    ks = _month_end(_yahoo("KS11"))
    kr_usd = {m: ks[m] / fx[m] for m in ks if m in fx}
    ndx = _month_end(_series("NASDAQ100.csv", "NASDAQ100"))
    aqr = _aqr()
    table: dict[str, dict[str, float]] = {}
    series = {
        "US": us,
        "RF": rf,
        "UST10": ust,
        "GOLD": _returns(gold_lvl),
        "COMM": _returns(comm_lvl),
        "KR": _returns(kr_usd),
        "NDX": _returns(ndx),
        "TSMOM_EX": aqr,
    }
    for name, s in series.items():
        for m, v in s.items():
            if m <= END:
                table.setdefault(m, {})[name] = v
    return table


def data_hash() -> str:
    h = hashlib.sha256()
    for p in sorted(DATA.glob("*")):
        h.update(p.name.encode())
        h.update(p.read_bytes())
    for code in ("KS11", "GSPC"):
        h.update((XMKT / f"{code}.csv").read_bytes())
    return h.hexdigest()


# ------------------------------------------------------------------ A


AVG_ASSETS = {"GOLD", "COMM"}


def run_a(
    table: Mapping[str, Mapping[str, float]], assets: Sequence[str], start: str
) -> dict[str, Any]:
    months = sorted(m for m in table if m >= start and all(a in table[m] for a in (*assets, "RF")))
    hist = {a: [table[m][a] for m in sorted(table) if m < start and a in table[m]] for a in assets}
    n = len(assets)
    trend, static, rfl = [], [], []
    w_old: dict[str, float] = {}
    s_old: dict[str, float] = {}
    rows_hist: list[list[float]] = []
    held = []
    for m in months:
        fr = {a: trend_fraction(hist[a], lag=1 if a in AVG_ASSETS else 0) for a in assets}
        if any(v is None for v in fr.values()):
            for a in assets:
                hist[a].append(table[m][a])
            rows_hist.append([table[m][a] for a in assets])
            continue
        w = [(1 / n) * (fr[a] or 0.0) for a in assets]
        w = cap_weights(w, rows_hist[-36:] if len(rows_hist) >= 12 else [], RISK_CAP)
        wt = dict(zip(assets, w, strict=True))
        ws = dict.fromkeys(assets, 1 / n)
        rf = table[m]["RF"]
        r_t = (
            sum(wt[a] * table[m][a] for a in assets)
            + (1 - sum(w)) * rf
            - COST * turnover(w_old, wt)
        )
        r_s = sum(ws[a] * table[m][a] for a in assets) - COST * turnover(s_old, ws)
        trend.append(r_t)
        static.append(r_s)
        rfl.append(rf)
        held.append(sum(w))
        # 다음 달을 위한 표류 비중(가격 변화 반영)
        grow_t = {a: wt[a] * (1 + table[m][a]) for a in assets}
        tot_t = sum(grow_t.values()) + (1 - sum(w)) * (1 + rf)
        w_old = {a: v / tot_t for a, v in grow_t.items()}
        grow_s = {a: ws[a] * (1 + table[m][a]) for a in assets}
        tot_s = sum(grow_s.values())
        s_old = {a: v / tot_s for a, v in grow_s.items()}
        for a in assets:
            hist[a].append(table[m][a])
        rows_hist.append([table[m][a] for a in assets])
    used = list(months)[-len(trend) :]
    half = len(trend) // 2
    st_t, st_s = stats(trend, rfl), stats(static, rfl)
    us_only = [table[m]["US"] for m in used]
    sixty = [0.6 * table[m]["US"] + 0.4 * table[m]["UST10"] for m in used]
    diff, z = sharpe_diff_z(trend, static, rfl)
    d1, _ = sharpe_diff_z(trend[:half], static[:half], rfl[:half])
    d2, _ = sharpe_diff_z(trend[half:], static[half:], rfl[half:])
    ok = z >= Z and st_s.mdd - st_t.mdd >= MDD_GAIN and d1 > 0 and d2 > 0
    return {
        "assets": list(assets),
        "period": [used[0], used[-1]],
        "verdict": "HISTORICAL_SUPPORT" if ok else "REJECT",
        "trend": _st(st_t),
        "static_1N": _st(st_s),
        "US_only": _st(stats(us_only, rfl)),
        "60_40": _st(stats(sixty, rfl)),
        "sharpe_diff_monthly": round(diff, 4),
        "z": round(z, 2),
        "halves_sharpe_diff_monthly": [round(d1, 4), round(d2, 4), used[half]],
        "mdd_reduction": round(st_s.mdd - st_t.mdd, 4),
        "avg_risky_weight": round(statistics.fmean(held), 3),
        "by_decade": _decades(used, trend, static),
    }


def _st(s: Any) -> dict[str, float]:
    return {
        k: round(getattr(s, k), 4) for k in ("months", "cagr", "vol", "sharpe", "mdd", "worst12")
    }


def _decades(
    months: Sequence[str], a: Sequence[float], b: Sequence[float]
) -> dict[str, list[float]]:
    out: dict[str, list[float]] = {}
    for dec in sorted({m[:3] for m in months}):
        idx = [i for i, m in enumerate(months) if m.startswith(dec)]
        ga = 1.0
        gb = 1.0
        for i in idx:
            ga *= 1 + a[i]
            gb *= 1 + b[i]
        yrs = len(idx) / 12
        out[dec + "0s"] = [round(ga ** (1 / yrs) - 1, 4), round(gb ** (1 / yrs) - 1, 4)]
    return out


# ------------------------------------------------------------------ B


def run_b(table: Mapping[str, Mapping[str, float]], equity: str) -> dict[str, Any]:
    months = sorted(m for m in table if all(k in table[m] for k in (equity, "TSMOM_EX", "RF")))
    rfl = [table[m]["RF"] for m in months]
    eq = [table[m][equity] for m in months]
    sleeve = [table[m]["TSMOM_EX"] + table[m]["RF"] - SLEEVE_FEE / 12 for m in months]
    res: dict[str, Any] = {
        "period": [months[0], months[-1]],
        "equity_only": _st(stats(eq, rfl)),
        "sleeve_only": _st(stats(sleeve, rfl)),
    }
    half = len(months) // 2
    for mix in MIXES:
        port = []
        w_old = {"E": 1 - mix, "S": mix}
        for e, s in zip(eq, sleeve, strict=True):
            target = {"E": 1 - mix, "S": mix}
            r = (1 - mix) * e + mix * s - COST * turnover(w_old, target)
            port.append(r)
            ge, gs = (1 - mix) * (1 + e), mix * (1 + s)
            w_old = {"E": ge / (ge + gs), "S": gs / (ge + gs)}
        st = stats(port, rfl)
        diff, z = sharpe_diff_z(port, eq, rfl)
        d1, _ = sharpe_diff_z(port[:half], eq[:half], rfl[:half])
        d2, _ = sharpe_diff_z(port[half:], eq[half:], rfl[half:])
        ok = z >= Z and stats(eq, rfl).mdd - st.mdd >= MDD_GAIN and d1 > 0 and d2 > 0
        res[f"mix_{int(mix * 100)}"] = {
            "stats": _st(st),
            "sharpe_diff_monthly": round(diff, 4),
            "z": round(z, 2),
            "halves": [round(d1, 4), round(d2, 4)],
            "mdd_reduction": round(stats(eq, rfl).mdd - st.mdd, 4),
            "pass": ok,
        }
    res["verdict"] = "HISTORICAL_SUPPORT" if res["mix_20"]["pass"] else "REJECT"
    return res


# ------------------------------------------------------------------ C


def run_c() -> dict[str, Any]:
    rate = _series("DTB3_full.csv", "DTB3")
    ndx = _series("NASDAQ100.csv", "NASDAQ100")
    spx = _yahoo("GSPC")
    ks = _yahoo("KS11")
    fx = _fred_daily("DEXKOUS")
    kr: dict[str, float] = {}
    last = None
    for d in sorted(ks):
        last = fx.get(d, last)
        if last:
            kr[d] = ks[d] / last
    idxs = {"S&P 500": spx, "나스닥 100": ndx, "KOSPI(달러)": kr}
    first = max(min(s) for s in idxs.values())
    out: dict[str, Any] = {}
    finals: dict[str, list[tuple[float, float, float, float]]] = {}
    for name, px in idxs.items():
        days = sorted(d for d in px if first <= d <= "2026-08-31")
        rf: dict[str, float] = {}
        lv = 0.0
        for d in sorted(set(days) | set(rate)):
            lv = rate.get(d, lv)
            rf[d] = lv
        sig = trend_signals(days, px)
        lev = leveraged(days, px, rf, 3.0, TQQQ_FEE_YEAR)
        wins = _windows(days, "1997-07", "2016-08")
        rows = []
        for s, e in wins:
            a = simulate(days, px, lev, sig, rf, mode="A", start=s, end=e)
            q = simulate(days, px, lev, sig, rf, mode="QS", start=s, end=e)
            rows.append((q.final, a.final, q.max_drawdown, a.max_drawdown))
        finals[name] = rows
        rel = [q / a for q, a, _, _ in rows]
        out[name] = {
            "windows": len(rows),
            "vs_A_median": round(statistics.median(rel), 4),
            "wins": round(sum(r > 1 for r in rel) / len(rel), 3),
            "dd_median_QS_A": [
                round(statistics.median(r[2] for r in rows), 4),
                round(statistics.median(r[3] for r in rows), 4),
            ],
        }
    n = min(len(v) for v in finals.values())
    rel = []
    for k in range(n):
        q = sum(finals[name][k][0] for name in idxs)
        a = sum(finals[name][k][1] for name in idxs)
        rel.append(q / a)
    out["split"] = {
        "windows": n,
        "vs_A_median": round(statistics.median(rel), 4),
        "wins": round(sum(r > 1 for r in rel) / n, 3),
        "min": round(min(rel), 4),
        "max": round(max(rel), 4),
    }
    out["verdict"] = (
        "HISTORICAL_SUPPORT"
        if out["split"]["vs_A_median"] >= 1.0 and out["split"]["wins"] >= 0.60
        else "REJECT"
    )
    out["first_day"] = first
    return out


# ------------------------------------------------------------------ 명령


def build() -> dict[str, Any]:
    table = build_table()
    spans = {}
    for a in ("US", "RF", "UST10", "GOLD", "COMM", "KR", "NDX", "TSMOM_EX"):
        ms = sorted(m for m in table if a in table[m])
        spans[a] = [ms[0], ms[-1], len(ms)]
    return {"data_sha": data_hash(), "spans": spans}


def run() -> dict[str, Any]:
    target = OUT / "result.json"
    if target.exists():
        raise SystemExit(f"이미 계산했다: {target}")
    if EXPECTED_DATA_SHA and data_hash() != EXPECTED_DATA_SHA:
        raise SystemExit("자료 해시 불일치")
    table = build_table()
    result = {
        "A1_미국주식_미국채_금_원자재_1973": run_a(
            table, ("US", "UST10", "GOLD", "COMM"), "1972-01"
        ),
        "A2_한국주식_추가_1998": run_a(table, ("US", "KR", "UST10", "GOLD", "COMM"), "1996-12"),
        "B_나스닥100": run_b(table, "NDX"),
        "B_미국시장(보조)": run_b(table, "US"),
        "C_QS_나눠걸기": run_c(),
        "computed": datetime.now(UTC).date().isoformat(),
    }
    OUT.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return result


def main(argv: list[str]) -> int:
    mode = argv[0] if argv else ""
    if mode == "fetch":
        fetch()
        return 0
    if mode not in ("build", "run"):
        raise SystemExit("usage: python -m scripts.run_multi_asset_176 fetch|build|run")
    res = build() if mode == "build" else run()
    sys.stdout.write(json.dumps(res, ensure_ascii=False, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            _reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main(sys.argv[1:]))
