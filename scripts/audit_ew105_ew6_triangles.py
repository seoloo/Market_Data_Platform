"""문서 105 — EW-6b 러닝·확장 삼각형 뒤 돌파 (H-RT6 · H-ET6), 10 코인 실제 1h.

``power``: 발동 파일 · 가설별 독립 사건·대조 수와 MDE (수익률 미계산).
``verdict``: 사용자 승인 뒤 **한 번만** — 발동 파일·15 분 캐시 SHA 와 기대 수 확인 뒤.
"""

from __future__ import annotations

import hashlib
import json
import math
import sys
from collections import Counter
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from statistics import pstdev

REPO_ROOT = Path(__file__).resolve().parent.parent
for _p in (REPO_ROOT, REPO_ROOT / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from scripts.audit_div101_15m import CACHE, CACHE_SHA256, SYMBOLS, _load, _sha, _tie  # noqa: E402
from scripts.audit_ew97_correction_resume import _welch  # noqa: E402

from auto_trading.core.types import Candle  # noqa: E402
from auto_trading.research.elliott.geometry_ew6 import ew6b_events  # noqa: E402

OUT = REPO_ROOT / "reports" / "research" / "elliott" / "105"
TRIGGERS = OUT / "triggers.jsonl"
HOUR = 3_600_000
QUARTER = 900_000
HORIZON = 30
ALPHA = 0.025
COST = 0.0019
REAL_PERP = 0.00023
GATE_SD_UNITS = 0.254
TRIGGERS_SHA256 = "da952c8d0c0d49f52a67aca4cb0f4f3aac24b3229e4d1e0f4ea82fe7edb2a46f"
EXPECTED: dict[str, tuple[int, int]] | None = {"H-RT6": (2366, 2498), "H-ET6": (2011, 2537)}
"""가설 → (독립 사건, 독립 대조). ``power`` 뒤(수익률 보기 전) 고정."""

OPEN = frozenset({"H-ET6"})
"""문서 106 §5: 헛검출 관문 통과 갈래의 가설만 연다 (러닝 삼각형 헛검출 13.9%·13.7% 미달)."""
HYPOTHESES: dict[str, Callable[[dict[str, object]], bool]] = {
    "H-RT6": lambda r: "RUNNING_TRIANGLE" in r["five"],  # type: ignore[operator]
    "H-ET6": lambda r: "EXPANDING_TRIANGLE" in r["five"],  # type: ignore[operator]
}


def hour_segments(symbol: str) -> list[list[Candle]]:
    """15 분 캐시 → 실제 1h 봉. 15 분 4 개가 모두 있는 시간만, 빠진 시간에서 구간을 끊는다."""
    quarters = _load(symbol)
    groups: dict[int, list[tuple[int, float, float, float, float]]] = {}
    for q in quarters:
        groups.setdefault(q[0] // HOUR * HOUR, []).append(q)
    segs: list[list[Candle]] = []
    for start in sorted(groups):
        g = groups[start]
        if len(g) != 4 or [x[0] for x in g] != [start + i * QUARTER for i in range(4)]:
            segs.append([])
            continue
        bar = Candle(
            open_ms=start,
            close_ms=start + HOUR - 1,
            open=Decimal(repr(g[0][1])),
            high=Decimal(repr(max(x[2] for x in g))),
            low=Decimal(repr(min(x[3] for x in g))),
            close=Decimal(repr(g[-1][4])),
            volume=Decimal(1),
        )
        if segs and segs[-1] and segs[-1][-1].open_ms == start - HOUR:
            segs[-1].append(bar)
        else:
            segs.append([bar])
    return [s for s in segs if len(s) > HORIZON + 30]


def power() -> dict[str, object]:
    for s in SYMBOLS:
        if _sha(CACHE / f"{s}.csv") != CACHE_SHA256[s]:
            raise SystemExit(f"{s} 15 분 캐시가 101 고정값과 다르다")
    rows: list[dict[str, object]] = []
    fwd: list[float] = []
    for symbol in SYMBOLS:
        for seg in hour_segments(symbol):
            closes = [float(b.close) for b in seg]
            fwd.extend(math.log(closes[t + HORIZON] / closes[t]) for t in range(len(seg) - HORIZON))
            for e in ew6b_events(seg, 2.0):
                c = e.pivot.confirmed
                if c + HORIZON >= len(seg):
                    continue
                rows.append(
                    {
                        "symbol": symbol,
                        "close_ms": seg[c].close_ms,
                        "d": e.pivot.kind,
                        "five": sorted(e.five),
                        "three": sorted(e.three),
                    }
                )
        sys.stderr.write(f"power {symbol}\n")
        sys.stderr.flush()
    OUT.mkdir(parents=True, exist_ok=True)
    rows.sort(key=lambda r: (int(str(r["close_ms"])), str(r["symbol"])))
    with TRIGGERS.open("w", encoding="utf-8", newline="\n") as fh:
        for r in rows:
            fh.write(json.dumps(r, sort_keys=True) + "\n")
    sd = pstdev(fwd)
    out: dict[str, object] = {
        "triggers": len(rows),
        "triggers_per_asset": dict(Counter(str(r["symbol"]) for r in rows)),
        "triggers_sha256": _sha(TRIGGERS),
        "sd_30h": round(sd, 5),
        "gate_threshold": round(GATE_SD_UNITS * sd, 5),
    }
    for name, pred in HYPOTHESES.items():
        ev, co = _split(rows, pred)
        mde = 2.84 * sd * math.sqrt(1 / len(ev) + 1 / len(co)) if ev and co else math.inf
        out[name] = {
            "all": sum(1 for r in rows if pred(r)),
            "all_per_asset": dict(Counter(str(r["symbol"]) for r in rows if pred(r))),
            "n1_events_independent": len(ev),
            "n2_controls_independent": len(co),
            "events_by_year": dict(sorted(Counter(_year(r) for r in ev).items())),
            "events_by_direction": dict(sorted(Counter(str(r["d"]) for r in ev).items())),
            "mde": round(mde, 5),
            "gate_pass": mde <= GATE_SD_UNITS * sd,
        }
    (OUT / "power.json").write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")
    return out


def _year(r: dict[str, object]) -> str:
    return datetime.fromtimestamp(int(str(r["close_ms"])) / 1000, UTC).strftime("%Y")


def _thin(rows: Sequence[dict[str, object]]) -> list[dict[str, object]]:
    kept: list[dict[str, object]] = []
    last = -(10**18)
    for r in sorted(
        rows,
        key=lambda x: (int(str(x["close_ms"])), _tie(int(str(x["close_ms"])), str(x["symbol"]))),
    ):
        c = int(str(r["close_ms"]))
        if c - last >= HORIZON * HOUR:
            kept.append(r)
            last = c
    return kept


def _split(
    rows: Sequence[dict[str, object]], pred: Callable[[dict[str, object]], bool]
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    return _thin([r for r in rows if pred(r)]), _thin([r for r in rows if not pred(r)])


def verdict() -> dict[str, object]:
    path = OUT / "verdict.json"
    if path.exists():
        raise SystemExit(f"이미 판정했다: {path}")
    if not TRIGGERS_SHA256 or EXPECTED is None:
        raise SystemExit("발동 파일 해시·기대 수가 고정되지 않았다")
    for s in SYMBOLS:
        if _sha(CACHE / f"{s}.csv") != CACHE_SHA256[s]:
            raise SystemExit(f"{s} 15 분 캐시가 101 고정값과 다르다")
    raw = TRIGGERS.read_bytes()
    if hashlib.sha256(raw).hexdigest() != TRIGGERS_SHA256:
        raise SystemExit("발동 파일이 관문 때와 다르다")
    rows = [json.loads(line) for line in raw.decode("utf-8").splitlines()]
    gate_info = json.loads((OUT / "power.json").read_text(encoding="utf-8"))
    closes: dict[str, dict[int, float]] = {}
    for s in SYMBOLS:
        closes[s] = {b.close_ms: float(b.close) for seg in hour_segments(s) for b in seg}

    def r(row: dict[str, object], bars: int) -> float | None:
        c = closes[str(row["symbol"])]
        t = int(str(row["close_ms"]))
        later = c.get(t + bars * HOUR)
        return None if later is None else -int(str(row["d"])) * math.log(later / c[t])

    def values(rows_: Sequence[dict[str, object]], bars: int) -> list[float]:
        return [v for v in (r(x, bars) for x in rows_) if v is not None]

    record: dict[str, object] = {}
    for name, pred in HYPOTHESES.items():
        ev, co = _split(rows, pred)
        if (len(ev), len(co)) != EXPECTED[name]:
            raise SystemExit(f"{name} 수가 관문과 다르다: {(len(ev), len(co))}")
        if not gate_info[name]["gate_pass"]:
            record[name] = {"opened": False, "note": "관문 미달 — 수익률 미열람"}
            continue
        if name not in OPEN:
            record[name] = {"opened": False, "note": "문서 106 헛검출 관문 미달 — 수익률 미열람"}
            continue
        main = _welch(values(ev, HORIZON), values(co, HORIZON))
        diff = float(str(main["diff"]))
        p = float(str(main["p_one_sided_normal_approx"]))
        groups: dict[str, list[float]] = {}
        for x in ev:
            v = r(x, HORIZON)
            if v is None:
                continue
            for key in (f"symbol={x['symbol']}", f"year={_year(x)}", f"d={x['d']}"):
                groups.setdefault(key, []).append(v)
        record[name] = {
            "opened": True,
            "main_30h": main,
            "significant_0_025": p < ALPHA,
            "diff_exceeds_cost_0_19pct": diff > COST,
            "reference_realistic_cost_net": round(diff - REAL_PERP, 6),
            "secondary_descriptive": {
                "horizon_6h": _welch(values(ev, 6), values(co, 6)),
                "horizon_60h": _welch(values(ev, 60), values(co, 60)),
                "groups_mean_R": {
                    k: {"n": len(v), "mean": round(sum(v) / len(v), 6)}
                    for k, v in sorted(groups.items())
                },
            },
        }
    record["note"] = "문서 105 1 회 판정. p 는 정규 근사. 보조는 기술 통계(판정 아님)."
    path.write_text(json.dumps(record, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return record


def main(argv: list[str]) -> int:
    if argv == ["power"]:
        result = power()
    elif argv == ["verdict"]:
        result = verdict()
    else:
        raise SystemExit("usage: audit_ew105_ew6_triangles.py power | verdict")
    sys.stdout.write(json.dumps(result, indent=1, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
