"""T/v2 현물 실거래 1단계 실행기 (Phase 8 준비, docs/research/57). **지금은 데모 모드에만 연결.**

Paper 실행기(:mod:`paper_t2`)와 **같은 판단**을 하되, 가상 원장 대신 **거래소 실제 잔고**로
크기를 정하고 지정가 L240(42 §7)으로 체결한다. 실계좌 현물 주문 어댑터는 아직 없다 —
Phase 8 승인 뒤 그것 하나만 더한다.

하루 한 번 (:meth:`LiveT2Runner.run_once`):

0. **복구 먼저.** 이 실행기의 해소되지 않은 주문(DB)을 거래소 조회로 맞춘다.
   아직 걸려 있는 지정가는 남은 대기 시간만큼 기다리고, 대기 시간이 지났으면
   취소 뒤 남은 수량을 시장가로 추격한다.
   조회가 안 되는 주문이 남으면 그날은 새 주문을 내지 않는다(DEGRADED, fail-closed).
1. 결정 시각 d = 방금 마감된 일봉. d 기록이 이미 있으면 끝(멱등).
2. 긴급 정지(``data/HALT``)면 새 주문 없이 d 목표만 기록한다(HALTED).
   위험 축소도 하지 않는다 — 사람이 판단한다.
3. d−1 기록이 없으면 체결하지 않는다(NO_PRIOR_DECISION — 오래된 목표 금지, Paper 와 같다).
4. d−1 목표를 실제 잔고로 계획한다(밴드 5%p, 매도 먼저, 매수는 가진 현금 안에서).
   **계좌 낙폭 정지(문서 85 §11 A2, 2026-10-02 사용자 결정):** 고점 대비 30% 에 닿으면 그때부터
   **새 매수를 내지 않는다**(DD_STOP). T/v2 자체의 추세 매도는 그대로 낸다 — 위험을 줄이는 쪽이라서.
   강제 청산은 없고 **자동 해제도 없다** — 사람이 원인을 적고 해제해야 다시 산다
   (:meth:`LiveT2Runner.release_drawdown`). 해제하면 고점을 그때부터 다시 잰다.
   백테스트 최악 15.9% 라 과거 경로에서는 닿지 않는다.
5. d 기록을 **먼저** 남기고(선기록), 주문마다 PENDING 을 커밋한 뒤 post-only 를 건다.
6. 대기(``poll_s``) → 체결 / 제한 시간 뒤 취소·시장가 추격. 체결 내역에서 평균가·수수료를 읽는다.

:meth:`LiveT2Runner.plan` 은 같은 판단을 **읽기만** 해서 보여 준다(실거래 첫날 "계획만", 85 §4) —
주문·기록·알림·복구 조회 모두 없다.

주문 ID: 지정가 ``<tag>-<d−1>-<종목>-<B|S>``, 추격 ``…m``. 같은 의도는 몇 번을 실행해도 주문 1 건.
결과를 모르는 전송(UNKNOWN)은 조회로만 해소한다.
거래소에 없다고 확인되면 NOT_PLACED 로 닫고 **다시 보내지 않는다**.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Protocol

from auto_trading.config.schema import SpotTrendConfig
from auto_trading.core.enums import Timeframe
from auto_trading.core.errors import ExchangeError, OrderRejectedError
from auto_trading.core.money import floor_to_step
from auto_trading.core.types import Candle
from auto_trading.exchange.spot import OrderStatus, SpotFilters, SpotOrderResult
from auto_trading.execution.demo_limit import DemoSpotVenue
from auto_trading.execution.spot_orders import client_order_id
from auto_trading.ops.kill_switch import halted_reason
from auto_trading.storage.repositories.paper_days import PaperDay, PaperDayRepository
from auto_trading.storage.repositories.risk_events import RiskEvent, RiskEventRepository
from auto_trading.storage.repositories.spot_orders import SpotOrderRecord, SpotOrderRepository
from auto_trading.strategy.spot_trend import (
    SpotSide,
    SpotTarget,
    plan_spot_orders,
    spot_trend_targets,
)

__all__ = [
    "DD_RELEASE",
    "DD_STOP",
    "DayPlan",
    "Drawdown",
    "LiveOptions",
    "LiveSpotVenue",
    "LiveT2Runner",
    "OrderReport",
    "SignalSource",
    "TradeReason",
]

DD_STOP = "t2_dd_stop"
"""``risk_events.kind`` — 계좌 낙폭 정지가 걸린 순간 (detail.strategy_id 로 구분)."""
DD_RELEASE = "t2_dd_release"
"""``risk_events.kind`` — 사람이 원인을 적고 해제한 순간."""

_DAY_MS = Timeframe.D1.ms
_QUOTE = "USDT"
_BPS = Decimal(10_000)
_STATE = {
    OrderStatus.NEW: "SUBMITTED",
    OrderStatus.PARTIALLY_FILLED: "PARTIALLY_FILLED",
    OrderStatus.FILLED: "FILLED",
    OrderStatus.CANCELED: "CANCELED",
    OrderStatus.REJECTED: "REJECTED",
    OrderStatus.EXPIRED: "EXPIRED",
}
_DONE = frozenset({"FILLED", "CANCELED", "EXPIRED", "REJECTED", "NOT_PLACED"})
"""대기를 끝낼 주문 상태 (기록 기준)."""


class LiveSpotVenue(DemoSpotVenue, Protocol):
    def balances(self) -> dict[str, Decimal]: ...

    def last_price(self, symbol: str) -> Decimal: ...

    def filters(self, symbol: str) -> SpotFilters: ...


class SignalSource(Protocol):
    def closed_daily(self, symbol: str, as_of_ms: int, limit: int = 200) -> list[Candle]: ...


@dataclass(frozen=True, slots=True)
class LiveOptions:
    mode: str
    """``spot_orders.mode`` — 지금은 'demo'. 실거래는 Phase 8 승인 뒤 'live'."""
    strategy_id: str
    tag: str
    timeout_min: int = 240
    poll_s: float = 30.0
    post_retries: int = 3
    fee_rate: Decimal = Decimal("0.001")
    """매수 수량을 현금 안에 맞출 때만 쓴다. 실제 수수료는 체결 내역에서 읽는다."""
    dd_stop: Decimal | None = Decimal("0.30")
    """계좌 낙폭 정지선 (TRADING_CORE 누적 DD 30%). None 이면 쓰지 않는다."""


@dataclass(frozen=True, slots=True)
class Drawdown:
    """계좌 낙폭. 고점 = 마지막 해제 뒤 이 실행기 기록의 평가액과 지금 평가액 중 최대."""

    peak: Decimal
    equity: Decimal
    limit: Decimal | None
    latched: bool
    """이미 정지 중 (해제 뒤 정지 기록이 있다)."""

    @property
    def dd(self) -> Decimal:
        return 1 - self.equity / self.peak if self.peak > 0 else Decimal(0)

    @property
    def stopped(self) -> bool:
        return self.limit is not None and (self.latched or self.dd >= self.limit)

    @property
    def new(self) -> bool:
        """이번에 처음 닿았다."""
        return self.stopped and not self.latched

    def text(self) -> str:
        line = f"고점 {self.peak:,.2f} → 지금 {self.equity:,.2f} USDT (낙폭 {self.dd:.1%}"
        if self.limit is not None:
            line += f" · 정지선 {self.limit:.0%}"
        return line + ")"


@dataclass(slots=True)
class OrderReport:
    """주문 하나(지정가 + 추격)의 결과. 알림·보고용."""

    symbol: str
    side: str
    qty: Decimal
    client_id: str
    p0_mid: Decimal | None = None
    limit_price: Decimal | None = None
    posted_ms: int | None = None
    done_ms: int | None = None
    maker_qty: Decimal = Decimal(0)
    taker_qty: Decimal = Decimal(0)
    quote: Decimal = Decimal(0)
    fee_usdt: Decimal = Decimal(0)
    post_rejects: int = 0
    chased: bool = False
    resumed: bool = False
    notes: list[str] = field(default_factory=list)

    @property
    def filled(self) -> Decimal:
        return self.maker_qty + self.taker_qty

    @property
    def avg_price(self) -> Decimal | None:
        return self.quote / self.filled if self.filled else None

    def cost_bps(self) -> tuple[Decimal, Decimal] | None:
        """(중간가 대비 가격 부분, 수수료) bp. 매수 +, 매도 −."""
        avg = self.avg_price
        if avg is None or not self.p0_mid:
            return None
        sign = 1 if self.side == "BUY" else -1
        return sign * (avg - self.p0_mid) / self.p0_mid * _BPS, self.fee_usdt / self.quote * _BPS

    def as_dict(self) -> dict[str, str]:
        costs = self.cost_bps()
        wait = (
            None
            if self.posted_ms is None or self.done_ms is None
            else self.done_ms - self.posted_ms
        )
        return {
            "symbol": self.symbol,
            "side": self.side,
            "qty": str(self.qty),
            "client_id": self.client_id,
            "p0_mid": str(self.p0_mid),
            "limit_price": str(self.limit_price),
            "maker_qty": str(self.maker_qty),
            "taker_qty": str(self.taker_qty),
            "avg_price": str(self.avg_price),
            "fee_usdt": str(self.fee_usdt),
            "cost_price_bps": "None" if costs is None else f"{costs[0]:.3f}",
            "cost_fee_bps": "None" if costs is None else f"{costs[1]:.3f}",
            "wait_ms": str(wait),
            "post_rejects": str(self.post_rejects),
            "chased": str(self.chased),
            "resumed": str(self.resumed),
            "notes": " | ".join(self.notes),
        }


@dataclass(frozen=True, slots=True)
class TradeReason:
    """알림용: 이 종목을 왜 사고/파는지 (T/v2 신호 그대로)."""

    ups: int
    """20·60·120 일 수익률 중 양수인 수 (0~3)."""
    target: Decimal
    current: Decimal
    vol: Decimal
    cap: Decimal
    target_vol: Decimal

    def action(self, side: SpotSide) -> str:
        if side is SpotSide.BUY:
            return "롱 진입(매수)" if self.current <= 0 else "롱 늘림(매수)"
        return "청산(매도) → 현금" if self.target <= 0 else "롱 줄임(매도)"

    @property
    def why(self) -> str:
        trend = f"20·60·120일 추세 {self.ups}/3 상승"
        if self.target <= 0:
            return f"{trend} → 상승 추세가 아니라 현금 (현재 {self.current:.1%})"
        size = (
            f"상한 {self.cap:.0%}"
            if self.target >= self.cap
            else f"{self.target_vol:.0%} ÷ 60일 변동성 {self.vol:.0%}"
        )
        strength = "상승 추세" if self.ups == 3 else "상승 우세"
        return (
            f"{trend} → {strength}. 목표 비중 {self.target:.1%} ({size}) ← 현재 {self.current:.1%}"
        )


Planned = tuple[SpotSide, str, Decimal, Decimal]
"""(방향, 종목, 수량, 목표 비중)."""


@dataclass(frozen=True, slots=True)
class DayPlan:
    """그날의 판단. :meth:`LiveT2Runner.run_once` 는 기록·주문하고 ``plan`` 은 보여만 준다."""

    day: PaperDay
    """d 기록 (아직 저장하지 않은 것)."""
    prev: PaperDay | None
    planned: list[Planned]
    reasons: dict[str, TradeReason]
    drawdown: Drawdown
    prices: dict[str, Decimal]

    def text(self, mode: str) -> str:
        d = self.day
        when = _utc_date(d.decision_ms)
        lines = [
            f"[{mode}·현물 T/v2 · 계획만 — 주문 0건] 결정일 {when} · 상태 {d.status}",
            "보유: " + " · ".join(f"{a} {q}" for a, q in d.holdings.items())
            if d.holdings
            else "보유: 없음",
            f"평가액 {d.equity:,.2f} USDT · {self.drawdown.text()}",
        ]
        if d.note:
            lines.append(f"메모: {d.note}")
        if self.prev is not None:
            lines.append(
                f"체결할 목표 ({_utc_date(self.prev.decision_ms)} 결정): "
                + " · ".join(f"{s} {_pct(w)}" for s, w in sorted(self.prev.targets.items()))
            )
        if self.planned:
            lines.append("주문 예정 (매도 먼저 · 지정가 = 보낼 때 1호가 · 4시간 뒤 시장가 추격):")
            for side, symbol, qty, w in self.planned:
                why = self.reasons.get(symbol)
                value = qty * self.prices[symbol]
                lines.append(
                    f"  {symbol} {why.action(side) if why else side.value} {qty}"
                    f" (≈ {value:,.2f} USDT) → 목표 {w:.1%}"
                )
                if why:
                    lines.append(f"    이유: {why.why}")
        elif d.status in ("OK", "DD_STOP"):
            lines.append("주문 예정 없음 — 목표와 현재 비중 차이가 밴드 안 (또는 정지로 매수 제외)")
        lines.append(
            "다음 결정 목표: " + " · ".join(f"{s} {_pct(w)}" for s, w in sorted(d.targets.items()))
        )
        return "\n".join(lines)


def _utc_date(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m-%d")


def _pct(w: str | None) -> str:
    return "—" if w is None else f"{Decimal(w):.1%}"


@dataclass(slots=True)
class _Working:
    record: SpotOrderRecord
    report: OrderReport
    deadline_ms: int


class LiveT2Runner:
    def __init__(
        self,
        venue: LiveSpotVenue,
        signals: SignalSource,
        orders: SpotOrderRepository,
        days: PaperDayRepository,
        *,
        options: LiveOptions,
        clock: Callable[[], int],
        sleep: Callable[[float], None],
        halt_path: Path,
        notify: Callable[[str], None],
        risk_events: RiskEventRepository,
        cfg: SpotTrendConfig | None = None,
    ) -> None:
        self._venue = venue
        self._signals = signals
        self._orders = orders
        self._days = days
        self._risk = risk_events
        self._opt = options
        self._clock = clock
        self._sleep = sleep
        self._halt = halt_path
        self._notify = notify
        self._cfg = cfg if cfg is not None else SpotTrendConfig()
        self.reports: list[OrderReport] = []

    # ------------------------------------------------------------ 공통

    def _mine(self, r: SpotOrderRecord) -> bool:
        return r.mode == self._opt.mode and r.strategy_id == self._opt.strategy_id

    def _unresolved(self) -> list[SpotOrderRecord]:
        return [r for r in self._orders.unresolved() if self._mine(r)]

    def _dd_events(self) -> tuple[RiskEvent | None, RiskEvent | None]:
        """(마지막 해제, 그 뒤의 첫 정지) — 이 실행기(strategy_id) 것만."""
        events = [
            e
            for e in self._risk.of_kinds(self._opt.mode, (DD_STOP, DD_RELEASE))
            if e.detail.get("strategy_id") == self._opt.strategy_id
        ]
        release = next((e for e in events if e.kind == DD_RELEASE), None)
        since = release.ts_ms if release else -1
        stop = next((e for e in events if e.kind == DD_STOP and e.ts_ms > since), None)
        return release, stop

    def drawdown(self, equity: Decimal) -> Drawdown:
        release, stop = self._dd_events()
        since = release.ts_ms if release else -1
        history = [
            d.equity
            for d in self._days.recent(self._opt.strategy_id, 100_000)
            if d.created_ms > since
        ]
        peak = max([equity, *history])
        return Drawdown(peak, equity, self._opt.dd_stop, latched=stop is not None)

    def release_drawdown(self, reason: str) -> None:
        """사람이 원인을 적고 낙폭 정지를 푼다. 고점은 지금부터 다시 잰다."""
        if not reason.strip():
            raise ValueError("해제 사유를 적어야 한다")
        _, stop = self._dd_events()
        if stop is None:
            raise ValueError("정지 중이 아니다 — 해제할 것이 없다")
        self._risk.insert(
            RiskEvent(
                self._clock(),
                self._opt.mode,
                "warn",
                DD_RELEASE,
                {"strategy_id": self._opt.strategy_id, "reason": reason, "stop": stop.detail},
                "새 매수 다시 허용 · 고점 재설정",
            )
        )
        self._notify(f"[{self._opt.mode}·현물 T/v2] 낙폭 정지 해제 — 사유: {reason}")

    def _mid(self, symbol: str) -> tuple[Decimal, Decimal, Decimal]:
        bids, asks = self._venue.order_book(symbol, limit=5)
        bid, ask = bids[0][0], asks[0][0]
        return bid, ask, (bid + ask) / 2

    def _save(self, record: SpotOrderRecord, result: SpotOrderResult) -> SpotOrderRecord:
        updated = record.with_state(
            _STATE[result.status],
            self._clock(),
            exchange_order_id=result.exchange_order_id,
            filled_qty=result.executed_qty,
            quote_qty=result.quote_qty,
            error=None,
        )
        self._orders.update(updated)
        return updated

    def _collect(self, record: SpotOrderRecord, report: OrderReport) -> SpotOrderRecord:
        """체결 내역으로 maker/taker·평균가·수수료를 채우고 기록의 수수료를 갱신한다."""
        if not record.exchange_order_id or not record.filled_qty:
            return record
        fee_usdt = Decimal(0)
        try:
            fills = self._venue.fills(record.symbol, record.exchange_order_id)
        except ExchangeError as exc:  # 체결 사실은 주문 기록에 있다 — 지표만 빠진다
            report.notes.append(f"체결 내역 조회 실패: {exc}")
            return record
        for f in fills:
            if f.is_maker:
                report.maker_qty += f.qty
            else:
                report.taker_qty += f.qty
            report.quote += f.quote_qty
            if f.commission_asset == _QUOTE:
                fee_usdt += f.commission
            elif record.symbol.startswith(f.commission_asset):
                fee_usdt += f.commission * f.price
            else:
                report.notes.append(f"수수료 자산 {f.commission_asset} — USDT 환산 안 함")
        report.fee_usdt += fee_usdt
        updated = record.with_state(record.state, self._clock(), fee=fee_usdt, fee_asset=_QUOTE)
        self._orders.update(updated)
        return updated

    # ------------------------------------------------------------ 주문

    def _new_record(
        self, cid: str, decision_ms: int, symbol: str, side: str, qty: Decimal, w: Decimal
    ) -> SpotOrderRecord:
        now = self._clock()
        record = SpotOrderRecord(
            client_order_id=cid,
            mode=self._opt.mode,
            strategy_id=self._opt.strategy_id,
            decision_ms=decision_ms,
            symbol=symbol,
            side=side,
            qty=qty,
            target_weight=w,
            state="PENDING",
            created_ms=now,
            updated_ms=now,
        )
        self._orders.insert(record)
        return record

    def _lookup(self, record: SpotOrderRecord) -> SpotOrderRecord | None:
        """결과를 모르는 주문을 조회로 해소한다. 조회 실패면 None (해소 안 됨)."""
        try:
            result = self._venue.get_order(record.symbol, record.client_order_id)
        except ExchangeError as exc:
            kept = record.with_state(record.state, self._clock(), error=str(exc))
            self._orders.update(kept)
            return None
        if result is None:
            closed = record.with_state(
                "NOT_PLACED", self._clock(), error="거래소에 없음 (조회로 확인)"
            )
            self._orders.update(closed)
            return closed
        return self._save(record, result)

    def _post(self, record: SpotOrderRecord, report: OrderReport) -> SpotOrderRecord:
        for _ in range(self._opt.post_retries):
            bid, ask, mid = self._mid(record.symbol)
            price = bid if record.side == "BUY" else ask
            try:
                result = self._venue.place_limit_maker(
                    record.symbol, record.side, record.qty, price, record.client_order_id
                )
            except OrderRejectedError:
                report.post_rejects += 1
                continue
            except ExchangeError as exc:
                unknown = record.with_state("UNKNOWN", self._clock(), error=str(exc))
                self._orders.update(unknown)
                report.p0_mid, report.limit_price, report.posted_ms = mid, price, self._clock()
                return self._lookup(unknown) or unknown
            report.p0_mid, report.limit_price, report.posted_ms = mid, price, self._clock()
            return self._save(record, result)
        _, _, mid = self._mid(record.symbol)
        report.p0_mid, report.posted_ms = mid, self._clock()
        rejected = record.with_state(
            "REJECTED", self._clock(), error=f"post-only 가 {report.post_rejects} 번 거절"
        )
        self._orders.update(rejected)
        return rejected

    def _chase(
        self,
        parent: SpotOrderRecord,
        remaining: Decimal,
        report: OrderReport,
        step: Decimal,
        min_notional: Decimal,
    ) -> None:
        cid = parent.client_order_id + "m"
        existing = self._orders.get(cid)
        if existing is not None:  # 이미 추격했다 (재시작) — 결과만 모은다
            if existing.state not in ("FILLED", "NOT_PLACED", "REJECTED"):
                existing = self._lookup(existing) or existing
            self._collect(existing, report)
            report.chased = True
            return
        qty = floor_to_step(remaining, step)
        _, _, mid = self._mid(parent.symbol)
        if qty <= 0 or qty * mid < min_notional:
            report.notes.append(f"남은 수량 {remaining} 이 최소 주문 미만 — 추격 안 함")
            return
        record = self._new_record(
            cid, parent.decision_ms, parent.symbol, parent.side, qty, parent.target_weight
        )
        try:
            result = self._venue.place_market_order(parent.symbol, parent.side, qty, cid)
            record = self._save(record, result)
        except OrderRejectedError as exc:
            self._orders.update(record.with_state("REJECTED", self._clock(), error=str(exc)))
            report.notes.append(f"추격 시장가 거절: {exc}")
            return
        except ExchangeError as exc:
            unknown = record.with_state("UNKNOWN", self._clock(), error=str(exc))
            self._orders.update(unknown)
            record = self._lookup(unknown) or unknown
        if record.state in ("SUBMITTED", "PARTIALLY_FILLED"):
            record = self._lookup(record) or record
        self._collect(record, report)
        report.chased = True

    def _finish(self, w: _Working, filters: Mapping[str, SpotFilters], *, expired: bool) -> None:
        record, report = w.record, w.report
        if expired and record.state in ("SUBMITTED", "PARTIALLY_FILLED"):
            with contextlib.suppress(OrderRejectedError):  # 방금 체결됐을 수 있다 — 조회가 판단
                self._venue.cancel_order(record.symbol, record.client_order_id)
            record = self._lookup(record) or record
        record = self._collect(record, report)
        remaining = record.qty - record.filled_qty
        if record.state in ("CANCELED", "EXPIRED", "REJECTED") and remaining > 0:
            f = filters[record.symbol]
            self._chase(record, remaining, report, f.step_size, f.min_notional)
        report.done_ms = self._clock()
        self.reports.append(report)
        self._notify(self._done_text(report))

    def _wait(self, working: list[_Working], filters: Mapping[str, SpotFilters]) -> None:
        while working:
            now = self._clock()
            still: list[_Working] = []
            for w in working:
                record = self._lookup(w.record) or w.record
                w.record = record
                if record.state in _DONE:
                    self._finish(w, filters, expired=False)
                elif now >= w.deadline_ms:
                    self._finish(w, filters, expired=True)
                else:
                    still.append(w)
            working = still
            if working:
                self._sleep(self._opt.poll_s)

    # ------------------------------------------------------------ 알림 문구

    def _done_text(self, r: OrderReport) -> str:
        side = "매수" if r.side == "BUY" else "매도"
        head = f"[{self._opt.mode}·현물 T/v2] {r.symbol} {side}"
        if not r.filled:
            return f"{head} 체결 없음 — {' | '.join(r.notes) or '확인 필요'}"
        costs = r.cost_bps()
        cost = (
            "" if costs is None else f" · 중간가 대비 {costs[0]:+.1f}bp + 수수료 {costs[1]:.1f}bp"
        )
        wait = (
            ""
            if r.posted_ms is None or r.done_ms is None
            else f" · 대기 {(r.done_ms - r.posted_ms) / 60_000:.0f}분"
        )
        how = (
            f"지정가 {r.maker_qty} + 시장가 추격 {r.taker_qty}"
            if r.chased
            else f"지정가 체결 {r.filled}"
        )
        return f"{head} {how} @{r.avg_price:.2f}{wait}{cost}"

    # ------------------------------------------------------------ 복구

    def recover(self, filters: Mapping[str, SpotFilters]) -> bool:
        """해소 안 된 주문을 맞춘다. 전부 해소되면 True."""
        working: list[_Working] = []
        for record in self._unresolved():
            if record.client_order_id.endswith("m"):  # 추격 시장가 — 조회로 상태만 맞춘다
                self._lookup(record)
                continue
            report = OrderReport(
                record.symbol, record.side, record.qty, record.client_order_id, resumed=True
            )
            report.posted_ms = record.created_ms
            resolved = self._lookup(record)
            if resolved is None:
                continue
            deadline = record.created_ms + self._opt.timeout_min * 60_000
            if resolved.state == "NOT_PLACED":
                report.notes.append("재시작: 거래소에 없음 — 다시 보내지 않음")
                self.reports.append(report)
                self._notify(self._done_text(report))
                continue
            working.append(_Working(resolved, report, deadline))
        if working:
            self._notify(
                f"[{self._opt.mode}·현물 T/v2] 재시작: 걸려 있던 주문 {len(working)}건을 이어받음"
            )
            self._wait(working, filters)
        return not self._unresolved()

    # ------------------------------------------------------------ 하루

    def _decide(self, decision: int, filters: Mapping[str, SpotFilters], resolved: bool) -> DayPlan:
        """d 의 판단 — 읽기만 한다 (기록·주문·알림 없음)."""
        symbols = self._cfg.symbols
        daily = {s: self._signals.closed_daily(s, decision) for s in symbols}
        new_targets = spot_trend_targets(daily, as_of_ms=decision, cfg=self._cfg)
        balances = self._venue.balances()
        prices = {s: self._venue.last_price(s) for s in symbols}
        holdings = {s: balances.get(s.removesuffix(_QUOTE), Decimal(0)) for s in symbols}
        cash = balances.get(_QUOTE, Decimal(0))
        equity = cash + sum(holdings[s] * prices[s] for s in symbols)
        prev = self._days.get(self._opt.strategy_id, decision - _DAY_MS)
        halt = halted_reason(self._halt)
        dd = self.drawdown(equity)
        status, note = "OK", None
        intents = []
        if halt is not None:
            status, note = "HALTED", f"긴급 정지: {halt}"
        elif not resolved:
            status, note = "DEGRADED", "해소되지 않은 주문이 있어 새 주문을 내지 않음"
        elif prev is None:
            status, note = "NO_PRIOR_DECISION", "전날 결정 기록 없음 — 오래된 목표는 체결하지 않음"
        else:
            prev_targets = {
                s: SpotTarget(s, None if w is None else Decimal(w)) for s, w in prev.targets.items()
            }
            intents = plan_spot_orders(
                prev_targets,
                holdings=holdings,
                prices=prices,
                equity=equity,
                cfg=self._cfg,
                step_size={s: f.step_size for s, f in filters.items()},
                min_notional={s: f.min_notional for s, f in filters.items()},
            )
            if dd.stopped:
                intents = [i for i in intents if i.side is SpotSide.SELL]
                status = "DD_STOP"
                note = f"계좌 낙폭 정지 — 새 매수 금지, 추세 매도만. {dd.text()}"
        planned: list[Planned] = []
        for intent in intents:  # 매도 먼저
            qty = intent.quantity
            f = filters[intent.symbol]
            if intent.side is SpotSide.BUY:
                price = prices[intent.symbol]
                qty = min(
                    qty, floor_to_step(cash / (price * (1 + self._opt.fee_rate)), f.step_size)
                )
                if qty * price < f.min_notional:
                    continue
                cash -= qty * price * (1 + self._opt.fee_rate)
            planned.append((intent.side, intent.symbol, qty, intent.target_weight))
        day = PaperDay(
            strategy_id=self._opt.strategy_id,
            decision_ms=decision,
            status=status,
            equity=equity,
            holdings={
                a: str(q)
                for a, q in sorted(balances.items())
                if q and (a == _QUOTE or f"{a}{_QUOTE}" in symbols)
            },
            prices={s: str(p) for s, p in prices.items()},
            targets={
                s: None if t.weight is None else str(t.weight) for s, t in new_targets.items()
            },
            executed=[
                {"symbol": s, "side": side.value, "qty": str(q), "target": str(w)}
                for side, s, q, w in planned
            ],
            note=note,
            created_ms=self._clock(),
        )
        reasons = (
            {}
            if prev is None
            else self._reasons(daily, decision - _DAY_MS, holdings, prices, equity)
        )
        return DayPlan(day, prev, planned, reasons, dd, prices)

    def plan(self, now_ms: int) -> DayPlan | None:
        """오늘 실행하면 무엇을 할지 — **읽기만**. 이미 실행한 날이면 None.

        복구 조회도 하지 않는다: 해소 안 된 주문이 DB 에 있으면 DEGRADED 로 보여 준다.
        """
        decision = now_ms - now_ms % _DAY_MS - 1
        if self._days.get(self._opt.strategy_id, decision) is not None:
            return None
        filters = {s: self._venue.filters(s) for s in self._cfg.symbols}
        return self._decide(decision, filters, resolved=not self._unresolved())

    def run_once(self, now_ms: int) -> PaperDay:
        decision = now_ms - now_ms % _DAY_MS - 1
        filters = {s: self._venue.filters(s) for s in self._cfg.symbols}
        resolved = self.recover(filters)
        done = self._days.get(self._opt.strategy_id, decision)
        if done is not None:
            return done
        plan = self._decide(decision, filters, resolved)
        day, prev, planned, reasons = plan.day, plan.prev, plan.planned, plan.reasons
        status, note = day.status, day.note
        if plan.drawdown.new:
            self._risk.insert(
                RiskEvent(
                    self._clock(),
                    self._opt.mode,
                    "critical",
                    DD_STOP,
                    {
                        "strategy_id": self._opt.strategy_id,
                        "decision_ms": decision,
                        "peak": str(plan.drawdown.peak),
                        "equity": str(plan.drawdown.equity),
                        "dd": str(plan.drawdown.dd),
                    },
                    "새 매수 금지 (추세 매도는 그대로, 자동 해제 없음)",
                )
            )
            self._notify(
                f"🚨 [{self._opt.mode}·현물 T/v2] 계좌 낙폭 정지 — {plan.drawdown.text()}\n"
                "지금부터 새 매수를 내지 않는다. T/v2 추세 매도는 그대로, 강제 청산은 없다.\n"
                '자동 해제 없음 — 원인을 확인한 뒤 해제 명령(release-dd "사유")으로만 다시 산다.'
            )
        self._days.insert(day)  # 선기록 — 주문보다 먼저
        if note is not None and status != "NO_PRIOR_DECISION":
            self._notify(f"[{self._opt.mode}·현물 T/v2] {status}: {note}")
        if prev is None:
            return day
        if not planned:
            if status == "OK":
                self._notify(
                    f"[{self._opt.mode}·현물 T/v2] 오늘 주문 없음 — 목표와 현재 비중 차이가 "
                    f"밴드 {self._cfg.band:.0%}p 안\n"
                    + "\n".join(f"{s}: {r.why}" for s, r in sorted(reasons.items()))
                )
            return day
        working: list[_Working] = []
        for side, symbol, qty, w in planned:
            cid = client_order_id(self._opt.tag, prev.decision_ms, symbol, side.value)
            record = self._new_record(cid, prev.decision_ms, symbol, side.value, qty, w)
            report = OrderReport(symbol, side.value, qty, cid)
            record = self._post(record, report)
            if record.state in ("SUBMITTED", "PARTIALLY_FILLED", "FILLED"):
                why = reasons.get(symbol)
                action = why.action(side) if why else side.value
                self._notify(
                    f"[{self._opt.mode}·현물 T/v2] {symbol} {action} {qty} "
                    f"@{report.limit_price} 지정가 예약 (최대 {self._opt.timeout_min // 60}시간)\n"
                    f"이유: {why.why if why else ''}\n"
                    "(T/v2 는 현물 롱 전용 — 상승 추세면 사고, 꺾이면 팔아 현금으로. 숏 없음)"
                )
            deadline = (report.posted_ms or self._clock()) + self._opt.timeout_min * 60_000
            if record.state in ("REJECTED",):
                self._finish(_Working(record, report, deadline), filters, expired=False)
            elif record.state in ("UNKNOWN", "PENDING"):
                report.notes.append("전송 결과를 모름 — 다음 실행이 조회로 해소")
                self.reports.append(report)
                self._notify(self._done_text(report))
            elif record.state == "NOT_PLACED":
                report.notes.append("전송 결과를 몰랐고 거래소에 없음 — 다시 보내지 않음")
                self.reports.append(report)
                self._notify(self._done_text(report))
            else:
                working.append(_Working(record, report, deadline))
        self._wait(working, filters)
        return day

    def _reasons(
        self,
        daily: Mapping[str, Sequence[Candle]],
        prev_ms: int,
        holdings: Mapping[str, Decimal],
        prices: Mapping[str, Decimal],
        equity: Decimal,
    ) -> dict[str, TradeReason]:
        out = {}
        for s, t in spot_trend_targets(daily, as_of_ms=prev_ms, cfg=self._cfg).items():
            if t.weight is None or t.direction is None or t.vol_annual is None:
                continue
            current = holdings[s] * prices[s] / equity if equity else Decimal(0)
            out[s] = TradeReason(
                ups=round((t.direction * 3 + 3) / 2),
                target=t.weight,
                current=current,
                vol=t.vol_annual,
                cap=self._cfg.max_weight,
                target_vol=self._cfg.target_vol,
            )
        return out
