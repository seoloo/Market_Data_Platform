"""문서 112 — AG: 소액 공격형 1,000 USD (TQQQ 추세·코인 3 배·반반), 최대 손실 −70%, 1~10 년.

사용 예::

    python -m scripts.run_ag_small run   # 계산 한 번 (작업 트리 깨끗 · 결과 파일 없음)

자료: ``tradfi/QQQ_adj.csv``·``DTB3.csv`` (110) · ``data/trading.db`` (82·O 와 같음).
"""

from __future__ import annotations

import json
import statistics
import sys
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from scripts.run_a1_train import git_state
from scripts.run_o_overlay import load as load_crypto
from scripts.run_qs_switch import (
    QLD_FEE_YEAR,
    TQQQ_FEE_YEAR,
    _inputs,
    leveraged,
    trend_signals,
)

from auto_trading.backtest.overlay import OverlayConfig, simulate_overlay

REPO = Path(__file__).resolve().parent.parent
OUTPUT = REPO / "reports" / "ag_small"
START_CASH = 1_000.0
STOP_LEVEL = 300.0
FEE = 0.0025
TAX, EXEMPT_USD = 0.22, 1_786.0
US_FROM, CR_FROM, TO = "2008-06-01", "2020-03-01", "2026-08-31"
HORIZONS = range(1, 11)
MIN_STARTS = 12
CR3 = OverlayConfig(exposure=3.0, catastrophe_stop=0.20, halt_drawdown=1.0)
CR2 = OverlayConfig(exposure=2.0, catastrophe_stop=0.20, halt_drawdown=1.0)
CR1 = OverlayConfig(exposure=1.0, daily_loss_limit=float("inf"), halt_drawdown=1.0)
DAY = 86_400_000

Path_ = dict[str, float]
"""날짜(YYYY-MM-DD) → 평가액."""


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m-%d")


def _ms(day: str) -> int:
    return int(datetime.fromisoformat(day).replace(tzinfo=UTC).timestamp() * 1000)


# ------------------------------------------------------------------ 미국 (TQQQ 추세)


def us_path(
    days: Sequence[str],
    lev: Mapping[str, float],
    signal: Mapping[str, bool | None],
    start: str,
    end: str,
    cash0: float,
    *,
    tax: bool = True,
) -> Path_:
    """켜짐이면 전액 레버리지 ETF, 꺼짐이면 현금. 전날 신호를 오늘 종가에. 공제 넘는 이익 22%."""
    span = [d for d in days if start <= d <= end]
    index = {d: i for i, d in enumerate(days)}
    cash, shares, basis, year_gain = cash0, 0.0, 0.0, 0.0
    out: Path_ = {}
    for n, d in enumerate(span):
        i = index[d]
        on = bool(signal.get(days[i - 1])) if i > 0 else False
        if on and shares == 0 and cash > 0:
            shares = cash * (1 - FEE) / lev[d]
            basis, cash = cash, 0.0
        elif not on and shares > 0:
            proceeds = shares * lev[d] * (1 - FEE)
            year_gain += proceeds - basis
            cash += proceeds
            shares = basis = 0.0
        if tax and (n == len(span) - 1 or span[n + 1][:4] != d[:4]):
            if year_gain > EXEMPT_USD:
                cash -= TAX * (year_gain - EXEMPT_USD)
            year_gain = 0.0
        out[d] = cash + shares * lev[d]
    return out


# ------------------------------------------------------------------ 코인 (T/v2 × 배율)


def crypto_path(data: tuple, cfg: OverlayConfig, start: str, end: str, cash0: float) -> Path_:  # type: ignore[type-arg]
    spot, perp, funding = data
    res = simulate_overlay(
        spot, perp, funding, from_ms=_ms(start), to_ms=_ms(end), initial_equity=cash0, cfg=cfg
    )
    return {_iso(d): e for d, e in zip(res.days, res.equity, strict=True)}


# ------------------------------------------------------------------ 공통


