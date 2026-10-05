"""문서 141 — LT(문서 113) 규칙을 유가증권시장에 그대로: 재현 시험. 1회 판정.

사용 예::

    python -m scripts.run_lt_kr audit   # 자료 점검·종목군 수 (수익 없음)
    python -m scripts.run_lt_kr run     # 깨끗한 트리 · 자료 해시 일치 · 판정 파일 없음일 때만

규칙(주도 점수·주도 섹터 3·주도주 10·추세·매달 진입/추매·추세 꺾임/−35% 매도·0.25%)은
``scripts.run_lt_leaders`` 의 함수·상수를 그대로 쓴다. 한국 시장 때문에 더한 것만 여기 있다:

* **그 시점 종목군:** 매달 마지막 거래일에 직전 63 거래일 평균 거래대금 상위 200 보통주
  (상장폐지 종목 포함, 유가증권시장 상장일 이후만). 다음 달 동안 주도주 선정·EW 의 종목군.
* **섹터:** 한국표준산업분류(KSIC) 중분류 → GICS 식 11 개 묶음
  (:data:`KSIC_SECTOR`, 결과 전에 고정).
* **시장 여지:** KOSPI 종가 > 200 일선 (SPY 대신).
* **거래 못 하는 날:** 거래량 0(거래정지)이거나 자료가 없는 날은 사고팔지 않는다.
  정한 매도는 다음 거래일로 넘어간다.
* **상장폐지·시장 이전:** 자료가 기간 끝 전에 끊긴 종목은 마지막 거래일 종가에 판다
  (상장폐지면 정리매매 마지막 날).
"""

from __future__ import annotations

import hashlib
import json
import statistics
import sys
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from scripts.run_a1_train import git_state
from scripts.run_lt_leaders import (
    FEE,
    FROM,
    LOOKS,
    MAX_POS,
    SPLIT,
    STOP_FROM_PEAK,
    TARGET,
    TO,
    TOP_SECTORS,
    TOP_STOCKS,
    stats,
    trend_off,
    trend_up,
)

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "data" / "research" / "kr_stocks"
OUTPUT = REPO / "reports" / "lt_kr"
UNIVERSE = 200
LIQ_DAYS = 63
INDEX_SMA = 200
UNKNOWN = "미분류"
COMMON = "101"
EXPECTED_DATA: str | None = "58588878f98dac2c5532f4de4422d11cad44ed3d591f5510fe0ce47f06f4fe68"
"""자료 묶음 해시 — audit 뒤 문서 141 에 적고 여기에 고정한다."""
WATCH: tuple[str, ...] = (
    "005930",  # 삼성전자
    "000660",  # SK하이닉스
    "012450",  # 한화에어로스페이스
    "267260",  # HD현대일렉트릭
    "034020",  # 두산에너빌리티
    "207940",  # 삼성바이오로직스
    "373220",  # LG에너지솔루션
    "010130",  # 고려아연
)

_GROUPS: dict[str, tuple[int, ...]] = {
    "에너지": (5, 6, 19),
    "소재": (7, 8, 16, 17, 20, 22, 23, 24, 25),
    "산업재": (28, 29, 31, 41, 42, 46, 49, 50, 51, 52, 69, 70, 71, 72, 73, 74, 75, 76),
    "경기소비재": (13, 14, 15, 30, 32, 33, 45, 47, 55, 56, 85, 90, 91, 96),
    "필수소비재": (1, 2, 3, 10, 11, 12),
    "헬스케어": (21, 27, 86),
    "금융": (64, 65, 66),
    "IT": (26, 62),
    "커뮤니케이션": (58, 59, 60, 61, 63),
    "유틸리티": (35, 36, 37),
    "부동산": (68,),
}
KSIC_SECTOR: dict[int, str] = {d: name for name, ds in _GROUPS.items() for d in ds}
"""KSIC 중분류 → GICS 식 섹터. 표에 없는 중분류는 :data:`UNKNOWN`."""


def ksic_sector(code: str) -> str:
    """한국투자증권 ``std_idst_clsf_cd`` (대분류 순번 2 자리 + 중분류 2 자리 + …) → 섹터."""
    if len(code) < 4 or not code[2:4].isdigit():
        return UNKNOWN
    return KSIC_SECTOR.get(int(code[2:4]), UNKNOWN)


def _iso(d: str) -> str:
    return f"{d[:4]}-{d[4:6]}-{d[6:8]}"


