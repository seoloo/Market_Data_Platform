"""문서 108 — 엘리엇 밖 단순 신호 후보 비교 (사건 수 · SD · MDE 만, 수익률 평균은 계산하지 않음).

사용::

    python scripts/audit_cand108_simple_signals.py build     # 1m zip → 거래량 포함 1h 캐시
    python scripts/audit_cand108_simple_signals.py measure   # 후보별 n · SD · MDE

후보 정의는 문서 108 §2 (측정 전 고정).
"""

from __future__ import annotations

import bisect
import csv
import hashlib
import json
import math
import sys
import zipfile
from collections import Counter
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path
from statistics import NormalDist, median, pstdev

REPO_ROOT = Path(__file__).resolve().parent.parent
for _p in (REPO_ROOT, REPO_ROOT / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from scripts.audit_div101_15m import SYMBOLS, VISION  # noqa: E402

CACHE = REPO_ROOT / "data" / "external" / "derived" / "1h_vol"
OUT = REPO_ROOT / "reports" / "research" / "candidates" / "108"
HOUR = 3_600_000
WINDOW = 720
VOL_MULT = 5.0
MOVE_SD = 2.0
HORIZON_V = 24
_MICROS = 10**14

Bar = tuple[int, float, float, float, float, float]  # open_ms, o, h, l, c, quote volume

CACHE_SHA256: dict[str, str] = {
    "BTCUSDT": "541bb8aedd83b125e4cc9bca409bdfb06ff89f05edb79678ef798af1287d5d1c",
    "ETHUSDT": "c61bfe42c126e9416595ea8fc46bfa30a4e044401bb69942a803400bbbd61430",
    "SOLUSDT": "91d1ba2996d4af3192c1812cdeb1f0396bf0fc64c18ec449dc4471245c2c3620",
    "XRPUSDT": "33f0ec9b48a6369f043deb039e2041866d757124bb6d3eb61c5b14e306360f4d",
    "BNBUSDT": "fc63026cf6dc2d3334eb2a982582e9a0c09e4ce9d23f0a6c1b538a5ccf38a530",
    "ADAUSDT": "db27845cdc1605d1ef3aaa20a8c3e15d03d2742cb6a8461e6c4e15ccf8c7d892",
    "DOGEUSDT": "00fe3ee20a94d121bd50e529191fbcbba7068d5ccb3f93a79df6b1829518e513",
    "LTCUSDT": "32762e2725cadb71c856dd9baf8e5495c9cd6f43572654769e45af4296da227a",
    "LINKUSDT": "566b260dba0416b437e8eac60de4b3ebe3f3257d20411702a8201dc280b0c663",
    "TRXUSDT": "d1a8665a2477b8a1d2379e62de38a5b3adaf17047e2ed535b0fcd594fdbb8338",
}
"""문서 108 §3: 측정 때의 1h 캐시 SHA-256 (판정 때 대조)."""
EXPECTED_V: tuple[int, int] = (1251, 1866)
"""문서 108 §4: H-V 독립 (사건, 대조) — 판정 때 그대로 나와야 한다."""
ALPHA_V = 0.05
COST = 0.0019
REAL_PERP = 0.00023
VERDICT_V = OUT / "verdict_V.json"


def build() -> dict[str, object]:
    CACHE.mkdir(parents=True, exist_ok=True)
    out: dict[str, object] = {}
    for symbol in SYMBOLS:
        rows: list[list[float]] = []
        cur_start = -1
        for path in sorted((VISION / symbol / "1m").glob(f"{symbol}-1m-*.zip")):
            with zipfile.ZipFile(path) as archive:
                (name,) = archive.namelist()
                for line in archive.read(name).decode("ascii").splitlines():
                    cells = line.split(",")
                    if not cells[0].isdigit():
                        continue
                    t = int(cells[0])
                    if t > _MICROS:
                        t //= 1000
                    start = t // HOUR * HOUR
                    o, h, lo, c, qv = (float(cells[i]) for i in (1, 2, 3, 4, 7))
                    if start != cur_start:
                        rows.append([start, o, h, lo, c, qv, 1])
                        cur_start = start
                    else:
                        r = rows[-1]
                        r[2], r[3], r[4] = max(r[2], h), min(r[3], lo), c
                        r[5] += qv
                        r[6] += 1
        target = CACHE / f"{symbol}.csv"
        with target.open("w", newline="", encoding="ascii") as fh:
            w = csv.writer(fh, lineterminator="\n")
            w.writerow(("open_ms", "open", "high", "low", "close", "quote_volume", "minutes"))
            w.writerows((int(r[0]), *(repr(x) for x in r[1:6]), int(r[6])) for r in rows)
        out[symbol] = {"bars": len(rows), "sha256": hashlib.sha256(target.read_bytes()).hexdigest()}
        sys.stderr.write(f"build {symbol} {len(rows)}\n")
        sys.stderr.flush()
    return out


def load(symbol: str) -> list[list[Bar]]:
    """60 분이 다 찬 시간만, 빠진 시간에서 구간을 끊는다."""
    segs: list[list[Bar]] = []
    with (CACHE / f"{symbol}.csv").open(encoding="ascii") as fh:
        reader = csv.reader(fh)
        next(reader)
        for r in reader:
            if int(r[6]) != 60:
                segs.append([])
                continue
            bar: Bar = (int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5]))
            if segs and segs[-1] and segs[-1][-1][0] == bar[0] - HOUR:
                segs[-1].append(bar)
            else:
                segs.append([bar])
    return [s for s in segs if len(s) > WINDOW + HORIZON_V]


