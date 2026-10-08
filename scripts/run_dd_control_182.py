"""문서 182 — 레버리지 나스닥 + 배당주의 낙폭 줄이기 (추세 스위치 · 낙폭 브레이크 · 변동성 맞춤).

사용 예::

    python -m scripts.run_dd_control_182 run   # 1 회 판정 → reports/dd_control_182/result.json

* 사전 등록: docs/research/182-leveraged-dividend-drawdown-control-preregistration.md.
* 매달 말 결정 → 다음 달 목표 비중. 레버리지 몫을 줄인 만큼 고배당으로(현금 아님).
"""

from __future__ import annotations

import csv
import json
import math
import statistics
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path
from typing import Any

from scripts.run_div_mix_180 import DATA as DIV_DATA
from scripts.run_div_mix_180 import DATA_SHA as DIV_SHA
from scripts.run_div_mix_180 import _etf, french_hi30, own_hash
from scripts.run_lev_div_181 import KEYS, LEV_SHA, _lev, lev_hash, synthetic
from scripts.run_multi_asset_176 import EXPECTED_DATA_SHA, _st, build_table, data_hash
from scripts.run_qs_dotcom import _series

from auto_trading.research.multi_asset import sharpe_diff_z, stats, turnover

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "reports" / "dd_control_182"
COST = 0.0015
LEV_MAX = 0.6
DD_FULL = 0.30
VOL_REF = 0.20
LOOKBACKS = (20, 60, 120)
VOL_DAYS = 20
P1_GAIN, P2_GAIN, P3_CAGR, P4_SHARPE = 0.15, 0.05, 0.02, 0.02
HALF_FROM = "2006-05"
CRISES = {
    "1987 블랙먼데이": ("1987-09", "1987-12"),
    "2000 ~ 02 닷컴": ("2000-03", "2002-09"),
    "2008 금융위기": ("2007-11", "2009-02"),
    "2020 코로나": ("2020-02", "2020-03"),
    "2022 금리 인상": ("2022-01", "2022-12"),
}


@dataclass(frozen=True, slots=True)
class Signals:
    """그달 마지막 거래일 기준 신호 (없으면 None)."""

    trend_on: bool | None
    vol20: float | None


def month_signals(days: Sequence[str], px: Mapping[str, float]) -> dict[str, Signals]:
    """일간 종가 → 월 → 그달 마지막 거래일의 추세 · 20 일 변동성 (순수)."""
    last: dict[str, int] = {}
    for i, d in enumerate(days):
        last[d[:7]] = i
    out: dict[str, Signals] = {}
    for m, i in last.items():
        p = px[days[i]]
        trend: bool | None = None
        if i >= max(LOOKBACKS):
            score = sum(
                (1 if p / px[days[i - k]] - 1 > 0 else -1 if p / px[days[i - k]] - 1 < 0 else 0)
                for k in LOOKBACKS
            ) / len(LOOKBACKS)
            trend = score > 0
        vol: float | None = None
        if i >= VOL_DAYS:
            rets = [px[days[j]] / px[days[j - 1]] - 1 for j in range(i - VOL_DAYS + 1, i + 1)]
            vol = statistics.pstdev(rets) * math.sqrt(252)
        out[m] = Signals(trend, vol)
    return out


Rule = Callable[[Signals | None, Sequence[float]], float]
"""(지난달 말 신호, 지금까지 달 말 계좌 값들) → 다음 달 레버리지 몫."""


def rule_m0(sig: Signals | None, values: Sequence[float]) -> float:
    return LEV_MAX


def rule_m1(sig: Signals | None, values: Sequence[float]) -> float:
    if sig is None or sig.trend_on is None:
        return LEV_MAX
    return LEV_MAX if sig.trend_on else 0.0


def rule_m2(sig: Signals | None, values: Sequence[float]) -> float:
    if not values:
        return LEV_MAX
    peak = max(values[-12:])
    dd = 1 - values[-1] / peak if peak > 0 else 0.0
    return LEV_MAX * min(1.0, max(0.0, 1 - dd / DD_FULL))


def rule_m3(sig: Signals | None, values: Sequence[float]) -> float:
    if sig is None or sig.vol20 is None or sig.vol20 <= 0:
        return LEV_MAX
    return LEV_MAX * min(1.0, VOL_REF / sig.vol20)


