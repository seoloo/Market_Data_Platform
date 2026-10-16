"""문서 252 — 하락장에서 덜 빠진 종목이 다음 주도주가 되는가 (국장 141 자료). 1회 판정.

사용 예::

    python -m scripts.run_bear_rs_252 events   # 사건 날짜만 (수익 안 봄)
    python -m scripts.run_bear_rs_252 run      # 깨끗한 트리 · 141 자료 해시 · 판정 파일 없음일 때만

* 사건: KOSPI 가 고점(P) 대비 −10% 이하로 내려간 뒤, 그 사이 저점(T)에서 +10% 반등한
  첫날(C, 바닥 확인). C 다음부터 고점을 새로 센다.
* 겹침: 앞에서 쓴 사건의 C 와 ``GAP`` 거래일 넘게 떨어진 사건만 쓴다
  (2008 연쇄 폭락이 사건 5 개로 몰림).
* 종목군: C 의 직전 월말 상위 200(그 시점, 상장폐지 포함). P · T 종가가 있는 종목.
* 하락 구간 수익 = T 종가 ÷ P 종가 − 1. 상위 10(덜 빠짐) · 하위 10 · 전체.
* C 다음 거래일 종가에 같은 금액으로 사서 126 거래일(6 달) 뒤 종가
  (거래 끊기면 마지막 종가), 왕복 0.3%.
"""

from __future__ import annotations

import json
import statistics
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scripts.run_a1_train import git_state
from scripts.run_lt_kr import DATA, EXPECTED_DATA, KrPanel, _iso, _last_px, data_hash
from scripts.run_lt_kr import build_panel as build_kr
from scripts.run_lt_leaders import FROM, SPLIT, TO
from scripts.run_pead_kr_244 import universe_by_day

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "reports" / "bear_rs_252"
DROP = 0.10
REBOUND = 0.10
TOP = 10
HOLD = 126
HOLDS_REF = (63, 252)
COST = 0.003
MIN_EVENTS = 6
GAP = 63
WIN_SHARE = 0.6


@dataclass(frozen=True)
class Event:
    peak: int
    trough: int
    confirm: int


def find_events(
    close: Sequence[float], drop: float = DROP, rebound: float = REBOUND
) -> list[Event]:
    """고점 대비 −drop 아래로 간 뒤 저점에서 +rebound 반등한 첫날. 확인 뒤 고점을 새로 센다."""
    out: list[Event] = []
    peak = 0
    trough: int | None = None
    for t in range(1, len(close)):
        if trough is None:
            if close[t] > close[peak]:
                peak = t
            elif close[t] <= close[peak] * (1 - drop):
                trough = t
            continue
        if close[t] < close[trough]:
            trough = t
        elif close[t] >= close[trough] * (1 + rebound):
            out.append(Event(peak, trough, t))
            peak, trough = t, None
    return out


def fwd(panel: KrPanel, sym: str, c: int, hold: int) -> float | None:
    """C 다음 거래일 종가 → hold 거래일 뒤 종가(끊기면 마지막 종가), 비용 뒤."""
    e = c + 1
    a = panel.close[sym][e]
    if a is None:
        return None
    return _last_px(panel, sym, e + hold) / a - 1 - COST


def groups(panel: KrPanel, pool: Sequence[str], ev: Event) -> dict[str, list[str]]:
    """하락 구간 수익 순서로 덜 빠진 상위 · 많이 빠진 하위 · 전체."""
    dd: list[tuple[float, str]] = []
    for s in pool:
        a, b = panel.close[s][ev.peak], panel.close[s][ev.trough]
        if a is not None and b is not None and panel.close[s][ev.confirm + 1] is not None:
            dd.append((b / a - 1, s))
    dd.sort(key=lambda x: (-x[0], x[1]))
    names = [s for _, s in dd]
    return {"strong": names[:TOP], "weak": names[-TOP:], "all": names}


def mean_fwd(panel: KrPanel, syms: Sequence[str], c: int, hold: int) -> float | None:
    rs = [r for s in syms if (r := fwd(panel, s, c, hold)) is not None]
    return statistics.fmean(rs) if rs else None


def judge(edges: Sequence[float], first: Sequence[float], second: Sequence[float]) -> str:
    """사전 등록 §4 (순수). edges = 사건마다 덜 빠진 상위 − 전체 (6 달)."""
    if not edges:
        return "불명"
    mean = statistics.fmean(edges)
    if mean <= 0:
        return "불리"
    wins = sum(1 for e in edges if e > 0) / len(edges)
    halves_ok = bool(first) and bool(second) and statistics.fmean(first) > 0
    halves_ok = halves_ok and statistics.fmean(second) > 0
    if len(edges) >= MIN_EVENTS and wins >= WIN_SHARE and halves_ok:
        return "지지"
    return "불명"


