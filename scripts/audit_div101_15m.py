"""문서 101 — 15 분 봉 RSI 정규 다이버전스, 10 코인, 4 시간(16 봉) 뒤 방향.

사용::

    python scripts/audit_div101_15m.py build    # 1m zip → 15 분 캐시 (수익률 계산 없음)
    python scripts/audit_div101_15m.py power    # 사건 파일 · 가설별 n1·n2·MDE (수익률 미계산)
    python scripts/audit_div101_15m.py verdict  # 사용자 승인 뒤 한 번 (SHA·기대 수 고정 필요)

다이버전스 정의는 TradingView 기본 "RSI Divergence Indicator" 와 같다 (문서 101 §3).
"""

from __future__ import annotations

import bisect
import csv
import hashlib
import json
import math
import sys
import zipfile
from collections import defaultdict
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from pathlib import Path
from statistics import NormalDist, pstdev

REPO_ROOT = Path(__file__).resolve().parent.parent
VISION = REPO_ROOT / "data" / "external" / "binance_vision" / "spot"
CACHE = REPO_ROOT / "data" / "external" / "derived" / "15m"
OUT = REPO_ROOT / "reports" / "research" / "divergence" / "101"
EVENTS = OUT / "events.jsonl"
VERDICT = OUT / "verdict.json"

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
STEP = 15 * 60_000
RSI_N = 14
PIVOT = 5
RANGE_MIN, RANGE_MAX = 5, 60
WARMUP = 100
HORIZON = 16
GATE_MDE = 0.0008
ALPHA = 0.025
HOLM = (0.0125, 0.025)
COST_PERP, COST_SPOT = 0.00095, 0.0015
REAL_PERP, REAL_SPOT = 0.00023, 0.0011
DESCRIPTIVE_HORIZONS = (4, 8, 32)
_MICROS = 10**14

CACHE_SHA256: dict[str, str] = {
    "BTCUSDT": "0b5eebe0a13232a5bba0e1257eafa370b53567e81210bc884880b2cdaca2995c",
    "ETHUSDT": "e1c250d5e141d3702c70ce0eb946290441d370ec68f220da51bc671782aad3dd",
    "SOLUSDT": "22fc12a7bd29dd761bd8e09dbfde9793696f73d3d9c5d1f75d93b4a5170b21cc",
    "XRPUSDT": "4835acf1b7d5ac02aba9b501281eb9a43bbabbfb45660ff0906beba63cb7b607",
    "BNBUSDT": "c59b6310be088664496680780a5c0b6e1dc75f286ea280586c595b5ae8429802",
    "ADAUSDT": "7ba90e032d801bc28d2f4865df244adca7da3021699bf170fee48d7652e3f970",
    "DOGEUSDT": "48e327658e3720a3f64ece89d1c754e5d2a399c9f26bec096da47a3cd47ff14b",
    "LTCUSDT": "47a118fdfe7ffac7005f2a44cf48be12b295585f0cab7133b75dd5ca7dd3ae25",
    "LINKUSDT": "7bce09647ccccf7f869a82932cbfc643c7aa3dbdd4ba060e91a6d645e687b8f3",
    "TRXUSDT": "bcbcec42ddad2ec65916bdd4eddf9ce677f946832acc6f67e059e5b76c0eb97b",
}
"""코인 → 15 분 캐시 SHA-256. ``power`` 뒤(수익률 보기 전) 문서 101 과 함께 고정한다."""
EVENTS_SHA256 = "0dc08be1ead57b73a7a31c757c9d8ea15b4e3f72631812c413c7d167b681c0c9"
EXPECTED: dict[str, tuple[int, int]] | None = {
    "H-D": (11079, 19555),
    "S1": (6777, 19555),
    "S2": (6237, 19555),
}
"""가설 → (독립 사건, 독립 대조). 판정 때 그대로 나와야 한다."""

Bar = tuple[int, float, float, float, float]  # open_ms, open, high, low, close


# ---------------------------------------------------------------- 15 분 캐시


