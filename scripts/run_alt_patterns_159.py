"""159 — 코인 알트 4 시간봉 반전형 패턴(IHS · DBOT) × 거래량 추세 (IDEAS 22 후보 B).

사용 예::

    python -m scripts.run_alt_patterns_159 count    # 사건 수만 (수익 없음)
    python -m scripts.run_alt_patterns_159 verdict  # 1 회 판정

* 사용자 (2026-10-07): 158(국장, 후보 A) REJECT 뒤 "한번 해줘 B".
* 종목군 · 자료: 157 과 같다 — 그달 말 30 일 평균 거래대금 상위 20(폐지 포함, KB 로 이미 본 10 종목
  제외) 이 다음 달 종목군, Binance 현물 공식 4 시간봉(157 자료 해시 그대로).
* 패턴: ``research/patterns_lmw.py`` 그대로(창 38 봉 · 지연 3 · 대역폭 1.25 봉 · 1.5% · DBOT 22 봉).
  158 과 같은 '봉 수' 정의 — 시간 환산 없음. 탐지 봉이 그달 종목군이어야 하고, 창 38 봉이 4 시간
  간격으로 끊김 없이 이어져야 한다.
* 거래량 추세: 창 앞 · 뒤 절반 평균 (거래량 × 종가) 비교, 1.2 배(LMW).
* 한 페어 한 번에 하나: 사건 뒤 31 봉(다음 봉 진입 + 30 봉 보유) 동안 다시 세지 않는다.
* 진입: 다음 봉부터 최대 5 봉, 한도 = 직전 봉 종가 × 1.005. 저가 < 한도면 체결,
  값 = min(한도, 시가) (158 교훈 — '한도 값 그대로' 쓰지 않는다).
* 청산: 체결 봉 + 30 봉 마감 뒤 다음 봉부터 한도 = 직전 종가 × 0.995, 고가 > 한도면
  값 = max(한도, 시가), 봉마다 다시. 자료가 끝난 폐지 페어는 마지막 종가.
* 비용: 편도 0.15%(현물 판정 기준), 참고 줄 0.11%.
* 기준: 체결 봉 시가 → 청산 봉 시가, 체결 봉 시각에 그달 종목군인 페어 동일 가중
  (폐지는 마지막 종가).
* 판정 SE: 탐지 시각 30 봉(5 일) 묶음 합의 Bartlett(시차 1) 군집 SE — 158 의 ``cluster_se``.
"""

from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import math
import random
import statistics
import sys
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from scripts.count_kr_patterns import volume_trend  # noqa: E402
from scripts.run_kb_alts_156 import load_4h  # noqa: E402
from scripts.run_kb_univ_157 import EXPECTED_DATA, UNIV, data_hash, member_months  # noqa: E402
from scripts.verdict_kr_patterns import cluster_se, summary_stats  # noqa: E402

from auto_trading.backtest.ema_box import Bars  # noqa: E402
from auto_trading.research.patterns_lmw import detect  # noqa: E402

BAR_MS = 4 * 3600 * 1000
WINDOW, LAG, HOLD = 38, 3, 30
BUSY = HOLD + 1
MAX_BUY_TRIES = 5
BUY_PAD, SELL_PAD = 1.005, 0.995
COST, COST_REF = 0.0015, 0.0011
START = "2018-01-01"
END_MS = int(datetime(2026, 9, 1, tzinfo=UTC).timestamp() * 1000)
SPLIT = "2022-01-01"
SEED = 20261007
RANDOM_DRAWS = 100
EXPECTED_EVENTS_SHA = "1e28e1806a2fbc03a68de791ca2d3ad96616d330ad6e44494f52bcbf6409be25"
EXPECTED_CLEAN = 3_230