def apply_stop(path: Path_, base: float = START_CASH) -> tuple[Path_, str | None]:
    """평가액이 처음 돈의 30% 이하가 된 날 정리하고 그 값으로 멈춘다."""
    out: Path_ = {}
    stopped: str | None = None
    frozen = 0.0
    for d in sorted(path):
        if stopped is None and path[d] <= STOP_LEVEL * base / START_CASH:
            stopped, frozen = d, path[d]
        out[d] = frozen if stopped else path[d]
    return out, stopped


def _at(path: Path_, day: str) -> float:
    keys = [d for d in sorted(path) if d <= day]
    return path[keys[-1]]


def _add_years(day: str, k: int) -> str:
    y, m, d = (int(x) for x in day.split("-"))
    return date(y + k, m, min(d, 28)).isoformat()


def month_starts(days: Sequence[str], first: str) -> list[str]:
    seen: set[str] = set()
    out = []
    for d in days:
        if d >= first and d[:7] not in seen and d <= TO:
            seen.add(d[:7])
            out.append(d)
    return out


def table(paths: Mapping[str, tuple[Path_, str | None]], last_day: str) -> dict[str, object]:
    """시작일별 경로 → 기간 k 마다 k 년 뒤 평가액 분포·−70% 비율."""
    out: dict[str, object] = {}
    for k in HORIZONS:
        finals, stops = [], 0
        for start, (path, stopped) in paths.items():
            end = _add_years(start, k)
            if end > last_day:
                continue
            finals.append(_at(path, end))
            stops += stopped is not None and stopped <= end
        if not finals:
            out[f"{k}y"] = None
            continue
        finals.sort()
        q = statistics.quantiles(finals, n=10) if len(finals) >= 10 else None
        out[f"{k}y"] = {
            "starts": len(finals),
            "min": round(finals[0], 1),
            "p10": round(q[0], 1) if q else None,
            "median": round(statistics.median(finals), 1),
            "p90": round(q[-1], 1) if q else None,
            "max": round(finals[-1], 1),
            "stop_share": round(stops / len(finals), 4),
        }
    return out


def first_path_yearly(
    path: Path_, stopped: str | None, start: str, last_day: str
) -> dict[str, object]:
    out: dict[str, object] = {"start": start, "stopped": stopped}
    for k in HORIZONS:
        end = _add_years(start, k)
        if end > last_day:
            break
        out[f"{k}y"] = round(_at(path, end), 1)
    peak, mdd = 0.0, 0.0
    for d in sorted(path):
        peak = max(peak, path[d])
        mdd = max(mdd, 1 - path[d] / peak) if peak > 0 else mdd
    out["max_drawdown_from_peak"] = round(mdd, 4)
    return out


def adopt(tab: Mapping[str, object]) -> dict[str, object]:
    """§4: 시작일 ≥ 12 인 가장 긴 k 에서 중앙값 ≥ 10^(k/10) 배 그리고 −70% 비율 ≤ 20%."""
    best = None
    for k in HORIZONS:
        row = tab.get(f"{k}y")
        if isinstance(row, dict) and row["starts"] >= MIN_STARTS:
            best = (k, row)
    if best is None:
        return {"k": None, "pass": False}
    k, row = best
    need = START_CASH * 10 ** (k / 10)
    ok = row["median"] >= need and row["stop_share"] <= 0.20
    return {
        "k": k,
        "median": row["median"],
        "need": round(need, 1),
        "stop_share": row["stop_share"],
        "pass": ok,
    }


def mix_path(
    us_seg: Callable[[str, str, float], Path_],
    cr_seg: Callable[[str, str, float], Path_],
    start: str,
    last_day: str,
) -> Path_:
    """500·500 으로 시작, 해마다 기념일에 50:50 으로 다시 맞춘다 (구간마다 각자 새로 시작)."""
    out: Path_ = {}
    a = b = START_CASH / 2
    seg_start = start
    while seg_start < last_day:
        seg_end = min(_add_years(seg_start, 1), last_day)
        pa = us_seg(seg_start, seg_end, a)
        pb = cr_seg(seg_start, seg_end, b)
        for d in sorted(pb):
            out[d] = _at(pa, d) if pa and d >= min(pa) else a
            out[d] += pb[d]
        total = _at(pa, seg_end) + _at(pb, seg_end)
        a = b = total / 2
        if total <= 0:
            break
        seg_start = (date.fromisoformat(seg_end) + timedelta(days=1)).isoformat()
    return out


