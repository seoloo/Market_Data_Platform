"""문서 94 후보 ③ 셋업 수 — 끝난 충격파 = 더 큰 1파, 2파 0.5 되돌림에서 3파 진입, 1파 시작 손절.

엘리엇 데모(문서 56)와 **같은 판독기·같은 셋업 규칙**: EW-3e 발동, 상위 10 중 6 개 이상
같은 방향 충격파 5파 종료, 진입 B − 0.5(B − A) · 손절 A · 목표 진입 + 1.618|B − A|.
현물 BTCUSDT 2017-08 ~ 2026-08, 4h degree 와 1h degree(문서 93 B 와 같은 시간 ×4, 임계 1%).

**셋업 시각까지의 정보만 쓴다.** 진입 체결·손절·목표 도달 등 셋업 뒤 가격은 보지 않는다.
결과: ``reports/research/elliott/94/setups_<degree>.jsonl`` 과 요약 JSON (stdout).
"""

from __future__ import annotations

import json
import math
import sys
from collections import Counter
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from scripts.audit_ew93_hourly_degree import (  # noqa: E402
    SCALE,
    THRESHOLD_B,
    _stretch,
    _stretched_minutes,
)
from scripts.audit_ew_edge_power import (  # noqa: E402
    SPOT_BTC_DIR,
    _aggregate,
    _chunks,
    _groups,
    _vision_minutes,
)

from auto_trading.core.enums import Timeframe  # noqa: E402
from auto_trading.core.types import Candle  # noqa: E402
from auto_trading.research.elliott.demo_setup import setup_from_set  # noqa: E402
from auto_trading.research.elliott.minute_path import replay_minute_groups_ladder  # noqa: E402
from auto_trading.research.elliott.pivots import EW0_THRESHOLD_PCT  # noqa: E402
from auto_trading.research.elliott.scanner import (  # noqa: E402
    EW3E_LOOKBACK_BARS,
    causal_triggers,
    scenario_lists,
)
from auto_trading.research.elliott.scenario_contract import to_json_dict  # noqa: E402
from auto_trading.research.elliott.scenario_export import export_scenario_set  # noqa: E402

H4 = Timeframe.H4.ms
OUT = REPO_ROOT / "reports" / "research" / "elliott" / "94"
THIN_BARS = 60
"""데모의 예약 유효 10 일 = 4h 60 봉. 1h degree 도 봉 수로 같게(실제 60 시간)."""
REWARD_RISK = 3.236
"""목표 1.618|B−A| ÷ 위험 0.5|B−A| (진입 B − 0.5(B−A), 손절 A)."""


def _dir(symbol: str) -> Path:
    return SPOT_BTC_DIR.parent.parent / symbol / "1m"


def _bars(
    degree: str, symbol: str = "BTCUSDT"
) -> tuple[tuple[Candle, ...], tuple[Candle, ...], Iterator[Candle], Decimal]:
    directory = _dir(symbol)
    if degree == "4h":

        def minutes() -> Iterator[Candle]:
            return _vision_minutes(directory, symbol)

        four = tuple(_aggregate(minutes(), H4))
        hours = tuple(_aggregate(minutes(), Timeframe.H1.ms))
        return four, hours, minutes(), EW0_THRESHOLD_PCT
    real_hours = _aggregate(_vision_minutes(directory, symbol), Timeframe.H1.ms)
    real_quarters = _aggregate(_vision_minutes(directory, symbol), Timeframe.M15.ms)
    four = tuple(_stretch(h for h in real_hours if h.volume > 0))
    hours = tuple(_stretch(iter(real_quarters)))
    return four, hours, _stretched_minutes(directory, symbol), THRESHOLD_B


def _real_ms(degree: str, ms: int) -> int:
    return ms if degree == "4h" else (ms + 1) // SCALE - 1