def _minutes(symbol: str) -> Iterator[tuple[int, float, float, float, float]]:
    for path in sorted((VISION / symbol / "1m").glob(f"{symbol}-1m-*.zip")):
        with zipfile.ZipFile(path) as archive:
            (name,) = archive.namelist()
            for line in archive.read(name).decode("ascii").splitlines():
                cells = line.split(",", 6)
                if not cells[0].isdigit():
                    continue
                open_ms = int(cells[0])
                if open_ms > _MICROS:
                    open_ms //= 1000
                yield open_ms, float(cells[1]), float(cells[2]), float(cells[3]), float(cells[4])


def build() -> dict[str, object]:
    CACHE.mkdir(parents=True, exist_ok=True)
    out: dict[str, object] = {}
    for symbol in SYMBOLS:
        rows: list[Bar] = []
        cur: list[float] | None = None
        cur_start = -1
        for open_ms, o, h, low, c in _minutes(symbol):
            start = open_ms // STEP * STEP
            if start != cur_start:
                if cur is not None:
                    rows.append((cur_start, cur[0], cur[1], cur[2], cur[3]))
                cur, cur_start = [o, h, low, c], start
            else:
                assert cur is not None
                cur[1] = max(cur[1], h)
                cur[2] = min(cur[2], low)
                cur[3] = c
        if cur is not None:
            rows.append((cur_start, cur[0], cur[1], cur[2], cur[3]))
        path = CACHE / f"{symbol}.csv"
        with path.open("w", newline="", encoding="ascii") as fh:
            writer = csv.writer(fh, lineterminator="\n")
            writer.writerow(("open_ms", "open", "high", "low", "close"))
            writer.writerows((r[0], repr(r[1]), repr(r[2]), repr(r[3]), repr(r[4])) for r in rows)
        out[symbol] = {"bars": len(rows), "sha256": _sha(path)}
        _log(f"build {symbol} {len(rows)}")
    return out


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load(symbol: str) -> list[Bar]:
    with (CACHE / f"{symbol}.csv").open(encoding="ascii") as fh:
        reader = csv.reader(fh)
        next(reader)
        return [(int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4])) for r in reader]


def _segments(bars: Sequence[Bar]) -> list[list[Bar]]:
    out: list[list[Bar]] = []
    for bar in bars:
        if out and bar[0] == out[-1][-1][0] + STEP:
            out[-1].append(bar)
        else:
            out.append([bar])
    return out


# ---------------------------------------------------------------- 다이버전스 (문서 101 §3)


def rsi(closes: Sequence[float], n: int = RSI_N) -> list[float | None]:
    """Wilder RSI. 앞 n 봉은 None."""
    out: list[float | None] = [None] * len(closes)
    if len(closes) <= n:
        return out
    gains = losses = 0.0
    for k in range(1, n + 1):
        d = closes[k] - closes[k - 1]
        gains += max(d, 0.0)
        losses += max(-d, 0.0)
    avg_g, avg_l = gains / n, losses / n
    out[n] = 100.0 if avg_l == 0 else 100 - 100 / (1 + avg_g / avg_l)
    for k in range(n + 1, len(closes)):
        d = closes[k] - closes[k - 1]
        avg_g = (avg_g * (n - 1) + max(d, 0.0)) / n
        avg_l = (avg_l * (n - 1) + max(-d, 0.0)) / n
        out[k] = 100.0 if avg_l == 0 else 100 - 100 / (1 + avg_g / avg_l)
    return out


