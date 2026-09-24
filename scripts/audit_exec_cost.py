"""문서 42 체결 비용 — ``depth``(공개 호가로 시장가 불리폭 h, §4) / ``run``(정책별 IS, §5, 1회).

``depth`` 는 인증 없는 공개 REST 만 쓴다. ``run`` 은 DB·내려받은 1m 을 읽기만 하고,
결과를 ``reports/exec_cost/run.json`` 에 한 번만 쓴다(있으면 거부).
"""

from __future__ import annotations

import json
import math
import random
import sys
import time
import urllib.request
import zipfile
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from statistics import median
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from scripts.audit_ew_edge_power import _btc_bars, _btc_minutes, _eth_minutes  # noqa: E402
from scripts.run_a1_train import git_state  # noqa: E402
from scripts.run_o_overlay import FROM_MS, INITIAL, TO_MS, load  # noqa: E402

import auto_trading.backtest.overlay as ov  # noqa: E402
from auto_trading.core.types import Candle  # noqa: E402

DEPTH_URLS = {
    "spot": "https://api.binance.com/api/v3/depth?symbol={s}&limit=1000",
    "perp": "https://fapi.binance.com/fapi/v1/depth?symbol={s}&limit=1000",
}
SYMBOLS = ("BTCUSDT", "ETHUSDT")
NOTIONALS = (1_000, 10_000, 100_000)
SNAPSHOTS = 6
SNAPSHOT_GAP_S = 10
H_FLOOR_BPS = 1.0


def _walk(levels: list[list[str]], notional: float) -> float:
    """호가를 따라 ``notional`` USDT 만큼 시장가 체결했을 때 평균 체결가."""
    left, qty = notional, 0.0
    for price_s, size_s in levels:
        price, size = float(price_s), float(size_s)
        take = min(left, price * size)
        qty += take / price
        left -= take
        if left <= 1e-9:
            return notional / qty
    raise ValueError("호가 깊이가 부족하다")


def _snapshot(leg: str, symbol: str) -> dict[str, object]:
    with urllib.request.urlopen(DEPTH_URLS[leg].format(s=symbol), timeout=10) as resp:
        book = json.load(resp)
    bid, ask = float(book["bids"][0][0]), float(book["asks"][0][0])
    mid = (bid + ask) / 2
    out: dict[str, object] = {"half_spread_bps": (ask - bid) / 2 / mid * 1e4}
    for n in NOTIONALS:
        buy = (_walk(book["asks"], n) / mid - 1) * 1e4
        sell = (1 - _walk(book["bids"], n) / mid) * 1e4
        out[str(n)] = (buy + sell) / 2
    return out


def depth() -> dict[str, object]:
    samples: dict[str, list[dict[str, object]]] = {}
    for i in range(SNAPSHOTS):
        for leg in DEPTH_URLS:
            for symbol in SYMBOLS:
                samples.setdefault(f"{leg}:{symbol}", []).append(_snapshot(leg, symbol))
        if i < SNAPSHOTS - 1:
            time.sleep(SNAPSHOT_GAP_S)
    table = {
        key: {
            field: round(median(float(s[field]) for s in rows), 4)  # type: ignore[arg-type]
            for field in ("half_spread_bps", *map(str, NOTIONALS))
        }
        for key, rows in samples.items()
    }
    h = {
        leg: max(H_FLOOR_BPS, median(table[f"{leg}:{s}"]["10000"] for s in SYMBOLS))
        for leg in DEPTH_URLS
    }
    return {
        "measured_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "median_bps_vs_mid": table,
        "h_bps": h,
        "note": "문서 42 §4. h = 명목 10,000 중앙값(두 종목), 1bp 미만이면 1bp.",
    }


# ------------------------------------------------------------------ run (§5)