@dataclass(slots=True)
class KrPanel:
    days: list[str]
    close: dict[str, list[float | None]]
    """거래한 날의 수정 종가. 거래량 0·자료 없음은 None."""
    value: dict[str, list[float]]
    """거래대금 (없으면 0)."""
    sector: dict[str, str]
    listed_from: dict[str, int]
    """유가증권시장 상장일 이후 첫 색인 — 그 전은 종목군에 넣지 않는다."""
    last: dict[str, int]
    """마지막 거래일 색인."""
    delisted: set[str] = field(default_factory=set)
    """KIND·한국투자증권 기록상 상장폐지 종목 (보고용)."""

    def ends_at(self, sym: str, t: int) -> bool:
        """자료가 기간 끝 전에 끊기는 종목의 마지막 거래일인가."""
        return t == self.last[sym] and t < len(self.days) - 1


def members(panel: KrPanel, t: int, size: int = UNIVERSE) -> list[str]:
    """그 시점 종목군: 그날 거래했고 주도 점수를 낼 수 있는(1·3·6 달 수익) 보통주 중
    직전 ``LIQ_DAYS`` 거래일 평균 거래대금 상위 ``size``."""
    lo = max(0, t - LIQ_DAYS + 1)
    ranked: list[tuple[float, str]] = []
    for sym, xs in panel.close.items():
        if t < panel.listed_from[sym] or xs[t] is None:
            continue
        if any(t - k < panel.listed_from[sym] or xs[t - k] is None for k in LOOKS):
            continue
        avg = sum(panel.value[sym][lo : t + 1]) / (t + 1 - lo)
        ranked.append((avg, sym))
    ranked.sort(key=lambda x: (-x[0], x[1]))
    return [s for _, s in ranked[:size]]


def leaders_pit(panel: KrPanel, t: int, pool: Sequence[str]) -> list[tuple[str, float]]:
    """문서 113 의 ``leaders`` 와 같은 계산을 그 시점 종목군 ``pool`` 안에서.

    섹터를 모르는 종목은 점수 순위에는 들어가지만 주도 섹터 후보가 되지 못한다."""
    rets: dict[int, dict[str, float]] = {}
    for k in LOOKS:
        rets[k] = {}
        for s in pool:
            a, b = panel.close[s][t], panel.close[s][t - k]
            if a is not None and b is not None:
                rets[k][s] = a / b - 1
    common = set.intersection(*(set(v) for v in rets.values()))
    if len(common) < 50:
        return []
    score: dict[str, float] = defaultdict(float)
    for k in LOOKS:
        ordered = sorted(common, key=lambda s: (rets[k][s], s))
        n = len(ordered)
        for i, s in enumerate(ordered):
            score[s] += i / (n - 1) / len(LOOKS)
    by_sector: dict[str, list[float]] = defaultdict(list)
    for s in common:
        if panel.sector[s] != UNKNOWN:
            by_sector[panel.sector[s]].append(score[s])
    top = sorted(by_sector, key=lambda x: (statistics.median(by_sector[x]), x), reverse=True)[
        :TOP_SECTORS
    ]
    picked = sorted(
        (s for s in common if panel.sector[s] in top), key=lambda s: (score[s], s), reverse=True
    )
    return [(s, score[s]) for s in picked[:TOP_STOCKS]]


@dataclass(slots=True)
class Run:
    equity: list[float] = field(default_factory=list)
    trades: list[dict[str, object]] = field(default_factory=list)
    leader_log: dict[str, list[str]] = field(default_factory=dict)
    universe_log: dict[str, list[str]] = field(default_factory=dict)


def _last_px(panel: KrPanel, sym: str, t: int) -> float:
    xs = panel.close[sym]
    while xs[t] is None:
        t -= 1
    return xs[t]  # type: ignore[return-value]


