"""문서 99 — Elliott 삼각형 E 끝 → 돌파(H-T) 재현 시험, 다른 코인 7 개 (1h degree).

98 의 판독·모양·사건 정의를 그대로 쓴다. 자산 목록·저장 위치·고정값만 다르다.
``power-asset SYMBOL`` (수익률 없음) → ``gate`` (합친 관문) → ``verdict`` (사용자 승인 뒤 1 회).
"""

from __future__ import annotations

import hashlib
import json
import math
import sys
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from statistics import pstdev

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from scripts.audit_ew97_correction_resume import _thin, _welch  # noqa: E402

from scripts import audit_ew98_triangle_correction as ew98  # noqa: E402

SYMBOLS = ("XRPUSDT", "BNBUSDT", "ADAUSDT", "DOGEUSDT", "LTCUSDT", "LINKUSDT", "TRXUSDT")
"""문서 99 §2 — 2019-07 이전부터 Binance 현물 1m 이 있는 주요 코인 (BTC·ETH·SOL 제외)."""
OUT = REPO_ROOT / "reports" / "research" / "elliott" / "99"
ASSET_SHA256: dict[str, str] = {
    "XRPUSDT": "1b17bb36a807ffd63c6e943701a2c9eeca3b4e6e85d1188f96207532365813ca",
    "BNBUSDT": "02406dc1d8f090940ad04e169b0494e3f43a3e0ec9bd5fa815171c2e77740675",
    "ADAUSDT": "de474afdc47917da7a153f949a7cac586e3f91c834231f4617b02c62aea5b3c3",
    "DOGEUSDT": "4af5b196f9b7c08491c409ce942353a3ed6e86442a023ab9ff03fd9e10d22daf",
    "LTCUSDT": "3261866e975a66330591361b9d2e5eb79bdd3bc40798e09ad2371202d875cbaa",
    "LINKUSDT": "e016187ddf327b4c00982349ae15222de608ff793ec3e0bf9b9fafe40b80b4af",
    "TRXUSDT": "a56a712c2f87ae87ce65d55b2a50cf8978c0b43b97ef1fee89edaa86b566b9f0",
}
"""코인 → 발동 파일 SHA-256 (수익률 보기 전 고정)."""
EXPECTED: tuple[int, int] | None = (401, 2510)
"""합친 관문의 (독립 사건, 독립 대조)."""
HORIZON, H4 = ew98.HORIZON, ew98.H4
ALPHA = 0.05
COST = ew98.COST
GATE_SD_UNITS = ew98.GATE_SD_UNITS


def _path(symbol: str) -> Path:
    return OUT / f"triggers_1h_{symbol}.jsonl"


def power_asset(symbol: str) -> dict[str, object]:
    ew98.OUT = OUT  # 98 의 계산을 그대로 쓰되 99 폴더에 쓴다
    result = ew98.power_asset(symbol)
    result["note"] = "문서 99 자산별 발동. 수익률은 계산하지 않았다."
    return result


def _load(symbol: str) -> list[dict[str, object]]:
    raw = _path(symbol).read_bytes()
    want = ASSET_SHA256.get(symbol, "")
    if not want or hashlib.sha256(raw).hexdigest() != want:
        raise SystemExit(f"{symbol} 발동 파일 해시가 고정값과 다르다 (또는 고정 전)")
    return [json.loads(line) for line in raw.decode("utf-8").splitlines()]


