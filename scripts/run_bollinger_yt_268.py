"""문서 268 — 유튜브 '타점공식' 볼린저 밴드 매매법 둘을 생존 편향 없는 국장 자료로 (1 회).

사용 예::

    python -m scripts.run_bollinger_yt_268 audit   # 자료 해시 · 거래 수 (수익 없이)
    python -m scripts.run_bollinger_yt_268 run     # 한 번 → reports/bollinger_yt_268/result.json

* A′: 종가가 볼린저(40, 2) 하단선 아래로 처음 + 외국인 10 일(t−10 ~ t−1) 순매수 ≥ 5 만 주
  → 다음 날 종가 매수, 종가 ≥ 중심선(40) 첫날 매도 (최대 40 거래일).
* B: 직전 20 일 안에 하단선(20, 2) 아래였고 오늘 종가가 중심선(20)을 위로 넘음
  → 다음 날 종가 매수, 종가 ≥ 매수가 × 1.10 첫날 매도 (최대 40 거래일).
종목군 · 비용 · 칸 10 개 계좌는 151(BNF) 그대로.
"""

from __future__ import annotations

import hashlib
import json
import math
import sys
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from scripts.run_a1_train import git_state
from scripts.run_community_150 import (
    KR,
    KR_FEE,
    KR_FEE_REF,
    MIN_N,
    equity_stats,
    load_close,
    summary,
    trade_verdict,
)
from scripts.run_lt_kr import KrPanel, build_panel
from scripts.run_traders_151 import (
    BNF_MIN_PRICE,
    EXPECTED_DATA,
    Trade,
    _week,
    data_hash,
    pit_members,
)

REPO = Path(__file__).resolve().parent.parent
OUTPUT = REPO / "reports" / "bollinger_yt_268"
FLOWS = KR / "flows"
EXPECTED_DATA_268 = "f6dd9d9785206805049bd170e87c421b3b55fee1fe551c1dc94edcf3405e504d"
"""151 뒤 가격 자료가 갱신돼 151 해시(``EXPECTED_DATA``)와 다르다 — 268 audit 값을 고정."""
EXPECTED_FLOWS = "63eb6ef92d92806ee5b0814b4fa920a9ee8f98542be66afad4f0ae8b1e52b001"
SPLIT, TO = "2017-01-01", "2026-08-31"
A_FROM, B_FROM = "2008-03-01", "2007-07-01"
A_N, B_N, K = 40, 20, 2.0
FRGN_DAYS, FRGN_SHARES = 10, 50_000.0
B_LOOK, B_TARGET = 20, 0.10
MAX_DAYS = 40
SLOTS = 10

Exit = Callable[[int, float, float], bool]
"""(날 색인, 그날 종가, 매수가) → 판다."""


def bands(xs: Sequence[float | None], n: int) -> list[tuple[float, float, float] | None]:
    """(하단, 중심, 상단) — n 일 모두 거래가 있어야 계산."""
    out: list[tuple[float, float, float] | None] = [None] * len(xs)
    for t in range(n - 1, len(xs)):
        w = xs[t - n + 1 : t + 1]
        if any(x is None for x in w):
            continue
        vals = [float(x) for x in w if x is not None]
        m = sum(vals) / n
        sd = math.sqrt(sum((x - m) ** 2 for x in vals) / n)
        out[t] = (m - K * sd, m, m + K * sd)
    return out


def flows_hash() -> str:
    h = hashlib.sha256()
    for p in sorted(FLOWS.glob("*.csv")):
        h.update(p.name.encode())
        h.update(p.read_bytes())
    return h.hexdigest()


def foreign_shares(panel: KrPanel) -> dict[str, list[float | None]]:
    """종목 → 날마다 외국인 순매수 주식 수 근사(금액 백만 원 × 100 만 ÷ 수정 종가). 없으면 None."""
    at = {d.replace("-", ""): i for i, d in enumerate(panel.days)}
    out: dict[str, list[float | None]] = {}
    for p in sorted(FLOWS.glob("*.csv")):
        sym = p.stem
        if sym not in panel.close:
            continue
        xs: list[float | None] = [None] * len(panel.days)
        for line in p.read_text(encoding="utf-8").splitlines()[1:]:
            d, frgn, *_ = line.split(",")
            i = at.get(d)
            c = panel.close[sym][i] if i is not None else None
            if i is not None and c:
                xs[i] = float(frgn) * 1e6 / c
        out[sym] = xs
    return out