DAY = 86_400_000
MINUTE = 60_000
H_BPS = 1.0
"""§4a 측정 결과 — 현물·무기한 모두 하한 1bp."""
FEES = {"spot": (10.0, 10.0), "perp": (2.0, 4.5)}
"""(maker, taker) bp. 현물 VIP0, 무기한은 config/base.yaml."""
POLICIES = ("M", "L10", "L60", "L240", "R10", "R60", "R240")
WINDOW = 241
"""00:00 분부터 240 분 + 추격 분."""
BOOT = 10_000
SEED = 20260925
TAIL_LIMIT_BPS = 50.0
SPOT_DIR = REPO_ROOT / "data" / "external" / "binance_vision" / "spot" / "BTCUSDT" / "1m"
PROXY_TO_MS = int(datetime(2022, 1, 1, tzinfo=UTC).timestamp() * 1000)
OUTPUT = REPO_ROOT / "reports" / "exec_cost" / "run.json"
Bar = tuple[int, float, float, float, float]  # open_ms, open, high, low, volume


@dataclass(frozen=True, slots=True)
class Trade:
    sim: str
    t0: int
    symbol: str
    leg: str
    side: int
    notional: float
    equity: float


def _trades() -> list[Trade]:
    spot, perp, funding = load()
    trades: list[Trade] = []
    original = ov.step_overlay_day
    for name, cfg in (("O2", ov.O2_STOP), ("T2", ov.T2_ALONE)):

        def capture(
            state: ov.OverlayState,
            day: int,
            market: ov.OverlayMarketDay,
            cfg: ov.OverlayConfig,
            tally: ov.OverlayResult,
            name: str = name,
        ) -> ov.OverlayDayOutcome:
            out = original(state, day, market, cfg, tally)
            for f in out.fills:
                if f["reason"] != "rebalance":
                    continue
                s = str(f["symbol"])
                for leg, before, after, px in (
                    ("spot", f["spot_before"], f["spot_after"], market.spot[s].close),
                    ("perp", f["fut_before"], f["fut_after"], market.perp[s].close),
                ):
                    delta = float(after) - float(before)
                    if abs(delta) * px < 1e-6:
                        continue
                    side = 1 if delta > 0 else -1
                    trades.append(Trade(name, day + DAY, s, leg, side, abs(delta) * px, out.equity))
            return out

        ov.step_overlay_day = capture
        try:
            ov.simulate_overlay(
                spot,
                perp,
                funding,
                from_ms=FROM_MS,
                to_ms=TO_MS,
                initial_equity=INITIAL,
                cfg=cfg,
            )
        finally:
            ov.step_overlay_day = original
    return trades


def _spot_btc_minutes() -> Iterator[Candle]:
    from decimal import Decimal

    from auto_trading.core.enums import Timeframe

    for path in sorted(SPOT_DIR.glob("BTCUSDT-1m-*.zip")):
        with zipfile.ZipFile(path) as archive:
            (name,) = archive.namelist()
            for line in archive.read(name).decode("ascii").splitlines():
                cells = line.split(",")
                if not cells[0].isdigit():
                    continue
                open_ms = int(cells[0])
                if open_ms > 10**14:  # 2025~ 파일은 마이크로초
                    open_ms //= 1000
                yield Candle(
                    open_ms=open_ms,
                    close_ms=open_ms + Timeframe.M1.ms - 1,
                    open=Decimal(cells[1]),
                    high=Decimal(cells[2]),
                    low=Decimal(cells[3]),
                    close=Decimal(cells[4]),
                    volume=Decimal(cells[5]),
                )


def _windows(minutes: Iterable[Candle], days: set[int]) -> dict[int, list[Bar]]:
    out: dict[int, list[Bar]] = {}
    for c in minutes:
        t0 = c.open_ms - c.open_ms % DAY
        if t0 in days and c.open_ms - t0 < WINDOW * MINUTE:
            out.setdefault(t0, []).append(
                (c.open_ms, float(c.open), float(c.high), float(c.low), float(c.volume))
            )
    return out


