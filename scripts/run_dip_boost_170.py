"""문서 170 — 지수 적립 + RSI 눌림 때 모아 둔 시드로 1 · 2 · 3 배 추가 매수 vs 전부 바로 지수 적립.

사용 예::

    python -m scripts.run_dip_boost_170 count   # RSI 신호 날 수 (수익 없음)
    python -m scripts.run_dip_boost_170 run     # 1 회 판정 → reports/dip_boost_170/result.json

* 사전 등록: docs/research/170-dip-boost-leverage.md. 87(RD, RSI ≤ 35 일 때만 사기 REJECT)과
  다른 점: 평소 적립은 계속하고 시드만 기다린다 · 2 · 3 배로도 산다 · 시드를 지수에 굴리는 경우.
* 매달 첫 거래일 입금의 절반은 바로 지수(1 배), 절반은 시드(현금 = 단기 금리 이자, 또는 지수).
  전날 RSI(14) < θ 이면 오늘 종가에 시드의 절반으로 k 배를 산다(10 거래일 간격, 팔지 않음).
  시드가 지수면 그만큼 지수를 팔고(실현 이익 22%, 해마다 합산) k 배를 산다. 수수료 0.25%.
* 비교(A): 입금 전부를 바로 지수 1 배.
"""

from __future__ import annotations

import csv
import json
import statistics
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path
from typing import Any

from scripts.run_qs_bottom_167 import _rsi_series
from scripts.run_qs_confirm import _windows
from scripts.run_qs_dotcom import _series
from scripts.run_qs_switch import FEE, QLD_FEE_YEAR, TAX, TQQQ_FEE_YEAR, leveraged

REPO = Path(__file__).resolve().parent.parent
XMKT = REPO / "data" / "research" / "qs_bottom_xmkt"
OUT = REPO / "reports" / "dip_boost_170"
CONTRIBUTION = 1_000.0
BASE_SHARE = 0.5
DEPLOY = 0.5
GAP = 10
THETAS = (30.0, 35.0)
DESIGNS = (("현금", 1), ("현금", 2), ("현금", 3), ("지수", 2), ("지수", 3))
NAS_WINDOWS = ("1987-01", "2016-08")
XMKT_FROM = "1950-01"
XMKT_RATE = 3.0
GAIN = 1.03
MARKETS_NEEDED = 8
XMKT_FILES = (
    "GSPC",
    "N225",
    "FTSE",
    "GSPTSE",
    "GDAXI",
    "HSI",
    "FCHI",
    "AXJO",
    "BVSP",
    "KS11",
    "TWII",
    "BSESN",
)


def simulate_boost(
    days: Sequence[str],
    idx: Mapping[str, float],
    lev: Mapping[str, float],
    rf: Mapping[str, float],
    rsi: Mapping[str, float | None],
    *,
    start: str,
    end: str,
    reserve: str,
    theta: float,
    base_share: float = BASE_SHARE,
) -> tuple[float, float, int]:
    """(마지막 평가액, 최대 낙폭(1 단위 가치), 추가 매수 횟수) (순수).

    ``base_share`` = 1 이면 A(전부 바로 지수)와 같다."""
    span = [d for d in days if start <= d <= end]
    pos = {d: i for i, d in enumerate(days)}
    q = l_sh = cash = res_sh = res_cost = units = peak = mdd = year_gain = 0.0
    last_buy = -(10**9)
    buys = 0
    last_month = ""
    value = 0.0
    for n, d in enumerate(span):
        i = pos[d]
        if i > 0 and cash > 0:
            cash *= 1 + rf.get(days[i - 1], 0.0) / 100 / 252
        before = q * idx[d] + l_sh * lev[d] + cash + res_sh * idx[d]
        if d[:7] != last_month:
            last_month = d[:7]
            nav = before / units if units > 0 else 1.0
            units += CONTRIBUTION / nav
            now = CONTRIBUTION * base_share
            q += now * (1 - FEE) / idx[d]
            seed = CONTRIBUTION - now
            if seed > 0:
                if reserve == "지수":
                    res_sh += seed * (1 - FEE) / idx[d]
                    res_cost += seed
                else:
                    cash += seed
        r = rsi.get(days[i - 1]) if i > 0 else None
        if r is not None and r < theta and i - last_buy >= GAP:
            if reserve == "지수" and res_sh > 0:
                sell = res_sh * DEPLOY
                proceeds = sell * idx[d] * (1 - FEE)
                cost_part = res_cost * DEPLOY
                year_gain += proceeds - cost_part
                res_sh -= sell
                res_cost -= cost_part
                l_sh += proceeds * (1 - FEE) / lev[d]
                last_buy, buys = i, buys + 1
            elif reserve == "현금" and cash > 0:
                amt = cash * DEPLOY
                cash -= amt
                l_sh += amt * (1 - FEE) / lev[d]
                last_buy, buys = i, buys + 1
        if n == len(span) - 1 or span[n + 1][:4] != d[:4]:
            if year_gain > 0:
                tax = TAX * year_gain
                if cash >= tax:
                    cash -= tax
                else:  # 현금이 모자라면 지수 시드에서 낸다
                    res_sh -= (tax - cash) / idx[d]
                    cash = 0.0
            year_gain = 0.0
        value = q * idx[d] + l_sh * lev[d] + cash + res_sh * idx[d]
        nav = value / units if units > 0 else 1.0
        peak = max(peak, nav)
        mdd = max(mdd, 1 - nav / peak) if peak > 0 else mdd
    return value, mdd, buys


