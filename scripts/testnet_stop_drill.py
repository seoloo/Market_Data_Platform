"""Phase 7 스톱 발동 훈련 — 가격이 실제로 보호 스톱에 닿을 때를 테스트넷에서 확인한다 (26 §3).

**가짜 돈만 쓴다.** 매일 훈련(:mod:`testnet_drill`)과 같은 테스트넷 키·어댑터·DB를 쓰되
종목을 **ETHUSDT** 로 나눠 서로의 포지션을 건드리지 않는다. 매일 자동 실행(21시대 KST) 근처에는
시작하지 않는다. 필요할 때 손으로 돌리는 선택형 훈련이며 스케줄에 넣지 않는다.

순서 (각 단계 PASS/FAIL):

1. 계좌 준비: one-way, 교차 증거금·레버리지 3배, ETH 포지션·스톱 정리
2. 최소 명목의 약 1.3배 ETH 롱
3. 현재 mark 가격 바로 아래(기본 0.15%)에 closePosition 보호 스톱 (Algo API)
4. 발동할 때까지 기다린다 (기본 최대 60분, 5초마다 조회). 스톱은 옮기지 않는다
5. 발동 뒤: 스톱 기록 FILLED · 체결 수량 = 포지션 · 체결가 기록 · 포지션 평탄 · 남은 스톱 0
   · 재시작 정합성 복구 RUNNING(스톱 체결을 로컬 기록이 반영했는지)
6. 관찰(판정 아님): 스톱가 대비 체결가 차이(bp), 스톱을 건 뒤 발동 확인까지 걸린 시간,
   발동 직전 mark

제한 시간 안에 발동하지 않으면 스톱을 취소하고 포지션을 닫은 뒤 ``NOT_TRIGGERED`` 로 끝낸다
(실패가 아니라 판정 불가 — 다시 돌린다).

사용 예::

    python scripts/testnet_stop_drill.py                  # 0.15% 아래, 최대 60분
    python scripts/testnet_stop_drill.py --offset-bps 10 --max-minutes 90
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import ROUND_UP, Decimal
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.testnet_drill import DB, LEVERAGE, REPORTS, load_testnet_credentials  # noqa: E402

from auto_trading.core.errors import ConfigError  # noqa: E402
from auto_trading.core.money import floor_to_step  # noqa: E402
from auto_trading.exchange.binance_usdm_testnet import BinanceUsdmTestnet  # noqa: E402
from auto_trading.exchange.futures import FuturesPosition  # noqa: E402
from auto_trading.execution.futures_guard import FuturesSupervisor, RecoveryReport  # noqa: E402
from auto_trading.execution.futures_orders import FuturesOrderManager  # noqa: E402
from auto_trading.storage.db import connect  # noqa: E402
from auto_trading.storage.migrations import apply_migrations  # noqa: E402
from auto_trading.storage.repositories.futures_orders import (  # noqa: E402
    FuturesOrderRecord,
    FuturesOrderRepository,
)
from auto_trading.storage.repositories.risk_events import RiskEventRepository  # noqa: E402

for _stream in (sys.stdout, sys.stderr):
    _reconfigure = getattr(_stream, "reconfigure", None)
    if _reconfigure is not None:
        _reconfigure(encoding="utf-8", errors="replace")

SYMBOL = "ETHUSDT"
TAG = "ts"
POLL_SECONDS = 5.0
DAILY_DRILL_UTC_MINUTES = (12 * 60 - 5, 12 * 60 + 30)
"""매일 자동 실행(21:00~21:20 KST = 12:00~12:20 UTC) 앞뒤로 비워 두는 구간 [11:55, 12:30) UTC.

