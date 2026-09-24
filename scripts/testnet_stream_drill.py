"""Phase 7 사용자 스트림 훈련 — 체결·스톱 변화를 WebSocket 으로 받아 기록에 반영하는지
테스트넷에서 확인한다.

**가짜 돈만 쓴다.** 스톱 발동 훈련(:mod:`testnet_stop_drill`)과 같은 준비·종목(ETHUSDT)·
시각 제한을 쓴다.
필요할 때 손으로 돌리는 선택형 훈련이며 스케줄에 넣지 않는다.

스트림 스레드는 이벤트를 큐에 넣기만 하고, 주 스레드가 :class:`StreamSync` 로 처리한다
(DB 는 한 스레드).

순서 (각 단계 PASS/FAIL):

1. 계좌 준비·ETH 정리 (스톱 발동 훈련과 같음)
2. 스트림 연결 → 연결 직후 전체 재조회
3. 진입 시장가 → 그 주문의 체결 이벤트 수신
4. 먼 보호 스톱 → ALGO_UPDATE NEW 수신
5. **관리자를 거치지 않은 취소**(거래소 직접) → 이벤트로 로컬 기록이 CANCELED 가 된다
6. **모르는 ID 의 스톱**(거래소 직접) → UNTRACKED 로 드러난다 (정리 후)
7. 연결 강제 끊기 → 재연결 → 전체 재조회
8. 가까운 보호 스톱(mark 15bp 아래) → **5초 조회 없이** 이벤트만으로 발동 체결을 확정 (최대 60분)
9. 마무리: 평탄·스톱 0·재시작 복구 RUNNING

사용 예::

    python scripts/testnet_stream_drill.py
    python scripts/testnet_stream_drill.py --offset-bps 10 --max-minutes 30
"""

from __future__ import annotations

import argparse
import json
import queue
import sys
import threading
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.testnet_drill import DB, REPORTS, load_testnet_credentials  # noqa: E402
from scripts.testnet_stop_drill import (  # noqa: E402
    SYMBOL,
    StopDrill,
    overlaps_daily_drill,
    stop_below,
)

from auto_trading.core.money import floor_to_step  # noqa: E402
from auto_trading.exchange.binance_usdm_stream import TESTNET_PRIVATE_WS, UserStream  # noqa: E402
from auto_trading.exchange.binance_usdm_testnet import BinanceUsdmTestnet  # noqa: E402
from auto_trading.exchange.user_stream import UserEvent  # noqa: E402
from auto_trading.execution.futures_orders import FuturesOrderManager  # noqa: E402
from auto_trading.execution.stream_sync import StreamSync, SyncResult  # noqa: E402
from auto_trading.storage.db import connect  # noqa: E402
from auto_trading.storage.migrations import apply_migrations  # noqa: E402
from auto_trading.storage.repositories.futures_orders import FuturesOrderRepository  # noqa: E402
from auto_trading.storage.repositories.risk_events import RiskEventRepository  # noqa: E402

TAG = "tw"
EVENT_WAIT_S = 15.0

Item = tuple[str, Any, int]
"""(종류, 내용, 받은 시각 ms). 종류: event · connect · disconnect · bad."""


def _now_ms() -> int:
    return int(time.time() * 1000)


