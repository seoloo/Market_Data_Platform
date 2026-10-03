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
from statistics import median, pstdev

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


def main(argv: list[str]) -> int:
    if argv == ["build"]:
        result = build()
    elif argv == ["measure"]:
        result = measure()
    else:
        raise SystemExit("usage: audit_cand108_simple_signals.py build | measure")
    sys.stdout.write(json.dumps(result, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
