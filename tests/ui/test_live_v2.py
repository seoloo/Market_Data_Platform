"""실거래 홈 보완 (2026-10-10): 금액 숨기기 · 갱신 시각 · 추정 · 거래 · 그래프 · 계좌 · 쿠키."""

from __future__ import annotations

import json
import os
import re
import threading
import urllib.request
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from http.cookiejar import CookieJar
from pathlib import Path
from typing import Any

import pytest

from auto_trading.ui.catchup import CatchupRunner
from auto_trading.ui.live_home import Book, Holding, Quotes, Trade, build_books, load_history
from auto_trading.ui.orbit_home import _nav_points, render_live
from auto_trading.ui.server import make_server

_KST = timezone(timedelta(hours=9))
_NOW = int(datetime(2026, 10, 10, 15, tzinfo=_KST).timestamp() * 1000)
_H = 3_600_000


@pytest.fixture(autouse=True)
def _show_upbit(monkeypatch: pytest.MonkeyPatch) -> None:
    """이 시험들은 업비트 Vega 장부로 화면 규칙을 본다 (실화면은 2026-10-15 부터 숨김)."""
    from auto_trading.ui import live_home

    monkeypatch.setattr(live_home, "SHOW_UPBIT", True)


def _write(root: Path, rel: str, obj: dict[str, Any]) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj), encoding="utf-8")
    return path


def _book(key: str = "vega", *, updated_ms: int = _NOW - _H, value: float | None = 120.0) -> Book:
    return Book(
        key,
        key.title(),
        "장부",
        100.0,
        10.0,
        (Holding("BTC", 0.5, 100.0, value, "🟢 추세 켜짐"),),
        trades=(
            Trade("2026-10-09", "buy", "BTC", 0.5, 200.0, 100.0, 0.05, "KRW", "체결"),
            Trade("2026-10-08", "deposit", "KRW", amount=110.0, status="입금 기록"),
            Trade("2026-10-09", "buy", "SCHD", 3, 34.0, 0.0, 0.0, "USD", "접수 · 체결 확인 대기"),
        ),
        updated_ms=updated_ms,
    )


def test_hide_masks_every_amount_but_keeps_structure() -> None:
    html = render_live([_book()], Quotes(at_ms=_NOW), _NOW, hide=True)
    main = html.split('class="orbit-networth"', 1)[1]
    assert "•••••" in main and "보이기" in html
    for secret in ("130", "120", "110원", "0.5주", "+20"):
        assert secret not in re.sub(r"<svg.*?</svg>", "", main)
    assert 'href="/live?amounts=show"' in html
    shown = render_live([_book()], Quotes(at_ms=_NOW), _NOW)
    assert 'href="/live?amounts=hide"' in shown and "130<small>원</small>" in shown


def test_update_stamps_and_stale_ledger_are_visible() -> None:
    fresh = render_live([_book()], Quotes(at_ms=_NOW), _NOW)
    assert "장부 업데이트 10/10 14:00" in fresh and "시세 업데이트 10/10 15:00" in fresh
    assert "오래된" not in fresh
    old = render_live([_book(updated_ms=_NOW - 40 * _H)], Quotes(at_ms=_NOW), _NOW)
    assert "오래된 기록" in old and "오래된 장부 1개" in old and "orbit-stale" in old
    assert "장부가 오래됨" in old  # 확인할 사항에도
    # Deneb 은 매달 도니 40 시간은 오래된 게 아니다
    assert "오래된" not in render_live([_book("deneb", updated_ms=_NOW - 40 * _H)], Quotes(), _NOW)


def test_missing_price_gets_estimate_badge_not_confirmed_value() -> None:
    html = render_live([_book(value=None)], Quotes(at_ms=_NOW), _NOW)
    assert html.count('class="orbit-est"') >= 3  # 총자산 · 카드 · 종목
    assert "시세 미확인" in html


def test_holding_detail_popup_has_numbers_and_no_order_button() -> None:
    html = render_live([_book()], Quotes(usd={}, at_ms=_NOW), _NOW)
    assert 'popovertarget="orbit-h-vega-0"' in html and 'id="orbit-h-vega-0"' in html
    pop = html.split('id="orbit-h-vega-0"', 1)[1].split("</section>", 1)[0]
    for label in ("보유 수량", "평균 매입단가", "현재 가격", "평가금액", "평가손익", "가격 기준"):
        assert label in pop
    assert "200원" in pop and "240원" in pop  # 원가 100 ÷ 0.5 · 평가 120 ÷ 0.5
    assert "주문" not in pop.replace("주문 기능 없음", "")


