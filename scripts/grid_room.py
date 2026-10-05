"""문서 145 — 그리드가 설 자리가 있나 (Phase 0, 수익 백테스트 아님).

사용 예::

    python -m scripts.grid_room hash   # 현물 1분봉 zip 묶음 SHA-256 (측정 전 고정)
    python -m scripts.grid_room run    # 측정 한 번 → reports/grid_room/result.json

그리드는 간격 s 마다 사고판다. 가격을 간격 s 의 격자(로그 간격 δ = ln(1+s))에 올려, 닿은 격자점이
바뀔 때마다 한 걸음(+1 위 · −1 아래)을 적는다. 그리드 손익은 걸음 수 T 와 끝 위치 X 로
``N·s·(T − X²)/2`` (칸당 명목 N, 비용 전)이다 — 무작위 걸음이면 기대값 0.

* 되돌림 비율 p: 다음 걸음이 반대 방향인 비율. 무작위면 0.5.
* VR10: 걸음 10 개 묶음의 끝 위치 제곱 평균 ÷ 10. 무작위면 1, 되돌아오면 1 보다 작다.
* 걸음당 순이익(칸당 명목 대비) ``s·(1 − VR10)/2 − f`` — 손익분기 VR10 < 1 − 2f/s,
  되돌림만 있는 경우(마르코프) p* = s / (2(s − f)).

1분봉 안의 길: 양봉은 시가 → 저가 → 고가 → 종가, 음봉은 시가 → 고가 → 저가 → 종가.
같은 측정을 종가만 이은 길로도 한다(봉 안 되돌림을 빼는 보수적인 길).
"""

from __future__ import annotations

import hashlib
import itertools
import json
import math
import statistics
import sys
from array import array
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scripts.run_grid_upbit import VISION, load_minutes

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "reports" / "grid_room" / "result.json"
SYMBOLS = (
    "BTCUSDT",
    "ETHUSDT",
    "SOLUSDT",
    "XRPUSDT",
    "DOGEUSDT",
    "ADAUSDT",
    "BNBUSDT",
    "LINKUSDT",
    "LTCUSDT",
    "TRXUSDT",
)
SPACINGS = (0.005, 0.01, 0.02, 0.04)
FEE = 0.0005
"""판정 비용 = 업비트 원화 마켓 0.05% (137 이 고른 현물 그리드 장소). 필요조건 검사라 가장 싼 곳."""
REF_FEES = (0.001, 0.0015)
"""참고 줄: Binance 현물 0.1% · 프로젝트 보수 비용 0.15%."""
BLOCK = 10
LEVELS = 10
FROM, SPLIT, TO = "2018-01-01", "2022-01-01", "2026-09-01"
YEAR_MS = 365.25 * 86_400_000
EXPECTED_BUNDLE = "d9a0796e192cebd671ab78b95f0af27490ad3d6995fbd3351385e6b0c0963ea5"
"""``hash`` 뒤(측정 전) 고정한다."""


def _ms(text: str) -> int:
    return int(datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=UTC).timestamp() * 1000)


def ohlc_path(
    o: Sequence[float], h: Sequence[float], lo: Sequence[float], c: Sequence[float]
) -> array[float]:
    """로그 가격 길 — 봉마다 네 점."""
    out = array("d")
    log = math.log
    for a, b, d, e in zip(o, h, lo, c, strict=True):
        if e >= a:
            out.extend((log(a), log(d), log(b), log(e)))
        else:
            out.extend((log(a), log(b), log(d), log(e)))
    return out


def steps_on_lattice(
    path: Sequence[float], delta: float, per_bar: int = 1
) -> tuple[array[int], array[int]]:
    """(걸음 ±1, 걸음이 생긴 봉 번호). 닿은 격자점 ``m·delta`` 이 바뀔 때마다 한 걸음.

    칸 ``j`` = ``[j·δ, (j+1)·δ)``. 위로 칸 j → j' 이면 격자점 j+1 … j', 아래로면 j … j'+1 을
    지난다. 같은 점에 연달아 닿은 것은 한 번으로 친다.
    """
    steps, where = array("b"), array("l")
    if not path:
        return steps, where
    floor = math.floor
    inv = 1.0 / delta
    prev = floor(path[0] * inv)
    last: int | None = None
    for i in range(1, len(path)):
        cell = floor(path[i] * inv)
        if cell == prev:
            continue
        points = range(prev + 1, cell + 1) if cell > prev else range(prev, cell, -1)
        bar = i // per_bar
        for m in points:
            if m != last:
                if last is not None:
                    step = m - last
                    if step not in (1, -1):
                        raise AssertionError("이어진 격자점이 1 칸보다 멀다")
                    steps.append(step)
                    where.append(bar)
                last = m
        prev = cell
    return steps, where


def stats(steps: Sequence[int], spacing: float, years: float) -> dict[str, Any]:
    """되돌림 비율·VR10·걸음당 순이익(판정 비용과 참고 비용)."""
    n = len(steps)
    if n < 2 * BLOCK:
        return {"steps": n}
    rev = sum(1 for a, b in itertools.pairwise(steps) if a != b)
    p = rev / (n - 1)
    se_p = math.sqrt(p * (1 - p) / (n - 1))
    k = n // BLOCK
    r = [sum(steps[j * BLOCK : (j + 1) * BLOCK]) ** 2 / BLOCK for j in range(k)]
    vr = statistics.fmean(r)
    se_vr = statistics.stdev(r) / math.sqrt(k)
    per_year = n / years
    out: dict[str, Any] = {
        "steps": n,
        "steps_per_year": round(per_year, 1),
        "p": round(p, 4),
        "se_p": round(se_p, 4),
        "vr10": round(vr, 4),
        "se_vr10": round(se_vr, 4),
    }
    for f in (FEE, *REF_FEES):
        edge = spacing * (1 - vr) / 2 - f
        out[f"edge_per_step_{f:g}"] = round(edge, 6)
        out[f"grid_year_{f:g}"] = round(per_year * edge / LEVELS, 4)
    return out


