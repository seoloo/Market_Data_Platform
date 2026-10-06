"""IDEAS 19 (비용 고려 DMN) Phase 0 감사 — 자료 · 모델 용량 · 검정력. 학습·후보 성과 없음.

사용 예::

    python -m scripts.audit_dmn_phase0 --out reports/dmn_phase0/audit.json

* 자료: 거래 DB(읽기 전용)의 BTC·ETH 현물·선물 봉 · 펀딩 기간, 공개 자료 파일(1 분봉 · 마크 일봉)
  의 달 수와 해시.
* 모델 용량: 초안 §4 의 LSTM(입력 4 · hidden 16 · 출력 1, 두 종목 공유)의 학습 파라미터 수와
  walk-forward 학습 창(12 개월) 안의 독립에 가까운 관찰 수.
* 검정력: **후보를 학습하지 않는다.** 기준선 둘(SIMPLE-LS = T 규칙 롱숏 · TV2-REF = T/v2 롱만,
  ``backtest.trend`` 그대로)의 월 수익과 그 차이로 장기 표준편차(Newey–West lag 3)를 어림하고,
  초안 §9 의 식으로 필요 월수·최소 검출 효과를 계산한다. 두 기준선의 2018 ~ 2026-08 성과는 T 연구
  (문서 16 · 86)에서 이미 본 것이라 새 노출은 없다. 후보 차이의 실측 분산이 아니다(초안 §9).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
import statistics
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scripts.run_t_trend import FROM_MS, INITIAL_EQUITY, TO_MS, load

from auto_trading.backtest.trend import TrendConfig, simulate_trend

REPO = Path(__file__).resolve().parent.parent
DB = REPO / "data" / "trading.db"
VISION = REPO / "data" / "external" / "binance_vision"
TARGET_MONTHLY = 0.0025
"""초안 §9 의 검정력 계획용 검출 대상(월 0.25%p = 연 산술 3%p)."""
Z_ALPHA, Z80, Z_EACH = 2.0, 0.8416, 1.501
HIDDEN, INPUTS, OUTPUTS = 16, 4, 1
TRAIN_MONTHS = 12


def lstm_params(inputs: int, hidden: int, outputs: int) -> int:
    """LSTM 1 층(게이트 4 개, 입력·순환 가중치 + 편향 2 벌 — PyTorch 기본) + 선형 출력 (순수)."""
    lstm = 4 * (hidden * inputs + hidden * hidden + 2 * hidden)
    return lstm + hidden * outputs + outputs


def newey_west_sd(xs: Sequence[float], lag: int) -> float:
    """장기 표준편차 — Bartlett 가중 자기공분산 (순수)."""
    n = len(xs)
    m = statistics.fmean(xs)
    d = [x - m for x in xs]
    var = sum(v * v for v in d) / n
    for k in range(1, lag + 1):
        cov = sum(d[i] * d[i - k] for i in range(k, n)) / n
        var += 2 * (1 - k / (lag + 1)) * cov
    return math.sqrt(max(var, 0.0))


def required_months(sd: float, target: float, z_beta: float) -> int:
    return math.ceil(((Z_ALPHA + z_beta) * sd / target) ** 2)


def mde(sd: float, n: int, z_beta: float) -> float:
    return (Z_ALPHA + z_beta) * sd / math.sqrt(n)


def monthly(days: Sequence[int], equity: Sequence[float]) -> dict[str, float]:
    last: dict[str, float] = {}
    for d, e in zip(days, equity, strict=True):
        last[datetime.fromtimestamp(d / 1000, UTC).strftime("%Y-%m")] = e
    keys = sorted(last)
    out = {}
    prev = INITIAL_EQUITY
    for k in keys:
        out[k] = last[k] / prev - 1
        prev = last[k]
    return out


def _d(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m-%d")


def data_inventory() -> dict[str, Any]:
    out: dict[str, Any] = {"db": {}, "files": {}}
    with sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True) as con:
        for ex, sym, tf, n, lo, hi in con.execute(
            "SELECT exchange, symbol, timeframe, count(*), min(open_ms), max(open_ms) FROM candles"
            " WHERE symbol IN ('BTCUSDT','ETHUSDT') AND timeframe IN ('1d','1h','1m')"
            " GROUP BY exchange, symbol, timeframe"
        ):
            out["db"][f"candles {ex} {sym} {tf}"] = {"rows": n, "from": _d(lo), "to": _d(hi)}
        for ex, sym, n, lo, hi, nomark in con.execute(
            "SELECT exchange, symbol, count(*), min(funding_ms), max(funding_ms),"
            " sum(mark_price IS NULL) FROM funding_rates WHERE symbol IN ('BTCUSDT','ETHUSDT')"
            " GROUP BY exchange, symbol"
        ):
            out["db"][f"funding {ex} {sym}"] = {
                "rows": n,
                "from": _d(lo),
                "to": _d(hi),
                "mark_price_missing": nomark,
            }
        specs = con.execute(
            "SELECT count(*) FROM instruments WHERE symbol IN ('BTCUSDT','ETHUSDT')"
        ).fetchone()[0]
        fees = con.execute("SELECT count(*) FROM fee_schedule").fetchone()[0]
        out["db"]["instrument_specs_rows"] = specs
        out["db"]["fee_schedule_rows"] = fees
    for rel in ("spot/BTCUSDT/1m", "spot/ETHUSDT/1m", "um/ETHUSDT/1m", "um_mark/BTCUSDT/1d",
                "um_mark/ETHUSDT/1d"):  # fmt: skip
        files = sorted((VISION / rel).glob("*.zip"))
        h = hashlib.sha256()
        for p in files:
            h.update(p.name.encode())
            h.update(p.read_bytes())
        out["files"][rel] = {
            "months": len(files),
            "first": files[0].stem[-7:] if files else None,
            "last": files[-1].stem[-7:] if files else None,
            "sha256": h.hexdigest()[:16],
        }
    return out


def power() -> dict[str, Any]:
    bars, funding = load()
    kw: dict[str, Any] = {"from_ms": FROM_MS, "to_ms": TO_MS, "initial_equity": INITIAL_EQUITY}
    ls = simulate_trend(bars, funding, cfg=TrendConfig(), **kw)
    lo = simulate_trend(bars, funding, cfg=TrendConfig(long_only=True), **kw)
    m_ls, m_lo = monthly(ls.days, ls.equity), monthly(lo.days, lo.equity)
    keys = sorted(set(m_ls) & set(m_lo))
    r_ls = [m_ls[k] for k in keys]
    r_lo = [m_lo[k] for k in keys]
    diff = [a - b for a, b in zip(r_ls, r_lo, strict=True)]
    out: dict[str, Any] = {"months": len(keys), "from": keys[0], "to": keys[-1], "series": {}}
    for name, xs in (("SIMPLE-LS r", r_ls), ("TV2-REF r", r_lo), ("SIMPLE-LS − TV2-REF", diff)):
        sd3 = newey_west_sd(xs, 3)
        out["series"][name] = {
            "sd_plain": round(statistics.stdev(xs), 4),
            "sd_lr_lag3": round(sd3, 4),
            "sd_lr_lag6_audit": round(newey_west_sd(xs, 6), 4),
            "needed_months_80pct": required_months(sd3, TARGET_MONTHLY, Z80),
            "needed_months_each_93pct": required_months(sd3, TARGET_MONTHLY, Z_EACH),
            "mde_monthly_80pct_at_60m": round(mde(sd3, 60, Z80), 4),
            "mde_annual_arith_80pct_at_60m": round(12 * mde(sd3, 60, Z80), 3),
            "mde_monthly_80pct_at_96m": round(mde(sd3, 96, Z80), 4),
        }
    return out


def capacity() -> dict[str, Any]:
    params = lstm_params(INPUTS, HIDDEN, OUTPUTS)
    train_days = TRAIN_MONTHS * 30
    return {
        "lstm_trainable_params": params,
        "train_window_days_per_symbol": train_days,
        "train_samples_two_symbols": 2 * train_days,
        "samples_per_param": round(2 * train_days / params, 2),
        "note": "120 일 시퀀스는 거의 겹치고 BTC·ETH 상관이 높아 독립 정보는 표본보다 훨씬 적다",
        "ml_libraries_installed": _ml_libs(),
    }


def _ml_libs() -> dict[str, bool]:
    import importlib.util

    return {m: importlib.util.find_spec(m) is not None for m in ("torch", "numpy", "tensorflow")}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    result = {"data": data_inventory(), "capacity": capacity(), "power": power()}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    sys.stdout.write(json.dumps(result, ensure_ascii=False, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            _reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
