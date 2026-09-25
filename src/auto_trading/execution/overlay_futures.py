"""O/v2 선물 다리 일일 실행기 — 목표 수량 맞추기와 공백 없는 보호 스톱 교체 (docs/research/52 V2).

Phase 8 2단계(선물 추가)용이다. **거래소 객체는 호출자가 준다** — 테스트는 FakeFuturesExchange,
테스트넷 훈련은 BinanceUsdmTestnet 이다. 실계좌 주문 경로는 Phase 8 승인 전까지 만들지 않는다.

하루 한 번 (종가 체결 뒤), 순서:

1. **정합성 복구** (:class:`FuturesSupervisor`): 열린 주문 해소 → 거래소 조회 → 차이 기록·조치.
   HALTED 면 아무것도 더 하지 않는다. 복구 중 **밤사이 발동한 스톱**을 보고서에 적는다.
2. **노출 증가 허용 여부:** 복구 상태가 RUNNING 이고, 긴급 정지 스위치가 꺼져 있고,
   호출자가 허용할 때만 (O 규칙: 그날 손실 2% 면 노출 증가 금지,
   :func:`daily_loss_blocks_increase`).
   막히면 **줄이는 거래와 보호는 계속한다**(AGENTS §2).
   스톱 발동 뒤 재진입도 이 규칙을 따른다(27 §2).
3. **수량 맞추기:** 목표 - 현재 = 차이. 줄이면 reduce-only(0 이면 CLOSE), 늘리면 OPEN.
   최소 주문 금액 미달인 늘리기는 건너뛴다(기록).
4. **보호 스톱 교체 (다리 방식, 52 §4.1):**
   새 가격 수량 스톱(다리) → 옛 스톱 취소 → 새 closePosition → 다리 취소.
   어느 순간에도 스톱이 하나 이상 걸려 있다.
   다리를 못 걸면 옛 스톱을 그대로 둔다(옛 가격이라도 보호).
   새 closePosition 을 못 걸면 다리를 남긴다(수량 = 포지션).
   걸린 스톱이 하나도 없으면 TRADING_CORE 규칙대로 3회·3초 재시도 → 실패 시 전량 시장가 청산·HALTED.
5. **마지막 확인:** 포지션이 있는 종목마다 거래소에 걸린 스톱이 있어야 한다.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal

from auto_trading.core.errors import ExchangeError, ReconciliationError
from auto_trading.core.money import floor_to_step
from auto_trading.exchange.futures import FuturesExchange, FuturesPosition
from auto_trading.execution.futures_guard import FuturesSupervisor
from auto_trading.execution.futures_orders import FuturesOrderManager, futures_client_order_id
from auto_trading.storage.repositories.futures_orders import (
    FuturesOrderRecord,
    FuturesOrderRepository,
)

__all__ = [
    "FuturesLegTarget",
    "LegReport",
    "OverlayExecutionReport",
    "OverlayFuturesExecutor",
    "daily_loss_blocks_increase",
]

_ZERO = Decimal(0)
State = Literal["OK", "DEGRADED", "HALTED"]


def daily_loss_blocks_increase(
    start_equity: Decimal, equity: Decimal, limit: Decimal = Decimal("0.02")
) -> bool:
    """O 규칙: UTC 하루 시작 자본 대비 손실이 ``limit`` 이상이면 노출 증가 금지."""
    if start_equity <= 0:
        return True  # 모르면 늘리지 않는다 (fail-closed)
    return (start_equity - equity) / start_equity >= limit


@dataclass(frozen=True, slots=True)
class FuturesLegTarget:
    symbol: str
    quantity: Decimal
    """목표 선물 포지션 (롱, 0 이상)."""
    stop_price: Decimal | None
    """보호 스톱 가격 (O/v2: 전날 무기한 종가 × 0.8). 목표가 0 이면 None 이어도 된다."""


@dataclass(slots=True)
class LegReport:
    symbol: str
    before: Decimal
    after: Decimal = _ZERO
    orders: list[str] = field(default_factory=list)
    stop: str | None = None
    notes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class OverlayExecutionReport:
    state: State
    recovery_state: str
    increases_allowed: bool
    fired_stops: list[str] = field(default_factory=list)
    legs: list[LegReport] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


class OverlayFuturesExecutor:
    def __init__(
        self,
        exchange: FuturesExchange,
        manager: FuturesOrderManager,
        store: FuturesOrderRepository,
        supervisor: FuturesSupervisor,
        *,
        clock: Callable[[], int],
        halted_reason: Callable[[], str | None],
        protect_attempts: int = 3,
        protect_window_ms: int = 3_000,
        settle: Callable[[], None] = lambda: None,
    ) -> None:
        self._ex = exchange
        self._m = manager
        self._store = store
        self._sup = supervisor
        self._clock = clock
        self._halted_reason = halted_reason
        self._attempts = protect_attempts
        self._window_ms = protect_window_ms
        self._settle = settle

    # ------------------------------------------------------------ 하루 실행

    def run(
        self,
        decision_ms: int,
        targets: Sequence[FuturesLegTarget],
        *,
        increases_allowed: bool,
    ) -> OverlayExecutionReport:
        stops_before = {r.client_order_id for r in self._m.working_stops()}
        by_symbol = {t.symbol: t for t in targets}
        recovery = self._sup.recover(
            decision_ms, lambda s, pos: self._fallback_stop(by_symbol.get(s), pos)
        )
        fired = [
            f"{r.symbol} {r.purpose} {r.stop_price} → {r.filled_qty} @ {r.avg_price}"
            for r in self._store.all()
            if r.client_order_id in stops_before and r.state == "FILLED"
        ]
        halt = self._halted_reason()
        allowed = increases_allowed and recovery.entries_allowed and halt is None
        report = OverlayExecutionReport("OK", recovery.state, allowed, fired)
        if halt is not None:
            report.notes.append(f"긴급 정지 스위치 — 노출 증가 금지: {halt}")
        if not increases_allowed:
            report.notes.append("호출자가 노출 증가를 막음 (그날 손실 2% 등)")
        if recovery.state == "HALTED":
            report.state = "HALTED"
            report.notes.append("정합성 복구가 HALTED — 오늘 아무 주문도 보내지 않는다")
            return report
        if recovery.state != "RUNNING" or halt is not None:
            report.state = "DEGRADED"  # 정지 스위치도 정상 운영이 아니다 — 점검에 드러나게
        for target in targets:
            leg = self._run_leg(decision_ms, target, allowed=allowed)
            report.legs.append(leg)
            if any(n.startswith("HALTED") for n in leg.notes):
                report.state = "HALTED"
            elif leg.notes and report.state == "OK":
                report.state = "DEGRADED"
        self._final_check(report)
        return report

    # ------------------------------------------------------------ 종목 하나

    def _position(self, symbol: str) -> Decimal:
        pos = self._ex.account().positions.get(symbol)
        return pos.quantity if pos is not None else _ZERO

    def _run_leg(self, decision_ms: int, t: FuturesLegTarget, *, allowed: bool) -> LegReport:
        try:
            current = self._position(t.symbol)
        except ExchangeError as exc:
            return LegReport(
                t.symbol, _ZERO, notes=[f"계좌 조회 실패 — 이 종목은 오늘 건너뜀: {exc}"]
            )
        leg = LegReport(t.symbol, current)
        f = self._ex.filters(t.symbol)
        desired = floor_to_step(max(t.quantity, _ZERO), f.step_size)
        if desired > current and not allowed:
            leg.notes.append(f"노출 증가 금지 — 목표 {desired} 대신 {current} 유지")
            desired = current
        delta = desired - current
        if abs(delta) >= f.step_size and delta:
            self._trade(decision_ms, t.symbol, current, desired, delta, leg)
        try:
            leg.after = self._position(t.symbol)
        except ExchangeError as exc:
            leg.notes.append(f"체결 뒤 계좌 조회 실패: {exc}")
            leg.after = current
            return leg
        self._maintain_stop(decision_ms, t, leg)
        return leg

    def _trade(
        self,
        decision_ms: int,
        symbol: str,
        current: Decimal,
        desired: Decimal,
        delta: Decimal,
        leg: LegReport,
    ) -> None:
        if delta > 0:
            f = self._ex.filters(symbol)
            if delta * self._ex.mark_price(symbol) < f.min_notional:
                leg.notes.append(f"늘리기 {delta} 가 최소 주문 금액 미달 — 건너뜀")
                return
            side, purpose = "BUY", "OPEN"
        else:
            side, purpose = "SELL", ("CLOSE" if desired == 0 else "REDUCE")
        try:
            record = self._m.submit_market(
                decision_ms=decision_ms,
                symbol=symbol,
                side=side,
                quantity=abs(delta),
                purpose=purpose,
                attempt=self._attempt(decision_ms, symbol, purpose, side),
            )
        except ReconciliationError as exc:
            leg.notes.append(f"{purpose} 거부 — {exc}")
            return
        leg.orders.append(f"{purpose} {side} {abs(delta)} → {record.state}")
        if record.state != "FILLED":
            leg.notes.append(f"{purpose} 가 끝나지 않음 ({record.state}) — 다음 복구가 조회로 해소")

    # ------------------------------------------------------------ 보호 스톱

    def _attempt(self, decision_ms: int, symbol: str, purpose: str, side: str) -> int:
        n = 0
        while self._store.get(
            futures_client_order_id(self._m.tag, decision_ms, symbol, purpose, side, n)
        ):
            n += 1
        return n

    def _fallback_stop(self, t: FuturesLegTarget | None, pos: FuturesPosition) -> Decimal | None:
        """복구가 보호 없는 포지션을 찾았을 때 걸 가격: 오늘 목표 스톱, 없으면 mark 의 20% 아래."""
        f = self._ex.filters(pos.symbol)
        if t is not None and t.stop_price is not None:
            return floor_to_step(t.stop_price, f.tick_size)
        return floor_to_step(self._ex.mark_price(pos.symbol) * Decimal("0.8"), f.tick_size)

    def _listed(self, symbol: str) -> set[str] | None:
        """거래소의 걸린 스톱 목록 (조회 실패면 None).

        테스트넷 실측(2026-09-25, 54 §3): 취소 직후 **개별 조회**는 잠시 NEW 로 보이고,
        막 건 스톱의 취소가 '없음'으로 올 때도 있다.
        걸린 목록(openAlgoOrders)은 바로 맞았다 — 그래서 목록을 기준으로 삼는다.
        """
        try:
            return {o.client_order_id for o in self._ex.open_stops(symbol)}
        except ExchangeError:
            return None

    def _stops(self, symbol: str) -> list[FuturesOrderRecord]:
        """로컬 기록상 걸린 스톱 중 거래소 목록에도 있는 것."""
        local = self._m.working_stops(symbol)
        listed = self._listed(symbol)
        return local if listed is None else [r for r in local if r.client_order_id in listed]

    def _cancel(self, record: FuturesOrderRecord, leg: LegReport, label: str) -> bool:
        """취소하고 거래소 목록으로 확인한다. 목록에서 사라졌으면 True."""
        r = self._m.cancel(record.client_order_id)
        gone = r.state != "SUBMITTED"
        if not gone:
            self._settle()
            listed = self._listed(record.symbol)
            gone = listed is not None and record.client_order_id not in listed
        leg.orders.append(
            f"{label} → {r.state}"
            + ("" if r.state != "SUBMITTED" or not gone else " (목록에서 사라짐 — 취소로 본다)")
        )
        return gone

    def _maintain_stop(self, decision_ms: int, t: FuturesLegTarget, leg: LegReport) -> None:
        symbol, pos = t.symbol, leg.after
        if pos == 0:
            for s in self._stops(symbol):
                self._cancel(s, leg, f"포지션 0 — 스톱 정리 {s.purpose}")
            return
        if pos < 0:
            leg.notes.append("숏 포지션 — O 선물 다리는 롱만이다. 사람이 확인한다")
            return
        tick = self._ex.filters(symbol).tick_size
        price = self._fallback_stop(t, FuturesPosition(symbol, pos, _ZERO, 0))
        if price is None:  # pragma: no cover - _fallback_stop 는 항상 가격을 준다
            return
        price = floor_to_step(price, tick)
        stops = self._stops(symbol)
        close = [s for s in stops if s.close_position]
        if len(stops) == 1 and close and close[0].stop_price == price:
            leg.stop = f"{price} 유지"
            return
        # 1) 다리: 새 가격 수량 스톱 (수량 = 현재 포지션)
        bridge = self._m.place_bridge_stop(
            decision_ms=decision_ms,
            symbol=symbol,
            side="SELL",
            stop_price=price,
            quantity=pos,
            attempt=self._attempt(decision_ms, symbol, "BRIDGE", "SELL"),
        )
        leg.orders.append(f"다리 스톱 {price} × {pos} → {bridge.state}")
        if not bridge.is_working_stop:
            if stops:
                leg.notes.append(
                    f"다리 스톱 실패 — 옛 스톱 유지 ({', '.join(str(s.stop_price) for s in stops)})"
                )
                leg.stop = "옛 스톱 유지"
                return
            self._protect_or_close(decision_ms, symbol, pos, price, leg)
            return
        # 2) 옛 스톱 취소 (다리가 보호 중)
        for s in stops:
            if not self._cancel(s, leg, f"옛 스톱 취소 {s.purpose} {s.stop_price}"):
                leg.notes.append(f"옛 스톱 취소 실패 {s.client_order_id} — 다음 실행이 다시 정리")
        # 3) 새 closePosition
        new = self._m.place_protective_stop(
            decision_ms=decision_ms,
            symbol=symbol,
            side="SELL",
            stop_price=price,
            attempt=self._attempt(decision_ms, symbol, "PROTECT", "SELL"),
        )
        leg.orders.append(f"새 closePosition 스톱 {price} → {new.state}")
        if not new.is_working_stop:
            leg.notes.append("새 closePosition 스톱 실패 — 다리 스톱(수량 = 포지션)이 보호 중")
            leg.stop = f"{price} 다리"
            return
        # 4) 다리 취소
        if not self._cancel(bridge, leg, "다리 취소"):
            leg.notes.append(
                "다리 취소 실패 — 다음 실행이 정리 (reduce-only 라 반대 포지션은 생기지 않는다)"
            )
        leg.stop = f"{price} 교체"

    def _protect_or_close(
        self, decision_ms: int, symbol: str, pos: Decimal, price: Decimal, leg: LegReport
    ) -> None:
        """걸린 스톱이 하나도 없다: 3회·3초 재시도 → 실패하면 전량 시장가 청산 (TRADING_CORE)."""
        start = self._clock()
        for _ in range(self._attempts):
            if self._clock() - start > self._window_ms:
                break
            r = self._m.place_protective_stop(
                decision_ms=decision_ms,
                symbol=symbol,
                side="SELL",
                stop_price=price,
                attempt=self._attempt(decision_ms, symbol, "PROTECT", "SELL"),
            )
            leg.orders.append(f"보호 스톱 재시도 {price} → {r.state}")
            if r.is_working_stop:
                leg.stop = f"{price} (재시도)"
                return
        close = self._m.submit_market(
            decision_ms=decision_ms,
            symbol=symbol,
            side="SELL",
            quantity=pos,
            purpose="CLOSE",
            attempt=self._attempt(decision_ms, symbol, "CLOSE", "SELL"),
        )
        done = close.state == "FILLED"
        leg.orders.append(f"보호 실패 비상 청산 {pos} → {close.state}")
        leg.notes.append(
            "HALTED: 보호 스톱을 걸 수 없어 비상 청산"
            + ("" if done else f" — 청산 미완료({close.state}), 계속 대조 필요")
        )

    def _final_check(self, report: OverlayExecutionReport) -> None:
        """포지션이 있는 종목마다 거래소에 걸린 스톱이 있어야 한다."""
        try:
            positions = self._ex.account().positions
            for symbol, pos in positions.items():
                if pos.quantity > 0 and not self._ex.open_stops(symbol):
                    report.state = "HALTED" if report.state == "HALTED" else "DEGRADED"
                    report.notes.append(f"{symbol}: 포지션 {pos.quantity} 에 걸린 스톱이 없다")
        except ExchangeError as exc:
            report.notes.append(f"마지막 확인 조회 실패: {exc}")
            if report.state == "OK":
                report.state = "DEGRADED"
