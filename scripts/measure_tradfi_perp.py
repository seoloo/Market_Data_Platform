"""문서 86 §6 — Binance TradFi 무기한(XAU·XAG·SPY·QQQ) 실행 가능성. 공개 API 읽기만, 주문 없음.

사용 예::

    python -m scripts.measure_tradfi_perp snapshot   # 호가 스냅숏 1회 (여러 날·시간대에 반복)
    python -m scripts.measure_tradfi_perp history    # funding·괴리(premium) 이력, 데모 상장 확인
    python -m scripts.measure_tradfi_perp report     # C1~C4 판정 보고

기록: ``reports/tradfi_perp/snapshots.jsonl``·``history.json``·``report.md``.
기준(C1~C4)은 문서 86 §6 에 고정돼 있다. 이 결과는 추세 우위 판정(Q1·Q2)과 별개다.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import urllib.request
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

SYMBOLS = ("XAUUSDT", "XAGUSDT", "SPYUSDT", "QQQUSDT")
MAINNET = "https://fapi.binance.com"
DEMO = "https://demo-fapi.binance.com"
OUT = Path(__file__).resolve().parent.parent / "reports" / "tradfi_perp"
NOTIONAL = 10_000.0
C1_BPS, C2_ANNUAL, C3_BPS = 5.0, 0.03, 10.0
C1_MIN_SNAPSHOTS, C1_MIN_DAYS = 10, 5
"""C1 을 판정하려면 미국 정규장 스냅숏이 종목마다 10개 이상, 서로 다른 5일 이상 있어야 한다."""
_NY = ZoneInfo("America/New_York")
_YEAR_MS = 365.25 * 86_400_000


def _get(url: str) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": "auto-trading-research"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


# ------------------------------------------------------------------ 순수 함수


def us_regular_session(ms: int) -> bool:
    """미국 주식 정규장(뉴욕 09:30~16:00, 평일) 안인가. 공휴일은 따지지 않는다."""
    t = datetime.fromtimestamp(ms / 1000, UTC).astimezone(_NY)
    minutes = t.hour * 60 + t.minute
    return t.weekday() < 5 and 9 * 60 + 30 <= minutes < 16 * 60


def book_cost_bps(
    asks: Sequence[Sequence[str]], bids: Sequence[Sequence[str]], notional: float
) -> dict[str, float | None]:
    """중간가 대비 시장가 비용(bp): 반 스프레드 + 호가를 먹는 충격. 호가가 모자라면 None."""
    best_ask, best_bid = float(asks[0][0]), float(bids[0][0])
    mid = (best_ask + best_bid) / 2

    def walk(levels: Sequence[Sequence[str]]) -> float | None:
        left, qty = notional, 0.0
        for price_s, qty_s in levels:
            price, avail = float(price_s), float(qty_s)
            take = min(avail, left / price)
            qty += take
            left -= take * price
            if left <= 1e-9:
                return notional / qty
        return None

    buy, sell = walk(asks), walk(bids)
    return {
        "spread_bps": (best_ask - best_bid) / mid * 1e4,
        "buy_bps": None if buy is None else (buy / mid - 1) * 1e4,
        "sell_bps": None if sell is None else (1 - sell / mid) * 1e4,
    }


def funding_annual(rates: Sequence[tuple[int, float]]) -> float | None:
    """롱이 낸 funding 의 연율 (양수 = 비용). 첫 정산부터 마지막 정산까지 합 / 기간."""
    if len(rates) < 2:
        return None
    span = (rates[-1][0] - rates[0][0]) / _YEAR_MS
    return sum(r for _, r in rates) / span if span > 0 else None


def premium_summary(klines: Sequence[tuple[int, float]]) -> dict[str, float | int | None]:
    """1시간 premium(= mark 와 index 의 괴리 비율) 종가의 |값| 중앙값, 정규장 안/밖 따로 (bp)."""

    def med(vals: list[float]) -> float | None:
        return statistics.median(vals) if vals else None

    def p95(vals: list[float]) -> float | None:
        return sorted(vals)[int(0.95 * (len(vals) - 1))] if vals else None

    alls = [abs(v) * 1e4 for _, v in klines]
    inside = [abs(v) * 1e4 for t, v in klines if us_regular_session(t)]
    outside = [abs(v) * 1e4 for t, v in klines if not us_regular_session(t)]
    return {
        "hours": len(alls),
        "median_bps": med(alls),
        "session_median_bps": med(inside),
        "closed_median_bps": med(outside),
        "closed_p95_bps": p95(outside),
    }


def judge(
    snapshots: Sequence[Mapping[str, Any]],
    history: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    """종목마다 C1~C4. 상태는 PASS · FAIL · 자료 부족."""
    out: dict[str, dict[str, Any]] = {}
    for sym in SYMBOLS:
        rows = [s for s in snapshots if s["symbol"] == sym and s["us_session"]]
        costs = [
            max(s["buy_bps"], s["sell_bps"])
            for s in rows
            if s["buy_bps"] is not None and s["sell_bps"] is not None
        ]
        days = {datetime.fromtimestamp(s["at_ms"] / 1000, UTC).date() for s in rows}
        missing = len(rows) - len(costs)
        if len(rows) < C1_MIN_SNAPSHOTS or len(days) < C1_MIN_DAYS:
            c1 = ("자료 부족", f"정규장 스냅숏 {len(rows)}개 · {len(days)}일")
        elif missing:
            c1 = ("FAIL", f"호가가 {NOTIONAL:,.0f} USDT 를 못 채운 스냅숏 {missing}개")
        else:
            m = statistics.median(costs)
            c1 = (
                "PASS" if m <= C1_BPS else "FAIL",
                f"중앙값 {m:.2f}bp ({len(rows)}개·{len(days)}일)",
            )
        h = history.get(sym, {})
        fa = h.get("funding_annual")
        c2 = (
            ("자료 부족", "funding 이력 부족")
            if fa is None
            else ("PASS" if fa <= C2_ANNUAL else "FAIL", f"롱 비용 연 {fa * 100:.2f}%")
        )
        pm = (h.get("premium") or {}).get("median_bps")
        c3 = (
            ("자료 부족", "premium 이력 없음")
            if pm is None
            else ("PASS" if pm <= C3_BPS else "FAIL", f"|괴리| 중앙값 {pm:.2f}bp")
        )
        listed = h.get("demo_listed")
        c4 = (
            ("자료 부족", "history 미실행")
            if listed is None
            else ("PASS" if listed else "FAIL", "데모 상장" if listed else "데모에 없음")
        )
        crit = {"C1": c1, "C2": c2, "C3": c3, "C4": c4}
        states = [s for s, _ in crit.values()]
        overall = (
            "가능"
            if all(s == "PASS" for s in states)
            else "불가"
            if "FAIL" in states
            else "판정 대기"
        )
        out[sym] = {"criteria": crit, "overall": overall}
    return out


# ------------------------------------------------------------------ 수집


def snapshot() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    now = int(time.time() * 1000)
    lines = []
    for sym in SYMBOLS:
        book = _get(f"{MAINNET}/fapi/v1/depth?symbol={sym}&limit=100")
        cost = book_cost_bps(book["asks"], book["bids"], NOTIONAL)
        row = {"at_ms": now, "symbol": sym, "us_session": us_regular_session(now), **cost}
        lines.append(json.dumps(row, ensure_ascii=False))
        print(row)
    with (OUT / "snapshots.jsonl").open("a", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")
    return 0


def _paged(path: str, sym: str, start: int, limit: int, extra: str = "") -> list[Any]:
    rows: list[Any] = []
    while True:
        page = _get(f"{MAINNET}{path}?symbol={sym}&startTime={start}&limit={limit}{extra}")
        if not page:
            return rows
        rows += page
        last = page[-1]["fundingTime"] if isinstance(page[-1], dict) else page[-1][0]
        if len(page) < limit:
            return rows
        start = int(last) + 1


def history() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    info = {s["symbol"]: s for s in _get(f"{MAINNET}/fapi/v1/exchangeInfo")["symbols"]}
    demo = {s["symbol"] for s in _get(f"{DEMO}/fapi/v1/exchangeInfo")["symbols"]}
    out: dict[str, Any] = {"fetched_ms": int(time.time() * 1000)}
    for sym in SYMBOLS:
        onboard = int(info[sym]["onboardDate"])
        funding = [
            (int(r["fundingTime"]), float(r["fundingRate"]))
            for r in _paged("/fapi/v1/fundingRate", sym, onboard, 1000)
        ]
        prem = [
            (int(k[0]), float(k[4]))
            for k in _paged("/fapi/v1/premiumIndexKlines", sym, onboard, 1500, "&interval=1h")
            if any(float(x) != 0 for x in k[1:5])  # 거래 시작 전 빈 봉(전부 0)은 뺀다
        ]
        nonzero = sum(1 for _, r in funding if r != 0)
        out[sym] = {
            "onboard": datetime.fromtimestamp(onboard / 1000, UTC).strftime("%Y-%m-%d"),
            "funding_events": len(funding),
            "funding_nonzero": nonzero,
            "funding_annual": funding_annual(funding),
            "funding_max": max((r for _, r in funding), default=None),
            "funding_min": min((r for _, r in funding), default=None),
            "premium": premium_summary(prem),
            "demo_listed": sym in demo,
            "max_leverage_from_margin": round(1 / float(info[sym]["requiredMarginPercent"]) * 100),
        }
        print(sym, json.dumps(out[sym], ensure_ascii=False))
    (OUT / "history.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    return 0


def report() -> int:
    snaps_path, hist_path = OUT / "snapshots.jsonl", OUT / "history.json"
    snaps = (
        [json.loads(x) for x in snaps_path.read_text(encoding="utf-8").splitlines() if x.strip()]
        if snaps_path.exists()
        else []
    )
    hist = json.loads(hist_path.read_text(encoding="utf-8")) if hist_path.exists() else {}
    result = judge(snaps, hist)
    lines = [
        "# Binance TradFi 무기한 실행 가능성 (문서 86 §6)",
        "",
        f"생성 {datetime.now(tz=UTC):%Y-%m-%d %H:%M} UTC · 스냅숏 {len(snaps)}줄"
        " · 추세 우위 판정과 별개",
        "",
        "| 종목 | C1 호가 비용 | C2 funding | C3 괴리 | C4 데모 | 종합 |",
        "|---|---|---|---|---|---|",
    ]
    for sym, r in result.items():
        cells = [f"{s} · {d}" for s, d in r["criteria"].values()]
        lines.append(f"| {sym} | " + " | ".join(cells) + f" | **{r['overall']}** |")
    text = "\n".join(lines) + "\n"
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "report.md").write_text(text, encoding="utf-8")
    sys.stdout.write(text)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("snapshot", "history", "report"))
    args = parser.parse_args()
    return {"snapshot": snapshot, "history": history, "report": report}[args.stage]()


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        _reconfigure = getattr(_stream, "reconfigure", None)
        if _reconfigure is not None:
            _reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
