"""244 — 국장 실적 갭: 공시 이름 거르기 · 2 일 반응 · D+2 진입 · 폐지 처리 · 판정 (가짜 판)."""

from __future__ import annotations

import pytest

from scripts import run_pead_kr_244 as m


def test_only_original_earnings_notices() -> None:
    rows = [
        {"report_nm": "연결재무제표기준영업(잠정)실적(공정공시)", "rcept_dt": "20240105"},
        {"report_nm": "영업(잠정)실적(공정공시)   ", "rcept_dt": "20240430"},
        {"report_nm": "[기재정정]연결재무제표기준영업(잠정)실적(공정공시)", "rcept_dt": "20240110"},
        {"report_nm": "[첨부추가]영업(잠정)실적(공정공시)", "rcept_dt": "20240111"},
        {"report_nm": "기업가치제고계획(자율공시)", "rcept_dt": "20240601"},
    ]
    assert m.earnings_dates(rows) == ["20240105", "20240430"]


def test_day_index_moves_to_next_trading_day() -> None:
    days = ["2024-01-04", "2024-01-08"]
    assert m.day_index(days, "20240106") == 1
    assert m.day_index(days, "20240104") == 0
    assert m.day_index(days, "20240109") is None


def test_gap2_needs_two_day_move_and_volume() -> None:
    close: list[float | None] = [100.0] * 30
    vol = [10.0] * 30
    close[26] = 106.0  # D = 25: D−1 100 → D+1 106
    vol[26] = 25.0
    assert m.gap2(close, vol, 25) == pytest.approx(0.06)
    vol[26] = 15.0
    assert m.gap2(close, vol, 25) is None  # 거래량 2 배 안 됨
    close[24] = None
    assert m.gap2(close, vol, 25) is None


def test_outcome_enters_d_plus_2_and_handles_delisting() -> None:
    days = [f"d{k:03d}" for k in range(40)]
    kospi = [100.0] * 40
    close: list[float | None] = [10.0] * 40
    close[12] = 20.0  # 진입 D+2 = 12 → 20 일 뒤 32
    close[32] = 22.0
    got = m.outcome(days, close, kospi, 10, last=39)
    assert got is not None
    assert got[0] == "d012" and got[1] == pytest.approx(22 / 20 - 1 - m.COST)
    close[20] = 5.0  # 20 번이 마지막 거래(상장폐지) → 그 종가로
    gone = m.outcome(days, close, kospi, 10, last=20)
    assert gone is not None and gone[1] == pytest.approx(5 / 20 - 1 - m.COST)
    assert m.outcome(days, close, kospi, 25, last=39) is None  # 20 일 다 못 채움


def test_judge_mirrors_240() -> None:
    assert m.judge((0.006, 0.002, 0.01), [0.008, 0.004], 0.005) == "지지"
    assert m.judge((0.006, -0.001, 0.01), [0.008, 0.004], 0.005) == "불명"
    assert m.judge((0.006, 0.002, 0.01), [0.008, -0.001], 0.005) == "불명"
    assert m.judge((0.006, 0.002, 0.01), [0.008, 0.004], 0.007) == "불명"
    assert m.judge((-0.001, -0.004, 0.002), [0.0, 0.0], 0.0) == "불리"
