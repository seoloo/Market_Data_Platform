"""문서 95 — Elliott 2파 0.5 되돌림 → 3파 진입(1h degree) 과거 거래 재현.

``fills``: 관문 — 체결 수·취소·만료만 센다. **손익(R)은 계산해도 출력하지 않는다.**
``verdict``: 사용자 승인 뒤 **한 번만**. 판정 파일이 있거나 체결 수가 관문과 다르면 멈춘다.

셋업: ``reports/research/elliott/94/setups_1h.jsonl`` (문서 94, SHA-256 고정). 경로: 현물 1m.
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
    simulate,
)

SETUPS = REPO_ROOT / "reports" / "research" / "elliott" / "94" / "setups_1h.jsonl"
SETUPS_SHA256 = "b34943e78a16f5eaa8336aa2cddbe0419ff763458826fae42d583a097a9f8628"
OUT = REPO_ROOT / "reports" / "research" / "elliott" / "95"
VERDICT = OUT / "verdict.json"
SD_R = 1.799
"""손익분기 승률(1/(1+3.236))에서 R 의 표준편차 — 관문 계산용 (문서 94 §1)."""
MDE_GATE_R = 0.5
EXPECTED_FILLS: int | None = None
"""``fills`` 결과를 사전 등록 문서에 적은 뒤 여기에 고정한다 — 판정 때 그대로 나와야 한다."""
REALISTIC_COST_SIDE = 0.00023
"""참고 줄 — 무기한 현실 편도 0.023% (프로젝트 참고 줄)."""


def _setups() -> list[Setup]:
    raw = SETUPS.read_bytes()
    if hashlib.sha256(raw).hexdigest() != SETUPS_SHA256:
        raise SystemExit("셋업 파일이 문서 94 와 다르다 (SHA-256 불일치)")
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


def _bars() -> Iterator[Bar]:
    for m in _vision_minutes(SPOT_BTC_DIR, "BTCUSDT"):
        yield Bar(m.open_ms, float(m.open), float(m.high), float(m.low), float(m.close))


def fills() -> dict[str, object]:
    _, counts = simulate(_setups(), _bars(), Rules())
    n = counts.filled
    mde = 2.49 * SD_R / math.sqrt(n) if n else math.inf
    return {
        "setups": counts.setups,
        "skipped_already_past_entry": counts.skipped,
        "replaced_while_pending": counts.replaced,
        "ignored_while_holding": counts.ignored_in_position,
        "cancelled_extension": counts.cancelled_extension,
        "expired": counts.expired,
        "filled": n,
        "mde_mean_r": round(mde, 3),
        "gate_mde_le_0_5r": mde <= MDE_GATE_R,
        "note": "문서 95 관문. 손익(R)·청산 이유는 출력하지 않았다.",
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


def verdict() -> dict[str, object]:
    if VERDICT.exists():
        raise SystemExit(f"이미 판정했다: {VERDICT}")
    if EXPECTED_FILLS is None:
        raise SystemExit("관문 체결 수가 고정되지 않았다 — 먼저 fills 를 문서에 적는다")
    setups = _setups()
    trades, counts = simulate(setups, _bars(), Rules())
    if counts.filled != EXPECTED_FILLS:
        raise SystemExit(f"체결 수가 관문과 다르다: {counts.filled} != {EXPECTED_FILLS}")
    real, _ = simulate(setups, _bars(), Rules(cost_side=REALISTIC_COST_SIDE))
    gross, _ = simulate(setups, _bars(), Rules(cost_side=0.0))
    main = _stats([t.r_net for t in trades])
    p = main["p_one_sided_normal_approx"]

    def year(t: Trade) -> str:
        return datetime.fromtimestamp(t.fill_ms / 1000, UTC).strftime("%Y")

    def by(key: str) -> dict[str, list[float]]:
        groups: dict[str, list[float]] = {}
        for t in trades:
            label = year(t) if key == "year" else str(t.setup.direction)
            groups.setdefault(label, []).append(t.r_net)
        return {k: [len(g), round(sum(g) / len(g), 4)] for k, g in sorted(groups.items())}

    record: dict[str, object] = {
        "main_net_r": main,
        "significant_0_05": p < 0.05,
        "mean_r_positive_after_cost": main["mean_r"] > 0,
        "secondary_descriptive": {
            "gross_r": _stats([t.r_gross for t in gross]),
            "realistic_cost_r": _stats([t.r_net for t in real]),
            "exit_reasons": dict(sorted(Counter(t.reason for t in trades).items())),
            "win_rate_target": round(sum(t.reason == "TARGET" for t in trades) / len(trades), 4),
            "by_year": by("year"),
            "by_direction": by("direction"),
            "worst_r": round(min(t.r_net for t in trades), 3),
            "max_losing_streak": _streak(trades),
        },
        "note": "문서 95 1회 판정. p 는 정규 근사. 보조는 기술 통계(판정 아님).",
    }
    OUT.mkdir(parents=True, exist_ok=True)
    VERDICT.write_text(json.dumps(record, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return record


def _streak(trades: Sequence[Trade]) -> int:
    best = run = 0
    for t in trades:
        run = run + 1 if t.r_net < 0 else 0
        best = max(best, run)
    return best


def main(argv: list[str]) -> int:
    modes = {"fills": fills, "verdict": verdict}
    if len(argv) != 1 or argv[0] not in modes:
        raise SystemExit("usage: audit_ew95_wave3_trades.py fills|verdict")
    sys.stdout.write(json.dumps(modes[argv[0]](), ensure_ascii=True, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
