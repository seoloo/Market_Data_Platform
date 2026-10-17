"""문서 257 — 255 의 반대(주말 움직임 거스르기)를 안 본 자료(2026-09 ~ 10)로 1차 확인. 1회 판정.

사용 예::

    python -m scripts.run_weekend_fade_257 fetch   # 2026-08-25 ~ 10-13 30 분봉 (수익 안 봄)
    python -m scripts.run_weekend_fade_257 run     # 깨끗한 트리 · 자료 해시 · 판정 파일 없음일 때만

시각 · 종목 · 비용은 255 그대로(F 금 미국 종가, S 일 19:30 UTC, O 월 미국 시가),
거래 = −sign(w) × f − 0.10%. 주말 = 금 2026-09-11 ~ 10-09 (9-04 는 월 노동절 휴장).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import statistics
import sys
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from scripts.run_a1_train import git_state
from scripts.run_weekend_perp_255 import API, COST, HALF_HOUR_MS, SETTLE_DAYS, points, t_stat

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "data" / "research" / "weekend_fade_257"
OUT = REPO / "reports" / "weekend_fade_257"
EXPECTED_DATA: str | None = "0e9755c68962ea37311fd16cdf28e26bf15859b7c2c7012e8d56ee69cb705b3e"
"""fetch 뒤 문서 257 §1 에 적고 여기 고정한다."""
FETCH_FROM = dt.datetime(2026, 8, 25, tzinfo=dt.UTC)
FETCH_TO = dt.datetime(2026, 10, 14, tzinfo=dt.UTC)
FRIDAYS = tuple(dt.date(2026, 9, 11) + dt.timedelta(days=7 * k) for k in range(5))
"""09-11 · 18 · 25 · 10-02 · 09. 월요일은 모두 미국 정규장(10-12 콜럼버스 데이는 주식 개장)."""
WIN_MIN = 4
BIG = 0.01


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
    (DATA / "symbols.json").write_text(json.dumps(syms, indent=0), encoding="utf-8")
    end_ms = int(FETCH_TO.timestamp() * 1000)
    for sym, onboard in sorted(syms.items()):
        if (DATA / f"{sym}.json").exists():
            continue
        rows: list[list[Any]] = []
        t = max(onboard, int(FETCH_FROM.timestamp() * 1000))
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


def fade(p: Mapping[str, float]) -> dict[str, float]:
    w = p["S"] / p["F"] - 1
    f = p["O"] / p["S"] - 1
    return {"w": w, "f": f, "trade": -math.copysign(1, w) * f - COST}


def judge(net: list[float]) -> str:
    """사전 등록 §3 (순수). 값은 주말 평균들."""
    if not net or statistics.fmean(net) <= 0:
        return "불리"
    if sum(1 for x in net if x > 0) >= WIN_MIN:
        return "통과"
    return "불명"


def collect() -> dict[str, dict[str, dict[str, float]]]:
    syms = json.loads((DATA / "symbols.json").read_text(encoding="utf-8"))
    out: dict[str, dict[str, dict[str, float]]] = {}
    for sym, onboard in sorted(syms.items()):
        path = DATA / f"{sym}.json"
        if not path.exists():
            continue
        start = dt.datetime.fromtimestamp(onboard / 1000, dt.UTC).date()
        bars = {int(r[0]): (float(r[1]), float(r[4])) for r in json.loads(path.read_text())}
        for fri in FRIDAYS:
            if fri < start + dt.timedelta(days=SETTLE_DAYS):
                continue
            p = points(bars, fri)
            if p is not None and p["S"] != p["F"]:
                out.setdefault(fri.isoformat(), {})[sym] = fade(p)
    return out


def run() -> dict[str, Any]:
    target = OUT / "result.json"
    if target.exists():
        raise SystemExit(f"이미 판정했다: {target}")
    sha, dirty = git_state()
    if dirty:
        raise SystemExit("작업 트리가 깨끗하지 않다 — 커밋 뒤에 실행")
    if EXPECTED_DATA is None or data_hash() != EXPECTED_DATA:
        raise SystemExit("자료 해시가 문서 257 §1 과 다르다")
    by = collect()
    weeks = sorted(by)
    net = [statistics.fmean(o["trade"] for o in by[w].values()) for w in weeks]
    every = [o for w in weeks for o in by[w].values()]
    big = [o["trade"] for o in every if abs(o["w"]) >= BIG]
    out = {
        "git": sha,
        "data": EXPECTED_DATA,
        "verdict": judge(net),
        "weekends": len(weeks),
        "obs": len(every),
        "net_mean": statistics.fmean(net) if net else None,
        "net_t": round(t_stat(net), 2),
        "by_week": dict(zip(weeks, net, strict=True)),
        "big_n": len(big),
        "big_mean": statistics.fmean(big) if big else None,
        "corr_w_f": statistics.correlation([o["w"] for o in every], [o["f"] for o in every])
        if len(every) > 2
        else None,
        "note": "문서 257 1 회. 255 반대 규칙, 2026-09-11 ~ 10-09 주말(안 본 자료).",
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
        print(json.dumps(run(), indent=1))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
