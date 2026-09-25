"""데모 O/v2 선물 추가분 — 실제 T/v2 목표로 매일 (docs/research/59). **데모 전용, 가짜 돈.**

O/v2 = T/v2 현물(데모 T/v2 가 이미 체결) + **선물 추가분 0.5 × w** + −20% 비상 스톱(24·27).
이 스크립트는 선물 다리만 맡는다. 현물 다리는 ``scripts/demo_t2_spot.py`` 가 같은 목표로 체결한다.

* 목표: T/v2 와 같은 전날 목표 비중 w(실거래소 공개 일봉) × 0.5 × E.
  E = 데모 **현물** 계좌 평가액. 현물 w·E 와 선물 0.5·w·E 를 합치면 O/v2 의 1.5 배 노출이다
  (증거금은 데모 선물 지갑에 있다).
* 밴드: 선물 명목 변화가 E 의 2.5%(= 5%p × 0.5) 미만이고 방향이 같으면 그대로 둔다.
* 스톱: 전날 무기한 종가 × 0.8 (O/v2 27 §2).
* 실행: O/v2 선물 일일 실행기(:class:`OverlayFuturesExecutor`, 52 V2 —
  수량 맞추기·공백 없는 스톱 교체·복구·긴급 정지).
* 종목: **ETHUSDT** 부터. BTC 는 21:08 Phase 7 훈련(BTCUSDT)이 끝나는 10/08 뒤 추가
  (사용자 결정 2026-09-25).
* 생략(데모 한계, Paper O/v2 가 판정): 일 손실 2% 노출 증가 금지·계좌 정지 30% —
  호출은 항상 "증가 허용"이고 긴급 정지 파일만 따른다.
  현물 비중이 커서 O 가 현물 일부를 선물로 옮기는 날(k < 1)은 따라 하지 않는다.

기록: ``data/demo_o2.db``(다른 훈련과 섞이지 않게 별도), ``reports/demo_o2/<결정일>.json``,
알림 사본 ``outbox.log``.
사용: ``python scripts/demo_o2_futures.py`` (매일 21:14 KST)
· ``--flatten`` (관찰 끝: 청산·스톱 정리)
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
for _p in (REPO_ROOT, REPO_ROOT / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import httpx  # noqa: E402

from auto_trading.config.schema import SpotTrendConfig  # noqa: E402
from auto_trading.config.secrets import (  # noqa: E402
    BinanceCredentials,
    load_telegram_credentials,
    read_env_file,
)
from auto_trading.core.money import floor_to_step  # noqa: E402
from auto_trading.exchange.binance_spot import BinanceSpotReadOnly  # noqa: E402
from auto_trading.exchange.binance_spot_demo import BinanceSpotDemo  # noqa: E402
from auto_trading.exchange.binance_usdm_testnet import BinanceUsdmTestnet  # noqa: E402
from auto_trading.execution.futures_guard import FuturesSupervisor  # noqa: E402
from auto_trading.execution.futures_orders import FuturesOrderManager  # noqa: E402
from auto_trading.execution.overlay_futures import (  # noqa: E402
    FuturesLegTarget,
    OverlayExecutionReport,
    OverlayFuturesExecutor,
)
from auto_trading.notify.channels import NotifyError, OutboxNotifier, TelegramNotifier  # noqa: E402
from auto_trading.ops.kill_switch import halted_reason  # noqa: E402
from auto_trading.storage.db import connect  # noqa: E402
from auto_trading.storage.migrations import apply_migrations  # noqa: E402
from auto_trading.storage.repositories.futures_orders import FuturesOrderRepository  # noqa: E402
from auto_trading.storage.repositories.risk_events import RiskEventRepository  # noqa: E402
from auto_trading.strategy.spot_trend import spot_trend_targets  # noqa: E402

for _stream in (sys.stdout, sys.stderr):
    _reconfigure = getattr(_stream, "reconfigure", None)
    if _reconfigure is not None:
        _reconfigure(encoding="utf-8", errors="replace")

SYMBOLS = ("ETHUSDT",)
"""BTCUSDT 는 10/08(Phase 7 훈련 종료) 뒤 추가."""
EXTRA = Decimal("0.5")
"""O/v2 노출 1.5 배 중 선물 추가분."""
BAND = Decimal("0.05") * EXTRA
STOP_FACTOR = Decimal("0.8")
LEVERAGE = 3
"""O 의 선물 레버리지 상한 3 배 (24 §2)."""
DB = REPO_ROOT / "data" / "demo_o2.db"
HALT = REPO_ROOT / "data" / "HALT"
OUT = REPO_ROOT / "reports" / "demo_o2"
TAG = "o2d"
STRATEGY = "o2_demo"
DAY_MS = 86_400_000


def _date(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m-%d")


@dataclass(frozen=True, slots=True)
class LegPlan:
    symbol: str
    weight: Decimal
    """T/v2 목표 비중 w."""
    target_qty: Decimal
    current_qty: Decimal
    stop: Decimal | None
    kept_by_band: bool


def plan_leg(
    symbol: str,
    weight: Decimal,
    equity: Decimal,
    mark: Decimal,
    prev_perp_close: Decimal,
    current_qty: Decimal,
    step: Decimal,
    tick: Decimal,
) -> LegPlan:
    """선물 목표 수량 = 0.5 × w × E / 가격 (step 내림).

    밴드 안이면 현재 수량을 그대로 목표로 둔다.
    """
    want = floor_to_step(max(weight, Decimal(0)) * EXTRA * equity / mark, step)
    change = abs(want - current_qty) * mark / equity if equity > 0 else Decimal(0)
    same_state = (want > 0) == (current_qty > 0)
    kept = change < BAND and same_state
    qty = current_qty if kept else want
    stop = floor_to_step(prev_perp_close * STOP_FACTOR, tick) if qty > 0 else None
    return LegPlan(symbol, weight, qty, current_qty, stop, kept)


def leg_message(
    p: LegPlan, before: Decimal, after: Decimal, ups: int | None, equity: Decimal
) -> str:
    """선물 다리 하나의 알림 — 무엇을 했고(롱 추가·늘림·줄임·청산·유지) 왜 했는지 짧게."""
    head = f"[데모·선물 O/v2] {p.symbol}"
    trend = "추세 정보 없음" if ups is None else f"20·60·120일 추세 {ups}/3 상승"
    stop = f"비상 스톱 {p.stop} (전날 무기한 종가 × 0.8)" if p.stop else "스톱 없음"
    if after == before:
        return (
            f"{head} 오늘 주문 없음 — 선물 롱 {after} 유지 (목표와 차이가 자본의 2.5% 밴드 안)\n"
            f"{trend} · T/v2 목표 {p.weight:.1%} → 선물 {p.weight * EXTRA:.1%} · {stop}"
        )
    if after == 0:
        action, why = (
            "선물 추가분 청산",
            f"{trend} → T/v2 목표가 0(현금)이라 선물 추가분도 0. 숏은 하지 않는다",
        )
    else:
        action = (
            "선물 롱 추가 진입"
            if before == 0
            else "선물 롱 늘림"
            if after > before
            else "선물 롱 줄임"
        )
        why = (
            f"현물 T/v2 가 롱 중({trend}, 목표 {p.weight:.1%}) → 그 절반({p.weight * EXTRA:.1%})을 "
            f"선물로 더 얹어 총 1.5배 (현물 자본 {equity:,.0f} USDT 기준)"
        )
    return f"{head} {action} {before} → {after}\n이유: {why}\n{stop}"


def state_message(rep: OverlayExecutionReport) -> str:
    """정상이 아닐 때 — 원인을 풀어 쓴다."""
    reasons = [*rep.notes]
    for g in rep.legs:
        reasons.extend(f"{g.symbol}: {n}" for n in g.notes)
    if rep.recovery_state != "RUNNING":
        reasons.insert(0, f"시작 점검(거래소 대조) {rep.recovery_state}")
    meaning = {
        "DEGRADED": "노출을 늘리지 않고 줄이기·보호만 했다",
        "HALTED": "아무 주문도 내지 않았다 — 사람 확인 필요",
    }.get(rep.state, "")
    return f"[데모·선물 O/v2] {rep.state} — {meaning}\n원인: {' | '.join(reasons) or '기록 확인'}"


class Notify:
    def __init__(self) -> None:
        creds = load_telegram_credentials(REPO_ROOT / ".env")
        self._telegram = TelegramNotifier(creds) if creds else None
        self._outbox = OutboxNotifier(OUT / "outbox.log")

    def __call__(self, text: str) -> None:
        print(text, flush=True)
        self._outbox.send(text)
        if self._telegram is not None:
            try:
                self._telegram.send(text)
            except NotifyError as exc:
                self._outbox.send(f"(텔레그램 실패: {exc})")


def _prev_perp_close(symbol: str, decision: int) -> Decimal:
    rows = httpx.get(
        "https://fapi.binance.com/fapi/v1/klines",
        params={"symbol": symbol, "interval": "1d", "endTime": decision, "limit": 3},
        timeout=20,
    ).json()
    closed = [r for r in rows if int(r[6]) <= decision]
    return Decimal(closed[-1][4])


def _summary(rep: OverlayExecutionReport) -> dict[str, Any]:
    return {
        "state": rep.state,
        "recovery_state": rep.recovery_state,
        "increases_allowed": rep.increases_allowed,
        "fired_stops": rep.fired_stops,
        "notes": rep.notes,
        "legs": [
            {
                "symbol": g.symbol,
                "before": str(g.before),
                "after": str(g.after),
                "orders": g.orders,
                "stop": g.stop,
                "notes": g.notes,
            }
            for g in rep.legs
        ],
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--flatten", action="store_true")
    args = parser.parse_args(argv)
    env = read_env_file(REPO_ROOT / ".env")
    creds = BinanceCredentials(env["BINANCE_TESTNET_API_KEY"], env["BINANCE_TESTNET_API_SECRET"])
    futures, spot, public = (
        BinanceUsdmTestnet(creds),
        BinanceSpotDemo(creds),
        BinanceSpotReadOnly(None),
    )
    conn = connect(DB)
    apply_migrations(conn)
    clock = lambda: int(time.time() * 1000)  # noqa: E731
    store = FuturesOrderRepository(conn)
    manager = FuturesOrderManager(
        futures, store, mode="demo", strategy_id=STRATEGY, strategy_tag=TAG, clock=clock
    )
    supervisor = FuturesSupervisor(
        futures,
        manager,
        store,
        RiskEventRepository(conn),
        mode="demo",
        symbols=list(SYMBOLS),
        require_stop=True,
        orphan_policy="flatten",
        shared_account=True,  # 데모 계좌를 여러 트랙이 나눠 쓴다 (문서 59 §3)
        clock=clock,
    )
    executor = OverlayFuturesExecutor(
        futures,
        manager,
        store,
        supervisor,
        clock=clock,
        halted_reason=lambda: halted_reason(HALT),
        settle=lambda: time.sleep(0.5),
    )
    notify = Notify()
    now = clock()
    decision = now - now % DAY_MS - 1
    record: dict[str, Any] = {"decision_date": _date(decision), "at_ms": now}
    try:
        positions = futures.account().positions
        if args.flatten:
            targets = [FuturesLegTarget(s, Decimal(0), None) for s in SYMBOLS]
            rep = executor.run(decision, targets, increases_allowed=False)
            record["report"] = _summary(rep)
            notify(
                f"[데모·선물 O/v2] 관찰 종료 — {', '.join(SYMBOLS)} 청산·스톱 정리 ({rep.state})"
            )
            return 0
        cfg = SpotTrendConfig()
        prev = decision - DAY_MS  # 오늘 체결하는 목표 = 전날 결정 (T/v2 와 같은 1 일 지연)
        daily = {s: public.closed_daily(s, prev) for s in cfg.symbols}
        signals = spot_trend_targets(daily, as_of_ms=prev, cfg=cfg)
        weights = {s: t.weight for s, t in signals.items()}
        ups = {
            s: round((t.direction * 3 + 3) / 2) if t.direction is not None else None
            for s, t in signals.items()
        }
        balances = spot.balances()
        prices = {s: spot.last_price(s) for s in cfg.symbols}
        equity = balances.get("USDT", Decimal(0)) + sum(
            balances.get(s.removesuffix("USDT"), Decimal(0)) * prices[s] for s in cfg.symbols
        )
        plans = []
        for s in SYMBOLS:
            f = futures.filters(s)
            pos = positions.get(s)
            plans.append(
                plan_leg(
                    s,
                    weights.get(s) or Decimal(0),
                    equity,
                    futures.mark_price(s),
                    _prev_perp_close(s, decision),
                    Decimal(0) if pos is None else pos.quantity,
                    f.step_size,
                    f.tick_size,
                )
            )
        for p in plans:  # 교차 증거금·레버리지는 포지션·스톱이 없을 때만 (54 §3.2, −4067)
            if not p.current_qty and not futures.open_stops(p.symbol):
                futures.set_margin_type(p.symbol, "CROSSED")
                futures.set_leverage(p.symbol, LEVERAGE)
        targets = [FuturesLegTarget(p.symbol, p.target_qty, p.stop) for p in plans]
        rep = executor.run(decision, targets, increases_allowed=True)
        record.update(
            equity_spot=str(equity),
            plans=[
                {
                    "symbol": p.symbol,
                    "weight": str(p.weight),
                    "target_qty": str(p.target_qty),
                    "current_qty": str(p.current_qty),
                    "stop": str(p.stop),
                    "kept_by_band": p.kept_by_band,
                }
                for p in plans
            ],
            report=_summary(rep),
        )
        for s in rep.fired_stops:
            notify(f"[데모·선물 O/v2] 비상 스톱 발동(밤사이): {s}")
        for p, leg in zip(plans, rep.legs, strict=True):
            notify(leg_message(p, leg.before, leg.after, ups.get(p.symbol), equity))
        if rep.state != "OK":
            notify(state_message(rep))
        return 0 if rep.state == "OK" else 1
    except Exception as exc:  # 무인 실행: 실패도 기록한다 (포지션은 거래소 스톱이 지킨다)
        record["error"] = f"{type(exc).__name__}: {exc}"
        notify(f"[데모·선물 O/v2] 실행 실패: {record['error']}")
        return 2
    finally:
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / f"{record['decision_date']}.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=1, default=str) + "\n", encoding="utf-8"
        )
        futures.close()
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
