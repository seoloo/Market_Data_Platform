"""나무 해외주식 원화 주문 클라이언트 — 가짜 서버 (실제 호출 없음)."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest

from auto_trading.config.secrets import NamuCredentials, load_namu_credentials
from auto_trading.core.errors import OrderStateUnknownError
from auto_trading.exchange.namu import LIVE_URL, MOCK_URL, NamuError, NamuTokenCache
from auto_trading.exchange.namu_global import NamuGlobal
from auto_trading.exchange.overseas import TradingDisabledError

T0 = 1_791_985_600.0  # 2026-10-14 22:46 KST (미국 장중)


def _nh(
    tmp_path: Path, handler: Any, *, live: bool = True, on: bool = True
) -> tuple[NamuGlobal, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def wrap(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        if req.url.path == "/n2/acctinfo":
            rows = [
                {"acct_no": "11111111101", "acct_type": "01"},
                {"acct_no": "22222222203", "acct_type": "03"},
            ]
            return httpx.Response(200, json={"Output_0": rows})
        return handler(req)  # type: ignore[no-any-return]

    cache = NamuTokenCache(tmp_path / "tok.json")
    cache.save("cached", 2e9)
    nh = NamuGlobal(
        NamuCredentials("k", "s", trading_enabled=on),
        cache,
        base_url=LIVE_URL if live else MOCK_URL,
        client=httpx.Client(transport=httpx.MockTransport(wrap)),
        clock=lambda: T0,
        sleep=lambda s: None,
        allowed=frozenset({"VOO"}),
        max_qty=2,
        max_order_usd=1_500.0,
    )
    return nh, seen


def _body(req: httpx.Request) -> dict[str, Any]:
    return dict(json.loads(req.content)["Input_0"])


def test_switch_defaults_off(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("NAMU_APP_API_KEY=a\nNAMU_APP_SECRET_API_KEY=b\n", encoding="utf-8")
    assert load_namu_credentials(env, environ={}).trading_enabled is False
    env.write_text(env.read_text() + "NAMU_TRADING_ENABLED=yes\n", encoding="utf-8")
    assert load_namu_credentials(env, environ={}).trading_enabled is False
    assert "zz-secret-9" not in repr(load_namu_credentials(env, environ={}))


def test_live_buy_uses_krw_margin_limit_and_returns_dated_id(tmp_path: Path) -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        b = _body(req)
        assert req.url.path == "/gbstock/order/v1/buy" and req.url.host == "api.nhplug.com"
        assert b["act_no"] == "11111111101" and b["wtm_cur_knd_cd"] == "2"
        assert b["ahi_nmn_pr_tp_cd"] == "00" and b["fc_orr_uit_pr"] == 699.5
        return httpx.Response(200, json={"Output_0": {"orr_no": 4321}})

    nh, _ = _nh(tmp_path, handler)
    r = nh.order("voo", "BUY", 2, Decimal("699.50"))
    assert r.accepted and r.code == "20261014:4321"


def test_guards_block_before_any_request(tmp_path: Path) -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        raise AssertionError("요청이 나가면 안 된다")

    off, seen = _nh(tmp_path, handler, on=False)
    with pytest.raises(TradingDisabledError, match="NAMU_TRADING_ENABLED"):
        off.order("VOO", "BUY", 1, Decimal("700"))
    with pytest.raises(TradingDisabledError):
        off.cancel("20261014:1", "VOO")
    nh, seen = _nh(tmp_path, handler)
    for args in [
        ("TSLA", "BUY", 1, Decimal("300")),  # 허용 밖
        ("VOO", "BUY", 3, Decimal("100")),  # 수량 상한
        ("VOO", "BUY", 2, Decimal("800")),  # 금액 상한 $1,600 > $1,500
        ("VOO", "BUY", 1, Decimal("700.123")),  # 센트 단위 아님
        ("VOO", "HOLD", 1, Decimal("700")),
    ]:
        with pytest.raises(TradingDisabledError):
            nh.order(*args)
    with pytest.raises(TradingDisabledError):
        nh.order("VOO", "BUY", 1, Decimal("700"), time_in_force="GTC")
    assert seen == []


def test_mock_needs_no_switch_and_buys_in_usd(tmp_path: Path) -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        b = _body(req)
        assert req.url.host == "moapi.nhplug.com" and b["act_no"] == "22222222203"
        assert b["wtm_cur_knd_cd"] == "1"
        return httpx.Response(200, json={"Output_0": {"orr_no": 9}})

    nh, _ = _nh(tmp_path, handler, live=False, on=False)
    assert nh.order("VOO", "BUY", 1, Decimal("700")).accepted


def test_unknown_result_never_retries(tmp_path: Path) -> None:
    calls = {"n": 0}

    def boom(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise httpx.ReadTimeout("x")

    nh, _ = _nh(tmp_path, boom)
    with pytest.raises(OrderStateUnknownError):
        nh.order("VOO", "BUY", 1, Decimal("700"))
    assert calls["n"] == 1

    def five(req: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"message": {"usr_msg": "점검"}})

    nh, _ = _nh(tmp_path, five)
    with pytest.raises(OrderStateUnknownError):
        nh.order("VOO", "BUY", 1, Decimal("700"))

    def reject(req: httpx.Request) -> httpx.Response:
        msg = {"msg_code": "14300", "usr_msg": "주문가능금액 부족"}
        return httpx.Response(200, json={"message": msg})

    nh, _ = _nh(tmp_path, reject)
    r = nh.order("VOO", "BUY", 1, Decimal("700"))
    assert not r.accepted and r.code == "14300" and "부족" in r.message


def _row(no: int, **kw: Any) -> dict[str, Any]:
    base = {"orr_no": no, "org_orr_no": 0, "iem_cd": "VOO", "orr_qty": 2, "cns_qty": 0}
    return {**base, "ny_cns_orr_qty": 0, "can_qty": 0, "cns_pr": 0, "rjt_rsn_cts": "", **kw}


@pytest.mark.parametrize(
    ("rows", "status", "filled"),
    [
        ([_row(7, ny_cns_orr_qty=2)], "PENDING", 0),
        ([_row(7, cns_qty=1, ny_cns_orr_qty=1, cns_pr=700)], "PARTIAL_FILLED", 1),
        ([_row(7, cns_qty=2, cns_pr=700)], "FILLED", 2),
        ([_row(7, can_qty=2)], "CANCELED", 0),
        ([_row(7, cns_qty=1, cns_pr=700), _row(8, org_orr_no=7, can_qty=1)], "CANCELED", 1),
        ([_row(7, rjt_rsn_cts="잔고 부족")], "REJECTED", 0),
    ],
)
def test_order_status_mapping(
    tmp_path: Path, rows: list[dict[str, Any]], status: str, filled: int
) -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/gbstock/inquiry/v1/unexecuted"
        return httpx.Response(
            200, json={"Output_0": rows if _body(req)["orr_dt"] == "20261014" else []}
        )

    nh, _ = _nh(tmp_path, handler)
    st = nh.order_status("20261014:7")
    assert st.status == status and st.filled_qty == filled
    if filled:
        assert st.filled_amount == Decimal("700") * filled


def test_order_found_next_kst_day_and_missing_is_unknown(tmp_path: Path) -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        day = _body(req)["orr_dt"]
        rows = [_row(7, cns_qty=2, cns_pr=10)] if day == "20261015" else []
        return httpx.Response(200, json={"Output_0": rows})

    nh, _ = _nh(tmp_path, handler)
    assert nh.order_status("20261014:7").status == "FILLED"
    assert nh.order_status("20261014:99").status == ""
    with pytest.raises(ValueError):
        nh.order_status("7")


def test_quote_book_and_live_account_must_be_single(tmp_path: Path) -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.host == "api.nhplug.com"
        o = {"trdprc": 700.1, "best_ask1": 700.2, "best_asiz1": 5, "best_ask2": 700.3}
        o |= {"best_asiz2": 9, "best_bid1": 700.0, "best_bsiz1": 4, "currency_prc": 1334}
        return httpx.Response(200, json={"Output_0": o})

    nh, _ = _nh(tmp_path, handler, live=False)
    q = nh.quote("voo")
    assert q.asks == [(Decimal("700.2"), Decimal(5)), (Decimal("700.3"), Decimal(9))]
    assert q.bids == [(Decimal("700.0"), Decimal(4))] and q.fx == 1334

    def two(req: httpx.Request) -> httpx.Response:
        rows = [{"acct_no": "1", "acct_type": "01"}, {"acct_no": "2", "acct_type": "01"}]
        return httpx.Response(200, json={"Output_0": rows})

    cache = NamuTokenCache(tmp_path / "t2.json")
    cache.save("c", 2e9)
    nh2 = NamuGlobal(
        NamuCredentials("k", "s"),
        cache,
        base_url=LIVE_URL,
        client=httpx.Client(transport=httpx.MockTransport(two)),
    )
    with pytest.raises(NamuError, match="하나여야"):
        nh2.account()
    with pytest.raises(ValueError):
        NamuGlobal(NamuCredentials("k", "s"), cache, base_url="https://evil")
