"""문서 231 — H1 숏을 생존 편향 없는 알트 종목군(새 코인 234 개, 폐지 포함)에서 그대로 재현.

사용 예::

    python -m scripts.run_h1_univ_231    # reports/h1_univ_231/result.json · 화면

* 규칙 · 체결은 229 엔진(`build_coin` · `_trade` 와 같은 계산) 그대로다.
  보유 중 자료가 끝나면(폐지) 마지막 종가 × 1.01 에서 닫는다(231 §2).
* 종목군: `data/research/alt_universe/members.json` — 달마다 그 달 종목군인 코인만 신호를 낸다.
"""

from __future__ import annotations

import bisect
import json
import math
import random
import statistics
import sys
from collections import Counter, defaultdict
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scripts.run_alt_short_230 import COINS as ALT15
from scripts.run_alt_short_230 import _coin as alt15_coin
from scripts.run_kb_alts_156 import load_4h

from auto_trading.research.short_search_229 import (
    SIZE,
    Coin,
    Trade,
    _finish,
    _isnan,
    build_coin,
)

REPO = Path(__file__).resolve().parent.parent
UNIV = REPO / "data" / "research" / "alt_universe" / "members.json"
OUT = REPO / "reports" / "h1_univ_231"
H4 = 4 * 3_600_000
WEEK_FMT = "%G-%V"
USED = {
    "BTCUSDT", "ETHUSDT", "XRPUSDT", "BNBUSDT", "ADAUSDT", "DOGEUSDT", "SOLUSDT", "LINKUSDT",
    "LTCUSDT", "TRXUSDT", "BCHUSDT", "DOTUSDT", "XLMUSDT", "ETCUSDT", "ATOMUSDT", "AVAXUSDT",
    "EOSUSDT",
}  # fmt: skip
REGIME, TRIGGER, EXIT, STOP = "R5", "T2", "E5", "S3"
MARKET = (0.0010, 0.0010, 0.0005, 0.0010)
"""판정 — 시장가: 진입 · 시간 청산 수수료 0.05% + 미끄럼 0.05%, 손절 수수료 0.05% + 미끄럼 0.10%."""
OLD230 = (0.00095, 0.00095, 0.00095, 0.002)
LIMIT = (0.0002, 0.0002, 0.0005, 0.0)
EOD_SLIP = 0.01
SEED, N_RANDOM = 231, 300
MIN_TRADES, COIN_MIN = 200, 10


def _ms(day: str) -> int:
    return int(datetime.fromisoformat(day).replace(tzinfo=UTC).timestamp() * 1000)


FROM, SPLIT, TO = _ms("2018-03-01"), _ms("2022-06-01"), _ms("2026-09-01")