def kospi_close(panel: KrPanel) -> list[float]:
    rows = (DATA / "kospi.csv").read_text(encoding="utf-8").splitlines()[1:]
    kmap = {_iso(r.split(",")[0]): float(r.split(",")[1]) for r in rows}
    return [kmap[d] for d in panel.days]


def usable(panel: KrPanel, evs: Sequence[Event], hold: int = HOLD) -> list[Event]:
    """C 가 판 시작 뒤이고 보유 끝이 자료 끝(2026-08-31) 안인 사건."""
    n = len(panel.days)
    out: list[Event] = []
    for e in evs:
        if panel.days[e.confirm] < FROM or e.confirm + 1 + hold >= n:
            continue
        if out and e.confirm - out[-1].confirm <= GAP:
            continue
        out.append(e)
    return out


def events_only() -> dict[str, Any]:
    if data_hash()[0] != EXPECTED_DATA:
        raise SystemExit("국장 자료 해시가 141 과 다르다")
    panel, _ok = build_kr(to=TO)
    k = kospi_close(panel)
    d = panel.days
    return {
        "events": [
            {
                "peak": d[e.peak],
                "trough": d[e.trough],
                "confirm": d[e.confirm],
                "kospi_drop": round(k[e.trough] / k[e.peak] - 1, 3),
            }
            for e in usable(panel, find_events(k))
        ]
    }


def run() -> dict[str, Any]:
    target = OUT / "result.json"
    if target.exists():
        raise SystemExit(f"이미 판정했다: {target}")
    sha, dirty = git_state()
    if dirty:
        raise SystemExit("작업 트리가 깨끗하지 않다 — 커밋 뒤에 실행")
    if data_hash()[0] != EXPECTED_DATA:
        raise SystemExit("국장 자료 해시가 141 과 다르다")
    panel, _ok = build_kr(to=TO)
    k = kospi_close(panel)
    uni = universe_by_day(panel)
    evs = usable(panel, find_events(k))
    rows = []
    for e in evs:
        g = groups(panel, sorted(uni[e.confirm]), e)
        row: dict[str, Any] = {
            "peak": panel.days[e.peak],
            "trough": panel.days[e.trough],
            "confirm": panel.days[e.confirm],
            "n": len(g["all"]),
            "strong_names": g["strong"],
        }
        for h in (HOLD, *HOLDS_REF):
            if e.confirm + 1 + h >= len(panel.days):
                continue
            s, w, a = (mean_fwd(panel, g[x], e.confirm, h) for x in ("strong", "weak", "all"))
            kx = k[e.confirm + 1 + h] / k[e.confirm + 1] - 1
            row[f"h{h}"] = {"strong": s, "weak": w, "all": a, "kospi": kx}
        rows.append(row)
    main = [r for r in rows if r[f"h{HOLD}"]["strong"] is not None and r[f"h{HOLD}"]["all"]]
    edge = {r["confirm"]: r[f"h{HOLD}"]["strong"] - r[f"h{HOLD}"]["all"] for r in main}
    first = [v for d, v in edge.items() if d < SPLIT]
    second = [v for d, v in edge.items() if d >= SPLIT]
    vals = list(edge.values())

    def avg(key: str, h: int = HOLD) -> float | None:
        xs = [r[f"h{h}"][key] for r in rows if f"h{h}" in r and r[f"h{h}"][key] is not None]
        return statistics.fmean(xs) if xs else None

    out = {
        "git": sha,
        "verdict": judge(vals, first, second),
        "n_events": len(vals),
        "edge_mean": statistics.fmean(vals) if vals else None,
        "edge_wins": sum(1 for v in vals if v > 0) / len(vals) if vals else None,
        "edge_first": statistics.fmean(first) if first else None,
        "edge_second": statistics.fmean(second) if second else None,
        "means": {
            f"h{h}": {x: avg(x, h) for x in ("strong", "weak", "all", "kospi")}
            for h in (HOLD, *HOLDS_REF)
        },
        "events": rows,
        "note": "문서 252 1 회 판정. 141 국장 자료(2008-06 ~ 2026-08), 6 달 · 왕복 0.3%.",
    }
    OUT.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else ""
    if cmd == "events":
        out = events_only()
    elif cmd == "run":
        out = {k: v for k, v in run().items() if k != "events"}
    else:
        print(__doc__)
        return 2
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
