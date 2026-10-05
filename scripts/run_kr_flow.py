"""문서 142 — 수급(외국인 + 기관 순매수)으로 주도 섹터·주도주를 고르는 LT 판. 1회 판정.

사용 예::

    python -m scripts.run_kr_flow audit   # 수급 자료 점검 (수익 없음)
    python -m scripts.run_kr_flow run     # 깨끗한 트리 · 자료 해시 일치 · 판정 파일 없음일 때만

LT-KR(문서 141)과 **주도주를 고르는 점수만** 다르다. 종목군(거래대금 상위 200)·섹터 표·추세 조건·
매달 진입/추매·추세 꺾임/−35% 매도·상장폐지·거래정지·KOSPI 200 일선·비용 0.25% 는 141 코드 그대로.

* **수급 비율(W 일)** = 직전 W 거래일 (외국인 + 기관) 순매수 금액 합 ÷ 같은 기간 거래대금 합.
* **수급 점수** = 종목군 안에서 W = 20·60 수급 비율 백분위의 평균.
* **주도 섹터** = 섹터별 수급 점수 중앙값 상위 3 · **주도주** = 그 안 수급 점수 상위 10.
"""

from __future__ import annotations

import hashlib
import json
import statistics
import sys
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from scripts.run_a1_train import git_state
from scripts.run_lt_kr import (
    DATA,
    EXPECTED_DATA,
    UNKNOWN,
    WATCH,
    KrPanel,
    Picker,
    _iso,
    _last_px,
    build_panel,
    data_hash,
    equal_weight_pit,
    members,
    simulate_kr,
)
from scripts.run_lt_leaders import FROM, SPLIT, TOP_SECTORS, TOP_STOCKS, stats

REPO = Path(__file__).resolve().parent.parent
FLOWS = DATA / "flows"
OUTPUT = REPO / "reports" / "kr_flow"
WINDOWS = (20, 60)
MIN_SCORED = 50
EXPECTED_FLOWS: str | None = "63eb6ef92d92806ee5b0814b4fa920a9ee8f98542be66afad4f0ae8b1e52b001"
"""수급 자료 해시 — audit 뒤 문서 142 에 적고 여기에 고정한다."""


@dataclass(slots=True)
class Flows:
    net: dict[str, list[float]]
    """(외국인 + 기관) 순매수 금액(원). 자료 없는 날 0."""
    value: dict[str, list[float]]
    """거래대금(원). 자료 없는 날 0."""


def flow_ratio(f: Flows, sym: str, t: int, w: int) -> float | None:
    if sym not in f.net or t - w + 1 < 0:
        return None
    tv = sum(f.value[sym][t - w + 1 : t + 1])
    if tv <= 0:
        return None
    return sum(f.net[sym][t - w + 1 : t + 1]) / tv


def flow_scores(f: Flows, t: int, pool: Sequence[str]) -> dict[str, float]:
    """종목군 안 W = 20·60 수급 비율 백분위 평균. 두 비율을 다 낼 수 있는 종목만."""
    ratios: dict[int, dict[str, float]] = {}
    for w in WINDOWS:
        ratios[w] = {s: r for s in pool if (r := flow_ratio(f, s, t, w)) is not None}
    common = set.intersection(*(set(v) for v in ratios.values()))
    if len(common) < MIN_SCORED:
        return {}
    score: dict[str, float] = defaultdict(float)
    for w in WINDOWS:
        ordered = sorted(common, key=lambda s: (ratios[w][s], s))
        n = len(ordered)
        for i, s in enumerate(ordered):
            score[s] += i / (n - 1) / len(WINDOWS)
    return dict(score)


def flow_picker(f: Flows) -> Picker:
    """LT 의 ``leaders_pit`` 과 같은 두 단계(섹터 중앙값 상위 3 → 그 안 상위 10)를 수급 점수로."""

    def pick(panel: KrPanel, t: int, pool: Sequence[str]) -> list[tuple[str, float]]:
        score = flow_scores(f, t, pool)
        if not score:
            return []
        by_sector: dict[str, list[float]] = defaultdict(list)
        for s, v in score.items():
            if panel.sector[s] != UNKNOWN:
                by_sector[panel.sector[s]].append(v)
        top = sorted(by_sector, key=lambda x: (statistics.median(by_sector[x]), x), reverse=True)[
            :TOP_SECTORS
        ]
        picked = sorted(
            (s for s in score if panel.sector[s] in top), key=lambda s: (score[s], s), reverse=True
        )
        return [(s, score[s]) for s in picked[:TOP_STOCKS]]

    return pick


# ------------------------------------------------------------------ 자료


def flow_hash() -> tuple[str, int]:
    h = hashlib.sha256()
    files = sorted(FLOWS.glob("*.csv"))
    for p in files:
        h.update(p.name.encode())
        h.update(p.read_bytes())
    return h.hexdigest(), len(files)


