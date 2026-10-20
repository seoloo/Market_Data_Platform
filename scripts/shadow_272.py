"""문서 272 — 앞으로의 그림자 기록 둘 (주문 · 알림 없음, 공개 시세만).

    python -m scripts.shadow_272 run      # 할 차례면 다시 계산 (텔레그램 봇이 4 시간마다 부름)
    python -m scripts.shadow_272 status   # 지금까지 요약

* **H1 숏** (229 · 230 · 239): 17 코인(BTC · ETH + 230 알트 15) 4 시간봉으로 230 엔진
  H1(R5 × T2 × E5 × S3)을 그대로 돌려 **신호가 시작일(2026-10-19) 뒤인 끝난 거래**만 239 지정가
  체결 모형(진입 60 분 안 메이커, 못 걸리면 거래 없음 · 시간 청산 메이커 → 60 분 뒤 테이커 ·
  손절 테이커)으로 기록한다. 매번 처음부터 다시 계산한다(상태가 꼬이지 않게) — 1 분봉 창만 저장.
* **Altair 펀딩비 끄기** (221 · IDEAS 34): 날마다 Altair 롱 목표(T/v2 × 4)와 '최근 7 일 펀딩비
  평균 ≥ 8 시간당 0.03% 면 그 코인은 × 1' 의 차이로 그날 손익 차(장부 대비)를 적는다.
  USDC 계약 가격 · 펀딩.
"""

from __future__ import annotations

import bisect
import json
import math
import statistics
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from scripts.run_alt_short_230 import COINS as ALTS

from auto_trading.research.h1_exec_239 import (
    MAKER,
    TAKER,
    WAIT_MIN,
    entry_filled,
    exit_fill,
    short_return,
)
from auto_trading.research.short_search_229 import H4, TradeCache, build_coin, run_config

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "data" / "shadow_272"
FAPI = "https://fapi.binance.com/fapi/v1"
DAY = 86_400_000
START = int(datetime(2026, 10, 19, tzinfo=UTC).timestamp() * 1000)
HIST = 330 * DAY
"""일봉 지표(ADX · EMA200) 준비용으로 시작일 앞 330 일부터 받는다."""
H1 = ("R5", "T2", "E5", "S3")
H1_COINS = ("BTCUSDT", "ETHUSDT", *ALTS)
ZERO = (0.0, 0.0, 0.0, 0.0)
GATE = 0.0003
"""221: 최근 7 일 펀딩비 평균이 8 시간당 0.03% 이상이면 그 코인 × 1."""
EXPOSURE = 4.0
F_COINS = {"BTC": ("BTCUSDT", "BTCUSDC"), "ETH": ("ETHUSDT", "ETHUSDC")}
"""코인 → (T/v2 신호 현물 심볼, Altair 주문 USDC 계약)."""
EVERY = 4 * 3_600_000
SKIPPED: list[str] = []
"""마지막 실행에서 시세가 끊겨 건너뛴 종목."""


# ------------------------------------------------------------------ 공개 시세


def _get(c: httpx.Client, path: str, params: dict[str, Any]) -> Any:
    for k in range(3):
        r = c.get(f"{FAPI}/{path}", params=params)
        if r.status_code == 200:
            return r.json()
        time.sleep(2 * (k + 1))
    r.raise_for_status()
    return None


def klines(c: httpx.Client, sym: str, interval: str, start: int, end: int) -> list[list[Any]]:
    out: list[list[Any]] = []
    t = start
    while t < end:
        rows = _get(
            c, "klines", {"symbol": sym, "interval": interval, "startTime": t, "limit": 1500}
        )
        if not rows:
            break
        out += rows
        t = int(rows[-1][0]) + 1
        if len(rows) < 1500:
            break
    return out


def funding(c: httpx.Client, sym: str, start: int, end: int) -> list[tuple[int, float]]:
    out: list[tuple[int, float]] = []
    t = start
    while t < end:
        rows = _get(
            c, "fundingRate", {"symbol": sym, "startTime": t, "endTime": end, "limit": 1000}
        )
        if not rows:
            break
        out += [(int(r["fundingTime"]), float(r["fundingRate"])) for r in rows]
        t = int(rows[-1]["fundingTime"]) + 1
        if len(rows) < 1000:
            break
    return out


# ------------------------------------------------------------------ H1 숏


