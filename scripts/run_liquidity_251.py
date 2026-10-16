"""문서 251 — 순유동성(연준 자산 − TGA − 역레포) 변화 → 다음 몇 주 QQQ · BTC. 1회 판정.

사용 예::

    python -m scripts.run_liquidity_251 fetch   # FRED 세 줄 받기 (수익 안 봄)
    python -m scripts.run_liquidity_251 run     # 깨끗한 트리 · 자료 해시 · 판정 파일 없음일 때만

* 순유동성(수요일, 십억 달러) = WALCL − WTREGEN − RRPONTSYD(그 수요일까지 마지막 값, 없으면 0).
  WALCL · WTREGEN 은 H.4.1(목요일 공표) 수요일 값이다. 공휴일로 공표가 밀려도 닿도록
  수요일 + 5 일(월요일) 이후 첫 거래일 종가에서만 쓴다.
* 신호 = 순유동성 k 주 변화 > 0 (늘어남) / ≤ 0 (줄어듦). 주 판정 k = 4, 앞으로 h = 4 주.
* 차이 = 늘어남 주의 h 주 수익 평균 − 줄어듦 주의 평균. 8 주 원형 블록 부트스트랩 2,000 번(시드 251)
  한쪽 p. 두 자산이라 p ≤ 0.025.
* 필터(설명용): 늘어남 주만 보유, 아니면 현금(QQQ 는 DTB3 이자, BTC 는 0), 바꿀 때 0.1%.
"""

from __future__ import annotations

import bisect
import csv
import datetime as dt
import hashlib
import json
import math
import random
import statistics
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scripts.run_a1_train import git_state

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "data" / "research" / "liquidity_251"
TRADFI = REPO / "data" / "research" / "tradfi"
BTC = REPO / "data" / "research" / "binance_daily" / "BTCUSDT.csv"
OUT = REPO / "reports" / "liquidity_251"
SERIES = ("WALCL", "WTREGEN", "RRPONTSYD")
EXPECTED_DATA: str | None = "0cff1a970bd73a2f943189eac03725ac44f04d6d3bbb50e8e9451768a92544f1"
"""fetch 뒤 문서 251 §1 에 적고 여기 고정한다."""

LAG_DAYS = 5
K_MAIN, H_MAIN = 4, 4
GRID = ((4, 4), (4, 8), (8, 4), (8, 8))
BLOCK = 8
DRAWS = 2000
SEED = 251
ALPHA = 0.025
SWITCH_COST = 0.001
SHARPE_GAIN = 0.10
TO = "2026-08-31"
FROM = {"QQQ": "2008-01-01", "BTC": "2017-08-21"}
SPLIT = "2020-01-01"
EX_FROM, EX_TO = "2020-03-01", "2022-12-31"


@dataclass(frozen=True)
class Row:
    """수요일 하나: 그 주 거래일 · 신호 · h 주 뒤 수익."""

    day: str
    up: bool
    fwd: float


# ------------------------------------------------------------------ 자료


def fetch() -> None:
    import httpx

    DATA.mkdir(parents=True, exist_ok=True)
    for sid in SERIES:
        r = httpx.get(
            f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}",
            headers={"User-Agent": "auto-trading-research"},
            timeout=60,
        )
        r.raise_for_status()
        (DATA / f"{sid}.csv").write_text(r.text, encoding="utf-8")
    print(json.dumps({"hash": data_hash()}, ensure_ascii=False))


def data_hash() -> str:
    h = hashlib.sha256()
    for sid in SERIES:
        h.update(sid.encode())
        h.update((DATA / f"{sid}.csv").read_bytes())
    return h.hexdigest()


def _fred(sid: str) -> dict[str, float]:
    with (DATA / f"{sid}.csv").open(encoding="utf-8") as f:
        return {
            r["observation_date"]: float(r[sid])
            for r in csv.DictReader(f)
            if r[sid] not in ("", ".")
        }


def _closes(path: Path) -> dict[str, float]:
    with path.open(encoding="utf-8") as f:
        return {r["date"]: float(r["close"]) for r in csv.DictReader(f) if float(r["close"]) > 0}


def _dtb3() -> dict[str, float]:
    with (TRADFI / "DTB3.csv").open(encoding="utf-8") as f:
        return {
            r["date"]: float(r["close"]) for r in csv.DictReader(f) if r["close"] not in ("", ".")
        }