RULES: dict[str, Rule] = {"M0": rule_m0, "M1": rule_m1, "M2": rule_m2, "M3": rule_m3}


def run_rule(
    months: Sequence[str],
    lev: Mapping[str, float],
    other: Mapping[str, float],
    rf: Mapping[str, float],
    sigs: Mapping[str, Signals],
    prev_month: Mapping[str, str],
    rule: Rule,
    off_asset: str = "other",
) -> tuple[list[float], list[float]]:
    """월 수익 · 그 달 레버리지 몫 (순수). ``off_asset`` 이 'cash' 면 남는 몫은 무위험 금리."""
    rets: list[float] = []
    shares: list[float] = []
    values = [1.0]
    w_old: dict[str, float] = {}
    for k, m in enumerate(months):
        s = rule(sigs.get(prev_month[m]), values[1:] if k else [])
        tgt = {"L": s, "O": 1 - s}
        fee = COST * (1.0 if k == 0 else turnover(w_old, tgt))
        r_o = rf[m] if off_asset == "cash" else other[m]
        r = s * lev[m] + (1 - s) * r_o - fee
        rets.append(r)
        shares.append(s)
        values.append(values[-1] * (1 + r))
        gl, go = s * (1 + lev[m]), (1 - s) * (1 + r_o)
        tot = gl + go
        w_old = {"L": gl / tot, "O": go / tot} if tot > 0 else {"L": 0.0, "O": 1.0}
    return rets, shares


def window(rets: Sequence[float]) -> dict[str, float]:
    v, peak, mdd = 1.0, 1.0, 0.0
    for r in rets:
        v *= 1 + r
        peak = max(peak, v)
        mdd = max(mdd, 1 - v / peak)
    return {"total": round(v - 1, 4), "mdd": round(mdd, 4)}


def verdict_row(
    months: Sequence[str], rets: Sequence[float], base: Sequence[float], rf: Sequence[float]
) -> dict[str, Any]:
    st, sb = stats(rets, rf), stats(base, rf)
    h = next(i for i, m in enumerate(months) if m >= HALF_FROM)
    halves = []
    for a, b in ((0, h), (h, len(months))):
        halves.append([round(window(rets[a:b])["mdd"], 4), round(window(base[a:b])["mdd"], 4)])
    _, z = sharpe_diff_z(rets, base, rf)
    p1 = st.mdd <= sb.mdd - P1_GAIN
    p2 = all(x <= y - P2_GAIN for x, y in halves)
    p3 = st.cagr >= sb.cagr - P3_CAGR
    p4 = st.sharpe >= sb.sharpe - P4_SHARPE
    return {
        "stats": _st(st),
        "M0": _st(sb),
        "half_mdd_[rule,M0]": halves,
        "z_sharpe_vs_M0": round(z, 2),
        "P1": p1,
        "P2": p2,
        "P3": p3,
        "P4": p4,
        "pass": p1 and p2 and p3 and p4,
    }


def _prev_map(months: Sequence[str]) -> dict[str, str]:
    out = {}
    for m in months:
        y, mo = int(m[:4]), int(m[5:7])
        out[m] = f"{y - (mo == 1)}-{(mo - 2) % 12 + 1:02d}"
    return out


