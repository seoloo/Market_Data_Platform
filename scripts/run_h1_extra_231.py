"""문서 231 덧붙임 — H1 숏 독립 검증 · 비용 · 롱과 합치기 (참고 계산, 판정 아님).

2026-10-12 Claude 세션이 230 결과를 다시 확인하며 한 계산을 재현한다.
결과는 문서 231 §8 에 적었다.

사용 예::

    python -m scripts.run_h1_extra_231 cluster   # 230 알트 15 · BTC/ETH 비용별 · 주 묶음 t
    python -m scripts.run_h1_extra_231 regime    # 230 알트 15 같은 국면 안 무작위 비교
    python -m scripts.run_h1_extra_231 switch    # T/v2 롱 1·2·3 배 + H1 BTC/ETH 숏

* 모두 이미 본 자료(2020-06 ~ 2026-08)다. BTC · ETH 의 H1 은 고를 때 본 자료라 숫자가 좋게 기운다.
* switch 의 롱 장부는 단순화(4 시간 단위, funding · 비상 스톱 · 정지선 없음)라 절대 숫자가
  정식 O 연구보다 높다 — '롱만' 대비 차이만 본다.
"""

from __future__ import annotations

import bisect
import collections
import math
import random
import sqlite3
import statistics
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import scripts.run_alt_short_230 as alt
import scripts.run_short_search_229 as s229

from auto_trading.backtest.overlay import OverlayConfig
from auto_trading.backtest.trend import target_weight
from auto_trading.research.short_search_229 import (
    FEE,
    STOP_SLIP,
    Coin,
    Trade,
    TradeCache,
    run_config,
)

REPO = Path(__file__).resolve().parent.parent
DB = REPO / "data" / "trading.db"
H4, DAY = 4 * 3_600_000, 86_400_000
H1 = alt.RULES["H1"]
COSTS = {
    "지정가 진입 · 손절 테이커 0.05%+미끄럼 0.05%": (0.0002, 0.0002, 0.0005, 0.0005),
    "시장가 0.05% + 미끄럼 0": (0.0005, 0.0005, 0.0005, 0.0),
    "시장가 0.05% + 미끄럼 0.03%": (0.0008, 0.0008, 0.0008, 0.0003),
    "시장가 0.05% + 미끄럼 0.05%": (0.0010, 0.0010, 0.0010, 0.0005),
    "시장가 0.05% + 미끄럼 0.10%": (0.0015, 0.0015, 0.0015, 0.0010),
    "시장가 0.05% + 미끄럼 0.20%": (0.0025, 0.0025, 0.0025, 0.0020),
    "230 판정 비용(0.095% + 손절 미끄럼 0.2%)": (FEE, FEE, FEE, STOP_SLIP),
}
SWITCH_SHORT_COST = (0.0008, 0.0008, 0.0008, 0.0003)
LONG_FEE = 0.0008


def _week(x: Trade) -> str:
    return datetime.fromtimestamp(x.entry_t / 1000, UTC).strftime("%G-%V")


def _trades(coins: Sequence[Coin], cost: tuple[float, float, float, float]) -> list[Trade]:
    c = TradeCache(coins)
    c.cost = cost
    return run_config(c, *H1, alt.FROM, alt.TO)


def _line(trades: Sequence[Trade]) -> str:
    r = [x.ret for x in trades]
    weeks: dict[str, float] = collections.defaultdict(float)
    for x in trades:
        weeks[_week(x)] += x.ret
    w = list(weeks.values())
    t = statistics.fmean(r) / statistics.stdev(r) * math.sqrt(len(r))
    tw = statistics.fmean(w) / statistics.stdev(w) * math.sqrt(len(w))
    mean = statistics.fmean(r) * 100
    return f"n={len(r)} 거래당 {mean:+.3f}% t={t:.2f} 주 묶음 t={tw:.2f}(주 {len(w)})"


