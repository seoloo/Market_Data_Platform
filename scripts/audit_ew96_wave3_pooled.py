"""문서 96 — Elliott 2파 0.5 되돌림 → 3파 진입(1h degree) BTC + ETH 합산, 겹치는 거래는 한 묶음.

``fills``: 관문 — 자산별 체결 수와 묶음 수만. **손익(R)·청산 이유는 출력하지 않는다.**
``verdict``: 사용자 승인 뒤 **한 번만**. 판정 파일이 있거나 체결·묶음 수가 관문과 다르면 멈춘다.
"""

from __future__ import annotations

import hashlib
import json
import math
import sys
from collections import Counter
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from pathlib import Path
from statistics import NormalDist

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from scripts.audit_ew_edge_power import SPOT_BTC_DIR, _vision_minutes  # noqa: E402

from auto_trading.research.elliott.wave3_trades import (  # noqa: E402
    Bar,
    Rules,
    Setup,
    Trade,
    cluster_trades,
    simulate,
)

SETUP_DIR = REPO_ROOT / "reports" / "research" / "elliott" / "94"
ASSETS = {
    "BTCUSDT": (
        "setups_1h.jsonl",
        "b34943e78a16f5eaa8336aa2cddbe0419ff763458826fae42d583a097a9f8628",
    ),
    "ETHUSDT": (
        "setups_1h_ETHUSDT.jsonl",
        "6d45c7535fe687502b875fe26fb0745e67f45babdac7d854bc3fb3ef393f03e1",
    ),
}
"""자산 → (셋업 파일, SHA-256). ETH 해시는 셋업을 센 뒤(손익 보기 전) 문서 96 과 함께 고정한다."""
OUT = REPO_ROOT / "reports" / "research" / "elliott" / "96"
VERDICT = OUT / "verdict.json"
SD_R = 1.799
MDE_GATE_R = 0.5
EXPECTED: tuple[int, int, int] | None = None
"""(BTC 체결, ETH 체결, 묶음) — ``fills`` 결과를 문서 96 에 적은 뒤 고정한다."""
REALISTIC_COST_SIDE = 0.00023


def _setups(symbol: str) -> list[Setup]:
    name, sha = ASSETS[symbol]
    raw = (SETUP_DIR / name).read_bytes()
    if not sha or hashlib.sha256(raw).hexdigest() != sha:
        raise SystemExit(f"{symbol} 셋업 파일 해시가 고정값과 다르다 (또는 아직 고정 전)")
    out = []
    for line in raw.decode("utf-8").splitlines():
        r = json.loads(line)
        out.append(
            Setup(
                as_of_ms=int(r["as_of_ms"]),
                direction=int(r["direction"]),
                entry=float(r["entry"]),
                stop=float(r["stop"]),
                target=float(r["target"]),
                b=float(r["b"]),
                skipped=bool(r["skipped_already_past_entry"]),
            )
        )
    return out


def _bars(symbol: str) -> Iterator[Bar]:
    directory = SPOT_BTC_DIR.parent.parent / symbol / "1m"
    for m in _vision_minutes(directory, symbol):
        yield Bar(m.open_ms, float(m.open), float(m.high), float(m.low), float(m.close))


def _run(rules: Rules) -> tuple[dict[str, list[Trade]], dict[str, object]]:
    trades: dict[str, list[Trade]] = {}
    counts: dict[str, object] = {}
    for symbol in ASSETS:
        t, c = simulate(_setups(symbol), _bars(symbol), rules)
        trades[symbol] = t
        counts[symbol] = {
            "setups": c.setups,
            "skipped": c.skipped,
            "replaced": c.replaced,
            "ignored_in_position": c.ignored_in_position,
            "cancelled_extension": c.cancelled_extension,
            "expired": c.expired,
            "filled": c.filled,
        }
    return trades, counts


def fills() -> dict[str, object]:
    trades, counts = _run(Rules())
    clusters = cluster_trades([t for ts in trades.values() for t in ts])
    n = len(clusters)
    mde = 2.49 * SD_R / math.sqrt(n) if n else math.inf
    return {
        "per_asset": counts,
        "clusters": n,
        "trades_in_multi_trade_clusters": sum(len(c) for c in clusters if len(c) > 1),
        "mde_mean_r": round(mde, 3),
        "gate_mde_le_0_5r": mde <= MDE_GATE_R,
        "note": "문서 96 관문. 손익(R)·청산 이유는 출력하지 않았다.",
    }