def p_star(s: float, f: float) -> float:
    return s / (2 * (s - f))


def judge(
    full: dict[str, Any], halves: Sequence[dict[str, Any]], close: dict[str, Any], s: float
) -> dict[str, Any]:
    """R1~R4 (문서 145 §3) — 모두 통과해야 '자리 있음'."""
    ps, vr_max = p_star(s, FEE), 1 - 2 * FEE / s
    ok = all("p" in x for x in (full, close, *halves))
    r1 = ok and full["p"] - 2 * full["se_p"] > ps
    r2 = ok and all(h["p"] > ps for h in halves)
    r3 = ok and close["p"] > ps
    r4 = ok and all(h["vr10"] < vr_max for h in halves)
    return {
        "p_star": round(ps, 4),
        "vr_max": round(vr_max, 4),
        "R1": bool(r1),
        "R2": bool(r2),
        "R3": bool(r3),
        "R4": bool(r4),
        "room": bool(r1 and r2 and r3 and r4),
    }


def _zips() -> list[Path]:
    return sorted(p for sym in SYMBOLS for p in (VISION / sym / "1m").glob(f"{sym}-1m-*.zip"))


def bundle_hash() -> tuple[str, int]:
    h = hashlib.sha256()
    files = _zips()
    for p in files:
        h.update(p.relative_to(VISION).as_posix().encode())
        h.update(hashlib.sha256(p.read_bytes()).digest())
    return h.hexdigest(), len(files)


def measure(symbol: str) -> dict[str, Any]:
    bars = load_minutes(symbol)
    t = bars.t
    lo_i = next(i for i, x in enumerate(t) if x >= _ms(FROM))
    hi_i = next((i for i, x in enumerate(t) if x >= _ms(TO)), len(t))
    tt = t[lo_i:hi_i]
    path = ohlc_path(bars.o[lo_i:hi_i], bars.h[lo_i:hi_i], bars.low[lo_i:hi_i], bars.c[lo_i:hi_i])
    close_path = array("d", (math.log(x) for x in bars.c[lo_i:hi_i]))
    split = _ms(SPLIT)
    cut = next((i for i, x in enumerate(tt) if x >= split), len(tt))
    span = (tt[-1] - tt[0]) / YEAR_MS
    years = (max((split - tt[0]) / YEAR_MS, 1e-9), max((tt[-1] - split) / YEAR_MS, 1e-9))
    out: dict[str, Any] = {
        "from": datetime.fromtimestamp(tt[0] / 1000, UTC).date().isoformat(),
        "bars": len(tt),
    }
    for s in SPACINGS:
        delta = math.log1p(s)
        steps, where = steps_on_lattice(path, delta, per_bar=4)
        first = array("b", (x for x, b in zip(steps, where, strict=True) if b < cut))
        second = array("b", (x for x, b in zip(steps, where, strict=True) if b >= cut))
        full = stats(steps, s, span)
        halves = [stats(first, s, years[0]), stats(second, s, years[1])]
        csteps, _ = steps_on_lattice(close_path, delta)
        close = stats(csteps, s, span)
        out[f"{s:g}"] = {
            "full": full,
            "first_half": halves[0],
            "second_half": halves[1],
            "close_path": close,
            **judge(full, halves, close, s),
        }
    return out


def run() -> dict[str, Any]:
    if OUT.exists():
        raise SystemExit(f"이미 측정했다: {OUT}")
    digest, count = bundle_hash()
    if digest != EXPECTED_BUNDLE:
        raise SystemExit(f"자료 묶음 해시가 고정값과 다르다 ({count} 파일) — 측정하지 않는다")
    result: dict[str, Any] = {"symbols": {}}
    for sym in SYMBOLS:
        result["symbols"][sym] = measure(sym)
        rooms = [f"{s:g}" for s in SPACINGS if result["symbols"][sym][f"{s:g}"]["room"]]
        print(sym, "자리 있음:", rooms or "없음", flush=True)
    result["rooms"] = [
        f"{sym} {s:g}"
        for sym, v in result["symbols"].items()
        for s in SPACINGS
        if v[f"{s:g}"]["room"]
    ]
    result["config"] = {
        "spacings": SPACINGS,
        "fee": FEE,
        "ref_fees": REF_FEES,
        "block": BLOCK,
        "range": [FROM, TO],
        "split": SPLIT,
        "bundle": digest,
    }
    result["computed"] = datetime.now(UTC).isoformat(timespec="seconds")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return result


def main(argv: list[str]) -> int:
    if argv == ["hash"]:
        digest, count = bundle_hash()
        print(f"{digest} ({count} 파일)")
        return 0
    if argv == ["run"]:
        if not EXPECTED_BUNDLE:
            raise SystemExit("자료 해시가 고정되지 않았다 — hash 뒤 EXPECTED_BUNDLE 을 적고 커밋")
        run()
        return 0
    raise SystemExit("usage: python -m scripts.grid_room hash | run")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
