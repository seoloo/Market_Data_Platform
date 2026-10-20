"""현황 동기화 (사용자 2026-10-20 '항시 모든 현황 알림은 동기화') — 장부 사본 맞추기 (순수)."""

from __future__ import annotations

import json
from pathlib import Path

from auto_trading.ui import live_sync
from auto_trading.ui.live_home import Quotes, build_books, vega_book


def test_apply_vega_uses_upbit_balance_and_avg_price() -> None:
    st = {"expected": {"KRW": "296482", "BTC": "0"}, "orders": [], "deposits": [["d", "471507"]]}
    up = {"KRW": (300_458.0, 0.0), "BTC": (0.00063573, 110_076_000.0), "ETH": (0.0178, 3_234_000.0)}
    out = live_sync.apply_vega(st, up)
    assert out["expected"]["KRW"] == "300458.0" and float(out["expected"]["BTC"]) == 0.00063573
    assert abs(out["live_costs"]["BTC"] - 0.00063573 * 110_076_000.0) < 1e-6
    assert st["expected"]["KRW"] == "296482"  # 원래 장부는 그대로
    book = vega_book(out, Quotes(krw={"BTC": 110_000_000.0, "ETH": 3_200_000.0, "SOL": 140_000.0}))
    btc = next(h for h in book.holdings if h.name == "BTC")
    assert abs(btc.cost_krw - 0.00063573 * 110_076_000.0) < 1e-3


def _o(sym: str, side: str, oid: str, kind: str) -> dict[str, object]:
    return {"symbol": sym, "side": side, "qty": 1, "status": "SENT", "order_id": oid, "kind": kind}


def _deneb() -> dict[str, object]:
    return {
        "cash_usd": 220.39,
        "qty": {"TQQQ": 0.0, "SCHD": 0.0},
        "brake": {"reserve_usd": 10.0},
        "orders": [
            _o("TQQQ", "BUY", "a", "fill"),
            _o("SCHD", "BUY", "b", "fill"),
            _o("TQQQ", "SELL", "c", "brake"),
            _o("IAUM", "BUY", "d", ""),
        ],
    }


def test_apply_deneb_fills_cash_qty_and_reserve() -> None:
    toss = {
        "a": {"status": "FILLED", "filled_qty": 2, "filled_amount": 141.0, "commission": 0.1},
        "b": {"status": "CANCELED", "filled_qty": 0, "filled_amount": 0, "commission": 0},
        "c": {"status": "FILLED", "filled_qty": 1, "filled_amount": 70.0, "commission": 0.05},
        "d": {"status": "PENDING", "filled_qty": 0, "filled_amount": 0, "commission": 0},
    }
    out = live_sync.apply_deneb(_deneb(), toss)
    assert out["qty"] == {"TQQQ": 1.0, "SCHD": 0.0}  # +2 매수 −1 브레이크 매도
    assert abs(out["cash_usd"] - (220.39 - 141.1)) < 1e-9
    assert abs(out["brake"]["reserve_usd"] - (10.0 + 70.0 - 0.05)) < 1e-9
    st = [o["status"] for o in out["orders"]]
    assert st == ["FILLED", "EXPIRED", "FILLED", "SENT"]


def test_apply_plan_adds_buy_and_qty() -> None:
    st = {
        "symbol": "BTQ", "last_qty": 5.0, "buys": [{"date": "x", "qty": 5.0, "cost_usd": 12.65}],
        "orders": [{"date": "2026-10-19", "status": "SENT", "code": "z", "message": "BUY"}],
    }  # fmt: skip
    toss = {"z": {"status": "FILLED", "filled_qty": 1, "filled_amount": 2.3, "commission": 0.01}}
    out = live_sync.apply_plan(st, toss, 1333.0)
    assert out["last_qty"] == 6.0 and out["buys"][-1]["cost_usd"] == 2.3
    assert out["orders"][0]["status"] == "FILLED" and st["last_qty"] == 5.0
    assert live_sync.apply_plan({**st, "currency": "KRW"}, toss, 1333.0)["last_qty"] == 5.0


def test_sent_ids_newest_first_and_capped() -> None:
    deneb = {"orders": [{"status": "SENT", "order_id": f"o{i}", "date": f"2026-10-{i:02d}"}
                        for i in range(1, 30)]}  # fmt: skip
    ids = live_sync.sent_ids(deneb, [])
    assert len(ids) == live_sync.MAX_ORDERS and ids[0] == "o29"


def test_no_env_means_no_sync(tmp_path: Path) -> None:
    assert live_sync.load(tmp_path, None, []) is None
    up = tmp_path / "data" / "upbit_t2"
    up.mkdir(parents=True)
    (up / "state.json").write_text(json.dumps({"expected": {"KRW": "1000"}}), encoding="utf-8")
    books = build_books(tmp_path, Quotes())
    assert books and not any("실제 잔고" in n for b in books for n in b.notes)