def _stats(values: Sequence[float]) -> dict[str, float]:
    n = len(values)
    mean = sum(values) / n
    sd = math.sqrt(sum((v - mean) ** 2 for v in values) / (n - 1))
    t = mean / (sd / math.sqrt(n))
    return {
        "n": n,
        "mean_r": round(mean, 4),
        "sd_r": round(sd, 4),
        "t": round(t, 3),
        "p_one_sided_normal_approx": round(1 - NormalDist().cdf(t), 5),
    }


def _cluster_values(trades: dict[str, list[Trade]]) -> list[float]:
    clusters = cluster_trades([t for ts in trades.values() for t in ts])
    return [sum(t.r_net for t in c) / len(c) for c in clusters]


def verdict() -> dict[str, object]:
    if VERDICT.exists():
        raise SystemExit(f"이미 판정했다: {VERDICT}")
    if EXPECTED is None:
        raise SystemExit("관문 수가 고정되지 않았다 — 먼저 fills 를 문서에 적는다")
    trades, counts = _run(Rules())
    got = (
        len(trades["BTCUSDT"]),
        len(trades["ETHUSDT"]),
        len(cluster_trades([t for ts in trades.values() for t in ts])),
    )
    if got != EXPECTED:
        raise SystemExit(f"체결·묶음 수가 관문과 다르다: {got} != {EXPECTED}")
    real, _ = _run(Rules(cost_side=REALISTIC_COST_SIDE))
    gross, _ = _run(Rules(cost_side=0.0))
    main = _stats(_cluster_values(trades))
    every = [t for ts in trades.values() for t in ts]

    def year(t: Trade) -> str:
        return datetime.fromtimestamp(t.fill_ms / 1000, UTC).strftime("%Y")

    def streak(ts: Sequence[Trade]) -> int:
        best = run = 0
        for t in sorted(ts, key=lambda x: x.fill_ms):
            run = run + 1 if t.r_net < 0 else 0
            best = max(best, run)
        return best

    by_year: dict[str, list[float]] = {}
    for t in every:
        by_year.setdefault(year(t), []).append(t.r_net)
    record: dict[str, object] = {
        "main_cluster_net_r": main,
        "significant_0_05": main["p_one_sided_normal_approx"] < 0.05,
        "mean_r_positive_after_cost": main["mean_r"] > 0,
        "worst_case_first": {
            "worst_r": round(min(t.r_net for t in every), 3),
            "max_losing_streak": streak(every),
            "by_year_mean_r": {
                k: [len(v), round(sum(v) / len(v), 4)] for k, v in sorted(by_year.items())
            },
        },
        "secondary_descriptive": {
            "btc_only_trades": _stats([t.r_net for t in trades["BTCUSDT"]]),
            "eth_only_trades": _stats([t.r_net for t in trades["ETHUSDT"]]),
            "all_trades_unclustered": _stats([t.r_net for t in every]),
            "cluster_gross_r": _stats(_cluster_values(gross)),
            "cluster_realistic_cost_r": _stats(_cluster_values(real)),
            "exit_reasons": dict(sorted(Counter(t.reason for t in every).items())),
            "target_win_rate": round(sum(t.reason == "TARGET" for t in every) / len(every), 4),
            "by_direction": {
                str(d): _stats([t.r_net for t in every if t.setup.direction == d])
                for d in (1, -1)
                if sum(1 for t in every if t.setup.direction == d) > 1
            },
            "per_asset_counts": counts,
        },
        "note": "문서 96 1회 판정. p 는 정규 근사. 보조는 기술 통계(판정 아님).",
    }
    OUT.mkdir(parents=True, exist_ok=True)
    VERDICT.write_text(json.dumps(record, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return record


def main(argv: list[str]) -> int:
    modes = {"fills": fills, "verdict": verdict}
    if len(argv) != 1 or argv[0] not in modes:
        raise SystemExit("usage: audit_ew96_wave3_pooled.py fills|verdict")
    sys.stdout.write(json.dumps(modes[argv[0]](), ensure_ascii=True, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