def _execute(
    policy: str, side: int, t0: int, bars: list[Bar], h: float, maker: float, taker: float
) -> tuple[float, bool, int]:
    """(IS bp, maker 체결 여부, 대기 분)."""
    p0 = bars[0][1]
    if policy == "M":
        return h + taker, False, 0
    kind, limit = policy[0], int(policy[1:])
    end = t0 + limit * MINUTE
    for ms, o, hi, lo, vol in bars:
        if ms >= end:
            px = o * (1 + side * h / 1e4)
            return side * (px - p0) / p0 * 1e4 + taker, False, limit
        if vol <= 0:
            continue
        ref = p0 if kind == "L" else o
        if (lo < ref) if side > 0 else (hi > ref):
            return side * (ref - p0) / p0 * 1e4 + maker, True, (ms - t0) // MINUTE + 1
    raise ValueError("창이 짧다")


def _usable(bars: list[Bar] | None, t0: int) -> bool:
    return bool(bars) and bars[0][0] == t0 and bars[-1][0] >= t0 + 240 * MINUTE  # type: ignore[index]


Row = tuple[list[float], list[bool], list[int]]
"""거래 하나의 정책별 (IS bp, maker 체결, 대기 분)."""


def _rows(
    trades: list[Trade], paths: dict[tuple[str, int], list[Bar]], h: float, fees: dict[str, Any]
) -> list[Row]:
    out: list[Row] = []
    for tr in trades:
        maker, taker = fees[tr.leg]
        bars = paths[(tr.symbol, tr.t0)]
        cells = [_execute(p, tr.side, tr.t0, bars, h, maker, taker) for p in POLICIES]
        out.append(([c[0] for c in cells], [c[1] for c in cells], [c[2] for c in cells]))
    return out


def _wmean(xs: Sequence[float], ws: Sequence[float]) -> float:
    return sum(x * w for x, w in zip(xs, ws, strict=True)) / sum(ws)


def _pct(xs: Sequence[float], q: float) -> float:
    """선형 보간 퍼센타일 (numpy 기본과 같다)."""
    s = sorted(xs)
    pos = (len(s) - 1) * q / 100
    lo = math.floor(pos)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def _holm(pvals: dict[str, float], alpha: float = 0.05) -> dict[str, bool]:
    order = sorted(pvals, key=pvals.__getitem__)
    out = dict.fromkeys(pvals, False)
    for k, name in enumerate(order):
        if pvals[name] > alpha / (len(order) - k):
            break
        out[name] = True
    return out


