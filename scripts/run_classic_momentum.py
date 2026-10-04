"""문서 122 — 고전 모멘텀(52 주 신고가 근처 · 12-1): 2008~2016 로 고르고 2017~2026 으로 검증.

사용 예::

    python -m scripts.run_classic_momentum run   # 판정 한 번 (트리 깨끗 · 결과 파일 없음)
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scripts.run_a1_train import git_state
from scripts.run_sb_breakout import QQQ, SPY, _sma, load

from scripts import run_lt_leaders as lt
from scripts import run_lt_sp400 as lt400

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "reports" / "classic_momentum"
TRAIN = (lt.FROM, "2016-12-31")
TEST = ("2017-01-01", lt.TO)
NS = (10, 20, 50)
LOOK = 252
SKIP = 21
TRAIN_EDGE = 0.10
T1_EDGE = 0.10
T3_MARGIN = 0.10

Score = Callable[[Sequence[float | None], int], float | None]


def score_52wh(xs: Sequence[float | None], t: int) -> float | None:
    if t < LOOK or xs[t] is None or xs[t - LOOK] is None:
        return None
    window = [x for x in xs[t - LOOK + 1 : t + 1] if x is not None]
    return xs[t] / max(window) if window else None  # type: ignore[operator]


def score_mom(xs: Sequence[float | None], t: int) -> float | None:
    if t < LOOK or xs[t] is None or xs[t - SKIP] is None or xs[t - LOOK] is None:
        return None
    return xs[t - SKIP] / xs[t - LOOK] - 1  # type: ignore[operator]


SIGNALS: dict[str, Score] = {"52WH": score_52wh, "MOM": score_mom}


def simulate(
    panel: lt.Panel, score: Score, n: int, *, spy_ok: Sequence[bool] | None = None
) -> tuple[list[float], float]:
    """(lt.FROM 부터의 평가액, 평균 월 회전율). 월말 점수 → 다음 거래일 종가에 똑같은 비중."""
    days = panel.days
    start = next(i for i, d in enumerate(days) if d >= lt.FROM)
    hold: dict[str, float] = {}
    cash = 1.0
    out: list[float] = []
    pending: list[str] | None = None
    turnover: list[float] = []

    def value(t: int) -> dict[str, float]:
        vals = {}
        for s, q in hold.items():
            xs = panel.close[s]
            k = t
            while xs[k] is None:
                k -= 1
            vals[s] = q * xs[k]  # type: ignore[operator]
        return vals

    for t in range(start, len(days)):
        if pending is not None:
            vals = value(t)
            total = cash + sum(vals.values())
            picks = [s for s in pending if panel.close[s][t] is not None]
            each = total / len(picks) if picks else 0.0
            moved = sum(abs(each - vals.get(s, 0.0)) for s in picks) + sum(
                v for s, v in vals.items() if s not in picks
            )
            turnover.append(moved / total / 2 if total > 0 else 0.0)
            total -= moved * lt.FEE
            if picks:
                each = total / len(picks)
                hold = {s: each / panel.close[s][t] for s in picks}  # type: ignore[operator]
                cash = 0.0
            else:
                hold, cash = {}, total
            pending = None
        out.append(cash + sum(value(t).values()))
        month_end = t + 1 < len(days) and days[t + 1][:7] != days[t][:7]
        if month_end:
            if spy_ok is not None and not spy_ok[t]:
                pending = []
                continue
            scored = [(sc, s) for s, xs in panel.close.items() if (sc := score(xs, t)) is not None]
            scored.sort(reverse=True)
            pending = [s for _, s in scored[:n]]
    return out, (sum(turnover) / len(turnover) if turnover else 0.0)


def _spy_ok(panel: lt.Panel) -> list[bool]:
    spy = load(SPY)
    m = dict(zip(spy.dates, spy.close, strict=True))
    c = [m[d] for d in panel.days]
    sma = _sma(c, lt.SPY_SMA)
    return [s is not None and x > s for x, s in zip(c, sma, strict=True)]


def _hold(span: Sequence[str], path: Path) -> list[float]:
    s = load(path)
    m = dict(zip(s.dates, s.close, strict=True))
    return [m[d] / m[span[0]] for d in span]


def run() -> dict[str, Any]:
    target = OUT / "verdict.json"
    if target.exists():
        raise SystemExit(f"이미 판정했다: {target}")
    _sha, dirty = git_state()
    if dirty:
        raise SystemExit("작업 트리가 깨끗하지 않다 — 커밋 뒤에 실행")
    p500 = lt.build_panel()
    span = p500.days[next(i for i, d in enumerate(p500.days) if d >= lt.FROM) :]
    ew = lt.equal_weight(p500)
    paths = {(name, n): simulate(p500, fn, n) for name, fn in SIGNALS.items() for n in NS}
    train = {
        f"{k[0]}_{k[1]}": lt.stats(span, eq, *TRAIN) | {"turnover": round(to, 3)}
        for k, (eq, to) in paths.items()
    }
    ew_train = lt.stats(span, ew, *TRAIN)
    best = sorted(
        paths,
        key=lambda k: (
            -train[f"{k[0]}_{k[1]}"]["sharpe"],
            -train[f"{k[0]}_{k[1]}"]["cagr"],
            -k[1],
        ),
    )[0]
    key = f"{best[0]}_{best[1]}"
    edge = train[key]["sharpe"] - ew_train["sharpe"]
    result: dict[str, Any] = {
        "train": train,
        "EW_train": ew_train,
        "chosen": key,
        "train_edge_sharpe": round(edge, 3),
    }
    if edge < TRAIN_EDGE:
        result["verdict"] = "REJECT"
        result["reason"] = f"학습 문턱 미달 ({edge:.3f} < {TRAIN_EDGE})"
    else:
        eq = paths[best][0]
        test = lt.stats(span, eq, *TEST)
        ew_test = lt.stats(span, ew, *TEST)
        lt400._point_to_sp400()
        lt.UNIVERSE_SHA256 = lt400.UNIVERSE_SHA256
        p400 = lt.build_panel()
        span400 = p400.days[next(i for i, d in enumerate(p400.days) if d >= lt.FROM) :]
        eq400, _ = simulate(p400, SIGNALS[best[0]], best[1])
        rep = lt.stats(span400, eq400, *TEST)
        ew400 = lt.stats(span400, lt.equal_weight(p400), *TEST)
        t1 = test["sharpe"] >= ew_test["sharpe"] + T1_EDGE
        t2 = test["cagr"] > ew_test["cagr"]
        t3 = test["mdd"] <= ew_test["mdd"] + T3_MARGIN
        t4 = rep["cagr"] > ew400["cagr"]
        result |= {
            "test": test,
            "EW_test": ew_test,
            "sp400_test": rep,
            "EW400_test": ew400,
            "T1": t1,
            "T2": t2,
            "T3": t3,
            "T4": t4,
            "verdict": "PASS" if t1 and t2 and t3 and t4 else "REJECT",
            "all_test_report_only": {
                f"{k[0]}_{k[1]}": lt.stats(span, e, *TEST) for k, (e, _t) in paths.items()
            },
        }
    result["benchmarks"] = {
        "QQQ_train": lt.stats(span, _hold(span, QQQ), *TRAIN),
        "QQQ_test": lt.stats(span, _hold(span, QQQ), *TEST),
        "SPY_train": lt.stats(span, _hold(span, SPY), *TRAIN),
        "SPY_test": lt.stats(span, _hold(span, SPY), *TEST),
    }
    result["computed"] = datetime.now(UTC).date().isoformat()
    OUT.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return result


def main(argv: list[str]) -> int:
    if argv != ["run"]:
        raise SystemExit("usage: python -m scripts.run_classic_momentum run")
    sys.stdout.write(json.dumps(run(), ensure_ascii=True, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
