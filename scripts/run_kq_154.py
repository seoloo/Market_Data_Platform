"""문서 154 — 141(가격 주도주) · 142(수급 주도주, 통과) 규칙을 코스닥에 그대로. 1회 판정.

사용 예::

    python -m scripts.run_kq_154 audit   # 자료 해시 · 종목군 · 공포 군집 횟수 (수익 없음)
    python -m scripts.run_kq_154 run     # 깨끗한 트리 · 자료 해시 일치 · 판정 파일 없음

* 141·142 코드(``simulate_kr`` · ``leaders_pit`` · ``flow_picker`` · ``equal_weight_pit`` · 0.25%)
  를 그대로 쓴다. 바뀌는 것: 시장 = 코스닥(그날 소속은 KRX 일별), 시장 여지 = 코스닥 지수 >
  200 일선, 기간 2011-01 ~ 2026-08 (KRX 일별이 2010 년부터), 반쪽 경계 2017-01-01.
* 시험 넷: FLOW-KQ(142 그대로 — 주 시험) · LT-KQ(141 그대로) · 둘의 '작은 섹터 제외' 판
  (그 시점 종목군에서 종목 5 개 미만인 섹터의 종목은 주도주 후보에서 뺀다, IDEAS 1).
"""

from __future__ import annotations

import csv
import gzip
import hashlib
import io
import json
import statistics
import sys
from collections import Counter
from collections.abc import Sequence
from pathlib import Path

from scripts.run_a1_train import git_state
from scripts.run_kr_flow import flow_picker, load_flows
from scripts.run_lt_kr import (
    UNKNOWN,
    KrPanel,
    Picker,
    equal_weight_pit,
    ksic_sector,
    leaders_pit,
    members,
    simulate_kr,
)
from scripts.run_lt_leaders import stats

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "data" / "research" / "kq_stocks"
KRX = REPO / "data" / "research" / "krx" / "ksq"
OUTPUT = REPO / "reports" / "kq_154"
EXPECTED_DATA: str | None = None
"""자료 묶음 해시 — audit 뒤 문서 154 에 적고 여기에 고정한다."""
FROM, SPLIT, TO = "2011-01-01", "2017-01-01", "2026-08-31"
INDEX_SMA = 200
MIN_SECTOR = 5
SPAC = "스팩"
PANIC_DEV, PANIC_SHARE, PANIC_GAP = 0.20, 0.10, 20


def _iso(d: str) -> str:
    return f"{d[:4]}-{d[4:6]}-{d[6:8]}"


def membership() -> dict[str, set[str]]:
    """KRX 일별 → {종목: 그날 코스닥에 있던 날짜(ISO)}."""
    out: dict[str, set[str]] = {}
    for p in sorted(KRX.glob("*.csv.gz")):
        day = _iso(p.name[:8])
        text = gzip.decompress(p.read_bytes()).decode("utf-8")
        for r in csv.DictReader(io.StringIO(text)):
            out.setdefault(r["ISU_CD"], set()).add(day)
    return out