def _month(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m")


def _day(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m-%d")


def eligible(b: Bars, i: int, months: set[str]) -> bool:
    """칸 i 가 창 끝이 될 수 있나 — 종목군 · 38 봉 연속 · 가격 · 거래량 양수 (순수)."""
    if i < WINDOW - 1 or _month(b.t[i]) not in months or _day(b.t[i]) < START:
        return False
    lo = i - WINDOW + 1
    if b.t[i] - b.t[lo] != (WINDOW - 1) * BAR_MS:
        return False
    return min(b.c[lo : i + 1]) > 0 and min(b.v[lo : i + 1]) > 0


def scan_pair(b: Bars, months: set[str]) -> tuple[list[dict[str, Any]], int]:
    events: list[dict[str, Any]] = []
    n_ok = 0
    free_from = 0
    for i in range(WINDOW - 1, len(b.t)):
        if i < free_from or not eligible(b, i, months):
            continue
        n_ok += 1
        lo = i - WINDOW + 1
        closes = list(b.c[lo : i + 1])
        found = detect(closes, LAG)
        if not found:
            continue
        found.sort(key=lambda q: q.kind != "IHS")
        q = found[0]
        value = [b.v[k] * b.c[k] for k in range(lo, i + 1)]
        events.append(
            {
                "t": b.t[i],
                "detect": _day(b.t[i]),
                "kind": q.kind,
                "points": [b.t[lo + k] for k in q.points],
                "volume": volume_trend(value),
            }
        )
        free_from = i + BUSY
    return events, n_ok


@dataclass(frozen=True, slots=True)
class Trade:
    fill: int
    entry: float
    exit_i: int
    exit: float
    delisted: bool


def simulate_trade(b: Bars, i: int) -> Trade | str | None:
    """None = 미체결, "incomplete" = 자료 끝(폐지 아님)까지 청산 못 함 (순수)."""
    n = len(b.t)
    fill, entry = -1, math.nan
    for k in range(1, MAX_BUY_TRIES + 1):
        j = i + k
        if j >= n:
            break
        lim = b.c[j - 1] * BUY_PAD
        if b.low[j] < lim:
            fill, entry = j, min(lim, b.o[j])
            break
    if fill < 0:
        return None
    for k in range(fill + HOLD + 1, n):
        lim = b.c[k - 1] * SELL_PAD
        if b.h[k] > lim:
            return Trade(fill, entry, k, max(lim, b.o[k]), False)
    if b.t[-1] + BAR_MS < END_MS - 7 * 24 * 3600 * 1000:  # 기간 끝 1 주 전보다 먼저 끝남 = 폐지
        return Trade(fill, entry, n - 1, b.c[n - 1], True)
    return "incomplete"


def net_return(tr: Trade, cost: float = COST) -> float:
    return tr.exit * (1 - cost) / (tr.entry * (1 + cost)) - 1


class Universe:
    def __init__(self) -> None:
        union = json.loads((UNIV / "union.json").read_text(encoding="utf-8"))
        self.months = member_months()
        self.bars: dict[str, Bars] = {s: load_4h(s) for s in sorted(union)}
        self.idx: dict[str, dict[int, int]] = {
            s: {t: k for k, t in enumerate(b.t)} for s, b in self.bars.items()
        }
        self._bench: dict[tuple[int, int], float] = {}

    def price_at(self, s: str, t: int) -> float:
        """시각 t 봉의 시가, 없으면(폐지·결측) t 이전 마지막 종가."""
        b = self.bars[s]
        k = self.idx[s].get(t)
        if k is not None:
            return b.o[k]
        j = bisect.bisect_left(b.t, t) - 1
        return b.c[j] if j >= 0 else math.nan

    def bench(self, t0: int, t1: int) -> float:
        key = (t0, t1)
        if key not in self._bench:
            m = _month(t0)
            rets = []
            for s, b in self.bars.items():
                if m not in self.months.get(s, set()) or t0 not in self.idx[s]:
                    continue
                rets.append(self.price_at(s, t1) / b.o[self.idx[s][t0]] - 1)
            self._bench[key] = statistics.fmean(rets) if rets else 0.0
        return self._bench[key]


def count(uni: Universe) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    events: list[dict[str, Any]] = []
    windows = 0
    for s, b in uni.bars.items():
        evs, n_ok = scan_pair(b, uni.months.get(s, set()))
        windows += n_ok
        for e in evs:
            e["pair"] = s
        events.extend(evs)
    events.sort(key=lambda e: (e["t"], e["pair"]))
    t0 = int(datetime(2018, 1, 1, tzinfo=UTC).timestamp() * 1000)
    blocks = Counter((e["t"] - t0) // (HOLD * BAR_MS) for e in events)
    top5 = sum(n for _, n in blocks.most_common(5))
    summary = {
        "eligible_windows": windows,
        "events": len(events),
        "cells": dict(sorted(Counter(f"{e['kind']}·{e['volume']}" for e in events).items())),
        "by_year": dict(sorted(Counter(e["detect"][:4] for e in events).items())),
        "pairs_with_events": len({e["pair"] for e in events}),
        "blocks_with_events": len(blocks),
        "events_per_block_mean": round(len(events) / max(1, len(blocks)), 2),
        "events_per_block_max": max(blocks.values(), default=0),
        "top5_block_share": round(top5 / max(1, len(events)), 3),
        "per_pair_year": {
            k: round(sum(1 for e in events if e["kind"] == k) / (windows / (6 * 365)), 3)
            for k in ("IHS", "DBOT")
        },
    }
    return events, summary


def evaluate(
    uni: Universe, rows: Sequence[Mapping[str, Any]], cost: float = COST
) -> tuple[list[dict[str, Any]], Counter[str]]:
    t0 = int(datetime(2018, 1, 1, tzinfo=UTC).timestamp() * 1000)
    done: list[dict[str, Any]] = []
    status: Counter[str] = Counter()
    for e in rows:
        b = uni.bars[e["pair"]]
        tr = simulate_trade(b, uni.idx[e["pair"]][e["t"]])
        if tr is None or isinstance(tr, str):
            status["unfilled" if tr is None else "incomplete"] += 1
            continue
        status["filled"] += 1
        net = net_return(tr, cost)
        exit_t = b.t[tr.exit_i] if not tr.delisted else b.t[tr.exit_i] + BAR_MS
        done.append(
            {
                **e,
                "block": (e["t"] - t0) // (HOLD * BAR_MS),
                "net": net,
                "excess": net - uni.bench(b.t[tr.fill], exit_t),
                "delisted": tr.delisted,
            }
        )
    return done, status


def random_control(
    uni: Universe, rows: Sequence[Mapping[str, Any]], actual: float
) -> dict[str, Any]:
    rng = random.Random(SEED)
    pool: dict[tuple[str, str], list[int]] = defaultdict(list)
    for s in {e["pair"] for e in rows}:
        b, months = uni.bars[s], uni.months.get(s, set())
        for i in range(len(b.t)):
            if eligible(b, i, months):
                pool[(s, _day(b.t[i])[:4])].append(b.t[i])
    means = []
    for _ in range(RANDOM_DRAWS):
        fake = [
            {**e, "t": rng.choice(pool[(e["pair"], e["detect"][:4])])}
            for e in rows
            if pool.get((e["pair"], e["detect"][:4]))
        ]
        done, _ = evaluate(uni, fake)
        means.append(statistics.fmean(r["excess"] for r in done))
    means.sort()
    return {
        "draws": RANDOM_DRAWS,
        "median": round(means[len(means) // 2], 5),
        "p90": round(means[int(0.9 * len(means))], 5),
        "actual_rank_above": sum(1 for m in means if actual > m),
    }


def verdict(uni: Universe, rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    done, status = evaluate(uni, rows)
    xs, bl = [r["excess"] for r in done], [r["block"] for r in done]
    main = summary_stats(xs, bl)

    def part(cond: Any) -> dict[str, Any]:
        sel = [r for r in done if cond(r)]
        return summary_stats([r["excess"] for r in sel], [r["block"] for r in sel])

    halves = {
        "first": part(lambda r: r["detect"] < SPLIT),
        "second": part(lambda r: r["detect"] >= SPLIT),
    }
    t1 = main["lower"] > 0
    t2 = halves["first"]["mean"] > 0 and halves["second"]["mean"] > 0
    up, dn = part(lambda r: r["volume"] == "up"), part(lambda r: r["volume"] == "down")
    diff, diff_se = up["mean"] - dn["mean"], math.hypot(up["se"], dn["se"])
    worst = sorted(done, key=lambda r: r["excess"])
    delisted = [r for r in done if r["delisted"]]
    ref, _ = evaluate(uni, rows, COST_REF)
    by_year: dict[str, list[float]] = defaultdict(list)
    for r in done:
        by_year[r["detect"][:4]].append(r["excess"])
    return {
        "worst_first": {
            "worst_event": {k: worst[0][k] for k in ("pair", "detect", "kind", "excess", "net")},
            "bottom_5pct_excess": round(worst[int(0.05 * len(worst))]["excess"], 4),
            "delisted_in_hold": len(delisted),
            "delisted_mean_excess": round(statistics.fmean(r["excess"] for r in delisted), 4)
            if delisted
            else None,
        },
        "verdict": "HISTORICAL_SUPPORT" if t1 and t2 else "REJECT",
        "T1_lower_2se_gt0": t1,
        "T2_both_halves_gt0": t2,
        "H1": main,
        "halves": halves,
        "H2_volume_up_minus_down": {
            "diff": round(diff, 5),
            "se": round(diff_se, 5),
            "lower_2.24se": round(diff - 2.24 * diff_se, 5),
            "up": up,
            "down": dn,
        },
        "status": dict(status),
        "by_kind": {k: part(lambda r, k=k: r["kind"] == k) for k in ("IHS", "DBOT")},
        "by_volume": {k: part(lambda r, k=k: r["volume"] == k) for k in ("up", "down", "none")},
        "by_year": {y: round(statistics.fmean(v), 5) for y, v in sorted(by_year.items())},
        "median_excess": round(statistics.median(xs), 5),
        "median_net": round(statistics.median(r["net"] for r in done), 5),
        "cost_ref_0.11pct": summary_stats([r["excess"] for r in ref], [r["block"] for r in ref]),
        "random_control": random_control(uni, rows, main["mean"]),
        "note_cluster_se": round(cluster_se(xs, bl), 6),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("count", "verdict"))
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "reports/indicator_b")
    args = parser.parse_args(argv)
    sha, nfiles = data_hash()
    if EXPECTED_DATA and sha != EXPECTED_DATA:
        raise SystemExit(f"자료 해시 불일치 {sha}")
    uni = Universe()
    args.out.mkdir(parents=True, exist_ok=True)
    path = args.out / "events.jsonl"
    if args.mode == "count":
        events, summary = count(uni)
        path.write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
        summary.update(
            {
                "data_sha": sha,
                "data_files": nfiles,
                "events_sha": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
        (args.out / "summary.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
        sys.stdout.write(json.dumps(summary, ensure_ascii=False, indent=1) + "\n")
        return 0
    if hashlib.sha256(path.read_bytes()).hexdigest() != EXPECTED_EVENTS_SHA:
        raise SystemExit("사건 파일 해시 불일치")
    rows = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x]
    if len(rows) != EXPECTED_CLEAN:
        raise SystemExit(f"사건 수 {len(rows)} ≠ {EXPECTED_CLEAN}")
    result = verdict(uni, rows)
    result.update({"data_sha": sha, "events_sha": EXPECTED_EVENTS_SHA})
    (args.out / "verdict.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    sys.stdout.write(json.dumps(result, ensure_ascii=False, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            _reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