def simulate_kr(panel: KrPanel, index_ok: Sequence[bool]) -> Run:
    """문서 113 ``simulate`` 와 같은 순서(어제 신호 → 오늘 종가 실행 → 오늘 종가로 내일 할 일).

    다른 점: 거래 못 하는 날은 사고팔지 않는다(매도는 넘어감), 상장폐지 종목은 마지막 거래일에 판다,
    주도주는 그 시점 종목군에서 고른다."""
    days = panel.days
    start = next(i for i, d in enumerate(days) if d >= FROM)
    cash = 1.0
    pos: dict[str, list[float]] = {}  # sym -> [수량, 원가, 진입 뒤 최고 종가, 진입 색인]
    run = Run()
    pending_sell: dict[str, str] = {}
    pending_buy: list[str] | None = None

    def sell(sym: str, t: int, reason: str) -> None:
        nonlocal cash
        q, cost, _peak, i0 = pos.pop(sym)
        proceeds = q * _last_px(panel, sym, t) * (1 - FEE)
        cash += proceeds
        run.trades.append(
            {
                "symbol": sym,
                "entry": days[int(i0)],
                "exit": days[t],
                "ret": round(proceeds / cost - 1, 4),
                "reason": reason,
            }
        )

    for t in range(start, len(days)):
        d = days[t]
        # 1) 어제 정한 매도·매수를 오늘 종가에 (거래한 종목만)
        for sym in sorted(pending_sell):
            if sym in pos and panel.close[sym][t] is not None:
                sell(sym, t, pending_sell.pop(sym))
        pending_sell = {s: r for s, r in pending_sell.items() if s in pos}
        equity = cash + sum(q * _last_px(panel, s, t) for s, (q, *_r) in pos.items())
        if pending_buy is not None:
            for sym in list(pos) + pending_buy:
                p = panel.close[sym][t]
                if p is None or sym in pending_sell:
                    continue
                if sym in pos:
                    q, cost, peak, i0 = pos[sym]
                    gap = TARGET * equity - q * p
                    if gap > 0 and cash > 0:
                        amt = min(gap, cash)
                        pos[sym] = [q + amt * (1 - FEE) / p, cost + amt, peak, i0]
                        cash -= amt
                elif len(pos) < MAX_POS and cash > 0:
                    amt = min(TARGET * equity, cash)
                    pos[sym] = [amt * (1 - FEE) / p, amt, p, t]
                    cash -= amt
            pending_buy = None
        # 1b) 자료가 끊기는 종목(상장폐지 등): 마지막 거래일 종가에 판다
        for sym in sorted(pos):
            if panel.ends_at(sym, t):
                pending_sell.pop(sym, None)
                sell(sym, t, "delisted")
        # 2) 오늘 종가로 내일 할 일
        for sym, rec in pos.items():
            p_now = panel.close[sym][t]
            if p_now is None:
                continue
            rec[2] = max(rec[2], p_now)
            if p_now <= (1 - STOP_FROM_PEAK) * rec[2]:
                pending_sell[sym] = "stop_35"
            elif trend_off(panel.close[sym], t) and sym not in pending_sell:
                pending_sell[sym] = "trend_off"
        month_end = t + 1 < len(days) and days[t + 1][:7] != d[:7]
        if month_end:
            pool = members(panel, t)
            run.universe_log[d] = pool
            lead = leaders_pit(panel, t, pool)
            run.leader_log[d] = [s for s, _ in lead]
            if index_ok[t]:
                pending_buy = [
                    s
                    for s, _ in lead
                    if s not in pos and s not in pending_sell and trend_up(panel.close[s], t)
                ]
        run.equity.append(cash + sum(q * _last_px(panel, s, t) for s, (q, *_r) in pos.items()))
    return run


def equal_weight_pit(panel: KrPanel, universe_log: Mapping[str, Sequence[str]]) -> list[float]:
    """그 시점 종목군을 매달 똑같은 비중으로.

    월말 종목군 → 다음 거래일 종가에 맞춤, 바뀐 금액에 0.25%.

    거래 못 하는 종목은 그날 그대로 둔다. 상장폐지 종목은 마지막 거래일 종가에 현금이 된다."""
    days = panel.days
    start = next(i for i, d in enumerate(days) if d >= FROM)
    hold: dict[str, float] = {}
    cash, out = 1.0, []
    target: Sequence[str] | None = universe_log.get(days[start - 1]) or members(panel, start - 1)
    for t in range(start, len(days)):
        if target is not None:
            values = {s: q * _last_px(panel, s, t) for s, q in hold.items()}
            stuck = {s for s in hold if panel.close[s][t] is None}
            tradable = [s for s in target if panel.close[s][t] is not None]
            if tradable:
                free = cash + sum(v for s, v in values.items() if s not in stuck)
                each = free / len(tradable)
                moved = sum(abs(each - values.get(s, 0.0)) for s in tradable) + sum(
                    v for s, v in values.items() if s not in stuck and s not in tradable
                )
                free -= moved * FEE
                each = free / len(tradable)
                new = {s: hold[s] for s in stuck}
                for s in tradable:
                    new[s] = new.get(s, 0.0) + each / panel.close[s][t]  # type: ignore[operator]
                hold, cash = new, 0.0
            target = None
        for s in sorted(hold):
            if t >= panel.last[s] and panel.last[s] < len(days) - 1:
                cash += hold.pop(s) * _last_px(panel, s, t)
        out.append(cash + sum(q * _last_px(panel, s, t) for s, q in hold.items()))
        if t + 1 < len(days) and days[t + 1][:7] != days[t][:7]:
            target = universe_log.get(days[t]) or members(panel, t)
    return out