def build_kq_panel() -> tuple[KrPanel, list[bool]]:
    """코스닥 판: 그날 코스닥 소속이고 거래한 날만 수정 종가, 나머지는 None."""
    rows = (DATA / "kosdaq.csv").read_text(encoding="utf-8").splitlines()[1:]
    index = {_iso(r.split(",")[0]): float(r.split(",")[1]) for r in rows}
    days = sorted(d for d in index if d <= TO)
    at = {d: i for i, d in enumerate(days)}
    info = json.loads((DATA / "info.json").read_text(encoding="utf-8"))
    member = membership()
    close: dict[str, list[float | None]] = {}
    value: dict[str, list[float]] = {}
    sector: dict[str, str] = {}
    listed_from: dict[str, int] = {}
    last: dict[str, int] = {}
    delisted: set[str] = set()
    for path in sorted((DATA / "prices").glob("*.csv")):
        sym = path.stem
        v = info.get(sym) or {}
        if v.get("kind") != "101" or SPAC in str(v.get("name", "")):
            continue
        inside = member.get(sym, set())
        xs: list[float | None] = [None] * len(days)
        vs = [0.0] * len(days)
        for line in path.read_text(encoding="utf-8").splitlines()[1:]:
            d, c, vol, val = line.split(",")
            i = at.get(_iso(d))
            if i is None or float(vol) <= 0 or days[i] not in inside:
                continue
            xs[i], vs[i] = float(c), float(val)
        seen = [i for i, x in enumerate(xs) if x is not None]
        if not seen:
            continue
        close[sym], value[sym] = xs, vs
        sector[sym] = ksic_sector(str(v.get("ksic", "")))
        listed_from[sym] = min(at[d] for d in inside if d in at) if inside else seen[0]
        last[sym] = seen[-1]
        if last[sym] < len(days) - 1:
            delisted.add(sym)
    series = [index[d] for d in days]
    sma: list[float | None] = [
        sum(series[i - INDEX_SMA + 1 : i + 1]) / INDEX_SMA if i >= INDEX_SMA - 1 else None
        for i in range(len(series))
    ]
    ok = [m is not None and c > m for c, m in zip(series, sma, strict=True)]
    return KrPanel(days, close, value, sector, listed_from, last, delisted), ok


def min_sector(pick: Picker) -> Picker:
    """그 시점 종목군에서 종목이 ``MIN_SECTOR`` 개 미만인 섹터의 종목을 후보에서 뺀다."""

    def wrapped(panel: KrPanel, t: int, pool: Sequence[str]) -> list[tuple[str, float]]:
        size = Counter(panel.sector[s] for s in pool)
        keep = [
            s for s in pool if panel.sector[s] != UNKNOWN and size[panel.sector[s]] >= MIN_SECTOR
        ]
        return pick(panel, t, keep)

    return wrapped


def panic_episodes(panel: KrPanel, month_ends: Sequence[int]) -> list[str]:
    """IDEAS 14 횟수만: 종목군(그 시점 상위 200)의 10% 이상이 25 일선 −20% 아래인 날, 20 거래일
    넘게 떨어진 첫날들. 날마다 종목군은 그달 시작 월말의 것."""
    out: list[str] = []
    last_hit = -(10**9)
    me = sorted(month_ends)
    pools = {m: members(panel, m) for m in me}
    for t in range(me[0] + 1, len(panel.days)):
        pool = pools[max(m for m in me if m < t)]
        below = 0
        n = 0
        for s in pool:
            xs = panel.close[s]
            w = xs[t - 24 : t + 1]
            if t < 24 or any(x is None for x in w):
                continue
            n += 1
            if w[-1] <= (1 - PANIC_DEV) * sum(w) / 25:  # type: ignore[operator, arg-type]
                below += 1
        if n and below / n >= PANIC_SHARE:
            if t - last_hit > PANIC_GAP:
                out.append(panel.days[t])
            last_hit = t
    return out


def _month_ends(panel: KrPanel) -> list[int]:
    days = panel.days
    start = next(i for i, d in enumerate(days) if d >= FROM)
    return [t for t in range(start - 1, len(days) - 1) if days[t + 1][:7] != days[t][:7]]


def data_files() -> list[Path]:
    files = [DATA / "kosdaq.csv", DATA / "info.json", DATA / "candidates.json"]
    files += sorted((DATA / "prices").glob("*.csv"))
    files += sorted((DATA / "flows").glob("*.csv"))
    files += sorted(KRX.glob("*.csv.gz"))
    return files


def data_hash() -> tuple[str, int]:
    h = hashlib.sha256()
    files = data_files()
    for p in files:
        h.update(p.name.encode())
        h.update(p.read_bytes())
    return h.hexdigest(), len(files)


