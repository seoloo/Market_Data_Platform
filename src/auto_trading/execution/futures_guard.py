"""선물 재시작 정합성 복구와 보호 주문 감시.

TRADING_CORE §재시작·정합성 복구, §포지션 상태·보호주문.

순서 (TRADING_CORE):

1. 신규 진입을 막은 채 열린 주문을 거래소 조회로 해소한다 (:meth:`FuturesOrderManager.recover`).
2. 거래소 계좌·포지션·미체결 주문을 조회한다. **조회에 실패하면 아무 주문도 보내지 않고 멈춘다**
   — 모르는 상태에서 추정 주문을 보내지 않는다.
3. 로컬 기록(체결 합 + 확인된 외부 변동)과 거래소를 비교해 차이를 찾는다.
   **거래소가 진실의 원천이다.**
4. 정책대로 조치하고, 찾은 차이와 조치를 ``risk_events``에 남긴다.
5. 상태를 정한다: RUNNING(이상 없음) · DEGRADED(사람 확인 필요, 신규 진입 금지)
   · HALTED(보호 실패로 청산).

자동으로 하는 조치는 셋뿐이다. 나머지 차이(수량 불일치·사라진 포지션·모르는 주문)는 기록만 하고
사람이 확인한다 — 자동으로 맞추면 원인을 덮는다.

* **보호 스톱이 없는 포지션** (정책이 요구할 때): 최대 3회·3초 안에 스톱을 건다. 끝내 못 걸면
  전량 시장가 청산 후 HALTED. 청산도 실패하면 HALTED 를 유지하고 "청산 완료"로 적지 않는다.
* **원인 불명 포지션** (``orphan_policy=flatten``): reduce-only 로 정리하고, 그 외부 포지션을
  조정 기록으로 남긴다. ``hold_with_stop``이면 기록으로 입양하고 보호 스톱을 건다.
* **할 일이 없는 스톱**(포지션이 없거나 방향이 틀림): 취소한다.

**우리가 걸지 않은 주문이 있는 심볼은 아무것도 자동으로 하지 않는다.** 다른 주체가 있다는 뜻이고,
자동 조치는 그 주체와 부딪친다(모르는 스톱이 보호 중인데 우리 스톱이 -4130 으로 막혀 비상 청산 등).

보호 스톱 요구는 정책으로 받는다(``require_stop``). TRADING_CORE 는 거래소 보호주문을 필수로 두지만,
O/v1 은 연구 한정으로 "가격 손절 없음" 예외를 두었다(24 §7) — 이 충돌은 실거래 전 결정할 일이며
여기서 정하지 않는다. ``require_stop=False`` 면 보호 없는 포지션은 정보로만 기록한다.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal

from auto_trading.core.errors import ExchangeError
from auto_trading.exchange.futures import FuturesExchange, FuturesOrderResult, FuturesPosition
from auto_trading.execution.futures_orders import FuturesOrderManager, futures_client_order_id
from auto_trading.storage.repositories.futures_orders import (
    FuturesOrderRecord,
    FuturesOrderRepository,
)
from auto_trading.storage.repositories.risk_events import RiskEvent, RiskEventRepository

__all__ = [
    "FuturesSupervisor",
    "Issue",
    "RecoveryReport",
    "expected_positions",
    "inspect_futures",
]

Severity = Literal["info", "warn", "critical"]
_ZERO = Decimal(0)
_MANUAL = frozenset({"MISSING_POSITION", "QTY_MISMATCH", "UNTRACKED_ORDER", "LOCAL_STOP_MISSING"})


@dataclass(frozen=True, slots=True)
class Issue:
    kind: str
    symbol: str
    severity: Severity
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class RecoveryReport:
    state: Literal["RUNNING", "DEGRADED", "HALTED"]
    issues: list[Issue]
    actions: list[str]

    @property
    def entries_allowed(self) -> bool:
        return self.state == "RUNNING"


def expected_positions(
    records: Iterable[FuturesOrderRecord], adjustments: Iterable[tuple[str, Decimal]]
) -> dict[str, Decimal]:
    """로컬 기록으로 본 포지션 = 우리 체결의 합 + 확인된 외부 변동."""
    out: dict[str, Decimal] = {}
    for r in records:
        if r.filled_qty > 0:
            signed = r.filled_qty if r.side == "BUY" else -r.filled_qty
            out[r.symbol] = out.get(r.symbol, _ZERO) + signed
    for symbol, qty in adjustments:
        out[symbol] = out.get(symbol, _ZERO) + qty
    return out


def _closing_side(quantity: Decimal) -> str:
    return "SELL" if quantity > 0 else "BUY"


def inspect_futures(
    *,
    positions: Mapping[str, FuturesPosition],
    expected: Mapping[str, Decimal],
    local_stops: Sequence[FuturesOrderRecord],
    exchange_open: Mapping[str, Sequence[FuturesOrderResult]],
    known_ids: set[str],
    step_sizes: Mapping[str, Decimal],
    require_stop: bool,
) -> list[Issue]:
    """거래소 상태와 로컬 기록의 차이. 순수 함수."""
    issues: list[Issue] = []
    symbols = sorted(set(positions) | set(expected) | set(exchange_open))
    for s in symbols:
        remote = positions[s].quantity if s in positions else _ZERO
        local = expected.get(s, _ZERO)
        step = step_sizes.get(s, _ZERO)
        detail = {"exchange": str(remote), "local": str(local)}
        if remote and not local:
            issues.append(Issue("ORPHAN_POSITION", s, "critical", detail))
        elif local and not remote:
            issues.append(Issue("MISSING_POSITION", s, "critical", detail))
        elif abs(remote - local) > step:
            issues.append(Issue("QTY_MISMATCH", s, "critical", detail))
        remote_orders = list(exchange_open.get(s, ()))
        remote_ids = {o.client_order_id for o in remote_orders}
        for o in remote_orders:
            if o.client_order_id not in known_ids:
                issues.append(
                    Issue("UNTRACKED_ORDER", s, "critical", {"client_order_id": o.client_order_id})
                )
        stops = [r for r in local_stops if r.symbol == s]
        for r in stops:
            if r.client_order_id not in remote_ids:
                issues.append(
                    Issue(
                        "LOCAL_STOP_MISSING", s, "critical", {"client_order_id": r.client_order_id}
                    )
                )
        live = [r for r in stops if r.client_order_id in remote_ids]
        if not remote:
            issues.extend(
                Issue("STALE_STOP", s, "warn", {"client_order_id": r.client_order_id}) for r in live
            )
            continue
        good = [r for r in live if r.side == _closing_side(remote)]
        issues.extend(
            Issue("STOP_WRONG_SIDE", s, "critical", {"client_order_id": r.client_order_id})
            for r in live
            if r.side != _closing_side(remote)
        )
        if not good:
            severity: Severity = "critical" if require_stop else "info"
            issues.append(Issue("UNPROTECTED", s, severity, {"quantity": str(remote)}))
    return issues


StopPriceFor = Callable[[str, FuturesPosition], Decimal | None]
"""포지션에 걸 보호 스톱 가격. 정할 수 없으면 None (그러면 보호 실패로 다룬다)."""


class FuturesSupervisor:
    def __init__(
        self,
        exchange: FuturesExchange,
        manager: FuturesOrderManager,
        store: FuturesOrderRepository,
        events: RiskEventRepository,
        *,
        mode: str,
        symbols: Sequence[str],
        require_stop: bool,
        orphan_policy: str,
        clock: Callable[[], int],
        protect_attempts: int = 3,
        protect_window_ms: int = 3_000,
        shared_account: bool = False,
        orphan_close: str = "limit",
    ) -> None:
        """``shared_account``: 계좌를 여러 트랙이 나눠 쓴다(데모·테스트넷, 문서 59 §3).

        ``orphan_close``: 원인 불명 포지션 정리 주문. 기본 ``limit`` = 가격 한도 지정가 IOC
        (TRADING_CORE 시장가 금지). ``market`` 은 Phase 7 판정 창(~10/08) 동안 ``testnet_drill`` 이
        같은 경로를 유지하려고만 남긴다 — 판정 뒤 없앤다.

        기본(False)은 실거래처럼 **계좌 전체**를 본다
        — 기록에 없는 포지션은 어느 종목이든 원인 불명이다.
        True 면 ``symbols``·자기 스톱의 종목만 본다. 다른 트랙의 포지션을 원인 불명으로 보고
        정리(flatten)하지 않게 한다.
        """
        if orphan_policy not in {"flatten", "hold_with_stop"}:
            raise ValueError(f"orphan_policy: {orphan_policy}")
        if orphan_close not in {"limit", "market"}:
            raise ValueError(f"orphan_close: {orphan_close}")
        self._exchange = exchange
        self._manager = manager
        self._store = store
        self._events = events
        self._mode = mode
        self._symbols = tuple(symbols)
        self._require_stop = require_stop
        self._orphan_policy = orphan_policy
        self._clock = clock
        self._attempts = protect_attempts
        self._window_ms = protect_window_ms
        self._shared = shared_account
        self._orphan_capped = orphan_close == "limit"

    def protection_since_ms(self, symbol: str) -> int | None:
        event = self._events.latest_scoped(
            self._mode,
            ("futures.PROTECTION_STARTED", "futures.PROTECTION_FINISHED"),
            self._manager.tag,
            symbol,
        )
        return event.ts_ms if event and event.kind == "futures.PROTECTION_STARTED" else None

    def start_protection_window(self, symbol: str) -> None:
        """잠재 체결 전 영속 선기록. 실제 첫 체결 시각을 모를 때의 보수적 시작점."""
        if self.protection_since_ms(symbol) is None:
            self._protection_event("PROTECTION_STARTED", symbol, "info")

    def finish_protection_window(self, symbol: str) -> None:
        if self.protection_since_ms(symbol) is not None:
            self._protection_event("PROTECTION_FINISHED", symbol, "info")

    def protection_halted(self) -> bool:
        """보호 위반은 프로세스/날짜가 바뀌어도 자동 해제하지 않는다."""
        return (
            self._events.latest_scoped(
                self._mode, ("futures.PROTECTION_HALTED",), self._manager.tag
            )
            is not None
        )

    def halt_protection(self, symbol: str, elapsed_ms: int) -> None:
        if not self.protection_halted():
            self._protection_event("PROTECTION_HALTED", symbol, "critical", elapsed_ms)

    def _protection_event(
        self, kind: str, symbol: str, severity: str, elapsed_ms: int | None = None
    ) -> None:
        self._events.insert(
            RiskEvent(
                self._clock(),
                self._mode,
                severity,
                f"futures.{kind}",
                {
                    "strategy_tag": self._manager.tag,
                    "symbol": symbol,
                    "elapsed_upper_ms": elapsed_ms,
                },
            )
        )

    def recover(self, decision_ms: int, stop_price_for: StopPriceFor) -> RecoveryReport:
        report = RecoveryReport("RUNNING", [], [])
        self._manager.recover()
        if self._manager.blocked():
            report.issues.append(Issue("UNRESOLVED_ORDERS", "*", "critical"))
        try:
            account = self._exchange.account()
            own = set(self._symbols) | {r.symbol for r in self._manager.working_stops()}
            symbols = own if self._shared else own | set(account.positions)
            exchange_open = {
                s: [*self._exchange.open_orders(s), *self._exchange.open_stops(s)]
                for s in sorted(symbols)
            }
            steps = {s: self._exchange.filters(s).step_size for s in sorted(symbols)}
        except ExchangeError as exc:
            report.issues.append(
                Issue("ACCOUNT_QUERY_FAILED", "*", "critical", {"error": str(exc)})
            )
            report.state = "DEGRADED"
            self._log(report, [])
            return report

        records = self._store.all()
        found = inspect_futures(
            positions={s: p for s, p in account.positions.items() if s in symbols},
            expected=expected_positions(records, self._store.adjustments()),
            local_stops=self._manager.working_stops(),
            exchange_open=exchange_open,
            known_ids={r.client_order_id for r in records},
            step_sizes=steps,
            require_stop=self._require_stop,
        )
        report.issues.extend(found)
        halted = False
        for symbol in sorted({i.symbol for i in found}):
            kinds = {i.kind: i for i in found if i.symbol == symbol}
            position = account.positions.get(symbol)
            if "UNTRACKED_ORDER" in kinds:
                # 우리가 걸지 않은 주문이 있다 = 다른 주체(수동 조작 등)가 있다.
                # 자동으로 스톱을 걸거나 정리하면 그 주체와 부딪친다 — 예: 모르는 스톱이 이미
                # 보호 중인데 우리 스톱이 -4130 으로 막혀 멀쩡한 포지션을 비상 청산하게 된다.
                # 사람 확인으로 넘긴다.
                report.actions.append(f"{symbol}: 모르는 주문이 있어 자동 조치하지 않음")
                continue
            for kind in ("STALE_STOP", "STOP_WRONG_SIDE"):
                for issue in (i for i in found if i.symbol == symbol and i.kind == kind):
                    cid = issue.detail["client_order_id"]
                    result = self._manager.cancel(cid)
                    report.actions.append(f"{symbol}: {kind} 스톱 취소 {cid} → {result.state}")
            if "ORPHAN_POSITION" in kinds and position is not None:
                if self._orphan_policy == "flatten":
                    done = self._close(
                        decision_ms,
                        symbol,
                        position,
                        report,
                        reason="원인 불명 정리",
                        capped=self._orphan_capped,
                    )
                    if done:
                        self._store.add_adjustment(
                            self._clock(),
                            symbol,
                            position.quantity,
                            "orphan: flatten 전 외부 포지션",
                        )
                    continue
                self._store.add_adjustment(
                    self._clock(), symbol, position.quantity, "orphan: hold_with_stop 입양"
                )
                report.actions.append(f"{symbol}: 원인 불명 포지션 입양 (hold_with_stop)")
            unprotected = "UNPROTECTED" in kinds or "STOP_WRONG_SIDE" in kinds
            if (
                unprotected
                and position is not None
                and self._require_stop
                and not self._protect(decision_ms, symbol, position, stop_price_for, report)
            ):
                self._close(decision_ms, symbol, position, report, reason="보호 실패 비상 청산")
                halted = True
        if halted:
            report.state = "HALTED"
        elif any(i.kind in _MANUAL or i.kind == "UNRESOLVED_ORDERS" for i in report.issues):
            report.state = "DEGRADED"
        self._log(report, found)
        return report

    # ------------------------------------------------------------ 조치

    def _next_attempt(self, decision_ms: int, symbol: str, purpose: str, side: str) -> int:
        attempt = 0
        while self._store.get(
            futures_client_order_id(self._manager.tag, decision_ms, symbol, purpose, side, attempt)
        ):
            attempt += 1
        return attempt

    def _protect(
        self,
        decision_ms: int,
        symbol: str,
        position: FuturesPosition,
        stop_price_for: StopPriceFor,
        report: RecoveryReport,
    ) -> bool:
        side = _closing_side(position.quantity)
        start = self._clock()
        for _ in range(self._attempts):
            if self._clock() - start > self._window_ms:
                break
            price = stop_price_for(symbol, position)
            if price is None:
                report.actions.append(f"{symbol}: 보호 스톱 가격을 정할 수 없음")
                break
            attempt = self._next_attempt(decision_ms, symbol, "PROTECT", side)
            record = self._manager.place_protective_stop(
                decision_ms=decision_ms,
                symbol=symbol,
                side=side,
                stop_price=price,
                attempt=attempt,
            )
            report.actions.append(f"{symbol}: 보호 스톱 {price} (시도 {attempt}) → {record.state}")
            if record.is_working_stop:
                return True
        return False

    def _close(
        self,
        decision_ms: int,
        symbol: str,
        position: FuturesPosition,
        report: RecoveryReport,
        *,
        reason: str,
        capped: bool = False,
    ) -> bool:
        """포지션을 닫는다. ``capped`` 면 가격 한도 지정가 IOC(일반 정리), 아니면 시장가 —
        시장가는 보호 실패 비상 청산(TRADING_CORE 시장가 금지의 예외 2)에만 쓴다."""
        side = _closing_side(position.quantity)
        attempt = self._next_attempt(decision_ms, symbol, "CLOSE", side)
        if capped:
            result = self._manager.submit_capped(
                decision_ms=decision_ms,
                symbol=symbol,
                side=side,
                quantity=abs(position.quantity),
                purpose="CLOSE",
                first_attempt=attempt,
            )
            done, state = result.done, f"{result.last_state}, {result.stop_reason}"
        else:
            record = self._manager.submit_market(
                decision_ms=decision_ms,
                symbol=symbol,
                side=side,
                quantity=abs(position.quantity),
                purpose="CLOSE",
                attempt=attempt,
            )
            done, state = record.state == "FILLED", record.state
        # 청산이 끝나지 않았으면 "청산 완료"로 적지 않는다
        verdict = "청산 완료" if done else f"청산 미완료 ({state}) — 계속 대조 필요"
        report.actions.append(f"{symbol}: {reason} {abs(position.quantity)} → {verdict}")
        return done

    def _log(self, report: RecoveryReport, found: Sequence[Issue]) -> None:
        now = self._clock()
        for issue in report.issues:
            self._events.insert(
                RiskEvent(
                    now,
                    self._mode,
                    issue.severity,
                    f"futures.{issue.kind}",
                    {"symbol": issue.symbol, **issue.detail},
                )
            )
        if report.actions or report.state != "RUNNING":
            self._events.insert(
                RiskEvent(
                    now,
                    self._mode,
                    "critical" if report.state != "RUNNING" else "info",
                    "futures.RECOVERY",
                    {"state": report.state, "issues": len(report.issues), "found": len(found)},
                    "; ".join(report.actions) or None,
                )
            )