def load_flows(panel: KrPanel, folder: Path | None = None) -> Flows:
    """``folder``(기본 142 수급 자료)의 종목별 수급을 판의 날짜에 맞춘다."""
    at = {d: i for i, d in enumerate(panel.days)}
    net: dict[str, list[float]] = {}
    value: dict[str, list[float]] = {}
    for path in sorted((folder or FLOWS).glob("*.csv")):
        sym = path.stem
        ns = [0.0] * len(panel.days)
        vs = [0.0] * len(panel.days)
        for line in path.read_text(encoding="utf-8").splitlines()[1:]:
            d, frgn, orgn, _prsn, tv = line.split(",")
            i = at.get(_iso(d))
            if i is not None:
                ns[i] = (float(frgn) + float(orgn)) * 1e6  # 백만 원 → 원
                vs[i] = float(tv)
        net[sym], value[sym] = ns, vs
    return Flows(net, value)


def quintiles(
    panel: KrPanel, f: Flows, month_ends: Sequence[int], horizons: Sequence[int] = (1, 3, 6)
) -> dict[str, list[float]]:
    """보고용: 월말 수급 점수 5 분위 → 다음 h 달 평균 수익 (1 = 가장 약함)."""
    out: dict[str, list[list[float]]] = {f"{h}m": [[] for _ in range(5)] for h in horizons}
    for i, t in enumerate(month_ends):
        pool = members(panel, t)
        score = flow_scores(f, t, pool)
        if not score:
            continue
        ordered = sorted(score, key=lambda s: (score[s], s))
        size = len(ordered) // 5
        for h in horizons:
            if i + h >= len(month_ends):
                continue
            u = month_ends[i + h]
            for q in range(5):
                grp = ordered[q * size : (q + 1) * size]
                rets = [_last_px(panel, s, u) / panel.close[s][t] - 1 for s in grp]  # type: ignore[operator]
                out[f"{h}m"][q].append(statistics.fmean(rets))
    return {k: [round(statistics.fmean(v), 4) if v else 0.0 for v in qs] for k, qs in out.items()}


# ------------------------------------------------------------------ 단계


def audit() -> dict[str, object]:
    digest, n = flow_hash()
    panel, _ok = build_panel()
    f = load_flows(panel)
    ever = json.loads((DATA / "ever_universe.json").read_text(encoding="utf-8"))
    start = next(i for i, d in enumerate(panel.days) if d >= FROM)
    month_ends = [
        t
        for t in range(start - 1, len(panel.days) - 1)
        if panel.days[t + 1][:7] != panel.days[t][:7]
    ]
    scored = [len(flow_scores(f, t, members(panel, t))) for t in month_ends]
    covered = []
    for s in f.net:
        traded = [t for t, x in enumerate(panel.close.get(s, [])) if x is not None and t >= start]
        if traded:
            covered.append(sum(1 for t in traded if f.value[s][t] > 0) / len(traded))
    out = {
        "flows_sha256": digest,
        "flow_files": n,
        "ever_universe": len(ever),
        "missing_flow_files": sorted(set(ever) - set(f.net)),
        "scored_per_month_min_max": [min(scored), max(scored)],
        "flow_day_coverage_median": round(statistics.median(covered), 4) if covered else None,
        "flow_day_coverage_min": round(min(covered), 4) if covered else None,
    }
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "audit.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    return out


def _caught(
    run_leaders: Mapping[str, Sequence[str]], panel: KrPanel, month_ends: Sequence[int]
) -> dict[str, int]:
    """보고용: 해마다 종목군 안 가장 크게 오른 5 종목이
    그해 시작 전 3 달 안에 주도주 목록에 있었나."""
    days = panel.days
    pre = during = total = 0
    for y in sorted({d[:4] for d in days if d[:4] >= "2009"}):
        t0 = max((t for t in month_ends if days[t] < f"{y}-01-01"), default=None)
        if t0 is None:
            continue
        t1 = max(i for i, d in enumerate(days) if d[:4] == y)
        pool = members(panel, t0)
        r = {s: _last_px(panel, s, t1) / panel.close[s][t0] - 1 for s in pool}  # type: ignore[operator]
        top = sorted(r, key=lambda s: -r[s])[:5]
        before = {
            x for d, v in run_leaders.items() if f"{int(y) - 1}-10" <= d < f"{y}-01" for x in v
        }
        inside = {x for d, v in run_leaders.items() if d[:4] == y for x in v}
        total += len(top)
        pre += sum(s in before for s in top)
        during += sum(s in inside and s not in before for s in top)
    return {"top5_total": total, "listed_before_year": pre, "listed_during_year": during}