def cluster() -> None:
    sets = {"알트 15 (230)": [alt._coin(s) for s in alt.COINS], "BTC·ETH": s229.load()}
    for label, coins in sets.items():
        print(f"===== {label}")
        for name, cost in COSTS.items():
            print(f"{name:44s} {_line(_trades(coins, cost))}")
        real = _trades(coins, COSTS["지정가 진입 · 손절 테이커 0.05%+미끄럼 0.05%"])
        years: dict[int, list[float]] = collections.defaultdict(list)
        for x in real:
            years[datetime.fromtimestamp(x.entry_t / 1000, UTC).year].append(x.ret)
        print(
            "해마다 합(현실 비용):", {y: round(sum(v) * 100, 1) for y, v in sorted(years.items())}
        )
        r = sorted((x.ret for x in real), reverse=True)
        for k in (len(r) // 100, len(r) // 20):
            print(f"상위 {k} 건 빼면 거래당 {statistics.fmean(r[k:]) * 100:+.3f}%")
        events = sorted([(x.entry_t, 1) for x in real] + [(x.exit_t, -1) for x in real])
        cur = top = 0
        for _, d in events:
            cur += d
            top = max(top, cur)
        print("동시 보유 최대", top)
        years_n = (alt.TO - alt.FROM) / DAY / 365.25
        for lab, scale in (("코인별 1/N 몫", 1 / len(coins)), ("코인마다 전체 자본의 0.5 배", 1.0)):
            eq = peak = 1.0
            mdd = 0.0
            for x in sorted(real, key=lambda z: z.exit_t):
                eq *= 1 + x.ret * scale
                peak = max(peak, eq)
                mdd = max(mdd, 1 - eq / peak)
            print(f"계좌 {lab}: 총 {eq - 1:+.1%} 연 {eq ** (1 / years_n) - 1:+.2%} MDD {mdd:.1%}")


def regime() -> None:
    coins = [alt._coin(s) for s in alt.COINS]
    cost = COSTS["지정가 진입 · 손절 테이커 0.05%+미끄럼 0.05%"]
    c = TradeCache(coins)
    c.cost = cost
    h1 = run_config(c, *H1, alt.FROM, alt.TO)
    need = collections.Counter(x.coin for x in h1)
    rng = random.Random(230)
    means = []
    for _ in range(300):
        rs: list[float] = []
        for k in coins:
            reg = k.regime["R5"]
            lo, hi = bisect.bisect_left(k.t, alt.FROM), bisect.bisect_left(k.t, alt.TO)
            pool = [i for i in range(lo, hi - 1) if reg[i]]
            busy, got = -1, 0
            for i in sorted(rng.sample(pool, min(len(pool), need[k.name] * 3))):
                if k.t[i + 1] < busy:
                    continue
                tr = c.get(k.name, i, "E5", "S3")
                if tr is None:
                    continue
                rs.append(tr.ret)
                busy, got = tr.exit_t, got + 1
                if got >= need[k.name]:
                    break
        means.append(statistics.fmean(rs))
    m = statistics.fmean(x.ret for x in h1)
    print(f"H1 거래당 {m * 100:+.3f}%")
    print(
        f"같은 국면 안 무작위 300 번: 중앙 {statistics.median(means) * 100:+.3f}% · "
        f"H1 보다 낮은 비율 {sum(1 for x in means if x < m) / len(means):.0%}"
    )
    allr = []
    for k in coins:
        reg = k.regime["R5"]
        lo, hi = bisect.bisect_left(k.t, alt.FROM), bisect.bisect_left(k.t, alt.TO)
        busy = -1
        for i in range(lo, hi - 1):
            if not reg[i] or k.t[i + 1] < busy:
                continue
            tr = c.get(k.name, i, "E5", "S3")
            if tr is None:
                continue
            allr.append(tr.ret)
            busy = tr.exit_t
    print(
        f"국면만(비어 있을 때마다 숏): {len(allr)} 건 거래당 {statistics.fmean(allr) * 100:+.3f}%"
    )


def switch() -> None:
    coins = s229.load()
    by = {k.name: k for k in coins}
    tcfg = OverlayConfig().trend
    spot: dict[str, list[tuple[int, float]]] = {}
    with sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True) as db:
        for name in by:
            spot[name] = sorted(
                (int(ms), float(cl))
                for ms, cl in db.execute(
                    "SELECT open_ms, close FROM candles WHERE exchange='binance_spot'"
                    " AND symbol=? AND timeframe='1d'",
                    (name,),
                )
            )
    shorts = _trades(coins, SWITCH_SHORT_COST)
    times = sorted(set(by["BTCUSDT"].t) & set(by["ETHUSDT"].t))
    times = [t for t in times if alt.FROM <= t < alt.TO]
    idx = {n: {t: i for i, t in enumerate(by[n].t)} for n in by}
    cache: dict[tuple[str, int], float] = {}

    def tgt(name: str, day: int) -> float:
        if (name, day) not in cache:
            s = spot[name]
            j = bisect.bisect_left([x[0] for x in s], day)
            cache[(name, day)] = target_weight([x[1] for x in s[:j]], tcfg) or 0.0
        return cache[(name, day)]

    spans = collections.defaultdict(list)
    for x in shorts:
        spans[x.coin].append((x.entry_t, x.exit_t))
    exits = collections.defaultdict(list)
    for x in shorts:
        exits[x.exit_t].append(x)

    def run(k_mult: float, mode: str | None, start: int) -> tuple[float, float, float]:
        eq = peak = 1.0
        mdd = 0.0
        w_prev = dict.fromkeys(by, 0.0)
        ts = [t for t in times if t >= start]
        for t in ts:
            day = t - t % DAY
            r = 0.0
            for n, k in by.items():
                i = idx[n][t]
                w = k_mult * tgt(n, day)
                if mode in ("switch", "flat") and any(a <= t < b for a, b in spans[n]):
                    w = 0.0
                r += w * (k.c[i] / k.c[i - 1] - 1) - abs(w - w_prev[n]) * LONG_FEE
                w_prev[n] = w
            if mode in ("add", "switch"):  # 이 봉 안에서 닫힌 숏의 수익을 더한다
                for x in [y for e_t, ys in exits.items() if t < e_t <= t + H4 for y in ys]:
                    r += x.ret
            eq *= 1 + r
            peak = max(peak, eq)
            mdd = max(mdd, 1 - eq / peak)
        years = (ts[-1] - ts[0]) / DAY / 365.25
        return eq - 1, eq ** (1 / years) - 1, mdd

    held = [tgt(x.coin, x.entry_t - x.entry_t % DAY) for x in shorts]
    print(f"H1 BTC·ETH 숏 {len(shorts)} 건 · 롱 비중 0 일 때 {sum(1 for h in held if h == 0)} 건")
    for start, label in ((alt.FROM, "2020-06 ~ 2026-08"), (alt.SPLIT, "2024-01 ~ 2026-08")):
        print(f"===== {label}")
        for km in (1.0, 2.0, 3.0):
            for name, mode in (
                ("롱만", None),
                ("롱 + 숏 더하기", "add"),
                ("신호 때 롱 끄기만", "flat"),
                ("신호 때 롱 끄고 숏", "switch"),
            ):
                tot, cagr, mdd = run(km, mode, start)
                head = f"×{km:.0f} {name:16s}"
                print(f"{head} 총 {tot:+.0%} 연 {cagr * 100:5.1f}% MDD {mdd * 100:4.1f}%")


def main(argv: list[str]) -> int:
    commands: dict[str, Any] = {"cluster": cluster, "regime": regime, "switch": switch}
    if len(argv) != 1 or argv[0] not in commands:
        raise SystemExit("usage: python -m scripts.run_h1_extra_231 cluster | regime | switch")
    commands[argv[0]]()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