# ------------------------------------------------------------------ 자료


def data_files() -> list[Path]:
    return sorted([DATA / "info.json", DATA / "kospi.csv", *(DATA / "prices").glob("*.csv")])


def data_hash() -> tuple[str, int]:
    h = hashlib.sha256()
    files = data_files()
    for p in files:
        h.update(p.name.encode())
        h.update(p.read_bytes())
    return h.hexdigest(), len(files)


def build_panel() -> tuple[KrPanel, list[bool]]:
    info = json.loads((DATA / "info.json").read_text(encoding="utf-8"))
    rows = (DATA / "kospi.csv").read_text(encoding="utf-8").splitlines()[1:]
    kospi = {_iso(r.split(",")[0]): float(r.split(",")[1]) for r in rows}
    days = sorted(d for d in kospi if d <= TO)
    at = {d: i for i, d in enumerate(days)}
    close: dict[str, list[float | None]] = {}
    value: dict[str, list[float]] = {}
    sector: dict[str, str] = {}
    listed_from: dict[str, int] = {}
    last: dict[str, int] = {}
    delisted: set[str] = set()
    for path in sorted((DATA / "prices").glob("*.csv")):
        sym = path.stem
        v = info.get(sym)
        if v is None or v.get("kind") != COMMON:
            continue
        xs: list[float | None] = [None] * len(days)
        vs = [0.0] * len(days)
        seen = -1
        for line in path.read_text(encoding="utf-8").splitlines()[1:]:
            d, c, vol, val = line.split(",")
            i = at.get(_iso(d))
            if i is None or float(vol) <= 0:
                continue
            xs[i], vs[i] = float(c), float(val)
            seen = max(seen, i)
        if seen < 0:
            continue
        lstg = v.get("lstg") or "19000101"
        listed_from[sym] = next((i for i, d in enumerate(days) if d >= _iso(lstg)), len(days))
        close[sym], value[sym] = xs, vs
        sector[sym] = ksic_sector(v.get("ksic", ""))
        last[sym] = seen
        if v.get("kind_delisted") or v.get("abol"):
            delisted.add(sym)
    sma: list[float | None] = []
    series = [kospi[d] for d in days]
    for i in range(len(series)):
        sma.append(
            sum(series[i - INDEX_SMA + 1 : i + 1]) / INDEX_SMA if i >= INDEX_SMA - 1 else None
        )
    index_ok = [m is not None and c > m for c, m in zip(series, sma, strict=True)]
    return KrPanel(days, close, value, sector, listed_from, last, delisted), index_ok


# ------------------------------------------------------------------ 단계


def audit() -> dict[str, object]:
    digest, n = data_hash()
    panel, ok = build_panel()
    start = next(i for i, d in enumerate(panel.days) if d >= FROM)
    month_ends = [
        t
        for t in range(start - 1, len(panel.days) - 1)
        if panel.days[t + 1][:7] != panel.days[t][:7]
    ]
    sizes: list[int] = []
    delisted_in: set[str] = set()
    sectors: dict[str, int] = defaultdict(int)
    for t in month_ends:
        pool = members(panel, t)
        sizes.append(len(pool))
        delisted_in |= {s for s in pool if s in panel.delisted}
        for s in pool:
            sectors[panel.sector[s]] += 1
    total = sum(sectors.values())
    out = {
        "data_sha256": digest,
        "files": n,
        "stocks": len(panel.close),
        "delisted_stocks": len(panel.delisted),
        "days": [panel.days[0], panel.days[-1], len(panel.days)],
        "month_ends": len(month_ends),
        "universe_size_min_max": [min(sizes), max(sizes)],
        "delisted_ever_in_universe": len(delisted_in),
        "sector_share_in_universe": {k: round(v / total, 3) for k, v in sorted(sectors.items())},
        "index_above_200d_share": round(sum(ok[start:]) / len(ok[start:]), 3),
    }
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "audit.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    return out


