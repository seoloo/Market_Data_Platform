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


def _write(root: Path, rel: str, obj: dict[str, Any]) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj), encoding="utf-8")
    return path


def _book(key: str = "cruise", *, updated_ms: int = _NOW - _H, value: float | None = 120.0) -> Book:
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
    # Glide 는 매달 도니 40 시간은 오래된 게 아니다
    assert "오래된" not in render_live([_book("glide", updated_ms=_NOW - 40 * _H)], Quotes(), _NOW)


def test_missing_price_gets_estimate_badge_not_confirmed_value() -> None:
    html = render_live([_book(value=None)], Quotes(at_ms=_NOW), _NOW)
    assert html.count('class="orbit-est"') >= 3  # 총자산 · 카드 · 종목
    assert "시세 미확인" in html


def test_holding_detail_popup_has_numbers_and_no_order_button() -> None:
    html = render_live([_book()], Quotes(usd={}, at_ms=_NOW), _NOW)
    assert 'popovertarget="orbit-h-cruise-0"' in html and 'id="orbit-h-cruise-0"' in html
    pop = html.split('id="orbit-h-cruise-0"', 1)[1].split("</section>", 1)[0]
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


def test_accounts_show_last_success_missing_and_stale() -> None:
    html = render_live([_book(), _book("glide", updated_ms=_NOW - 900 * _H)], Quotes(), _NOW)
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