def test_trades_have_details_status_and_filter() -> None:
    html = render_live([_book()], Quotes(), _NOW)
    pop = html.split('id="orbit-all-trades"', 1)[1].split("</section>", 1)[0]
    for k in ("all", "buy", "sell", "deposit"):
        assert f'id="tf-{k}"' in pop
    assert 'class="kind-buy"' in pop and 'class="kind-deposit"' in pop
    assert "체결가 200원" in pop and "수수료 1원 미만" in pop and "state-wait" in pop
    assert "접수 · 체결 확인 대기" in pop and "입금 기록" in pop
    assert "가격 $34.00" in pop  # 접수만 된 주문은 '체결가' 가 아니다
    # 매도 기록이 없으면 매도 필터에 빈 칸 대신 한 줄 (매수 · 입금은 있으니 없음)
    assert "매도 기록이 아직 없습니다" in pop and "매수 기록이 아직" not in pop
    css = Path("src/auto_trading/ui/orbit.css").read_text(encoding="utf-8")
    assert "#orbit-all-trades{height:" in css  # 필터를 바꿔도 창 크기 고정


def test_accounts_show_last_success_missing_and_stale() -> None:
    html = render_live([_book(), _book("deneb", updated_ms=_NOW - 900 * _H)], Quotes(), _NOW)
    pop = html.split('id="orbit-accounts"', 1)[1].split("</section>", 1)[0]
    assert "마지막 조회 성공 · 10/10 14:00" in pop
    assert "오래된 장부" in pop and "연결 미확인 · 장부 없음" in pop
    assert "API 키는 표시하지 않습니다" in pop


def test_onboarding_only_when_not_seen() -> None:
    assert "처음 오셨나요" not in render_live([], Quotes(), _NOW)
    first = render_live([], Quotes(), _NOW, onboarded=False)
    assert "처음 오셨나요" in first and "매매 프로그램은 멈추지 않습니다" in first
    assert 'href="/live?onboard=done"' in first


def test_history_chart_only_from_priced_records_and_removes_deposits() -> None:
    rows = [
        {"date": "2026-10-01", "value": 100.0, "deposits": 100.0, "priced": True},
        {"date": "2026-10-02", "value": 210.0, "deposits": 200.0, "priced": True},  # 입금 100 + 10
        {"date": "2026-10-03", "value": 999.0, "deposits": 200.0, "priced": False},  # 추정 → 빼기
        {"date": "2026-10-04", "value": 231.0, "deposits": 200.0, "priced": True},
    ]
    pts = _nav_points(rows)
    assert [p[0] for p in pts] == ["2026-10-01", "2026-10-02", "2026-10-04"]
    assert pts[1][3] == pytest.approx(1.10) and pts[2][3] == pytest.approx(1.21)
    html = render_live([_book()], Quotes(), _NOW, history=rows)
    assert 'class="orbit-plot p-w1 v-asset"' in html and 'class="orbit-plot p-m1 v-perf"' in html
    assert "입출금 뺀 수익률 +21.00%" in html
    one = render_live([_book()], Quotes(), _NOW, history=rows[:1])
    assert "2026-10-01 부터 기록 중" in one and "orbit-plot-svg" not in one


def test_build_books_reads_trades_and_mtime(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        "data/upbit_t2/state.json",
        {
            "deposits": [["2026-10-08", "100000"]],
            "expected": {"KRW": "50000", "BTC": "0.0005"},
            "orders": [
                {
                    "id": "t2u-2026-10-08-BTC-B",
                    "side": "bid",
                    "executed_volume": "0.0005",
                    "funds": "50000",
                    "paid_fee": "25",
                    "state": "done",
                },
                {
                    "id": "t2u-2026-10-09-ETH-S",
                    "side": "ask",
                    "executed_volume": "0",
                    "state": "cancel",
                },
            ],
        },
    )
    os.utime(p, (_NOW / 1000, _NOW / 1000))
    (book,) = build_books(tmp_path, Quotes())
    assert book.updated_ms == _NOW
    kinds = {(t.kind, t.symbol, t.status) for t in book.trades}
    assert ("buy", "BTC", "체결") in kinds and ("sell", "ETH", "체결 없음") in kinds
    assert ("deposit", "KRW", "입금 기록") in kinds
    btc = next(t for t in book.trades if t.symbol == "BTC")
    assert btc.price == pytest.approx(100_000_000) and btc.fee == pytest.approx(25)


