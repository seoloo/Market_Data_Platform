"""문서 255 — 바이낸스 주식 무기한 선물의 주말 움직임이 월요일 미국 시가까지 이어지나. 1회 판정.

사용 예::

    python -m scripts.run_weekend_perp_255 fetch   # 30 분봉 받기 (공개 API, 수익 안 봄)
    python -m scripts.run_weekend_perp_255 run     # 깨끗한 트리 · 자료 해시 · 판정 파일 없음일 때만

* 종목: 거래소 정보의 TradFi EQUITY 무기한(USDT), 상장 뒤 7 일부터.
* 주말마다: 금 미국 종가 F(동부 15:30 봉 종가), 일 UTC 19:30 봉 종가 S(선물 시장 열리기 전),
  월 미국 시가 O(동부 09:30 봉 시가), 월 종가 C.
* w = S ÷ F − 1 (주말 움직임), f = O ÷ S − 1 (그 뒤 월 시가까지), 거래 = sign(w) × f − 비용 0.10%.
* 주말마다 종목 평균을 한 관찰로(같은 주말 종목끼리 같이 움직임) t. 미국 휴장이 낀 주말은 뺀다.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import statistics
import sys
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from scripts.run_a1_train import git_state

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "data" / "research" / "weekend_perp_255"
OUT = REPO / "reports" / "weekend_perp_255"
API = "https://fapi.binance.com"
ET = ZoneInfo("America/New_York")
EXPECTED_DATA: str | None = None
"""fetch 뒤 문서 255 §1 에 적고 여기 고정한다."""

TO = "2026-08-31"
SPLIT = "2026-06-01"
COST = 0.001
SETTLE_DAYS = 7
T_MIN = 2.0
HOLIDAYS = frozenset(
    {"2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25", "2026-06-19", "2026-07-03"}
)
"""2026 미국 휴장(9 월 전). 금 · 월이 휴장이면 그 주말을 뺀다."""
HALF_HOUR_MS = 30 * 60 * 1000


# ------------------------------------------------------------------ 자료


def fetch() -> None:
    import httpx

    DATA.mkdir(parents=True, exist_ok=True)
    c = httpx.Client(timeout=30)
    info = c.get(f"{API}/fapi/v1/exchangeInfo").json()
    syms = {
        s["symbol"]: int(s["onboardDate"])
        for s in info["symbols"]
        if s.get("underlyingType") == "EQUITY" and s["quoteAsset"] == "USDT"
    }
    end_ms = int(dt.datetime(2026, 9, 1, tzinfo=dt.UTC).timestamp() * 1000)
    (DATA / "symbols.json").write_text(json.dumps(syms, indent=0), encoding="utf-8")
    # 요청이 많아 막히면(429) 쉬었다 잇는다. 이미 받은 종목은 건너뛴다.
    for sym, start in sorted(syms.items()):
        if (DATA / f"{sym}.json").exists():
            continue
        rows: list[list[Any]] = []
        t = start
        while t < end_ms:
            r = c.get(
                f"{API}/fapi/v1/klines",
                params={"symbol": sym, "interval": "30m", "startTime": t, "limit": 1500},
            )
            if r.status_code in (418, 429):
                time.sleep(60)
                continue
            r.raise_for_status()
            got = r.json()
            if not got:
                break
            rows.extend(x[:5] for x in got if x[0] < end_ms)
            t = int(got[-1][0]) + HALF_HOUR_MS
            time.sleep(0.5)
        (DATA / f"{sym}.json").write_text(json.dumps(rows), encoding="utf-8")
    print(json.dumps({"symbols": len(syms), "hash": data_hash()}, ensure_ascii=False))


def data_hash() -> str:
    h = hashlib.sha256()
    for p in sorted(DATA.glob("*.json")):
        h.update(p.name.encode())
        h.update(p.read_bytes())
    return h.hexdigest()


def load_bars(sym: str) -> dict[int, tuple[float, float]]:
    """봉 시작 ms → (시가, 종가)."""
    rows = json.loads((DATA / f"{sym}.json").read_text(encoding="utf-8"))
    return {int(r[0]): (float(r[1]), float(r[4])) for r in rows}


# ------------------------------------------------------------------ 시각


def et_ms(day: dt.date, hh: int, mm: int) -> int:
    return int(dt.datetime(day.year, day.month, day.day, hh, mm, tzinfo=ET).timestamp() * 1000)


def utc_ms(day: dt.date, hh: int, mm: int) -> int:
    return int(dt.datetime(day.year, day.month, day.day, hh, mm, tzinfo=dt.UTC).timestamp() * 1000)


def weekends(first: dt.date, last: dt.date) -> list[dt.date]:
    """금요일 날짜들 (월요일이 ``last`` 이하, 금 · 월 휴장 아님)."""
    d = first + dt.timedelta(days=(4 - first.weekday()) % 7)
    out = []
    while d + dt.timedelta(days=3) <= last:
        mon = d + dt.timedelta(days=3)
        if d.isoformat() not in HOLIDAYS and mon.isoformat() not in HOLIDAYS:
            out.append(d)
        d += dt.timedelta(days=7)
    return out


def points(bars: Mapping[int, tuple[float, float]], fri: dt.date) -> dict[str, float] | None:
    """F · S · O · C. 봉이 하나라도 없으면 None."""
    sun, mon = fri + dt.timedelta(days=2), fri + dt.timedelta(days=3)
    keys = {
        "F": (et_ms(fri, 15, 30), 1),
        "S": (utc_ms(sun, 19, 30), 1),
        "O": (et_ms(mon, 9, 30), 0),
        "C": (et_ms(mon, 15, 30), 1),
    }
    out = {}
    for k, (ms, col) in keys.items():
        b = bars.get(ms)
        if b is None:
            return None
        out[k] = b[col]
    return out


# ------------------------------------------------------------------ 계산


def obs(p: Mapping[str, float]) -> dict[str, float]:
    w = p["S"] / p["F"] - 1
    f = p["O"] / p["S"] - 1
    return {"w": w, "f": f, "g": p["C"] / p["O"] - 1, "trade": math.copysign(1, w) * f - COST}


def t_stat(xs: Sequence[float]) -> float:
    if len(xs) < 3:
        return 0.0
    sd = statistics.stdev(xs)
    return statistics.fmean(xs) / (sd / math.sqrt(len(xs))) if sd > 0 else 0.0


def judge(
    gross: float, net: Sequence[float], first: Sequence[float], second: Sequence[float]
) -> str:
    """사전 등록 §3 (순수). 값은 주말 평균들."""
    if gross <= 0:
        return "불리"
    ok = bool(net) and statistics.fmean(net) > 0 and t_stat(net) >= T_MIN
    ok = ok and bool(first) and bool(second)
    if ok and statistics.fmean(first) > 0 and statistics.fmean(second) > 0:
        return "지지"
    return "불명"


def collect() -> dict[str, dict[str, dict[str, float]]]:
    """주말(금요일 날짜) → 종목 → 관찰."""
    syms = json.loads((DATA / "symbols.json").read_text(encoding="utf-8"))
    last = dt.date.fromisoformat(TO)
    out: dict[str, dict[str, dict[str, float]]] = {}
    for sym, onboard in sorted(syms.items()):
        if not (DATA / f"{sym}.json").exists():
            continue
        start = dt.datetime.fromtimestamp(onboard / 1000, dt.UTC).date()
        bars = load_bars(sym)
        for fri in weekends(start + dt.timedelta(days=SETTLE_DAYS), last):
            p = points(bars, fri)
            if p is not None and p["S"] != p["F"]:
                out.setdefault(fri.isoformat(), {})[sym] = obs(p)
    return out


def run() -> dict[str, Any]:
    target = OUT / "result.json"
    if target.exists():
        raise SystemExit(f"이미 판정했다: {target}")
    sha, dirty = git_state()
    if dirty:
        raise SystemExit("작업 트리가 깨끗하지 않다 — 커밋 뒤에 실행")
    if EXPECTED_DATA is None or data_hash() != EXPECTED_DATA:
        raise SystemExit("자료 해시가 문서 255 §1 과 다르다")
    by_week = collect()
    weeks = sorted(by_week)
    net = [statistics.fmean(o["trade"] for o in by_week[w].values()) for w in weeks]
    gross = statistics.fmean(n + COST for n in net) if net else 0.0
    first = [n for w, n in zip(weeks, net, strict=True) if w < SPLIT]
    second = [n for w, n in zip(weeks, net, strict=True) if w >= SPLIT]
    every = [o for w in weeks for o in by_week[w].values()]
    big = [o["trade"] for o in every if abs(o["w"]) >= 0.01]
    out = {
        "git": sha,
        "data": EXPECTED_DATA,
        "verdict": judge(gross, net, first, second),
        "weekends": len(weeks),
        "obs": len(every),
        "gross": gross,
        "net_mean": statistics.fmean(net) if net else None,
        "net_t": round(t_stat(net), 2),
        "first": statistics.fmean(first) if first else None,
        "second": statistics.fmean(second) if second else None,
        "ref": {
            "w_abs_mean": statistics.fmean(abs(o["w"]) for o in every) if every else None,
            "corr_w_f": statistics.correlation([o["w"] for o in every], [o["f"] for o in every])
            if len(every) > 2
            else None,
            "corr_w_g": statistics.correlation([o["w"] for o in every], [o["g"] for o in every])
            if len(every) > 2
            else None,
            "big_w_1pct_n": len(big),
            "big_w_1pct_mean": statistics.fmean(big) if big else None,
        },
        "by_week": dict(zip(weeks, net, strict=True)),
        "note": "문서 255 1 회 판정. 30 분봉, 2026-08-31 까지, 비용 왕복 0.10%.",
    }
    OUT.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else ""
    if cmd == "fetch":
        fetch()
        return 0
    if cmd == "run":
        print(json.dumps({k: v for k, v in run().items() if k != "by_week"}, indent=1))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
