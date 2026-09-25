"""문서 60 — BTC 시장에서 플랫·삼각형이 상위 목록에 얼마나 자주 오르나 (기술 통계, 판정 아님).

BTCUSDT USD-M 2020-01~ 모든 EW-3e 발동에서 EW-3e·EW-4 두 버전의 순위 목록을 만들고 센다:

* 상위 10·50 에 플랫·삼각형이 있는 발동 비율 (연도별)
* 상위 10 플랫의 하위 유형 분포 (FourLeaf/NEoWave 라벨, 문서 60 §4)
* 데모(56)가 세는 "충격파 5파 종료" 의 상위 10 수가 EW-4 로 얼마나 줄어드나

DB 는 읽기 전용. 수익률·가격 방향을 보지 않는다.
"""

from __future__ import annotations

import json
import sys
from bisect import bisect_right
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
for _p in (REPO_ROOT, REPO_ROOT / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from scripts.audit_ew_edge_power import _btc_bars, _btc_minutes, _chunks, _groups  # noqa: E402

from auto_trading.research.elliott.minute_path import replay_minute_groups_ladder  # noqa: E402
from auto_trading.research.elliott.parser import FinePivot, Label, Parse  # noqa: E402
from auto_trading.research.elliott.pivots import EW0_THRESHOLD_PCT  # noqa: E402
from auto_trading.research.elliott.scanner import (  # noqa: E402
    EW3E_LOOKBACK_BARS,
    ScenarioList,
    causal_triggers,
    scenario_lists,
)
from auto_trading.research.elliott.scenario_export import flat_classes  # noqa: E402

TOPS = (10, 50)


def _top_parses(scen: ScenarioList, k: int) -> list[Parse]:
    """비관적 순위(동점은 뒤로) ≤ k — ``pessimistic_rank`` 와 같다, 정렬 키 이분 탐색."""
    keys = [key for _, key in scen.ranked]
    return [c for c, key in scen.ranked[:k] if bisect_right(keys, key) <= k]


def _top(scen: ScenarioList, k: int) -> list[Label]:
    return [c.pattern for c in _top_parses(scen, k)]


def _impulse_ends(scen: ScenarioList) -> int:
    return sum(p is Label.IMPULSE for p in _top(scen, 10))


def _flat_class(scen: ScenarioList, segment: Sequence[FinePivot]) -> list[str]:
    out = []
    for c in _top_parses(scen, 10):
        if c.pattern is not Label.FLAT:
            continue
        q = [float(segment[k].price) for k in c.bounds]
        legs = [abs(b - a) for a, b in pairwise(q)]
        b, cc = flat_classes(legs[1] / legs[0], legs[2] / legs[1])
        out.append(f"{b}+{cc}")
    return out


def main() -> int:
    four, hours, to_ms = _btc_bars()
    minutes = _btc_minutes(to_ms)
    per_year: dict[str, Counter[str]] = {}
    classes: Counter[str] = Counter()
    impulse_drop: Counter[int] = Counter()
    candidates = {"ew3e": 0, "ew4": 0}
    recent: list[dict[str, object]] = []
    for chunk in _chunks(four):
        (replay,) = replay_minute_groups_ladder(
            chunk, _groups(minutes, chunk), chunk[-1].close_ms, (EW0_THRESHOLD_PCT,)
        )
        chunk_hours = [h for h in hours if chunk[0].open_ms <= h.open_ms <= chunk[-1].close_ms]
        triggers = causal_triggers(replay, chunk_hours, chunk, all_new_pivots=True)
        old = scenario_lists(triggers, chunk_hours, lookback_bars=EW3E_LOOKBACK_BARS)
        new = scenario_lists(triggers, chunk_hours, lookback_bars=EW3E_LOOKBACK_BARS, ew4=True)
        for trig, a, b in zip(triggers, old, new, strict=True):
            year = str(datetime.fromtimestamp(trig.close_ms / 1000, UTC).year)
            c = per_year.setdefault(year, Counter())
            c["triggers"] += 1
            for k in TOPS:
                top = _top(b, k)
                c[f"flat_top{k}"] += Label.FLAT in top
                c[f"triangle_top{k}"] += Label.TRIANGLE in top
                c[f"either_top{k}"] += Label.FLAT in top or Label.TRIANGLE in top
            top1 = b.ranked[0][0].pattern.value if b.ranked else "NONE"
            c[f"top1_{top1}"] += 1
            classes.update(_flat_class(b, trig.segment))
            impulse_drop[_impulse_ends(a) - _impulse_ends(b)] += 1
            candidates["ew3e"] += a.total
            candidates["ew4"] += b.total
            if trig.close_ms >= to_ms - 30 * 86_400_000:
                recent.append(
                    {
                        "close_utc": datetime.fromtimestamp(trig.close_ms / 1000, UTC).isoformat(),
                        "ew3e_total": a.total,
                        "ew4_total": b.total,
                        "ew4_top10": [p.value for p in _top(b, 10)],
                    }
                )
    total = Counter[str]()
    for c in per_year.values():
        total.update(c)
    n = total["triggers"]
    sys.stdout.write(
        json.dumps(
            {
                "triggers": n,
                "share": {
                    key: round(total[key] / n, 4)
                    for key in sorted(total)
                    if key != "triggers" and n
                },
                "per_year": {y: dict(c) for y, c in sorted(per_year.items())},
                "flat_classes_top10": dict(classes.most_common()),
                "impulse_end_top10_drop": dict(sorted(impulse_drop.items())),
                "candidates_total": candidates,
                "recent_30d": recent,
            },
            ensure_ascii=False,
        )
        + "\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
