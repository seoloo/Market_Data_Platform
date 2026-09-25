"""지갑 이체 (53 V3): 멱등 의도·선기록·결과 모름을 이력 조회로만 해소·추정 재이체 금지."""

from __future__ import annotations

from decimal import Decimal

import pytest

from auto_trading.core.errors import ReconciliationError
from auto_trading.exchange.fake_wallet import FakeWallets
from auto_trading.exchange.wallet import TransferDirection, TransferRow
from auto_trading.execution.wallet_transfers import WalletTransferManager, transfer_intent_id
from auto_trading.storage.db import connect
from auto_trading.storage.migrations import apply_migrations
from auto_trading.storage.repositories.wallet_transfers import WalletTransferRepository

S2F = TransferDirection.SPOT_TO_FUTURES
F2S = TransferDirection.FUTURES_TO_SPOT
D = 1_790_207_999_999


class Clock:
    def __init__(self) -> None:
        self.now = 1_790_300_000_000

    def __call__(self) -> int:
        return self.now


def rig(
    **faults: int,
) -> tuple[WalletTransferManager, FakeWallets, Clock, WalletTransferRepository]:
    clock = Clock()
    wallets = FakeWallets(
        Decimal(1_000),
        Decimal(500),
        clock,
        fail_before_accept=faults.get("fail_before_accept", 0),
        lose_response_after_accept=faults.get("lose_response_after_accept", 0),
        fail_lookups=faults.get("fail_lookups", 0),
    )
    conn = connect(":memory:")
    apply_migrations(conn, now_ms=1)
    store = WalletTransferRepository(conn)
    m = WalletTransferManager(
        wallets, store, mode="testnet", strategy_id="o2", strategy_tag="o2", clock=clock
    )
    return m, wallets, clock, store


def test_intent_id_is_deterministic() -> None:
    assert transfer_intent_id("o2", D, S2F) == f"o2-{D}-S2F"
    assert transfer_intent_id("o2", D, F2S, 2) == f"o2-{D}-F2S-2"
    with pytest.raises(ValueError):
        transfer_intent_id("o2", D, S2F, -1)


def test_confirmed_transfer_moves_money_once_even_if_called_again() -> None:
    m, w, _, _ = rig()
    first = m.transfer(decision_ms=D, direction=S2F, amount=Decimal("100.009"))
    again = m.transfer(decision_ms=D, direction=S2F, amount=Decimal("100.009"))
    assert first.state == "CONFIRMED" and first.tran_id and first.amount == Decimal("100.00")
    assert again == first
    assert (w.spot, w.futures, len(w.history)) == (Decimal(900), Decimal(600), 1)


def test_rejected_transfer_moves_nothing() -> None:
    m, w, _, _ = rig()
    r = m.transfer(decision_ms=D, direction=F2S, amount=Decimal(600))  # 선물 지갑 500
    assert r.state == "REJECTED" and w.history == [] and not m.blocked()


def test_lost_response_is_resolved_from_history_without_resending() -> None:
    m, w, _, _ = rig(lose_response_after_accept=1)
    r = m.transfer(decision_ms=D, direction=S2F, amount=Decimal(100))
    assert r.state == "UNKNOWN" and m.blocked()
    with pytest.raises(ReconciliationError):  # 돈이 어디 있는지 모르면 새 이체 금지
        m.transfer(decision_ms=D + 1, direction=F2S, amount=Decimal(10))
    (done,) = m.recover()
    assert done.state == "CONFIRMED" and done.tran_id == w.history[0].tran_id
    assert len(w.history) == 1 and not m.blocked()


def test_not_sent_is_closed_only_after_the_resolve_window() -> None:
    m, w, clock, _ = rig(fail_before_accept=1)
    r = m.transfer(decision_ms=D, direction=S2F, amount=Decimal(100))
    assert r.state == "UNKNOWN"
    (early,) = m.recover()
    assert early.state == "UNKNOWN" and m.blocked()  # 이력이 늦게 보일 수 있다
    clock.now += 600_000
    (late,) = m.recover()
    assert late.state == "NOT_FOUND" and not m.blocked() and w.history == []
    # 다시 보내려면 재발행 번호를 올려 **명시적으로** 새 의도를 만든다
    retry = m.transfer(decision_ms=D, direction=S2F, amount=Decimal(100), attempt=1)
    assert retry.state == "CONFIRMED" and len(w.history) == 1


def test_crash_after_write_ahead_before_send() -> None:
    m, w, clock, store = rig()
    m.transfer(decision_ms=D, direction=S2F, amount=Decimal(50))
    record = store.all()[0]
    store.update(record.with_state("PENDING", clock(), tran_id=None))  # 응답 기록 전에 꺼진 척
    (fixed,) = m.recover()
    assert fixed.state == "CONFIRMED" and len(w.history) == 1  # 이력에서 찾았다


def test_same_amount_already_claimed_is_not_matched_twice() -> None:
    m, w, clock, _ = rig()
    m.transfer(decision_ms=D, direction=S2F, amount=Decimal(100))
    clock.now += 1_000
    w.lose_response_after_accept = 1
    lost = m.transfer(decision_ms=D + 86_400_000, direction=S2F, amount=Decimal(100))
    assert lost.state == "UNKNOWN"
    (done,) = m.recover()
    assert done.state == "CONFIRMED" and done.tran_id == w.history[1].tran_id


def test_ambiguous_history_stays_unknown_for_a_human() -> None:
    m, w, clock, _ = rig(lose_response_after_accept=1)
    m.transfer(decision_ms=D, direction=S2F, amount=Decimal(100))
    # 같은 시각대에 사람이 같은 금액을 손으로 옮겼다
    w.history.append(TransferRow("9999", S2F, "USDT", Decimal(100), "CONFIRMED", clock()))
    (r,) = m.recover()
    assert r.state == "UNKNOWN" and "사람 확인" in (r.error or "") and m.blocked()


def test_history_lookup_failure_keeps_unknown() -> None:
    m, _, _, _ = rig(lose_response_after_accept=1, fail_lookups=1)
    m.transfer(decision_ms=D, direction=S2F, amount=Decimal(100))
    (r,) = m.recover()
    assert r.state == "UNKNOWN" and "이력 조회 실패" in (r.error or "")


def test_amount_must_be_at_least_one_cent() -> None:
    m, _, _, _ = rig()
    with pytest.raises(ValueError):
        m.transfer(decision_ms=D, direction=S2F, amount=Decimal("0.009"))
