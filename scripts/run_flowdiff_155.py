"""문서 155 — IDEAS 16: 현물·선물 공격적 체결 흐름 차이가 다음 24 시간 선물 수익을 알려 주는가.

사용 예::

    python -m scripts.run_flowdiff_155 audit   # 자료 해시 · 표본 수 · 수익 분산(평균 안 봄)
    python -m scripts.run_flowdiff_155 run     # 깨끗한 트리 · 해시 일치 · 판정 파일 없음 — 단계별

* 자료: 바이낸스 공개 4 시간봉(현물 · USD-M) BTCUSDT · ETHUSDT 2020-01 ~ 2026-08.
* 흐름: 시장마다 순공격 거래대금 = 2 × taker 매수 거래대금 − 전체 거래대금, 지난 30 일(180 봉,
  그 봉 제외) 평균 거래대금으로 나눈 z. 신호 S = z(현물) − z(선물).
* 관찰: 날마다 00:00 UTC 에 끝나는 봉 하나(겹치지 않게), 다음 24 시간(6 봉) 선물 종가 수익.
* 규칙: TRAIN 분포의 5 분위 경계로 위 20% · 아래 20% 날에 24 시간 롱/숏(방향은 TRAIN 에서 정함),
  왕복 0.19%(선물 0.095% × 2). 단계: TRAIN 비용 뒤 > 0 → 검정력 ≥ 80% → VALIDATION
  (평균 > 0 · 2SE 하한 > 0 · 선물 흐름만 쓴 같은 규칙보다 큼) → TEST 1 회.
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import statistics
import sys
import zipfile
from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from scripts.run_a1_train import git_state
from scripts.run_kq_154 import dirty_paths, foreign_changes

REPO = Path(__file__).resolve().parent.parent
VISION = REPO / "data" / "external" / "binance_vision"
OUTPUT = REPO / "reports" / "flowdiff_155"
EXPECTED_DATA: str | None = "f2918698cb0e2483c1abf1bcd5739ee421eac6c33c1524969fd5de8baafb2ff2"
"""자료 묶음 해시 — audit 뒤 문서 155 에 적고 여기에 고정한다."""
SYMBOLS = ("BTCUSDT", "ETHUSDT")
NORM_BARS, HOLD = 180, 6
COST, COST_REF = 0.0019, 0.00046
TRAIN = ("2020-07-01", "2022-12-31")
VALID = ("2023-01-01", "2024-12-31")
TEST = ("2025-01-01", "2026-08-31")
Z80, Z_ALPHA = 0.8416, 2.0


def _ms(x: str) -> int:
    v = int(x)
    return v // 1000 if v > 10**14 else v  # 2025 년부터 현물 파일은 마이크로초


def load_bars(market: str, symbol: str) -> dict[int, tuple[float, float, float]]:
    """{봉 시작 ms: (종가, 거래대금, taker 매수 거래대금)}."""
    out: dict[int, tuple[float, float, float]] = {}
    for p in sorted((VISION / market / symbol / "4h").glob("*.zip")):
        with zipfile.ZipFile(p) as zf:
            text = zf.read(zf.namelist()[0]).decode("utf-8")
        for line in io.StringIO(text):
            a = line.strip().split(",")
            if not a[0].isdigit():
                continue  # 머리글
            out[_ms(a[0])] = (float(a[4]), float(a[7]), float(a[10]))
    return out


def flow_z(bars: dict[int, tuple[float, float, float]]) -> dict[int, float]:
    """순공격 거래대금 ÷ 지난 180 봉(그 봉 제외) 평균 거래대금 (순수)."""
    keys = sorted(bars)
    out: dict[int, float] = {}
    acc: list[float] = []
    for k in keys:
        _c, qv, tb = bars[k]
        if len(acc) >= NORM_BARS:
            avg = sum(acc[-NORM_BARS:]) / NORM_BARS
            if avg > 0:
                out[k] = (2 * tb - qv) / avg
        acc.append(qv)
    return out


def observations(symbol: str) -> list[dict[str, float | str]]:
    """날마다 00:00 UTC 에 끝나는 봉(20:00 시작) 의 신호와 다음 24 시간 선물 수익."""
    spot, perp = load_bars("spot", symbol), load_bars("um", symbol)
    zs, zp = flow_z(spot), flow_z(perp)
    four_h = 4 * 3600 * 1000
    out: list[dict[str, float | str]] = []
    for k in sorted(perp):
        dt = datetime.fromtimestamp(k / 1000, UTC)
        if dt.hour != 20 or k not in zs or k not in zp:
            continue
        end = k + HOLD * four_h
        if end not in perp:
            continue
        out.append(
            {
                "day": (datetime.fromtimestamp((k + four_h) / 1000, UTC)).strftime("%Y-%m-%d"),
                "symbol": symbol,
                "s": zs[k] - zp[k],
                "zp": zp[k],
                "ret": perp[end][0] / perp[k][0] - 1,
            }
        )
    return out


def quintile_cuts(xs: Sequence[float]) -> tuple[float, float]:
    """20 · 80 번째 백분위 (순수)."""
    v = sorted(xs)
    return v[int(0.2 * (len(v) - 1))], v[int(0.8 * (len(v) - 1))]


def trades(
    obs: Sequence[dict[str, float | str]],
    key: str,
    cuts: tuple[float, float],
    sign: int,
    cost: float,
) -> list[tuple[str, float]]:
    """위 20% 는 sign 방향, 아래 20% 는 반대 방향으로 24 시간 — (날, 비용 뒤 수익)."""
    lo, hi = cuts
    out = []
    for o in obs:
        x, r = float(o[key]), float(o["ret"])
        if x >= hi:
            out.append((str(o["day"]), sign * r - cost))
        elif x <= lo:
            out.append((str(o["day"]), -sign * r - cost))
    return out


def clustered(xs: Sequence[tuple[str, float]]) -> dict[str, float]:
    """평균 · 날로 묶은 표준오차 · 2SE 하한."""
    if len(xs) < 2:
        return {"n": len(xs), "mean": 0.0, "se": 0.0, "lo2": 0.0}
    vals = [v for _, v in xs]
    mean = statistics.fmean(vals)
    groups: dict[str, float] = defaultdict(float)
    for d, v in xs:
        groups[d] += v - mean
    g = len(groups)
    var = sum(x * x for x in groups.values()) / (len(vals) ** 2) * (g / (g - 1) if g > 1 else 1)
    se = math.sqrt(var)
    return {
        "n": len(vals),
        "mean": round(mean, 6),
        "se": round(se, 6),
        "lo2": round(mean - 2 * se, 6),
    }


def _span(obs: Sequence[dict[str, float | str]], lo: str, hi: str) -> list[dict[str, float | str]]:
    return [o for o in obs if lo <= str(o["day"]) <= hi]


def data_files() -> list[Path]:
    return sorted(
        p for m in ("spot", "um") for s in SYMBOLS for p in (VISION / m / s / "4h").glob("*.zip")
    )


def data_hash() -> tuple[str, int]:
    h = hashlib.sha256()
    files = data_files()
    for p in files:
        h.update(p.name.encode())
        h.update(p.read_bytes())
    return h.hexdigest(), len(files)


def _all() -> list[dict[str, float | str]]:
    return [o for s in SYMBOLS for o in observations(s)]


def audit() -> dict[str, object]:
    """표본 수와 24 시간 수익의 표준편차만 (평균·신호 성과 안 봄)."""
    digest, n = data_hash()
    obs = _all()
    out: dict[str, object] = {"data_sha256": digest, "files": n}
    for name, (lo, hi) in (("train", TRAIN), ("valid", VALID), ("test", TEST)):
        seg = _span(obs, lo, hi)
        rets = [float(o["ret"]) for o in seg]
        out[name] = {
            "obs": len(seg),
            "days": len({o["day"] for o in seg}),
            "ret_sd": round(statistics.stdev(rets), 4) if len(rets) > 1 else 0.0,
            "expected_trades": round(0.4 * len(seg)),
        }
    return out


def run() -> dict[str, object]:
    target = OUTPUT / "verdict.json"
    if target.exists():
        raise SystemExit(f"이미 판정했다: {target}")
    sha, _dirty = git_state()
    others = foreign_changes()
    if others:  # Codex 가 작업 중인 IDEAS.md 만 허용 (run_kq_154.ALLOWED_DIRTY)
        raise SystemExit(f"작업 트리가 깨끗하지 않다 — 커밋 뒤에 실행: {others}")
    digest, _ = data_hash()
    if EXPECTED_DATA is None or digest != EXPECTED_DATA:
        raise SystemExit(f"자료 해시가 다르다: {digest}")
    obs = _all()
    tr = _span(obs, *TRAIN)
    cuts = quintile_cuts([float(o["s"]) for o in tr])
    cuts_p = quintile_cuts([float(o["zp"]) for o in tr])
    gross = trades(tr, "s", cuts, 1, 0.0)
    sign = 1 if statistics.fmean(v for _, v in gross) > 0 else -1
    train = clustered(trades(tr, "s", cuts, sign, COST))
    result: dict[str, object] = {
        "commit": sha,
        "data_sha256": digest,
        "uncommitted_allowed": dirty_paths(),
        "cuts": cuts,
        "sign": sign,
        "train": train,
        "train_gross_mean": round(statistics.fmean(sign * v for _, v in gross), 6),
    }
    stage = "TRAIN"
    verdict = "REJECT"
    if train["mean"] > 0:
        stage = "POWER"
        va = _span(obs, *VALID)
        # BTC·ETH 같은 날은 함께 움직여 독립이 아니다 → 서로 다른 날 수로 센다
        n_valid = len({d for d, _ in trades(va, "s", cuts, sign, COST)})
        sd = statistics.stdev([float(o["ret"]) for o in tr])
        power_n = ((Z_ALPHA + Z80) * sd / train["mean"]) ** 2
        result["power"] = {"needed_days": round(power_n), "valid_trade_days": n_valid}
        if n_valid >= power_n:
            stage = "VALIDATION"
            valid = clustered(trades(va, "s", cuts, sign, COST))
            control = clustered(trades(va, "zp", cuts_p, sign, COST))
            result["valid"] = valid
            result["valid_perp_only"] = control
            result["valid_ref_cost"] = clustered(trades(va, "s", cuts, sign, COST_REF))
            if valid["mean"] > 0 and valid["lo2"] > 0 and valid["mean"] > control["mean"]:
                stage = "TEST"
                test = clustered(trades(_span(obs, *TEST), "s", cuts, sign, COST))
                result["test"] = test
                if test["mean"] > 0 and test["lo2"] > 0:
                    verdict = "PASS"
        else:
            verdict = "DATA_INSUFFICIENT"
    result["stopped_at"] = stage
    result["verdict"] = verdict
    OUTPUT.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else "audit"
    if cmd == "audit":
        print(json.dumps(audit(), ensure_ascii=False, indent=2))
    elif cmd == "run":
        print(json.dumps(run(), ensure_ascii=False, indent=2))
    else:
        raise SystemExit(f"알 수 없는 명령: {cmd}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