def h1_engine_trades(c: httpx.Client, now_ms: int) -> list[dict[str, Any]]:
    """230 엔진(비용 0)으로 끝난 H1 거래 — 239 ``trades`` 와 같은 되돌림."""
    closed = now_ms - now_ms % H4  # 마감된 4 시간봉까지만
    SKIPPED.clear()
    coins = []
    for s in H1_COINS:
        bars = [
            (int(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5]))
            for k in klines(c, s, "4h", START - HIST, closed)
            if int(k[0]) + H4 <= closed
        ]
        if len(bars) < 300 or bars[-1][0] + H4 < closed - DAY:
            SKIPPED.append(s)  # 선물에서 내려간 종목(예: EOSUSDT 2025-05) — 건너뛰고 적는다
            continue
        coins.append(build_coin(s, bars, funding(c, s, START - HIST, closed)))
    cache = TradeCache(coins)
    cache.cost = ZERO
    by = {k.name: k for k in coins}
    out = []
    for t in run_config(cache, *H1, START, closed):
        k = by[t.coin]
        entry = k.o[t.signal + 1]
        lo = bisect.bisect_left(k.fund_t, t.entry_t)
        hi = bisect.bisect_left(k.fund_t, t.exit_t)
        fund = sum(k.fund_r[lo:hi])
        out.append(
            {
                "coin": t.coin,
                "entry_t": t.entry_t,
                "exit_t": t.exit_t,
                "entry": entry,
                "exit": entry * (1 - (2 * t.ret - fund)),
                "fund": fund,
                "stopped": t.stopped,
            }
        )
    return out


def h1_real(c: httpx.Client, trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    path = DATA / "h1_1m.json"
    win: dict[str, Any] = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    rows = []
    for x in trades:
        for t in (x["entry_t"], *(() if x["stopped"] else (x["exit_t"],))):
            key = f"{x['coin']}:{t}"
            if key not in win or len(win[key]) < WAIT_MIN:
                got = _get(
                    c,
                    "klines",
                    {"symbol": x["coin"], "interval": "1m", "startTime": t, "limit": WAIT_MIN},
                )
                win[key] = [
                    [int(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4])] for k in got
                ]
        ew = [tuple(b) for b in win[f"{x['coin']}:{x['entry_t']}"]]
        if not entry_filled(ew, x["entry"]):
            rows.append({**x, "status": "missed"})
            continue
        if x["stopped"]:
            px, fee_out = x["exit"], TAKER
        else:
            f = exit_fill([tuple(b) for b in win[f"{x['coin']}:{x['exit_t']}"]], x["exit"])
            if f is None:
                rows.append({**x, "status": "no_data"})
                continue
            px, fee_out = f
        rows.append(
            {
                **x,
                "status": "filled",
                "real": short_return(x["entry"], px, x["fund"], MAKER, fee_out),
            }
        )
    DATA.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(win), encoding="utf-8")
    return rows


# ------------------------------------------------------------------ 펀딩비 끄기

SIGNAL = {"BTC": "BTCUSDT", "ETH": "ETHUSDT"}
"""롱 목표를 잴 신호 종목 (은퇴한 scripts/altair_demo.SIGNAL 과 같음)."""


def _long_targets(now_ms: int) -> tuple[dict[str, float], dict[str, dict[str, Any]]]:
    """[쓰임] 마지막으로 끝난 일봉 기준 T/v2 롱 목표 비중과 근거.
    [이유] Altair 은퇴(2026-10-19)로 scripts/altair_demo 를 retired/ 로 옮기면서, 이 그림자 기록이
           쓰던 함수만 여기로 그대로 옮겼다 (계산 같음)."""
    from auto_trading.config.schema import SpotTrendConfig
    from auto_trading.exchange.binance_spot import BinanceSpotReadOnly
    from auto_trading.features.tsmom import realized_vol_annual
    from auto_trading.strategy.spot_trend import spot_trend_targets

    as_of = now_ms - now_ms % DAY - 1
    spot = BinanceSpotReadOnly(None)
    try:
        daily = {SIGNAL[c]: spot.closed_daily(SIGNAL[c], as_of) for c in SIGNAL}
    finally:
        spot.close()
    tg = spot_trend_targets(daily, as_of_ms=as_of, cfg=SpotTrendConfig())
    info: dict[str, dict[str, Any]] = {}
    for c, sym in SIGNAL.items():
        closes = [float(x.close) for x in daily[sym]]
        if len(closes) < 121:
            continue
        vol = float(realized_vol_annual([x.close for x in daily[sym]], 60) or 0)
        info[c] = {
            "parts": [closes[-1] / closes[-1 - k] - 1 for k in (20, 60, 120)],
            "vol": vol,
            "cap": min(0.15 / vol, 0.5) if vol > 0 else 0.0,
        }
    return {c: float(tg[SIGNAL[c]].weight or 0) for c in SIGNAL}, info


