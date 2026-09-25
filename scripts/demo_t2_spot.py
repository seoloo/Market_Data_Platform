"""데모 T/v2 현물 — 지정가 L240 체결 비용 실측 (docs/research/55). **데모 모드 전용, 가짜 돈.**

매일 한 번(예정 21:12 KST):

1. 방금 마감된 일봉의 close_ms 를 결정 시각 d 로 둔다.
   d 기록이 이미 끝났으면 아무것도 하지 않는다(멱등).
2. **d−1 결정의 목표**를 데모 계좌 잔고·데모 가격으로 체결한다 (T/v2 와 같은 1 일 지연·밴드 5%p).
   d−1 기록이 없으면(첫 실행·PC 꺼짐) 실거래소 공개 일봉으로 d−1 목표를 다시 계산한다
   — 과거 종가만 쓰므로 미래 정보가 아니다.
   주문: 매수 1호가·매도 1호가 post-only, 최대 4 시간, 남은 수량은 시장가 (42 §7 L240).
3. 실거래소 공개 일봉(api.binance.com, 키 불필요)으로 d 목표를 정해 기록한다.
4. 텔레그램: 예약·체결·추격마다 짧게 (무슨 연구·무엇을·왜).

기록: ``reports/demo_t2/days.jsonl`` (결정일 한 줄), ``reports/demo_t2/outbox.log`` (알림 사본).
사용: ``python scripts/demo_t2_spot.py`` (``--dry-run``: 주문 없이 계획만 출력).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from auto_trading.config.schema import SpotTrendConfig  # noqa: E402
from auto_trading.config.secrets import (  # noqa: E402
    BinanceCredentials,
    load_telegram_credentials,
    read_env_file,
)
from auto_trading.core.money import floor_to_step  # noqa: E402
from auto_trading.exchange.binance_spot import BinanceSpotReadOnly  # noqa: E402
from auto_trading.exchange.binance_spot_demo import BinanceSpotDemo  # noqa: E402
from auto_trading.execution.demo_limit import (  # noqa: E402
    DemoLimitOrder,
    LimitOutcome,
    run_limit_orders,
)
from auto_trading.notify.channels import NotifyError, OutboxNotifier, TelegramNotifier  # noqa: E402
from auto_trading.strategy.spot_trend import (  # noqa: E402
    SpotSide,
    SpotTarget,
    plan_spot_orders,
    spot_trend_targets,
)

OUT = REPO_ROOT / "reports" / "demo_t2"
LEDGER = OUT / "days.jsonl"
DAY_MS = 86_400_000
QUOTE = "USDT"
FEE = Decimal("0.001")
TAG = "[데모·현물 T/v2] 연구 55 지정가 체결 비용 시험"


def _date(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m-%d")


def _ledger() -> dict[int, dict[str, Any]]:
    if not LEDGER.exists():
        return {}
    rows = [json.loads(line) for line in LEDGER.read_text(encoding="utf-8").splitlines() if line]
    return {int(r["decision_ms"]): r for r in rows}


def _append(row: dict[str, Any]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


class Notify:
    def __init__(self, *, dry_run: bool) -> None:
        creds = load_telegram_credentials(REPO_ROOT / ".env")
        self._telegram = TelegramNotifier(creds) if creds and not dry_run else None
        self._outbox = OutboxNotifier(OUT / "outbox.log")
        self._dry = dry_run

    def __call__(self, text: str) -> None:
        print(text)
        if self._dry:
            return
        self._outbox.send(text)
        if self._telegram is not None:
            try:
                self._telegram.send(text)
            except NotifyError as exc:
                self._outbox.send(f"(텔레그램 실패: {exc})")


def _targets(
    public: BinanceSpotReadOnly, cfg: SpotTrendConfig, as_of: int
) -> dict[str, SpotTarget]:
    daily = {s: public.closed_daily(s, as_of, limit=200) for s in cfg.symbols}
    return spot_trend_targets(daily, as_of_ms=as_of, cfg=cfg)


def _target_row(t: SpotTarget) -> dict[str, Any]:
    return {
        "weight": None if t.weight is None else str(t.weight),
        "direction": None if t.direction is None else str(t.direction),
        "vol": None if t.vol_annual is None else str(t.vol_annual),
        "reason": t.reason,
    }


def _why(row: dict[str, Any], current: Decimal) -> str:
    w = Decimal(row["weight"])
    d = Decimal(row["direction"]) if row["direction"] else Decimal(0)
    ups = round((d * 3 + 3) / 2)
    vol = Decimal(row["vol"]) if row["vol"] else Decimal(0)
    return (
        f"T/v2 목표 {w:.1%} ← 현재 {current:.1%} "
        f"(20·60·120일 추세 {ups}/3 상승, 60일 변동성 {vol:.0%})"
    )


def _describe(kind: str, out: LimitOutcome, side_ko: str) -> str:
    o = out.order
    head = f"[데모·현물] {o.symbol} {side_ko}"
    if kind == "posted":
        return f"{TAG}\n{head} {o.quantity} @{out.limit_price} post-only 예약 (최대 4시간)"
    costs = out.is_bps()
    cost = "" if costs is None else f" · 중간가 대비 {costs[0]:+.1f}bp + 수수료 {costs[1]:.1f}bp"
    wait = "" if out.wait_min is None else f" · 대기 {out.wait_min:.0f}분"
    tail = f" @{out.avg_price:.2f}{wait}{cost}"
    if kind == "filled":
        return f"{head} 지정가 체결 {out.filled_qty}{tail}"
    return f"{head}: 지정가 {out.maker_qty} 체결, 나머지 {out.taker_qty} 시장가 추격{tail}"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    env = read_env_file(REPO_ROOT / ".env")
    key, secret = env.get("BINANCE_TESTNET_API_KEY", ""), env.get("BINANCE_TESTNET_API_SECRET", "")
    if not key or not secret:
        raise SystemExit(".env 에 BINANCE_TESTNET_API_KEY / SECRET(데모 키)가 없다")
    cfg = SpotTrendConfig()
    notify = Notify(dry_run=args.dry_run)
    now = int(time.time() * 1000)
    decision = now - now % DAY_MS - 1
    ledger = _ledger()
    if decision in ledger:
        print(f"{_date(decision)} 결정은 이미 기록됨 — 할 일 없음")
        return 0
    public = BinanceSpotReadOnly(None)
    demo = BinanceSpotDemo(BinanceCredentials(key, secret))
    prev_ms = decision - DAY_MS
    prev = ledger.get(prev_ms)
    bootstrap = prev is None
    if prev is None:
        prev_targets = {s: _target_row(t) for s, t in _targets(public, cfg, prev_ms).items()}
    else:
        prev_targets = prev["targets"]
    balances = demo.balances()
    prices = {s: demo.last_price(s) for s in cfg.symbols}
    filters = {s: demo.filters(s) for s in cfg.symbols}
    holdings = {s: balances.get(s.removesuffix(QUOTE), Decimal(0)) for s in cfg.symbols}
    equity = balances.get(QUOTE, Decimal(0)) + sum(holdings[s] * prices[s] for s in cfg.symbols)
    targets = {
        s: SpotTarget(s, None if r["weight"] is None else Decimal(r["weight"]))
        for s, r in prev_targets.items()
    }
    intents = plan_spot_orders(
        targets,
        holdings=holdings,
        prices=prices,
        equity=equity,
        cfg=cfg,
        step_size={s: f.step_size for s, f in filters.items()},
        min_notional={s: f.min_notional for s, f in filters.items()},
    )
    cash = balances.get(QUOTE, Decimal(0))
    orders: list[DemoLimitOrder] = []
    reasons: dict[str, str] = {}
    stamp = _date(prev_ms).replace("-", "")
    for intent in intents:
        qty = intent.quantity
        f = filters[intent.symbol]
        if intent.side is SpotSide.BUY:
            qty = min(qty, floor_to_step(cash / (prices[intent.symbol] * (1 + FEE)), f.step_size))
            if qty * prices[intent.symbol] < f.min_notional:
                continue
            cash -= qty * prices[intent.symbol] * (1 + FEE)
        cid = f"dt2-{stamp}-{intent.symbol[:3]}-{intent.side.value[0]}"
        orders.append(
            DemoLimitOrder(intent.symbol, intent.side.value, qty, cid, f.step_size, f.min_notional)
        )
        current = holdings[intent.symbol] * prices[intent.symbol] / equity if equity else Decimal(0)
        reasons[intent.symbol] = _why(prev_targets[intent.symbol], current)
    print(
        f"결정 {_date(decision)} · 체결 대상 {_date(prev_ms)} 목표"
        f"{' (재계산)' if bootstrap else ''} · "
        f"데모 자본 {equity:.2f} USDT · 주문 {len(orders)}건"
    )

    def on_event(kind: str, out: LimitOutcome) -> None:
        side_ko = "매수" if out.order.side == "BUY" else "매도"
        text = _describe(kind, out, side_ko)
        if kind == "posted":
            text += f"\n이유: {reasons[out.order.symbol]}"
        notify(text)

    outcomes: list[LimitOutcome] = []
    if orders and not args.dry_run:
        outcomes = run_limit_orders(
            demo, orders, clock=lambda: int(time.time() * 1000), sleep=time.sleep, on_event=on_event
        )
    elif orders:
        for o in orders:
            print(f"(dry-run) {o.symbol} {o.side} {o.quantity} — {reasons[o.symbol]}")
    new_targets = {s: _target_row(t) for s, t in _targets(public, cfg, decision).items()}
    if args.dry_run:
        print("다음 목표:", json.dumps(new_targets, ensure_ascii=False))
        return 0
    executed = []
    for out in outcomes:
        costs = out.is_bps()
        executed.append(
            {
                "symbol": out.order.symbol,
                "side": out.order.side,
                "qty": str(out.order.quantity),
                "p0_mid": str(out.p0_mid),
                "limit_price": None if out.limit_price is None else str(out.limit_price),
                "maker_qty": str(out.maker_qty),
                "taker_qty": str(out.taker_qty),
                "avg_price": None if out.avg_price is None else str(out.avg_price),
                "fee_usdt": str(out.fee_usdt),
                "is_price_bps": None if costs is None else str(round(costs[0], 3)),
                "is_fee_bps": None if costs is None else str(round(costs[1], 3)),
                "wait_min": out.wait_min,
                "post_rejects": out.post_rejects,
                "chased": out.chased,
                "notes": out.notes,
            }
        )
    _append(
        {
            "decision_ms": decision,
            "decision_date": _date(decision),
            "executed_for": _date(prev_ms),
            "bootstrap": bootstrap,
            "equity_before": str(equity),
            "targets": new_targets,
            "executed": executed,
            "created_ms": int(time.time() * 1000),
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
