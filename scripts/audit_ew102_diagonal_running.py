"""문서 102 — 대각선 뒤 되돌림(H-DG) · 러닝 삼각형 뒤 돌파(H-RT), 10 코인 1h degree, EW-5 검출기.

``power-asset SYMBOL``: 발동마다 검출 정보를 파일로 남긴다 (수익률 미계산).
``gate``: 가설별 독립 사건·대조 수와 MDE (수익률 미계산).
``verdict``: 사용자 승인 뒤 **한 번만** — 발동 파일(SHA 고정)과 종가만 읽는다.
"""

from __future__ import annotations

import hashlib
import json
import math
import sys
from collections import Counter
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from statistics import pstdev

REPO_ROOT = Path(__file__).resolve().parent.parent
for _p in (REPO_ROOT, REPO_ROOT / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from scripts.audit_ew94_wave3_count import _bars  # noqa: E402
from scripts.audit_ew97_correction_resume import _real, _thin, _welch  # noqa: E402
from scripts.audit_ew_edge_power import _chunks, _groups  # noqa: E402

from auto_trading.research.elliott.minute_path import replay_minute_groups_ladder  # noqa: E402
from auto_trading.research.elliott.parser import Label  # noqa: E402
from auto_trading.research.elliott.scanner import (  # noqa: E402
    EW3E_LOOKBACK_BARS,
    ScenarioList,
    Trigger,
    causal_triggers,
    scenario_lists,
)
from scripts import audit_ew98_triangle_correction as ew98  # noqa: E402

SYMBOLS = (
    "BTCUSDT",
    "ETHUSDT",
    "SOLUSDT",
    "XRPUSDT",
    "BNBUSDT",
    "ADAUSDT",
    "DOGEUSDT",
    "LTCUSDT",
    "LINKUSDT",
    "TRXUSDT",
)
OUT = REPO_ROOT / "reports" / "research" / "elliott" / "102"
HORIZON, H4 = ew98.HORIZON, ew98.H4
ALPHA = 0.025
COST = ew98.COST
REAL_PERP = 0.00023
GATE_SD_UNITS = ew98.GATE_SD_UNITS
DIAGONALS = frozenset({Label.ENDING_DIAGONAL, Label.LEADING_DIAGONAL})
ASSET_SHA256: dict[str, str] = {}
"""코인 → 발동 파일 SHA-256. ``gate`` 뒤(수익률 보기 전) 문서 102 와 함께 고정한다."""
EXPECTED: dict[str, tuple[int, int]] | None = None
"""가설 → (독립 사건, 독립 대조). 판정 때 그대로 나와야 한다."""


def _pct(trigger: Trigger, a: int, b: int) -> float:
    pa, pb = float(trigger.segment[a].price), float(trigger.segment[b].price)
    return abs(pb / pa - 1)


def _shape(scen: ScenarioList, trigger: Trigger) -> dict[str, object]:
    """검출(검증 비율 1) 모양과, 순위가 가장 높은 대각선·러닝 삼각형의 기술용 값."""
    full = [(p, key) for p, key in scen.ranked if key.neg_verified == -1]
    detected = sorted({p.pattern.value for p, _ in full})
    first_key = scen.ranked[0][1] if scen.ranked else None
    rank1 = sorted({p.pattern.value for p, key in scen.ranked if key == first_key})
    dg = next((p for p, _ in full if p.pattern in DIAGONALS), None)
    rt = next((p for p, _ in full if p.pattern is Label.RUNNING_TRIANGLE), None)
    seg = trigger.segment
    return {
        "detected": detected,
        "rank1": rank1,
        "dg_subtypes": sorted({p.pattern.value for p, _ in full if p.pattern in DIAGONALS}),
        "dg_start_price": None if dg is None else float(seg[dg.bounds[0]].price),
        "end_price": float(seg[-1].price),
        "rt_widest_pct": None
        if rt is None
        else max(_pct(trigger, a, b) for a, b in zip(rt.bounds, rt.bounds[1:], strict=False)),
    }


def is_dg(row: dict[str, object]) -> bool:
    det = row["detected"]
    assert isinstance(det, list)
    return Label.ENDING_DIAGONAL.value in det or Label.LEADING_DIAGONAL.value in det


def is_rt(row: dict[str, object]) -> bool:
    det = row["detected"]
    assert isinstance(det, list)
    return Label.RUNNING_TRIANGLE.value in det


HYPOTHESES: dict[str, Callable[[dict[str, object]], bool]] = {"H-DG": is_dg, "H-RT": is_rt}


def _triggers_path(symbol: str) -> Path:
    return OUT / f"triggers_1h_{symbol}.jsonl"


def power_asset(symbol: str) -> dict[str, object]:
    """코인 하나의 발동마다 검출 정보를 파일로 남긴다. 수익률은 계산하지 않는다."""
    four, hours, minutes, threshold = _bars("1h", symbol)
    rows: list[dict[str, object]] = []
    for chunk in _chunks(four):
        (replay,) = replay_minute_groups_ladder(
            chunk, _groups(minutes, chunk), chunk[-1].close_ms, (threshold,)
        )
        chunk_hours = [h for h in hours if chunk[0].open_ms <= h.open_ms <= chunk[-1].close_ms]
        triggers = causal_triggers(replay, chunk_hours, chunk, all_new_pivots=True)
        lists = scenario_lists(
            triggers,
            chunk_hours,
            lookback_bars=EW3E_LOOKBACK_BARS,
            ew4=True,
            nested=True,
            ew5=True,
        )
        for trig, scen in zip(triggers, lists, strict=True):
            rows.append(
                {
                    "symbol": symbol,
                    "close_ms": trig.close_ms,
                    "real_close_ms": _real(trig.close_ms),
                    "d": trig.direction,
                    **_shape(scen, trig),
                }
            )
    OUT.mkdir(parents=True, exist_ok=True)
    path = _triggers_path(symbol)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        for r in rows:
            f.write(json.dumps(r, sort_keys=True) + "\n")
    return {
        "symbol": symbol,
        "real_hour_bars": len(four),
        "triggers": len(rows),
        "dg_all": sum(1 for r in rows if is_dg(r)),
        "rt_all": sum(1 for r in rows if is_rt(r)),
        "triggers_file_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "note": "문서 102 코인별 발동. 수익률은 계산하지 않았다.",
    }


def _load(symbol: str, *, check: bool) -> list[dict[str, object]]:
    raw = _triggers_path(symbol).read_bytes()
    if check:
        want = ASSET_SHA256.get(symbol, "")
        if not want or hashlib.sha256(raw).hexdigest() != want:
            raise SystemExit(f"{symbol} 발동 파일 해시가 고정값과 다르다 (또는 고정 전)")
    return [json.loads(line) for line in raw.decode("utf-8").splitlines()]


def _split(
    rows: Sequence[dict[str, object]],
    closes: dict[str, dict[int, float]],
    pred: Callable[[dict[str, object]], bool],
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    usable = [r for r in rows if int(str(r["close_ms"])) + HORIZON * H4 in closes[str(r["symbol"])]]
    return _thin([r for r in usable if pred(r)]), _thin([r for r in usable if not pred(r)])


def gate() -> dict[str, object]:
    rows = [r for s in SYMBOLS for r in _load(s, check=False)]
    closes = {s: ew98._closes(s) for s in SYMBOLS}
    fwd = [
        math.log(c[t + HORIZON * H4] / v)
        for c in closes.values()
        for t, v in c.items()
        if t + HORIZON * H4 in c
    ]
    sd = pstdev(fwd)

    def year(r: dict[str, object]) -> str:
        return datetime.fromtimestamp(int(str(r["real_close_ms"])) / 1000, UTC).strftime("%Y")

    out: dict[str, object] = {
        "triggers_per_asset": {s: sum(1 for r in rows if r["symbol"] == s) for s in SYMBOLS},
        "sd_pooled_30h": round(sd, 5),
        "gate_threshold": round(GATE_SD_UNITS * sd, 5),
        "files_sha256": {
            s: hashlib.sha256(_triggers_path(s).read_bytes()).hexdigest() for s in SYMBOLS
        },
    }
    for name, pred in HYPOTHESES.items():
        ev, co = _split(rows, closes, pred)
        mde = 2.84 * sd * math.sqrt(1 / len(ev) + 1 / len(co)) if ev and co else math.inf
        out[name] = {
            "all_per_asset": {
                s: sum(1 for r in rows if r["symbol"] == s and pred(r)) for s in SYMBOLS
            },
            "n1_events_independent": len(ev),
            "n2_controls_independent": len(co),
            "events_by_asset": dict(sorted(Counter(str(r["symbol"]) for r in ev).items())),
            "events_by_year": dict(sorted(Counter(year(r) for r in ev).items())),
            "events_by_direction": dict(sorted(Counter(str(r["d"]) for r in ev).items())),
            "mde": round(mde, 5),
            "gate_pass": mde <= GATE_SD_UNITS * sd,
        }
    out["note"] = "문서 102 §5 관문. 사건 뒤 수익률은 계산하지 않았다."
    return out


def verdict() -> dict[str, object]:
    path = OUT / "verdict.json"
    if path.exists():
        raise SystemExit(f"이미 판정했다: {path}")
    if EXPECTED is None:
        raise SystemExit("관문 수가 고정되지 않았다")
    rows = [r for s in SYMBOLS for r in _load(s, check=True)]
    closes = {s: ew98._closes(s) for s in SYMBOLS}
    gate_info = json.loads((OUT / "gate.json").read_text(encoding="utf-8"))

    def r(row: dict[str, object], bars: int) -> float | None:
        c = closes[str(row["symbol"])]
        t = int(str(row["close_ms"]))
        later = c.get(t + bars * H4)
        return None if later is None else -int(str(row["d"])) * math.log(later / c[t])

    def values(rows_: Sequence[dict[str, object]], bars: int) -> list[float]:
        return [v for v in (r(x, bars) for x in rows_) if v is not None]

    def year(x: dict[str, object]) -> str:
        return datetime.fromtimestamp(int(str(x["real_close_ms"])) / 1000, UTC).strftime("%Y")

    record: dict[str, object] = {}
    for name, pred in HYPOTHESES.items():
        ev, co = _split(rows, closes, pred)
        if (len(ev), len(co)) != EXPECTED[name]:
            raise SystemExit(f"{name} 사건·대조 수가 관문과 다르다: {(len(ev), len(co))}")
        if not gate_info[name]["gate_pass"]:
            record[name] = {"opened": False, "note": "관문 미달 — 수익률 미열람"}
            continue
        main = _welch(values(ev, HORIZON), values(co, HORIZON))
        diff = float(str(main["diff"]))
        p = float(str(main["p_one_sided_normal_approx"]))
        groups: dict[str, list[float]] = {}
        for x in ev:
            v = r(x, HORIZON)
            if v is None:
                continue
            for key in (f"symbol={x['symbol']}", f"year={year(x)}", f"d={x['d']}"):
                groups.setdefault(key, []).append(v)
            if name == "H-DG":
                subs = x["dg_subtypes"]
                assert isinstance(subs, list)
                groups.setdefault("sub=" + "+".join(subs), []).append(v)
        extra: dict[str, object] = {}
        if name == "H-DG":
            hit = []
            for x in ev:
                c = closes[str(x["symbol"])]
                t = int(str(x["close_ms"]))
                start = float(str(x["dg_start_price"]))
                path_ = [c[t + k * H4] for k in range(1, HORIZON + 1) if t + k * H4 in c]
                if not path_:
                    continue
                d = int(str(x["d"]))
                hit.append(any((d > 0 and v <= start) or (d < 0 and v >= start) for v in path_))
            extra["reached_diagonal_start_30h"] = round(sum(hit) / len(hit), 4) if hit else None
        else:
            reached = [
                v >= float(str(x["rt_widest_pct"]))
                for x in ev
                if x["rt_widest_pct"] is not None and (v := r(x, HORIZON)) is not None
            ]
            extra["thrust_reached_widest_leg_30h"] = (
                round(sum(reached) / len(reached), 4) if reached else None
            )
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
                **extra,
            },
        }
    record["note"] = "문서 102 1 회 판정. p 는 정규 근사. 보조는 기술 통계(판정 아님)."
    path.write_text(json.dumps(record, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return record


def main(argv: list[str]) -> int:
    if len(argv) == 2 and argv[0] == "power-asset" and argv[1] in SYMBOLS:
        result = power_asset(argv[1])
    elif argv == ["gate"]:
        result = gate()
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / "gate.json").write_text(json.dumps(result, indent=1) + "\n", encoding="utf-8")
    elif argv == ["verdict"]:
        result = verdict()
    else:
        raise SystemExit("usage: audit_ew102 power-asset SYMBOL | gate | verdict")
    sys.stdout.write(json.dumps(result, ensure_ascii=True, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
