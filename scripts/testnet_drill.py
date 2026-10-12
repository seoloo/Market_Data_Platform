"""Phase 7 테스트넷 훈련 — 주문·보호 스톱·재시작 복구를 실제 거래소 응답으로 검증한다.

**가짜 돈만 쓴다.**

테스트넷 전용 키(``BINANCE_TESTNET_*``)와 테스트넷 전용 어댑터만 쓴다. 실계좌 키와 같으면 멈춘다.
기록은 별도 DB ``data/testnet.db`` 에 남긴다 — 운영 DB 와 섞지 않는다.

순서 (각 단계 PASS/FAIL, 관찰 항목은 판정 없이 기록):

1. 계좌 준비: one-way 확인, 교차 증거금·레버리지 3배, 남은 포지션·스톱 정리
2. 같은 진입 의도 3회 → 주문 1건
3. 실제 수수료율 관찰 (userTrades)
4. 보호 스톱을 Algo API 로 건다
5. 재시작(새 관리자) 정합성 복구 → RUNNING
6. 거래소 거절 4종: 닫을 포지션 없는 스톱 · 같은 방향 두 번째 스톱 · reduce-only · 최소 명목
   (즉시 발동 스톱 거절은 4단계에서 본다)
7. 전송 직후 강제 종료 흉내 → 새 관리자의 조회 복구로 FILLED
8. 스톱을 취소하면 감시자가 다시 건다
9. 전량 청산 뒤 closePosition 스톱이 어떻게 되는지 관찰, 남으면 감시자가 취소
10. 원인 불명 포지션(직접 주문) → 감시자가 정리하고 외부 변동을 기록
11. 관찰: 이미 체결된 client_order_id 를 거래소가 다시 받는가 (로컬 멱등의 필요성)
12. 마무리: 포지션·주문·스톱 0, 감시자 RUNNING

사용 예::

    python scripts/testnet_drill.py
"""

from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import ROUND_UP, Decimal
from pathlib import Path
from typing import Any

from auto_trading.config.secrets import BinanceCredentials, read_env_file
from auto_trading.core.errors import ConfigError, OrderRejectedError
from auto_trading.core.money import floor_to_step
from auto_trading.exchange.binance_usdm_testnet import BinanceUsdmTestnet
from auto_trading.exchange.futures import FuturesPosition
from auto_trading.execution.futures_guard import FuturesSupervisor, RecoveryReport
from auto_trading.execution.futures_orders import FuturesOrderManager, futures_client_order_id
from auto_trading.storage.db import connect
from auto_trading.storage.migrations import apply_migrations
from auto_trading.storage.repositories.futures_orders import (
    FuturesOrderRecord,
    FuturesOrderRepository,
)
from auto_trading.storage.repositories.risk_events import RiskEventRepository

for _stream in (sys.stdout, sys.stderr):
    _reconfigure = getattr(_stream, "reconfigure", None)
    if _reconfigure is not None:
        _reconfigure(encoding="utf-8", errors="replace")

REPO = Path(__file__).resolve().parent.parent
DB = REPO / "data" / "testnet.db"
REPORTS = REPO / "reports" / "testnet"
SYMBOL = "BTCUSDT"
TAG = "tn"
LEVERAGE = 3


