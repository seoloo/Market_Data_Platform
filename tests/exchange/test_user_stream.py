"""사용자 스트림 이벤트 해석 — 2026-09-24 테스트넷 실측 원문으로 고정한다."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from auto_trading.exchange.user_stream import (
    AccountUpdate,
    AlgoUpdate,
    ListenKeyExpired,
    OrderUpdate,
    OtherEvent,
    parse_user_event,
)

# 실측 원문 (테스트넷, ETHUSDT). 발동한 스톱의 실제 주문은 스톱과 같은 ID 로 왔다.
TRIGGERED_FILL: dict[str, Any] = {
    "e": "ORDER_TRADE_UPDATE",
    "T": 1790220738059,
    "E": 1790220738060,
    "o": {
        "s": "ETHUSDT", "c": "wp-1790220275371-n", "S": "SELL", "o": "MARKET", "f": "GTC",
        "q": "0.01", "p": "0", "ap": "2673.74", "sp": "0", "x": "TRADE", "X": "FILLED",
        "i": 16803986671, "l": "0.01", "z": "0.01", "L": "2673.74", "n": "0.01069496",
        "N": "USDT", "T": 1790220738059, "t": 330837932, "b": "0", "a": "0", "m": False,
        "R": True, "wt": "MARK_PRICE", "ot": "MARKET", "ps": "BOTH", "cp": True, "rp": "-0.0251",
        "pP": False, "si": 1000000216366613, "ss": -1, "st": "ALGO_CONDITION",
        "V": "EXPIRE_MAKER", "pm": "NONE", "gtd": 0, "er": "0",
    },
}  # fmt: skip
PLAIN_NEW: dict[str, Any] = {
    "e": "ORDER_TRADE_UPDATE",
    "T": 1790220275647,
    "E": 1790220275648,
    "o": {
        "s": "ETHUSDT", "c": "wp-1790220275371-o", "S": "BUY", "o": "MARKET", "f": "GTC",
        "q": "0.01", "p": "0", "ap": "0", "sp": "0", "x": "NEW", "X": "NEW", "i": 16803982404,
        "l": "0", "z": "0", "L": "0", "n": "0", "N": "USDT", "T": 1790220275647, "t": 0,
        "R": False, "cp": False, "rp": "0", "si": 0, "ss": 0,
    },
}  # fmt: skip
ALGO_FINISHED: dict[str, Any] = {
    "e": "ALGO_UPDATE",
    "T": 1790220738123,
    "E": 1790220738139,
    "o": {
        "caid": "wp-1790220275371-n", "aid": 1000000216366613, "at": "CONDITIONAL",
        "o": "STOP_MARKET", "s": "ETHUSDT", "S": "SELL", "ps": "BOTH", "f": "GTC", "q": "0.01",
        "X": "FINISHED", "ai": "16803986671", "ap": "2673.74", "aq": "0.01", "act": "MARKET",
        "tp": "2674.58", "p": "0", "wt": "MARK_PRICE", "cp": True, "R": True,
        "tt": 1790220738059,
    },
}  # fmt: skip
ALGO_NEW: dict[str, Any] = {
    "e": "ALGO_UPDATE",
    "T": 1790220277771,
    "E": 1790220277771,
    "o": {
        "caid": "wp-1790220275371-s", "aid": 1000000216366553, "o": "STOP_MARKET",
        "s": "ETHUSDT", "S": "SELL", "q": "0", "X": "NEW", "ai": "", "tp": "2409.99",
    },
}  # fmt: skip
ACCOUNT: dict[str, Any] = {
    "e": "ACCOUNT_UPDATE",
    "T": 1790220738059,
    "E": 1790220738060,
    "a": {
        "B": [{"a": "USDT", "wb": "4998.93788145", "cw": "4998.93788145", "bc": "0"}],
        "P": [{"s": "ETHUSDT", "pa": "0", "ep": "0", "ps": "BOTH"}],
        "m": "ORDER",
    },
}


def test_triggered_stop_fill_links_to_algo() -> None:
    event = parse_user_event(TRIGGERED_FILL)
    assert isinstance(event, OrderUpdate)
    assert event.client_order_id == "wp-1790220275371-n"
    assert event.status == "FILLED"
    assert event.filled_qty == Decimal("0.01")
    assert event.avg_price == Decimal("2673.74")
    assert event.fee == Decimal("0.01069496")
    assert event.algo_id == "1000000216366613"
    assert event.close_position and event.reduce_only
    assert event.event_ms == 1790220738060


def test_plain_order_has_no_algo_id() -> None:
    event = parse_user_event(PLAIN_NEW)
    assert isinstance(event, OrderUpdate)
    assert event.algo_id is None  # si=0, st 없음
    assert event.status == "NEW" and event.filled_qty == 0


def test_algo_finished_carries_fill() -> None:
    event = parse_user_event(ALGO_FINISHED)
    assert isinstance(event, AlgoUpdate)
    assert event.client_algo_id == "wp-1790220275371-n"
    assert event.status == "FINISHED"
    assert event.actual_order_id == "16803986671"
    assert (event.filled_qty, event.avg_price) == (Decimal("0.01"), Decimal("2673.74"))
    assert event.trigger_price == Decimal("2674.58")


def test_algo_new_has_no_actual_order() -> None:
    event = parse_user_event(ALGO_NEW)
    assert isinstance(event, AlgoUpdate)
    assert event.actual_order_id is None
    assert event.filled_qty == 0


def test_account_update_positions_and_wallet() -> None:
    event = parse_user_event(ACCOUNT)
    assert isinstance(event, AccountUpdate)
    assert event.positions == {"ETHUSDT": Decimal(0)}
    assert event.wallet == {"USDT": Decimal("4998.93788145")}
    assert event.reason == "ORDER"


def test_listen_key_expired_and_unknown() -> None:
    assert isinstance(parse_user_event({"e": "listenKeyExpired", "E": 5}), ListenKeyExpired)
    other = parse_user_event({"e": "MARGIN_CALL", "E": 7})
    assert other == OtherEvent("MARGIN_CALL", 7)


def test_missing_required_field_raises() -> None:
    with pytest.raises(KeyError):
        parse_user_event({"e": "ORDER_TRADE_UPDATE", "E": 1, "o": {"s": "ETHUSDT"}})
