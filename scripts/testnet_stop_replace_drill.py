"""O/v2 안전성 검증 — 매일 보호 스톱 교체를 공백 없이 할 수 있는가 (테스트넷, docs/research/52).

**가짜 돈만 쓴다.** 스톱 발동 훈련(:mod:`testnet_stop_drill`)과 같은 키·어댑터·DB·종목(ETHUSDT).
발동시키지 않는다 — 모든 스톱은 mark 의 약 20% 아래(O/v2 거리)에 걸고, 끝나면 모두 취소·청산한다.

O/v2 는 매일 스톱 가격을 "전날 종가 × 0.8" 로 다시 건다.
같은 방향 closePosition 스톱은 하나만 걸린다
(-4130, 26 §6). 그러면 "새 스톱 → 옛 스톱 취소" 순서가 안 되고 "취소 → 새 스톱" 사이에 보호가 빈다.
이 훈련은 다음을 잰다:

P1. closePosition 스톱이 걸린 상태에서 두 번째 closePosition 스톱 → 거부되는가 (-4130 재확인)
P2. closePosition 스톱이 걸린 상태에서 **수량 지정 reduce-only** 스톱 → 함께 걸리는가
P3. 수량 지정 스톱 두 개 → 함께 걸리는가 (교대로 교체할 수 있는가)
P4. 수량 지정 스톱이 걸린 상태에서 closePosition 스톱 → 걸리는가
P5. "취소 → 새 closePosition" 방식의 공백 시간(ms) — 취소 응답부터 새 스톱 접수까지
P6. 각 단계 뒤 걸린 스톱이 최소 하나 있었는가 (공백 없는 경로라면)

판정이 아니라 **측정**이다. 결과로 O/v2 실행기의 스톱 교체 방식을 정한다.

사용 예::

    python scripts/testnet_stop_replace_drill.py
"""

from __future__ import annotations

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
from scripts.testnet_stop_drill import SYMBOL, StopDrill, overlaps_daily_drill  # noqa: E402

from auto_trading.core.errors import ConfigError, ExchangeError  # noqa: E402
from auto_trading.core.money import floor_to_step  # noqa: E402
from auto_trading.exchange.binance_usdm_testnet import BinanceUsdmTestnet  # noqa: E402
from auto_trading.storage.db import connect  # noqa: E402
from auto_trading.storage.migrations import apply_migrations  # noqa: E402
from auto_trading.storage.repositories.futures_orders import FuturesOrderRepository  # noqa: E402
from auto_trading.storage.repositories.risk_events import RiskEventRepository  # noqa: E402

for _stream in (sys.stdout, sys.stderr):
    _reconfigure = getattr(_stream, "reconfigure", None)
    if _reconfigure is not None:
        _reconfigure(encoding="utf-8", errors="replace")