def walk(panel: KrPanel, sym: str, t: int, end: int, sell: Exit) -> tuple[int, str, float] | None:
    """신호 t → t+1 종가 매수 → (매도 색인, 이유, 비용 전 수익). 다음 날 거래 없으면 None."""
    xs = panel.close[sym]
    j = t + 1
    if j > end or xs[j] is None:
        return None
    entry = xs[j]
    assert entry is not None
    held, u = 0, j
    while u < min(end, panel.last[sym]):
        u += 1
        c = xs[u]
        if c is None:
            continue
        held += 1
        if sell(u, c, entry):
            return u, "target", c / entry - 1
        if held >= MAX_DAYS:
            return u, "max_days", c / entry - 1
    k = min(end, panel.last[sym])
    while xs[k] is None:
        k -= 1
    reason = "delisted" if panel.last[sym] < end else "period_end"
    c = xs[k]
    assert c is not None
    return k, reason, c / entry - 1


def trades_for(
    panel: KrPanel,
    signals: Sequence[tuple[str, int, float]],
    sell_for: Callable[[str], Exit],
) -> tuple[list[Trade], dict[tuple[str, str], float]]:
    """(종목, 신호 색인, 순서 점수) → 거래 (한 종목 한 번에 하나, 종목군 · 100 원 조건)."""
    universe = pit_members(panel, [t for _, t, _ in signals])
    end = max(i for i, d in enumerate(panel.days) if d <= TO)
    busy: dict[str, int] = defaultdict(lambda: -1)
    out: list[Trade] = []
    rank: dict[tuple[str, str], float] = {}
    for sym, t, score in sorted(signals, key=lambda x: (x[1], x[0])):
        c = panel.close[sym][t]
        if sym not in universe[t] or t <= busy[sym] or c is None or c < BNF_MIN_PRICE:
            continue
        r = walk(panel, sym, t, end, sell_for(sym))
        if r is None:
            continue
        k, reason, ret = r
        out.append(Trade(sym, panel.days[t], ret, k - (t + 1), reason))
        rank[sym, panel.days[t]] = score
        busy[sym] = k
    return out, rank


def signals_a(
    panel: KrPanel, fr: Mapping[str, Sequence[float | None]], *, use_foreign: bool = True
) -> list[tuple[str, int, float]]:
    start = next(i for i, d in enumerate(panel.days) if d >= A_FROM)
    end = max(i for i, d in enumerate(panel.days) if d <= TO)
    out = []
    for sym, xs in panel.close.items():
        if sym not in fr:
            continue
        bb, f = bands(xs, A_N), fr[sym]
        for t in range(max(start, 1), end + 1):
            b, pb, c, pc = bb[t], bb[t - 1], xs[t], xs[t - 1]
            if b is None or pb is None or c is None or pc is None:
                continue
            if not (c < b[0] and pc >= pb[0]):
                continue
            if use_foreign:
                w = f[t - FRGN_DAYS : t]
                if sum(x or 0.0 for x in w) < FRGN_SHARES:
                    continue
            out.append((sym, t, c / b[0] - 1))
    return out


def sell_a(panel: KrPanel, upper: bool = False) -> Callable[[str], Exit]:
    cache: dict[str, list[tuple[float, float, float] | None]] = {}

    def for_sym(sym: str) -> Exit:
        if sym not in cache:
            cache[sym] = bands(panel.close[sym], A_N)
        bb = cache[sym]

        def sell(u: int, c: float, _entry: float) -> bool:
            b = bb[u]
            return b is not None and c >= (b[2] if upper else b[1])

        return sell

    return for_sym


def signals_b(panel: KrPanel) -> list[tuple[str, int, float]]:
    start = next(i for i, d in enumerate(panel.days) if d >= B_FROM)
    end = max(i for i, d in enumerate(panel.days) if d <= TO)
    out = []
    for sym, xs in panel.close.items():
        bb = bands(xs, B_N)
        for t in range(max(start, B_LOOK + 1), end + 1):
            b, pb, c, pc = bb[t], bb[t - 1], xs[t], xs[t - 1]
            if b is None or pb is None or c is None or pc is None:
                continue
            if not (pc <= pb[1] and c > b[1]):
                continue
            depths = [
                x / bk[0] - 1
                for k in range(t - B_LOOK, t)
                if (x := xs[k]) is not None and (bk := bb[k]) is not None and x < bk[0]
            ]
            if depths:
                out.append((sym, t, min(depths)))
    return out


