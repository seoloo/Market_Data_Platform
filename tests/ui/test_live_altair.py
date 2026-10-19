"""오르빗 실거래 — 업비트 Vega 숨김 · 바이낸스 Vega + Altair 카드 (사용자 2026-10-15)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from auto_trading.ui.live_home import Quotes, build_books


@pytest.fixture(autouse=True)
def _binance_era(monkeypatch: pytest.MonkeyPatch) -> None:
    """바이낸스 Vega · Altair 시기(10-15 ~ 10-19) 화면 — 장부 코드는 남겨 두고 시험한다."""
    monkeypatch.setattr("auto_trading.ui.live_home.VEGA_ON_UPBIT", False)
    monkeypatch.setattr("auto_trading.ui.live_home.SHOW_UPBIT", False)


def _write(root: Path, rel: str, data: object) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data), encoding="utf-8")


def test_upbit_hidden_and_altair_card(tmp_path: Path) -> None:
    q = Quotes(fx=1400.0)
    _write(tmp_path, "data/upbit_t2/state.json", {"expected": {"KRW": "296482"}, "deposits": []})
    assert build_books(tmp_path, q) == []  # 업비트 장부는 실거래 화면에 안 나온다
    _write(
        tmp_path,
        "data/vega/state.json",
        {
            "expected": {"USDT": "100"},
            "deposits": [["2026-10-15", "1200"]],
            "altair": {
                "futures_usdt": "600",
                "checked": "2026-10-15",
                "transfers": [["2026-10-15", "600.00"]],
            },
        },
    )
    books = {b.key: b for b in build_books(tmp_path, q)}
    assert set(books) == {"vega", "altair"}
    f = books["altair"]
    assert f.title == "Altair" and f.cash_krw == 600 * 1400.0 and f.deposits_krw == 600 * 1400.0
    assert any("선물 지갑 600.00 USDT" in n for n in f.notes)


def test_altair_uses_live_runner_equity(tmp_path: Path) -> None:
    """실행기 장부가 있으면 평가액 = 마지막 기록(미실현 포함), 현황은 '현금' 대신 장부 · 포지션 줄
    (사용자 2026-10-18 "현황에 altair 반영 안 되어 있다")."""
    from auto_trading.ui.live_home import summary_text

    q = Quotes(fx=1400.0)
    _write(
        tmp_path,
        "data/vega/state.json",
        {
            "expected": {"USDT": "100"},
            "deposits": [],
            "altair": {"futures_usdt": "600", "transfers": [["2026-10-15", "600.00"]]},
        },
    )
    _write(
        tmp_path,
        "data/altair_live/state.json",
        {
            "nav_peak": 1.0,
            "long_w": {"BTC": 0.1, "ETH": 0.0},
            "shorts": {},
            "history": [[1792153262301, 600.0, 1.0], [1792253161844, 588.0, 0.98]],
        },
    )
    a = {b.key: b for b in build_books(tmp_path, q)}["altair"]
    assert a.value_krw == 588.0 * 1400.0 and a.deposits_krw == 600 * 1400.0
    assert any("고점 대비 -2.0%" in n for n in a.notes)
    text = summary_text([a], q, 1792253161844)
    assert "장부 588.00 USDT" in text and "롱 BTC 40% · 숏 없음" in text
    assert "현금" not in text


def test_sirius_book_shows_lots_cash_and_decision() -> None:
    import pytest

    from auto_trading.ui.live_home import Quotes, sirius_book, summary_text

    state = {
        "deposits": [["2026-10-19", 1_000_000, 714.29, 1400.0]],
        "cash_usd": 595.0,
        "lots": [
            {"id": "T:MU", "sym": "MU", "qty": 0.0135, "buy_day": "2026-11-02",
             "cost_usd": 11.9, "tranche": "2026-10-30"},
            {"id": "T2:MU", "sym": "MU", "qty": 0.01, "buy_day": "2026-12-01",
             "cost_usd": 9.0, "tranche": "2026-11-30"},
        ],
        "decisions": {"2026-10-30": {"picks": ["MU", "DELL"], "bought": True}},
        "orders": [{"sym": "DELL", "side": "BUY", "status": "SENT"}],
    }  # fmt: skip
    q = Quotes({}, {"MU": 900.0}, 1400.0, 0, ())
    b = sirius_book(state, q)
    mu = b.holdings[0]
    assert mu.name == "MU" and mu.qty == pytest.approx(0.0235)
    assert mu.value_krw == pytest.approx(0.0235 * 900 * 1400)
    assert b.deposits_krw == 1_000_000 and b.cash_krw == pytest.approx(595 * 1400)
    assert b.pending and "DELL" in b.pending[0]
    assert any("2026-10-30 결정" in n for n in b.notes)
    assert "✨ Sirius" in summary_text([b], q, 0)