class ReplaceDrill(StopDrill):
    """스톱 교체 측정. 발동 대기 없이 걸고·취소만 한다."""

    def price(self, factor: str) -> Decimal:
        mark = self.api.mark_price(SYMBOL)
        return floor_to_step(mark * Decimal(factor), self.api.filters(SYMBOL).tick_size)

    def cid(self, label: str) -> str:
        return f"tsr-{self.decision}-{label}"

    def working(self) -> list[str]:
        return sorted(s.client_order_id for s in self.api.open_stops(SYMBOL))

    def try_stop(self, label: str, factor: str, *, quantity: Decimal | None) -> tuple[bool, str]:
        """스톱을 걸어 본다. (걸림, 거부 사유). 결과를 모르면 예외를 올린다."""
        try:
            self.api.place_stop_market(
                SYMBOL, "SELL", self.price(factor), self.cid(label), quantity=quantity
            )
        except ExchangeError as exc:
            if "전송 결과 모름" in str(exc):
                raise
            return False, str(exc)
        time.sleep(0.5)
        return self.cid(label) in self.working(), ""

    def cancel_all_stops(self) -> None:
        for s in self.api.open_stops(SYMBOL):
            self.api.cancel_stop(SYMBOL, s.client_order_id)

    def run(self) -> None:
        m = self.manager()
        print("1. 계좌 준비")
        if not self.check("one-way 모드", not self.api.dual_side_position()):
            raise ConfigError("헤지 모드 계좌 — one-way 만 쓴다")
        self.api.set_margin_type(SYMBOL, "CROSSED")
        lev = self.api.set_leverage(SYMBOL, LEVERAGE)
        self.check("교차 증거금·레버리지", lev == LEVERAGE, f"{lev}배")
        self.cancel_all_stops()
        self.recover()
        self.flatten(m)
        self.recover()
        self.check("정리 뒤 평탄", self.position() == 0 and not self.working(), SYMBOL)

        print("2. ETH 롱")
        qty = self.entry_qty()
        opened = m.submit_market(
            decision_ms=self.decision, symbol=SYMBOL, side="BUY", quantity=qty, purpose="OPEN"
        )
        if not self.check(
            "진입 체결", opened.state == "FILLED" and self.position() == qty, f"{qty}"
        ):
            return
        obs = self.observations
        always_protected = True

        print("3. 스톱 교체 측정 (모두 mark 의 약 20% 아래 — 발동하지 않는다)")
        ok, why = self.try_stop("A", "0.80", quantity=None)
        self.check("A closePosition 스톱 걸림", ok, why)
        ok, why = self.try_stop("B", "0.79", quantity=None)
        obs["P1_second_close_position"] = {"accepted": ok, "reason": why}
        print(f"  P1 두 번째 closePosition: {'걸림' if ok else '거부'} {why}")
        ok_c, why = self.try_stop("C", "0.79", quantity=qty)
        obs["P2_qty_stop_with_close_position"] = {"accepted": ok_c, "reason": why}
        print(f"  P2 closePosition 있을 때 수량 스톱: {'걸림' if ok_c else '거부'} {why}")
        ok_d, why = self.try_stop("D", "0.78", quantity=qty)
        obs["P3_two_qty_stops"] = {"accepted": ok_d, "reason": why}
        print(f"  P3 수량 스톱 두 개: {'걸림' if ok_d else '거부'} {why}")
        obs["working_after_probes"] = self.working()

        # 공백 없는 교대 경로: A 취소 → (C·D 가 보호) → C 취소 → D 만 남음
        if ok_c:
            self.api.cancel_stop(SYMBOL, self.cid("A"))
            time.sleep(0.5)
            left = self.working()
            always_protected &= bool(left)
            obs["after_cancel_A"] = left
            ok_e, why = self.try_stop("E", "0.80", quantity=None)
            obs["P4_close_position_with_qty_stop"] = {"accepted": ok_e, "reason": why}
            print(f"  P4 수량 스톱 있을 때 closePosition: {'걸림' if ok_e else '거부'} {why}")
        self.cancel_all_stops()
        time.sleep(0.5)

        print("4. 취소 → 새 closePosition 공백 시간 (P5)")
        gaps: list[int] = []
        for i in range(3):
            ok, why = self.try_stop(f"G{i}", "0.80", quantity=None)
            if not ok:
                obs["P5_error"] = why
                break
            t0 = time.perf_counter()
            self.api.cancel_stop(SYMBOL, self.cid(f"G{i}"))
            t1 = time.perf_counter()
            self.api.place_stop_market(
                SYMBOL, "SELL", self.price("0.80"), self.cid(f"H{i}"), quantity=None
            )
            t2 = time.perf_counter()
            gaps.append(int((t2 - t1) * 1000))
            obs.setdefault("P5_cancel_ms", []).append(int((t1 - t0) * 1000))
            self.api.cancel_stop(SYMBOL, self.cid(f"H{i}"))
            time.sleep(0.3)
        obs["P5_gap_ms_cancel_to_new_accepted"] = gaps
        obs["P6_always_protected_on_qty_path"] = always_protected if ok_c else None
        if gaps:
            print(f"  P5 공백(취소 응답 → 새 스톱 접수): {gaps} ms")

        print("5. 정리")
        self.cancel_all_stops()
        self.flatten(self.manager())
        time.sleep(0.5)
        final = self.recover()
        self.check(
            "정리 뒤 평탄·남은 스톱 0",
            self.position() == 0 and not self.working(),
            final.state,
        )
        self.outcome = "MEASURED"


def main() -> int:
    now = int(time.time() * 1000)
    if overlaps_daily_drill(now, 10):
        raise SystemExit("매일 자동 실행(21:00~21:20 KST) 구간과 겹친다 — 21:30 KST 이후 다시")
    api = BinanceUsdmTestnet(load_testnet_credentials())
    conn = connect(DB)
    apply_migrations(conn)
    drill = ReplaceDrill(
        api, FuturesOrderRepository(conn), RiskEventRepository(conn), now, Decimal(0), 0.0
    )
    error: str | None = None
    try:
        print(f"테스트넷 {api.base_url} · 지갑 {api.account().wallet_balance:f} USDT · 가짜 돈")
        drill.run()
    except Exception as exc:  # 어떤 실패든 보고서를 남기고 정리를 시도한다
        error = f"{type(exc).__name__}: {exc}"
        print(f"중단: {error}")
        try:
            drill.cancel_all_stops()
            drill.flatten(drill.manager(), purpose_attempt=1)
            drill.recover()
        except Exception as cleanup:
            print(f"정리 실패 — 테스트넷 ETH 포지션·스톱을 확인한다: {cleanup}")
    finally:
        api.close()
        conn.close()
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    REPORTS.mkdir(parents=True, exist_ok=True)
    path = REPORTS / f"replace-{stamp}.json"
    record: dict[str, Any] = {
        "at": stamp,
        "symbol": SYMBOL,
        "outcome": drill.outcome,
        "error": error,
        "checks": drill.checks,
        "observations": drill.observations,
    }
    path.write_text(
        json.dumps(record, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n"
    )
    print(f"기록: {path.relative_to(REPO)}")
    return 0 if error is None and all(c["pass"] for c in drill.checks) else 1


if __name__ == "__main__":
    sys.exit(main())
