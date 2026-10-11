# ruff: noqa: E501 — 한국어 설명 · 표 문자열이 길다
"""문서 212 §6 — ③ 비용 전 효과와 거래별 비용 문턱 (자리 · 위험 제한 없이 신호 사건마다). 주문 없음.

사건 = 신호 일 t 의 다음 날 00:00 UTC 뒤 첫 거래 있는 5분봉 시가에 코인 롱 · BTC 숏(beta × N) 진입, 72 시간 뒤 첫 5분봉 시가에 둘 다 청산.
같은 코인을 들고 있는 72 시간 동안 새 신호는 무시. 1 시간 안에 가격이 없으면 '체결 불가' 로 뺀다.
비용 = 0.0019 × (1 + beta), funding = 실제 정산(시각 · rate) × 그때 mark ÷ 진입가 (롱은 양수 rate 면 냄, BTC 숏은 받음).
"""

from __future__ import annotations

import csv
import io
import json
import random
import statistics
import zipfile
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from itertools import pairwise
from pathlib import Path
from typing import Any

from scripts.run_resid_rev_212 import DAY, M5, MIN, OUT, REPORT, VISION, Signal, load_daily, signals

HOLD = 72 * 3_600_000
WAIT = 3_600_000  # 첫 가격을 기다리는 최대 시간
COST_RT = 0.0019  # 다리마다 왕복 (편도 0.095% × 2)


def _rows(z: Path) -> list[list[str]]:
    with zipfile.ZipFile(z) as zf:
        return [
            r
            for r in csv.reader(io.TextIOWrapper(zf.open(zf.namelist()[0]), "utf-8"))
            if r and r[0][:1].isdigit()
        ]


class Intraday:
    """종목 · 달 단위로 5분봉 시가 · 거래량, mark 1시간 시가, funding 을 필요할 때 읽는다."""

    def __init__(self) -> None:
        self.k5: dict[tuple[str, str], dict[int, tuple[float, float, float]]] = {}
        self.mark: dict[tuple[str, str], dict[int, float]] = {}
        self.fund: dict[tuple[str, str], list[tuple[int, float, float]] | None] = {}

    def _k5(self, s: str, month: str) -> dict[int, tuple[float, float, float]]:
        key = (s, month)
        if key not in self.k5:
            z = VISION / "um" / s / "5m" / f"{s}-5m-{month}.zip"
            d: dict[int, tuple[float, float, float]] = {}
            if z.is_file():
                for r in _rows(z):
                    t = int(r[0])
                    d[t] = (float(r[1]), float(r[4]), float(r[7]))  # 시가 · 종가 · 거래대금
            self.k5[key] = d
        return self.k5[key]

    def first_open(self, s: str, t: int) -> tuple[int, float] | None:
        """t 이후(포함) 1 시간 안 첫 거래 있는 5분봉 (시각, 시가)."""
        x = t
        while x < t + WAIT:
            d = self._k5(s, _month(x))
            bar = d.get(x)
            if bar is not None and bar[2] > 0:
                return x, bar[0]
            x += M5
        return None

    def mark_at(self, s: str, t: int) -> float | None:
        hour = t // 3_600_000 * 3_600_000
        key = (s, _month(hour))
        if key not in self.mark:
            z = VISION / "um_mark" / s / "1h" / f"{s}-1h-{key[1]}.zip"
            self.mark[key] = {int(r[0]): float(r[1]) for r in _rows(z)} if z.is_file() else {}
        return self.mark[key].get(hour)

    def price_at(self, s: str, t: int) -> float | None:
        """mark 가 없을 때: 그 시각 직전에 끝난 5분봉 종가."""
        x = t // M5 * M5 - M5
        bar = self._k5(s, _month(x)).get(x)
        return bar[1] if bar else None

    def funding(self, s: str, month: str) -> list[tuple[int, float, float]] | None:
        """(정산 ms, 간격 시간, rate). 그 달 파일이 없으면 None (결측 — 0 이 아님)."""
        key = (s, month)
        if key not in self.fund:
            z = VISION / "um_funding" / s / f"{s}-fundingRate-{month}.zip"
            self.fund[key] = (
                [(int(r[0]), float(r[1]), float(r[2])) for r in _rows(z)] if z.is_file() else None
            )
        return self.fund[key]

    def funding_between(self, s: str, a: int, b: int) -> tuple[list[tuple[int, float]], bool]:
        """(a, b] 정산들과 결측 여부. 앞뒤 정산 사이 간격이 정해진 간격 × 1.5 를 넘으면 결측으로 본다."""
        months = sorted({_month(a - DAY), _month(a), _month(b), _month(b + DAY)})
        evs: list[tuple[int, float, float]] = []
        for m in months:
            f = self.funding(s, m)
            if f is None:
                if m in (_month(a), _month(b)):
                    return [], True
                continue
            evs.extend(f)
        evs.sort()
        inside = [(t, r) for t, _h, r in evs if a < t <= b]
        around = [e for e in evs if a - 9 * 3_600_000 <= e[0] <= b + 9 * 3_600_000]
        if len(around) < 2:
            return inside, True
        for (t0, h0, _), (t1, _h1, _) in pairwise(around):
            if t1 - t0 > h0 * 3_600_000 * 1.5 + 60_000:
                return inside, True
        return inside, False


