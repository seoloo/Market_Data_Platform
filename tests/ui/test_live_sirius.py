"""오르빗 실거래 — ✨ Sirius 카드 (문서 279).

옛 바이낸스 Vega · Altair 카드 시험(2026-10-15 ~ 10-19)은 은퇴한 카드 코드와 함께 뺐다
(리팩토링 5 단계, 2026-10-21) — 그래서 파일 이름도 test_live_altair 에서 바꿈.
"""

from __future__ import annotations


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
