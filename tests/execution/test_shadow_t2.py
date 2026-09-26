"""T/v2 그림자 운영 점검 함수 (51): 호가 미끄러짐 추정과 실거래 준비 점검."""

from __future__ import annotations

from decimal import Decimal

import pytest

from auto_trading.execution.shadow_t2 import MUST_BE_OFF, book_slippage_bps, readiness_findings

ASKS = [(Decimal(101), Decimal(1)), (Decimal(102), Decimal(1)), (Decimal(104), Decimal(10))]
MID = Decimal(100)
READ_ONLY = {"enableReading": True, "ipRestrict": True, **dict.fromkeys(MUST_BE_OFF, False)}
PRICES = {"BTCUSDT": Decimal(100)}


class TestSlippage:
    def test_single_level(self) -> None:
        assert book_slippage_bps(ASKS, Decimal(50), mid=MID) == Decimal(100)  # 101 vs 100

    def test_walks_levels(self) -> None:
        # 101 × 1 + 102 × 1 = 203 USDT → 평균 101.5 → 150 bps
        assert book_slippage_bps(ASKS, Decimal(203), mid=MID) == Decimal(150)

    def test_not_enough_depth(self) -> None:
        assert book_slippage_bps(ASKS, Decimal(10_000), mid=MID) is None

    def test_rejects_bad_input(self) -> None:
        with pytest.raises(ValueError):
            book_slippage_bps(ASKS, Decimal(0), mid=MID)


def findings(**kw: object) -> list[tuple[str, str]]:
    base: dict[str, object] = {
        "restrictions": READ_ONLY,
        "clock_offset_ms": 100,
        "balances": {"USDT": Decimal(2_000)},
        "prices": PRICES,
        "allocation": Decimal(1_000),
        "halted_reason": None,
    }
    base.update(kw)
    return [(f.level, f.message) for f in readiness_findings(**base)]  # type: ignore[arg-type]


class TestReadiness:
    def test_ready_account_has_no_findings(self) -> None:
        assert findings() == []

    def test_trading_permission_before_phase8_is_critical(self) -> None:
        out = findings(restrictions={**READ_ONLY, "enableSpotAndMarginTrading": True})
        assert out[0][0] == "CRITICAL" and "enableSpotAndMarginTrading" in out[0][1]

    def test_missing_ip_restriction_is_critical(self) -> None:
        assert ("CRITICAL", "키에 IP 제한이 없음") in findings(
            restrictions={**READ_ONLY, "ipRestrict": False}
        )

    def test_unreadable_account_and_clock_are_warnings(self) -> None:
        out = findings(restrictions=None, balances=None, clock_offset_ms=None)
        assert [lvl for lvl, _ in out] == ["WARN", "WARN", "WARN"]
        assert findings(clock_offset_ms=1500)[0][0] == "WARN"

    def test_small_balance_is_info_and_counts_coins(self) -> None:
        assert findings(balances={"USDT": Decimal(500)})[0][0] == "INFO"
        assert findings(balances={"USDT": Decimal(500), "BTC": Decimal(6)}) == []  # 500 + 600

    def test_halt_and_tracking_gaps_are_warnings(self) -> None:
        out = findings(halted_reason="점검", rejected_orders=["BTCUSDT 목표 0.2 vs 실제 0.1"])
        assert [lvl for lvl, _ in out] == ["WARN", "WARN"]


def test_ip_whitelist_rejection_is_named() -> None:
    """문서 84 §3: 집 공인 IP 가 바뀌면 -2015 — 원인을 짐작할 수 있게 적는다."""
    out = findings(
        restrictions=None,
        balances=None,
        account_error="Binance 오류 401 code=-2015: Invalid API-key, IP, or permissions",
    )
    assert any("공인 IP" in msg for _, msg in out)
    assert not any("공인 IP" in msg for _, msg in findings(account_error="timeout"))
