"""문서 265 — 코인 상대 강도 갈아타기 (매주 28 일 수익 상위 3 · BTC 추세 필터) vs Vega.

사용 예::

    python -m scripts.run_coin_rs_265 audit   # 자료 · 종목군 · 시작일 (수익 없이)
    python -m scripts.run_coin_rs_265 run     # 한 번 → reports/coin_rs_265/result.json

종목군 = 157 ``monthly_top`` (지난 30 일 거래대금 상위 20, 폐지 페어 포함) + 157 에서 뺀 큰 코인 10.
R · B2 는 종가 d 목표 → 종가 d+1 체결, 비용 편도 0.15%, 세전, 처음 돈 1 한 번.
V 는 197 ``simulate_cruise`` (T/v2 BTC + ETH) 를 같은 가격 · 하루 늦춤 · 비용으로.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import sys
import zipfile
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import scripts.run_cruise_range_197 as m197
from scripts.fetch_alt_universe import OUT as UNIV
from scripts.fetch_alt_universe import SEEN, VISION, _month_ends, eligible, monthly_top

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "reports" / "coin_rs_265"
END = "2026-08-31"
MIN_UNIVERSE = 10
LOOK = 28
TOP = 3
COST = 0.0015
HALF = "2021-12"
LOOKBACKS = (20, 60, 120)
DATA_SHA = "cc836ffe68464e12ab1d55ad0bdcf4364bb5a8390214f5fb12845e2571b1536d"
"""audit 이 낸 자료 묶음 SHA-256 — 사전 등록 커밋 때 채운다(다르면 run 이 멈춤)."""

Series = dict[str, float]


def pairs() -> list[str]:
    symbols = json.loads((UNIV / "symbols.json").read_text(encoding="utf-8"))
    return sorted(set(eligible(symbols)) | {f"{b}USDT" for b in SEEN})


def load(sym: str) -> tuple[Series, Series]:
    """(종가, 거래대금) {YYYY-MM-DD: 값}."""
    close: Series = {}
    vol: Series = {}
    for p in sorted((VISION / sym / "1d").glob("*.zip")):
        with zipfile.ZipFile(p) as zf:
            text = zf.read(zf.namelist()[0]).decode("ascii")
        for row in csv.reader(io.StringIO(text)):
            if not row or not row[0].isdigit():
                continue
            ms = int(row[0])
            ms = ms // 1000 if ms > 10**14 else ms
            d = datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m-%d")
            if d <= END:
                close[d] = float(row[4])
                vol[d] = float(row[7])
    return close, vol


def data_hash(syms: Sequence[str]) -> str:
    h = hashlib.sha256()
    for s in sorted(syms):
        for p in sorted((VISION / s / "1d").glob("*.zip")):
            h.update(p.name.encode())
            h.update(hashlib.sha256(p.read_bytes()).digest())
    return h.hexdigest()


def universe() -> tuple[dict[str, Series], dict[str, list[str]], str]:
    """(종가, 달말 → 다음 달 종목군, 시작일)."""
    closes: dict[str, Series] = {}
    vols: dict[str, Series] = {}
    for s in pairs():
        c, v = load(s)
        if c:
            closes[s], vols[s] = c, v
    members = monthly_top(vols, _month_ends())
    first = next(me for me, m in sorted(members.items()) if len(m) >= MIN_UNIVERSE)
    start = (date.fromisoformat(first) + timedelta(days=1)).isoformat()
    return closes, members, start


def day_range(a: str, b: str) -> list[str]:
    d, e, out = date.fromisoformat(a), date.fromisoformat(b), []
    while d <= e:
        out.append(d.isoformat())
        d += timedelta(days=1)
    return out


class Px:
    """종가 조회 — 빠진 날은 직전 종가, 자료가 끝난 뒤는 None (폐지)."""

    def __init__(self, closes: Mapping[str, Series]) -> None:
        self.c = closes
        self.last = {s: max(v) for s, v in closes.items()}
        self.first = {s: min(v) for s, v in closes.items()}

    def at(self, s: str, d: str) -> float | None:
        if s not in self.c or d > self.last[s] or d < self.first[s]:
            return None
        if d in self.c[s]:
            return self.c[s][d]
        k = date.fromisoformat(d)
        while k.isoformat() not in self.c[s]:
            k -= timedelta(days=1)
        return self.c[s][k.isoformat()]


def btc_on(px: Px, d: str) -> bool:
    p = px.at("BTCUSDT", d)
    k = date.fromisoformat(d)
    ups = 0
    for lb in LOOKBACKS:
        q = px.at("BTCUSDT", (k - timedelta(days=lb)).isoformat())
        ups += p is not None and q is not None and p > q
    return ups >= 2


def members_for(members: Mapping[str, list[str]], d: str) -> list[str]:
    me = max(m for m in members if m < d)
    return members[me]


def rank(px: Px, univ: Sequence[str], d: str, look: int, top: int) -> list[str]:
    back = (date.fromisoformat(d) - timedelta(days=look)).isoformat()
    scored = []
    for s in univ:
        if d not in px.c.get(s, {}):
            continue  # 그날 실제로 거래된 코인만
        a, b = px.at(s, back), px.c[s][d]
        if a and a > 0:
            scored.append((-(b / a - 1), s))
    return [s for _, s in sorted(scored)[:top]]


Target = Callable[[str, bool], dict[str, float] | None]


def simulate(px: Px, days: Sequence[str], target: Target, cost: float) -> dict[str, Any]:
    """종가 d 목표 → 종가 d+1 체결. ``target(d, weekly)`` 가 None 이면 그대로."""
    qty: dict[str, float] = {}
    cash = 1.0
    pending: dict[str, float] | None = None
    last_t: dict[str, float] | None = None
    navs: Series = {}
    pnl: dict[str, float] = {}
    prev_px: dict[str, float] = {}
    traded = 0.0
    delisted: list[tuple[str, str]] = []
    held_names: set[str] = set()
    invested_days = 0.0

    def value(d: str) -> float:
        return cash + sum(q * (px.at(s, d) or prev_px[s]) for s, q in qty.items())

    for d in days:
        for s, q in list(qty.items()):
            p = px.at(s, d)
            if p is None:  # 자료 끝 — 마지막 종가에 판다
                cash += q * prev_px[s] * (1 - cost)
                traded += q * prev_px[s]
                del qty[s]
                delisted.append((d, s))
                continue
            pnl[s] = pnl.get(s, 0.0) + q * (p - prev_px[s])
            prev_px[s] = p
        if pending is not None:
            eq = value(d)
            want = {s: w for s, w in pending.items() if px.at(s, d) is not None}
            names = set(want) | set(qty)
            turn = sum(
                abs(want.get(s, 0.0) * eq - qty.get(s, 0.0) * (px.at(s, d) or 0.0)) for s in names
            )
            eq_after = eq - turn * cost
            traded += turn
            qty = {}
            for s, w in want.items():
                p = px.at(s, d)
                assert p is not None
                qty[s] = w * eq_after / p
                prev_px[s] = p
                held_names.add(s)
            cash = eq_after * (1 - sum(want.values()))
            pending = None
        weekly = date.fromisoformat(d).weekday() == 6
        t = target(d, weekly)
        if t is not None and (weekly or t != last_t):
            pending, last_t = t, t
        v = value(d)
        navs[d] = v
        invested_days += (v - cash) / v if v > 0 else 0.0
    return {
        "navs": navs,
        "pnl": pnl,
        "turnover": traded,
        "delisted": delisted,
        "held": sorted(held_names),
        "exposure": invested_days / len(days),
    }


def _filtered(px: Px, pick: Callable[[str], dict[str, float]]) -> Target:
    """BTC 필터 꺼지면 현금, 켜지는 날 · 주간 날(켜짐)에 ``pick`` 으로 다시 맞춤."""
    state: dict[str, bool | None] = {"on": None}

    def g(d: str, weekly: bool) -> dict[str, float] | None:
        on, was = btc_on(px, d), state["on"]
        state["on"] = on
        if not on:
            return {} if was is not False else None
        return pick(d) if weekly or was is not True else None

    return g


def rotation(px: Px, members: Mapping[str, list[str]], look: int, top: int) -> Target:
    def pick(d: str) -> dict[str, float]:
        names = rank(px, members_for(members, d), d, look, top)
        return {s: 1 / len(names) for s in names}

    return _filtered(px, pick)


def fixed(px: Px, w: Mapping[str, float]) -> Target:
    return _filtered(px, lambda d: dict(w))


def month_navs(navs: Mapping[str, float]) -> Series:
    out: Series = {}
    for d in sorted(navs):
        out[d[:7]] = navs[d]
    return out


def sharpe(rets: Sequence[float]) -> float:
    if len(rets) < 2:
        return float("nan")
    m = sum(rets) / len(rets)
    sd = math.sqrt(sum((r - m) ** 2 for r in rets) / (len(rets) - 1))
    return m / sd * math.sqrt(12) if sd > 0 else float("nan")


def stats(navs: Mapping[str, float], start: str) -> dict[str, Any]:
    days = sorted(navs)
    mn = month_navs(navs)
    months = sorted(mn)
    rets = {m: mn[m] / (mn[months[i - 1]] if i else 1.0) - 1 for i, m in enumerate(months)}
    r = list(rets.values())
    k = max(1, math.ceil(0.02 * len(r)))
    trimmed = sorted(r)[:-k]
    years = (date.fromisoformat(days[-1]) - date.fromisoformat(start)).days / 365.25
    final = navs[days[-1]]
    yearly: dict[str, float] = {}
    prev = 1.0
    for y in sorted({m[:4] for m in months}):
        end = mn[max(m for m in months if m[:4] == y)]
        yearly[y] = end / prev - 1
        prev = end
    return {
        "final": final,
        "cagr": final ** (1 / years) - 1,
        "mdd": -m197.mdd([navs[d] for d in days]),
        "sharpe": sharpe(r),
        "sharpe_h1": sharpe([v for m, v in rets.items() if m <= HALF]),
        "sharpe_h2": sharpe([v for m, v in rets.items() if m > HALF]),
        "sharpe_trim2": sharpe(trimmed),
        "trimmed_months": k,
        "yearly": yearly,
    }


def vega(px: Px, start: str) -> Series:
    coins = {"BTC": "BTCUSDT", "ETH": "ETHUSDT"}
    p = {c: dict(px.c[s]) for c, s in coins.items()}
    days = sorted(set(p["BTC"]) & set(p["ETH"]))
    m197.FEE = COST
    s = m197.simulate_cruise(days, p, start=start, monthly=0.0, lump=1e8, tax=False, lag=1)
    return {d: v / 1e8 for d, v in zip(s["nav_days"], s["vals"], strict=True)}


def audit() -> dict[str, Any]:
    closes, members, start = universe()
    union = sorted({s for m in members.values() for s in m})
    counts = {me: len(m) for me, m in members.items()}
    return {
        "pairs_with_daily": len(closes),
        "start": start,
        "union": len(union),
        "union_gone": sum(1 for s in union if max(closes[s]) < END),
        "seen_in_union": sorted(s for s in union if s[:-4] in SEEN),
        "counts_first": {k: v for k, v in counts.items() if k <= "2019-06-30"},
        "data_sha": data_hash([*union, "BTCUSDT", "ETHUSDT"]),
    }


def run() -> dict[str, Any]:
    target = OUT / "result.json"
    if target.exists():
        raise SystemExit(f"이미 계산했다: {target}")
    closes, members, start = universe()
    union = sorted({s for m in members.values() for s in m})
    sha = data_hash([*union, "BTCUSDT", "ETHUSDT"])
    if sha != DATA_SHA:
        raise SystemExit(f"자료 SHA 다름: {sha}")
    px = Px(closes)
    days = day_range(start, END)
    runs = {
        "R": simulate(px, days, rotation(px, members, LOOK, TOP), COST),
        "B2": simulate(px, days, fixed(px, {"BTCUSDT": 0.5, "ETHUSDT": 0.5}), COST),
        "BTC": simulate(px, days, lambda d, w: {"BTCUSDT": 1.0} if d == days[0] else None, COST),
        "R84": simulate(px, days, rotation(px, members, 84, TOP), COST),
        "R5": simulate(px, days, rotation(px, members, LOOK, 5), COST),
        "R_c010": simulate(px, days, rotation(px, members, LOOK, TOP), 0.0010),
        "B2_c010": simulate(px, days, fixed(px, {"BTCUSDT": 0.5, "ETHUSDT": 0.5}), 0.0010),
    }
    out: dict[str, Any] = {"prereg": "", "data_sha": sha, "start": start, "end": END, "models": {}}
    for k, r in runs.items():
        st = stats(r["navs"], start)
        yrs = len(days) / 365.25
        pnl = sorted(r["pnl"].items(), key=lambda x: x[1])
        out["models"][k] = {
            **st,
            "exposure": r["exposure"],
            "turnover_per_year": r["turnover"] / yrs,
            "coins_held": len(r["held"]),
            "delisted_sells": r["delisted"],
            "best5": pnl[::-1][:5],
            "worst5": pnl[:5],
        }
    out["models"]["V"] = stats(vega(px, start), start)
    r, b2, v = out["models"]["R"], out["models"]["B2"], out["models"]["V"]
    j = {
        "J1": r["sharpe"] >= b2["sharpe"] + 0.10,
        "J2": r["sharpe_h1"] > b2["sharpe_h1"] and r["sharpe_h2"] > b2["sharpe_h2"],
        "J3": r["sharpe"] > v["sharpe"],
        "J4": r["sharpe_trim2"] > b2["sharpe"],
    }
    out["checks"] = j
    out["verdict"] = "HISTORICAL_SUPPORT" if all(j.values()) else "REJECT"
    OUT.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def main(argv: list[str]) -> int:
    if argv[1:] == ["audit"]:
        print(json.dumps(audit(), ensure_ascii=False, indent=1))
        return 0
    if argv[1:] != ["run"]:
        raise SystemExit("usage: python -m scripts.run_coin_rs_265 audit|run")
    out = run()
    print(out["start"], "~", out["end"], out["verdict"], out["checks"])
    for k, m in out["models"].items():
        head = f"{k:7} 끝 {m['final']:.2f}배 연 {m['cagr']:+.1%} 낙폭 {m['mdd']:.1%}"
        sh = f" 샤프 {m['sharpe']:.2f} (앞 {m['sharpe_h1']:.2f} 뒤 {m['sharpe_h2']:.2f}"
        sh += f" 상위2%뺌 {m['sharpe_trim2']:.2f})"
        if "exposure" in m:
            sh += f" 투자 {m['exposure']:.0%} 회전 {m['turnover_per_year']:.1f}/년"
            sh += f" 코인 {m['coins_held']} 폐지 {len(m['delisted_sells'])}"
        print(head + sh)
        print("        " + " ".join(f"{y[2:]} {v:+.0%}" for y, v in m["yearly"].items()))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