def test_history_file_keeps_last_row_per_day_and_skips_broken(tmp_path: Path) -> None:
    path = tmp_path / "data/live/history.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text(
        '{"date": "2026-10-02", "value": 1}\nnot json\n'
        '{"date": "2026-10-01", "value": 5}\n{"date": "2026-10-02", "value": 2}\n',
        encoding="utf-8",
    )
    assert [(r["date"], r["value"]) for r in load_history(tmp_path)] == [
        ("2026-10-01", 5),
        ("2026-10-02", 2),
    ]


class TestLiveServer:
    @pytest.fixture
    def base(self, tmp_path: Path) -> Iterator[str]:
        runner = CatchupRunner(tmp_path, query=lambda: [], run=lambda *a, **k: None)
        server = make_server(tmp_path, port=0, catchup=runner)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_address[1]}"
        finally:
            server.shutdown()
            server.server_close()

    def test_live_csp_allows_only_own_script_and_others_stay_strict(self, base: str) -> None:
        with urllib.request.urlopen(base + "/live") as r:
            csp = r.headers["Content-Security-Policy"]
            assert "script-src 'self'" in csp and "unsafe-inline" not in csp.split("style-src")[0]
        with urllib.request.urlopen(base + "/") as r:
            assert "script-src" not in r.headers["Content-Security-Policy"]
        with urllib.request.urlopen(base + "/assets/orbit-live.js") as r:
            body = r.read().decode()
            assert "data-live" in body and 'fetch("/live"' in body
            assert r.headers["Content-Type"].startswith("text/javascript")

    def test_amount_and_onboard_cookies_persist(self, base: str) -> None:
        jar = CookieJar()
        browser = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        with browser.open(base + "/live?onboard=again") as r:
            assert "처음 오셨나요" in r.read().decode()
        with browser.open(base + "/live?amounts=hide&onboard=done") as r:
            html = r.read().decode()
            assert "처음 오셨나요" not in html and 'href="/live?amounts=show"' in html
            cookies = r.headers.get_all("Set-Cookie") or []
            assert any(c.startswith("dashboard_amounts=hide") and "HttpOnly" in c for c in cookies)
        with browser.open(base + "/live") as r:
            html = r.read().decode()
            assert 'href="/live?amounts=show"' in html and "처음 오셨나요" not in html
        with browser.open(base + "/live?amounts=bogus") as r:
            assert 'href="/live?amounts=show"' in r.read().decode()  # 이상한 값은 무시


def test_detail_tiles_bar_and_table_follow_approved_layout() -> None:
    vega = Book(
        "vega",
        "Vega",
        "업비트",
        300.0,
        100.0,
        (
            Holding("BTC", 0.001, 120.0, 150.0, target=0.41),
            Holding("ETH", 0.01, 40.0, 50.0, target=0.0),
        ),
        info={
            "buying": 1,
            "coins": 3,
            "dd": 0.006,
            "stopped": False,
            "last_decision": "2026-10-09",
        },
    )
    html = render_live([vega], Quotes(at_ms=_NOW), _NOW, show_detail=True)
    dt = html.split('data-live="card-vega-detail"', 1)[1].split("</details>", 1)[0]
    for text in (
        "현금",
        "남은 돈",
        "사는 중인 코인",
        "1 / 3개",
        "최고점 기준",
        "−0.6%",
        "−30%\u2060면 매수 멈춤",
        "하는 중",
        "지금 구성",
        "비중 / 목표",
        "매일 09:05 판단 · 마지막 10/09",
        "장부 기준 · 세금 전 · 평가액",
    ):
        assert text in dt, text
    assert "50 / 41%" in dt  # BTC 150 / 300
    assert 'class="warn">17 / 0%' in dt  # 목표에서 10%p 넘게 벗어나면 노랑
    deneb = Book(
        "deneb",
        "Deneb",
        "토스",
        300_000.0,
        57_057.0,
        (Holding("TQQQ", 1, 109_000.0, 108_988.0, target=0.6, avg_usd=81.28),),
        info={
            "cash_usd": 42.55,
            "month": "2026-10",
            "month_amount": 300_000.0,
            "lev_target": 0.6,
            "brake_stage": 1,
        },
    )
    g = render_live([deneb], Quotes(usd={"TQQQ": 80.98}, at_ms=_NOW), _NOW, show_detail=True)
    gd = g.split('data-live="card-deneb-detail"', 1)[1].split("</details>", 1)[0]
    for text in (
        "남은 달러 $42.55",
        "10월 첫 거래일",
        "TQQQ 목표 비중",
        "60%",
        "절반 팖",
        "$81.28",
        "$80.98",
        "다음 매수 11월 첫 거래일",
    ):
        assert text in gd, text
    assert "시장이 잔잔해" not in gd
    hidden = render_live([deneb], Quotes(usd={"TQQQ": 80.98}), _NOW, hide=True)
    hd = hidden.split('data-live="card-deneb-detail"', 1)[1].split("</details>", 1)[0]
    assert "57,057" not in hd and "300,000" not in hd and "42.55" not in hd and "60%" in hd


