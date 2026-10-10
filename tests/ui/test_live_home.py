"""실거래 홈: 장부만 읽고, 공개 시세가 없으면 원가로 두며, Paper 를 더하지 않는다."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from auto_trading.ui import live_home
from auto_trading.ui.data import load_dashboard
from auto_trading.ui.live_home import Book, Quotes, build_books, fetch_quotes, render_live
from auto_trading.ui.render import render_page

LIVE_SCRIPT = '<script src="/assets/orbit-live.js" defer></script>'

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
    assert "오르빗" in html and "SOL" in html


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_desktop_home_has_explanations_and_read_only_navigation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, theme: str
) -> None:
    _seed(tmp_path)
    monkeypatch.setattr(live_home, "quote_source", lambda coins, tickers: _Q)
    html = render_page(load_dashboard(tmp_path, _NOW), "live", theme=theme)
    assert f'data-theme="{theme}" data-page="live"' in html
    assert '<a class="logo" href="/live">' in html
    for label in (
        "오늘도 오비와 함께",
        "화면 읽는 법",
        "미국 주식",
        "코인",
        "주식 모으기",
        "기준 금액",
        "총 입금 + 적립 매입원가",
        "확정 수익 아님",
        "확인할 사항",
        "주문 접수 ≠ 체결 완료",
        "자산 변화",
        "평가 기록 대기",
        "자동 적립",
        "연결 계좌",
    ):
        assert label in html
    assert 'class="orbit-holdings"' in html and 'class="orbit-book-detail"' in html
    for i in range(3):
        assert f'id="live-book-{i}"' in html
    for excluded in ('href="/research"', 'href="/compare"', 'href="/running"', "토스 적립"):
        assert excluded not in html
    assert html.replace(LIVE_SCRIPT, "").count("<script") == 0 and "<form" not in html


def test_missing_prices_hide_aggregate_profit_not_fabricate_a_return(tmp_path: Path) -> None:
    _seed(tmp_path)
    q = Quotes(errors=("업비트 시세 실패",))
    html = render_live(build_books(tmp_path, q), q, _NOW)
    hero = html.split('aria-labelledby="live-total-title"', 1)[1].split("</section>", 1)[0]
    assert "내 자산" in hero and "추정치 포함" in hero
    assert "시세 확인 필요" in hero and 'class="neg"' not in hero and 'class="pos"' not in hero
    assert "시세 확인 후 표시" in html


def test_missing_fx_does_not_mark_dollar_cash_as_confirmed() -> None:
    book = live_home.glide_book(
        {"deposits": [["2026-10-09", 130_000.0, 100.0, 1300.0]], "cash_usd": 100.0},
        Quotes(),
    )
    assert book.value_krw == 130_000.0  # 기존 환율 대체 계산은 유지
    html = render_live([book], Quotes(), _NOW)
    assert "추정 평가액" in html and "시세 확인 후 표시" in html


def test_stopped_book_is_warning_not_live_health_status(tmp_path: Path) -> None:
    _seed(tmp_path)
    p = tmp_path / "data/upbit_t2/state.json"
    state = json.loads(p.read_text(encoding="utf-8"))
    state["stopped"] = True
    _write(tmp_path, "data/upbit_t2/state.json", state)
    html = render_live(build_books(tmp_path, _Q), _Q, _NOW)
    assert "매수 정지 기록" in html and "매수 정지 기록을 확인하세요" in html
    assert "프로그램의 현재 가동 여부를 확정하지 않습니다" in html


@pytest.mark.parametrize("value", [0.0, -10.0])
def test_nonpositive_values_do_not_break_display(value: float) -> None:
    book = Book("glide", "Glide", "토스", 0.0, value, ())
    html = render_live([book], _Q, _NOW)
    assert "nan" not in html.lower() and "inf" not in html.lower()
    assert f"{value:,.0f}" in html


def test_empty_state_explains_missing_data_without_zero_balance() -> None:
    html = render_live([], Quotes(), _NOW)
    assert "아직 실거래 장부가 없습니다" in html and "화면 읽는 법" in html
    assert "기록 없음은 잔고 0원이나 운영 중단을 뜻하지 않습니다" in html
    assert 'class="orbit-total">—' in html
    assert "<strong>0<span>원</span>" not in html


def test_dynamic_live_content_and_unknown_book_key_are_escaped() -> None:
    bad = '<script>alert("test")</script>'
    book = Book(bad, bad, bad, 100.0, 110.0, (), (bad,), (bad,), ((bad, bad),))
    html = render_live([book], Quotes(errors=(bad,)), _NOW)
    assert bad not in html and "&lt;script&gt;" in html
    assert bad not in html.split('<div class="orbit-cards">')[1].split("</div>", 1)[0]
    assert "<script" not in html and "<form" not in html


def test_read_only_cost_display_matches_existing_ledger_for_partial_sales() -> None:
    from auto_trading.ops.t2_upbit import books

    orders: list[dict[str, object]] = [
        {
            "identifier": "t2u-2026-10-01-BTC-B",
            "side": "bid",
            "executed_volume": "2",
            "funds": "200",
            "paid_fee": "2",
        },
        {
            "identifier": "t2u-2026-10-02-BTC-B",
            "side": "bid",
            "executed_volume": "1",
            "funds": "150",
            "paid_fee": "1.5",
        },
        {
            "identifier": "t2u-2026-10-03-BTC-S",
            "side": "ask",
            "executed_volume": "0.5",
            "funds": "80",
            "paid_fee": "0.8",
        },
        {
            "id": "t2u-2026-10-04-ETH-B",
            "side": "bid",
            "executed_volume": "1",
            "funds": "50",
            "paid_fee": "0.05",
        },
        {
            "identifier": "t2u-2026-10-05-ETH-S",
            "side": "ask",
            "executed_volume": "2",
            "funds": "70",
            "paid_fee": "0.07",
        },
        {"identifier": "external-BTC-B", "side": "bid", "executed_volume": "0"},
        {"identifier": "unknown-OTHER-B", "side": "bid", "executed_volume": "1"},
    ]
    for end in range(len(orders) + 1):
        expected = books(orders[:end])
        assert live_home._stored_coin_costs(orders[:end]) == {
            coin: record.cost for coin, record in expected.items()
        }


def test_summary_text_for_phone(tmp_path: Path) -> None:
    from auto_trading.ui.live_home import summary_text

    _seed(tmp_path)
    text = summary_text(build_books(tmp_path, _Q), _Q, _NOW)
    assert text.startswith("📊 [실거래 현황] 10/09 22:00")
    assert "📈 Glide · 토스" in text and "🛫 Cruise · 업비트" in text
    assert "🟢 BTC" in text and "⏳ SCHD 3주" in text
    assert "원/달러 1,300.0" in text


def test_orbit_keeps_sol_and_actual_holding_value(tmp_path: Path) -> None:
    _seed(tmp_path)
    state = json.loads((tmp_path / "data/upbit_t2/state.json").read_text(encoding="utf-8"))
    state["expected"]["SOL"] = "2"
    state["orders"].append(
        {
            "identifier": "t2u-2026-10-09-SOL-B",
            "side": "bid",
            "executed_volume": "2",
            "funds": "300000",
            "paid_fee": "150",
        }
    )
    _write(tmp_path, "data/upbit_t2/state.json", state)
    quotes = Quotes({"BTC": 110_000_000.0, "SOL": 180_000.0}, _Q.usd, _Q.fx, _NOW)
    html = render_live(build_books(tmp_path, quotes), quotes, _NOW)
    assert "솔라나" in html and "360,000원" in html
    assert "<b>SOL</b>" in html  # 헤더 배지는 제거해도 실제 보유 목록은 유지한다.
    assert "coin-sol" in html and "보유 종목 (2개)" in html


def test_orbit_does_not_invent_history_or_account_status(tmp_path: Path) -> None:
    _seed(tmp_path)
    html = render_live(build_books(tmp_path, _Q), _Q, _NOW)
    assert "평가 기록 대기" in html
    assert "계좌가 정상적으로 연결" not in html
    assert "2,034,000" not in html  # 승인 PNG의 예시 금액을 복제하지 않는다
    assert '<path d="M' not in html.split('class="orbit-chart-empty"', 1)[1].split("</div>", 1)[0]
    assert 'name="orbit-range"' in html


def test_orbit_render_preserves_stored_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import hashlib

    _seed(tmp_path)
    paths = tuple((tmp_path / "data").rglob("*.json"))
    before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    monkeypatch.setattr(live_home, "quote_source", lambda coins, tickers: _Q)
    for theme in ("light", "dark"):
        render_page(load_dashboard(tmp_path, _NOW), "live", theme=theme)
    assert {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths} == before


def test_telegram_bot_replies_once_and_leaves_amounts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import scripts.telegram_bot as bot_mod

    from auto_trading.notify.inbox import InboxMessage

    _seed(tmp_path)
    sent: list[str] = []
    msgs = [InboxMessage(5, 0, "현황"), InboxMessage(6, 0, "수익"), InboxMessage(7, 0, "30만")]

    class FakeInbox:
        def __init__(self, creds: object) -> None:
            pass

        def fetch(self, offset: int) -> tuple[list[InboxMessage], int]:
            out = list(msgs)
            msgs.clear()
            return out, 8 if out else offset

    class FakeBot:
        def __init__(self, creds: object) -> None:
            pass

        def send(self, text: str) -> None:
            sent.append(text)

        def close(self) -> None:
            pass

    monkeypatch.setattr(bot_mod, "load_telegram_credentials", lambda path: object())
    monkeypatch.setattr(bot_mod, "TelegramInbox", FakeInbox)
    monkeypatch.setattr(bot_mod, "TelegramNotifier", FakeBot)
    monkeypatch.setattr(bot_mod, "fetch_quotes", lambda coins, tickers: _Q)
    assert bot_mod.run(tmp_path) == 0
    assert len(sent) == 1 and "실거래 현황" in sent[0]
    queue = json.loads((tmp_path / "data/telegram/inbox.json").read_text(encoding="utf-8"))
    assert [p["msg"]["text"] for p in queue["pending"]] == ["30만"]  # Glide 몫은 남는다
    assert bot_mod.run(tmp_path) == 0 and len(sent) == 1  # 새 명령 없으면 조용
