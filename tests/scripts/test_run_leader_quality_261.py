"""261 — 공시일 지키기 · 분기 1 년 비교 · 시총 추정 · 풀 안 고르기 · 판정 (가짜 자료)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import run_leader_quality_261 as m


def test_floats_and_quarters_first_filed_wins() -> None:
    raw = [
        {"end": "2020-06-30", "val": 100, "filed": "2021-02-01"},
        {"end": "2020-06-30", "val": 999, "filed": "2022-02-01"},  # 다시 적힌 값 — 처음 것
        {"end": "2021-06-30", "val": 120, "filed": "2022-02-01"},
    ]
    assert m.floats(raw) == [
        ("2021-02-01", "2020-06-30", 100.0),
        ("2022-02-01", "2021-06-30", 120.0),
    ]
    q = [
        {"start": "2020-01-01", "end": "2020-03-31", "val": 10, "filed": "2020-05-01"},
        {"start": "2020-01-01", "end": "2020-12-31", "val": 50, "filed": "2021-02-01"},  # 1 년 — 뺌
    ]
    assert m.quarters(q) == [("2020-05-01", "2020-03-31", 10.0)]


def test_grew_respects_filed_and_staleness() -> None:
    qs = [("2020-05-01", "2020-03-31", 10.0), ("2021-05-01", "2021-03-31", 12.0)]
    assert m.grew(qs, "2021-04-30") is None  # 올해 분기 아직 공시 전 → 1 년 전 비교 없음
    assert m.grew(qs, "2021-05-01") is True
    assert m.grew(qs, "2022-01-01") is None  # 끝난 지 200 일 넘음
    assert (
        m.grew(
            [("2020-05-01", "2020-03-31", 10.0), ("2021-05-01", "2021-03-31", 9.0)], "2021-06-01"
        )
        is False
    )


def test_cap_uses_price_change_since_asof(tmp_path: Path) -> None:
    (tmp_path / "X.json").write_text(
        json.dumps({"float": [{"end": "2020-01-02", "val": 1e11, "filed": "2020-02-01"}]}),
        encoding="utf-8",
    )
    f = m.Fundamentals(tmp_path)
    days = ["2020-01-02", "2020-02-03", "2020-03-02"]
    close: list[float | None] = [10.0, 11.0, 20.0]
    assert f.cap("X", "2020-01-31", close, days) is None  # 공시 전
    assert f.cap("X", "2020-03-02", close, days) == pytest.approx(2e11)
    assert f.cap("Y", "2020-03-02", close, days) is None
    assert not f.growing("X", "2020-03-02")


def test_pick_in_pool_and_keep() -> None:
    n = 140
    close: dict[str, list[float | None]] = {
        f"S{k:02d}": [100.0 * (1 + 0.0001 * k) ** t for t in range(n)] for k in range(40)
    }
    sector = {s: ("A", "B", "C", "D")[int(s[1:]) % 4] for s in close}
    pool = sorted(close)
    got = m.pick_in(close, sector, n - 1, pool)
    assert len(got) == 10 and got[0] == "S39"
    got2 = m.pick_in(close, sector, n - 1, pool, keep=lambda s: s != "S39")
    assert "S39" not in got2 and len(got2) == 10
    assert m.pick_in(close, sector, n - 1, pool[:10]) == []  # 풀이 30 개 미만


def test_judge() -> None:
    def row(s: float, s1: float, s2: float, mdd: float) -> dict[str, dict[str, float]]:
        return {
            "full": {"sharpe": s, "mdd": mdd},
            "first": {"sharpe": s1},
            "second": {"sharpe": s2},
        }

    rows = {
        "BASE": row(0.7, 0.5, 0.8, 0.3),
        "CAP": row(0.85, 0.6, 0.9, 0.3),
        "CAP_EARN": row(0.9, 0.4, 1.0, 0.3),
    }
    assert m.judge(rows) == "ADOPT_CAP"  # CAP_EARN 은 앞 반쪽이 BASE 보다 낮음
    rows["CAP"] = row(0.75, 0.6, 0.9, 0.3)
    assert m.judge(rows) == "KEEP_BASE"