def _thin(times: list[int], horizon: int) -> int:
    kept, last = 0, -(10**18)
    for t in sorted(times):
        if t - last >= horizon * HOUR:
            kept += 1
            last = t
    return kept


def measure() -> dict[str, object]:
    v_events: list[int] = []
    v_controls: list[int] = []
    fwd24: list[float] = []
    weekday_ret: list[float] = []
    weekend_ret: list[float] = []
    per_symbol: Counter[str] = Counter()
    for symbol in SYMBOLS:
        for seg in load(symbol):
            closes = [b[4] for b in seg]
            rets = [0.0] + [math.log(closes[t] / closes[t - 1]) for t in range(1, len(seg))]
            # 직전 WINDOW 봉의 수익 SD(t 제외)와 거래량 중앙값(t 제외)을 굴려 가며 계산
            s1 = sum(rets[1:WINDOW])
            s2 = sum(x * x for x in rets[1:WINDOW])
            window_vol = sorted(b[5] for b in seg[0:WINDOW])
            for t in range(WINDOW, len(seg) - HORIZON_V):
                if t > WINDOW:
                    old, new = rets[t - WINDOW], rets[t - 1]
                    s1 += new - old
                    s2 += new * new - old * old
                    window_vol.pop(bisect.bisect_left(window_vol, seg[t - WINDOW - 1][5]))
                    bisect.insort(window_vol, seg[t - 1][5])
                fwd24.append(math.log(closes[t + HORIZON_V] / closes[t]))
                n = WINDOW - 1
                var = max(0.0, (s2 - s1 * s1 / n) / n)
                sd = math.sqrt(var)
                if sd <= 0 or abs(rets[t]) < MOVE_SD * sd:
                    continue
                if seg[t][5] >= VOL_MULT * median(window_vol):
                    v_events.append(seg[t][0])
                    per_symbol[symbol] += 1
                else:
                    v_controls.append(seg[t][0])
            # S: 하루(UTC) 수익 — 하루 24 봉이 다 있는 날만
            by_day: dict[int, list[float]] = {}
            for b in seg:
                by_day.setdefault(b[0] // 86_400_000, []).append(b[4])
            days = sorted(by_day)
            for d0, d1 in pairwise(days):
                if d1 != d0 + 1 or len(by_day[d1]) != 24:
                    continue
                r = math.log(by_day[d1][-1] / by_day[d0][-1])
                wd = datetime.fromtimestamp(d1 * 86_400, UTC).weekday()
                (weekend_ret if wd >= 5 else weekday_ret).append(r)
        sys.stderr.write(f"measure {symbol}\n")
        sys.stderr.flush()
    sd24 = pstdev(fwd24)
    n1, n2 = _thin(v_events, HORIZON_V), _thin(v_controls, HORIZON_V)
    mde_v = 2.84 * sd24 * math.sqrt(1 / n1 + 1 / n2)
    sd_day = pstdev(weekday_ret + weekend_ret)
    # S 는 코인끼리 같은 날이 겹친다 → 독립 표본은 날짜 수로 센다 (보수적)
    n_we = len(weekend_ret) // len(SYMBOLS)
    n_wd = len(weekday_ret) // len(SYMBOLS)
    mde_s = 2.84 * sd_day * math.sqrt(1 / n_we + 1 / n_wd)
    out = {
        "V": {
            "events_raw": len(v_events),
            "events_by_symbol": dict(per_symbol),
            "controls_raw": len(v_controls),
            "n1_independent_24h": n1,
            "n2_independent_24h": n2,
            "sd_24h": round(sd24, 5),
            "mde": round(mde_v, 5),
            "mde_over_cost_0_19pct": round(mde_v / 0.0019, 2),
        },
        "S": {
            "weekend_days_approx": n_we,
            "weekday_days_approx": n_wd,
            "sd_day": round(sd_day, 5),
            "mde": round(mde_s, 5),
        },
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "measure.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    return out


def _tie(close_ms: int, symbol: str) -> str:
    return hashlib.sha256(f"{close_ms}-{symbol}".encode()).hexdigest()


Row = tuple[int, str, int]  # open_ms, symbol, 그 봉 방향 부호


def _thin_rows(rows: list[Row], horizon: int) -> list[Row]:
    kept: list[Row] = []
    last = -(10**18)
    for r in sorted(rows, key=lambda x: (x[0], _tie(x[0], x[1]))):
        if r[0] - last >= horizon * HOUR:
            kept.append(r)
            last = r[0]
    return kept


def _welch(a: list[float], b: list[float]) -> dict[str, float]:
    ma, mb = sum(a) / len(a), sum(b) / len(b)
    va = sum((x - ma) ** 2 for x in a) / (len(a) - 1)
    vb = sum((x - mb) ** 2 for x in b) / (len(b) - 1)
    se = math.sqrt(va / len(a) + vb / len(b))
    z = (ma - mb) / se
    return {
        "n1": len(a),
        "n2": len(b),
        "mean_event": round(ma, 6),
        "mean_control": round(mb, 6),
        "diff": round(ma - mb, 6),
        "t": round(z, 3),
        "p_two_sided": round(2 * (1 - NormalDist().cdf(abs(z))), 5),
    }


def verdict_v() -> dict[str, object]:
    """H-V 한 번 판정 (문서 108 §4): 그 봉 방향 부호의 24 시간 수익, 사건 − 대조, 양쪽 검정."""
    if VERDICT_V.exists():
        raise SystemExit(f"이미 판정했다: {VERDICT_V}")
    for sym in SYMBOLS:
        if hashlib.sha256((CACHE / f"{sym}.csv").read_bytes()).hexdigest() != CACHE_SHA256[sym]:
            raise SystemExit(f"{sym} 1h 캐시가 측정 때와 다르다")
    events: list[Row] = []
    controls: list[Row] = []
    closes: dict[str, dict[int, float]] = {}
    for symbol in SYMBOLS:
        lookup: dict[int, float] = {}
        for seg in load(symbol):
            c = [b[4] for b in seg]
            for b in seg:
                lookup[b[0]] = b[4]
            rets = [0.0] + [math.log(c[t] / c[t - 1]) for t in range(1, len(seg))]
            s1 = sum(rets[1:WINDOW])
            s2 = sum(x * x for x in rets[1:WINDOW])
            window_vol = sorted(b[5] for b in seg[0:WINDOW])
            for t in range(WINDOW, len(seg) - HORIZON_V):
                if t > WINDOW:
                    old, new = rets[t - WINDOW], rets[t - 1]
                    s1 += new - old
                    s2 += new * new - old * old
                    window_vol.pop(bisect.bisect_left(window_vol, seg[t - WINDOW - 1][5]))
                    bisect.insort(window_vol, seg[t - 1][5])
                n = WINDOW - 1
                sd = math.sqrt(max(0.0, (s2 - s1 * s1 / n) / n))
                if sd <= 0 or abs(rets[t]) < MOVE_SD * sd:
                    continue
                row: Row = (seg[t][0], symbol, 1 if rets[t] > 0 else -1)
                if seg[t][5] >= VOL_MULT * median(window_vol):
                    events.append(row)
                else:
                    controls.append(row)
        closes[symbol] = lookup
    ev, co = _thin_rows(events, HORIZON_V), _thin_rows(controls, HORIZON_V)
    if (len(ev), len(co)) != EXPECTED_V:
        raise SystemExit(f"수가 측정과 다르다: {(len(ev), len(co))}")

    def r(row: Row, h: int) -> float | None:
        c = closes[row[1]]
        later = c.get(row[0] + h * HOUR)
        return None if later is None else row[2] * math.log(later / c[row[0]])

    def vals(rows: list[Row], h: int) -> list[float]:
        return [v for v in (r(x, h) for x in rows) if v is not None]

    main = _welch(vals(ev, HORIZON_V), vals(co, HORIZON_V))
    diff, p = main["diff"], main["p_two_sided"]
    groups: dict[str, list[float]] = {}
    for x in ev:
        v = r(x, HORIZON_V)
        if v is None:
            continue
        year = datetime.fromtimestamp(x[0] / 1000, UTC).strftime("%Y")
        for key in (f"symbol={x[1]}", f"year={year}", f"dir={x[2]}"):
            groups.setdefault(key, []).append(v)
    if p < ALPHA_V and diff > COST:
        call = "continuation hypothesis (재현 필요)"
    elif p < ALPHA_V and diff < -COST:
        call = "reversal hypothesis (재현 필요)"
    elif p < ALPHA_V:
        call = "significant but below cost"
    else:
        call = "no evidence"
    record = {
        "H-V": {
            "main_24h": main,
            "call": call,
            "reference_realistic_cost_abs_net": round(abs(diff) - REAL_PERP, 6),
            "descriptive": {
                "h4": _welch(vals(ev, 4), vals(co, 4)),
                "h72": _welch(vals(ev, 72), vals(co, 72)),
                "groups_mean_R": {
                    k: {"n": len(v), "mean": round(sum(v) / len(v), 6)}
                    for k, v in sorted(groups.items())
                },
            },
        },
        "note": "문서 108 §4 1 회 판정. p 는 정규 근사, 양쪽. 보조는 기술 통계.",
    }
    VERDICT_V.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
    return record


def main(argv: list[str]) -> int:
    if argv == ["build"]:
        result = build()
    elif argv == ["measure"]:
        result = measure()
    elif argv == ["verdict-v"]:
        result = verdict_v()
    else:
        raise SystemExit("usage: audit_cand108_simple_signals.py build | measure")
    sys.stdout.write(json.dumps(result, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