@dataclass
class Drill:
    api: BinanceUsdmTestnet
    store: FuturesOrderRepository
    events: RiskEventRepository
    decision: int
    checks: list[dict[str, Any]] = field(default_factory=list)
    observations: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------ 도구

    def manager(self) -> FuturesOrderManager:
        """매번 새로 만든다 — 재시작한 프로세스처럼 로컬 기록만 들고 시작한다."""
        return FuturesOrderManager(
            self.api,
            self.store,
            mode="testnet",
            strategy_id="drill",
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
            # Phase 7 판정 창(~10/08) 동안 훈련 경로를 바꾸지 않는다 — 판정 뒤 지정가로 바꾼다
            orphan_close="market",
            shared_account=True,  # 데모 계좌를 여러 트랙이 나눠 쓴다 (문서 59 §3)
            clock=lambda: int(time.time() * 1000),
        )

    def stop_price(self, _symbol: str, position: FuturesPosition) -> Decimal:
        mark = self.api.mark_price(SYMBOL)
        tick = self.api.filters(SYMBOL).tick_size
        factor = Decimal("0.85") if position.quantity > 0 else Decimal("1.15")
        return floor_to_step(mark * factor, tick)

    def recover(self) -> RecoveryReport:
        return self.supervisor(self.manager()).recover(self.decision, self.stop_price)

    def position(self) -> Decimal:
        pos = self.api.account().positions.get(SYMBOL)
        return pos.quantity if pos else Decimal(0)

    def check(self, name: str, ok: bool, detail: str = "") -> bool:
        self.checks.append({"step": name, "pass": ok, "detail": detail})
        print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f" — {detail}" if detail else ""))
        return ok

    def next_attempt(self, purpose: str, side: str) -> int:
        attempt = 0
        while self.store.get(
            futures_client_order_id(TAG, self.decision, SYMBOL, purpose, side, attempt)
        ):
            attempt += 1
        return attempt

    def close_all(self, m: FuturesOrderManager) -> FuturesOrderRecord | None:
        qty = self.position()
        if not qty:
            return None
        side = "SELL" if qty > 0 else "BUY"
        return m.submit_market(
            decision_ms=self.decision,
            symbol=SYMBOL,
            side=side,
            quantity=abs(qty),
            purpose="CLOSE",
            attempt=self.next_attempt("CLOSE", side),
        )

    def entry_qty(self) -> Decimal:
        f = self.api.filters(SYMBOL)
        raw = f.min_notional * Decimal("1.3") / self.api.mark_price(SYMBOL)
        steps = (raw / f.step_size).to_integral_value(rounding=ROUND_UP)
        return max(steps * f.step_size, f.min_qty)

    # ------------------------------------------------------------ 훈련

    def run(self) -> None:
        m = self.manager()
        print("1. 계좌 준비")
        one_way = not self.api.dual_side_position()
        if not self.check("one-way 모드", one_way):
            raise ConfigError(
                "헤지 모드 계좌 — 이 시스템은 one-way 만 쓴다. 테스트넷 설정을 바꾼다."
            )
        self.api.set_margin_type(SYMBOL, "CROSSED")
        lev = self.api.set_leverage(SYMBOL, LEVERAGE)
        self.check("교차 증거금·레버리지", lev == LEVERAGE, f"{lev}배")
        first = self.recover()
        self.close_all(m)
        for stop in m.working_stops(SYMBOL):
            m.cancel(stop.client_order_id)
        self.recover()
        self.check(
            "정리 뒤 평탄",
            self.position() == 0 and not self.api.open_stops(SYMBOL),
            f"시작 상태 {first.state} {sorted({i.kind for i in first.issues})}",
        )

        print("2. 같은 진입 의도 3회")
        qty = self.entry_qty()
        started = time.perf_counter()
        opens = [
            m.submit_market(
                decision_ms=self.decision, symbol=SYMBOL, side="BUY", quantity=qty, purpose="OPEN"
            )
            for _ in range(3)
        ]
        latency_ms = (time.perf_counter() - started) * 1000
        self.check(
            "주문 1건만 체결",
            {o.state for o in opens} == {"FILLED"}
            and len({o.client_order_id for o in opens}) == 1
            and self.position() == qty,
            f"수량 {qty}",
        )
        self.check("체결가 기록", opens[0].avg_price > 0, f"평균가 {opens[0].avg_price}")
        self.observations["open_roundtrip_ms_for_3_calls"] = round(latency_ms)

        print("3. 실제 수수료율 (관찰)")
        fee, notional = self.api.trade_fees(SYMBOL, opens[0].exchange_order_id or "")
        rate = fee / notional if notional else Decimal(0)
        self.observations["taker_fee_rate"] = str(rate)
        print(f"  관찰  수수료 {fee} / 체결 명목 {notional} = {rate:.5%} (연구 가정 0.045%)")

        print("4. 보호 스톱 (Algo API)")
        pos = self.api.account().positions[SYMBOL]
        mark = self.api.mark_price(SYMBOL)
        tick = self.api.filters(SYMBOL).tick_size
        # 거래소 검사 순서(실측): 닫을 포지션(-4509) → 같은 방향 중복(-4130) → 즉시 발동(-2021).
        # 그래서 "즉시 발동"은 롱이 있고 SELL 스톱이 아직 없는 지금만 볼 수 있다.
        bad_stop = m.place_protective_stop(
            decision_ms=self.decision,
            symbol=SYMBOL,
            side="SELL",
            stop_price=floor_to_step(mark * Decimal("1.05"), tick),
        )
        self.check(
            "즉시 발동 스톱 거절(-2021)",
            bad_stop.state == "REJECTED" and "-2021" in (bad_stop.error or ""),
            bad_stop.error or "",
        )
        stop = m.place_protective_stop(
            decision_ms=self.decision,
            symbol=SYMBOL,
            side="SELL",
            stop_price=self.stop_price(SYMBOL, pos),
            attempt=self.next_attempt("PROTECT", "SELL"),
        )
        listed = {s.client_order_id for s in self.api.open_stops(SYMBOL)}
        self.check(
            "스톱이 거래소에 걸림",
            stop.state == "SUBMITTED" and stop.client_order_id in listed,
            f"{stop.stop_price} · {stop.client_order_id}",
        )

        print("5. 재시작 정합성 복구")
        report = self.recover()
        self.check("RUNNING", report.state == "RUNNING" and not report.issues, report.state)

        print("6. 거래소 거절 4종")
        mark = self.api.mark_price(SYMBOL)
        tick = self.api.filters(SYMBOL).tick_size
        no_short = m.place_protective_stop(
            decision_ms=self.decision,
            symbol=SYMBOL,
            side="BUY",
            stop_price=floor_to_step(mark * Decimal("1.20"), tick),
        )
        self.check(
            "닫을 포지션 없는 스톱 거절(-4509)",
            no_short.state == "REJECTED" and "-4509" in (no_short.error or ""),
            no_short.error or "",
        )
        dup_stop = m.place_protective_stop(
            decision_ms=self.decision,
            symbol=SYMBOL,
            side="SELL",
            stop_price=floor_to_step(mark * Decimal("0.80"), tick),
            attempt=self.next_attempt("PROTECT", "SELL"),
        )
        self.check(
            "같은 방향 두 번째 스톱 거절(-4130)",
            dup_stop.state == "REJECTED" and "-4130" in (dup_stop.error or ""),
            dup_stop.error or "",
        )
        bad_reduce = m.submit_market(
            decision_ms=self.decision, symbol=SYMBOL, side="BUY", quantity=qty, purpose="REDUCE"
        )
        self.check("reduce-only 위반 거절", bad_reduce.state == "REJECTED", bad_reduce.error or "")
        eth_tiny = self.api.filters("ETHUSDT").min_qty
        tiny = m.submit_market(
            decision_ms=self.decision,
            symbol="ETHUSDT",
            side="BUY",
            quantity=eth_tiny,
            purpose="OPEN",
        )
        self.check("최소 명목 미달 거절", tiny.state == "REJECTED", tiny.error or "")
        self.check("거절 뒤에도 신규 진입 막히지 않음", not m.blocked())

        print("7. 전송 직후 강제 종료 흉내")
        cid = futures_client_order_id(TAG, self.decision, SYMBOL, "OPEN", "BUY", 1)
        now = int(time.time() * 1000)
        self.store.insert(
            FuturesOrderRecord(
                client_order_id=cid,
                mode="testnet",
                strategy_id="drill",
                decision_ms=self.decision,
                symbol=SYMBOL,
                side="BUY",
                order_type="MARKET",
                purpose="OPEN",
                reduce_only=False,
                close_position=False,
                qty=qty,
                state="PENDING",
                created_ms=now,
                updated_ms=now,
            )
        )
        self.api.place_market_order(SYMBOL, "BUY", qty, cid, reduce_only=False)  # 응답을 버린다
        restarted = self.manager()
        self.check("시작 시 결과 모름으로 신규 진입 막힘", restarted.blocked())
        resolved = next(r for r in restarted.recover() if r.client_order_id == cid)
        self.check(
            "조회로 FILLED 복구",
            resolved.state == "FILLED" and resolved.filled_qty == qty and not restarted.blocked(),
            f"{resolved.filled_qty} @ {resolved.avg_price}",
        )
        report = self.recover()
        self.check("증가한 포지션도 closePosition 스톱이 보호", report.state == "RUNNING")

        print("8. 스톱 취소 → 감시자가 다시 건다")
        (working,) = m.working_stops(SYMBOL)
        canceled = m.cancel(working.client_order_id)
        report = self.recover()
        self.check(
            "재보호",
            canceled.state == "CANCELED"
            and report.state == "RUNNING"
            and "UNPROTECTED" in {i.kind for i in report.issues}
            and len(self.api.open_stops(SYMBOL)) == 1,
            "; ".join(report.actions),
        )

        print("9. 전량 청산 뒤 closePosition 스톱")
        closed = self.close_all(self.manager())
        time.sleep(1.0)
        left = self.api.open_stops(SYMBOL)
        self.observations["close_position_stop_after_flat"] = (
            "거래소가 자동 정리" if not left else f"남아 있음 {len(left)}건"
        )
        print(f"  관찰  청산 뒤 스톱: {self.observations['close_position_stop_after_flat']}")
        report = self.recover()
        pos_after, stops_after = self.position(), self.api.open_stops(SYMBOL)
        # 조건마다 값을 남긴다 — 2026-10-11 실패는 어느 조건인지 기록이 없었다
        detail = (
            f"청산 {closed.state if closed else '없음'} · 포지션 {pos_after} · "
            f"스톱 {len(stops_after)} · {report.state}"
        )
        if report.actions:
            detail += " · " + "; ".join(report.actions)
        self.check(
            "평탄·스톱 없음·RUNNING",
            closed is not None
            and closed.state == "FILLED"
            and pos_after == 0
            and not stops_after
            and report.state == "RUNNING",
            detail,
        )

        print("10. 원인 불명 포지션")
        orphan = f"tnx-{int(time.time() * 1000)}"
        self.api.place_market_order(SYMBOL, "BUY", qty, orphan, reduce_only=False)
        report = self.recover()
        again = self.recover()
        self.check(
            "정리·외부 변동 기록·다음 복구 깨끗",
            "ORPHAN_POSITION" in {i.kind for i in report.issues}
            and self.position() == 0
            and again.state == "RUNNING"
            and not again.issues,
            "; ".join(report.actions),
        )

        print("11. 관찰: 체결된 ID 재전송")
        try:
            self.api.place_market_order(
                SYMBOL, "BUY", qty, opens[0].client_order_id, reduce_only=False
            )
            verdict = "받았다 — 거래소 중복 검사는 영구 멱등을 보장하지 않는다"
        except OrderRejectedError as exc:
            verdict = f"거절했다: {exc}"
        self.observations["filled_client_order_id_reuse"] = verdict
        print(f"  관찰  {verdict}")
        report = self.recover()  # 받았다면 원인 불명 포지션으로 정리된다

        print("12. 마무리")
        final = self.recover()
        self.check(
            "포지션·일반 주문·스톱 0, RUNNING",
            self.position() == 0
            and not self.api.open_orders(SYMBOL)
            and not self.api.open_stops(SYMBOL)
            and final.state == "RUNNING",
            final.state,
        )