def _month(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m")


@dataclass(frozen=True, slots=True)
class Event:
    day: str
    symbol: str
    z: float
    beta: float
    entry_ms: int
    pc0: float
    pc1: float
    pb0: float
    pb1: float
    gross: float
    cost: float
    fund_coin: float
    fund_btc: float
    mark_proxy: int

    @property
    def net(self) -> float:
        return self.gross - self.cost - self.fund_coin - self.fund_btc


def build_events(sigs: list[Signal], it: Intraday) -> tuple[list[Event], dict[str, int]]:
    out: list[Event] = []
    skip: dict[str, int] = defaultdict(int)
    busy_until: dict[str, int] = {}
    for sg in sorted(sigs, key=lambda x: (x.day, x.z, -x.qv_prev, x.symbol)):
        t = int(datetime.fromisoformat(sg.day).replace(tzinfo=UTC).timestamp() * 1000) + DAY
        if busy_until.get(sg.symbol, 0) > t:
            skip["같은 코인 보유 중"] += 1
            continue
        ec, eb = it.first_open(sg.symbol, t), it.first_open("BTCUSDT", t)
        if ec is None or eb is None:
            skip["진입 가격 없음(1 시간)"] += 1
            continue
        t0 = ec[0]
        xc, xb = it.first_open(sg.symbol, t0 + HOLD), it.first_open("BTCUSDT", t0 + HOLD)
        if xc is None or xb is None:
            skip["청산 가격 없음(폐지 · 정지)"] += 1
            continue
        fc, miss_c = it.funding_between(sg.symbol, t0, xc[0])
        fb, miss_b = it.funding_between("BTCUSDT", eb[0], xb[0])
        if miss_c or miss_b:
            skip["funding 결측"] += 1
            continue
        proxy = 0

        def pay(sym: str, evs: list[tuple[int, float]], p0: float) -> float:
            nonlocal proxy
            tot = 0.0
            for ft, rate in evs:
                mk = it.mark_at(sym, ft)
                if mk is None:
                    mk = it.price_at(sym, ft)
                    proxy += 1
                if mk is None:
                    raise RuntimeError(f"가격 없음 {sym} {ft}")
                tot += rate * mk / p0
            return tot

        f_coin = pay(sg.symbol, fc, ec[1])  # 롱: + 면 냄
        f_btc = -sg.beta * pay("BTCUSDT", fb, eb[1])  # 숏: + rate 면 받음 → 비용은 음수
        gross = (xc[1] / ec[1] - 1) - sg.beta * (xb[1] / eb[1] - 1)
        out.append(
            Event(
                sg.day,
                sg.symbol,
                sg.z,
                sg.beta,
                t0,
                ec[1],
                xc[1],
                eb[1],
                xb[1],
                gross,
                COST_RT * (1 + sg.beta),
                f_coin,
                f_btc,
                proxy,
            )
        )
        busy_until[sg.symbol] = t0 + HOLD
    return out, dict(skip)


def date_block_bootstrap(
    events: list[Event], field: str, reps: int = 2000, block_days: int = 7, seed: int = 212
) -> tuple[float, float, float]:
    """사건을 진입일로 묶고, 연속 7 일 묶음을 다시 뽑아 평균의 90% 구간. (같은 날 · 겹치는 보유 · 공유 BTC 를 같이 움직이게)"""
    by_day: dict[str, list[float]] = defaultdict(list)
    for e in events:
        v = e.net if field == "net" else getattr(e, field)
        by_day[e.day].append(v)
    if not by_day:
        return 0.0, 0.0, 0.0
    d0 = date.fromisoformat(min(by_day))
    n_days = (date.fromisoformat(max(by_day)) - d0).days + 1
    cal = [by_day.get((d0 + timedelta(days=k)).isoformat(), []) for k in range(n_days)]
    rng = random.Random(seed)
    means = []
    for _ in range(reps):
        vals: list[float] = []
        while len(vals) < len(events):
            st = rng.randrange(n_days)
            for k in range(block_days):
                vals.extend(cal[(st + k) % n_days])
        means.append(statistics.fmean(vals[: len(events)]))
    means.sort()
    allv = [x for c in cal for x in c]
    return statistics.fmean(allv), means[int(0.05 * reps)], means[int(0.95 * reps)]


def cmd_threshold() -> int:
    members = json.loads((OUT / "members.json").read_text(encoding="utf-8"))
    daily = load_daily()
    sigs = signals(daily, members)
    it = Intraday()
    events, skip = build_events(sigs, it)
    res: dict[str, Any] = {
        "made": datetime.now(UTC).isoformat(),
        "signals": len(sigs),
        "events": len(events),
        "skipped": skip,
    }
    for f in ("gross", "cost", "fund_coin", "fund_btc", "net"):
        m, lo, hi = date_block_bootstrap(events, f)
        res[f] = {"mean_bp": round(m * 1e4, 2), "ci90_bp": [round(lo * 1e4, 2), round(hi * 1e4, 2)]}
    res["beta_mean"] = round(statistics.fmean(e.beta for e in events), 3) if events else None
    res["share_gross_gt_cost_pct"] = (
        round(sum(1 for e in events if e.gross > e.cost) / len(events) * 100, 1) if events else None
    )
    res["mark_proxy_settlements"] = sum(e.mark_proxy for e in events)
    by_year: dict[str, list[Event]] = defaultdict(list)
    for e in events:
        by_year[e.day[:4]].append(e)
    res["by_year"] = {
        y: {
            "n": len(v),
            "gross_bp": round(statistics.fmean(x.gross for x in v) * 1e4, 1),
            "net_bp": round(statistics.fmean(x.net for x in v) * 1e4, 1),
        }
        for y, v in sorted(by_year.items())
    }
    by_sym: dict[str, list[Event]] = defaultdict(list)
    for e in events:
        by_sym[e.symbol].append(e)
    res["top_symbols_by_count"] = sorted(
        (
            (s, len(v), round(statistics.fmean(x.net for x in v) * 1e4, 1))
            for s, v in by_sym.items()
        ),
        key=lambda x: -x[1],
    )[:10]
    res["signal_days"] = len({e.day for e in events})
    res["decision"] = "진행(④)" if res["net"]["mean_bp"] > 0 else "비용 문턱상 진행 가치 낮음"
    REPORT.mkdir(parents=True, exist_ok=True)
    (REPORT / "threshold.json").write_text(
        json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    (REPORT / "threshold_events.json").write_text(
        json.dumps([{**asdict(e), "net": e.net} for e in events], ensure_ascii=False),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                k: res[k]
                for k in (
                    "signals",
                    "events",
                    "skipped",
                    "gross",
                    "cost",
                    "fund_coin",
                    "fund_btc",
                    "net",
                    "beta_mean",
                    "share_gross_gt_cost_pct",
                    "signal_days",
                    "decision",
                )
            },
            ensure_ascii=False,
            indent=1,
        )
    )
    return 0


__all__ = ["MIN", "Event", "Intraday", "build_events", "cmd_threshold", "date_block_bootstrap"]