def run() -> dict[str, object]:
    target = OUTPUT / "verdict.json"
    if target.exists():
        raise SystemExit(f"이미 판정했다: {target}")
    sha, dirty = git_state()
    if dirty:
        raise SystemExit("작업 트리가 깨끗하지 않다 — 커밋 뒤에 실행")
    digest, _n = data_hash()
    if EXPECTED_DATA is None or digest != EXPECTED_DATA:
        raise SystemExit("자료 해시가 문서 141 고정값과 다르다(또는 아직 고정 전)")
    panel, index_ok = build_panel()
    days = panel.days
    start = next(i for i, d in enumerate(days) if d >= FROM)
    span = days[start:]
    lt = simulate_kr(panel, index_ok)
    ew = equal_weight_pit(panel, lt.universe_log)
    rows = (DATA / "kospi.csv").read_text(encoding="utf-8").splitlines()[1:]
    kospi = {_iso(r.split(",")[0]): float(r.split(",")[1]) for r in rows}
    kospi_hold = [kospi[d] / kospi[span[0]] for d in span]
    end = span[-1]
    full = {
        "LT": stats(span, lt.equity, FROM, end),
        "EW": stats(span, ew, FROM, end),
        "KOSPI": stats(span, kospi_hold, FROM, end),
    }
    halves = {
        "first": {
            "LT": stats(span, lt.equity, FROM, "2016-12-31"),
            "EW": stats(span, ew, FROM, "2016-12-31"),
        },
        "second": {"LT": stats(span, lt.equity, SPLIT, end), "EW": stats(span, ew, SPLIT, end)},
    }
    l1 = full["LT"]["sharpe"] >= full["EW"]["sharpe"] + 0.20
    l2 = all(h["LT"]["cagr"] > h["EW"]["cagr"] for h in halves.values())
    l3 = full["LT"]["mdd"] <= max(full["EW"]["mdd"], 0.40)
    yearly: dict[str, dict[str, float]] = {}
    for y in sorted({d[:4] for d in span}):
        idx = [i for i, d in enumerate(span) if d[:4] == y]
        prev = idx[0] - 1 if idx[0] > 0 else idx[0]
        yearly[y] = {
            "LT": round(lt.equity[idx[-1]] / lt.equity[prev] - 1, 4),
            "EW": round(ew[idx[-1]] / ew[prev] - 1, 4),
        }
    reasons: dict[str, int] = defaultdict(int)
    by_sym: dict[str, float] = defaultdict(float)
    for tr in lt.trades:
        reasons[str(tr["reason"])] += 1
        by_sym[str(tr["symbol"])] += float(str(tr["ret"]))
    holds = [sum(1 for d in span if str(tr["entry"]) <= d <= str(tr["exit"])) for tr in lt.trades]
    months_as_leader: dict[str, int] = defaultdict(int)
    for v in lt.leader_log.values():
        for s in v:
            months_as_leader[s] += 1
    out = {
        "git": sha,
        "data_sha256": digest,
        "full": full,
        "halves": halves,
        "L1_sharpe": l1,
        "L2_both_halves": l2,
        "L3_mdd": l3,
        "verdict": "PASS" if l1 and l2 and l3 else "REJECT",
        "report": {
            "krw_1m_final": round(1_000_000 * lt.equity[-1] / lt.equity[0]),
            "ew_krw_1m_final": round(1_000_000 * ew[-1] / ew[0]),
            "yearly": yearly,
            "trades": len(lt.trades),
            "hold_days_median": statistics.median(holds) if holds else None,
            "exit_reasons": dict(reasons),
            "top_symbols_sum_ret": dict(sorted(by_sym.items(), key=lambda x: -x[1])[:10]),
            "watch": {
                s: {
                    "trades": [tr for tr in lt.trades if tr["symbol"] == s],
                    "months_as_leader": months_as_leader.get(s, 0),
                }
                for s in WATCH
            },
            "best_trades": sorted(lt.trades, key=lambda tr: -float(str(tr["ret"])))[:15],
            "worst_trades": sorted(lt.trades, key=lambda tr: float(str(tr["ret"])))[:10],
            "most_months_as_leader": dict(
                sorted(months_as_leader.items(), key=lambda x: -x[1])[:15]
            ),
        },
        "note": "문서 141 1 회 판정. 배당 제외(수정주가) — LT·EW·KOSPI 모두 가격만.",
    }
    OUTPUT.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def main(argv: list[str]) -> int:
    if argv not in (["audit"], ["run"]):
        raise SystemExit("usage: python -m scripts.run_lt_kr audit|run")
    result = audit() if argv == ["audit"] else run()
    sys.stdout.write(json.dumps(result, ensure_ascii=False, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            _reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main(sys.argv[1:]))
