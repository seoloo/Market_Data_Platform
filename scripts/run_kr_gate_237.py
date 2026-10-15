"""문서 237 — 국장 주도주 규칙의 시장 조건: KOSPI 200 일선 · 60 일선 · 조건 없음. 1회 판정.

사용 예::

    python -m scripts.run_kr_gate_237 run   # 깨끗한 트리 · 자료 해시 일치 · 판정 파일 없음일 때만

규칙은 141 · 142 의 ``simulate_kr`` 그대로이고 ``index_ok``(새로 살 수 있는 날)만 바꾼다.
조건은 '새로 사기' 만 막는다 — 들고 있던 종목은 추세 꺾임 · 고점 −35% 까지 들고 간다
(141 · 142 와 같음).
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from scripts.run_a1_train import git_state
from scripts.run_kr_flow import EXPECTED_FLOWS, flow_hash, flow_picker, load_flows
from scripts.run_lt_kr import (
    DATA,
    EXPECTED_DATA,
    _iso,
    biweekly_day,
    build_panel,
    data_hash,
    month_end_day,
    simulate_kr,
)
from scripts.run_lt_leaders import FROM, SPLIT, TO, stats

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "reports" / "kr_gate_237"
GATES = {"G200": 200, "G60": 60, "G0": 0}
"""조건 이름 → 이동평균 길이(0 = 조건 없음). G200 이 지금 Paper · 141 · 142."""
FIRST_END = "2016-12-31"
SHARPE_GAIN = 0.10
MDD_SLACK = 0.05
INDEX_FEE = 0.0005
"""지수 확인(참고)의 편도 비용 — ETF 로 사고판다고 본다."""


def gate(series: Sequence[float], n: int) -> list[bool]:
    """그날 종가가 n 일 이동평균 위면 True. n = 0 이면 언제나 True. 평균이 없는 앞쪽은 False."""
    if n == 0:
        return [True] * len(series)
    out: list[bool] = []
    run = 0.0
    for i, c in enumerate(series):
        run += c
        if i >= n:
            run -= series[i - n]
        out.append(i >= n - 1 and c > run / n)
    return out


def index_gated(series: Sequence[float], ok: Sequence[bool], fee: float = INDEX_FEE) -> list[float]:
    """지수 확인: t 종가에 조건을 보고 t+1 종가에 맞춤(들고 있거나 현금, 이자 0)."""
    eq, held, want = [1.0], ok[0], ok[0]
    for t in range(1, len(series)):
        v = eq[-1] * (series[t] / series[t - 1] if held else 1.0)
        if want != held:
            v *= 1 - fee
            held = want
        eq.append(v)
        want = ok[t]
    return eq


def judge(rows: dict[str, dict[str, Any]], cand: str) -> dict[str, Any]:
    """후보 조건이 LT · FLOW 둘 다에서 G200 보다 나은가 (§4)."""
    checks: dict[str, dict[str, bool]] = {}
    for rule in ("LT", "FLOW"):
        a, b = rows[f"{rule}/{cand}"], rows[f"{rule}/G200"]
        checks[rule] = {
            "sharpe": a["full"]["sharpe"] >= b["full"]["sharpe"] + SHARPE_GAIN,
            "halves": all(a[h]["cagr"] > b[h]["cagr"] for h in ("first", "second")),
            "mdd": a["full"]["mdd"] <= b["full"]["mdd"] + MDD_SLACK,
        }
    ok = all(all(c.values()) for c in checks.values())
    gain = sum(
        rows[f"{r}/{cand}"]["full"]["sharpe"] - rows[f"{r}/G200"]["full"]["sharpe"]
        for r in ("LT", "FLOW")
    )
    return {"checks": checks, "pass": ok, "sharpe_gain_sum": round(gain, 3)}


def _yearly(eq: Sequence[float], span: Sequence[str]) -> dict[str, float]:
    out = {}
    for y in sorted({d[:4] for d in span}):
        idx = [i for i, d in enumerate(span) if d[:4] == y]
        prev = idx[0] - 1 if idx[0] > 0 else idx[0]
        out[y] = round(eq[idx[-1]] / eq[prev] - 1, 4)
    return out


def _row(eq: Sequence[float], span: Sequence[str], run: Any = None) -> dict[str, Any]:
    end = span[-1]
    row: dict[str, Any] = {
        "full": stats(span, eq, FROM, end),
        "first": stats(span, eq, FROM, FIRST_END),
        "second": stats(span, eq, SPLIT, end),
        "krw_1m_final": round(1_000_000 * eq[-1] / eq[0]),
        "yearly": _yearly(eq, span),
    }
    if run is not None:
        row["trades"] = len(run.trades)
    return row


def build() -> dict[str, Any]:
    panel, _g200 = build_panel()
    days = panel.days
    rows_k = (DATA / "kospi.csv").read_text(encoding="utf-8").splitlines()[1:]
    kospi_map = {_iso(r.split(",")[0]): float(r.split(",")[1]) for r in rows_k}
    kospi = [kospi_map[d] for d in days]
    oks = {name: gate(kospi, n) for name, n in GATES.items()}
    if oks["G200"] != _g200:
        raise SystemExit("G200 이 141 의 index_ok 와 다르다")
    start = next(i for i, d in enumerate(days) if d >= FROM)
    span = days[start:]
    both = load_flows(panel)
    foreign = load_flows(panel, who="foreign")
    pickers: dict[str, Callable[..., Any] | None] = {
        "LT": None,
        "FLOW": flow_picker(both),
        "FOREIGN": flow_picker(foreign),
    }
    out: dict[str, Any] = {}
    for cadence, decide in (("biweekly", biweekly_day), ("month_end", month_end_day)):
        rows: dict[str, dict[str, Any]] = {}
        for rule, pick in pickers.items():
            for name, ok in oks.items():
                r = simulate_kr(panel, ok, pick, decide=decide)
                row = _row(r.equity, span, r)
                decisions = [t for t in range(start, len(days)) if decide(days, t)]
                row["decision_days"] = len(decisions)
                row["decision_days_open"] = sum(1 for t in decisions if ok[t])
                rows[f"{rule}/{name}"] = row
        out[cadence] = rows
    idx: dict[str, Any] = {}
    seg = kospi[start:]
    for name, ok in oks.items():
        idx[name] = _row(index_gated(seg, ok[start:]), span)
        idx[name]["open_share"] = round(sum(ok[start:]) / len(span), 3)
    out["index"] = idx
    return out


def run() -> dict[str, object]:
    target = OUT / "verdict.json"
    if target.exists():
        raise SystemExit(f"이미 판정했다: {target}")
    sha, dirty = git_state()
    if dirty:
        raise SystemExit("작업 트리가 깨끗하지 않다 — 커밋 뒤에 실행")
    if data_hash()[0] != EXPECTED_DATA:
        raise SystemExit("가격 자료 해시가 문서 141 고정값과 다르다")
    if flow_hash()[0] != EXPECTED_FLOWS:
        raise SystemExit("수급 자료 해시가 문서 142 고정값과 다르다")
    res = build()
    main_rows = res["biweekly"]
    verdicts = {c: judge(main_rows, c) for c in ("G60", "G0")}
    passed = [c for c, v in verdicts.items() if v["pass"]]
    pick = max(passed, key=lambda c: verdicts[c]["sharpe_gain_sum"]) if passed else None
    out: dict[str, object] = {
        "git": sha,
        "period": [FROM, TO],
        "verdicts": verdicts,
        "verdict": f"CHANGE_{pick}" if pick else "KEEP_G200",
        "results": res,
        "note": "문서 237 1 회 판정. 이미 본 구간이라 설명용 — CHANGE 도 Paper 줄 추가까지만.",
    }
    OUT.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def main(argv: list[str]) -> int:
    if argv[1:] != ["run"]:
        print(__doc__)
        return 2
    out = run()
    print(json.dumps({"verdict": out["verdict"], "verdicts": out["verdicts"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
