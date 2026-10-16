"""문서 247 — 시장 전체 공포 뒤 KOSPI 지수 매수를 안 본 옛 자료(1997 ~ 2006)로. 1회 판정.

사용 예::

    python -m scripts.run_panic_247 calibrate   # 2008 ~ 2026 공포일에 맞는 지수 기준 (수익 안 봄)
    python -m scripts.run_panic_247 run         # 깨끗한 트리 · 기준 고정 · 판정 파일 없음일 때만

* 공포일(폭): 141 그 시점 종목군(직전 월말 상위 200) 중 종가 ≤ 25 일선 × 0.8 인 비율 ≥ 10% 인
  첫날(앞 20 거래일 공포일 없음) — 151 · 154 와 같은 정의, 2008 ~ 2026-08.
* 지수 기준: KOSPI 종가 ≤ 25 일선 × (1 − X) 인 첫날(앞 20 거래일 없음). X 는 5 ~ 15% 중
  공포일(±5 거래일)과 F1 이 가장 높은 값(같으면 작은 값). 날짜만 맞추고 수익은 보지 않는다.
* 시험: 그 X 로 1997-01 ~ 2006-12 KOSPI(야후 ^KS11)의 공포일 → 다음 거래일 종가 매수 →
  20 거래일(주) · 60(참고) 보유, 왕복 0.2%(ETF). 비교 = 같은 기간 아무 날이나 산 20 일 평균.
"""

from __future__ import annotations

import bisect
import json
import statistics
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from scripts.run_a1_train import git_state
from scripts.run_lt_kr import DATA, EXPECTED_DATA, _iso, data_hash
from scripts.run_lt_kr import build_panel as build_kr
from scripts.run_lt_leaders import TO
from scripts.run_pead_kr_244 import universe_by_day

REPO = Path(__file__).resolve().parent.parent
KS11 = REPO / "data" / "research" / "qs_bottom_xmkt" / "KS11.csv"
OUT = REPO / "reports" / "panic_247"
MA = 25
DROP = 0.20
SHARE = 0.10
GAP = 20
MATCH = 5
XS = tuple(round(0.05 + 0.01 * k, 2) for k in range(11))
HOLD, HOLD_LONG = 20, 60
COST = 0.002
TEST_FROM, TEST_TO = "1997-01-01", "2006-12-31"
CAL_FROM = "2008-01-01"
CHOSEN_X: float | None = 0.07
"""calibrate 뒤 문서 247 §5 에 적고 여기 고정한다."""


def first_days(flags: Sequence[bool], gap: int = GAP) -> list[int]:
    """참인 날 중 앞 ``gap`` 거래일 안에 참이 없던 첫날들."""
    out: list[int] = []
    last = -(10**9)
    for i, f in enumerate(flags):
        if f:
            if i - last > gap:
                out.append(i)
            last = i
    return out


def sma(xs: Sequence[float], i: int, n: int = MA) -> float | None:
    return sum(xs[i - n + 1 : i + 1]) / n if i >= n - 1 else None


def index_flags(close: Sequence[float], x: float) -> list[bool]:
    out = []
    for i, c in enumerate(close):
        m = sma(close, i)
        out.append(m is not None and c <= m * (1 - x))
    return out


def f1(pred: Sequence[int], truth: Sequence[int], tol: int = MATCH) -> float:
    if not pred or not truth:
        return 0.0
    hit_p = sum(1 for p in pred if any(abs(p - t) <= tol for t in truth))
    hit_t = sum(1 for t in truth if any(abs(p - t) <= tol for p in pred))
    prec, rec = hit_p / len(pred), hit_t / len(truth)
    return 0.0 if prec + rec == 0 else 2 * prec * rec / (prec + rec)


def choose_x(
    close: Sequence[float], truth: Sequence[int], lo: int = 0
) -> tuple[float, dict[str, float]]:
    """``lo`` 앞(보정 기간 밖)의 지수 신호는 세지 않는다."""
    scores = {x: f1([i for i in first_days(index_flags(close, x)) if i >= lo], truth) for x in XS}
    best = max(XS, key=lambda x: (scores[x], -x))
    return best, {f"{x:.2f}": round(s, 3) for x, s in scores.items()}


def forward(close: Sequence[float], i: int, hold: int) -> float | None:
    """i 공포일 → i+1 종가 매수 → i+1+hold 종가 매도, 비용 뒤."""
    e, x = i + 1, i + 1 + hold
    if x >= len(close):
        return None
    return close[x] / close[e] - 1 - COST


