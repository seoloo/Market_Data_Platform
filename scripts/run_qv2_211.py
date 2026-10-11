# ruff: noqa: E501 — 한국어 설명 · 표 문자열이 길다
"""문서 211 — QV2: T/v2 신호(20 · 60 · 120 부호 평균, 롱만)를 거래대금 봉으로 세기 · 후향 회계 파일럿. 주문 없음.

210 의 자료 · 계좌 엔진(`scripts/run_qb_dc_210.py`)을 그대로 쓴다. 신호만 새로 만든다.

사용::

    python -m scripts.run_qv2_211 run   # → reports/qv2_211/result.json
"""

from __future__ import annotations

import json
import statistics
import sys
from dataclasses import asdict
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from scripts.run_qb_dc_210 import (
    DAY,
    H4,
    MIN,
    SENS,
    WARM,
    Market,
    Params,
    Signals,
    _load,
    _ms,
    build_signals,
    daily_closes,
    daily_quote,
    qb_bars,
    summarize,
    verdict,
)

REPORT = Path(__file__).resolve().parent.parent / "reports" / "qv2_211"
EVAL_START_211 = _ms(date(2020, 7, 1))
"""두 신호(시간 120 일 · 거래대금 120 봉)가 모두 준비된 뒤 — 결과 보기 전 고정 (문서 211 §3)."""
LOOKS = (20, 60, 120)
SHORT_DAYS = (18, 52, 104)
"""대조: 시간 기간을 거래대금 봉 평균 길이(210 측정 중앙 약 20.9 시간 ≈ 0.87 일)만큼 줄인 것 — 보고만."""


def _tv2(closes: list[float], k: int, looks: tuple[int, ...]) -> float:
    s = [1 if closes[k] / closes[k - n] - 1 > 0 else -1 for n in looks]
    return max(0.0, sum(s) / len(s))


def build_211(m: Market) -> dict[str, list[float]]:
    """판단 시각마다 QV2 · TV2S (그 시각까지 완성된 자료만)."""
    n_j = len(m.c) * MIN // H4
    dcl = daily_closes(m)
    bars = qb_bars(m, daily_quote(m))
    out = {"QV2": [0.0] * n_j, "TV2S": [0.0] * n_j}
    b = 0
    for j in range(n_j):
        d_ms = WARM + j * H4
        while b < len(bars) and bars[b][0] <= d_ms:
            b += 1
        if b >= max(LOOKS) + 1:
            closes = [x[1] for x in bars[b - max(LOOKS) - 1 : b]]
            out["QV2"][j] = _tv2(closes, len(closes) - 1, LOOKS)
        k = d_ms // DAY - WARM // DAY - 1
        if k >= max(SHORT_DAYS):
            out["TV2S"][j] = _tv2(dcl, k, SHORT_DAYS)
    return out


def run() -> int:
    mk = _load()
    sigs: dict[str, Signals] = {}
    for s, m in mk.items():
        base = build_signals(m)
        sigs[s] = Signals({**base.by_j, **build_211(m)})
    variants = ("TV2REF", "QV2", "TV2S", "T20", "QB")
    runs: dict[str, dict[str, dict[str, Any]]] = {}
    for sname, p in SENS.items():
        runs[sname] = {v: summarize_from(mk, sigs, v, p) for v in variants}
    signs = [
        runs[s]["QV2"]["sharpe"] > runs[s]["TV2REF"]["sharpe"]
        for s in SENS
        if s != "cost_realistic_ref"
    ]
    same = all(signs) or not any(signs)
    word, stat = verdict(runs["main"]["QV2"], runs["main"]["TV2REF"], same)
    corr = {
        v: round(
            statistics.correlation(
                [r for _, r in runs["main"][v]["_returns"]],
                [r for _, r in runs["main"]["TV2REF"]["_returns"]],
            ),
            3,
        )
        for v in ("QV2", "TV2S", "T20", "QB")
    }
    out: dict[str, Any] = {
        "made": datetime.now(UTC).isoformat(),
        "params": asdict(Params()),
        "eval_start": "2020-07-01",
        "pilot": {
            "QV2_vs_TV2REF": word,
            "stat": stat,
            "sens_sharpe_diff": {
                s: round(runs[s]["QV2"]["sharpe"] - runs[s]["TV2REF"]["sharpe"], 3) for s in SENS
            },
        },
        "control_TV2S_vs_TV2REF": verdict(runs["main"]["TV2S"], runs["main"]["TV2REF"], True)[1],
        "daily_corr_with_TV2REF": corr,
        "runs": {
            s: {v: {k: x for k, x in d.items() if k != "_returns"} for v, d in vs.items()}
            for s, vs in runs.items()
        },
    }
    REPORT.mkdir(parents=True, exist_ok=True)
    (REPORT / "result.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1, default=str), encoding="utf-8"
    )
    main_runs = runs["main"]
    for v in variants:
        d = main_runs[v]
        print(
            v,
            {
                k: d[k]
                for k in (
                    "cagr",
                    "sharpe",
                    "mdd",
                    "net_pnl",
                    "cost",
                    "funding",
                    "trades",
                    "avg_exposure",
                    "turnover_x_per_year",
                )
            },
        )
    print(json.dumps(out["pilot"], ensure_ascii=False))
    print("대조 TV2S", json.dumps(out["control_TV2S_vs_TV2REF"], ensure_ascii=False))
    print("상관", corr)
    return 0


def summarize_from(
    mk: dict[str, Market], sigs: dict[str, Signals], v: str, p: Params
) -> dict[str, Any]:
    return summarize(mk, sigs, v, p, eval_start=EVAL_START_211)


def main(argv: list[str]) -> int:
    if argv[:1] != ["run"]:
        print(__doc__)
        return 2
    return run()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