def count(degree: str, symbol: str = "BTCUSDT") -> dict[str, object]:
    four, hours, minutes, threshold = _bars(degree, symbol)
    close_at = {bar.close_ms: bar.close for bar in four}
    index_of = {bar.close_ms: k for k, bar in enumerate(four)}
    rows: list[dict[str, object]] = []
    triggers_total = 0
    for chunk in _chunks(four):
        (replay,) = replay_minute_groups_ladder(
            chunk, _groups(minutes, chunk), chunk[-1].close_ms, (threshold,)
        )
        chunk_hours = [h for h in hours if chunk[0].open_ms <= h.open_ms <= chunk[-1].close_ms]
        triggers = causal_triggers(replay, chunk_hours, chunk, all_new_pivots=True)  # EW-3e
        lists = scenario_lists(triggers, chunk_hours, lookback_bars=EW3E_LOOKBACK_BARS)
        triggers_total += len(triggers)
        for trig, scen in zip(triggers, lists, strict=True):
            data_set, ranks = export_scenario_set(
                trig, scen, venue="binance_spot", instrument=symbol, rule_profile="EW-3e"
            )
            setup = setup_from_set(to_json_dict(data_set), ranks)
            if setup is None:
                continue
            last = close_at[trig.close_ms]
            skipped = (last - setup.entry) * setup.direction <= 0  # 이미 0.5 되돌림을 지남
            rows.append(
                {
                    "as_of_ms": _real_ms(degree, trig.close_ms),
                    "bar": index_of[trig.close_ms],
                    "direction": setup.direction,
                    "a": str(setup.a),
                    "b": str(setup.b),
                    "entry": str(setup.entry),
                    "stop": str(setup.stop),
                    "target": str(setup.target),
                    "agree": setup.agree,
                    "impulse_pct": float(setup.impulse_pct),
                    "skipped_already_past_entry": skipped,
                }
            )
    OUT.mkdir(parents=True, exist_ok=True)
    name = f"setups_{degree}.jsonl" if symbol == "BTCUSDT" else f"setups_{degree}_{symbol}.jsonl"
    with (OUT / name).open("w", encoding="utf-8", newline="\n") as f:
        for r in rows:
            f.write(json.dumps(r, sort_keys=True) + "\n")
    live = [r for r in rows if not r["skipped_already_past_entry"]]
    thinned: list[dict[str, object]] = []
    for r in live:
        if not thinned or int(str(r["bar"])) - int(str(thinned[-1]["bar"])) >= THIN_BARS:
            thinned.append(r)

    def year(r: dict[str, object]) -> str:
        return datetime.fromtimestamp(int(str(r["as_of_ms"])) / 1000, UTC).strftime("%Y")

    # 손익분기 승률 p0 = 1/(1+3.236) 에서 R 의 표준편차 (이기면 +3.236R, 지면 −1R)
    p0 = 1 / (1 + REWARD_RISK)
    sd_r = (1 + REWARD_RISK) * math.sqrt(p0 * (1 - p0))

    def mde(n: int) -> float | None:
        return round(2.49 * sd_r / math.sqrt(n), 3) if n else None

    n = len(thinned)
    return {
        "degree": degree,
        "symbol": symbol,
        "triggers": triggers_total,
        "setups": len(rows),
        "skipped_already_past_entry": len(rows) - len(live),
        "setups_live": len(live),
        "setups_live_by_direction": dict(
            sorted(Counter(str(r["direction"]) for r in live).items())
        ),
        "setups_live_by_year": dict(sorted(Counter(year(r) for r in live).items())),
        "independent_setups_60bar": n,
        "median_impulse_pct": round(
            sorted(float(str(r["impulse_pct"])) for r in live)[len(live) // 2], 4
        )
        if live
        else None,
        "sd_r_at_breakeven": round(sd_r, 3),
        "mde_mean_r_if_fill_rate": {
            f"{rate:.0%}": mde(int(n * rate)) for rate in (0.4, 0.6, 0.8, 1.0)
        },
        "note": "문서 94 후보 ③ 셋업 수. 셋업 뒤 가격(체결·손절·목표)은 보지 않았다.",
    }


def main(argv: Sequence[str]) -> int:
    if len(argv) != 1 or argv[0] not in ("4h", "1h"):
        raise SystemExit("usage: audit_ew94_wave3_count.py 4h|1h")
    sys.stdout.write(json.dumps(count(argv[0]), ensure_ascii=True, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
