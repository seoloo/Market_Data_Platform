"""문서 112 §7 — AG-CR 배율 1~10 참고 계산 (판정 아님, 사용자 요청 "최대 10 배까지").

사용 예::

    python -m scripts.run_ag_leverage run

배율 E 마다 ``OverlayConfig(exposure=E, catastrophe_stop=0.20, halt_drawdown=1.0,
max_futures_leverage=max(3, E))`` — 선물 레버리지 상한이 배율을 막지 않게 E 까지 연다.
그 밖의 규칙(비상 스톱 −20%·일 손실 2%·유지 증거금 0.5% 청산)과 −70% 정리는 112 와 같다.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from scripts.run_ag_small import (
    CR_FROM,
    START_CASH,
    TO,
    _iso,
    apply_stop,
    first_path_yearly,
    load_crypto,
    month_starts,
    table,
)

from auto_trading.backtest.overlay import OverlayConfig, simulate_overlay

REPO = Path(__file__).resolve().parent.parent
OUTPUT = REPO / "reports" / "ag_small" / "leverage.json"
LEVELS = (1, 2, 3, 4, 5, 6, 7, 8, 9, 10)


def _ms(day: str) -> int:
    from datetime import UTC, datetime

    return int(datetime.fromisoformat(day).replace(tzinfo=UTC).timestamp() * 1000)


def run() -> dict[str, object]:
    spot, perp, funding = load_crypto()
    days = sorted(_iso(ms) for ms in spot["BTCUSDT"])
    last = max(d for d in days if d <= TO)
    starts = month_starts(days, CR_FROM)
    out: dict[str, object] = {}
    for e in LEVELS:
        cfg = OverlayConfig(
            exposure=float(e),
            catastrophe_stop=0.20,
            halt_drawdown=1.0,
            max_futures_leverage=max(3.0, float(e)),
        )
        paths = {}
        liquidations = 0
        stops_fired = 0
        worst = 0.0
        for s in starts:
            res = simulate_overlay(
                spot,
                perp,
                funding,
                from_ms=_ms(s),
                to_ms=_ms(last),
                initial_equity=START_CASH,
                cfg=cfg,
            )
            liquidations += res.liquidations
            stops_fired += res.stop_hits
            worst = max(worst, res.worst_intraday_drawdown)
            paths[s] = apply_stop({_iso(d): q for d, q in zip(res.days, res.equity, strict=True)})
        first = min(paths)
        out[f"{e}x"] = {
            "table": table(paths, last),
            "first_start_path": first_path_yearly(*paths[first], first, last),
            "liquidations_total_over_starts": liquidations,
            "stop_hits_total_over_starts": stops_fired,
            "worst_intraday_drawdown_any_start": round(worst, 4),
        }
        sys.stderr.write(f"{e}x done\n")
        sys.stderr.flush()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def main(argv: list[str]) -> int:
    if argv != ["run"]:
        raise SystemExit("usage: python -m scripts.run_ag_leverage run")
    run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
