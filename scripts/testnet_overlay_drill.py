"""O/v2 안전성 검증 V4 — 선물 일일 실행기를 테스트넷에서 매일 돌린다 (docs/research/54).

**가짜 돈만 쓴다.** 테스트넷 키·어댑터·DB(``data/testnet.db``)는 매일 훈련과 같다.
종목은 **SOLUSDT · BNBUSDT** — O 의 BTC·ETH 자리를 대신한다.
매일 훈련(BTC)·스톱 발동 훈련(ETH)과 포지션이 섞이지 않게 나눴다.
같은 DB 를 쓰므로 매일 훈련의 정합성 복구도 이 포지션을 우리 기록으로 안다.

한 번 실행 = 하루. 며칠에 걸쳐 포지션을 **들고 간다**.
매일 훈련과 달리 끝에 평탄하게 만들지 않는다.
목표는 실행 횟수에 따라 정해진 순서로 바뀐다.
유지·늘리기·줄이기·0·재진입·노출 증가 금지를 모두 거친다.
스톱 가격은 실행 때 mark × 0.8 (O/v2 는 전날 무기한 종가 × 0.8 — 가격 출처만 다르다).

확인 (각 PASS/FAIL):

1. 실행기 상태 OK (노출 증가 금지 날은 DEGRADED 가 정상)
2. 종목마다 포지션 = 목표 (금지 날의 늘리기는 전날 수량 유지)
3. 포지션이 있으면 거래소 스톱이 **정확히 하나**, closePosition, 가격 = 오늘 스톱. 없으면 스톱 0
4. 같은 목표·같은 스톱으로 다시 실행 → **새 주문 0건**
5. 정합성 복구 RUNNING, 차이 없음

매일 20:45 KST 에 실행한다 (21:00~21:20 자동 실행 구간 전에 끝난다).

사용 예::

    python scripts/testnet_overlay_drill.py            # 오늘 하루치
    python scripts/testnet_overlay_drill.py --flatten  # 관찰을 끝낼 때: 모두 청산·스톱 정리
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

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.testnet_drill import DB, LEVERAGE, REPORTS, load_testnet_credentials  # noqa: E402
from scripts.testnet_stop_drill import overlaps_daily_drill  # noqa: E402

from auto_trading.core.money import floor_to_step  # noqa: E402
from auto_trading.exchange.binance_usdm_testnet import BinanceUsdmTestnet  # noqa: E402
from auto_trading.execution.futures_guard import FuturesSupervisor  # noqa: E402
from auto_trading.execution.futures_orders import FuturesOrderManager  # noqa: E402
from auto_trading.execution.overlay_futures import (  # noqa: E402
    FuturesLegTarget,
    OverlayExecutionReport,
    OverlayFuturesExecutor,
)
from auto_trading.ops.kill_switch import halted_reason  # noqa: E402
from auto_trading.storage.db import connect  # noqa: E402
from auto_trading.storage.migrations import apply_migrations  # noqa: E402
from auto_trading.storage.repositories.futures_orders import FuturesOrderRepository  # noqa: E402
from auto_trading.storage.repositories.risk_events import RiskEventRepository  # noqa: E402

for _stream in (sys.stdout, sys.stderr):
    _reconfigure = getattr(_stream, "reconfigure", None)
    if _reconfigure is not None:
        _reconfigure(encoding="utf-8", errors="replace")

SYMBOLS = ("SOLUSDT", "BNBUSDT")
TAG = "ov"
STRATEGY = "o2_testnet"
BASE_NOTIONAL = Decimal(60)
"""종목별 기본 명목 (USDT). 가짜 돈이지만 작게 둔다."""
STOP_FACTOR = Decimal("0.8")
HALT = REPO / "data" / "HALT"
SCHEDULE = (
    (Decimal(1), Decimal(1), True),
    (Decimal(1), Decimal("1.5"), True),
    (Decimal("1.5"), Decimal("0.5"), True),
    (Decimal("0.5"), Decimal(0), True),
    (Decimal(0), Decimal(1), True),
    (Decimal(1), Decimal("1.5"), False),  # 노출 증가 금지 날 (그날 손실 2% 등)
    (Decimal(1), Decimal(1), True),
)
"""(SOL 배수, BNB 배수, 노출 증가 허용) — 실행 횟수에 따라 차례로."""


def day_index() -> int:
    """지금까지 하루치 실행 수 (청산 기록 ``overlay-flatten-*`` 은 세지 않는다)."""
    return len(list(REPORTS.glob("overlay-2*.json")))


class OverlayDrill:
    def __init__(self, api: BinanceUsdmTestnet, conn: Any, now: int) -> None:
        self.api = api
        self.store = FuturesOrderRepository(conn)
        self.events = RiskEventRepository(conn)
        self.now = now
        self.checks: list[dict[str, Any]] = []
        self.observations: dict[str, Any] = {}
        self.m = FuturesOrderManager(
            api,
            self.store,
            mode="testnet",
            strategy_id=STRATEGY,
            strategy_tag=TAG,
            clock=lambda: int(time.time() * 1000),
        )
        self.sup = FuturesSupervisor(
            api,
            self.m,
            self.store,
            self.events,
            mode="testnet",
            symbols=list(SYMBOLS),
            require_stop=True,
            orphan_policy="flatten",
            clock=lambda: int(time.time() * 1000),
        )
        self.x = OverlayFuturesExecutor(
            api,
            self.m,
            self.store,
            self.sup,
            clock=lambda: int(time.time() * 1000),
            halted_reason=lambda: halted_reason(HALT),
            settle=lambda: time.sleep(0.5),  # 취소 뒤 목록이 반영될 여유 (54 §3)
        )

    def check(self, name: str, ok: bool, detail: str = "") -> bool:
        self.checks.append({"step": name, "pass": ok, "detail": detail})
        print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f" — {detail}" if detail else ""))
        return ok

    def position(self, symbol: str) -> Decimal:
        pos = self.api.account().positions.get(symbol)
        return pos.quantity if pos else Decimal(0)

    def targets(self, factors: tuple[Decimal, Decimal]) -> list[FuturesLegTarget]:
        out = []
        for symbol, factor in zip(SYMBOLS, factors, strict=True):
            f = self.api.filters(symbol)
            mark = self.api.mark_price(symbol)
            qty = floor_to_step(BASE_NOTIONAL * factor / mark, f.step_size)
            stop = floor_to_step(mark * STOP_FACTOR, f.tick_size)
            out.append(FuturesLegTarget(symbol, qty, stop if qty > 0 else None))
        return out

    def prepare(self) -> None:
        """교차 증거금·레버리지는 포지션·스톱이 없을 때만 바꾼다.

        테스트넷 실측(2026-09-25): 걸린 주문이 있으면 증거금 방식 변경이 -4067 로 거부된다.
        며칠 들고 가는 훈련이라 첫날에만 설정된다.
        """
        for s in SYMBOLS:
            if self.position(s) or self.api.open_stops(s):
                continue
            self.api.set_margin_type(s, "CROSSED")
            self.api.set_leverage(s, LEVERAGE)

    def run(self, day: int) -> None:
        sol, bnb, allowed = SCHEDULE[day % len(SCHEDULE)]
        self.observations["day"] = day
        self.observations["factors"] = [str(sol), str(bnb)]
        self.observations["increases_allowed_input"] = allowed
        gate = "허용" if allowed else "금지"
        print(f"1. 준비 · 하루 {day} · 배수 SOL {sol} · BNB {bnb} · 노출 증가 {gate}")
        self.prepare()
        before = {s: self.position(s) for s in SYMBOLS}
        targets = self.targets((sol, bnb))

        print("2. 실행기")
        t0 = time.perf_counter()
        rep = self.x.run(self.now, targets, increases_allowed=allowed)
        self.observations["run_ms"] = int((time.perf_counter() - t0) * 1000)
        self.observations["report"] = summarize(rep)
        expected_state = "OK" if allowed else "DEGRADED"
        halted = halted_reason(HALT)
        if halted is not None:
            expected_state = "DEGRADED"
        self.check("실행기 상태", rep.state in {expected_state, "OK"}, f"{rep.state} · {rep.notes}")
        for t, leg in zip(targets, rep.legs, strict=True):
            want = t.quantity
            if (not allowed or halted is not None) and want > before[t.symbol]:
                want = before[t.symbol]
            got = self.position(t.symbol)
            self.check(
                f"{t.symbol} 포지션 = 목표", got == want, f"{got} (목표 {want}) {leg.orders}"
            )
            stops = self.api.open_stops(t.symbol)
            if got > 0:
                ok = (
                    len(stops) == 1
                    and stops[0].close_position
                    and t.stop_price is not None
                    and stops[0].stop_price == t.stop_price
                )
                detail = ", ".join(
                    f"{s.stop_price}/{'close' if s.close_position else s.orig_qty}" for s in stops
                )
                self.check(f"{t.symbol} 스톱 하나·closePosition·오늘 가격", ok, detail)
            else:
                self.check(f"{t.symbol} 포지션 0 → 스톱 0", not stops, str(len(stops)))

        print("3. 같은 목표로 다시 실행 (멱등)")
        n_before = len(self.store.all())
        again = self.x.run(self.now + 1, targets, increases_allowed=allowed)
        n_after = len(self.store.all())
        self.check(
            "재실행 새 주문 0건", n_after == n_before, f"{n_after - n_before}건 · {again.state}"
        )

        print("4. 정합성 복구")
        final = self.sup.recover(self.now + 2, lambda s, p: None)
        self.check(
            "복구 RUNNING·차이 없음",
            final.state == "RUNNING" and not final.issues,
            final.state + " " + ", ".join(sorted({i.kind for i in final.issues})),
        )

    def flatten(self) -> None:
        print("모두 청산·스톱 정리")
        targets = [FuturesLegTarget(s, Decimal(0), None) for s in SYMBOLS]
        rep = self.x.run(self.now, targets, increases_allowed=False)
        self.observations["report"] = summarize(rep)
        for s in SYMBOLS:
            self.check(f"{s} 평탄·스톱 0", self.position(s) == 0 and not self.api.open_stops(s))


def summarize(rep: OverlayExecutionReport) -> dict[str, Any]:
    return {
        "state": rep.state,
        "recovery_state": rep.recovery_state,
        "increases_allowed": rep.increases_allowed,
        "fired_stops": rep.fired_stops,
        "notes": rep.notes,
        "legs": [
            {
                "symbol": leg.symbol,
                "before": str(leg.before),
                "after": str(leg.after),
                "orders": leg.orders,
                "stop": leg.stop,
                "notes": leg.notes,
            }
            for leg in rep.legs
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--flatten", action="store_true", help="관찰 끝: 모두 청산")
    args = parser.parse_args()
    now = int(time.time() * 1000)
    if overlaps_daily_drill(now, 5):
        raise SystemExit(
            "매일 자동 실행(21:00~21:20 KST) 구간과 겹친다 — 20:45 KST 나 21:30 KST 이후에"
        )
    api = BinanceUsdmTestnet(load_testnet_credentials())
    conn = connect(DB)
    apply_migrations(conn)
    drill = OverlayDrill(api, conn, now)
    day = day_index()
    error: str | None = None
    try:
        print(f"테스트넷 {api.base_url} · 지갑 {api.account().wallet_balance:f} USDT · 가짜 돈")
        if args.flatten:
            drill.flatten()
        else:
            drill.run(day)
    except Exception as exc:  # 무인 실행: 어떤 실패든 보고서를 남긴다 (포지션은 보호 스톱이 지킨다)
        error = f"{type(exc).__name__}: {exc}"
        print(f"중단: {error}")
    finally:
        api.close()
        conn.close()
    passed = sum(c["pass"] for c in drill.checks)
    ok = error is None and passed == len(drill.checks)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    REPORTS.mkdir(parents=True, exist_ok=True)
    prefix = "overlay-flatten" if args.flatten else "overlay"
    path = REPORTS / f"{prefix}-{stamp}.json"
    record = {
        "at": stamp,
        "decision_ms": now,
        "ok": ok,
        "error": error,
        "checks": drill.checks,
        "observations": drill.observations,
    }
    path.write_text(
        json.dumps(record, ensure_ascii=False, indent=1, default=str) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    mark = "✅" if ok else "❌"
    print(f"\n{mark} {passed}/{len(drill.checks)} 통과 · 기록 {path.relative_to(REPO)}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
