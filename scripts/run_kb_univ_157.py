"""문서 157 — KB(139) 진입 우위를 안 본 자료로: 폐지 코인을 포함한 알트코인 종목군(월말 거래대금
상위 20, 이미 본 10 종목 제외), 4시간봉. 1회 판정.

사용 예::

    python -m scripts.run_kb_univ_157 audit   # 자료 해시 · 종목군 · 신호 수(수익 없음)
    python -m scripts.run_kb_univ_157 run     # 깨끗한 트리(IDEAS.md 예외) · 해시 일치

* 엔진·설정·비용은 139 그대로(``simulate_box`` · ``BoxConfig()`` · 편도 0.15%). 바뀌는 것:
  그달 종목군에 든 페어만 새로 진입(``can_enter``), 이미 든 묶음은 종목군에서 빠져도 규칙대로
  청산, 자료가 끝나면(상장폐지·거래 중단) 마지막 봉 종가에 정리하고 R 을 센다(``close_at_end``).
* 주 기준(진입 단위 — 156 판정 뒤 사용자 지적: 노출이 다른 보유와 샤프 비교는 공정하지 않다):
  진입 100 번 이상 · 진입당 평균 R > 0 · 평균 − 2 × 표준오차(진입 주로 묶음) > 0 ·
  두 반쪽(2018 ~ 2021 · 2022 ~ 2026-08, 진입 날짜) 평균 R 모두 > 0.
"""

from __future__ import annotations

import hashlib
import json
import math
import statistics
import sys
from collections import defaultdict
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from scripts.run_ema_box import BASE, FROM, SLEEVE, TO
from scripts.run_kb_alts_156 import load_4h
from scripts.run_kq_154 import dirty_paths, foreign_changes

from auto_trading.backtest.ema_box import simulate_box

REPO = Path(__file__).resolve().parent.parent
VISION = REPO / "data" / "external" / "binance_vision" / "spot"
UNIV = REPO / "data" / "research" / "alt_universe"
OUTPUT = REPO / "reports" / "kb_univ_157"
EXPECTED_DATA: str | None = None
"""자료 묶음 해시 — audit 뒤 문서 157 에 적고 여기에 고정한다."""
MIN_ENTRIES = 100
SPLIT = "2022-01-01"


def member_months() -> dict[str, set[str]]:
    """{페어: 종목군에 든 달 'YYYY-MM'} — 월말 me 의 상위 20 이 다음 달 종목군."""
    members = json.loads((UNIV / "members.json").read_text(encoding="utf-8"))
    out: dict[str, set[str]] = defaultdict(set)
    for me, syms in members.items():
        y, m = int(me[:4]), int(me[5:7])
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
        for s in syms:
            out[s].add(f"{y:04d}-{m:02d}")
    return dict(out)