def _date(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m-%d")


def _judge_leg(trades: list[Trade], rows: list[Row], rng: random.Random) -> dict[str, Any]:
    w = [t.notional for t in trades]
    days = sorted({t.t0 for t in trades})
    index = {d: k for k, d in enumerate(days)}
    sw = [0.0] * len(days)
    swis = [[0.0] * len(days) for _ in POLICIES]
    for t, (is_, _, _) in zip(trades, rows, strict=True):
        k = index[t.t0]
        sw[k] += t.notional
        for j in range(len(POLICIES)):
            swis[j][k] += t.notional * is_[j]
    draws = [[rng.randrange(len(days)) for _ in days] for _ in range(BOOT)]
    denom = [sum(map(sw.__getitem__, d)) for d in draws]
    boot = [
        [sum(map(col.__getitem__, d)) / n for d, n in zip(draws, denom, strict=True)]
        for col in swis
    ]
    means = {p: _wmean([r[0][j] for r in rows], w) for j, p in enumerate(POLICIES)}
    out: dict[str, Any] = {}
    pvals: dict[str, float] = {}
    for j, pol in enumerate(POLICIES[1:], start=1):
        d_boot = [a - m for a, m in zip(boot[j], boot[0], strict=True)]
        excess = [r[0][j] - r[0][0] for r in rows]
        pvals[pol] = (sum(1 for d in d_boot if d >= 0) + 1) / (BOOT + 1)
        worst = sorted(range(len(rows)), key=excess.__getitem__, reverse=True)[:10]
        out[pol] = {
            "mean_is_bps": round(means[pol], 3),
            "D_bps": round(means[pol] - means["M"], 3),
            "ci95_bps": [round(_pct(d_boot, q), 3) for q in (2.5, 97.5)],
            "p_one_sided": round(pvals[pol], 5),
            "excess_p99_bps": round(_pct(excess, 99), 2),
            "excess_max_bps": round(max(excess), 2),
            "worst10": [
                {
                    "date": _date(trades[k].t0),
                    "sim": trades[k].sim,
                    "symbol": trades[k].symbol,
                    "excess_bps": round(excess[k], 1),
                    "equity_loss_pct": round(
                        trades[k].notional * excess[k] / 1e4 / trades[k].equity * 100, 4
                    ),
                }
                for k in worst
            ],
        }
    holm = _holm(pvals)
    for p in POLICIES[1:]:
        out[p]["holm_significant"] = holm[p]
    eligible = [p for p in POLICIES[1:] if holm[p] and out[p]["excess_p99_bps"] <= TAIL_LIMIT_BPS]
    return {
        "n_trades": len(trades),
        "n_days": len(days),
        "mean_is_M_bps": round(means["M"], 3),
        "policies": out,
        "adopted": min(eligible, key=means.__getitem__) if eligible else None,
    }


def _report_leg(trades: list[Trade], rows: list[Row]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    total = sum(t.notional for t in trades)
    pairs = list(zip(trades, rows, strict=True))
    for j, pol in enumerate(POLICIES[1:], start=1):
        filled = [(t.notional, r[0][j], r[2][j]) for t, r in pairs if r[1][j]]
        chased = [(t.notional, r[0][j]) for t, r in pairs if not r[1][j]]
        out[pol] = {
            "maker_fill_rate_notional": round(sum(f[0] for f in filled) / total, 4),
            "mean_is_filled_bps": (
                round(_wmean([f[1] for f in filled], [f[0] for f in filled]), 3) if filled else None
            ),
            "mean_is_chased_bps": (
                round(_wmean([c[1] for c in chased], [c[0] for c in chased]), 3) if chased else None
            ),
            "mean_wait_min_filled": (
                round(sum(f[2] for f in filled) / len(filled), 1) if filled else None
            ),
        }
    return out


def _burden(trades: list[Trade], rates: Sequence[float]) -> float:
    """연간 비용 부담(자본 대비 %): Σ 명목 × 비용률 / 그날 자본 / 연수."""
    years = (TO_MS - FROM_MS + DAY) / DAY / 365.0
    total = sum(t.notional * r / 1e4 / t.equity for t, r in zip(trades, rates, strict=True))
    return round(total / years * 100, 4)


def _by(trades: list[Trade], rows: list[Row], key: str) -> dict[str, dict[str, float]]:
    groups: dict[str, list[int]] = {}
    for i, t in enumerate(trades):
        label = t.symbol if key == "symbol" else _date(t.t0)[:4]
        groups.setdefault(label, []).append(i)
    return {
        label: {
            p: round(_wmean([rows[i][0][j] for i in ix], [trades[i].notional for i in ix]), 2)
            for j, p in enumerate(POLICIES)
        }
        for label, ix in sorted(groups.items())
    }


def _means(trades: list[Trade], rows: list[Row]) -> dict[str, float]:
    w = [t.notional for t in trades]
    return {p: round(_wmean([r[0][j] for r in rows], w), 3) for j, p in enumerate(POLICIES)}


def run() -> dict[str, object]:
    if OUTPUT.exists():
        raise SystemExit(f"이미 결과가 있다: {OUTPUT}. 문서 42 §5 는 1회만 실행한다.")
    sha, dirty = git_state()
    if dirty:
        raise SystemExit("작업 트리에 커밋되지 않은 변경이 있다. 재현 가능한 코드로만 실행한다.")
    trades = _trades()
    days = {t.t0 for t in trades}
    _, _, to_ms = _btc_bars()
    perp_paths = {
        "BTCUSDT": _windows(_btc_minutes(to_ms), days),
        "ETHUSDT": _windows(_eth_minutes(), days),
    }
    paths = {(s, d): bars for s, by_day in perp_paths.items() for d, bars in by_day.items()}
    kept = [t for t in trades if _usable(paths.get((t.symbol, t.t0)), t.t0)]
    rng = random.Random(SEED)
    base = _rows(kept, paths, H_BPS, FEES)
    scenarios = {
        "perp_taker_5bp": (H_BPS, {**FEES, "perp": (2.0, 5.0)}),
        "h_0_5bp": (0.5, FEES),
        "h_5bp": (5.0, FEES),
        "bnb_discount": (H_BPS, {"spot": (7.5, 7.5), "perp": (1.8, 4.05)}),
    }
    scen_rows = {name: _rows(kept, paths, h, fees) for name, (h, fees) in scenarios.items()}
    assumed = {"spot": 15.0, "perp": 9.5}
    legs: dict[str, Any] = {}
    for leg in ("spot", "perp"):
        ix = [i for i, t in enumerate(kept) if t.leg == leg]
        lt = [kept[i] for i in ix]
        rows = [base[i] for i in ix]
        burden: dict[str, Any] = {}
        for sim in ("O2", "T2"):
            sx = [k for k, t in enumerate(lt) if t.sim == sim]
            if not sx:
                continue
            st = [lt[k] for k in sx]
            burden[sim] = {
                "assumed_current": _burden(st, [assumed[leg]] * len(st)),
                **{p: _burden(st, [rows[k][0][j] for k in sx]) for j, p in enumerate(POLICIES)},
            }
        legs[leg] = {
            **_judge_leg(lt, rows, rng),
            "fills": _report_leg(lt, rows),
            "annual_cost_burden_pct_of_equity": burden,
            "sensitivity_mean_is_bps": {
                name: _means(lt, [r[i] for i in ix]) for name, r in scen_rows.items()
            },
            "by_symbol": _by(lt, rows, "symbol"),
            "by_year": _by(lt, rows, "year"),
        }
    # §2 대리 검증: 2020-03~2021-12 BTC 현물 다리, 현물 1m vs 무기한 1m
    proxy = [t for t in kept if t.leg == "spot" and t.symbol == "BTCUSDT" and t.t0 < PROXY_TO_MS]
    spot_paths = _windows(_spot_btc_minutes(), {t.t0 for t in proxy})
    proxy = [t for t in proxy if _usable(spot_paths.get(t.t0), t.t0)]
    spot_keyed = {("BTCUSDT", d): b for d, b in spot_paths.items()}
    result: dict[str, Any] = {
        "git_sha": sha,
        "executed_at": datetime.now(tz=UTC).isoformat(),
        "h_bps": H_BPS,
        "fees_bps_maker_taker": FEES,
        "n_trades_total": len(trades),
        "n_dropped_missing_minutes": len(trades) - len(kept),
        "legs": legs,
        "proxy_check_btc_spot_2020_2021": {
            "n": len(proxy),
            "mean_is_on_spot_bps": _means(proxy, _rows(proxy, spot_keyed, H_BPS, FEES)),
            "mean_is_on_perp_bps": _means(proxy, _rows(proxy, paths, H_BPS, FEES)),
        },
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(result, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return result


def main(argv: list[str]) -> int:
    modes = {"depth": depth, "run": run}
    mode = argv[0] if argv else "depth"
    if mode not in modes:
        raise SystemExit("usage: audit_exec_cost.py [depth|run]")
    sys.stdout.write(json.dumps(modes[mode](), ensure_ascii=False, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