def run() -> dict[str, object]:
    target = OUTPUT / "verdict.json"
    if target.exists():
        raise SystemExit(f"이미 판정했다: {target}")
    sha, dirty = git_state()
    if dirty:
        raise SystemExit("작업 트리가 깨끗하지 않다 — 커밋 뒤에 실행")
    if data_hash()[0] != EXPECTED_DATA:
        raise SystemExit("가격 자료 해시가 문서 141 고정값과 다르다")
    digest, _n = flow_hash()
    if EXPECTED_FLOWS is None or digest != EXPECTED_FLOWS:
        raise SystemExit("수급 자료 해시가 문서 142 고정값과 다르다(또는 아직 고정 전)")
    panel, index_ok = build_panel()
    f = load_flows(panel)
    days = panel.days
    start = next(i for i, d in enumerate(days) if d >= FROM)
    span = days[start:]
    month_ends = [t for t in range(start - 1, len(days) - 1) if days[t + 1][:7] != days[t][:7]]
    fl = simulate_kr(panel, index_ok, flow_picker(f))
    lt = simulate_kr(panel, index_ok)
    ew = equal_weight_pit(panel, fl.universe_log)
    rows = (DATA / "kospi.csv").read_text(encoding="utf-8").splitlines()[1:]
    kospi = {_iso(r.split(",")[0]): float(r.split(",")[1]) for r in rows}
    kospi_hold = [kospi[d] / kospi[span[0]] for d in span]
    end = span[-1]
    full = {
        "FLOW": stats(span, fl.equity, FROM, end),
        "EW": stats(span, ew, FROM, end),
        "LT_KR": stats(span, lt.equity, FROM, end),
        "KOSPI": stats(span, kospi_hold, FROM, end),
    }
    halves = {
        "first": {
            "FLOW": stats(span, fl.equity, FROM, "2016-12-31"),
            "EW": stats(span, ew, FROM, "2016-12-31"),
        },
        "second": {"FLOW": stats(span, fl.equity, SPLIT, end), "EW": stats(span, ew, SPLIT, end)},
    }
    f1 = full["FLOW"]["sharpe"] >= full["EW"]["sharpe"] + 0.20
    f2 = all(h["FLOW"]["cagr"] > h["EW"]["cagr"] for h in halves.values())
    f3 = full["FLOW"]["mdd"] <= max(full["EW"]["mdd"], 0.40)
    yearly: dict[str, dict[str, float]] = {}
    for y in sorted({d[:4] for d in span}):
        idx = [i for i, d in enumerate(span) if d[:4] == y]
        prev = idx[0] - 1 if idx[0] > 0 else idx[0]
        yearly[y] = {
            "FLOW": round(fl.equity[idx[-1]] / fl.equity[prev] - 1, 4),
            "EW": round(ew[idx[-1]] / ew[prev] - 1, 4),
            "LT_KR": round(lt.equity[idx[-1]] / lt.equity[prev] - 1, 4),
        }
    reasons: dict[str, int] = defaultdict(int)
    by_sym: dict[str, float] = defaultdict(float)
    for tr in fl.trades:
        reasons[str(tr["reason"])] += 1
        by_sym[str(tr["symbol"])] += float(str(tr["ret"]))
    holds = [sum(1 for d in span if str(tr["entry"]) <= d <= str(tr["exit"])) for tr in fl.trades]
    months_as_leader: dict[str, int] = defaultdict(int)
    for v in fl.leader_log.values():
        for s in v:
            months_as_leader[s] += 1
    out = {
        "git": sha,
        "flows_sha256": digest,
        "full": full,
        "halves": halves,
        "F1_sharpe": f1,
        "F2_both_halves": f2,
        "F3_mdd": f3,
        "verdict": "PASS" if f1 and f2 and f3 else "REJECT",
        "report": {
            "krw_1m_final": round(1_000_000 * fl.equity[-1] / fl.equity[0]),
            "ew_krw_1m_final": round(1_000_000 * ew[-1] / ew[0]),
            "yearly": yearly,
            "trades": len(fl.trades),
            "hold_days_median": statistics.median(holds) if holds else None,
            "exit_reasons": dict(reasons),
            "top_symbols_sum_ret": dict(sorted(by_sym.items(), key=lambda x: -x[1])[:10]),
            "best_trades": sorted(fl.trades, key=lambda tr: -float(str(tr["ret"])))[:15],
            "worst_trades": sorted(fl.trades, key=lambda tr: float(str(tr["ret"])))[:10],
            "most_months_as_leader": dict(
                sorted(months_as_leader.items(), key=lambda x: -x[1])[:15]
            ),
            "watch": {
                s: {
                    "trades": [tr for tr in fl.trades if tr["symbol"] == s],
                    "months_as_leader": months_as_leader.get(s, 0),
                }
                for s in WATCH
            },
            "flow_score_quintiles_forward": quintiles(panel, f, month_ends),
            "yearly_top5_caught": _caught(fl.leader_log, panel, month_ends),
        },
        "note": "문서 142 1 회 판정. 141 과 같은 종목군·규칙, 주도주 점수만 수급. 배당 제외.",
    }
    OUTPUT.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def main(argv: list[str]) -> int:
    if argv not in (["audit"], ["run"]):
        raise SystemExit("usage: python -m scripts.run_kr_flow audit|run")
    result = audit() if argv == ["audit"] else run()
    sys.stdout.write(json.dumps(result, ensure_ascii=False, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            _reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main(sys.argv[1:]))