def net_liquidity(
    walcl: Mapping[str, float], tga: Mapping[str, float], rrp: Mapping[str, float]
) -> list[tuple[str, float]]:
    """수요일마다 순유동성(십억 달러). WALCL · WTREGEN 은 백만, RRP 는 십억 단위."""
    rdays = sorted(rrp)
    out = []
    for d in sorted(walcl):
        if d not in tga:
            continue
        j = bisect.bisect_right(rdays, d) - 1
        r = rrp[rdays[j]] if j >= 0 else 0.0
        out.append((d, walcl[d] / 1000 - tga[d] / 1000 - r))
    return out


def trade_day(days: Sequence[str], wed: str, lag: int = LAG_DAYS) -> int | None:
    """수요일 + ``lag`` 일 이후 첫 거래일 번호."""
    first = (dt.date.fromisoformat(wed) + dt.timedelta(days=lag)).isoformat()
    i = bisect.bisect_left(days, first)
    return i if i < len(days) else None


# ------------------------------------------------------------------ 표 만들기


def build_rows(
    nl: Sequence[tuple[str, float]],
    days: Sequence[str],
    close: Sequence[float],
    k: int,
    h: int,
    start: str,
    to: str = TO,
) -> list[Row]:
    """k 주 변화 신호 · 그 주 거래일 종가에서 h 주 뒤 거래일 종가까지 수익(끝이 ``to`` 이하)."""
    rows = []
    for w in range(k, len(nl) - h):
        i = trade_day(days, nl[w][0])
        j = trade_day(days, nl[w + h][0])
        if i is None or j is None or days[i] < start or days[j] > to or j <= i:
            continue
        rows.append(Row(days[i], nl[w][1] - nl[w - k][1] > 0, close[j] / close[i] - 1))
    return rows


def spread(rows: Sequence[Row]) -> float | None:
    up = [r.fwd for r in rows if r.up]
    dn = [r.fwd for r in rows if not r.up]
    if not up or not dn:
        return None
    return statistics.fmean(up) - statistics.fmean(dn)


def boot_p(rows: Sequence[Row], block: int = BLOCK, draws: int = DRAWS, seed: int = SEED) -> float:
    """원형 블록 부트스트랩으로 차이 ≤ 0 인 비율(한쪽 p)."""
    n = len(rows)
    if n < block:
        return 1.0
    rng = random.Random(seed)
    nb = math.ceil(n / block)
    bad = 0
    for _ in range(draws):
        pick: list[Row] = []
        for _b in range(nb):
            s = rng.randrange(n)
            pick.extend(rows[(s + t) % n] for t in range(block))
        sp = spread(pick[:n])
        if sp is None or sp <= 0:
            bad += 1
    return bad / draws


def part(rows: Sequence[Row], lo: str = "", hi: str = "9999") -> list[Row]:
    return [r for r in rows if lo <= r.day < hi]


def excl(rows: Sequence[Row], lo: str = EX_FROM, hi: str = EX_TO) -> list[Row]:
    return [r for r in rows if not lo <= r.day <= hi]


def stats(rows: Sequence[Row]) -> dict[str, Any]:
    up = [r.fwd for r in rows if r.up]
    sp = spread(rows)
    return {
        "n": len(rows),
        "n_up": len(up),
        "up_mean": round(statistics.fmean(up), 5) if up else None,
        "down_mean": round(statistics.fmean([r.fwd for r in rows if not r.up]), 5)
        if len(up) < len(rows)
        else None,
        "spread": None if sp is None else round(sp, 5),
    }


def judge(full_p: float, full_sp: float | None, pre_sp: float | None, ex_sp: float | None) -> str:
    """사전 등록 §4 (순수)."""
    if full_sp is None or full_sp <= 0:
        return "불리"
    if full_p <= ALPHA and pre_sp is not None and pre_sp > 0 and ex_sp is not None and ex_sp > 0:
        return "지지"
    return "불명"


# ------------------------------------------------------------------ 필터 (설명용)


def filter_path(
    nl: Sequence[tuple[str, float]],
    days: Sequence[str],
    close: Sequence[float],
    cash_rate: Mapping[str, float] | None,
    start: str,
    to: str = TO,
    k: int = K_MAIN,
) -> tuple[list[str], list[float], list[float]]:
    """주마다 (거래일, 보유 수익, 필터 수익). 필터 = 늘어남이면 보유, 아니면 현금, 바꿀 때 비용."""
    out_d: list[str] = []
    hold: list[float] = []
    filt: list[float] = []
    rdays = sorted(cash_rate) if cash_rate else []
    prev_in: bool | None = None
    for w in range(k, len(nl) - 1):
        i = trade_day(days, nl[w][0])
        j = trade_day(days, nl[w + 1][0])
        if i is None or j is None or days[i] < start or days[j] > to or j <= i:
            continue
        r = close[j] / close[i] - 1
        on = nl[w][1] - nl[w - k][1] > 0
        cash = 0.0
        if cash_rate:
            q = bisect.bisect_right(rdays, days[i]) - 1
            cash = cash_rate[rdays[q]] / 100 / 52 if q >= 0 else 0.0
        f = r if on else cash
        if prev_in is not None and on != prev_in:
            f -= SWITCH_COST
        prev_in = on
        out_d.append(days[i])
        hold.append(r)
        filt.append(f)
    return out_d, hold, filt


