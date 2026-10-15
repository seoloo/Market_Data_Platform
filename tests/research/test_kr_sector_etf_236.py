"""236 섹터 ETF 수급 — 섹터 나누기 · 시뮬레이션 규칙 · 판정 (가짜 자료, 실제 수익 계산 없음)."""

from __future__ import annotations

import pytest

from scripts import run_kr_sector_etf_236 as m


def test_sector_mapping_splits_medical_devices_and_skips_utilities() -> None:
    assert m.sector_of("032702") == "IT"  # 정밀기기
    assert m.sector_of("032701") == "헬스케어"  # 의료용 기기
    assert m.sector_of("032102") == "헬스케어"  # 의약품
    assert m.sector_of("116401") == "금융"
    assert m.sector_of("033501") is None  # 전기 · 가스(유틸리티) — 맞는 ETF 없음
    assert m.sector_of("") is None


def test_sector_score_is_median_of_member_scores() -> None:
    sc = m.sector_scores(
        {"a": 0.9, "b": 0.1, "c": 0.5, "d": 0.7}, {"a": "IT", "b": "IT", "c": "IT"}
    )
    assert sc == {"IT": (0.5, 3)}


def _days(n: int) -> list[str]:
    # 2011-10-01 부터, 달 바뀜을 만들려고 28 일마다 다음 달
    out = []
    for i in range(n):
        month, day = 10 + i // 28, 1 + i % 28
        year = 2011 + (month - 1) // 12
        out.append(f"{year}-{(month - 1) % 12 + 1:02d}-{day:02d}")
    return out


def test_gate_off_on_decision_day_sells_everything(monkeypatch: pytest.MonkeyPatch) -> None:
    days = _days(90)
    monkeypatch.setattr(m, "FROM", days[0])
    monkeypatch.setattr(m, "TO", days[-1])
    etf: dict[str, list[float | None]] = {"A": [100.0] * 90, "B": [100.0] * 90}
    ends = m.month_ends(days)
    decide = {t: ["A", "B"] for t in ends}
    ok = [True] * 90
    ok[ends[1]] = False  # 둘째 판단일엔 200 일선 아래
    res = m.simulate(days, etf, decide, ok, 0.0, top=2, use_trend=False)
    assert res.exposure[ends[1] + 1] == pytest.approx(0.0)  # 다음 날 모두 팔림
    assert res.exposure[ends[0] + 1] == pytest.approx(1.0)  # 첫 판단 다음 날 다 삼
    assert res.picks[days[ends[1]]] == []


def test_equal_weight_and_fee() -> None:
    days = _days(40)
    etf: dict[str, list[float | None]] = {"A": [10.0] * 40, "B": [10.0] * 33 + [20.0] * 7}
    ends = m.month_ends(days)
    res = m.simulate(days, etf, {ends[0]: ["A", "B"]}, [True] * 40, 0.01, top=2, use_trend=False)
    # 11 월 첫날 0.99 를 반씩 → B 가 두 배 된 뒤 0.495 + 0.99 = 1.485
    assert res.equity[-1] == pytest.approx(0.495 + 0.99)


def test_judge_needs_all_three() -> None:
    def part(sharpe: float, h1: float, h2: float, mdd: float) -> dict[str, dict[str, float]]:
        return {
            "all": {"sharpe": sharpe, "mdd": mdd},
            "half1": {"cagr": h1},
            "half2": {"cagr": h2},
        }

    assert m.judge(part(0.6, 0.05, 0.05, 0.3), part(0.3, 0.01, 0.01, 0.5))["verdict"] == "PASS"
    assert m.judge(part(0.45, 0.05, 0.05, 0.3), part(0.3, 0.01, 0.01, 0.5))["S1"] is False
    assert m.judge(part(0.6, 0.0, 0.05, 0.3), part(0.3, 0.01, 0.01, 0.5))["S2"] is False
    assert m.judge(part(0.6, 0.05, 0.05, 0.45), part(0.3, 0.01, 0.01, 0.3))["S3"] is False
