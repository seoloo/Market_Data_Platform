from auto_trading.ops import position_size as m


def _up(n: int = 220, start: float = 100.0) -> list[float]:
    return [start * (1 + 0.002 * i) for i in range(n)]


def _inp(**kw: object) -> m.Inputs:
    base: dict[str, object] = {
        "symbol": "NVDA",
        "name": "NVIDIA",
        "price": 100.0,
        "krw_cash": 10_000_000.0,
        "usd_cash": 20_000.0,
        "fx": 1_400.0,
        "deneb_usd": 0.0,
        "closes": _up(),
        "index_name": "SPY",
        "index_closes": _up(),
        "earnings_date": "2026-11-20",
        "earnings_in_days": 20,
    }
    base.update(kw)
    return m.Inputs(**base)  # type: ignore[arg-type]


def test_base_excludes_deneb_dollars_and_stocks() -> None:
    assert m.base_krw(1_000.0, 10.0, 1_000.0, 3.0) == 1_000.0 + 7.0 * 1_000.0
    assert m.base_krw(1_000.0, 2.0, 1_000.0, 3.0) == 1_000.0


def test_cap_25_percent_binds_before_33() -> None:
    a = m.recommend(_inp())
    # 기준 1,000 만 + 2 만 달러 × 1,400 = 3,800 만 → 25% = 950 만 → $6,785.7 ÷ $100.50 = 67 주
    assert a.ok and a.limit == 100.5 and a.qty == 67
    assert 0.24 < a.weight <= 0.25
    assert a.stop == 97.48  # 100.50 × 0.97 = 97.485 → 내림
    assert abs(a.risk_krw - 67 * (100.5 - 97.48) * 1_400) < 1


def test_index_below_200d_halves() -> None:
    down = list(reversed(_up()))
    a = m.recommend(_inp(index_closes=down))
    assert a.qty == 33 and any("절반" in x for x in a.lines)


def test_blocks_earnings_trend_and_strategy_symbols() -> None:
    assert not m.recommend(_inp(earnings_in_days=3, earnings_date="2026-10-20")).ok
    assert m.recommend(_inp(earnings_in_days=4)).ok
    assert not m.recommend(_inp(closes=list(reversed(_up())))).ok
    a = m.recommend(_inp(symbol="TQQQ", blocked_symbol="Deneb"))
    assert not a.ok and "Deneb" in a.blocked[0]


def test_dollars_short_reduces_quantity() -> None:
    a = m.recommend(_inp(usd_cash=2_000.0, deneb_usd=500.0))
    assert a.qty == 14 and any("모자라" in x for x in a.lines)  # $1,500 ÷ $100.50


def test_already_held_tops_up_only() -> None:
    a = m.recommend(_inp(held_qty=50))
    assert a.qty == 17  # 950 만 − 50 주 × $100 × 1,400 = 250 만 → $1,785.7 ÷ 100.5


def test_korean_stock_ticks_and_won() -> None:
    a = m.recommend(
        _inp(symbol="005930", name="삼성전자", price=71_200.0, krw_cash=20_000_000.0, usd_cash=0.0)
    )
    assert a.currency == "KRW" and a.limit == 71_600.0  # 71,556 → 100 원 단위 올림
    assert a.qty == 69 and a.stop == 69_400.0  # 500 만 ÷ 71,600 · 69,452 → 내림
    assert "⚠️ 실적 발표일 모름" not in a.lines  # 실적일을 줬으니


def test_round_price_and_text() -> None:
    assert m.round_price(0.12345, False, up=True) == 0.1235
    assert m.round_price(1999.0, True, up=False) == 1999.0
    text = m.advice_text(m.recommend(_inp()))
    assert "추천: 25%" in text and "상한 25%" in text and "−3%" in text
    bad = m.advice_text(m.recommend(_inp(earnings_in_days=1)))
    assert "⛔ 지금은 사지 않아요" in bad and "참고: 조건이 맞았다면 25%" in bad
    none = m.recommend(_inp(earnings_date="", earnings_in_days=8))
    assert "✅ 실적 발표 다음 8일 안에 없음" in none.lines
    down = m.recommend(_inp(closes=list(reversed(_up()))))
    assert "점수 -1.00" in down.blocked[0] and "50일선 아래" in down.blocked[0]