def sell_b(_sym: str) -> Exit:
    return lambda _u, c, entry: c >= entry * (1 + B_TARGET)


def portfolio(
    panel: KrPanel,
    trades: Sequence[Trade],
    rank: Mapping[tuple[str, str], float],
    start_day: str,
    fee: float = KR_FEE,
) -> tuple[list[str], list[float], int]:
    """151 §9 와 같은 칸 10 개 계좌. 같은 날은 점수 낮은(깊은) 순. (날짜, 평가액, 못 산 신호 수)."""
    at = {d: i for i, d in enumerate(panel.days)}
    by_entry: dict[int, list[Trade]] = defaultdict(list)
    for x in trades:
        by_entry[at[x.date] + 1].append(x)
    start = next(i for i, d in enumerate(panel.days) if d >= start_day)
    end = max(i for i, d in enumerate(panel.days) if d <= TO)
    cash, missed = 1.0, 0
    open_: list[tuple[Trade, float, float, int]] = []
    days: list[str] = []
    eq: list[float] = []
    for t in range(start, end + 1):
        kept = []
        for x, q, last, out_t in open_:
            c = panel.close[x.sym][t]
            p = c if c is not None else last
            if t >= out_t:
                cash += q * p * (1 - fee)
            else:
                kept.append((x, q, p, out_t))
        open_ = kept
        value = cash + sum(q * last for _, q, last, _ in open_)
        for x in sorted(by_entry.get(t, []), key=lambda x: rank[x.sym, x.date]):
            entry = panel.close[x.sym][t]
            if len(open_) >= SLOTS or entry is None:
                missed += 1
                continue
            amt = min(cash, value / SLOTS)
            if amt <= 0:
                missed += 1
                continue
            cash -= amt
            open_.append((x, amt * (1 - fee) / entry, entry, t + x.days))
        days.append(panel.days[t])
        eq.append(cash + sum(q * last for _, q, last, _ in open_))
    return days, eq, missed


def evaluate(
    panel: KrPanel, trades: list[Trade], rank: Mapping[tuple[str, str], float], start_day: str
) -> dict[str, Any]:
    nets = [x.ret - 2 * KR_FEE for x in trades]
    dates = [x.date for x in trades]
    weeks = [_week(d) for d in dates]
    tv = trade_verdict(nets, dates, SPLIT, MIN_N, keys=weeks)
    days, eq, missed = portfolio(panel, trades, rank, start_day)
    kd, kc = load_close(KR / "kospi.csv")
    kmap = dict(zip(kd, kc, strict=True))
    k = [kmap[d] for d in days]
    split = next(i for i, d in enumerate(days) if d >= SPLIT)
    full, kos = equity_stats(eq), equity_stats(k)
    halves = [
        [equity_stats(eq[:split]), equity_stats(k[:split])],
        [equity_stats(eq[split:]), equity_stats(k[split:])],
    ]
    acct = {
        "a_cagr_gt_kospi": full["cagr"] > kos["cagr"],
        "b_sharpe_gt_kospi": full["sharpe"] > kos["sharpe"],
        "c_halves_cagr_gt_kospi": all(h[0]["cagr"] > h[1]["cagr"] for h in halves),
    }
    wk = Counter(weeks)
    cut = sorted(wk.values())[int(0.95 * (len(wk) - 1))] if wk else 0
    busy = [n for n, w in zip(nets, weeks, strict=True) if wk[w] >= cut]
    calm = [n for n, w in zip(nets, weeks, strict=True) if wk[w] < cut]
    by_year: dict[str, list[float]] = defaultdict(list)
    for x, n in zip(trades, nets, strict=True):
        by_year[x.date[:4]].append(n)
    yearly_acct = {}
    for y in sorted({d[:4] for d in days}):
        idx = [i for i, d in enumerate(days) if d[:4] == y]
        yearly_acct[y] = round(eq[idx[-1]] / eq[max(idx[0] - 1, 0)] - 1, 4)
    passed = tv.get("verdict") == "PASS" and all(acct.values())
    return {
        "verdict": "PASS" if passed else "REJECT",
        "trade": tv,
        "account_checks": acct,
        "account": full,
        "kospi_price_index": kos,
        "account_halves": halves,
        "account_by_year": yearly_acct,
        "missed_signals": missed,
        "exit_reasons": dict(Counter(x.reason for x in trades)),
        "mean_days": round(sum(x.days for x in trades) / len(trades), 1) if trades else 0,
        "by_year": {y: [len(v), round(sum(v) / len(v), 4)] for y, v in sorted(by_year.items())},
        "ref_cost": summary([x.ret - 2 * KR_FEE_REF for x in trades], weeks),
        "busy_weeks": {
            "cut_signals_per_week": cut,
            "share": round(len(busy) / len(nets), 3) if nets else 0,
            "mean_busy": round(sum(busy) / len(busy), 4) if busy else None,
            "mean_other": round(sum(calm) / len(calm), 4) if calm else None,
        },
    }