def perf(rets: Sequence[float]) -> dict[str, float]:
    if len(rets) < 2:
        return {"sharpe": 0.0, "cagr": 0.0, "mdd": 0.0}
    eq, peak, mdd = 1.0, 1.0, 0.0
    for r in rets:
        eq *= 1 + r
        peak = max(peak, eq)
        mdd = max(mdd, 1 - eq / peak)
    sd = statistics.stdev(rets)
    return {
        "sharpe": round(statistics.fmean(rets) / sd * math.sqrt(52), 3) if sd > 0 else 0.0,
        "cagr": round(eq ** (52 / len(rets)) - 1, 4),
        "mdd": round(mdd, 4),
    }


def filter_report(d: Sequence[str], hold: Sequence[float], filt: Sequence[float]) -> dict[str, Any]:
    def cut(lo: str, hi: str) -> dict[str, Any]:
        idx = [n for n, x in enumerate(d) if lo <= x < hi]
        return {
            "hold": perf([hold[n] for n in idx]),
            "filter": perf([filt[n] for n in idx]),
            "weeks": len(idx),
        }

    full, pre = cut("", "9999"), cut("", SPLIT)
    ok = (
        full["filter"]["sharpe"] >= full["hold"]["sharpe"] + SHARPE_GAIN
        and pre["filter"]["sharpe"] >= pre["hold"]["sharpe"]
    )
    return {"full": full, "pre": pre, "post": cut(SPLIT, "9999"), "filter_better": ok}


# ------------------------------------------------------------------ 실행


def assets() -> dict[str, tuple[list[str], list[float], dict[str, float] | None]]:
    q = _closes(TRADFI / "QQQ_adj.csv")
    b = _closes(BTC)
    qd, bd = sorted(q), sorted(b)
    return {
        "QQQ": (qd, [q[x] for x in qd], _dtb3()),
        "BTC": (bd, [b[x] for x in bd], None),
    }


def one_asset(
    nl: Sequence[tuple[str, float]], name: str, days: list[str], close: list[float], cash: Any
) -> dict[str, Any]:
    grid: dict[str, dict[str, Any]] = {}
    for k, h in GRID:
        rows = build_rows(nl, days, close, k, h, FROM[name])
        grid[f"k{k}_h{h}"] = {
            "full": stats(rows),
            "p": boot_p(rows),
            "pre": stats(part(rows, hi=SPLIT)),
            "post": stats(part(rows, lo=SPLIT)),
            "ex_2020_22": stats(excl(rows)),
        }
    m = grid[f"k{K_MAIN}_h{H_MAIN}"]
    verdict = judge(m["p"], m["full"]["spread"], m["pre"]["spread"], m["ex_2020_22"]["spread"])
    return {
        "verdict": verdict,
        "main": m,
        "grid": grid,
        "filter": filter_report(*filter_path(nl, days, close, cash, FROM[name])),
    }


def run() -> dict[str, Any]:
    target = OUT / "result.json"
    if target.exists():
        raise SystemExit(f"이미 판정했다: {target}")
    sha, dirty = git_state()
    if dirty:
        raise SystemExit("작업 트리가 깨끗하지 않다 — 커밋 뒤에 실행")
    if EXPECTED_DATA is None or data_hash() != EXPECTED_DATA:
        raise SystemExit("자료 해시가 문서 251 §1 과 다르다")
    nl = net_liquidity(_fred("WALCL"), _fred("WTREGEN"), _fred("RRPONTSYD"))
    out: dict[str, Any] = {"git": sha, "data": EXPECTED_DATA, "tests": len(GRID) * 2}
    for name, (days, close, cash) in assets().items():
        out[name] = one_asset(nl, name, days, close, cash)
    out["note"] = (
        "문서 251 1 회 판정. 주 판정 k4 → h4, 2026-08-31 까지. 나머지 격자는 참고(다중 검정 기록)."
    )
    OUT.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else ""
    if cmd == "fetch":
        fetch()
        return 0
    if cmd == "run":
        out = run()
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