def _month(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m")


def membership() -> dict[str, set[str]]:
    """코인 → 종목군인 달(YYYY-MM). 달 말 목록이 다음 달 종목군이다."""
    raw: dict[str, list[str]] = json.loads(UNIV.read_text(encoding="utf-8"))
    out: dict[str, set[str]] = defaultdict(set)
    for month_end, syms in raw.items():
        y, m = int(month_end[:4]), int(month_end[5:7])
        nxt = f"{y + (m == 12)}-{(m % 12) + 1:02d}"
        for s in syms:
            if s not in USED:
                out[s].add(nxt)
    return out


def trade(k: Coin, i: int, cost: tuple[float, float, float, float]) -> Trade | None:
    """229 ``_trade`` (E5 · S3) 와 같은 계산 + 자료 끝이면 마지막 종가 × 1.01 에서 닫기."""
    n = len(k.c)
    j = i + 1
    if j >= n or _isnan(k.ind["atr"][i]):
        return None
    entry = k.o[j]
    stop = entry * 1.03
    low_close = entry
    for q in range(j, min(n, j + 18 + 1)):
        if k.h[q] >= stop:
            px = max(k.o[q], stop) * (1 + cost[3])
            return _finish(k, i, j, q, entry, px, k.t[q] + H4, True, cost[0], cost[2])
        held = q - j + 1
        low_close = min(low_close, k.c[q])
        if not math.isnan(k.ind["atr"][q]):
            stop = min(stop, low_close + 2 * k.ind["atr"][q])
        if held >= 18:
            if q + 1 >= n:
                break
            return _finish(k, i, j, q + 1, entry, k.o[q + 1], k.t[q + 1], False, cost[0], cost[1])
    last = n - 1  # 자료가 끝났다 (폐지 · 중단)
    px = k.c[last] * (1 + EOD_SLIP)
    return _finish(k, i, j, last, entry, px, k.t[last] + H4, False, cost[0], cost[1])


def signals(k: Coin, months: set[str], cond: Callable[[int], bool]) -> list[int]:
    lo, hi = bisect.bisect_left(k.t, FROM), bisect.bisect_left(k.t, TO)
    return [i for i in range(lo, hi) if _month(k.t[i]) in months and cond(i)]


def run_rule(
    coins: Sequence[Coin], months: dict[str, set[str]], cost: tuple[float, float, float, float]
) -> list[Trade]:
    out: list[Trade] = []
    for k in coins:
        reg, trg = k.regime[REGIME], k.trig[TRIGGER]
        busy = -1
        for i in signals(k, months[k.name], lambda i, reg=reg, trg=trg: bool(reg[i] and trg[i])):
            if i + 1 >= len(k.t) or k.t[i + 1] < busy:
                continue
            tr = trade(k, i, cost)
            if tr is None:
                continue
            out.append(tr)
            busy = tr.exit_t
    out.sort(key=lambda x: x.exit_t)
    return out


def _week(x: Trade) -> str:
    return datetime.fromtimestamp(x.entry_t / 1000, UTC).strftime(WEEK_FMT)


def stats(trades: Sequence[Trade]) -> dict[str, Any]:
    r = [x.ret for x in trades]
    if len(r) < 3:
        return {"trades": len(r)}
    weeks: dict[str, float] = defaultdict(float)
    for x in trades:
        weeks[_week(x)] += x.ret
    w = list(weeks.values())
    sd, wsd = statistics.stdev(r), statistics.stdev(w)
    return {
        "trades": len(r),
        "mean": statistics.fmean(r),
        "t_naive": statistics.fmean(r) / sd * math.sqrt(len(r)) if sd else None,
        "weeks": len(w),
        "t_week": statistics.fmean(w) / wsd * math.sqrt(len(w)) if wsd else None,
        "win": sum(1 for x in r if x > 0) / len(r),
        "stops": sum(1 for x in trades if x.stopped) / len(r),
        "sum": sum(r),
    }


def random_in_regime(
    coins: Sequence[Coin], months: dict[str, set[str]], trades: Sequence[Trade]
) -> list[float]:
    """같은 코인 · 종목군 달 · 국면 안 아무 4 시간봉에서 같은 수만큼 진입 (겹치지 않게)."""
    need = Counter(x.coin for x in trades)
    pools = {
        k.name: signals(k, months[k.name], lambda i, reg=k.regime[REGIME]: bool(reg[i]))
        for k in coins
        if need[k.name]
    }
    by = {k.name: k for k in coins}
    rng = random.Random(SEED)
    means = []
    cache: dict[tuple[str, int], Trade | None] = {}
    for _ in range(N_RANDOM):
        rs: list[float] = []
        for name, pool in pools.items():
            k, got, busy = by[name], 0, -1
            for i in sorted(rng.sample(pool, min(len(pool), need[name] * 3))):
                if i + 1 >= len(k.t) or k.t[i + 1] < busy:
                    continue
                key = (name, i)
                if key not in cache:
                    cache[key] = trade(k, i, MARKET)
                tr = cache[key]
                if tr is None:
                    continue
                rs.append(tr.ret)
                busy = tr.exit_t
                got += 1
                if got >= need[name]:
                    break
        means.append(statistics.fmean(rs))
    return means


def account(trades: Sequence[Trade], cap_open: int | None) -> dict[str, float]:
    """거래마다 자본 대비 수익을 복리로.

    ``cap_open`` 이면 동시 숏이 그 수에 차면 새 진입을 건너뛴다.
    """
    taken: list[Trade] = []
    open_until: list[int] = []
    for x in sorted(trades, key=lambda z: z.entry_t):
        open_until = [e for e in open_until if e > x.entry_t]
        if cap_open is not None and len(open_until) >= cap_open:
            continue
        taken.append(x)
        open_until.append(x.exit_t)
    eq = peak = 1.0
    mdd = 0.0
    for x in sorted(taken, key=lambda z: z.exit_t):
        eq *= 1 + x.ret
        peak = max(peak, eq)
        mdd = max(mdd, 1 - eq / peak)
    years = (TO - FROM) / 86_400_000 / 365.25
    return {"trades": len(taken), "total": eq - 1, "cagr": eq ** (1 / years) - 1, "mdd": mdd}


def main() -> int:
    months = membership()
    names = sorted(months)
    coins: list[Coin] = []
    for s in names:
        b = load_4h(s)
        if len(b.t) < 60:
            continue
        bars = list(zip(b.t, b.o, b.h, b.lo, b.c, b.v, strict=True))
        coins.append(build_coin(s, bars, []))
    print(f"코인 {len(coins)} 개 읽음", flush=True)
    res: dict[str, Any] = {"coins_loaded": len(coins), "size_per_coin": SIZE}
    main_tr = run_rule(coins, months, MARKET)
    s = stats(main_tr)
    first = stats([x for x in main_tr if x.entry_t < SPLIT])
    second = stats([x for x in main_tr if x.entry_t >= SPLIT])
    per_coin = defaultdict(list)
    for x in main_tr:
        per_coin[x.coin].append(x.ret)
    elig = {c: sum(v) for c, v in per_coin.items() if len(v) >= COIN_MIN}
    pos_share = sum(1 for v in elig.values() if v > 0) / len(elig) if elig else None
    rnd = random_in_regime(coins, months, main_tr) if main_tr else []
    beat = sum(1 for m in rnd if m < s.get("mean", 0)) / len(rnd) if rnd else None
    if s["trades"] < MIN_TRADES:
        verdict = "표본 부족"
    elif s["mean"] <= 0:
        verdict = "불리"
    elif (
        (s["t_week"] or 0) >= 2.0
        and first.get("mean", 0) > 0
        and second.get("mean", 0) > 0
        and (pos_share or 0) >= 0.55
        and (beat or 0) >= 0.95
    ):
        verdict = "지지"
    else:
        verdict = "불명"
    res["market"] = {
        **s,
        "first_half": first,
        "second_half": second,
        "coins_eligible": len(elig),
        "coins_pos_share": pos_share,
        "random_in_regime_median": statistics.median(rnd) if rnd else None,
        "beats_random_share": beat,
        "verdict": verdict,
    }
    res["ref_old230"] = stats(run_rule(coins, months, OLD230))
    res["ref_limit"] = stats(run_rule(coins, months, LIMIT))
    by_year: dict[int, list[float]] = defaultdict(list)
    for x in main_tr:
        by_year[datetime.fromtimestamp(x.entry_t / 1000, UTC).year].append(x.ret)
    res["by_year"] = {y: {"trades": len(v), "sum": sum(v)} for y, v in sorted(by_year.items())}
    r_sorted = sorted((x.ret for x in main_tr), reverse=True)
    res["drop_top"] = {
        f"top{p}%": statistics.fmean(r_sorted[max(1, len(r_sorted) * p // 100) :]) for p in (1, 5)
    }
    dead = {"LUNAUSDT", "FTTUSDT", "WAVESUSDT", "SRMUSDT", "ANCUSDT"}
    res["delisted_examples"] = {
        c: {"trades": len(per_coin[c]), "sum": sum(per_coin[c])} for c in dead if per_coin.get(c)
    }
    res["coins_best"] = sorted(elig.items(), key=lambda z: -z[1])[:8]
    res["coins_worst"] = sorted(elig.items(), key=lambda z: z[1])[:8]
    res["account_doc"] = account(main_tr, None)
    res["account_cap_1x"] = account(main_tr, 2)
    # 230 알트 15 개(같은 시장가 비용)와 합치기
    from scripts.run_alt_short_230 import FROM as F230
    from scripts.run_alt_short_230 import RULES
    from scripts.run_alt_short_230 import TO as T230

    from auto_trading.research.short_search_229 import TradeCache, run_config

    c15 = TradeCache([alt15_coin(x) for x in ALT15])
    c15.cost = MARKET
    tr15 = run_config(c15, *RULES["H1"], F230, T230)
    res["alt15_market"] = stats(tr15)
    res["pooled_with_alt15"] = stats(list(main_tr) + list(tr15))
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "result.json").write_text(
        json.dumps(res, ensure_ascii=False, indent=1, default=float), encoding="utf-8"
    )
    print(json.dumps(res, ensure_ascii=False, indent=1, default=float))
    return 0


if __name__ == "__main__":
    sys.exit(main())