def _split(
    rows: Sequence[dict[str, object]], closes: dict[str, dict[int, float]]
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    usable = [r for r in rows if int(str(r["close_ms"])) + HORIZON * H4 in closes[str(r["symbol"])]]
    return (
        _thin([r for r in usable if ew98.is_ht(r)]),
        _thin([r for r in usable if not ew98.is_ht(r)]),
    )


def gate() -> dict[str, object]:
    rows = [r for s in SYMBOLS for r in _load(s)]
    closes = {s: ew98._closes(s) for s in SYMBOLS}
    ev, co = _split(rows, closes)
    fwd = [
        math.log(c[t + HORIZON * H4] / v)
        for c in closes.values()
        for t, v in c.items()
        if t + HORIZON * H4 in c
    ]
    sd = pstdev(fwd)
    mde = 2.84 * sd * math.sqrt(1 / len(ev) + 1 / len(co)) if ev and co else math.inf

    def year(r: dict[str, object]) -> str:
        return datetime.fromtimestamp(int(str(r["real_close_ms"])) / 1000, UTC).strftime("%Y")

    return {
        "ht_per_asset": {
            s: sum(1 for r in rows if r["symbol"] == s and ew98.is_ht(r)) for s in SYMBOLS
        },
        "n1_events_independent": len(ev),
        "n2_controls_independent": len(co),
        "events_by_asset": dict(sorted(Counter(str(r["symbol"]) for r in ev).items())),
        "events_by_year": dict(sorted(Counter(year(r) for r in ev).items())),
        "sd_pooled_30h": round(sd, 5),
        "mde": round(mde, 5),
        "gate_threshold": round(GATE_SD_UNITS * sd, 5),
        "gate_pass": mde <= GATE_SD_UNITS * sd,
        "note": "문서 99 합친 관문. 사건 뒤 수익률은 계산하지 않았다.",
    }


def verdict() -> dict[str, object]:
    path = OUT / "verdict.json"
    if path.exists():
        raise SystemExit(f"이미 판정했다: {path}")
    if EXPECTED is None:
        raise SystemExit("합친 관문 수가 고정되지 않았다")
    rows = [r for s in SYMBOLS for r in _load(s)]
    closes = {s: ew98._closes(s) for s in SYMBOLS}
    ev, co = _split(rows, closes)
    if (len(ev), len(co)) != EXPECTED:
        raise SystemExit(f"사건·대조 수가 관문과 다르다: {(len(ev), len(co))}")

    def r(row: dict[str, object], bars: int) -> float | None:
        c = closes[str(row["symbol"])]
        t = int(str(row["close_ms"]))
        later = c.get(t + bars * H4)
        return None if later is None else -int(str(row["d"])) * math.log(later / c[t])

    def values(rows_: Sequence[dict[str, object]], bars: int) -> list[float]:
        return [v for v in (r(x, bars) for x in rows_) if v is not None]

    main = _welch(values(ev, HORIZON), values(co, HORIZON))
    per_asset = {
        s: _welch(
            values([x for x in ev if x["symbol"] == s], HORIZON),
            values([x for x in co if x["symbol"] == s], HORIZON),
        )
        for s in SYMBOLS
        if sum(1 for x in ev if x["symbol"] == s) > 1
    }
    reached = [
        v >= float(str(x["tri_a_pct"]))
        for x in ev
        if x["tri_a_pct"] is not None and (v := r(x, HORIZON)) is not None
    ]
    record: dict[str, object] = {
        "hypothesis": "H-T replication on 7 altcoins",
        "main_30h": main,
        "significant_0_05": float(str(main["p_one_sided_normal_approx"])) < ALPHA,
        "diff_exceeds_cost_0_19pct": float(str(main["diff"])) > COST,
        "secondary_descriptive": {
            "per_asset_30h": per_asset,
            "horizon_6h": _welch(values(ev, 6), values(co, 6)),
            "horizon_60h": _welch(values(ev, 60), values(co, 60)),
            "thrust_reached_widest_leg_30h": round(sum(reached) / len(reached), 4)
            if reached
            else None,
        },
        "note": "문서 99 1회 판정. p 는 정규 근사. 보조는 기술 통계(판정 아님).",
    }
    path.write_text(json.dumps(record, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return record


def main(argv: list[str]) -> int:
    if len(argv) == 2 and argv[0] == "power-asset" and argv[1] in SYMBOLS:
        result = power_asset(argv[1])
    elif argv == ["gate"]:
        result = gate()
    elif argv == ["verdict"]:
        result = verdict()
    else:
        raise SystemExit("usage: audit_ew99 power-asset SYMBOL|gate|verdict")
    sys.stdout.write(json.dumps(result, ensure_ascii=True, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