def test_one_toggle_opens_all_three_details_and_words_stay_whole() -> None:
    closed = render_live([_book(), _book("deneb")], Quotes(), _NOW)
    assert closed.count("orbit-book-detail") == 3 and closed.count('-detail" hidden>') == 3
    assert 'href="/live?detail=open"' in closed and "상세 보기" in closed
    assert "orbit-detail-open" not in closed  # 카드마다 있던 링크는 없다
    opened = render_live([_book()], Quotes(), _NOW, show_detail=True)
    assert '-detail" hidden>' not in opened and 'href="/live?detail=close"' in opened
    assert "−30%\u2060면" in opened  # % 뒤에서 줄이 끊기지 않게


def test_cards_pad_rows_so_lines_match_and_toggle_sits_in_accumulate_card() -> None:
    two = Book(
        "deneb", "Deneb", "토스", 1.0, 0.0, (Holding("TQQQ", 1, 1, 1), Holding("SCHD", 1, 1, 1))
    )
    three = Book(
        "vega",
        "Vega",
        "업비트",
        1.0,
        0.0,
        tuple(Holding(c, 1, 1, 1) for c in ("BTC", "ETH", "SOL")),
    )
    html = render_live([two, three], Quotes(), _NOW, show_detail=True)
    deneb = html.split('id="live-book-0"', 1)[1].split('id="live-book-1"', 1)[0]
    acc = html.split('id="live-book-2"', 1)[1]
    assert deneb.count('<li class="orbit-pad"') == 1  # 3 종목 카드에 맞춰 한 줄 채움
    assert deneb.count('<tr class="orbit-pad"') == 1
    assert acc.count('<li class="orbit-pad"') == 2  # 기록 없음 한 줄 + 빈 줄 둘
    # 버튼은 카드 밖, 화면 읽는 법 카드와 카드들 사이 줄에 (오른쪽 선 맞춤은 CSS)
    tools = html.split('class="orbit-cards-tools"', 1)[1].split('class="orbit-cards"', 1)[0]
    assert "orbit-detail-toggle" in tools and "orbit-detail-toggle" not in acc
    assert "flex:" in html.split('class="orbit-dt-bar"', 1)[1][:200]  # 비율대로 꽉 채움


def test_deneb_brake_reserve_counts_in_value_summary_and_tiles() -> None:
    from auto_trading.ui import live_home

    state = {
        "deposits": [["2026-10-09", 260_000.0, 200.0, 1300.0]],
        "cash_usd": 10.0,
        "qty": {"TQQQ": 0.0, "SCHD": 3.0},
        "brake": {"stage": 1, "reserve_usd": 90.0},
    }
    q = Quotes(usd={"SCHD": 30.0}, fx=1300.0, at_ms=_NOW)
    book = live_home.deneb_book(state, q)
    # 남은 달러 10 + 보관 90 = 100 달러 → 130,000원, SCHD 3 × 30 × 1300 = 117,000원
    assert book.cash_krw == pytest.approx(130_000.0)
    assert book.value_krw == pytest.approx(247_000.0)
    assert book.info["reserve_usd"] == 90.0
    assert any("브레이크 보관 달러 $90.00" in n for n in book.notes)
    assert "247,000원" in live_home.summary_text([book], q, _NOW)  # 텔레그램 현황도 같은 합
    html = render_live([book], q, _NOW, show_detail=True)
    dt = html.split('data-live="card-deneb-detail"', 1)[1].split("</section>", 1)[0]
    assert "보관 $90.00 포함" in dt and "판 돈 $90.00 보관" in dt and "절반 팖" in dt
    hidden = render_live([book], q, _NOW, show_detail=True, hide=True)
    assert "90.00" not in hidden.split('data-live="card-deneb-detail"', 1)[1]