def audit() -> dict[str, object]:
    digest, n = data_hash()
    panel, _ = build_kq_panel()
    me = _month_ends(panel)
    sizes = [len(members(panel, t)) for t in me]
    in_univ: set[str] = set()
    for t in me:
        in_univ |= set(members(panel, t))
    flows = {p.stem for p in (DATA / "flows").glob("*.csv")}
    small = []
    for t in me[::12]:
        pool = members(panel, t)
        c = Counter(panel.sector[s] for s in pool)
        small.append(sum(1 for k, v in c.items() if v < MIN_SECTOR and k != UNKNOWN))
    return {
        "data_sha256": digest,
        "files": n,
        "stocks_in_panel": len(panel.close),
        "month_ends": len(me),
        "universe_min_mean": [min(sizes), round(statistics.fmean(sizes), 1)],
        "ever_in_universe": len(in_univ),
        "ever_left_kosdaq": len(in_univ & panel.delisted),
        "universe_without_flows": len(in_univ - flows),
        "unknown_sector_share": round(
            sum(1 for s in in_univ if panel.sector[s] == UNKNOWN) / len(in_univ), 3
        ),
        "small_sectors_per_year_sample": small,
        "panic_episodes": panic_episodes(panel, me),
    }


def run() -> dict[str, object]:
    target = OUTPUT / "verdict.json"
    if target.exists():
        raise SystemExit(f"이미 판정했다: {target}")
    sha, dirty = git_state()
    if dirty:
        raise SystemExit("작업 트리가 깨끗하지 않다 — 커밋 뒤에 실행")
    digest, _ = data_hash()
    if EXPECTED_DATA is None or digest != EXPECTED_DATA:
        raise SystemExit(f"자료 해시가 다르다: {digest}")
    panel, ok = build_kq_panel()
    f = load_flows(panel, DATA / "flows")
    runs = {
        "FLOW_KQ": simulate_kr(panel, ok, flow_picker(f), start_date=FROM),
        "LT_KQ": simulate_kr(panel, ok, start_date=FROM),
        "FLOW_KQ_MIN5": simulate_kr(panel, ok, min_sector(flow_picker(f)), start_date=FROM),
        "LT_KQ_MIN5": simulate_kr(panel, ok, min_sector(leaders_pit), start_date=FROM),
    }
    start = next(i for i, d in enumerate(panel.days) if d >= FROM)
    span = panel.days[start:]
    ew = equal_weight_pit(panel, runs["LT_KQ"].universe_log, start_date=FROM)
    rows = (DATA / "kosdaq.csv").read_text(encoding="utf-8").splitlines()[1:]
    idx = {_iso(r.split(",")[0]): float(r.split(",")[1]) for r in rows}
    kosdaq = [idx[d] / idx[span[0]] for d in span]
    end = span[-1]

    def three(eq: Sequence[float]) -> list[dict[str, float]]:
        return [
            stats(span, eq, FROM, end),
            stats(span, eq, FROM, "2016-12-31"),
            stats(span, eq, SPLIT, end),
        ]

    base = three(ew)
    out: dict[str, object] = {
        "commit": sha,
        "data_sha256": digest,
        "EW": base,
        "KOSDAQ": three(kosdaq),
    }
    for name, r in runs.items():
        s = three(r.equity)
        checks = {
            "L1_sharpe_ge_ew_plus_0.20": s[0]["sharpe"] >= base[0]["sharpe"] + 0.20,
            "L2_both_halves_cagr_gt_ew": s[1]["cagr"] > base[1]["cagr"]
            and s[2]["cagr"] > base[2]["cagr"],
            "L3_mdd_le_max_ew_40": s[0]["mdd"] <= max(base[0]["mdd"], 0.40),
        }
        by_year: dict[str, float] = {}
        for y in sorted({d[:4] for d in span}):
            ix = [i for i, d in enumerate(span) if d[:4] == y]
            by_year[y] = round(r.equity[ix[-1]] / r.equity[max(ix[0] - 1, 0)] - 1, 4)
        out[name] = {
            "verdict": "PASS" if all(checks.values()) else "REJECT",
            "checks": checks,
            "stats": s,
            "trades": len(r.trades),
            "by_year": by_year,
        }
    OUTPUT.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else "audit"
    if cmd == "audit":
        print(json.dumps(audit(), ensure_ascii=False, indent=2))
    elif cmd == "run":
        print(json.dumps(run(), ensure_ascii=False, indent=2))
    else:
        raise SystemExit(f"알 수 없는 명령: {cmd}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
