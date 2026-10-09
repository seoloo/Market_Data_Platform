"""실거래 홈: 장부만 읽고, 공개 시세가 없으면 원가로 두며, Paper 를 더하지 않는다."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from auto_trading.ui import live_home
from auto_trading.ui.data import load_dashboard
from auto_trading.ui.live_home import Quotes, build_books, fetch_quotes, render_live
from auto_trading.ui.render import render_page

_KST = timezone(timedelta(hours=9))
_NOW = int(datetime(2026, 10, 9, 22, tzinfo=_KST).timestamp() * 1000)
_Q = Quotes({"BTC": 110_000_000.0}, {"TQQQ": 80.0, "SCHD": 30.0, "BTQ": 3.0}, 1300.0, _NOW)


def _write(root: Path, rel: str, obj: dict[str, Any]) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj), encoding="utf-8")


def _seed(root: Path) -> None:
    _write(
        root,
        "data/toss/M3.json",
        {
            "cash_usd": 10.0,
            "qty": {"TQQQ": 1.0, "SCHD": 0.0},
            "deposits": [["2026-10-09", 130_000.0, 100.0, 1300.0]],
            "months": [["2026-10", 0.15, 0.6]],
            "orders": [
                {
                    "date": "2026-10-09",
                    "symbol": "TQQQ",
                    "status": "FILLED",
                    "qty": 1,
                    "filled_qty": 1.0,
                    "filled_usd": 85.0,
                    "commission": 0.0,
                },
                {
                    "date": "2026-10-09",
                    "symbol": "SCHD",
                    "status": "SENT",
                    "qty": 3,
                    "limit": 34.0,
                    "filled_qty": 0.0,
                },
            ],
            "amounts": {"2026-10": 130_000.0},
        },
    )
    _write(
        root,
        "data/upbit_t2/state.json",
        {
            "units": "100",
            "peak": "1000",
            "deposits": [["2026-10-08", "100000"]],
            "expected": {"KRW": "50000", "BTC": "0.0005"},
            "orders": [
                {
                    "id": "t2u-2026-10-08-BTC-B",
                    "identifier": "t2u-2026-10-08-BTC-B",
                    "line": "🛒 BTC 매수",
                    "side": "bid",
                    "executed_volume": "0.0005",
                    "funds": "50000",
                    "paid_fee": "25",
                }
            ],
            "last_trade_signals": {"BTC": {"weight": "0.5"}},
        },
    )
    _write(
        root,
        "data/toss/BTQ.json",
        {
            "symbol": "BTQ",
            "last_qty": 1.0,
            "buys": [
                {"date": "2026-10-08", "qty": 1, "cost_usd": 3.5, "fx_rate": 1300.0, "cost": 3.5035}
            ],
        },
    )
    _write(root, "data/toss/QS_SWITCH.json", {"symbol": "QS", "last_qty": 99.0})


def test_books_value_with_public_quotes(tmp_path: Path) -> None:
    _seed(tmp_path)
    books = {b.key: b for b in build_books(tmp_path, _Q)}
    assert set(books) == {"glide", "cruise", "accumulate"}  # QS_SWITCH(Paper) 제외
    g = books["glide"]
    assert g.deposits_krw == 130_000
    assert g.value_krw == pytest.approx(10 * 1300 + 80 * 1300)
    assert g.holdings[0].cost_krw == pytest.approx(85 * 1300)
    assert g.pending == ("SCHD 3주 · 지정가 $34.00",)
    c = books["cruise"]
    assert c.value_krw == pytest.approx(50_000 + 0.0005 * 110_000_000)
    assert c.holdings[0].cost_krw == pytest.approx(50_025)
    assert "🟢" in c.holdings[0].note
    a = books["accumulate"]
    assert a.deposits_krw == pytest.approx(3.5 * 1300)  # 'cost' 칸(단위 제각각)은 안 쓴다
    assert a.value_krw == pytest.approx(3.0 * 1300)


def test_missing_quotes_fall_back_to_cost(tmp_path: Path) -> None:
    _seed(tmp_path)
    q = Quotes(errors=("업비트 시세 실패: ConnectError",))
    books = build_books(tmp_path, q)
    cruise = next(b for b in books if b.key == "cruise")
    assert cruise.value_krw == pytest.approx(50_000 + 50_025)
    html = render_live(books, q, _NOW)
    assert "원가로 셈" in html and "업비트 시세 실패" in html


def test_fetch_quotes_parses_and_caches() -> None:
    calls: list[str] = []

    def get(url: str) -> Any:
        calls.append(url)
        if "upbit" in url:
            return [{"market": "KRW-BTC", "trade_price": 100.0}]
        if "BAD" in url:
            raise OSError("down")
        price = 1350.0 if "KRW=X" in url else 50.0
        return {"chart": {"result": [{"meta": {"regularMarketPrice": price}}]}}

    live_home._cache.clear()
    q = fetch_quotes({"BTC"}, {"TQQQ", "BAD"}, get)
    assert q.krw == {"BTC": 100.0} and q.usd == {"TQQQ": 50.0} and q.fx == 1350.0
    assert any("BAD" in e for e in q.errors)
    n = len(calls)
    fetch_quotes({"BTC"}, {"TQQQ", "BAD"}, get)
    assert len(calls) == n  # 60 초 캐시
    live_home._cache.clear()


def test_live_page_in_nav_without_network(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _seed(tmp_path)
    monkeypatch.setattr(live_home, "quote_source", lambda coins, tickers: _Q)
    html = render_page(load_dashboard(tmp_path, _NOW), "live")
    assert 'href="/live"' in html and "총 입금" in html and "Cruise" in html
    assert "11/02" in html  # 다음 Glide = 11 월 첫 평일