def funding_rows(c: httpx.Client, now_ms: int, done: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """끝난 날마다 한 줄. ``done`` 에 이미 있는 날은 다시 계산하지 않는다(그날 값은 바뀌지 않음)."""

    last_day = now_ms - now_ms % DAY - DAY  # 끝난 날까지
    if last_day < START:
        return []
    have = {r["day"] for r in done}
    rows = list(done)
    fund = {k: funding(c, f, START - 8 * DAY, last_day + DAY) for k, (_s, f) in F_COINS.items()}
    daily = {
        k: {
            int(x[0]): (float(x[1]), float(x[4])) for x in klines(c, f, "1d", START, last_day + DAY)
        }
        for k, (_s, f) in F_COINS.items()
    }
    for d in range(START, last_day + 1, DAY):
        if datetime.fromtimestamp(d / 1000, UTC).strftime("%Y-%m-%d") in have:
            continue
        w, _info = _long_targets(d + 5 * 60_000)
        diff = 0.0
        per: dict[str, Any] = {}
        for k in F_COINS:
            fs = fund[k]
            f7 = [r for t, r in fs if d - 7 * DAY <= t < d]
            f_day = sum(r for t, r in fs if d <= t < d + DAY)
            o, cl = daily[k].get(d, (math.nan, math.nan))
            ret = cl / o - 1 if o == o and o > 0 else 0.0
            live = EXPOSURE * w.get(k, 0.0)
            gated = statistics.fmean(f7) >= GATE if f7 else False
            shadow = w.get(k, 0.0) if gated else live
            diff += (shadow - live) * (ret - f_day)
            per[k] = {"w": w.get(k, 0.0), "f7": statistics.fmean(f7) if f7 else None,
                      "gated": gated, "ret": ret, "fund_day": f_day}  # fmt: skip
        rows.append({"day": datetime.fromtimestamp(d / 1000, UTC).strftime("%Y-%m-%d"),
                     "diff": diff, "coins": per})  # fmt: skip
    return rows


# ------------------------------------------------------------------ 실행


def summary(state: dict[str, Any]) -> dict[str, Any]:
    h = [r for r in state.get("h1", []) if r["status"] == "filled"]
    rr = [r["real"] for r in h]
    sd = statistics.stdev(rr) if len(rr) > 1 else 0.0
    f = state.get("funding", [])
    eq = 1.0
    for r in f:
        eq *= 1 + r["diff"]
    return {
        "updated": state.get("updated"),
        "skipped_coins": state.get("skipped_coins", []),
        "h1_filled": len(h),
        "h1_missed": sum(1 for r in state.get("h1", []) if r["status"] == "missed"),
        "h1_mean": statistics.fmean(rr) if rr else None,
        "h1_t": statistics.fmean(rr) / sd * math.sqrt(len(rr)) if sd > 0 else None,
        "funding_days": len(f),
        "funding_gated_coin_days": sum(v["gated"] for r in f for v in r["coins"].values()),
        "funding_cum_diff": eq - 1,
    }


def run(now_ms: int | None = None, *, force: bool = False) -> dict[str, Any] | None:
    now_ms = now_ms or int(time.time() * 1000)
    path = DATA / "state.json"
    state: dict[str, Any] = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    if not force and now_ms - int(state.get("ran_ms", 0)) < EVERY:
        return None
    with httpx.Client(timeout=30.0) as c:
        state["h1"] = h1_real(c, h1_engine_trades(c, now_ms))
        state["funding"] = funding_rows(c, now_ms, state.get("funding", []))
    state["skipped_coins"] = list(SKIPPED)
    state["ran_ms"] = now_ms
    state["updated"] = datetime.fromtimestamp(now_ms / 1000, UTC).isoformat(timespec="minutes")
    DATA.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    return summary(state)


def main(argv: list[str]) -> int:
    if argv == ["run"]:
        out = run(force=True)
    elif argv == ["status"]:
        p = DATA / "state.json"
        out = (
            summary(json.loads(p.read_text(encoding="utf-8")))
            if p.exists()
            else {"상태": "아직 없음"}
        )
    else:
        raise SystemExit("usage: python -m scripts.shadow_272 run | status")
    sys.stdout.write(json.dumps(out, ensure_ascii=False, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