def judge(rets: Sequence[float], base: float) -> str:
    """사전 등록 §4 (순수)."""
    if not rets:
        return "불명"
    mean = statistics.fmean(rets)
    if mean <= 0:
        return "불리"
    wins = sum(1 for r in rets if r > 0) / len(rets)
    if len(rets) >= 5 and mean > base and wins >= 0.6:
        return "지지"
    return "불명"


def breadth_truth() -> tuple[list[str], list[int], list[float]]:
    """2008 ~ 2026 공포일(폭) · 같은 날들의 KOSPI 종가 (141 자료)."""
    if data_hash()[0] != EXPECTED_DATA:
        raise SystemExit("국장 자료 해시가 141 과 다르다")
    panel, _ok = build_kr(to=TO)
    uni = universe_by_day(panel)
    days = panel.days
    flags = []
    for t in range(len(days)):
        mem = [s for s in uni[t] if panel.close[s][t] is not None]
        low = 0
        for s in mem:
            w = [v for v in panel.close[s][t - MA + 1 : t + 1] if v is not None]
            c = panel.close[s][t]
            if t >= MA - 1 and len(w) == MA and c is not None and c <= (1 - DROP) * sum(w) / MA:
                low += 1
        flags.append(days[t] >= CAL_FROM and bool(mem) and low / len(mem) >= SHARE)
    rows = (DATA / "kospi.csv").read_text(encoding="utf-8").splitlines()[1:]
    kmap = {_iso(r.split(",")[0]): float(r.split(",")[1]) for r in rows}
    return days, first_days(flags), [kmap[d] for d in days]


def calibrate() -> dict[str, Any]:
    days, truth, kospi = breadth_truth()
    lo = bisect.bisect_left(days, CAL_FROM)
    best, scores = choose_x(kospi, truth, lo)
    return {
        "panic_days": [days[i] for i in truth],
        "chosen_x": best,
        "f1": scores,
        "index_days": [days[i] for i in first_days(index_flags(kospi, best)) if i >= lo],
    }


def load_ks11() -> tuple[list[str], list[float]]:
    rows = KS11.read_text(encoding="utf-8").splitlines()[1:]
    pairs = [(r.split(",")[0], float(r.split(",")[1])) for r in rows if r.split(",")[1]]
    return [d for d, _ in pairs], [c for _, c in pairs]


def run() -> dict[str, Any]:
    target = OUT / "result.json"
    if target.exists():
        raise SystemExit(f"이미 판정했다: {target}")
    sha, dirty = git_state()
    if dirty:
        raise SystemExit("작업 트리가 깨끗하지 않다 — 커밋 뒤에 실행")
    if CHOSEN_X is None:
        raise SystemExit("CHOSEN_X 미고정 — calibrate 뒤 문서 · 코드에 적고 커밋")
    days, close = load_ks11()
    lo, hi = bisect.bisect_left(days, TEST_FROM), bisect.bisect_right(days, TEST_TO) - 1
    ev = [i for i in first_days(index_flags(close, CHOSEN_X)) if lo <= i <= hi]
    r20 = {days[i]: forward(close, i, HOLD) for i in ev}
    r60 = {days[i]: forward(close, i, HOLD_LONG) for i in ev}
    got = [r for r in r20.values() if r is not None]
    base_all = [r for i in range(lo, hi + 1) if (r := forward(close, i, HOLD)) is not None]
    base = statistics.fmean(base_all)
    out = {
        "git": sha,
        "x": CHOSEN_X,
        "verdict": judge(got, base),
        "events": {d: {"r20": r20[d], "r60": r60[d]} for d in r20},
        "n": len(got),
        "mean20": statistics.fmean(got) if got else None,
        "median20": statistics.median(got) if got else None,
        "wins20": sum(1 for r in got if r > 0) / len(got) if got else None,
        "mean60": statistics.fmean([r for r in r60.values() if r is not None] or [0.0]),
        "base20": base,
        "note": "문서 247 1 회 판정. 1997 ~ 2006 KOSPI(야후 ^KS11), 왕복 0.2%.",
    }
    OUT.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else ""
    if cmd == "calibrate":
        out = calibrate()
    elif cmd == "run":
        out = {k: v for k, v in run().items() if k != "events"}
    else:
        print(__doc__)
        return 2
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