@dataclass
class StreamDrill(StopDrill):
    inbox: queue.Queue[Item] = field(default_factory=queue.Queue)
    results: list[dict[str, Any]] = field(default_factory=list)
    kinds: Counter[str] = field(default_factory=Counter)
    sync: StreamSync | None = None

    def manager(self) -> FuturesOrderManager:
        return FuturesOrderManager(
            self.api,
            self.store,
            mode="testnet",
            strategy_id="streamdrill",
            strategy_tag=TAG,
            clock=_now_ms,
        )

    # ------------------------------------------------------------ 큐 처리 (주 스레드)

    def pump(self, until: Callable[[str, Any, SyncResult | None], bool], timeout_s: float) -> bool:
        """큐를 처리하다가 ``until`` 이 참이면 True. 시간 초과면 False."""
        assert self.sync is not None
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            if self.sync.lagging:
                # 이벤트는 종결인데 조회가 늦었던 주문 — 조회가 따라오면 그것도 이벤트 처리 결과다
                for record in self.sync.retry_lagging():
                    self.kinds["LAGGING_RESOLVED"] += 1
                    resolved = SyncResult("REFRESHED", record.client_order_id, record)
                    if until("event", None, resolved):
                        return True
            try:
                kind, payload, at = self.inbox.get(timeout=0.5)
            except queue.Empty:
                continue
            result: SyncResult | None = None
            if kind == "event":
                result = self.sync.handle(payload)
                self.kinds[result.kind] += 1
                self.results.append(
                    {
                        "received_ms": at,
                        "event_ms": getattr(payload, "event_ms", 0),
                        "type": type(payload).__name__,
                        "status": getattr(payload, "status", ""),
                        "client_order_id": result.client_order_id,
                        "sync": result.kind,
                        "state": result.record.state if result.record else None,
                    }
                )
            elif kind == "connect":
                self.sync.on_connect()
            if until(kind, payload, result):
                return True
        return False

    @staticmethod
    def about(cid: str, *states: str) -> Callable[[str, Any, SyncResult | None], bool]:
        def match(kind: str, _p: Any, r: SyncResult | None) -> bool:
            if kind != "event" or r is None or r.client_order_id != cid:
                return False
            return not states or (r.record is not None and r.record.state in states)

        return match

    # ------------------------------------------------------------ 훈련

    def run_stream(self, stream: UserStream) -> None:
        m = self.manager()
        self.sync = StreamSync(m, self.store)
        print("1. 계좌 준비")
        self.api.set_margin_type(SYMBOL, "CROSSED")
        self.recover()
        self.flatten(m)
        self.recover()
        self.check("정리 뒤 평탄", self.position() == 0 and not self.api.open_stops(SYMBOL))

        print("2. 스트림 연결")
        connected = self.pump(lambda k, _p, _r: k == "connect", 20)
        if not self.check("연결·연결 직후 재조회", connected, TESTNET_PRIVATE_WS):
            return

        print("3. 진입 체결 이벤트")
        sent = _now_ms()
        opened = m.submit_market(
            decision_ms=self.decision,
            symbol=SYMBOL,
            side="BUY",
            quantity=self.entry_qty(),
            purpose="OPEN",
        )
        got = self.pump(self.about(opened.client_order_id, "FILLED"), EVENT_WAIT_S)
        if got:
            self.observations["open_event_after_send_ms"] = self.results[-1]["received_ms"] - sent
        self.check(
            "진입 체결 이벤트 수신", got and opened.state == "FILLED", opened.client_order_id
        )

        print("4. 보호 스톱 NEW 이벤트")
        tick = self.api.filters(SYMBOL).tick_size
        far = m.place_protective_stop(
            decision_ms=self.decision,
            symbol=SYMBOL,
            side="SELL",
            stop_price=floor_to_step(self.api.mark_price(SYMBOL) * Decimal("0.95"), tick),
        )
        self.check(
            "스톱 NEW 이벤트 수신",
            self.pump(self.about(far.client_order_id, "SUBMITTED"), EVENT_WAIT_S),
            far.client_order_id,
        )

        print("5. 관리자 밖 취소 → 스트림으로 반영")
        self.api.cancel_stop(SYMBOL, far.client_order_id)  # 기록을 건드리지 않는 직접 취소
        self.check(
            "로컬 기록 CANCELED (이벤트로)",
            self.pump(self.about(far.client_order_id, "CANCELED"), EVENT_WAIT_S)
            and (self.store.get(far.client_order_id) or far).state == "CANCELED",
        )

        print("6. 모르는 ID 의 스톱")
        foreign = f"twx-{_now_ms()}"
        self.api.place_stop_market(
            SYMBOL,
            "SELL",
            floor_to_step(self.api.mark_price(SYMBOL) * Decimal("0.9"), tick),
            foreign,
        )

        def untracked(kind: str, _p: Any, r: SyncResult | None) -> bool:
            return kind == "event" and r is not None and r.kind == "UNTRACKED"

        seen = self.pump(untracked, EVENT_WAIT_S)
        self.api.cancel_stop(SYMBOL, foreign)
        self.pump(lambda *_: False, 2)  # 취소 이벤트도 흘려보낸다
        self.check("UNTRACKED 로 드러남", seen, foreign)

        print("7. 연결 강제 끊기 → 재연결")
        dropped_at = _now_ms()
        stream.drop()
        again = self.pump(lambda k, _p, _r: k == "connect", 30)
        self.observations["reconnect_ms"] = _now_ms() - dropped_at
        self.check("재연결·재조회", again, f"{self.observations['reconnect_ms']}ms")

        print(f"8. 가까운 스톱 ({self.offset_bps}bp) — 이벤트로만 발동 확정")
        price = stop_below(self.api.mark_price(SYMBOL), self.offset_bps, tick)
        near = m.place_protective_stop(
            decision_ms=self.decision, symbol=SYMBOL, side="SELL", stop_price=price, attempt=1
        )
        placed = _now_ms()
        fired = self.pump(self.about(near.client_order_id, "FILLED"), self.max_minutes * 60)
        if not fired:
            self.outcome = "NOT_TRIGGERED"
            print("  제한 시간 안에 발동하지 않음 — 정리 후 판정 불가로 끝낸다")
            self.flatten(self.manager(), purpose_attempt=1)
        else:
            record = self.store.get(near.client_order_id) or near
            self.observations["trigger_waited_seconds"] = round((_now_ms() - placed) / 1000)
            self.observations["stop_price"] = f"{price:f}"
            self.observations["fill_price"] = f"{record.avg_price:f}"
            self.check(
                "발동 체결을 이벤트로 확정 (조회 루프 없음)",
                record.state == "FILLED" and record.filled_qty > 0,
                f"{record.filled_qty} @ {record.avg_price}",
            )
            self.pump(lambda *_: False, 3)  # 뒤따르는 ALGO FINISHED·ACCOUNT_UPDATE
            self.outcome = "TRIGGERED"

        print("9. 마무리")
        report = self.recover()
        self.check(
            "평탄·스톱 0·RUNNING",
            self.position() == 0 and not self.api.open_stops(SYMBOL) and report.state == "RUNNING",
            report.state + " " + ", ".join(sorted({i.kind for i in report.issues})),
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offset-bps", type=Decimal, default=Decimal(15))
    parser.add_argument("--max-minutes", type=float, default=60.0)
    args = parser.parse_args()
    if not Decimal(3) <= args.offset_bps <= Decimal(200):
        raise SystemExit("--offset-bps 는 3~200")
    now = _now_ms()
    if overlaps_daily_drill(now, args.max_minutes + 5):
        raise SystemExit(
            "매일 자동 실행(21:00~21:20 KST) 구간과 겹친다 — 21:30 KST 이후나 대기 시간을 줄여 다시"
        )
    api = BinanceUsdmTestnet(load_testnet_credentials())
    conn = connect(DB)
    apply_migrations(conn)
    drill = StreamDrill(
        api, FuturesOrderRepository(conn), RiskEventRepository(conn), now, args.offset_bps,
        args.max_minutes,
    )  # fmt: skip
    stream = UserStream(api, TESTNET_PRIVATE_WS)
    stop = threading.Event()
    bad: list[str] = []
    disconnects: list[str] = []

    def put(kind: str, payload: Any = None) -> None:
        drill.inbox.put((kind, payload, _now_ms()))

    def on_event(event: UserEvent) -> None:
        put("event", event)

    def on_disconnect(reason: str) -> None:
        disconnects.append(reason)
        put("disconnect", reason)

    thread = threading.Thread(
        target=stream.run,
        args=(on_event,),
        kwargs={
            "stop": stop,
            "on_connect": lambda: put("connect"),
            "on_disconnect": on_disconnect,
            "on_bad_message": lambda raw, err: bad.append(f"{err}: {raw[:200]}"),
        },
        daemon=True,
    )
    error: str | None = None
    try:
        print(f"테스트넷 {api.base_url} · 지갑 {api.account().wallet_balance:f} USDT · 가짜 돈")
        thread.start()
        drill.run_stream(stream)
    except Exception as exc:  # 어떤 실패든 보고서를 남긴다
        error = f"{type(exc).__name__}: {exc}"
        print(f"중단: {error}")
        try:
            drill.flatten(drill.manager(), purpose_attempt=2)
            drill.recover()
        except Exception as cleanup:
            print(f"정리 실패 — 테스트넷 ETH 포지션·스톱을 확인한다: {cleanup}")
    finally:
        stop.set()
        thread.join(timeout=15)
        conn.close()
    # 받은 시각은 로컬 시계, 이벤트 시각은 거래소 시계 — 서버 시계 차이로 맞춘다
    offset = api.clock_offset_ms()
    latencies = [r["received_ms"] + offset - r["event_ms"] for r in drill.results if r["event_ms"]]
    drill.observations.update(
        {
            "events": len(drill.results),
            "sync_kinds": dict(drill.kinds),
            "stream_stats": {
                "connects": stream.stats.connects,
                "disconnects": stream.stats.disconnects,
                "keepalives": stream.stats.keepalives,
                "parse_errors": stream.stats.parse_errors,
            },
            "disconnect_reasons": disconnects,
            "bad_messages": bad[:5],
            "event_to_receipt_ms_max": max(latencies) if latencies else None,
            "server_clock_offset_ms": offset,
        }
    )
    api.close()
    passed = sum(c["pass"] for c in drill.checks)
    ok = error is None and passed == len(drill.checks) and drill.outcome == "TRIGGERED"
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    REPORTS.mkdir(parents=True, exist_ok=True)
    path = REPORTS / f"stream-{stamp}.json"
    record = {
        "at": stamp,
        "decision_ms": now,
        "symbol": SYMBOL,
        "outcome": drill.outcome,
        "ok": ok,
        "error": error,
        "checks": drill.checks,
        "observations": drill.observations,
        "events": drill.results,
    }
    path.write_text(json.dumps(record, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"\n관찰 {json.dumps(drill.observations, ensure_ascii=False)}")
    mark = "✅" if ok else ("⏳" if drill.outcome == "NOT_TRIGGERED" and error is None else "❌")
    print(f"{mark} {drill.outcome} {passed}/{len(drill.checks)} · 기록 {path.relative_to(REPO)}")
    if ok:
        return 0
    return 2 if drill.outcome == "NOT_TRIGGERED" and error is None else 1


if __name__ == "__main__":
    sys.exit(main())