def divergences(seg: Sequence[Bar]) -> list[dict[str, object]]:
    """구간 하나의 정규 다이버전스. 사건 색인 t = k2 + PIVOT (확정 봉), 16 봉 뒤 자료 필요."""
    values = rsi([b[4] for b in seg])
    out: list[dict[str, object]] = []
    last: dict[int, int | None] = {
        +1: None,
        -1: None,
    }  # +1: 저점 꼭짓점(강세), −1: 고점 꼭짓점(약세)
    for k in range(max(WARMUP, PIVOT), len(seg) - PIVOT):
        v = values[k]
        if v is None:
            continue
        window = [values[k + i] for i in range(-PIVOT, PIVOT + 1) if i != 0]
        if any(w is None for w in window):
            continue
        for side in (+1, -1):
            is_pivot = all((w > v) if side == +1 else (w < v) for w in window)  # type: ignore[operator]
            if not is_pivot:
                continue
            k1 = last[side]
            last[side] = k
            if k1 is None or not RANGE_MIN <= k - k1 <= RANGE_MAX:
                continue
            r1, r2 = values[k1], v
            assert r1 is not None
            if side == +1:
                hit = r2 > r1 and seg[k][3] < seg[k1][3]
                extreme = r1 < 30
            else:
                hit = r2 < r1 and seg[k][2] > seg[k1][2]
                extreme = r1 > 70
            t = k + PIVOT
            if hit and t + HORIZON < len(seg):
                out.append(
                    {
                        "t": t,
                        "close_ms": seg[t][0] + STEP - 1,
                        "side": side,
                        "rsi1": round(r1, 4),
                        "rsi2": round(r2, 4),
                        "k1_ms": seg[k1][0],
                        "k2_ms": seg[k][0],
                        "extreme": extreme,
                    }
                )
    return out


# ---------------------------------------------------------------- 독립성 (문서 101 §4)


def _tie(close_ms: int, symbol: str) -> str:
    return hashlib.sha256(f"{close_ms}-{symbol}".encode()).hexdigest()


def thin(rows: Sequence[tuple[int, str]]) -> list[tuple[int, str]]:
    """(close_ms, symbol) 를 한 시간축에서 16 봉 창 안이면 버린다. 동점은 SHA 순."""
    kept: list[tuple[int, str]] = []
    last = -(10**18)
    for c, s in sorted(rows, key=lambda x: (x[0], _tie(x[0], x[1]))):
        if c - last >= HORIZON * STEP:
            kept.append((c, s))
            last = c
    return kept


def _thin_controls(by_time: dict[int, list[str]]) -> list[tuple[int, str]]:
    kept: list[tuple[int, str]] = []
    last = -(10**18)
    for c in sorted(by_time):
        if c - last >= HORIZON * STEP:
            kept.append((c, min(by_time[c], key=lambda s: _tie(c, s))))
            last = c
    return kept


# ---------------------------------------------------------------- 표본 (수익률 없음)


def _universe() -> tuple[dict[str, list[list[Bar]]], list[dict[str, object]], dict[int, list[str]]]:
    segs: dict[str, list[list[Bar]]] = {}
    events: list[dict[str, object]] = []
    controls: dict[int, list[str]] = defaultdict(list)
    for symbol in SYMBOLS:
        segs[symbol] = _segments(_load(symbol))
        for seg in segs[symbol]:
            found = divergences(seg)
            event_t = {int(str(e["t"])) for e in found}
            for e in found:
                e.pop("t")
                events.append({"symbol": symbol, **e})
            for t in range(WARMUP, len(seg) - HORIZON):
                if t not in event_t:
                    controls[seg[t][0] + STEP - 1].append(symbol)
        _log(f"universe {symbol}")
    return segs, events, controls


HYPOTHESES = ("H-D", "S1", "S2")


def _select(events: Sequence[dict[str, object]], hyp: str) -> list[dict[str, object]]:
    if hyp == "H-D":
        return list(events)
    if hyp == "S1":
        return [e for e in events if e["extreme"]]
    return [e for e in events if int(str(e["side"])) == +1]


def _thin_events(events: Sequence[dict[str, object]]) -> list[dict[str, object]]:
    index = {(int(str(e["close_ms"])), str(e["symbol"])): e for e in events}
    return [index[key] for key in thin(list(index))]


def _sd_all(segs: dict[str, list[list[Bar]]]) -> float:
    returns = [
        math.log(seg[t + HORIZON][4] / seg[t][4])
        for symbol in SYMBOLS
        for seg in segs[symbol]
        for t in range(WARMUP, len(seg) - HORIZON)
    ]
    return pstdev(returns)