def load_testnet_credentials() -> BinanceCredentials:
    env = read_env_file(REPO / ".env")
    key, secret = env.get("BINANCE_TESTNET_API_KEY", ""), env.get("BINANCE_TESTNET_API_SECRET", "")
    if not key or not secret:
        raise SystemExit(".env 에 BINANCE_TESTNET_API_KEY / BINANCE_TESTNET_API_SECRET 가 없다")
    if key == env.get("BINANCE_API_KEY"):
        raise SystemExit("테스트넷 키가 실계좌 키와 같다 — 멈춘다")
    return BinanceCredentials(key, secret)


def main() -> int:
    api = BinanceUsdmTestnet(load_testnet_credentials())
    conn = connect(DB)
    apply_migrations(conn)
    decision = int(time.time() * 1000)
    drill = Drill(api, FuturesOrderRepository(conn), RiskEventRepository(conn), decision)
    error: str | None = None
    try:
        print(f"테스트넷 {api.base_url} · 지갑 {api.account().wallet_balance:f} USDT · 가짜 돈")
        drill.run()
    except Exception as exc:  # 무인 실행: 어떤 실패든 보고서를 남겨 점검 보고에 원인이 보이게 한다
        error = f"{type(exc).__name__}: {exc}"
        print(f"중단: {error}")
    finally:
        api.close()
        conn.close()
    passed = sum(c["pass"] for c in drill.checks)
    ok = error is None and passed == len(drill.checks)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    REPORTS.mkdir(parents=True, exist_ok=True)
    report = {
        "at": stamp,
        "decision_ms": decision,
        "ok": ok,
        "error": error,
        "checks": drill.checks,
        "observations": drill.observations,
    }
    path = REPORTS / f"drill-{stamp}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    mark = "✅" if ok else "❌"
    print(f"\n{mark} {passed}/{len(drill.checks)} 통과 · 기록 {path.relative_to(REPO)}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
