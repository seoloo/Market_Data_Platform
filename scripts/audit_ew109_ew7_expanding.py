"""문서 109 — EW-7 (m_high 3) 확장 삼각형 뒤 돌파 H-ET7, 10 코인 실제 1h.

``power``: 발동 파일 · 독립 사건·대조 수와 MDE (수익률 미계산).
``verdict``: 사용자 승인 뒤 **한 번만** — 발동 파일·15 분 캐시 SHA 와 기대 수 확인 뒤.
105 와 같은 자료·결과·독립성·검정. 판독기만 EW-7 (m = 2, m_high = 3, 107 §8).
"""

from __future__ import annotations

import hashlib
import json
import math
import sys
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from statistics import pstdev

REPO_ROOT = Path(__file__).resolve().parent.parent
for _p in (REPO_ROOT, REPO_ROOT / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from scripts.audit_div101_15m import CACHE, CACHE_SHA256, SYMBOLS, _sha  # noqa: E402
from scripts.audit_ew97_correction_resume import _welch  # noqa: E402
from scripts.audit_ew105_ew6_triangles import (  # noqa: E402
    ALPHA,
    COST,
    GATE_SD_UNITS,
    HORIZON,
    HOUR,
    REAL_PERP,
    _split,
    _year,
    hour_segments,
)

from auto_trading.research.elliott.geometry_ew6 import ew7_events  # noqa: E402

OUT = REPO_ROOT / "reports" / "research" / "elliott" / "109"
TRIGGERS = OUT / "triggers.jsonl"
NAME = "H-ET7"
M_HIGH = 3.0
TRIGGERS_SHA256 = "8d50175c7224f7ebfc71c4fdc49d462357f16f736840f37dcbba141713ee7a9f"
EXPECTED: tuple[int, int] | None = (1596, 2544)
"""(독립 사건, 독립 대조). ``power`` 뒤(수익률 보기 전) 고정."""


def is_event(r: dict[str, object]) -> bool:
    five = r["five"]
    assert isinstance(five, list)
    return "EXPANDING_TRIANGLE" in five


def _check_caches() -> None:
    for s in SYMBOLS:
        if _sha(CACHE / f"{s}.csv") != CACHE_SHA256[s]:
            raise SystemExit(f"{s} 15 분 캐시가 101 고정값과 다르다")


def power() -> dict[str, object]:
    _check_caches()
    rows: list[dict[str, object]] = []
    fwd: list[float] = []
    for symbol in SYMBOLS:
        for seg in hour_segments(symbol):
            closes = [float(b.close) for b in seg]
            fwd.extend(math.log(closes[t + HORIZON] / closes[t]) for t in range(len(seg) - HORIZON))
            for e in ew7_events(seg, 2.0, M_HIGH):
                c = e.pivot.confirmed
                if c + HORIZON >= len(seg):
                    continue
                rows.append(
                    {
                        "symbol": symbol,
                        "close_ms": seg[c].close_ms,
                        "d": e.pivot.kind,
                        "five": sorted(e.five),
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
    ev, co = _split(rows, is_event)
    mde = 2.84 * sd * math.sqrt(1 / len(ev) + 1 / len(co)) if ev and co else math.inf
    out: dict[str, object] = {
        "triggers": len(rows),
        "triggers_sha256": _sha(TRIGGERS),
        "sd_30h": round(sd, 5),
        "gate_threshold": round(GATE_SD_UNITS * sd, 5),
        NAME: {
            "all": sum(1 for r in rows if is_event(r)),
            "all_per_asset": dict(Counter(str(r["symbol"]) for r in rows if is_event(r))),
            "n1_events_independent": len(ev),
            "n2_controls_independent": len(co),
            "events_by_year": dict(sorted(Counter(_year(r) for r in ev).items())),
            "events_by_direction": dict(sorted(Counter(str(r["d"]) for r in ev).items())),
            "mde": round(mde, 5),
            "gate_pass": mde <= GATE_SD_UNITS * sd,
        },
    }
    (OUT / "power.json").write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")
    return out


def verdict() -> dict[str, object]:
    path = OUT / "verdict.json"
    if path.exists():
        raise SystemExit(f"이미 판정했다: {path}")
    if not TRIGGERS_SHA256 or EXPECTED is None:
        raise SystemExit("발동 파일 해시·기대 수가 고정되지 않았다")
    _check_caches()
    raw = TRIGGERS.read_bytes()
    if hashlib.sha256(raw).hexdigest() != TRIGGERS_SHA256:
        raise SystemExit("발동 파일이 관문 때와 다르다")
    rows = [json.loads(line) for line in raw.decode("utf-8").splitlines()]
    info = json.loads((OUT / "power.json").read_text(encoding="utf-8"))[NAME]
    ev, co = _split(rows, is_event)
    if (len(ev), len(co)) != EXPECTED:
        raise SystemExit(f"수가 관문과 다르다: {(len(ev), len(co))}")
    if not info["gate_pass"]:
        raise SystemExit("관문 미달 — 수익률을 열지 않는다")
    closes = {
        s: {b.close_ms: float(b.close) for seg in hour_segments(s) for b in seg} for s in SYMBOLS
    }

    def r(row: dict[str, object], bars: int) -> float | None:
        c = closes[str(row["symbol"])]
        t = int(str(row["close_ms"]))
        later = c.get(t + bars * HOUR)
        return None if later is None else -int(str(row["d"])) * math.log(later / c[t])

    def values(rows_: Sequence[dict[str, object]], bars: int) -> list[float]:
        return [v for v in (r(x, bars) for x in rows_) if v is not None]

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
    record = {
        NAME: {
            "main_30h": main,
            "significant": p < ALPHA,
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
        },
        "note": "문서 109 1 회 판정. p 는 정규 근사. 보조는 기술 통계(판정 아님).",
    }
    path.write_text(json.dumps(record, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return record


def main(argv: list[str]) -> int:
    if argv == ["power"]:
        result = power()
    elif argv == ["verdict"]:
        result = verdict()
    else:
        raise SystemExit("usage: audit_ew109_ew7_expanding.py power | verdict")
    sys.stdout.write(json.dumps(result, indent=1, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
