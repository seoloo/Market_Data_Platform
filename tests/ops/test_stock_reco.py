"""💹 월간 미장 주도주 추천 · 매도 신호 (2026-10-17) — 월말 판정 · 추천 · 신호 · 글 · 버튼 기록."""

from __future__ import annotations

from pathlib import Path

import pytest

from auto_trading.ops import stock_reco as m
from scripts import stock_reco as sr


def _days(n: int) -> list[str]:
    from datetime import date, timedelta

    out: list[str] = []
    d = date(2025, 1, 1)
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def test_is_month_end() -> None:
    assert m.is_month_end("2026-10-30", "2026-11-02")
    assert not m.is_month_end("2026-10-29", "2026-10-30")


def test_recommend_market_and_picks() -> None:
    n = 260
    days = _days(n)
    closes: dict[str, list[float | None]] = {
        f"S{k:02d}": [100.0 * (1 + 0.0002 * k) ** t for t in range(n)] for k in range(60)
    }
    sectors = {
        s: ("Energy", "Health Care", "Utilities", "Financials")[int(s[1:]) % 4] for s in closes
    }
    up: list[float | None] = [100.0 * 1.001**t for t in range(n)]
    r = m.recommend(days, closes, sectors, up, n - 1)
    assert r.market_ok and r.spy_gap > 0 and len(r.picks) == 10 and r.picks[0].symbol == "S59"
    assert r.picks[0].trend and r.picks[0].rets[0] > 0
    down: list[float | None] = [100.0 * 0.999**t for t in range(n)]
    assert not m.recommend(days, closes, sectors, down, n - 1).market_ok


def test_sell_signal_needs_trend_confirmed_first() -> None:
    falling: list[float | None] = [100.0 * 0.998**t for t in range(200)]
    assert (
        m.sell_signal(falling, 150, 199) is None
    )  # 추세 밖에서 산 종목 — 추세 확인 전엔 신호 없음
    rise_then_fall: list[float | None] = [100.0 * 1.003**t for t in range(160)]
    top = rise_then_fall[-1]
    assert top is not None
    rise_then_fall += [top * 0.995**k for k in range(1, 80)]
    sig = m.sell_signal(rise_then_fall, 150, len(rise_then_fall) - 1)
    assert sig is not None and sig[0] in ("trend", "peak35")
    crash = [*rise_then_fall[:160], top * 0.6]
    got = m.sell_signal(crash, 150, 160)
    assert got is not None and got[0] == "peak35" and got[1] == pytest.approx(top)


def test_texts() -> None:
    days = ["2026-10-30"]
    p = m.Pick("MRNA", "Health Care", 1.0, (0.62, 2.83, 3.33), True)
    reco = m.Reco(days[0], True, 0.07, ["Health Care", "Energy", "Information Technology"], [p] * 4)
    t = m.reco_text(reco, "11월", detail={"MRNA": ["시총 약 $850억"]}, names={"MRNA": "모더나"})
    assert "11월" in t and "10/30 종가" in t and "헬스케어 · 에너지 · IT" in t
    assert "MRNA 모더나" in t and "+283%" in t and "시총 약 $850억" in t and "4 ~ 4위" in t
    off = m.Reco(days[0], False, -0.032, ["Energy"], [p])
    assert "새로 사지 않아요" in m.reco_text(off, "11월")
    s = m.sell_text("VLO", 2, 444.06, 478.20, 512.0, "trend", 1_400.0)
    assert "추세 꺾임" in s and "+7.7%" in s and "+$68" in s
    assert "고점 −35%" in m.sell_text("VLO", 2, 444.06, 332.0, 512.0, "peak35", 1_400.0)


def test_offers_once_and_expire(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sr, "OFFERS", tmp_path / "offers.json")
    n = sr.new_offer("buy", 1_000, sym="MRNA")
    assert sr.take_offer(n, "sell", 1_001) is None  # 다른 종류
    assert sr.take_offer(n, "buy", 1_001) is not None
    assert sr.take_offer(n, "buy", 1_002) is None  # 한 번만
    late = sr.new_offer("buy", 1_000, sym="VEEV")
    assert sr.take_offer(late, "buy", 1_000 + 6 * 86_400_000) is None  # 5 일 지남


def test_sell_signal_ticket_sells_with_its_own_wording() -> None:
    from auto_trading.ops import stop_guard as sg
    from auto_trading.ops.size_order import Ticket
    from tests.ops.test_size_order import FakeTrader

    t = Ticket("1", "VLO", "발레로", "USD", 2, 444.0, 0, status="selling")
    t.filled, t.avg, t.note = 2.0, 444.0, "매도 신호 — 추세 꺾임"
    tr = FakeTrader(bid=478.0)
    msgs = sg.step_sell(t, tr, 0, live=True, sellable=2.0, fx=1_400.0)
    assert msgs[0].startswith("🧹 발레로 매도 신호") and "예상 손익" in msgs[0]
    tr.fills["o0"] = ("FILLED", 2.0, 478.0)
    assert "매도 신호 매도 끝" in sg.step_sell(t, tr, 1, live=True, sellable=0.0)[0]
