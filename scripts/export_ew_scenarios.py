"""BTCUSDT USD-M 2020-01~ 전체 발동의 시나리오 묶음(상위 50)을 파일로 내보낸다 — EW-4n (문서 61 §6).

* ``data/research/elliott_scenarios/binance_usdm_BTCUSDT.sets.jsonl.gz``
  — 평가자(JEV 등) 입력, 순위 정보 없음
* ``data/research/elliott_scenarios/binance_usdm_BTCUSDT.baseline_ranks.jsonl.gz``
  — 기준 순위 규칙 사이드카 (섀도 비교 전용, 평가자에게 주지 않는다)

DB 는 읽기 전용. 수익률을 계산하지 않는다.
이전 판은 ``*.ew3d.*``(EW-3d′)·``*.ew3e.*``(EW-3e)·``*.ew4.*``(EW-4) 로 보존.
"""

from __future__ import annotations

import gzip
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from scripts.audit_ew_edge_power import _btc_bars, _btc_minutes, _chunks, _groups  # noqa: E402

from auto_trading.research.elliott.minute_path import replay_minute_groups_ladder  # noqa: E402
from auto_trading.research.elliott.pivots import EW0_THRESHOLD_PCT  # noqa: E402
from auto_trading.research.elliott.scanner import (  # noqa: E402
    EW3E_LOOKBACK_BARS,
    causal_triggers,
    scenario_lists,
)
from auto_trading.research.elliott.scenario_contract import (  # noqa: E402
    scenario_set_digest,
    to_json_dict,
    validate_scenario_set_dict,
)
from auto_trading.research.elliott.scenario_export import export_scenario_set  # noqa: E402

OUT = REPO_ROOT / "data" / "research" / "elliott_scenarios"


def main() -> int:
    four, hours, to_ms = _btc_bars()
    minutes = _btc_minutes(to_ms)
    OUT.mkdir(parents=True, exist_ok=True)
    sets_path = OUT / "binance_usdm_BTCUSDT.sets.jsonl.gz"
    ranks_path = OUT / "binance_usdm_BTCUSDT.baseline_ranks.jsonl.gz"
    written = scenarios = 0
    with (
        gzip.open(sets_path, "wt", encoding="utf-8") as sets,
        gzip.open(ranks_path, "wt", encoding="utf-8") as ranks,
    ):
        for chunk in _chunks(four):
            (replay,) = replay_minute_groups_ladder(
                chunk, _groups(minutes, chunk), chunk[-1].close_ms, (EW0_THRESHOLD_PCT,)
            )
            chunk_hours = [h for h in hours if chunk[0].open_ms <= h.open_ms <= chunk[-1].close_ms]
            # EW-3e 발동(58) + EW-4 플랫·삼각형(60) + 4파·B파 안 모양(61)
            triggers = causal_triggers(replay, chunk_hours, chunk, all_new_pivots=True)
            lists = scenario_lists(
                triggers, chunk_hours, lookback_bars=EW3E_LOOKBACK_BARS, ew4=True, nested=True
            )
            for trig, scen in zip(triggers, lists, strict=True):
                data_set, sidecar = export_scenario_set(
                    trig, scen, venue="binance_usdm", instrument="BTCUSDT", rule_profile="EW-4n"
                )
                data = to_json_dict(data_set)
                problems = validate_scenario_set_dict(data)
                if problems:
                    raise SystemExit(f"계약 위반 {trig.close_ms}: {problems}")
                digest = scenario_set_digest(data)
                sets.write(json.dumps(data, ensure_ascii=False, sort_keys=True) + "\n")
                ranks.write(
                    json.dumps(
                        {"as_of_ms": trig.close_ms, "set_digest": digest, "baseline_rank": sidecar},
                        sort_keys=True,
                    )
                    + "\n"
                )
                written += 1
                scenarios += len(data["scenarios"])
    sys.stdout.write(
        json.dumps(
            {
                "sets": written,
                "scenarios": scenarios,
                "sets_file": str(sets_path.relative_to(REPO_ROOT)),
                "sets_bytes": sets_path.stat().st_size,
                "ranks_file": str(ranks_path.relative_to(REPO_ROOT)),
                "ranks_bytes": ranks_path.stat().st_size,
            },
            ensure_ascii=False,
        )
        + "\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