def _month(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m")


def _day(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m-%d")


def _member_check(months: set[str]) -> Callable[[int], bool]:
    def check(t: int) -> bool:
        return _month(t) in months

    return check


def entries_r() -> tuple[list[tuple[str, str, float]], dict[str, Any]]:
    """[(페어, 진입 날짜, R)] 과 보고용 수."""
    months = member_months()
    out: list[tuple[str, str, float]] = []
    info: dict[str, Any] = {"signals": 0, "end_exits": 0, "pairs": len(months)}
    for sym in sorted(months):
        mm = months[sym]
        b = load_4h(sym)
        if not b.t:
            continue
        r = simulate_box(
            b,
            from_ms=FROM,
            to_ms=TO,
            initial=SLEEVE,
            cfg=BASE,
            can_enter=_member_check(mm),
            close_at_end=True,
        )
        info["signals"] += r.signals
        info["end_exits"] += r.end_exits
        out += [(sym, _day(t0), x) for t0, x in zip(r.r_entry_ms, r.r_multiples, strict=True)]
    return out, info


def week(d: str) -> str:
    y, w, _ = date.fromisoformat(d).isocalendar()
    return f"{y}-{w:02d}"


def verdict(rows: Sequence[tuple[str, str, float]]) -> dict[str, Any]:
    """주 기준 (순수)."""
    if len(rows) < MIN_ENTRIES:
        return {"verdict": "DATA_INSUFFICIENT", "n": len(rows)}
    rs = [x for _, _, x in rows]
    mean = statistics.fmean(rs)
    groups: dict[str, float] = defaultdict(float)
    for _, d, x in rows:
        groups[week(d)] += x - mean
    g = len(groups)
    se = math.sqrt(sum(v * v for v in groups.values()) / len(rs) ** 2 * (g / (g - 1)))
    first = [x for _, d, x in rows if d < SPLIT]
    second = [x for _, d, x in rows if d >= SPLIT]
    halves = [statistics.fmean(h) if h else 0.0 for h in (first, second)]
    ok = mean > 0 and mean - 2 * se > 0 and all(h > 0 for h in halves)
    return {
        "verdict": "PASS" if ok else "REJECT",
        "n": len(rs),
        "mean_R": round(mean, 4),
        "se": round(se, 4),
        "lo2": round(mean - 2 * se, 4),
        "halves_mean_R": [round(h, 4) for h in halves],
        "halves_n": [len(first), len(second)],
        "win": round(sum(x > 0 for x in rs) / len(rs), 3),
        "median_R": round(statistics.median(rs), 3),
    }


def data_files() -> list[Path]:
    syms = sorted(member_months())
    files = [UNIV / "members.json"]
    files += sorted(p for s in syms for p in (VISION / s / "4h").glob("*.zip"))
    return files


def data_hash() -> tuple[str, int]:
    h = hashlib.sha256()
    files = data_files()
    for p in files:
        h.update(p.name.encode())
        h.update(p.read_bytes())
    return h.hexdigest(), len(files)


def audit() -> dict[str, Any]:
    digest, n = data_hash()
    months = member_months()
    union = json.loads((UNIV / "union.json").read_text(encoding="utf-8"))
    gone = [s for s, v in union.items() if v["last_day"] < "2026-08-31"]
    rows, info = entries_r()
    return {
        "data_sha256": digest,
        "files": n,
        "pairs_ever_in_universe": len(months),
        "pairs_stopped_trading": len(gone),
        "signals": info["signals"],
        "entries": len(rows),  # 수만 — R 은 보지 않는다
        "entries_halves": [
            sum(d < SPLIT for _, d, _ in rows),
            sum(d >= SPLIT for _, d, _ in rows),
        ],
        "pairs_with_entries": len({s for s, _, _ in rows}),
    }


def run() -> dict[str, Any]:
    target = OUTPUT / "verdict.json"
    if target.exists():
        raise SystemExit(f"이미 판정했다: {target}")
    others = foreign_changes()
    if others:
        raise SystemExit(f"작업 트리가 깨끗하지 않다 — 커밋 뒤에 실행: {others}")
    from scripts.run_a1_train import git_state

    sha, _ = git_state()
    digest, _n = data_hash()
    if EXPECTED_DATA is None or digest != EXPECTED_DATA:
        raise SystemExit(f"자료 해시가 다르다: {digest}")
    rows, info = entries_r()
    union = json.loads((UNIV / "union.json").read_text(encoding="utf-8"))
    gone = {s for s, v in union.items() if v["last_day"] < "2026-08-31"}
    rs = sorted(x for _, _, x in rows)
    k = max(1, len(rs) // 50)
    by_year: dict[str, list[float]] = defaultdict(list)
    for _, d, x in rows:
        by_year[d[:4]].append(x)
    gone_r = [x for s, _, x in rows if s in gone]
    result: dict[str, Any] = {
        "commit": sha,
        "data_sha256": digest,
        "uncommitted_allowed": dirty_paths(),
        **verdict(rows),
        "mean_R_without_top2pct": round(statistics.fmean(rs[:-k]), 4) if len(rs) > k else None,
        "stopped_pairs_entries": len(gone_r),
        "stopped_pairs_mean_R": round(statistics.fmean(gone_r), 4) if gone_r else None,
        "end_exits": info["end_exits"],
        "by_year": {y: [len(v), round(statistics.fmean(v), 3)] for y, v in sorted(by_year.items())},
        "pairs_with_entries": len({s for s, _, _ in rows}),
    }
    OUTPUT.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


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
