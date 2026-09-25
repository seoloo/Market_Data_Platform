"""문서 61 §4 — M1: BTC 대 구조 없는 대조에서 4파·B파가 삼각형·플랫으로 읽히는 빈도.

사용::

    python scripts/audit_ew4n_market.py btc
    python scripts/audit_ew4n_market.py null 1     # 난수 "ew61-null-1" (2·3 도 같은 방식)

BTCUSDT USD-M 2020-01~ 모든 EW-3e 발동의 EW-4n 순위 목록(상위 10)을 센다. 대조는 1m 봉 모양을
주 안에서 뒤섞은 경로(문서 60 §8 방식). DB 는 읽기 전용, 수익·방향 미조회.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
for _p in (REPO_ROOT, REPO_ROOT / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from scripts.audit_ew4_market import H1, _shuffled_minutes, _top_parses  # noqa: E402
from scripts.audit_ew_edge_power import (  # noqa: E402
    H4,
    _aggregate,
    _btc_bars,
    _btc_minutes,
    _chunks,
    _groups,
)

from auto_trading.research.elliott.minute_path import replay_minute_groups_ladder  # noqa: E402
from auto_trading.research.elliott.parser import Label  # noqa: E402
from auto_trading.research.elliott.pivots import EW0_THRESHOLD_PCT  # noqa: E402
from auto_trading.research.elliott.scanner import (  # noqa: E402
    EW3E_LOOKBACK_BARS,
    causal_triggers,
    scenario_lists,
)

NESTED_LEG = {Label.IMPULSE: 3, Label.ZIGZAG: 1}
"""충격파 4파 · 지그재그 B파."""


def main(argv: list[str]) -> int:
    if not argv or argv[0] not in ("btc", "null") or (argv[0] == "null" and len(argv) < 2):
        raise SystemExit("usage: audit_ew4n_market.py btc | null <1|2|3>")
    four, hours, to_ms = _btc_bars()
    minutes = _btc_minutes(to_ms)
    source = "BTCUSDT"
    if argv[0] == "null":
        source = f"ew61-null-{argv[1]}"
        four = tuple(_aggregate(_shuffled_minutes(to_ms, source), H4))
        hours = tuple(_aggregate(_shuffled_minutes(to_ms, source), H1))
        minutes = _shuffled_minutes(to_ms, source)
    count: Counter[str] = Counter()
    per_year: dict[str, Counter[str]] = {}
    for chunk in _chunks(four):
        (replay,) = replay_minute_groups_ladder(
            chunk, _groups(minutes, chunk), chunk[-1].close_ms, (EW0_THRESHOLD_PCT,)
        )
        chunk_hours = [h for h in hours if chunk[0].open_ms <= h.open_ms <= chunk[-1].close_ms]
        triggers = causal_triggers(replay, chunk_hours, chunk, all_new_pivots=True)
        lists = scenario_lists(
            triggers, chunk_hours, lookback_bars=EW3E_LOOKBACK_BARS, ew4=True, nested=True
        )
        for trig, scen in zip(triggers, lists, strict=True):
            year = str(datetime.fromtimestamp(trig.close_ms / 1000, UTC).year)
            top = _top_parses(scen, 10)
            readings = {c.leg_labels[NESTED_LEG[c.pattern]] for c in top if c.pattern in NESTED_LEG}
            flags = {
                "triggers": True,
                "any_impulse_or_zigzag": any(c.pattern in NESTED_LEG for c in top),
                "nested_triangle": Label.TRIANGLE in readings,
                "nested_flat": Label.FLAT in readings,
                "standalone_triangle": any(c.pattern is Label.TRIANGLE for c in top),
            }
            for key, on in flags.items():
                count[key] += on
                per_year.setdefault(year, Counter())[key] += on
    n = count["triggers"]
    sys.stdout.write(
        json.dumps(
            {
                "source": source,
                "version": "EW-4n",
                "triggers": n,
                "share_top10": {k: round(v / n, 4) for k, v in count.items() if k != "triggers"},
                "counts": dict(count),
                "per_year": {y: dict(c) for y, c in sorted(per_year.items())},
            },
            ensure_ascii=False,
        )
        + "\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