def run() -> dict[str, object]:
    target = OUTPUT / "result.json"
    if target.exists():
        raise SystemExit(f"이미 계산했다: {target}")
    sha, dirty = git_state()
    if dirty:
        raise SystemExit("작업 트리가 깨끗하지 않다 — 커밋 뒤에 실행")
    days, qqq, rf = _inputs()
    sig = trend_signals(days, qqq)
    tqqq = leveraged(days, qqq, rf, 3.0, TQQQ_FEE_YEAR)
    qld = leveraged(days, qqq, rf, 2.0, QLD_FEE_YEAR)
    us_last = max(d for d in days if d <= TO)
    crypto = load_crypto()
    cr_days = sorted(_iso(ms) for ms in crypto[0]["BTCUSDT"])
    cr_last = max(d for d in cr_days if d <= TO)

    def us_run(
        lev: Mapping[str, float], starts: Sequence[str]
    ) -> dict[str, tuple[Path_, str | None]]:
        return {s: apply_stop(us_path(days, lev, sig, s, us_last, START_CASH)) for s in starts}

    def cr_run(cfg: OverlayConfig, starts: Sequence[str]) -> dict[str, tuple[Path_, str | None]]:
        return {s: apply_stop(crypto_path(crypto, cfg, s, cr_last, START_CASH)) for s in starts}

    us_starts = month_starts(days, US_FROM)
    cr_starts = list(month_starts(cr_days, CR_FROM))
    results: dict[str, dict[str, tuple[Path_, str | None]]] = {
        "AG-US": us_run(tqqq, us_starts),
        "AG-CR": cr_run(CR3, cr_starts),
    }
    mixed: dict[str, tuple[Path_, str | None]] = {}
    for s in cr_starts:
        p = mix_path(
            lambda a, b, c: us_path(days, tqqq, sig, a, b, c),
            lambda a, b, c: crypto_path(crypto, CR3, a, b, c),
            s,
            min(cr_last, us_last),
        )
        mixed[s] = apply_stop(p)
    results["AG-MIX"] = mixed
    refs = {
        "AG-CR_2x": cr_run(CR2, cr_starts),
        "AG-CR_1x_T2": cr_run(CR1, cr_starts),
        "AG-US_QLD_2x": us_run(qld, us_starts),
        "QQQ_hold": {
            s: apply_stop(
                {d: START_CASH * qqq[d] / qqq[s] * (1 - FEE) for d in days if s <= d <= us_last}
            )
            for s in us_starts
        },
    }
    last = {"AG-US": us_last, "AG-CR": cr_last, "AG-MIX": min(cr_last, us_last)}
    out: dict[str, object] = {"git": sha, "start_cash": START_CASH, "stop_level": STOP_LEVEL}
    for name, paths in results.items():
        tab = table(paths, last[name])
        first = min(paths)
        out[name] = {
            "table": tab,
            "first_start_path": first_path_yearly(*paths[first], first, last[name]),
            "adopt": adopt(tab),
        }
    out["references"] = {}
    for name, paths in refs.items():
        last_day = cr_last if name.startswith("AG-CR") else us_last
        first = min(paths)
        out["references"][name] = {  # type: ignore[index]
            "table": table(paths, last_day),
            "first_start_path": first_path_yearly(*paths[first], first, last_day),
        }
    OUTPUT.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def main(argv: list[str]) -> int:
    if argv != ["run"]:
        raise SystemExit("usage: python -m scripts.run_ag_small run")
    sys.stdout.write(json.dumps(run(), indent=1) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