def power() -> dict[str, object]:
    OUT.mkdir(parents=True, exist_ok=True)
    segs, events, controls = _universe()
    with EVENTS.open("w", encoding="utf-8", newline="\n") as fh:
        for e in sorted(events, key=lambda x: (int(str(x["close_ms"])), str(x["symbol"]))):
            fh.write(json.dumps(e, sort_keys=True) + "\n")
    ctrl = _thin_controls(controls)
    sd = _sd_all(segs)
    out: dict[str, object] = {
        "events_raw": len(events),
        "events_by_symbol": {s: sum(1 for e in events if e["symbol"] == s) for s in SYMBOLS},
        "controls_thinned": len(ctrl),
        "sd_16bar_all": round(sd, 6),
        "events_sha256": _sha(EVENTS),
        "cache_sha256": {s: _sha(CACHE / f"{s}.csv") for s in SYMBOLS},
    }
    for hyp in HYPOTHESES:
        kept = _thin_events(_select(events, hyp))
        n1, n2 = len(kept), len(ctrl)
        bull = sum(1 for e in kept if int(str(e["side"])) == +1)
        mde = 2.84 * sd * math.sqrt(1 / max(1, n1) + 1 / max(1, n2))
        out[hyp] = {
            "n1": n1,
            "n2": n2,
            "bull": bull,
            "bear": n1 - bull,
            "mde": round(mde, 6),
            "gate_pass": mde <= GATE_MDE,
        }
    (OUT / "power.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    return out


# ---------------------------------------------------------------- 판정 (사용자 승인 뒤 한 번)


def _closes(segs: dict[str, list[list[Bar]]]) -> dict[str, tuple[list[int], list[float]]]:
    out: dict[str, tuple[list[int], list[float]]] = {}
    for symbol, parts in segs.items():
        ms = [b[0] + STEP - 1 for seg in parts for b in seg]
        cl = [b[4] for seg in parts for b in seg]
        out[symbol] = (ms, cl)
    return out


def _fwd(lookup: dict[str, tuple[list[int], list[float]]], symbol: str, c: int, h: int) -> float:
    ms, cl = lookup[symbol]
    i = bisect.bisect_left(ms, c)
    assert ms[i] == c
    if i + h >= len(ms) or ms[i + h] != c + h * STEP:
        return math.nan
    return math.log(cl[i + h] / cl[i])


def _stats(
    r_event: Sequence[float], signs: Sequence[int], r_ctrl: Sequence[float]
) -> dict[str, object]:
    def mv(xs: Sequence[float]) -> tuple[float, float]:
        m = sum(xs) / len(xs)
        return m, sum((x - m) ** 2 for x in xs) / (len(xs) - 1)

    m1, v1 = mv(r_event)
    m2, v2 = mv(r_ctrl)
    w = (sum(1 for s in signs if s > 0) - sum(1 for s in signs if s < 0)) / len(signs)
    d = m1 - w * m2
    se = math.sqrt(v1 / len(r_event) + w * w * v2 / len(r_ctrl))
    z = d / se
    return {
        "n1": len(r_event),
        "n2": len(r_ctrl),
        "mean_event": round(m1, 6),
        "mean_control": round(m2, 6),
        "w_plus_minus": round(w, 4),
        "D": round(d, 6),
        "z": round(z, 3),
        "p_one_sided": round(1 - NormalDist().cdf(z), 5),
    }


def verdict() -> dict[str, object]:
    if VERDICT.exists():
        raise SystemExit(f"이미 판정했다: {VERDICT}")
    if not CACHE_SHA256 or not EVENTS_SHA256 or EXPECTED is None:
        raise SystemExit("캐시·사건 해시·기대 수가 고정되지 않았다")
    for s in SYMBOLS:
        if _sha(CACHE / f"{s}.csv") != CACHE_SHA256[s]:
            raise SystemExit(f"{s} 캐시가 관문 때와 다르다")
    raw = EVENTS.read_bytes()
    if hashlib.sha256(raw).hexdigest() != EVENTS_SHA256:
        raise SystemExit("사건 파일이 관문 때와 다르다")
    events = [json.loads(line) for line in raw.decode("utf-8").splitlines()]
    segs, _, controls = _universe()
    lookup = _closes(segs)
    ctrl = _thin_controls(controls)
    r_ctrl = [_fwd(lookup, s, c, HORIZON) for c, s in ctrl]
    res: dict[str, dict[str, object]] = {}
    gated: dict[str, float] = {}
    for hyp in HYPOTHESES:
        kept = _thin_events(_select(events, hyp))
        if (len(kept), len(ctrl)) != EXPECTED[hyp]:
            raise SystemExit(f"{hyp} 수가 관문과 다르다: {(len(kept), len(ctrl))}")
        if power_mde(hyp) is None:
            res[hyp] = {"opened": False, "note": "관문 미달 — 수익률 미열람"}
            continue
        signs = [int(str(e["side"])) for e in kept]
        r = [
            sg * _fwd(lookup, str(e["symbol"]), int(str(e["close_ms"])), HORIZON)
            for sg, e in zip(signs, kept, strict=True)
        ]
        stats = _stats(r, signs, r_ctrl)  # S2(강세만)는 w = 1 → D = mean(R) − mean(r)
        res[hyp] = {"opened": True, **stats}
        gated[hyp] = float(str(stats["p_one_sided"]))
    # 판정 (문서 101 §5·§7): 주 가설 α 0.025, 보조 둘은 Holm m = 2 고정 (하나만 열려도 0.0125 부터)
    if "H-D" in gated:
        res["H-D"]["significant"] = gated["H-D"] < ALPHA
    side = sorted((p, h) for h, p in gated.items() if h != "H-D")
    still = True
    for rank, (p, h) in enumerate(side):
        still = still and p < HOLM[rank]
        res[h]["significant"] = still
    for h in gated:
        d = float(str(res[h]["D"]))
        cost = COST_SPOT if h == "S2" else COST_PERP
        real = REAL_SPOT if h == "S2" else REAL_PERP
        res[h]["exceeds_cost"] = d > cost
        res[h]["cost"] = cost
        res[h]["reference_realistic_cost_net"] = round(d - real, 6)
    out: dict[str, object] = dict(res)
    out["descriptive"] = _describe(events, lookup, ctrl)
    VERDICT.write_text(json.dumps(out, indent=1), encoding="utf-8")
    return out


def power_mde(hyp: str) -> float | None:
    data = json.loads((OUT / "power.json").read_text(encoding="utf-8"))
    return float(data[hyp]["mde"]) if data[hyp]["gate_pass"] else None


def _describe(
    events: Sequence[dict[str, object]],
    lookup: dict[str, tuple[list[int], list[float]]],
    ctrl: Sequence[tuple[int, str]],
) -> dict[str, object]:
    """판정 뒤 기술 통계 (확증 아님)."""
    kept = _thin_events(events)
    out: dict[str, object] = {}
    for h in DESCRIPTIVE_HORIZONS:
        r = [
            int(str(e["side"])) * _fwd(lookup, str(e["symbol"]), int(str(e["close_ms"])), h)
            for e in kept
        ]
        rc = [_fwd(lookup, s, c, h) for c, s in ctrl]
        r = [x for x in r if not math.isnan(x)]
        rc = [x for x in rc if not math.isnan(x)]
        out[f"h{h}"] = {
            "mean_event": round(sum(r) / len(r), 6),
            "mean_control": round(sum(rc) / len(rc), 6),
        }
    groups: dict[str, list[float]] = defaultdict(list)
    for e in kept:
        sg = int(str(e["side"]))
        x = sg * _fwd(lookup, str(e["symbol"]), int(str(e["close_ms"])), HORIZON)
        year = datetime.fromtimestamp(int(str(e["close_ms"])) / 1000, UTC).strftime("%Y")
        groups[f"symbol={e['symbol']}"].append(x)
        groups[f"year={year}"].append(x)
        groups["side=bull" if sg > 0 else "side=bear"].append(x)
    out["groups_mean_R"] = {
        k: {"n": len(v), "mean": round(sum(v) / len(v), 6)} for k, v in sorted(groups.items())
    }
    return out


def _log(msg: str) -> None:
    sys.stderr.write(msg + "\n")
    sys.stderr.flush()


def main(argv: list[str]) -> int:
    modes = {"build": build, "power": power, "verdict": verdict}
    if not argv or argv[0] not in modes:
        raise SystemExit("usage: audit_div101_15m.py build | power | verdict")
    result = modes[argv[0]]()
    sys.stdout.write(json.dumps(result, indent=1) + "\n")  # ASCII (cp949 콘솔)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