def build() -> dict[str, Any]:
    panel, _ = build_panel()
    fr = foreign_shares(panel)
    a_tr, a_rank = trades_for(panel, signals_a(panel, fr), sell_a(panel))
    b_tr, b_rank = trades_for(panel, signals_b(panel), sell_b)
    return {"panel": panel, "fr": fr, "A": (a_tr, a_rank), "B": (b_tr, b_rank)}


def audit() -> dict[str, Any]:
    digest, _ = data_hash()
    built = build()
    a_tr, b_tr = built["A"][0], built["B"][0]
    return {
        "data_sha256": digest,
        "data_matches_151": digest == EXPECTED_DATA,
        "flows_sha256": flows_hash(),
        "flows_symbols_in_panel": len(built["fr"]),
        "A_trades": len(a_tr),
        "A_halves": [sum(x.date < SPLIT for x in a_tr), sum(x.date >= SPLIT for x in a_tr)],
        "B_trades": len(b_tr),
        "B_halves": [sum(x.date < SPLIT for x in b_tr), sum(x.date >= SPLIT for x in b_tr)],
    }


def run() -> dict[str, Any]:
    target = OUTPUT / "result.json"
    if target.exists():
        raise SystemExit(f"이미 판정했다: {target}")
    sha, dirty = git_state()
    if dirty:
        raise SystemExit("작업 트리가 깨끗하지 않다 — 커밋 뒤에 실행")
    digest, _ = data_hash()
    if digest != EXPECTED_DATA_268 or flows_hash() != EXPECTED_FLOWS:
        raise SystemExit("자료 해시가 다르다")
    built = build()
    panel, fr = built["panel"], built["fr"]
    result: dict[str, Any] = {"commit": sha, "data_sha256": digest, "flows_sha256": EXPECTED_FLOWS}
    a_tr, a_rank = built["A"]
    b_tr, b_rank = built["B"]
    result["A"] = evaluate(panel, a_tr, a_rank, A_FROM)
    result["B"] = evaluate(panel, b_tr, b_rank, B_FROM)
    up_tr, up_rank = trades_for(panel, signals_a(panel, fr), sell_a(panel, upper=True))
    nf_tr, nf_rank = trades_for(panel, signals_a(panel, fr, use_foreign=False), sell_a(panel))
    result["report_A_upper_exit"] = evaluate(panel, up_tr, up_rank, A_FROM)
    result["report_A_no_foreign"] = evaluate(panel, nf_tr, nf_rank, A_FROM)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    with target.open("x", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    return result


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else "audit"
    if cmd == "audit":
        print(json.dumps(audit(), ensure_ascii=False, indent=1))
        return 0
    if cmd != "run":
        raise SystemExit(f"알 수 없는 명령: {cmd}")
    r = run()
    for k in ("A", "B", "report_A_upper_exit", "report_A_no_foreign"):
        v = r[k]
        t = v["trade"]
        print(
            k,
            v["verdict"],
            f"거래 {t.get('n')} 평균 {t.get('mean')} 하한 {t.get('lo2')} 반쪽 {t.get('halves')}",
            f"승률 {t.get('win')} | 계좌 {v['account']} KOSPI {v['kospi_price_index']}",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
