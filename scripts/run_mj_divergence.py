"""문서 140 — MJ-B(자금 관리) · MJ-C(매물대 겹침): RSI 다이버전스 1시간봉, BTC·ETH. 1회 판정.

사용 예::

    python -m scripts.run_mj_divergence count     # 수익 없이 신호·거래 수만 (사건 수 관문)
    python -m scripts.run_mj_divergence verdict   # 깨끗한 트리 · 해시 · 고정한 수 일치일 때만

자료는 GR(137)과 같은 Binance 현물 1분봉 zip 218 개. 기준은 문서 140 §5 그대로다.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
import statistics
import sys
import zipfile
from array import array
from collections.abc import Sequence
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scripts.run_a1_train import git_state
from scripts.run_grid_upbit import EXPECTED_BUNDLE, VISION, bundle_hash

from auto_trading.backtest.ema_box import Bars, aggregate
from auto_trading.backtest.mj_divergence import (
    M0,
    M1,
    Management,
    SignalConfig,
    Trade,
    TradeRun,
    boxes,
    confluent,
    signals,
    simulate,
)

REPO = Path(__file__).resolve().parent.parent
OUTPUT = REPO / "reports" / "mj_divergence"
DB = REPO / "data" / "trading.db"
SYMBOLS = ("BTCUSDT", "ETHUSDT")
HOUR = 3_600_000
FEE = 0.00095
RISK = 0.05
_MICROS = 10**14

# 사건 수 관문(count) 결과 — 판정은 이 값과 같을 때만 돈다 (문서 140 §6).
EXPECTED_COUNTS: dict[str, int] | None = {
    "C_busy": 1,
    "C_missed": 0,
    "C_skipped": 2,
    "C_trades": 178,
    "M0_busy": 14,
    "M0_missed": 1,
    "M0_skipped": 4,
    "M0_trades": 745,
    "M1_busy": 52,
    "M1_missed": 1,
    "M1_skipped": 4,
    "M1_trades": 707,
    "conf_signals_BTCUSDT": 99,
    "conf_signals_ETHUSDT": 82,
    "signals_BTCUSDT": 373,
    "signals_ETHUSDT": 391,
}
EXPECTED_SIGNALS: str | None = "5ee9c5cdc5b78a5e95a9890d0f335f4cf81b3b7c4fee5228474a3fc2ba1d9dc3"


def _ms(text: str) -> int:
    return int(datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=UTC).timestamp() * 1000)


def _date(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m-%d")


FROM, TO = _ms("2018-01-01"), _ms("2026-09-01") - HOUR
SPLIT = _ms("2022-01-01")
CFG = SignalConfig()
# C v2 (문서 140 §10): 후보 {2.0, 2.5, 3.0} 중 C 거래 ≥ 150 인 가장 작은 값 — 수만 보고 골랐다.
CFG_C = replace(CFG, box_atr=2.0)


def load_hourly(symbol: str) -> Bars:
    t, o, h, lo, c, v = (
        array("q"),
        array("d"),
        array("d"),
        array("d"),
        array("d"),
        array("d"),
    )
    for path in sorted((VISION / symbol / "1m").glob(f"{symbol}-1m-*.zip")):
        with zipfile.ZipFile(path) as archive:
            (name,) = archive.namelist()
            for line in archive.read(name).decode("ascii").splitlines():
                cells = line.split(",")
                if not cells[0].isdigit():
                    continue
                ms = int(cells[0])
                if ms > _MICROS:
                    ms //= 1000
                if t and ms <= t[-1]:
                    continue
                t.append(ms)
                o.append(float(cells[1]))
                h.append(float(cells[2]))
                lo.append(float(cells[3]))
                c.append(float(cells[4]))
                v.append(float(cells[5]))
    return aggregate(t, o, h, lo, c, v, HOUR)


# ------------------------------------------------------------------ 실행 단위


def build(bars: dict[str, Bars]) -> dict[str, dict[str, Any]]:
    """종목마다 전체 신호, 겹침 신호."""
    out: dict[str, dict[str, Any]] = {}
    for s, b in bars.items():
        sigs = [x for x in signals(b, CFG) if FROM <= b.t[x.i] <= TO]
        z = boxes(b, CFG_C)
        out[s] = {"all": sigs, "conf": [x for x in sigs if confluent(x, z, CFG_C)]}
    return out


def signal_digest(sigs: dict[str, dict[str, Any]], bars: dict[str, Bars]) -> str:
    rows = [
        [s, bars[s].t[x.i], x.side, bars[s].t[x.pivot], bars[s].t[x.prev], x.stop, kind]
        for s in SYMBOLS
        for kind in ("all", "conf")
        for x in sigs[s][kind]
    ]
    return hashlib.sha256(json.dumps(rows, separators=(",", ":")).encode()).hexdigest()


def runs(
    bars: dict[str, Bars],
    sigs: dict[str, dict[str, Any]],
    *,
    fee: float = FEE,
    liq_mmr: float | None = None,
) -> dict[str, dict[str, TradeRun]]:
    arms: dict[str, tuple[str, Management]] = {
        "M0": ("all", M0),
        "M1": ("all", M1),
        "C": ("conf", M1),
    }
    out: dict[str, dict[str, TradeRun]] = {}
    for arm, (kind, mgmt) in arms.items():
        out[arm] = {
            s: simulate(
                bars[s], sigs[s][kind], mgmt, from_ms=FROM, to_ms=TO, fee=fee, liq_mmr=liq_mmr
            )
            for s in SYMBOLS
        }
    return out


def counts(r: dict[str, dict[str, TradeRun]], sigs: dict[str, dict[str, Any]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for s in SYMBOLS:
        out[f"signals_{s}"] = len(sigs[s]["all"])
        out[f"conf_signals_{s}"] = len(sigs[s]["conf"])
    for arm, per in r.items():
        out[f"{arm}_trades"] = sum(len(x.trades) for x in per.values())
        out[f"{arm}_missed"] = sum(x.missed for x in per.values())
        out[f"{arm}_skipped"] = sum(x.skipped for x in per.values())
        out[f"{arm}_busy"] = sum(x.busy for x in per.values())
    return out


# ------------------------------------------------------------------ 통계


def mean_t(rs: Sequence[float]) -> dict[str, Any]:
    if len(rs) < 2:
        return {"n": len(rs), "mean_r": statistics.fmean(rs) if rs else None, "t": None}
    m, sd = statistics.fmean(rs), statistics.stdev(rs)
    return {"n": len(rs), "mean_r": m, "t": m / (sd / math.sqrt(len(rs))) if sd > 0 else None}


def streak(rs: Sequence[float]) -> int:
    best = cur = 0
    for x in rs:
        cur = cur + 1 if x <= 0 else 0
        best = max(best, cur)
    return best


def describe(trades: Sequence[tuple[str, Trade]], bars: dict[str, Bars]) -> dict[str, Any]:
    rs = [tr.r for _, tr in trades]
    wins = [x for x in rs if x > 0]
    losses = [x for x in rs if x <= 0]
    hours = [bars[s].t[tr.exit_i] - bars[s].t[tr.entry_i] for s, tr in trades]
    kinds: dict[str, int] = {}
    for _, tr in trades:
        kinds[tr.exit_kind] = kinds.get(tr.exit_kind, 0) + 1
    return {
        **mean_t(rs),
        "win_rate": len(wins) / len(rs) if rs else None,
        "profit_factor": sum(wins) / -sum(losses) if losses and sum(losses) < 0 else None,
        "losing_streak": streak(rs),
        "avg_hold_h": statistics.fmean(hours) / HOUR if hours else None,
        "exit_kinds": kinds,
    }


def ordered(per: dict[str, TradeRun], bars: dict[str, Bars]) -> list[tuple[str, Trade]]:
    rows = [(s, tr) for s, run in per.items() for tr in run.trades]
    rows.sort(key=lambda x: (bars[x[0]].t[x[1].exit_i], x[0]))
    return rows


def account(trades: Sequence[tuple[str, Trade]], bars: dict[str, Bars]) -> dict[str, Any]:
    """계산기 방식: 진입 때 실현 자본의 5% 위험, 청산 때 R × 위험 금액을 더한다(초기 100)."""
    events: list[tuple[int, int, int]] = []
    for k, (s, tr) in enumerate(trades):
        events.append((bars[s].t[tr.entry_i], 1, k))
        events.append((bars[s].t[tr.exit_i], 0, k))
    events.sort()
    equity, peak, mdd = 100.0, 100.0, 0.0
    risk: dict[int, float] = {}
    worst_from = worst_to = ""
    peak_at = ""
    year_end: dict[str, float] = {}
    for ms, kind, k in events:
        if kind == 1:
            risk[k] = RISK * equity
            continue
        equity += trades[k][1].r * risk.pop(k)
        if equity >= peak:
            peak, peak_at = equity, _date(ms)
        elif 1 - equity / peak > mdd:
            mdd, worst_from, worst_to = 1 - equity / peak, peak_at, _date(ms)
        year_end[_date(ms)[:4]] = equity
    yearly, prev = {}, 100.0
    for y in sorted(year_end):
        yearly[y] = year_end[y] / prev - 1
        prev = year_end[y]
    return {"final": equity, "mdd": mdd, "mdd_window": [worst_from, worst_to], "yearly": yearly}


def funding_series() -> dict[str, list[tuple[int, float]]]:
    with sqlite3.connect(DB) as conn:
        return {
            s: [
                (int(ms), float(rate))
                for ms, rate in conn.execute(
                    "SELECT funding_ms, rate FROM funding_rates WHERE exchange = 'binance_usdm'"
                    " AND symbol = ? ORDER BY funding_ms",
                    (s,),
                )
            ]
            for s in SYMBOLS
        }


def with_funding(
    trades: Sequence[tuple[str, Trade]],
    bars: dict[str, Bars],
    fund: dict[str, list[tuple[int, float]]],
) -> dict[str, Any]:
    """funding 자료가 있는 거래만: 보유 중 정산 합 × 처음 물량(분할 익절 무시 — 비용 쪽으로 큼)."""
    plain: list[float] = []
    paid: list[float] = []
    for s, tr in trades:
        series = fund[s]
        start, stop = bars[s].t[tr.entry_i], bars[s].t[tr.exit_i]
        if not series or start < series[0][0]:
            continue
        rate_sum = sum(rate for ms, rate in series if start < ms <= stop)
        cost = tr.side * rate_sum * tr.entry / abs(tr.entry - tr.stop)
        plain.append(tr.r)
        paid.append(tr.r - cost)
    return {"covered": mean_t(plain), "after_funding": mean_t(paid)}


# ------------------------------------------------------------------ 단계


def _load() -> tuple[dict[str, Bars], dict[str, dict[str, Any]]]:
    bars = {s: load_hourly(s) for s in SYMBOLS}
    return bars, build(bars)


def count(output: Path) -> int:
    digest, n = bundle_hash()
    if digest != EXPECTED_BUNDLE:
        print(f"1분봉 묶음 해시가 문서 137 §3 과 다르다 ({n}개). 세지 않는다.")
        return 3
    bars, sigs = _load()
    r = runs(bars, sigs)
    c = counts(r, sigs)
    record = {"bundle_sha256": digest, "signal_sha256": signal_digest(sigs, bars), "counts": c}
    path = output / "count.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(record, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    for k, v in c.items():
        print(f"  {k}: {v}")
    print(f"  signal_sha256: {record['signal_sha256']}")
    gate_b = c["M1_trades"] >= 200
    gate_c = c["C_trades"] >= 100
    print(f"관문: B {'통과' if gate_b else '미달'} · C {'통과' if gate_c else '미달'}")
    print(f"기록: {path}")
    return 0


def verdict(output: Path) -> int:
    path = output / "verdict.json"
    if path.exists():
        print(f"이미 판정이 있다: {path}. 사전등록상 1회만 실행한다.")
        return 3
    if EXPECTED_COUNTS is None or EXPECTED_SIGNALS is None:
        print("사건 수 관문 결과가 아직 고정되지 않았다 (문서 140 §6).")
        return 3
    sha, dirty = git_state()
    if dirty:
        print("작업 트리에 커밋되지 않은 변경이 있다. 재현 가능한 코드로만 실행한다.")
        return 3
    digest, n = bundle_hash()
    if digest != EXPECTED_BUNDLE:
        print(f"1분봉 묶음 해시가 문서 137 §3 과 다르다 ({n}개). 판정하지 않는다.")
        return 3
    bars, sigs = _load()
    r = runs(bars, sigs)
    if counts(r, sigs) != EXPECTED_COUNTS or signal_digest(sigs, bars) != EXPECTED_SIGNALS:
        print("센 수 또는 신호 해시가 고정 값과 다르다. 판정하지 않는다.")
        return 3

    def split(rows: list[tuple[str, Trade]]) -> list[dict[str, Any]]:
        first = [x for x in rows if bars[x[0]].t[x[1].entry_i] < SPLIT]
        second = [x for x in rows if bars[x[0]].t[x[1].entry_i] >= SPLIT]
        return [mean_t([t.r for _, t in first]), mean_t([t.r for _, t in second])]

    arms = {arm: ordered(per, bars) for arm, per in r.items()}
    full = {arm: mean_t([t.r for _, t in rows]) for arm, rows in arms.items()}
    halves = {arm: split(rows) for arm, rows in arms.items()}
    b_ok = EXPECTED_COUNTS["M1_trades"] >= 200
    c_ok = EXPECTED_COUNTS["C_trades"] >= 100

    def passes(arm: str) -> tuple[bool, bool]:
        f = full[arm]
        first = f["mean_r"] is not None and f["mean_r"] > 0 and (f["t"] or 0) >= 2.0
        second = all(h["mean_r"] is not None and h["mean_r"] > 0 for h in halves[arm])
        return first, second

    b1, b2 = passes("M1")
    c1, c2 = passes("C")
    criteria = {
        "B1": b1,
        "B2": b2,
        "B3": full["M1"]["mean_r"] > full["M0"]["mean_r"],
        "C1": c1,
        "C2": c2,
        "C3": full["C"]["mean_r"] > full["M1"]["mean_r"],
    }
    status_b = "PASS" if b_ok and all(criteria[k] for k in ("B1", "B2", "B3")) else "REJECT"
    status_c = "PASS" if c_ok and all(criteria[k] for k in ("C1", "C2", "C3")) else "REJECT"
    if not b_ok:
        status_b = "NO_VERDICT"
    if not c_ok:
        status_c = "NO_VERDICT"

    detail: dict[str, Any] = {}
    for arm, rows in arms.items():
        detail[arm] = {
            "all": describe(rows, bars),
            "long": describe([x for x in rows if x[1].side > 0], bars),
            "short": describe([x for x in rows if x[1].side < 0], bars),
            "by_symbol": {s: describe([x for x in rows if x[0] == s], bars) for s in SYMBOLS},
            "account_5pct": account(rows, bars),
        }
    refs: dict[str, Any] = {}
    alts = {
        "fee_0.05": runs(bars, sigs, fee=0.0005),
        "isolated_liq": runs(bars, sigs, liq_mmr=0.005),
    }
    for label, alt in alts.items():
        refs[label] = {
            arm: {
                **mean_t([t.r for _, t in ordered(per, bars)]),
                "account_5pct": account(ordered(per, bars), bars),
            }
            for arm, per in alt.items()
        }
    fund = funding_series()
    refs["funding"] = {arm: with_funding(rows, bars, fund) for arm, rows in arms.items()}

    record = {
        "git_sha": sha,
        "executed_at": datetime.now(tz=UTC).isoformat(),
        "bundle_sha256": digest,
        "signal_sha256": EXPECTED_SIGNALS,
        "config": asdict(CFG),
        "config_c": asdict(CFG_C),
        "status": {"MJ-B": status_b, "MJ-C": status_c},
        "criteria": criteria,
        "full": full,
        "halves": halves,
        "detail": detail,
        "reference": refs,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(record, ensure_ascii=False, indent=1, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"MJ-B {status_b} · MJ-C {status_c} · {criteria}")
    for arm in arms:
        f, (h1, h2) = full[arm], halves[arm]
        acc = detail[arm]["account_5pct"]
        print(
            f"  {arm}: n {f['n']} · 평균 R {f['mean_r']:+.3f} (t {f['t']:.2f})"
            f" · 반쪽 {h1['mean_r']:+.3f} / {h2['mean_r']:+.3f}"
            f" · 승률 {detail[arm]['all']['win_rate']:.1%}"
            f" · 5% 복리 100→{acc['final']:.1f} MDD {acc['mdd']:.1%}"
        )
    print(f"기록: {path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("count", "verdict"))
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    return count(args.output) if args.stage == "count" else verdict(args.output)


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            _reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