2026-09-24 사용자 결정으로 스케줄을 아침 9시대에서 저녁 9시대로 옮겼다."""
_MINUTE_MS = 60_000
_DAY_MS = 86_400_000


def overlaps_daily_drill(start_ms: int, max_minutes: float) -> bool:
    """[start, start + 대기 + 5분 여유)가 매일 훈련 구간과 겹치는가."""
    end_ms = start_ms + int((max_minutes + 5) * _MINUTE_MS)
    lo, hi = DAILY_DRILL_UTC_MINUTES
    day = start_ms // _DAY_MS * _DAY_MS
    for base in (day, day + _DAY_MS):
        if start_ms < base + hi * _MINUTE_MS and base + lo * _MINUTE_MS < end_ms:
            return True
    return False


def stop_below(mark: Decimal, offset_bps: Decimal, tick: Decimal) -> Decimal:
    """롱 보호 스톱: mark 의 ``offset_bps`` 아래, tick 에 내림."""
    return floor_to_step(mark * (1 - offset_bps / 10_000), tick)


def slippage_bps(stop_price: Decimal, fill_price: Decimal) -> Decimal:
    """롱 스톱(매도) 기준 체결가가 스톱가보다 **불리한** 정도. 양수 = 스톱가보다 낮게 팔렸다."""
    return (stop_price - fill_price) / stop_price * 10_000


@dataclass
class StopDrill:
    api: BinanceUsdmTestnet
    store: FuturesOrderRepository
    events: RiskEventRepository
    decision: int
    offset_bps: Decimal
    max_minutes: float
    checks: list[dict[str, Any]] = field(default_factory=list)
    observations: dict[str, Any] = field(default_factory=dict)
    outcome: str = "INCOMPLETE"

    def manager(self) -> FuturesOrderManager:
        return FuturesOrderManager(
            self.api,
            self.store,
            mode="testnet",
            strategy_id="stopdrill",
            strategy_tag=TAG,
            clock=lambda: int(time.time() * 1000),
        )

    def supervisor(self, manager: FuturesOrderManager) -> FuturesSupervisor:
        return FuturesSupervisor(
            self.api,
            manager,
            self.store,
            self.events,
            mode="testnet",
            symbols=[SYMBOL],
            require_stop=True,
            orphan_policy="flatten",
            shared_account=True,  # 데모 계좌를 여러 트랙이 나눠 쓴다 (문서 59 §3)
            clock=lambda: int(time.time() * 1000),
        )

    def far_stop(self, _symbol: str, position: FuturesPosition) -> Decimal:
        """복구 중 보호 스톱이 필요할 때만 쓰는 먼 스톱(15%)."""
        mark = self.api.mark_price(SYMBOL)
        factor = Decimal("0.85") if position.quantity > 0 else Decimal("1.15")
        return floor_to_step(mark * factor, self.api.filters(SYMBOL).tick_size)

    def recover(self) -> RecoveryReport:
        return self.supervisor(self.manager()).recover(self.decision, self.far_stop)

    def position(self) -> Decimal:
        pos = self.api.account().positions.get(SYMBOL)
        return pos.quantity if pos else Decimal(0)

    def check(self, name: str, ok: bool, detail: str = "") -> bool:
        self.checks.append({"step": name, "pass": ok, "detail": detail})
        print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f" — {detail}" if detail else ""))
        return ok

    def flatten(self, m: FuturesOrderManager, purpose_attempt: int = 0) -> None:
        for stop in m.working_stops(SYMBOL):
            m.cancel(stop.client_order_id)
        qty = self.position()
        if qty:
            m.submit_market(
                decision_ms=self.decision,
                symbol=SYMBOL,
                side="SELL" if qty > 0 else "BUY",
                quantity=abs(qty),
                purpose="CLOSE",
                attempt=purpose_attempt,
            )

    def entry_qty(self) -> Decimal:
        f = self.api.filters(SYMBOL)
        raw = f.min_notional * Decimal("1.3") / self.api.mark_price(SYMBOL)
        steps = (raw / f.step_size).to_integral_value(rounding=ROUND_UP)
        return max(steps * f.step_size, f.min_qty)

    def wait_for_trigger(self, stop: FuturesOrderRecord) -> tuple[FuturesOrderRecord, int, str]:
        """발동(스톱 FILLED 또는 포지션 0)까지 조회한다. (마지막 기록, 경과 ms, 마지막 mark)."""
        started = time.time()
        deadline = started + self.max_minutes * 60
        record, last_mark = stop, ""
        while time.time() < deadline:
            time.sleep(POLL_SECONDS)
            try:
                last_mark = f"{self.api.mark_price(SYMBOL):f}"
                record = next(
                    (
                        r
                        for r in self.manager().recover()
                        if r.client_order_id == stop.client_order_id
                    ),
                    self.store.get(stop.client_order_id) or record,
                )
                if record.state == "FILLED" or self.position() == 0:
                    return record, int((time.time() - started) * 1000), last_mark
            except Exception as exc:  # 조회 실패는 기다리며 다시 본다 — 주문은 보내지 않는다
                print(f"  조회 실패, 계속 대기: {type(exc).__name__}: {exc}")
        return record, int((time.time() - started) * 1000), last_mark

    def run(self) -> None:
        m = self.manager()
        print("1. 계좌 준비")
        if not self.check("one-way 모드", not self.api.dual_side_position()):
            raise ConfigError("헤지 모드 계좌 — one-way 만 쓴다")
        self.api.set_margin_type(SYMBOL, "CROSSED")
        lev = self.api.set_leverage(SYMBOL, LEVERAGE)
        self.check("교차 증거금·레버리지", lev == LEVERAGE, f"{lev}배")
        self.recover()
        self.flatten(m)
        self.recover()
        self.check("정리 뒤 평탄", self.position() == 0 and not self.api.open_stops(SYMBOL), SYMBOL)

        print("2. ETH 롱")
        qty = self.entry_qty()
        opened = m.submit_market(
            decision_ms=self.decision, symbol=SYMBOL, side="BUY", quantity=qty, purpose="OPEN"
        )
        if not self.check(
            "진입 체결",
            opened.state == "FILLED" and self.position() == qty,
            f"{qty} @ {opened.avg_price}",
        ):
            return

        print(f"3. 보호 스톱 (mark {self.offset_bps}bp 아래)")
        mark = self.api.mark_price(SYMBOL)
        price = stop_below(mark, self.offset_bps, self.api.filters(SYMBOL).tick_size)
        stop = m.place_protective_stop(
            decision_ms=self.decision, symbol=SYMBOL, side="SELL", stop_price=price
        )
        self.observations["mark_when_placed"] = f"{mark:f}"
        if not self.check(
            "스톱이 거래소에 걸림",
            stop.state == "SUBMITTED"
            and stop.client_order_id in {s.client_order_id for s in self.api.open_stops(SYMBOL)},
            f"{price} · {stop.client_order_id} · {stop.error or ''}",
        ):
            self.flatten(m)
            return

        print(f"4. 발동 대기 (최대 {self.max_minutes:g}분, 스톱은 옮기지 않는다)")
        record, waited_ms, last_mark = self.wait_for_trigger(stop)
        self.observations["waited_seconds"] = round(waited_ms / 1000)
        self.observations["last_mark_seen"] = last_mark
        if record.state != "FILLED" and self.position() != 0:
            self.outcome = "NOT_TRIGGERED"
            print("  제한 시간 안에 발동하지 않음 — 스톱 취소·청산 후 판정 불가로 끝낸다")
            self.flatten(self.manager())
            final = self.recover()
            self.check(
                "정리 뒤 평탄·RUNNING",
                self.position() == 0
                and not self.api.open_stops(SYMBOL)
                and final.state == "RUNNING",
                final.state,
            )
            return

        print("5. 발동 뒤 확인")
        time.sleep(1.0)  # 거래소 조회가 체결을 반영할 여유
        record = next(
            (r for r in self.manager().recover() if r.client_order_id == stop.client_order_id),
            self.store.get(stop.client_order_id) or record,
        )
        self.check(
            "스톱 기록 FILLED·체결 수량 = 포지션",
            record.state == "FILLED" and record.filled_qty == qty,
            f"{record.state} {record.filled_qty}",
        )
        self.check("체결가 기록", record.avg_price > 0, f"{record.avg_price}")
        self.check(
            "포지션 평탄·남은 스톱 0",
            self.position() == 0 and not self.api.open_stops(SYMBOL),
        )
        report = self.recover()
        self.check(
            "재시작 정합성 복구 RUNNING (스톱 체결을 로컬이 반영)",
            report.state == "RUNNING" and not report.issues,
            report.state + " " + ", ".join(sorted({i.kind for i in report.issues})),
        )
        if record.avg_price > 0:
            slip = slippage_bps(price, record.avg_price)
            self.observations["stop_price"] = f"{price:f}"
            self.observations["fill_price"] = f"{record.avg_price:f}"
            self.observations["slippage_bps_vs_stop"] = f"{slip:.2f}"
            print(f"  관찰  스톱 {price} → 체결 {record.avg_price} ({slip:.2f}bp 불리)")
        print(f"  관찰  발동 확인까지 {self.observations['waited_seconds']}초")
        self.outcome = "TRIGGERED"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offset-bps", type=Decimal, default=Decimal(15))
    parser.add_argument("--max-minutes", type=float, default=60.0)
    args = parser.parse_args()
    if not Decimal(3) <= args.offset_bps <= Decimal(200):
        raise SystemExit("--offset-bps 는 3~200 (즉시 발동 거절·과도한 대기 방지)")
    now = int(time.time() * 1000)
    if overlaps_daily_drill(now, args.max_minutes):
        raise SystemExit(
            "매일 자동 실행(21:00~21:20 KST) 구간과 겹친다 — 21:30 KST 이후나 대기 시간을 줄여 다시"
        )
    api = BinanceUsdmTestnet(load_testnet_credentials())
    conn = connect(DB)
    apply_migrations(conn)
    drill = StopDrill(
        api,
        FuturesOrderRepository(conn),
        RiskEventRepository(conn),
        now,
        args.offset_bps,
        args.max_minutes,
    )
    error: str | None = None
    try:
        print(f"테스트넷 {api.base_url} · 지갑 {api.account().wallet_balance:f} USDT · 가짜 돈")
        drill.run()
    except Exception as exc:  # 어떤 실패든 보고서를 남긴다
        error = f"{type(exc).__name__}: {exc}"
        print(f"중단: {error}")
        try:  # 흔적을 남기지 않는다 — 실패해도 포지션·스톱 정리를 한 번 시도한다
            drill.flatten(drill.manager(), purpose_attempt=1)
            drill.recover()
        except Exception as cleanup:
            print(f"정리 실패 — 테스트넷 ETH 포지션·스톱을 확인한다: {cleanup}")
    finally:
        api.close()
        conn.close()
    passed = sum(c["pass"] for c in drill.checks)
    ok = error is None and passed == len(drill.checks) and drill.outcome == "TRIGGERED"
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    REPORTS.mkdir(parents=True, exist_ok=True)
    path = REPORTS / f"stop-{stamp}.json"
    record = {
        "at": stamp,
        "decision_ms": now,
        "symbol": SYMBOL,
        "offset_bps": str(args.offset_bps),
        "max_minutes": args.max_minutes,
        "outcome": drill.outcome,
        "ok": ok,
        "error": error,
        "checks": drill.checks,
        "observations": drill.observations,
    }
    path.write_text(json.dumps(record, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    mark = "✅" if ok else ("⏳" if drill.outcome == "NOT_TRIGGERED" and error is None else "❌")
    print(f"\n{mark} {drill.outcome} {passed}/{len(drill.checks)} · 기록 {path.relative_to(REPO)}")
    if ok:
        return 0
    return 2 if drill.outcome == "NOT_TRIGGERED" and error is None else 1


if __name__ == "__main__":
    sys.exit(main())