def part_a(table_in: Mapping[str, Mapping[str, float]]) -> dict[str, Any]:
    syn = synthetic()
    hi = french_hi30()
    ndx = _series("NASDAQ100.csv", "NASDAQ100")
    days = sorted(d for d in ndx if d <= "2026-08-31")
    sigs = month_signals(days, ndx)
    months = sorted(
        m
        for m, row in table_in.items()
        if m >= "1986-02"
        and m in hi
        and "RF" in row
        and all(m in syn[k] for k in syn)
        and "UST10" in row
        and "GOLD" in row
    )
    prev = _prev_map(months)
    rf = {m: table_in[m]["RF"] for m in months}
    rf_l = [rf[m] for m in months]
    res: dict[str, Any] = {
        "period": [months[0], months[-1]],
        "NDX_1x": _st(stats([syn["N1"][m] for m in months], rf_l)),
    }
    methods: dict[str, dict[str, Any]] = {}
    for k, key in KEYS.items():
        lev = syn[key]
        tag = f"{int(k)}x"
        res[f"{tag}_only"] = _st(stats([lev[m] for m in months], rf_l))
        runs = {
            name: run_rule(months, lev, hi, rf, sigs, prev, rule) for name, rule in RULES.items()
        }
        runs["QS_cash"] = run_rule(months, lev, hi, rf, sigs, prev, rule_m1, off_asset="cash")
        base = runs["M0"][0]
        block: dict[str, Any] = {}
        for name, (rets, shares) in runs.items():
            row: dict[str, Any] = (
                verdict_row(months, rets, base, rf_l)
                if name != "M0"
                else {"stats": _st(stats(rets, rf_l))}
            )
            row["avg_lev_share"] = round(statistics.fmean(shares), 3)
            row["changes_gt_5pp"] = sum(abs(b - a) > 0.05 for a, b in pairwise(shares))
            row["crises"] = {
                c: window([r for m, r in zip(months, rets, strict=True) if a <= m <= b])
                for c, (a, b) in CRISES.items()
            }
            block[name] = row
        res[tag] = block
        for name in ("M1", "M2", "M3"):
            methods.setdefault(name, {})[tag] = block[name]["pass"]
    res["method_verdicts"] = {
        n: "HISTORICAL_SUPPORT" if all(v.values()) else "REJECT" for n, v in methods.items()
    }
    return res


def _qqq_daily() -> dict[str, float]:
    with (DIV_DATA / "QQQ.csv").open(encoding="utf-8") as f:
        return {r["date"]: float(r["adjclose"]) for r in csv.DictReader(f)}


def part_b(table: Mapping[str, Mapping[str, float]]) -> dict[str, Any]:
    q = _qqq_daily()
    qd = sorted(q)
    sigs = month_signals(qd, q)
    last_rf = table[max(m for m in table if "RF" in table[m])]["RF"]
    out: dict[str, Any] = {}
    for lev_sym in ("TQQQ", "QLD"):
        lev = _lev(lev_sym)
        for d in ("SCHD", "VYM", "HDV"):
            div = _etf(d)
            months = sorted(set(lev) & set(div))
            prev = _prev_map(months)
            rf = {m: table.get(m, {}).get("RF", last_rf) for m in months}
            rf_l = [rf[m] for m in months]
            runs = {n: run_rule(months, lev, div, rf, sigs, prev, r) for n, r in RULES.items()}
            base = runs["M0"][0]
            block: dict[str, Any] = {"period": [months[0], months[-1]]}
            for n, (rets, _) in runs.items():
                st = _st(stats(rets, rf_l))
                block[n] = {
                    **st,
                    "z_vs_M0": round(sharpe_diff_z(rets, base, rf_l)[1], 2) if n != "M0" else None,
                    "2020": window(
                        [
                            r
                            for m, r in zip(months, rets, strict=True)
                            if "2020-02" <= m <= "2020-03"
                        ]
                    ),
                    "2022": window(
                        [
                            r
                            for m, r in zip(months, rets, strict=True)
                            if "2022-01" <= m <= "2022-12"
                        ]
                    ),
                }
            out[f"{lev_sym}+{d}"] = block
    return out


def run() -> dict[str, Any]:
    target = OUT / "result.json"
    if target.exists():
        raise SystemExit(f"이미 계산했다: {target}")
    if data_hash() != EXPECTED_DATA_SHA:
        raise SystemExit("자료 해시 불일치 (176)")
    if own_hash() != DIV_SHA:
        raise SystemExit("자료 해시 불일치 (180)")
    if lev_hash() != LEV_SHA:
        raise SystemExit("자료 해시 불일치 (181)")
    table = build_table()
    res: dict[str, Any] = {"A": part_a(table), "B": part_b(table)}
    res["computed"] = datetime.now(UTC).date().isoformat()
    OUT.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    return res


def main(argv: list[str]) -> int:
    if argv != ["run"]:
        raise SystemExit("usage: python -m scripts.run_dd_control_182 run")
    sys.stdout.write(json.dumps(run(), ensure_ascii=False, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            _reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main(sys.argv[1:]))