def _nasdaq() -> tuple[list[str], dict[str, float], dict[str, float]]:
    ndx = _series("NASDAQ100.csv", "NASDAQ100")
    rate = _series("DTB3_full.csv", "DTB3")
    days = sorted(d for d in ndx if d <= "2026-08-31")
    rf: dict[str, float] = {}
    last = 0.0
    for d in sorted(set(days) | set(rate)):
        last = rate.get(d, last)
        rf[d] = last
    return days, ndx, rf


def _market(code: str) -> tuple[list[str], dict[str, float], dict[str, float]]:
    with (XMKT / f"{code}.csv").open(encoding="utf-8") as f:
        px = {
            r["date"]: float(r["close"])
            for r in csv.DictReader(f)
            if r["date"] <= "2026-08-31" and float(r["close"]) > 0
        }
    days = sorted(px)
    return days, px, dict.fromkeys(days, XMKT_RATE)


def _evaluate(
    days: Sequence[str],
    idx: Mapping[str, float],
    rf: Mapping[str, float],
    wins: Sequence[tuple[str, str]],
) -> dict[str, Any]:
    rsi_list = _rsi_series([idx[d] for d in days])
    rsi = dict(zip(days, rsi_list, strict=True))
    levs = {
        1: dict(idx),
        2: leveraged(days, idx, rf, 2.0, QLD_FEE_YEAR),
        3: leveraged(days, idx, rf, 3.0, TQQQ_FEE_YEAR),
    }
    out: dict[str, Any] = {}
    base = [
        simulate_boost(
            days, idx, levs[1], rf, rsi, start=s, end=e, reserve="현금", theta=0, base_share=1.0
        )
        for s, e in wins
    ]
    out["A_dd_median"] = round(statistics.median(b[1] for b in base), 4)
    for reserve, k in DESIGNS:
        for th in THETAS:
            rel, dds, nb = [], [], []
            for (s, e), b in zip(wins, base, strict=True):
                v, dd, buys = simulate_boost(
                    days, idx, levs[k], rf, rsi, start=s, end=e, reserve=reserve, theta=th
                )
                rel.append(v / b[0])
                dds.append(dd)
                nb.append(buys)
            out[f"{reserve}·{k}배·RSI<{int(th)}"] = {
                "vs_A_median": round(statistics.median(rel), 4),
                "vs_A_min": round(min(rel), 4),
                "beats_A": round(sum(x > 1 for x in rel) / len(rel), 3),
                "dd_median": round(statistics.median(dds), 4),
                "buys_median": statistics.median(nb),
            }
    return out


def count() -> dict[str, Any]:
    days, ndx, _rf = _nasdaq()
    rsi = _rsi_series([ndx[d] for d in days])
    out = {}
    for th in THETAS:
        sig = [
            d for d, r in zip(days, rsi, strict=True) if r is not None and r < th and d >= "1987"
        ]
        spells = sum(1 for a, b in pairwise(sig) if days.index(b) - days.index(a) > GAP) + (
            1 if sig else 0
        )
        out[f"RSI<{int(th)}"] = {"days_1987_2026": len(sig), "spells_gap10": spells}
    return out


def run() -> dict[str, Any]:
    target = OUT / "result.json"
    if target.exists():
        raise SystemExit(f"이미 계산했다: {target}")
    days, ndx, rf = _nasdaq()
    nas = _evaluate(days, ndx, rf, _windows(days, *NAS_WINDOWS))
    xm: dict[str, Any] = {}
    for code in XMKT_FILES:
        mdays, px, mrf = _market(code)
        first = max(XMKT_FROM, mdays[min(len(mdays) - 1, 400)][:7])
        wins = [w for k, w in enumerate(_windows(mdays, first, "2016-08")) if k % 3 == 0]
        if len(wins) >= 4:
            xm[code] = {"windows": len(wins), **_evaluate(mdays, px, mrf, wins)}
    designs: dict[str, Any] = {}
    for reserve, k in DESIGNS:
        ok = True
        rows = {}
        for th in THETAS:
            key = f"{reserve}·{k}배·RSI<{int(th)}"
            beat = sum(1 for v in xm.values() if v[key]["vs_A_median"] > 1.0)
            p = nas[key]["vs_A_median"] >= GAIN and beat >= MARKETS_NEEDED
            ok = ok and p
            rows[key] = {"nasdaq": nas[key], "markets_beating_A": beat, "pass": p}
        designs[f"{reserve}·{k}배"] = {"rows": rows, "pass": ok}
    passed = [k for k, v in designs.items() if v["pass"]]
    result = {
        "nasdaq_A_dd": nas["A_dd_median"],
        "designs": designs,
        "passed": passed,
        "verdict": "HISTORICAL_SUPPORT" if passed else "REJECT",
        "xmkt": xm,
        "computed": datetime.now(UTC).date().isoformat(),
    }
    OUT.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return result


def main(argv: list[str]) -> int:
    mode = argv[0] if argv else ""
    if mode not in ("count", "run"):
        raise SystemExit("usage: python -m scripts.run_dip_boost_170 count|run")
    res = count() if mode == "count" else run()
    if mode == "run":
        res = {k: v for k, v in res.items() if k != "xmkt"}
    sys.stdout.write(json.dumps(res, ensure_ascii=False, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            _reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main(sys.argv[1:]))
